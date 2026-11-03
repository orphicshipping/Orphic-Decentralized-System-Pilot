"""Process-shared SQLite cache of explicitly authorized immutable content.

SPDX-License-Identifier: AGPL-3.0-or-later
The launcher owns the local database path and current authority reader. SQLite
is a single-machine backend, not a network-filesystem or distributed cache.
No execution, admission, verification occurrence or permission is memoized.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat

from .toolbox_cache import (CacheAuthority, CacheScopeChanged, MAX_CACHE_BYTES,
                           MAX_ENTRIES, MAX_ENTRY_BYTES, canonical, digest,
                           _hash, _text)
from .session import loads_json


class PersistenceRefusal(ValueError):
    """A bounded diagnostic, without paths or caller content."""


class PrivateSQLite:
    """Host-only local database binding; a fresh connection per transaction.

    A private, same-uid directory excludes other local users. This does not
    sandbox a malicious administrator or another process running as this uid.
    Existing files and journal sidecars must be private regular files, never
    links. FULL synchronous WAL transactions provide SQLite's local durability
    contract; they are not a physical power-loss qualification.
    """
    def __init__(self, path, *, timeout=2.0):
        self.path = Path(path)
        if (not self.path.is_absolute() or self.path.name in ('', '.', '..')
                or type(timeout) not in (int, float) or not 0 < timeout <= 10):
            raise PersistenceRefusal('database_binding_refused')
        self.timeout = timeout
        parent = self.path.parent
        if parent.resolve() != parent:
            raise PersistenceRefusal('database_directory_link_refused')
        info = parent.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise PersistenceRefusal('private_database_directory_required')
        self._directory_identity = (info.st_dev, info.st_ino)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._check()
        with self.connect() as db:
            if db.execute('PRAGMA journal_mode=WAL').fetchone()[0] != 'wal':
                raise PersistenceRefusal('database_wal_unavailable')
            # Bound a damaged or accidentally unbounded database to 64 MiB.
            db.execute('PRAGMA max_page_count=16384')

    def _check(self):
        info = self.path.parent.lstat()
        if ((info.st_dev, info.st_ino) != self._directory_identity
                or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise PersistenceRefusal('database_directory_changed')
        for suffix in ('', '-wal', '-shm', '-journal'):
            try:
                item = Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                if not suffix:
                    raise PersistenceRefusal('database_missing') from None
                continue
            if (not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid()
                    or item.st_mode & 0o077 or item.st_nlink != 1):
                raise PersistenceRefusal('database_file_refused')

    @contextmanager
    def connect(self):
        self._check()
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                self._check()
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise


def authority_identity(authority):
    if type(authority) is not CacheAuthority:
        raise PersistenceRefusal('current_cache_authority_required')
    scope = authority.scope
    return digest({'tenant': scope.tenant, 'principal': scope.principal,
                   'policy_sha256': scope.policy_sha256,
                   'pins': [list(row) for row in scope.pins],
                   'replay_identity': scope.replay_identity,
                   'schema_names': list(authority.schema_names),
                   'immutable_reads': list(authority.immutable_reads)})


class PersistentCache:
    """One shared bounded database, with fresh host authorization on every use.

    Only immutable content explicitly classified by the host is eligible.
    Neither schema hints nor a caller-provided name classify an operation.
    Limits are fixed in database metadata; another process cannot reopen the
    same database with more permissive limits. Entries are detached JSON.
    """
    def __init__(self, path, read_authority, *, max_entries=64,
                 max_bytes=1024 * 1024, max_entry_bytes=MAX_ENTRY_BYTES):
        if not callable(read_authority):
            raise TypeError('host_authority_reader_required')
        for n, limit in ((max_entries, MAX_ENTRIES), (max_bytes, MAX_CACHE_BYTES),
                         (max_entry_bytes, MAX_ENTRY_BYTES)):
            if type(n) is not int or not 1 <= n <= limit:
                raise ValueError('invalid_cache_limit')
        self._read = read_authority
        self._limits = (max_entries, max_bytes, min(max_bytes, max_entry_bytes))
        self.db = PrivateSQLite(path)
        with self.db.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cache_config '
                       '(singleton INTEGER PRIMARY KEY CHECK(singleton=1), config BLOB NOT NULL)')
            encoded = canonical(list(self._limits))
            db.execute('INSERT OR IGNORE INTO cache_config VALUES (1,?)', (encoded,))
            if db.execute('SELECT config FROM cache_config WHERE singleton=1').fetchone()[0] != encoded:
                raise PersistenceRefusal('cache_configuration_changed')
            db.execute('CREATE TABLE IF NOT EXISTS cache_entries '
                       '(key BLOB PRIMARY KEY, owner TEXT NOT NULL, authority TEXT NOT NULL, '
                       'value BLOB NOT NULL, value_hash TEXT NOT NULL, size INTEGER NOT NULL, '
                       'touch INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS cache_scopes '
                       '(owner TEXT PRIMARY KEY, authority TEXT NOT NULL)')

    def _current(self):
        authority = self._read()
        identity = authority_identity(authority)
        s = authority.scope
        owner = digest([s.tenant, s.principal, s.replay_identity])
        return authority, identity, owner

    def _assert_current(self, identity):
        if self._current()[1] != identity:
            raise CacheScopeChanged('cache_scope_changed_before_disclosure')

    @staticmethod
    def _key(authority, identity, namespace, name, version, schema_hash, request):
        allowed = authority.schema_names if namespace == 'schema' else authority.immutable_reads
        if type(name) is not str or name not in allowed:
            raise PersistenceRefusal('cache_read_not_authorized')
        _text(version)
        _hash(schema_hash)
        raw = canonical(request)
        if len(raw) > MAX_ENTRY_BYTES:
            raise PersistenceRefusal('cache_request_budget')
        return canonical({'authority': identity, 'namespace': namespace, 'name': name,
                          'content_version': version, 'schema_sha256': schema_hash,
                          'request_sha256': sha256(raw).hexdigest()})

    @staticmethod
    def _refresh_scope(db, owner, identity):
        old = db.execute('SELECT authority FROM cache_scopes WHERE owner=?', (owner,)).fetchone()
        if old and old[0] != identity:
            db.execute('DELETE FROM cache_entries WHERE owner=?', (owner,))
        db.execute('INSERT INTO cache_scopes VALUES (?,?) ON CONFLICT(owner) '
                   'DO UPDATE SET authority=excluded.authority', (owner, identity))
        # Scope rows are metadata, not persisted grants; bound them too.
        db.execute('DELETE FROM cache_scopes WHERE owner NOT IN '
                   '(SELECT owner FROM cache_entries) AND owner != ?', (owner,))

    def _encode_entry(self, key, namespace, identity, version, schema_hash, value):
        """Identical content contract for every persistence adapter."""
        raw_value = canonical(value)
        if namespace == 'schema' and sha256(raw_value).hexdigest() != schema_hash:
            raise PersistenceRefusal('cache_schema_hash_mismatch')
        raw = canonical({'cache_kind': namespace, 'scope_sha256': identity,
                         'content_version': version, 'schema_sha256': schema_hash,
                         'value_sha256': sha256(raw_value).hexdigest(),
                         'value': json.loads(raw_value), 'authorizes_execution': False,
                         'fresh_execution': False, 'creates_receipt': False})
        size = len(key) + len(raw)
        if size > self._limits[2]:
            raise PersistenceRefusal('cache_entry_budget')
        return raw, sha256(raw).hexdigest(), size

    def _decode_entry(self, row, key, namespace, identity, version, schema_hash):
        raw, checksum, size = row
        if (type(raw) is not bytes or len(raw) + len(key) != size
                or size > self._limits[2] or sha256(raw).hexdigest() != checksum):
            raise PersistenceRefusal('cache_content_corrupt')
        result = loads_json(raw.decode('utf-8'))
        if (type(result) is not dict or canonical(result) != raw
                or result.get('scope_sha256') != identity
                or result.get('cache_kind') != namespace
                or result.get('content_version') != version
                or result.get('schema_sha256') != schema_hash
                or result.get('value_sha256') != digest(result.get('value'))
                or any(result.get(k) is not False for k in
                       ('fresh_execution', 'creates_receipt', 'authorizes_execution'))):
            raise PersistenceRefusal('cache_content_corrupt')
        return result

    def _get(self, namespace, name, version, schema_hash, request):
        authority, identity, owner = self._current()
        key = self._key(authority, identity, namespace, name, version, schema_hash, request)
        with self.db.transaction() as db:
            self._assert_current(identity)
            self._refresh_scope(db, owner, identity)
            row = db.execute('SELECT value,value_hash,size FROM cache_entries '
                             'WHERE key=? AND owner=? AND authority=?', (key, owner, identity)).fetchone()
            result = None
            if row:
                result = self._decode_entry(row, key, namespace, identity, version, schema_hash)
                db.execute('UPDATE cache_entries SET touch=(SELECT COALESCE(MAX(touch),0)+1 '
                           'FROM cache_entries) WHERE key=?', (key,))
            self._assert_current(identity)
        self._assert_current(identity)
        return result

    def _put(self, namespace, name, version, schema_hash, request, value):
        authority, identity, owner = self._current()
        key = self._key(authority, identity, namespace, name, version, schema_hash, request)
        raw, checksum, size = self._encode_entry(key, namespace, identity, version, schema_hash, value)
        with self.db.transaction() as db:
            self._assert_current(identity)
            self._refresh_scope(db, owner, identity)
            touch = db.execute('SELECT COALESCE(MAX(touch),0)+1 FROM cache_entries').fetchone()[0]
            db.execute('INSERT OR REPLACE INTO cache_entries VALUES (?,?,?,?,?,?,?)',
                       (key, owner, identity, raw, checksum, size, touch))
            while True:
                count, total = db.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM cache_entries').fetchone()
                if count <= self._limits[0] and total <= self._limits[1]:
                    break
                db.execute('DELETE FROM cache_entries WHERE key IN '
                           '(SELECT key FROM cache_entries ORDER BY touch,key LIMIT 1)')
            self._assert_current(identity)
        self._assert_current(identity)

    def get_schema(self, name, *, content_version, schema_sha256):
        return self._get('schema', name, content_version, schema_sha256, None)

    def put_schema(self, name, value, *, content_version, schema_sha256):
        return self._put('schema', name, content_version, schema_sha256, None, value)

    def get_result(self, name, *, request, content_version, schema_sha256):
        return self._get('immutable_read', name, content_version, schema_sha256, request)

    def put_result(self, name, value, *, request, content_version, schema_sha256):
        return self._put('immutable_read', name, content_version, schema_sha256, request, value)
