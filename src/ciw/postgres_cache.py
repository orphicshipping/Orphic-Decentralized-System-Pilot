"""Shared PostgreSQL cache of host-approved immutable content, never permissions.

SPDX-License-Identifier: AGPL-3.0-or-later
DSNs and cache identifiers belong to trusted launchers. Importing this module
does not connect, provision a database, create credentials or expose MCP tools.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import re

from .persistent_cache import PersistentCache, PersistenceRefusal
from .toolbox_cache import MAX_CACHE_BYTES, MAX_ENTRIES, MAX_ENTRY_BYTES, canonical


_DDL = """
CREATE SCHEMA IF NOT EXISTS odsp_persistent_cache_v1;
CREATE TABLE IF NOT EXISTS odsp_persistent_cache_v1.format (
 singleton boolean PRIMARY KEY CHECK(singleton), digest text NOT NULL);
CREATE TABLE IF NOT EXISTS odsp_persistent_cache_v1.caches (
 cache_id text PRIMARY KEY CHECK(octet_length(cache_id) BETWEEN 1 AND 80),
 limits bytea NOT NULL CHECK(octet_length(limits) BETWEEN 1 AND 128),
 next_touch bigint NOT NULL DEFAULT 0 CHECK(next_touch BETWEEN 0 AND 9007199254740991));
CREATE TABLE IF NOT EXISTS odsp_persistent_cache_v1.entries (
 cache_id text NOT NULL REFERENCES odsp_persistent_cache_v1.caches(cache_id),
 key_hash text NOT NULL CHECK(octet_length(key_hash)=64),
 key bytea NOT NULL CHECK(octet_length(key) BETWEEN 1 AND 4096),
 owner text NOT NULL CHECK(octet_length(owner)=64),
 authority text NOT NULL CHECK(octet_length(authority)=64),
 value bytea NOT NULL CHECK(octet_length(value) BETWEEN 1 AND 262144),
 value_hash text NOT NULL CHECK(octet_length(value_hash)=64),
 size integer NOT NULL CHECK(size BETWEEN 1 AND 262144),
 touch bigint NOT NULL CHECK(touch BETWEEN 1 AND 9007199254740991),
 PRIMARY KEY(cache_id,key_hash), CHECK(size=octet_length(key)+octet_length(value)));
CREATE INDEX IF NOT EXISTS entries_lru ON odsp_persistent_cache_v1.entries(cache_id,touch,key_hash);
CREATE TABLE IF NOT EXISTS odsp_persistent_cache_v1.scopes (
 cache_id text NOT NULL REFERENCES odsp_persistent_cache_v1.caches(cache_id),
 owner text NOT NULL CHECK(octet_length(owner)=64),
 authority text NOT NULL CHECK(octet_length(authority)=64),
 PRIMARY KEY(cache_id,owner));
