"""Current principal policy across SDK and real external MCP intake calls."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import invoke
from ciw.offline_intake import encode
from test_offline_intake import BASE, fixture, host, launch_text


def policy_value():
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'intake-test-1', 'principals': [
        {'principal': 'owner-a', 'enabled': True,
         'operations': [
             {'name': 'ncp_intake_describe', 'constraints': {}},
             {'name': 'ncp_intake_record', 'constraints': {'record_id': ['opc-1']}},
             {'name': 'ncp_intake_verify', 'constraints': {'record_id': ['opc-1']}}],
         'scientific_operations': [], 'resources': [], 'reference_scopes': [], 'handoff_recipients': []}]}


class IntakePolicyTests(unittest.TestCase):
    def test_sdk_selected_grants_and_revocation_precede_host_dispatch(self):
        value, bound = policy_value(), host()
        policy = PrincipalPolicy(lambda: value)
        facade = policy.facade('owner-a', intake=bound)
        expected = invoke('ncp_intake_record', {'record_id': 'opc-1'}, host=bound)
        with patch.object(bound, 'call', wraps=bound.call) as calls:
            receipt = invoke('ncp_intake_record', {'record_id': 'opc-1'}, host=facade)
            self.assertEqual(receipt, expected)
            self.assertEqual(invoke('ncp_intake_verify', {'record_id': 'opc-1',
                'receipt_json': encode(receipt).decode()}, host=facade)['outcome'], 'matched_host_capture')
            accepted_calls = calls.call_count
            for name, args in (('ncp_intake_record', {'record_id': 'mqtt-1'}),
                               ('ncp_intake_records', {}), ('ncp_intake_subscribe', {}),
                               ('ncp_intake_describe', {'principal': 'owner-b'}),
                               ('ncp_intake_describe', {'owner': 'owner-a'})):
                with self.assertRaises(PrincipalRefusal):
                    invoke(name, args, host=facade)
                self.assertEqual(calls.call_count, accepted_calls)
            value['principals'][0]['operations'] = []
            self.assertEqual(facade.descriptions(), [])
            with self.assertRaises(PrincipalRefusal):
                invoke('ncp_intake_record', {'record_id': 'opc-1'}, host=facade)
            value['principals'][0]['enabled'] = False
            self.assertEqual(facade.call('ncp_intake_describe', {})['status'], 'refused')
            self.assertEqual(calls.call_count, accepted_calls)

    def test_principal_and_resource_scope_cannot_reopen_other_capture(self):
        value = policy_value()
        value['principals'].append({**deepcopy(value['principals'][0]), 'principal': 'owner-b'})
        policy = PrincipalPolicy(lambda: value)
        with self.assertRaises(PrincipalRefusal):
            policy.facade('owner-b', intake=host())
        with self.assertRaises(PrincipalRefusal):
            policy.facade('unknown', intake=host())
        facade = policy.facade('owner-a', intake=host())
        self.assertEqual(facade.list_resources(), [])
        self.assertEqual(facade.resource_templates, [])
        for uri in ('ncp://intake/opc-1', 'ncp://intakeevil/opc-1', 'ncp://retained/opc-1'):
            with self.assertRaises(PrincipalRefusal):
                facade.read_resource(uri)

    def test_external_mcp_current_constraints_and_disabled_grant_stop_dispatch(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        raw = encode(fixture())
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            policy_path, count_path = directory/'policy.json', directory/'calls.txt'
            value = policy_value()
            policy_path.write_text(json.dumps(value))
            # The count is test instrumentation: rejected calls must not reach
            # the host, which otherwise has no file or process side effects.
            extra = f'''from pathlib import Path
from ciw.principal_policy import PrincipalPolicy
from ciw.knowledge_mcp import serve_stdio
count_path=Path({str(count_path)!r})
count_path.write_text('0')
original=host.call
def counted(name,args):
 count_path.write_text(str(int(count_path.read_text())+1))
 return original(name,args)
host.call=counted
host=PrincipalPolicy.from_file({str(policy_path)!r}).facade('owner-a',intake=host)
'''
            launcher = directory/'host.py'
            launcher.write_text(launch_text(raw).replace('asyncio.run(serve_stdio(host))',
                                 extra+'asyncio.run(serve_stdio(host))'))
            async def run():
                params = StdioServerParameters(command=sys.executable, args=['-B', str(launcher)], env=dict(os.environ))
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as client:
                        await client.initialize()
                        self.assertEqual({row.name for row in (await client.list_tools()).tools},
                                         {row['name'] for row in value['principals'][0]['operations']})
                        self.assertEqual((await client.list_resources()).resources, [])
                        self.assertEqual((await client.list_resource_templates()).resource_templates, [])
                        result = await client.call_tool('ncp_intake_record', {'record_id': 'opc-1'})
                        self.assertFalse(result.is_error)
                        receipt = result.structured_content['data']
                        args = {'record_id': 'opc-1', 'receipt_json': encode(receipt).decode()}
                        self.assertFalse((await client.call_tool('ncp_intake_verify', args)).is_error)
                        calls = count_path.read_text()
                        self.assertEqual(calls, '2')
                        for name, denied in (('ncp_intake_record', {'record_id': 'mqtt-1'}),
                                             ('ncp_intake_records', {}), ('ncp_intake_publish', {}),
                                             ('ncp_intake_describe', {'endpoint': 'mqtt://example.invalid'})):
                            self.assertTrue((await client.call_tool(name, denied)).is_error)
                            self.assertEqual(count_path.read_text(), calls)
                        value['principals'][0]['operations'] = [value['principals'][0]['operations'][0]]
                        policy_path.write_text(json.dumps(value))
                        self.assertEqual([row.name for row in (await client.list_tools()).tools], ['ncp_intake_describe'])
                        self.assertTrue((await client.call_tool('ncp_intake_verify', args)).is_error)
                        self.assertEqual(count_path.read_text(), calls)
                        value['principals'][0]['enabled'] = False
                        policy_path.write_text(json.dumps(value))
                        self.assertTrue((await client.call_tool('ncp_intake_describe', {})).is_error)
                        self.assertEqual(count_path.read_text(), calls)
            asyncio.run(run())


if __name__ == '__main__':
    unittest.main(verbosity=2)
