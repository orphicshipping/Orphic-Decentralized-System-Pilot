"""Host-scoped cache contracts; no services, network or invocation in this module."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import unittest

from ciw.toolbox_cache import (CacheAuthority, CacheScope, CacheScopeChanged,
                               ToolboxCache, canonical, digest)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.scope = CacheScope('tenant-a', 'alice', 'a' * 64, (('runtime', 'pin-1'),), 'replay-1')
        self.authority = CacheAuthority(self.scope, ('ncp_schema',), ('ncp_retained_read',))
        self.reads = 0
        self.failed = False
        self.cache = ToolboxCache(self.read)
        self.schema = {'name': 'ncp_schema', 'inputSchema': {'type': 'object'}, 'description': '界🌍'}
        self.schema_hash = digest(self.schema)
        self.schema_args = {'content_version': 'source-v1', 'schema_sha256': self.schema_hash}
        self.result_args = {'request': {'artifact_id': 'retained-1'},
                            'content_version': 'capture-v1', 'schema_sha256': self.schema_hash}

    def read(self):
        self.reads += 1
        if self.failed:
            raise PermissionError('host_revoked')
        return self.authority

    def test_exact_schema_and_immutable_result_detached(self):
        self.cache.put_schema('ncp_schema', self.schema, **self.schema_args)
        out = self.cache.get_schema('ncp_schema', **self.schema_args)
        self.assertEqual(out['value'], self.schema)
        self.assertEqual(out['value_sha256'], self.schema_hash)
        out['value']['inputSchema']['type'] = 'altered'
        self.assertEqual(self.cache.get_schema('ncp_schema', **self.schema_args)['value'], self.schema)
        result = {'retained': ['界', 123], 'receipt_digest': 'existing-only'}
        self.cache.put_result('ncp_retained_read', result, **self.result_args)
        result['retained'].clear()
        hit = self.cache.get_result('ncp_retained_read', **self.result_args)
        self.assertEqual(hit['value']['retained'], ['界', 123])
        self.assertFalse(hit['fresh_execution'])
        self.assertFalse(hit['creates_receipt'])
        self.assertFalse(hit['authorizes_execution'])
        self.assertEqual(self.reads, 5)

    def test_refresh_failure_clears_before_return(self):
        self.cache.put_schema('ncp_schema', self.schema, **self.schema_args)
        self.failed = True
        with self.assertRaises(PermissionError):
            self.cache.get_schema('ncp_schema', **self.schema_args)
        self.failed = False
        self.assertIsNone(self.cache.get_schema('ncp_schema', **self.schema_args))

    def test_scope_isolation_and_invalidation(self):
        changes = [dict(tenant='tenant-b'), dict(principal='bob'),
                   dict(policy_sha256='b' * 64), dict(pins=(('runtime', 'pin-2'),)),
                   dict(replay_identity='replay-2')]
        for change in changes:
            with self.subTest(change=change):
                self.authority = CacheAuthority(self.scope, ('ncp_schema',), ('ncp_retained_read',))
                cache = ToolboxCache(self.read)
                cache.put_schema('ncp_schema', self.schema, **self.schema_args)
                cache.put_result('ncp_retained_read', {'x': 1}, **self.result_args)
                self.authority = replace(self.authority, scope=replace(self.scope, **change))
                with self.assertRaises(CacheScopeChanged):
                    cache.get_result('ncp_retained_read', **self.result_args)
                self.assertIsNone(cache.get_schema('ncp_schema', **self.schema_args))
                self.assertIsNone(cache.get_result('ncp_retained_read', **self.result_args))

    def test_grant_removal_even_if_host_forgets_policy_hash_change(self):
        self.cache.put_result('ncp_retained_read', {'x': 1}, **self.result_args)
        self.authority = replace(self.authority, immutable_reads=())
        with self.assertRaises(CacheScopeChanged):
            self.cache.get_result('ncp_retained_read', **self.result_args)
        with self.assertRaisesRegex(ValueError, 'not_authorized'):
            self.cache.get_result('ncp_retained_read', **self.result_args)
        self.authority = replace(self.authority, immutable_reads=('ncp_retained_read',))
        with self.assertRaises(CacheScopeChanged):
            self.cache.get_result('ncp_retained_read', **self.result_args)
        self.assertIsNone(self.cache.get_result('ncp_retained_read', **self.result_args))

    def test_no_execution_memoization_or_caller_scope_parameters(self):
        for name in ('ncp_run_submit', 'ncp_plan_admit', 'ncp_payment'):
            with self.assertRaisesRegex(ValueError, 'not_authorized'):
                self.cache.put_result(name, {}, **self.result_args)
        for field in ('tenant', 'principal', 'pins', 'policy_sha256', 'replay_identity'):
            with self.assertRaises(TypeError):
                self.cache.get_result('ncp_retained_read', **self.result_args, **{field: 'override'})
        self.assertFalse(hasattr(self.cache, 'call_tool'))
        self.assertFalse(hasattr(self.cache, 'invoke'))

    def test_content_request_schema_versions_and_namespaces_are_distinct(self):
        self.cache.put_schema('ncp_schema', self.schema, **self.schema_args)
        self.cache.put_result('ncp_retained_read', {'x': 1}, **self.result_args)
        for field, value in [('request', {'artifact_id': 'retained-2'}),
                             ('content_version', 'capture-v2'), ('schema_sha256', 'b' * 64)]:
            args = {**self.result_args, field: value}
            self.assertIsNone(self.cache.get_result('ncp_retained_read', **args))
        self.assertIsNone(self.cache.get_schema('ncp_schema', **{**self.schema_args, 'content_version': 'v2'}))
        with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
            self.cache.put_schema('ncp_schema', {'bad': True}, **self.schema_args)
        self.assertEqual(self.cache.get_schema('ncp_schema', **self.schema_args)['value'], self.schema)

    def test_entry_bound_unicode_and_invalid_json(self):
        cache = ToolboxCache(self.read, max_entry_bytes=1000)
        with self.assertRaisesRegex(ValueError, 'budget_exceeded'):
            cache.put_result('ncp_retained_read', {'value': '界🌍' * 100}, **self.result_args)
        self.assertIsNone(cache.get_result('ncp_retained_read', **self.result_args))
        for bad in (float('nan'), object(), ('tuple',)):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises((ValueError, TypeError)):
                    cache.put_result('ncp_retained_read', {'value': bad}, **self.result_args)
        with self.assertRaisesRegex(ValueError, 'request_budget'):
            self.cache.get_result('ncp_retained_read', **{**self.result_args, 'request': ['界' * 60000]})

    def test_lru_entry_and_total_byte_bounds(self):
        cache = ToolboxCache(self.read, max_entries=2, max_bytes=3000)
        args = self.result_args
        def key(version):
            return {**args, 'content_version': version}
        for version in ('v1', 'v2'):
            cache.put_result('ncp_retained_read', {'x': version}, **key(version))
        cache.get_result('ncp_retained_read', **key('v1'))
        cache.put_result('ncp_retained_read', {'x': 'v3'}, **key('v3'))
        self.assertIsNone(cache.get_result('ncp_retained_read', **key('v2')))
        self.assertEqual(cache.get_result('ncp_retained_read', **key('v1'))['value'], {'x': 'v1'})
        cache.put_result('ncp_retained_read', {'x': 'replacement'}, **key('v1'))
        self.assertEqual(cache.get_result('ncp_retained_read', **key('v1'))['value'], {'x': 'replacement'})
        self.assertLessEqual(cache._bytes, 3000)
        tiny = ToolboxCache(self.read, max_entries=64, max_bytes=1500)
        for n in range(10):
            tiny.put_result('ncp_retained_read', {'x': n}, **key(str(n)))
            self.assertLessEqual(tiny._bytes, 1500)
        self.assertIsNone(tiny.get_result('ncp_retained_read', **key('0')))

    def test_deterministic_pins_and_parallel_instances(self):
        scope = replace(self.scope, pins=(('z', '2'), ('a', '1')))
        self.assertEqual(scope.pins, (('a', '1'), ('z', '2')))
        caches = [ToolboxCache(self.read) for _ in range(4)]
        def work(n):
            caches[n].put_result('ncp_retained_read', {'client': n}, **self.result_args)
            for _ in range(20):
                self.assertEqual(caches[n].get_result('ncp_retained_read', **self.result_args)['value'], {'client': n})
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(work, range(4)))
        self.assertEqual(len({c.get_result('ncp_retained_read', **self.result_args)['value']['client'] for c in caches}), 4)

    def test_invalid_host_snapshot_and_limits(self):
        for change in [dict(tenant=''), dict(policy_sha256='guess'), dict(pins=()),
                       dict(pins=(('x', 'a'), ('x', 'b'))), dict(replay_identity='')]:
            with self.assertRaises(ValueError):
                replace(self.scope, **change)
        with self.assertRaises(ValueError):
            CacheAuthority(self.scope, ('duplicate', 'duplicate'))
        for limit in (True, 0, -1, 1025):
            with self.assertRaises(ValueError):
                ToolboxCache(self.read, max_entries=limit)
        cache = ToolboxCache(lambda: {'scope': 'model-supplied'})
        with self.assertRaises(ValueError):
            cache.get_schema('ncp_schema', **self.schema_args)
        self.assertEqual(digest(self.schema), sha256(canonical(self.schema)).hexdigest())
        self.assertEqual(json.loads(canonical(self.schema)), self.schema)


if __name__ == '__main__':
    unittest.main()