"""
_FORMAT = sha256(_DDL.encode()).hexdigest()
_MAX_TOUCH = 2**53 - 1


def _binding(dsn, cache_id=None):
    if type(dsn) is not str or not 1 <= len(dsn) <= 8192 or '\x00' in dsn:
        raise PersistenceRefusal('host_postgres_binding_required')
    if cache_id is not None and (type(cache_id) is not str
            or re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', cache_id) is None):
        raise PersistenceRefusal('host_cache_identity_required')


@contextmanager
def _connection(dsn):
    try:
        import psycopg
    except ImportError:
        raise PersistenceRefusal('postgres_psycopg_dependency_required') from None
    try:
        # A transaction pooler may reject startup options and change the backend
        # after each transaction. No named preparation or session state is needed.
        # Explicit transactions also forbid an authority callback from committing
        # partially validated cache state through a shared connection.
        with psycopg.connect(dsn, connect_timeout=3, application_name='odsp-persistent-cache-v1',
                prepare_threshold=None) as db:
            with db.transaction():
                db.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE')
                db.execute('SET LOCAL statement_timeout=5000; SET LOCAL lock_timeout=2000; '
                           'SET LOCAL idle_in_transaction_session_timeout=5000; '
                           'SET LOCAL synchronous_commit=on; SET LOCAL search_path=pg_catalog',
                           prepare=False)
                settings = db.execute(
                    "SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                    "current_setting('idle_in_transaction_session_timeout'),"
                    "current_setting('synchronous_commit'),current_setting('search_path'),"
                    "current_setting('transaction_isolation'),current_setting('transaction_read_only')").fetchone()
                if settings != ('5s', '2s', '5s', 'on', 'pg_catalog', 'read committed', 'off'):
                    raise PersistenceRefusal('postgres_cache_transaction_settings')
                yield db
    except psycopg.Error:
        # Driver errors may contain the DSN, endpoint or query values. These
        # are never returned as diagnostics to the embedding application.
        raise PersistenceRefusal('postgres_cache_storage_unavailable') from None


def install_schema(dsn):
    """Explicit host migration in an already authorized PostgreSQL database.

    Requires schema creation only for this administrative action. Runtime roles
    need USAGE on the fixed schema, SELECT on format, and SELECT/INSERT/UPDATE/
    DELETE on caches, entries and scopes. No role or credential is created.
    """
    _binding(dsn)
    with _connection(dsn) as db:
        db.execute('SELECT pg_advisory_xact_lock(678044119138)')
        if db.info.server_version < 140000:
            raise PersistenceRefusal('postgres_cache_version')
        if any(db.execute('SELECT current_setting(%s)', (setting,)).fetchone()[0] != 'on'
               for setting in ('fsync', 'full_page_writes')):
            raise PersistenceRefusal('postgres_cache_durability_setting')
        db.execute(_DDL, prepare=False)
        db.execute('INSERT INTO odsp_persistent_cache_v1.format VALUES(true,%s) ON CONFLICT DO NOTHING', (_FORMAT,))
        if db.execute('SELECT digest FROM odsp_persistent_cache_v1.format').fetchall() != [(_FORMAT,)]:
            raise PersistenceRefusal('postgres_cache_schema_mismatch')


class PostgresPersistentCache(PersistentCache):
    """Multi-client backend with the same authority and bytes as PersistentCache.

    A host-fixed cache_id selects one shared budget within the fixed SQL schema.
    Every access locks that cache's metadata row: concurrent processes/hosts
    cannot exceed its limits or race eviction. Different cache_ids have separate
    budgets, and are deployment configuration, never caller request arguments.
    The reader must consult current host authority on every call; a cache does
    not make a stale policy source current. There is no automatic DB retry.
    """
    def __init__(self, dsn, read_authority, *, cache_id='default', max_entries=64,
                 max_bytes=1024 * 1024, max_entry_bytes=MAX_ENTRY_BYTES):
        _binding(dsn, cache_id)
        if not callable(read_authority):
            raise TypeError('host_authority_reader_required')
        for value, maximum in ((max_entries, MAX_ENTRIES), (max_bytes, MAX_CACHE_BYTES),
                               (max_entry_bytes, MAX_ENTRY_BYTES)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('invalid_cache_limit')
        self._dsn, self.cache_id, self._read = dsn, cache_id, read_authority
        self._limits = (max_entries, max_bytes, min(max_bytes, max_entry_bytes))
        with _connection(self._dsn) as db:
            if db.execute('SELECT digest FROM odsp_persistent_cache_v1.format').fetchall() != [(_FORMAT,)]:
                raise PersistenceRefusal('postgres_cache_schema_mismatch')
            db.execute('INSERT INTO odsp_persistent_cache_v1.caches(cache_id,limits) '
                       'VALUES(%s,%s) ON CONFLICT DO NOTHING', (self.cache_id, canonical(list(self._limits))))
            self._lock_cache(db)

    def _lock_cache(self, db):
        row = db.execute('SELECT limits,next_touch FROM odsp_persistent_cache_v1.caches '
                         'WHERE cache_id=%s FOR UPDATE', (self.cache_id,)).fetchone()
        if row is None or row[0] != canonical(list(self._limits)):
            raise PersistenceRefusal('cache_configuration_changed')
        if type(row[1]) is not int or not 0 <= row[1] < _MAX_TOUCH:
            raise PersistenceRefusal('cache_sequence_exhausted')
        return row[1] + 1

    def _scope(self, db, owner, identity):
        row = db.execute('SELECT authority FROM odsp_persistent_cache_v1.scopes '
                         'WHERE cache_id=%s AND owner=%s', (self.cache_id, owner)).fetchone()
        if row and row[0] != identity:
            db.execute('DELETE FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s AND owner=%s',
                       (self.cache_id, owner))
        db.execute('INSERT INTO odsp_persistent_cache_v1.scopes VALUES(%s,%s,%s) '
                   'ON CONFLICT(cache_id,owner) DO UPDATE SET authority=excluded.authority',
                   (self.cache_id, owner, identity))
        db.execute('DELETE FROM odsp_persistent_cache_v1.scopes WHERE cache_id=%s AND owner<>%s '
                   'AND owner NOT IN (SELECT owner FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s)',
                   (self.cache_id, owner, self.cache_id))

    def _touch(self, db, sequence):
        db.execute('UPDATE odsp_persistent_cache_v1.caches SET next_touch=%s WHERE cache_id=%s',
                   (sequence, self.cache_id))

    def _get(self, namespace, name, version, schema_hash, request):
        authority, identity, owner = self._current()
        key = self._key(authority, identity, namespace, name, version, schema_hash, request)
        with _connection(self._dsn) as db:
            sequence = self._lock_cache(db)
            self._assert_current(identity)
            self._scope(db, owner, identity)
            row = db.execute('SELECT key,value,value_hash,size FROM odsp_persistent_cache_v1.entries '
                             'WHERE cache_id=%s AND key_hash=%s AND owner=%s AND authority=%s',
                             (self.cache_id, sha256(key).hexdigest(), owner, identity)).fetchone()
            result = None
            if row:
                if row[0] != key:
                    raise PersistenceRefusal('cache_key_binding_mismatch')
                result = self._decode_entry(row[1:], key, namespace, identity, version, schema_hash)
                db.execute('UPDATE odsp_persistent_cache_v1.entries SET touch=%s WHERE cache_id=%s AND key_hash=%s',
                           (sequence, self.cache_id, sha256(key).hexdigest()))
                self._touch(db, sequence)
            self._assert_current(identity)
        self._assert_current(identity)
        return result

    def _put(self, namespace, name, version, schema_hash, request, value):
        authority, identity, owner = self._current()
        key = self._key(authority, identity, namespace, name, version, schema_hash, request)
        key_hash = sha256(key).hexdigest()
        raw, checksum, size = self._encode_entry(key, namespace, identity, version, schema_hash, value)
        with _connection(self._dsn) as db:
            sequence = self._lock_cache(db)
            self._assert_current(identity)
            self._scope(db, owner, identity)
            existing = db.execute('SELECT key FROM odsp_persistent_cache_v1.entries '
                                  'WHERE cache_id=%s AND key_hash=%s', (self.cache_id, key_hash)).fetchone()
            if existing and existing[0] != key:
                raise PersistenceRefusal('cache_key_binding_mismatch')
            db.execute('INSERT INTO odsp_persistent_cache_v1.entries '
                       '(cache_id,key_hash,key,owner,authority,value,value_hash,size,touch) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                       'ON CONFLICT(cache_id,key_hash) DO UPDATE SET owner=excluded.owner,authority=excluded.authority,'
                       'value=excluded.value,value_hash=excluded.value_hash,size=excluded.size,touch=excluded.touch',
                       (self.cache_id, key_hash, key, owner, identity, raw, checksum, size, sequence))
            self._touch(db, sequence)
            while True:
                count, total = db.execute('SELECT count(*),COALESCE(sum(size),0) '
                                         'FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s',
                                         (self.cache_id,)).fetchone()
                if count <= self._limits[0] and total <= self._limits[1]:
                    break
                db.execute('DELETE FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s AND key_hash IN '
                           '(SELECT key_hash FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s '
                           'ORDER BY touch,key_hash LIMIT 1)', (self.cache_id, self.cache_id))
            self._assert_current(identity)
        self._assert_current(identity)
