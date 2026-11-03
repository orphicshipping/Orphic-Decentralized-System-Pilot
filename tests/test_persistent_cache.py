"""Actual local SQLite persistence, process contention and current authority."""
from dataclasses import replace
import multiprocessing
from pathlib import Path
import sqlite3

import pytest

from ciw.persistent_cache import PersistentCache, PersistenceRefusal
from ciw.toolbox_cache import CacheAuthority, CacheScope, CacheScopeChanged, digest


def authority(principal='alice', policy='a'):
    return CacheAuthority(CacheScope('lab', principal, policy * 64,
        (('source', 'fixture-v1'),), 'review'), ('ncp_fixture_schema',), ('ncp_fixture_read',))


def _writer(path, index, queue):
    try:
        cache = PersistentCache(path, authority, max_entries=32, max_bytes=65536)
        for i in range(12):
            cache.put_result('ncp_fixture_read', {'writer': index, 'row': i},
                request={'row': index * 100 + i}, content_version='v1', schema_sha256='c' * 64)
        queue.put('ok')
    except Exception as error:
        queue.put(type(error).__name__ + ':' + str(error))


def _read_schema(path, queue):
    try:
        value = {'name': 'ncp_fixture_schema', 'inputSchema': {'type': 'object'}}
        result = PersistentCache(path, authority).get_schema('ncp_fixture_schema',
            content_version='v1', schema_sha256=digest(value))
        queue.put(result)
    except Exception as error:
        queue.put(str(error))


def private(tmp_path, name='cache.sqlite'):
    tmp_path.chmod(0o700)
    return tmp_path / name


def test_persisted_content_read_by_fresh_process(tmp_path):
    path = private(tmp_path)
    value = {'name': 'ncp_fixture_schema', 'inputSchema': {'type': 'object'}}
    cache = PersistentCache(path, authority)
    cache.put_schema('ncp_fixture_schema', value, content_version='v1', schema_sha256=digest(value))
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    child = ctx.Process(target=_read_schema, args=(str(path), queue))
    child.start()
    result = queue.get(timeout=15)
    child.join(15)
    assert child.exitcode == 0
    assert result['value'] == value
    assert result['fresh_execution'] is False and result['creates_receipt'] is False
    assert path.stat().st_mode & 0o777 == 0o600


def test_real_concurrent_writers_share_global_bounds(tmp_path):
    path = private(tmp_path)
    PersistentCache(path, authority, max_entries=32, max_bytes=65536)
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    children = [ctx.Process(target=_writer, args=(str(path), i, queue)) for i in range(4)]
    for child in children:
        child.start()
    results = [queue.get(timeout=20) for _ in children]
    for child in children:
        child.join(20)
        assert child.exitcode == 0
    assert results == ['ok'] * 4
    with sqlite3.connect(path) as db:
        count, size = db.execute('SELECT COUNT(*),SUM(size) FROM cache_entries').fetchone()
        assert count == 32 and size <= 65536
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_every_access_refreshes_and_policy_replay_tenant_scopes_do_not_cross(tmp_path):
    current = [authority()]
    path = private(tmp_path)
    cache = PersistentCache(path, lambda: current[0])
    args = dict(request={'id': 'one'}, content_version='v1', schema_sha256='c' * 64)
    cache.put_result('ncp_fixture_read', {'a': 1}, **args)
    assert cache.get_result('ncp_fixture_read', **args)['value'] == {'a': 1}
    for changes in ({'principal': 'bob'}, {'tenant': 'other'}, {'policy_sha256': 'b' * 64},
                    {'pins': (('source', 'v2'),)}, {'replay_identity': 'other'}):
        current[0] = replace(authority(), scope=replace(authority().scope, **changes))
        assert cache.get_result('ncp_fixture_read', **args) is None
    current[0] = replace(authority(), immutable_reads=())
    with pytest.raises(PersistenceRefusal, match='not_authorized'):
        cache.get_result('ncp_fixture_read', **args)


def test_revocation_after_sqlite_read_withholds_data(tmp_path):
    path = private(tmp_path)
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    PersistentCache(path, authority).put_result('ncp_fixture_read', {'secret': 'fixture'}, **args)
    calls = []
    def reader():
        calls.append(1)
        return authority(policy='a' if len(calls) < 3 else 'b')
    with pytest.raises(CacheScopeChanged):
        PersistentCache(path, reader).get_result('ncp_fixture_read', **args)
    assert len(calls) >= 3


def test_failed_reader_no_read_or_write_and_tamper_refusal(tmp_path):
    path = private(tmp_path)
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    cache = PersistentCache(path, authority)
    cache.put_result('ncp_fixture_read', {'value': 42}, **args)
    def denied():
        raise PermissionError('current policy unavailable')
    with pytest.raises(PermissionError):
        PersistentCache(path, denied).get_result('ncp_fixture_read', **args)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE cache_entries SET value=?', (b'{}',))
    with pytest.raises(PersistenceRefusal, match='corrupt'):
        cache.get_result('ncp_fixture_read', **args)


def test_no_execution_memoization_configuration_widening_or_symlinks(tmp_path):
    path = private(tmp_path)
    cache = PersistentCache(path, authority, max_entries=4)
    with pytest.raises(PersistenceRefusal, match='not_authorized'):
        cache.put_result('ncp_run_submit', {}, request={}, content_version='v1', schema_sha256='c' * 64)
    with pytest.raises(PersistenceRefusal, match='configuration_changed'):
        PersistentCache(path, authority, max_entries=5)
    link = tmp_path / 'link.sqlite'
    link.symlink_to(path)
    with pytest.raises(PersistenceRefusal, match='file_refused'):
        PersistentCache(link, authority)
    path.chmod(0o644)
    with pytest.raises(PersistenceRefusal, match='file_refused'):
        cache.get_result('ncp_fixture_read', request={}, content_version='v1', schema_sha256='c' * 64)


def test_invalid_and_oversized_content_refused_before_storage(tmp_path):
    cache = PersistentCache(private(tmp_path), authority, max_entry_bytes=1024)
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    with pytest.raises(PersistenceRefusal, match='entry_budget'):
        cache.put_result('ncp_fixture_read', {'x': '界' * 1000}, **args)
    with pytest.raises(ValueError):
        cache.put_result('ncp_fixture_read', {'x': float('nan')}, **args)
    assert cache.get_result('ncp_fixture_read', **args) is None
