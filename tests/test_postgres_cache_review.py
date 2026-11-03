"""Independent real transaction-pool tests for cache authority and atomicity.

Use the pinned private PostgreSQL/PgBouncer harness. No hosted connection or
fake storage is used. The connection wrappers below expose fault boundaries
while every SQL statement and transaction still runs in PostgreSQL.
"""
from contextlib import contextmanager
from dataclasses import replace
import time
from unittest.mock import patch
from uuid import uuid4

import pytest

from ciw import postgres_cache
from ciw.persistent_cache import PersistenceRefusal
from ciw.toolbox_cache import CacheScopeChanged
from test_distributed_coordinator import active_connection, runtime
from test_persistent_cache import authority


ARGS = dict(request={'id': 'kept'}, content_version='v1', schema_sha256='c' * 64)
_SETTINGS = ('statement_timeout', 'lock_timeout',
             'idle_in_transaction_session_timeout', 'synchronous_commit',
             'search_path', 'default_transaction_isolation',
             'default_transaction_read_only')


@pytest.fixture
def review_cache(runtime):
    # This review specifically qualifies transaction pooling, not direct-only SQL.
    assert runtime.pooler is not None, 'review requires pinned local PgBouncer'
    postgres_cache.install_schema(runtime.direct_dsn)
    name = 'review-' + uuid4().hex

    def make(read=authority, **limits):
        return postgres_cache.PostgresPersistentCache(
            runtime.dsn, read, cache_id=name, **limits)

    return name, make


def _snapshot(runtime, name):
    """Independent direct read: do not consume or alter the cache's pooled state."""
    with active_connection(runtime.direct_dsn) as db:
        metadata = db.execute('SELECT limits,next_touch FROM odsp_persistent_cache_v1.caches '
                              'WHERE cache_id=%s', (name,)).fetchall()
        entries = db.execute('SELECT key_hash,key,owner,authority,value,value_hash,size,touch '
                             'FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s '
                             'ORDER BY key_hash', (name,)).fetchall()
        scopes = db.execute('SELECT owner,authority FROM odsp_persistent_cache_v1.scopes '
                            'WHERE cache_id=%s ORDER BY owner', (name,)).fetchall()
    return metadata, entries, scopes


def _redacted(error, runtime):
    assert type(error) is PersistenceRefusal
    assert str(error) == 'postgres_cache_storage_unavailable'
    assert runtime.dsn not in str(error)
    assert runtime.direct_dsn not in str(error)
    assert error.__cause__ is None and error.__suppress_context__ is True


def test_pooler_backend_defaults_cannot_weaken_one_transaction(runtime):
    import psycopg
    assert runtime.pooler is not None, 'review requires pinned local PgBouncer'
    poisoned = ('37s', '11s', '42s', 'off', 'public', 'serializable', 'on')
    # The observer must itself be pool-compatible; otherwise its repeated
    # settings queries create named statements before the cache is tested.
    def observe():
        return psycopg.connect(runtime.dsn, connect_timeout=3, prepare_threshold=None)

    with observe() as db:
        baseline = tuple(db.execute('SELECT current_setting(%s)', (key,)).fetchone()[0]
                         for key in _SETTINGS)
        pid = db.execute('SELECT pg_backend_pid()').fetchone()[0]
        for key, value in zip(_SETTINGS, poisoned):
            db.execute('SELECT set_config(%s,%s,false)', (key, value))
    try:
        with postgres_cache._connection(runtime.dsn) as db:
            assert db.execute('SELECT pg_backend_pid()').fetchone()[0] == pid
            assert db.execute(
                "SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                "current_setting('idle_in_transaction_session_timeout'),"
                "current_setting('synchronous_commit'),current_setting('search_path'),"
                "current_setting('transaction_isolation'),current_setting('transaction_read_only')"
            ).fetchone() == ('5s', '2s', '5s', 'on', 'pg_catalog', 'read committed', 'off')
            # Exceed the driver's ordinary preparation threshold on the real pool.
            for number in range(12):
                assert db.execute('SELECT %s::int', (number,)).fetchone() == (number,)
            assert db.execute('SELECT count(*) FROM pg_prepared_statements').fetchone() == (0,)
        with observe() as db:
            assert db.execute('SELECT pg_backend_pid()').fetchone()[0] == pid
            after = tuple(db.execute('SELECT current_setting(%s)', (key,)).fetchone()[0]
                          for key in _SETTINGS)
            assert after == poisoned
    finally:
        # Restore this isolated fixture's defaults, even if an assertion fails.
        with observe() as db:
            for key, value in zip(_SETTINGS, baseline):
                db.execute('SELECT set_config(%s,%s,false)', (key, value))


