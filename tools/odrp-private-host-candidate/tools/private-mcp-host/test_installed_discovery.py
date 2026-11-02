"""Exercise the installed wheel from an isolated process outside the checkout."""
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest


class InstalledDiscovery(unittest.TestCase):
    def test_index_and_descriptions_use_packaged_ledger_without_grants(self):
        program = textwrap.dedent("""
            import json
            from pathlib import Path
            import sys
            import ciw
            from ciw.agent_mcp import demo_config, from_profile
            from ciw.control_gateway import ControlHost
            from ciw.services import invoke

            # -I ignores PYTHONPATH and the working directory. Also reject an
            # editable install so a source checkout cannot hide package gaps.
            module = Path(ciw.__file__).resolve()
            assert module.is_relative_to(Path(sys.prefix).resolve()), module
            assert (module.parent / 'capability-ledger.json').is_file()
            with from_profile(demo_config(Path.cwd() / 'profile')) as owner, ControlHost(owner) as host:
                used = owner.capabilities()['execution_budget']['used']
                rows, offset = [], 0
                while True:
                    response = host.call('ncp_index_list', {'offset': offset, 'limit': 32})
                    assert response['status'] == 'ok', response
                    page = response['data']
                    assert page['authorizes_execution'] is False
                    rows.extend(page['items'])
                    if page['next_offset'] is None:
                        break
                    assert page['next_offset'] > offset
                    offset = page['next_offset']
                assert len(rows) == page['total']
                assert page['ledger_total'] > 0
                assert all(row['authorizes_execution'] is False for row in rows)
                assert any(row['surface'] == 'ledger' and row['capability_id'] == 'public_core' for row in rows)
                for capability, route in [('leakage.assess-balance.v1', 'leakage'),
                                          ('proved_heat', 'ncp_ledger_read')]:
                    response = host.call('ncp_capability_describe', {'capability_id': capability})
                    assert response['status'] == 'ok', response
                    item = response['data']
                    assert item['host_bound'] is False
                    assert item['authorizes_execution'] is False
                    assert route in (item['mcp_profile'], item['mcp_tool'])
                    assert invoke('ncp_capability_describe', {'capability_id': capability}, host=host) == item
                assert host.call('ncp_capability_describe', {'capability_id': 'not-a-capability'})['status'] == 'refused'
                assert owner.capabilities()['execution_budget']['used'] == used
                print(json.dumps({'index_total': page['index_total'], 'ledger_total': page['ledger_total'],
                                  'execution_budget_used': used, 'module': str(module)}))
        """)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-c', program],
                                    cwd=directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertGreater(summary['index_total'], 0)
        self.assertGreater(summary['ledger_total'], 0)
        self.assertEqual(summary['execution_budget_used'], 0)


if __name__ == '__main__':
    unittest.main()
