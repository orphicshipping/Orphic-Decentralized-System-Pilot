"""Real PostgreSQL cache: process sharing, restart, contention and revocation.

Reuses the private pinned PostgreSQL/NATS qualification harness; no hosted DB.
ODSP_REQUIRE_DISTRIBUTED=1 turns missing runtime prerequisites into a failure.
"""
from dataclasses import replace
from hashlib import sha256
import multiprocessing
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from ciw.persistent_cache import PersistenceRefusal
from ciw.postgres_cache import PostgresPersistentCache, install_schema
from ciw.toolbox_cache import CacheScopeChanged, canonical, digest
from test_persistent_cache import authority
from test_distributed_coordinator import runtime, active_connection


@pytest.fixture
def configured_cache(runtime):
    # Administrative migration uses the direct database endpoint. Runtime calls
    # exercise the pooled endpoint when the qualification fixture provides one.
    install_schema(runtime.direct_dsn)
    name = 'fixture-' + uuid4().hex
    def make(read=authority, **limits):
        return PostgresPersistentCache(runtime.dsn, read, cache_id=name, **limits)
    return name, make


def test_transaction_pooler_has_no_prepared_or_leaked_session_state(runtime):
    from ciw.postgres_cache import _connection
    with active_connection(runtime.dsn) as db:
        before = db.execute("SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                            "current_setting('search_path'),pg_backend_pid()").fetchone()
    with _connection(runtime.dsn) as db:
        for number in range(12):
            assert db.execute('SELECT %s::int', (number,)).fetchone() == (number,)
        assert db.execute('SELECT count(*) FROM pg_prepared_statements').fetchone() == (0,)
        pid = db.execute('SELECT pg_backend_pid()').fetchone()[0]
        assert db.execute("SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                          "current_setting('synchronous_commit'),current_setting('search_path')").fetchone() == (
                              '5s', '2s', 'on', 'pg_catalog')
    with active_connection(runtime.dsn) as db:
        after = db.execute("SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                           "current_setting('search_path'),pg_backend_pid()").fetchone()
    assert before[:3] == after[:3]
    if runtime.pooler is not None:
        assert before[3] == pid == after[3]


def test_transaction_abort_keeps_cache_content_and_returns_clean_backend(configured_cache, runtime):
    from ciw.postgres_cache import _connection
    name, make = configured_cache
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    make().put_result('ncp_fixture_read', {'original': True}, **args)
    with active_connection(runtime.dsn) as db:
        before = db.execute("SELECT current_setting('statement_timeout'),pg_backend_pid()").fetchone()
    with pytest.raises(ValueError, match='deliberate transaction abort'):
        with _connection(runtime.dsn) as db:
            pid = db.execute('SELECT pg_backend_pid()').fetchone()[0]
            db.execute('DELETE FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s', (name,))
            raise ValueError('deliberate transaction abort')
    with active_connection(runtime.dsn) as db:
        after = db.execute("SELECT current_setting('statement_timeout'),pg_backend_pid()").fetchone()
    assert before[0] == after[0]
    if runtime.pooler is not None:
        assert before[1] == pid == after[1]
    assert make().get_result('ncp_fixture_read', **args)['value'] == {'original': True}


def _reader_process(dsn, name, queue):
    try:
        schema = {'name': 'ncp_fixture_schema', 'inputSchema': {'type': 'object'}}
        cache = PostgresPersistentCache(dsn, authority, cache_id=name)
        queue.put(cache.get_schema('ncp_fixture_schema', content_version='v1', schema_sha256=digest(schema)))
    except Exception as exc:
        queue.put({'failure': type(exc).__name__, 'reason': str(exc)})


def _writer_process(dsn, name, index, ready, queue):
    try:
        cache = PostgresPersistentCache(dsn, authority, cache_id=name, max_entries=24, max_bytes=24000)
        ready.wait(timeout=15)
        for row in range(12):
            cache.put_result('ncp_fixture_read', {'process': index, 'row': row},
                request={'row': index * 100 + row}, content_version='v1', schema_sha256='c' * 64)
            with active_connection(dsn) as db:
                count, size = db.execute('SELECT count(*),COALESCE(sum(size),0) '
                    'FROM odsp_persistent_cache_v1.entries WHERE cache_id=%s', (name,)).fetchone()
                assert count <= 24 and size <= 24000
        queue.put('ok')
    except Exception as exc:
        queue.put(type(exc).__name__ + ':' + str(exc))


def test_real_fresh_process_read_and_database_crash_restart(configured_cache, runtime):
    name, make = configured_cache
    schema = {'name': 'ncp_fixture_schema', 'inputSchema': {'type': 'object'}}
    make().put_schema('ncp_fixture_schema', schema, content_version='v1', schema_sha256=digest(schema))
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()
    child = context.Process(target=_reader_process, args=(runtime.dsn, name, queue))
    child.start()
    result = queue.get(timeout=20)
    child.join(20)
    assert child.exitcode == 0 and result['value'] == schema
    assert result['fresh_execution'] is False and result['creates_receipt'] is False
    cache = make()
    runtime.stop_process('pg', crash=True)
    try:
        with pytest.raises(PersistenceRefusal, match='postgres_cache_storage_unavailable') as refused:
            cache.get_schema('ncp_fixture_schema', content_version='v1', schema_sha256=digest(schema))
        assert runtime.dsn not in str(refused.value)
    finally:
        runtime.start_postgres()
    assert make().get_schema('ncp_fixture_schema', content_version='v1', schema_sha256=digest(schema)) == result


def test_independent_process_writers_enforce_one_shared_budget(configured_cache, runtime):
    name, make = configured_cache
    make(max_entries=24, max_bytes=24000)
    context = multiprocessing.get_context('spawn')
    ready, queue = context.Event(), context.Queue()
    children = [context.Process(target=_writer_process, args=(runtime.dsn, name, index, ready, queue))
                for index in range(4)]
    for child in children:
        child.start()
    ready.set()
    results = [queue.get(timeout=30) for _ in children]
    for child in children:
        child.join(20)
        assert child.exitcode == 0
    assert results == ['ok'] * 4
    with active_connection(runtime.dsn) as db:
        count, size = db.execute('SELECT count(*),sum(size) FROM odsp_persistent_cache_v1.entries '
                                'WHERE cache_id=%s', (name,)).fetchone()
        assert 0 < count <= 24 and size <= 24000
        assert db.execute('SELECT next_touch FROM odsp_persistent_cache_v1.caches '
                          'WHERE cache_id=%s', (name,)).fetchone()[0] == 48


def test_current_authority_every_access_and_scope_never_crosses(configured_cache):
    _, make = configured_cache
    current = [authority()]
    cache = make(lambda: current[0])
    args = dict(request={'id': 'one'}, content_version='v1', schema_sha256='c' * 64)
    cache.put_result('ncp_fixture_read', {'fixture': 1}, **args)
    for changes in ({'principal': 'bob'}, {'tenant': 'other'}, {'policy_sha256': 'b' * 64},
                    {'pins': (('source', 'v2'),)}, {'replay_identity': 'other'}):
        current[0] = replace(authority(), scope=replace(authority().scope, **changes))
        assert cache.get_result('ncp_fixture_read', **args) is None
    current[0] = replace(authority(), immutable_reads=())
    with pytest.raises(PersistenceRefusal, match='not_authorized'):
        cache.get_result('ncp_fixture_read', **args)
    with pytest.raises(PersistenceRefusal, match='not_authorized'):
        cache.put_result('ncp_fixture_read', {'new': 'fixture'}, **args)
    with pytest.raises(PersistenceRefusal, match='not_authorized'):
        make().put_result('ncp_run_submit', {}, **args)
    assert not hasattr(cache, 'invoke')
    with pytest.raises(TypeError):
        cache.get_result('ncp_fixture_read', principal='bob', **args)


def test_policy_change_after_storage_read_withholds_content(configured_cache):
    _, make = configured_cache
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    make().put_result('ncp_fixture_read', {'private': 'fixture'}, **args)
    reads = []
    def current():
        reads.append(1)
        return authority(policy='a' if len(reads) < 3 else 'b')
    with pytest.raises(CacheScopeChanged):
        make(current).get_result('ncp_fixture_read', **args)
    assert len(reads) >= 3


def test_policy_change_while_waiting_for_database_lock_prevents_write(configured_cache, runtime):
    name, make = configured_cache
    current = [authority()]
    observed = Event()
    def read():
        observed.set()
        return current[0]
    cache = make(read)
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    with ThreadPoolExecutor(1) as executor:
        with active_connection(runtime.dsn) as db:
            db.execute('SELECT cache_id FROM odsp_persistent_cache_v1.caches '
                       'WHERE cache_id=%s FOR UPDATE', (name,))
            pending = executor.submit(cache.put_result, 'ncp_fixture_read', {'forbidden': True}, **args)
            assert observed.wait(2)
            current[0] = authority(policy='b')
        with pytest.raises(CacheScopeChanged):
            pending.result(timeout=10)
    with active_connection(runtime.dsn) as db:
        assert db.execute('SELECT count(*) FROM odsp_persistent_cache_v1.entries '
                          'WHERE cache_id=%s', (name,)).fetchone()[0] == 0


def test_exact_contract_parity_with_sqlite_and_global_lru(configured_cache, tmp_path):
    from ciw.persistent_cache import PersistentCache
    _, make = configured_cache
    tmp_path.chmod(0o700)
    pg = make(max_entries=2, max_bytes=5000)
    local = PersistentCache(tmp_path / 'parity.sqlite', authority, max_entries=2, max_bytes=5000)
    args = dict(content_version='v1', schema_sha256='c' * 64)
    for cache in (pg, local):
        for value in range(2):
            cache.put_result('ncp_fixture_read', {'value': value, 'unicode': '界🌍'}, request={'id': value}, **args)
        assert cache.get_result('ncp_fixture_read', request={'id': 0}, **args)['value']['value'] == 0
        cache.put_result('ncp_fixture_read', {'value': 2}, request={'id': 2}, **args)
        assert cache.get_result('ncp_fixture_read', request={'id': 1}, **args) is None
    for index in (0, 2):
        assert pg.get_result('ncp_fixture_read', request={'id': index}, **args) == local.get_result(
            'ncp_fixture_read', request={'id': index}, **args)


def test_corrupted_content_cannot_create_execution_claim(configured_cache, runtime):
    name, make = configured_cache
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    make().put_result('ncp_fixture_read', {'x': 1}, **args)
    value = make().get_result('ncp_fixture_read', **args)
    value['authorizes_execution'] = True
    raw = canonical(value)
    with active_connection(runtime.dsn) as db:
        db.execute('UPDATE odsp_persistent_cache_v1.entries SET value=%s,value_hash=%s,'
                   'size=octet_length(key)+%s WHERE cache_id=%s',
                   (raw, sha256(raw).hexdigest(), len(raw), name))
    with pytest.raises(PersistenceRefusal, match='cache_content_corrupt'):
        make().get_result('ncp_fixture_read', **args)


def test_limits_cannot_widen_and_invalid_content_never_persists(configured_cache, runtime):
    name, make = configured_cache
    cache = make(max_entry_bytes=1000)
    with pytest.raises(PersistenceRefusal, match='configuration_changed'):
        make(max_entry_bytes=2000)
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    with pytest.raises(PersistenceRefusal, match='cache_entry_budget'):
        cache.put_result('ncp_fixture_read', {'value': '界' * 1000}, **args)
    with pytest.raises(ValueError):
        cache.put_result('ncp_fixture_read', {'value': float('nan')}, **args)
    with pytest.raises(PersistenceRefusal, match='schema_hash_mismatch'):
        cache.put_schema('ncp_fixture_schema', {}, content_version='v1', schema_sha256='0' * 64)
    with pytest.raises(PersistenceRefusal, match='host_cache_identity'):
        PostgresPersistentCache(runtime.dsn, authority, cache_id='public; DROP TABLE anything')
    with active_connection(runtime.dsn) as db:
        assert db.execute('SELECT count(*) FROM odsp_persistent_cache_v1.entries '
                          'WHERE cache_id=%s', (name,)).fetchone()[0] == 0


def test_unavailable_current_authority_prevents_io(configured_cache, runtime):
    name, make = configured_cache
    def unavailable():
        raise PermissionError('authority_unavailable')
    cache = make(unavailable)
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    with pytest.raises(PermissionError):
        cache.get_result('ncp_fixture_read', **args)
    with pytest.raises(PermissionError):
        cache.put_result('ncp_fixture_read', {}, **args)
    with active_connection(runtime.dsn) as db:
        assert db.execute('SELECT next_touch FROM odsp_persistent_cache_v1.caches '
                          'WHERE cache_id=%s', (name,)).fetchone()[0] == 0


def test_scope_metadata_is_bounded_by_retained_owner_partitions(configured_cache, runtime):
    name, make = configured_cache
    current = [authority()]
    cache = make(lambda: current[0], max_entries=2)
    for index in range(12):
        current[0] = authority(principal='fixture-' + str(index))
        cache.put_result('ncp_fixture_read', {'value': index}, request={}, content_version='v1', schema_sha256='c' * 64)
    with active_connection(runtime.dsn) as db:
        assert db.execute('SELECT count(*) FROM odsp_persistent_cache_v1.scopes '
                          'WHERE cache_id=%s', (name,)).fetchone()[0] <= 3


def test_maximum_escaped_content_version_does_not_overflow_postgres_index(configured_cache):
    _, make = configured_cache
    args = dict(request={}, content_version='\x00' * 512, schema_sha256='c' * 64)
    cache = make()
    cache.put_result('ncp_fixture_read', {'value': 'bounded fixture'}, **args)
    assert cache.get_result('ncp_fixture_read', **args)['value'] == {'value': 'bounded fixture'}
