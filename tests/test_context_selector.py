"""Source-derived client context contracts; stdlib runnable without pytest."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import unittest

from ciw.context_catalog import SOURCE_REVISION, source_registry
from ciw.context_selector import AuthorizedCatalog, ContextSelector, StaleCatalog, serialize


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.registry = source_registry()
        self.revision = SOURCE_REVISION
        self.reader_calls = 0
        self.failed = False
        self.selector = ContextSelector(self.read)
        self.name = self.registry['reference'][0]['name']

    def read(self):
        self.reader_calls += 1
        if self.failed:
            raise PermissionError('host_refresh_unavailable')
        return AuthorizedCatalog(self.revision, self.registry)

    def test_source_reconciliation_and_exact_preservation(self):
        self.assertEqual({k: len(v) for k, v in self.registry.items()}, {
            'control': 35, 'reference': 7, 'evidence': 5, 'logistics': 5,
            'recompiler': 3, 'schematic': 5, 'clocksync': 3, 'state': 8, 'nise': 5,
            'intake': 4, 'proof': 2, 'domain_read': 4,
            'toolbox': 10, 'live': 3, 'metrology': 5, 'data_database': 7})
        for rows in self.registry.values():
            for row in rows:
                self.assertEqual(self.selector.describe(row['name'])['tool'], row)
                self.assertEqual(self.selector.select([row['name']])['tools'], [row])
                self.selector.deselect([row['name']])
        self.assertEqual(sum(map(len, self.registry.values())), 111)

    def test_determinism_and_detached_output(self):
        a = self.selector.list(limit=64)
        self.registry = {k: list(reversed(v)) for k, v in reversed(list(self.registry.items()))}
        self.assertEqual(a, self.selector.list(limit=64))
        row = self.selector.describe(self.name)
        row['tool']['inputSchema']['type'] = 'mutated'
        self.assertNotEqual(row, self.selector.describe(self.name))
        self.selector.select([self.name])['tools'].clear()
        self.assertEqual(len(self.selector.selected()['tools']), 1)

    def test_authorization_and_revocation(self):
        self.registry = {'reference': self.registry['reference'][:1]}
        self.assertEqual(self.selector.list()['total'], 1)
        with self.assertRaisesRegex(ValueError, 'unknown_tool'):
            self.selector.select(['ncp_run_submit'])
        self.selector.select([self.name])
        self.registry = {'reference': []}
        self.revision = 'membership-revoked'
        with self.assertRaises(StaleCatalog):
            self.selector.selected()
        self.assertEqual(self.selector.selected()['tools'], [])
        with self.assertRaisesRegex(ValueError, 'unknown_tool'):
            self.selector.describe(self.name)

    def test_membership_generation_and_schema_change(self):
        self.selector.select([self.name])
        self.revision = 'different-principal-same-visible-schemas'
        with self.assertRaises(StaleCatalog):
            self.selector.selected()
        self.assertEqual(self.selector.selected()['tools'], [])
        self.selector.select([self.name])
        self.registry['reference'][0]['inputSchema']['title'] = 'new schema'
        with self.assertRaises(StaleCatalog):
            self.selector.selected()
        self.assertEqual(self.selector.selected()['tools'], [])

    def test_refresh_failure_fails_closed(self):
        self.selector.select([self.name])
        self.failed = True
        with self.assertRaises(PermissionError):
            self.selector.selected()
        self.failed = False
        self.assertEqual(self.selector.selected()['tools'], [])

    def test_pages_search_and_bounds(self):
        names = []
        cursor = None
        while True:
            page = self.selector.list(cursor=cursor, limit=3, max_bytes=1400)
            self.assertLessEqual(len(serialize(page)), 1400)
            names.extend(x['name'] for x in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(len(names), 111)
        self.assertEqual(names, sorted(set(names)))
        page = self.selector.search('ncp', limit=1)
        with self.assertRaises(ValueError):
            self.selector.search('other', cursor=page['next_cursor'])
        old = self.selector.list(limit=1)['next_cursor']
        self.revision = 'new'
        with self.assertRaises(StaleCatalog):
            self.selector.list(cursor=old)
        with self.assertRaises(ValueError):
            self.selector.list(cursor=old)
        for limit in (True, 0, -1, 65, 1.5):
            with self.assertRaises(ValueError):
                self.selector.list(limit=limit)
        for query in ('', ' ', '界' * 171):
            with self.assertRaises(ValueError):
                self.selector.search(query)
        for category in ('gis', 'finance', 'trade', 'unknown', ['reference']):
            with self.assertRaisesRegex(ValueError, 'unknown_category'):
                self.selector.list(category=category)
        with self.assertRaises(TypeError):
            self.selector.list(principal='someone')
        with self.assertRaises(TypeError):
            self.selector.select([self.name], tenant='someone')

    def test_unicode_budget_and_atomic_selection(self):
        self.registry['reference'][0]['description'] = '🌍界' * 1000
        row = deepcopy(self.registry['reference'][0])
        self.assertEqual(self.selector.describe(self.name)['tool'], row)
        with self.assertRaisesRegex(ValueError, 'output_budget_exceeded'):
            self.selector.select([self.name], max_bytes=512)
        self.assertEqual(self.selector.selected()['tools'], [])
        with self.assertRaises(ValueError):
            self.selector.list(max_bytes=128)
        for budget in (True, 0, 127, 262145):
            with self.assertRaises(ValueError):
                self.selector.selected(max_bytes=budget)
        page = self.selector.list(category='reference', max_bytes=5000)
        self.assertLessEqual(len(serialize(page)), 5000)
        self.assertTrue(self.selector.describe(self.name)['tool']['description'].endswith('界'))

    def test_concurrent_clients_and_no_dispatch(self):
        other = ContextSelector(self.read)
        other_name = self.registry['state'][0]['name']
        def choose(selector, name):
            for _ in range(10):
                self.assertEqual(selector.select([name])['tools'][0]['name'], name)
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(choose, self.selector, self.name)
            b = pool.submit(choose, other, other_name)
            a.result(); b.result()
        self.assertEqual(self.selector.selected()['tools'][0]['name'], self.name)
        self.assertEqual(other.selected()['tools'][0]['name'], other_name)
        self.assertGreaterEqual(self.reader_calls, 22)
        self.assertFalse(hasattr(self.selector, 'call_tool'))

    def test_existing_server_discovery_unchanged(self):
        from ciw.agent_mcp import Server
        rows = self.registry['control']
        class Host:
            def descriptions(self):
                return deepcopy(rows)
            def call(self, *args):
                raise AssertionError('selector must never dispatch')
        server = Server(Host())
        server.initialized = True
        def listing(request_id):
            return server.handle(serialize({'jsonrpc': '2.0', 'id': request_id,
                'method': 'tools/list', 'params': {}}))['result']
        before = listing(1)
        self.selector.list(); self.selector.describe(self.name)
        self.selector.select([self.name]); self.selector.deselect([self.name])
        self.assertEqual(before, listing(2))
        self.assertEqual(before['tools'], rows)

    def test_real_principal_facade_revocation_and_dispatch_authorization(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from ciw.agent_mcp import demo_config, from_profile
        from ciw.control_gateway import ControlHost
        from ciw.principal_policy import PrincipalPolicy, owner_for
        from ciw.context_catalog import for_principal_facade
        grant = {'principal': 'alice', 'enabled': True,
                 'operations': [{'name': 'ncp_capabilities_list', 'constraints': {}}],
                 'scientific_operations': ['statistics.v1'], 'resources': [],
                 'reference_scopes': [], 'handoff_recipients': []}
        value = {'schema': 'ciw.principal-policy.v1', 'revision': 'one', 'principals': [grant]}
        policy = PrincipalPolicy(lambda: value)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'NCP_RETAIN_DIR': str(root / 'retained')}):
                with from_profile(demo_config(root / 'demo')) as owner:
                    with ControlHost(owner, retained_owner=owner_for('alice')) as control:
                        facade = policy.facade('alice', control=control)
                        selector = for_principal_facade(facade)
                        # A selector operation must never dispatch, even a read tool.
                        with patch.object(facade, 'call', side_effect=AssertionError('dispatch')):
                            listing = selector.list()
                            self.assertEqual([r['name'] for r in listing['items']], ['ncp_capabilities_list'])
                            selector.select(['ncp_capabilities_list'])
                        # Policy-only changes invalidate selection even if discovery is identical.
                        grant['scientific_operations'] = []
                        with self.assertRaises(StaleCatalog):
                            selector.selected()
                        selector.select(['ncp_capabilities_list'])
                        # Host authorization independently refuses a saved selected name.
                        grant['operations'] = []
                        self.assertEqual(facade.call('ncp_capabilities_list', {})['status'], 'refused')
                        with self.assertRaises(StaleCatalog):
                            selector.selected()
                        self.assertEqual(selector.selected()['tools'], [])
                        grant['enabled'] = False
                        with self.assertRaises(ValueError):
                            selector.list()
                        self.assertEqual(owner.capabilities()['execution_budget']['used'], 0)

    def test_principal_adapter_uses_one_coherent_policy_snapshot(self):
        from ciw.context_catalog import for_principal_facade
        from ciw.principal_policy import PrincipalPolicy
        corpus = 'sha256:' + '1' * 64
        rows = deepcopy(self.registry['reference'])
        class Reference:
            corpus_ref = corpus
            evidence_ref = None
            def descriptions(self):
                return deepcopy(rows)
            def call(self, *args):
                raise AssertionError('selection must not invoke tools')
        def document(name, revision):
            return {'schema': 'ciw.principal-policy.v1', 'revision': revision, 'principals': [{
                'principal': 'alice', 'enabled': True,
                'operations': [{'name': name, 'constraints': {}}],
                'scientific_operations': [], 'resources': [],
                'reference_scopes': [corpus], 'handoff_recipients': []}]}
        a = document('ncp_knowledge_overview', 'A')
        b = document('ncp_knowledge_search', 'B')
        sequence = []
        reads = []
        def reader():
            value = sequence.pop(0) if sequence else a
            reads.append(value['revision'])
            return value
        facade = PrincipalPolicy(reader).facade('alice', reference=Reference())
        selector = for_principal_facade(facade)
        sequence[:] = [a, b, a]
        reads.clear()
        # The original three reads accepted search under B but labeled it A.
        with self.assertRaisesRegex(ValueError, 'unknown_tool'):
            selector.select(['ncp_knowledge_search'])
        self.assertEqual(reads, ['A'])
        self.assertEqual(sequence, [b, a])

    def test_published_service_schemas_include_nise_without_runtime_binding(self):
        from hashlib import sha256
        from unittest.mock import patch
        from ciw.nise_profile import NiseHost, profile_manifest
        from ciw.services import capability_map
        with patch.object(NiseHost, '__init__', side_effect=AssertionError('runtime binding')):
            manifest = profile_manifest()
            profiles = capability_map()['bounded_computation_profiles']
            service = next(p for p in profiles if p['profile_id'] == manifest['profile_id'])
            self.assertEqual(self.registry['nise'], service['tools'])
            for row in service['tools']:
                result = self.selector.describe(row['name'])
                self.assertEqual(result['tool'], row)
                self.assertEqual(result['schema_sha256'], sha256(serialize(row)).hexdigest())

    def test_actual_tenant_bindings_are_isolated_and_never_caller_selected(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from ciw.context_catalog import for_principal_facade
        from ciw.logistics_profile import LogisticsHost
        from ciw.principal_policy import PrincipalPolicy
        def no_transport(*args):
            raise AssertionError('selection reached transport')
        grant = {'principal': 'alice', 'enabled': True,
                 'operations': [{'name': 'ncp_logistics_catalog', 'constraints': {}}],
                 'scientific_operations': [], 'resources': [],
                 'reference_scopes': [], 'handoff_recipients': []}
        policy = PrincipalPolicy(lambda: {'schema': 'ciw.principal-policy.v1',
            'revision': 'tenants', 'principals': [grant]})
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'NCP_RETAIN_DIR': str(Path(directory) / 'retained')}):
                def host(tenant):
                    return LogisticsHost(transport=no_transport, principal='alice', tenant=tenant,
                        roles=['pilot:agent-read', 'pilot:compute-read'], allowed_kinds=['catalog'],
                        node='/usr/bin/false', synthetic=True)
                a, b = host('tenant-a'), host('tenant-b')
                fa, fb = policy.facade('alice', logistics=a), policy.facade('alice', logistics=b)
                sa, sb = for_principal_facade(fa), for_principal_facade(fb)
                selected_a = sa.select(['ncp_logistics_catalog'])
                selected_b = sb.selected()
                self.assertNotEqual(selected_a['catalog_id'], selected_b['catalog_id'])
                self.assertEqual(selected_b['tools'], [])
                self.assertNotEqual(a.store.owner, b.store.owner)
                with self.assertRaises(TypeError):
                    sa.select(['ncp_logistics_catalog'], tenant='tenant-b')
                # Trusted rebind still invalidates old context before output.
                fa.hosts['logistics'] = b
                with self.assertRaises(StaleCatalog):
                    sa.selected()
                self.assertEqual(sa.selected()['tools'], [])
                grant['operations'] = []
                self.assertEqual(fb.call('ncp_logistics_catalog', {})['status'], 'refused')
                with self.assertRaises(StaleCatalog):
                    sb.selected()

    def test_catalog_and_selection_limits(self):
        all_names = [r['name'] for rows in self.registry.values() for r in rows]
        with self.assertRaisesRegex(ValueError, 'invalid_selection'):
            self.selector.select(all_names[:65])
        self.registry['reference'].append(deepcopy(self.registry['reference'][0]))
        with self.assertRaisesRegex(ValueError, 'invalid_host_catalog'):
            self.selector.list()
        self.registry = source_registry()
        for row in self.registry['control']:
            row['description'] = '界' * 25000
        with self.assertRaisesRegex(ValueError, 'catalog_budget_exceeded'):
            self.selector.list()
        self.registry = source_registry()
        self.assertEqual(self.selector.selected()['tools'], [])

    def test_malformed_host_catalog_fails_closed(self):
        for value in (float('nan'), object(), ('tuple',)):
            with self.subTest(value=type(value).__name__):
                self.registry = source_registry()
                self.selector.select([self.name])
                self.registry['reference'][0]['inputSchema']['bad'] = value
                with self.assertRaises((ValueError, TypeError)):
                    self.selector.selected()
                self.registry = source_registry()
                self.assertEqual(self.selector.selected()['tools'], [])


if __name__ == '__main__':
    unittest.main()