def test_real_statement_timeout_rolls_back_and_redacts(review_cache, runtime):
    import psycopg
    name, make = review_cache
    make().put_result('ncp_fixture_read', {'kept': '界🌍'}, **ARGS)
    before = _snapshot(runtime, name)
    started = time.monotonic()
    with patch.object(psycopg, 'connect', wraps=psycopg.connect) as connects:
        with pytest.raises(PersistenceRefusal) as caught:
            with postgres_cache._connection(runtime.dsn) as db:
                db.execute('DELETE FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s', (name,))
                db.execute('SELECT pg_sleep(10) /* private-review-query-marker */')
        assert connects.call_count == 1  # No automatic reconnect or replay.
    elapsed = time.monotonic() - started
    _redacted(caught.value, runtime)
    assert 'private-review-query-marker' not in str(caught.value)
    assert 4 <= elapsed < 10, elapsed
    assert _snapshot(runtime, name) == before
    assert make().get_result('ncp_fixture_read', **ARGS)['value'] == {'kept': '界🌍'}


def test_real_lock_timeout_rolls_back_without_retry(review_cache, runtime):
    import psycopg
    name, make = review_cache
    cache = make()
    cache.put_result('ncp_fixture_read', {'kept': True}, **ARGS)
    before = _snapshot(runtime, name)
    with active_connection(runtime.direct_dsn) as blocker:
        blocker.execute('SELECT cache_id FROM odsp_persistent_cache_v1.caches '
                        'WHERE cache_id=%s FOR UPDATE', (name,))
        started = time.monotonic()
        with patch.object(psycopg, 'connect', wraps=psycopg.connect) as connects:
            with pytest.raises(PersistenceRefusal) as caught:
                cache.put_result('ncp_fixture_read', {'must_not_replace': True}, **ARGS)
            assert connects.call_count == 1
        elapsed = time.monotonic() - started
    _redacted(caught.value, runtime)
    assert 1.5 <= elapsed < 5, elapsed
    assert _snapshot(runtime, name) == before
    assert cache.get_result('ncp_fixture_read', **ARGS)['value'] == {'kept': True}


def test_exact_serialized_byte_budget_is_atomic_before_any_database_io(review_cache, runtime):
    import psycopg
    name, make = review_cache
    maximum = 1200
    cache = make(max_entries=1, max_bytes=maximum, max_entry_bytes=maximum)
    cache.put_result('ncp_fixture_read', {'payload': ''}, **ARGS)
    observed_size = _snapshot(runtime, name)[1][0][6]
    assert 0 < observed_size < maximum
    # Derive the boundary from real stored bytes, including key and envelope.
    payload = 'x' * (maximum - observed_size)
    cache.put_result('ncp_fixture_read', {'payload': payload}, **ARGS)
    before = _snapshot(runtime, name)
    assert before[1][0][6] == maximum
    for additional in ('x', '界'):
        with patch.object(psycopg, 'connect', side_effect=AssertionError('oversize cache touched database')):
            with pytest.raises(PersistenceRefusal, match='cache_entry_budget'):
                cache.put_result('ncp_fixture_read', {'payload': payload + additional}, **ARGS)
        assert _snapshot(runtime, name) == before
    assert cache.get_result('ncp_fixture_read', **ARGS)['value'] == {'payload': payload}


@pytest.mark.parametrize('method', ['commit', 'rollback'])
def test_authority_callback_cannot_end_partial_cache_transaction(review_cache, runtime, method):
    name, make = review_cache
    make(max_entries=1).put_result('ncp_fixture_read', {'kept': True}, **ARGS)
    before = _snapshot(runtime, name)
    exposed, reads = [], []
    original_connection = postgres_cache._connection

    @contextmanager
    def actual_connection(dsn):
        with original_connection(dsn) as db:
            exposed.append(db)
            try:
                yield db
            finally:
                exposed.pop()

    def read():
        reads.append(1)
        if len(reads) == 3:
            # DML and budget eviction have happened, but admission is incomplete.
            getattr(exposed[-1], method)()
        return authority()

    cache = make(read, max_entries=1)
    with patch.object(postgres_cache, '_connection', actual_connection):
        with pytest.raises(PersistenceRefusal) as caught:
            cache.put_result('ncp_fixture_read', {'must_not_persist': True},
                             **dict(ARGS, request={'id': 'replacement'}))
    _redacted(caught.value, runtime)
    assert len(reads) == 3
    assert _snapshot(runtime, name) == before  # Includes eviction, scopes and sequence.


@pytest.mark.parametrize('operation', ['get', 'put'])
@pytest.mark.parametrize('revocation_read', [3, 4], ids=['before_commit', 'after_commit'])
def test_revocation_across_commit_withholds_content_and_preserves_atomicity(
        review_cache, runtime, operation, revocation_read):
    name, make = review_cache
    make(max_entries=1).put_result('ncp_fixture_read', {'kept': True}, **ARGS)
    before = _snapshot(runtime, name)
    reads, snapshots = [], []

    def read():
        reads.append(1)
        if len(reads) >= revocation_read:
            # Observe independently whether the change is visible at revocation.
            snapshots.append(_snapshot(runtime, name))
            return replace(authority(), immutable_reads=())
        return authority()

    cache = make(read, max_entries=1)
    with pytest.raises(CacheScopeChanged):
        if operation == 'get':
            cache.get_result('ncp_fixture_read', **ARGS)
        else:
            cache.put_result('ncp_fixture_read', {'replacement': '界🌍'}, **ARGS)
    assert len(reads) == revocation_read
    after = _snapshot(runtime, name)
    if revocation_read == 3:
        assert snapshots == [before] and after == before
    else:
        assert snapshots == [after] and after != before
        assert after[0][0][1] == before[0][0][1] + 1
    with pytest.raises(PersistenceRefusal, match='not_authorized'):
        cache.get_result('ncp_fixture_read', **ARGS)


def test_backend_termination_after_write_rolls_back_without_replay(review_cache, runtime):
    import psycopg
    name, make = review_cache
    make(max_entries=1).put_result('ncp_fixture_read', {'kept': True}, **ARGS)
    before = _snapshot(runtime, name)
    exposed, reads = [], []
    original_connection = postgres_cache._connection
    direct_connect = psycopg.connect

    @contextmanager
    def actual_connection(dsn):
        with original_connection(dsn) as db:
            exposed.append(db)
            try:
                yield db
            finally:
                exposed.pop()

    def read():
        reads.append(1)
        if len(reads) == 3:
            pid = exposed[-1].execute('SELECT pg_backend_pid()').fetchone()[0]
            with direct_connect(runtime.direct_dsn, connect_timeout=3) as control:
                assert control.execute('SELECT pg_terminate_backend(%s)', (pid,)).fetchone() == (True,)
        return authority()

    cache = make(read, max_entries=1)
    try:
        with patch.object(postgres_cache, '_connection', actual_connection):
            with patch.object(psycopg, 'connect', wraps=psycopg.connect) as connects:
                with pytest.raises(PersistenceRefusal) as caught:
                    cache.put_result('ncp_fixture_read', {'must_not_persist': True},
                                     **dict(ARGS, request={'id': 'replacement'}))
                assert connects.call_count == 1
        _redacted(caught.value, runtime)
        assert len(reads) == 3
        assert _snapshot(runtime, name) == before
    finally:
        # Only a SELECT 1 readiness probe is retried; no cache operation is replayed.
        runtime.pooler.wait_ready()
