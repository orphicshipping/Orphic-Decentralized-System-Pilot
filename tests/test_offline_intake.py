"""Synthetic offline exports; real stdio transport, no live protocol qualification."""
import asyncio
import base64
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import ciw
from ciw.offline_intake import (CAPTURE_LIMITS, CAPTURE_SCHEMA, CLAIMS, IntakeRefusal,
    MAX_CAPTURE, MAX_RESPONSE, OfflineIntakeHost, PROTOCOLS, TOOLS, byte_digest,
    encode, invoke_intake, profile_manifest)

BASE = Path(ciw.__file__).resolve().parents[1]
TIME = '2026-11-03T01:02:03.123456789Z'
SOURCE = {'source_id': 'synthetic-export', 'revision': 'fixture-v1', 'digest': 'sha256:'+'a'*64}


def scalar_payload():
    return {'schema': 'odsp.scalar-observation.v1', 'value': 21.5, 'unit': 'degC', 'observed_at': TIME}


def fixture():
    records = [
        {'record_id': 'ros/../inert', 'protocol': PROTOCOLS[0], 'received_at': TIME,
         'observation': {'stamp': {'sec': 2, 'nanosec': 250000000},
           'clock': {'domain': 'simulation', 'clock_id': 'sim-1'}, 'frame_id': 'base_link',
           'name': ['slider_to_cart', 'revolute_axis'], 'joint_type': ['prismatic', 'revolute'],
           'position': [0.25, 1.5], 'velocity': [], 'effort': [1.0, 2.0]}},
        {'record_id': 'opc-1', 'protocol': PROTOCOLS[1], 'received_at': TIME,
         'observation': {'node_id': 'ns=2;s=Temperature', 'datatype': 'Double', 'value': 21.5,
           'unit': 'degC', 'status_code': 0, 'source_timestamp': TIME, 'server_timestamp': TIME}},
        {'record_id': 'mqtt-1', 'protocol': PROTOCOLS[2], 'received_at': TIME,
         'observation': {'message_type': 'observation', 'topic': '/observations/room temperature/',
           'qos': 1, 'retain': True, 'duplicate': False,
           'payload_b64': base64.b64encode(encode(scalar_payload())).decode('ascii')}}]
    return {'schema': CAPTURE_SCHEMA, 'principal': 'owner-a', 'source': deepcopy(SOURCE),
            'origin': 'synthetic', 'captured_at': TIME, 'records': records}


def host(data=None, **overrides):
    raw = encode(fixture() if data is None else data, limits=CAPTURE_LIMITS)
    args = {'capture_bytes': raw, 'expected_digest': byte_digest(raw), 'source_identity': deepcopy(SOURCE),
            'principal': 'owner-a', 'origin': 'synthetic'}
    args.update(overrides)
    return OfflineIntakeHost(**args)


def launch_text(raw):
    return f'''import asyncio,sys
sys.path.insert(0,{str(BASE)!r})
from ciw.offline_intake import OfflineIntakeHost,serve_stdio
host=OfflineIntakeHost(capture_bytes={raw!r},expected_digest={byte_digest(raw)!r},source_identity={SOURCE!r},principal='owner-a',origin='synthetic')
asyncio.run(serve_stdio(host))
'''


class OfflineIntakeTests(unittest.TestCase):
    def test_describe_and_no_resources(self):
        h = host()
        result = invoke_intake(h, 'ncp_intake_describe', {})
        self.assertEqual(result['record_count'], 3)
        self.assertEqual(result['provenance']['source_binding_verification'], 'host_declared_unverified')
        self.assertEqual(len(h.descriptions()), 4)
        self.assertEqual(h.resource_descriptions(), [])
        self.assertEqual(h.resource_templates(), [])
        with self.assertRaisesRegex(IntakeRefusal, 'resource_unavailable'):
            h.read_resource('ncp://intake/ros')
        for row in h.descriptions():
            self.assertEqual(row['annotations'], {'readOnlyHint': True, 'destructiveHint': False,
                'idempotentHint': True, 'openWorldHint': False})
        self.assertEqual(profile_manifest()['tools'], h.descriptions())

    def test_actual_ros_interpretation_preserves_clock_and_empty_fields(self):
        result = invoke_intake(host(), 'ncp_intake_record', {'record_id': 'ros/../inert'})
        self.assertEqual(result['record']['observation']['position'], [0.25, 1.5])
        joints = result['interpreted']['joints']
        self.assertEqual(joints[0]['position'], {'value': 0.25, 'unit': 'm'})
        self.assertEqual(joints[1]['effort'], {'value': 2.0, 'unit': 'N.m'})
        self.assertIsNone(joints[0]['velocity']['value'])
        self.assertEqual(result['interpreted']['stamp'], {'sec': 2, 'nanosec': 250000000})
        self.assertEqual(result['interpreted']['clock_synchronization'], 'not_established')
        self.assertEqual(result['claims'], CLAIMS)

    def test_actual_opcua_status_is_not_measurement_validation(self):
        data = fixture()
        row = data['records'][1]['observation']
        for status, severity in ((0, 'good'), (0x40000000, 'uncertain'), (0x80000000, 'bad'), (0xc0000000, 'reserved')):
            with self.subTest(status=status):
                row['status_code'] = status
                result = invoke_intake(host(data), 'ncp_intake_record', {'record_id': 'opc-1'})
                self.assertEqual(result['record']['observation']['value'], 21.5)
                self.assertEqual(result['interpreted']['reported_quality'], severity)
                self.assertFalse(result['interpreted']['reported_quality_verified'])
                if severity in ('bad', 'reserved'):
                    self.assertIsNone(result['interpreted']['value'])
        row.update(status_code=0x80000000, value=None, source_timestamp=None)
        self.assertEqual(invoke_intake(host(data), 'ncp_intake_record', {'record_id': 'opc-1'})['interpreted']['clock_status'],
                         'source_time_missing')
        row.update(status_code=0x40000000, value=None)
        result = invoke_intake(host(data), 'ncp_intake_record', {'record_id': 'opc-1'})
        self.assertFalse(result['interpreted']['reported_value_available'])

    def test_opcua_supported_scalar_types(self):
        for datatype, value in (('Boolean', False), ('Int32', -(2**31)), ('UInt32', 2**32-1),
                                ('Double', -1.25), ('String', 'inert source text')):
            data = fixture()
            data['records'][1]['observation'].update(datatype=datatype, value=value)
            with self.subTest(datatype=datatype):
                result = invoke_intake(host(data), 'ncp_intake_record', {'record_id': 'opc-1'})
                self.assertEqual(result['interpreted']['value'], value)

    def test_mqtt_payload_exact_bytes_and_delivery_claims(self):
        data = fixture()
        payload = b'{"schema":"odsp.scalar-observation.v1","value":21.5,"unit":"degC","observed_at":"2026-11-03T01:02:03Z"}'
        data['records'][2]['observation']['payload_b64'] = base64.b64encode(payload).decode()
        result = invoke_intake(host(data), 'ncp_intake_record', {'record_id': 'mqtt-1'})
        self.assertEqual(result['interpreted']['payload_digest'], 'sha256:'+sha256(payload).hexdigest())
        self.assertEqual(result['interpreted']['payload']['value'], 21.5)
        self.assertFalse(result['interpreted']['delivery_claims_verified'])

    def test_pages_are_complete_repeatable_and_defensive_copies(self):
        h = host()
        first = invoke_intake(h, 'ncp_intake_records', {'limit': 2})
        last = invoke_intake(h, 'ncp_intake_records', {'offset': first['next_offset'], 'limit': 2})
        self.assertEqual([r['record_id'] for r in first['items']+last['items']], ['ros/../inert', 'opc-1', 'mqtt-1'])
        self.assertIsNone(last['next_offset'])
        self.assertEqual(invoke_intake(h, 'ncp_intake_records', {'protocol': PROTOCOLS[1]})['total'], 1)
        result = invoke_intake(h, 'ncp_intake_record', {'record_id': 'ros/../inert'})
        result['record']['observation']['position'][0] = 9
        self.assertEqual(invoke_intake(h, 'ncp_intake_record', {'record_id': 'ros/../inert'})['record']['observation']['position'][0], 0.25)
        self.assertEqual(invoke_intake(h, 'ncp_intake_record', {'record_id': 'opc-1'}),
                         invoke_intake(host(), 'ncp_intake_record', {'record_id': 'opc-1'}))

    def test_full_receipt_verification_does_not_trust_digest_ids(self):
        h = host()
        receipt = invoke_intake(h, 'ncp_intake_record', {'record_id': 'opc-1'})
        valid = {'record_id': 'opc-1', 'receipt_json': encode(receipt).decode()}
        self.assertEqual(invoke_intake(h, 'ncp_intake_verify', valid)['outcome'], 'matched_host_capture')
        mutations = [lambda r: r['record']['observation'].__setitem__('value', 999),
                     lambda r: r['interpreted'].__setitem__('value', 999),
                     lambda r: r['provenance'].__setitem__('principal', 'intruder'),
                     lambda r: r['provenance']['source_identity'].__setitem__('digest', 'sha256:'+'b'*64),
                     lambda r: r['claims'].__setitem__('authorizes_execution', True),
                     lambda r: r.__setitem__('verified', True)]
        for mutate in mutations:
            forged = deepcopy(receipt)
            mutate(forged)
            for replace_digest in (False, True):
                if replace_digest:
                    forged['receipt_digest'] = byte_digest(encode({k:v for k,v in forged.items() if k != 'receipt_digest'}))
                result = h.call('ncp_intake_verify', {'record_id': 'opc-1', 'receipt_json': encode(forged).decode()})
                self.assertEqual(result['reason'], 'intake_receipt_mismatch')
        self.assertEqual(h.call('ncp_intake_verify', {**valid, 'record_id': 'mqtt-1'})['reason'], 'intake_receipt_mismatch')

    def test_duplicate_receipt_keys_are_rejected_before_information_loss(self):
        h = host()
        receipt = invoke_intake(h, 'ncp_intake_record', {'record_id': 'opc-1'})
        raw = encode(receipt).decode().replace('{', '{"schema":"wrong",', 1)
        self.assertEqual(h.call('ncp_intake_verify', {'record_id': 'opc-1', 'receipt_json': raw})['reason'], 'duplicate_json_key')

    def test_replay_is_bound_to_full_capture_and_every_record_is_verifiable(self):
        original = host()
        receipt = invoke_intake(original, 'ncp_intake_record', {'record_id': 'opc-1'})
        changed = fixture()
        changed['records'][2]['observation']['qos'] = 2
        # Even unchanged record content is bound to the complete reviewed capture.
        self.assertEqual(host(changed).call('ncp_intake_verify', {
            'record_id': 'opc-1', 'receipt_json': encode(receipt).decode()})['reason'], 'intake_receipt_mismatch')
        data = fixture()
        row = data['records'][0]['observation']
        row.update(name=['j'+str(i) for i in range(64)], joint_type=['prismatic']*64,
                   position=[0.25]*64, velocity=[], effort=[])
        bound = host(data)
        maximum = invoke_intake(bound, 'ncp_intake_record', {'record_id': 'ros/../inert'})
        self.assertEqual(invoke_intake(bound, 'ncp_intake_verify', {'record_id': 'ros/../inert',
            'receipt_json': encode(maximum).decode()})['outcome'], 'matched_host_capture')
        row.update(name=['joint-'+str(i)+'x'*120 for i in range(64)],
                   frame_id='\u2603'*128, position=[1.234567890123456e149]*64,
                   velocity=[1.234567890123456e149]*64, effort=[1.234567890123456e149]*64)
        # Valid shape maxima cannot admit a receipt too large to verify in full.
        with self.assertRaisesRegex(IntakeRefusal, 'intake_record_budget|json_byte_limit'):
            host(data)

    def test_host_binding_pin_source_origin_and_principal_are_exact(self):
        for kwargs in ({'expected_digest': 'sha256:'+'0'*64}, {'principal': 'owner-b'},
                       {'source_identity': {**SOURCE, 'revision': 'other'}}, {'origin': 'operator_capture'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(IntakeRefusal):
                host(**kwargs)
        data = fixture()
        data['origin'] = 'operator_capture'
        result = invoke_intake(host(data, origin='operator_capture'), 'ncp_intake_describe', {})
        self.assertEqual(result['provenance']['source_binding_verification'], 'host_declared_unverified')
        self.assertEqual(result['provenance']['authentication'], 'host_selected_local_only')

    def test_no_command_network_rebinding_or_invented_resource_surface(self):
        h = host()
        for name in ('ncp_intake_publish', 'ncp_intake_subscribe', 'ncp_intake_write', 'ncp_intake_handoff', 'runMcpTool'):
            self.assertEqual(h.call(name, {})['reason'], 'unknown_intake_operation')
        for args in ({'principal': 'other'}, {'endpoint': 'mqtt://host'}, {'file': '/etc/passwd'}, {'token': 'x'}):
            self.assertEqual(h.call('ncp_intake_describe', args)['status'], 'refused')
        with self.assertRaisesRegex(IntakeRefusal, 'intake_host_required'):
            invoke_intake(None, 'ncp_intake_describe', {})

    def test_empty_capture_and_empty_joint_fields_are_truthful(self):
        data = fixture()
        data['records'] = []
        self.assertEqual(invoke_intake(host(data), 'ncp_intake_records', {})['items'], [])
        data = fixture()
        for key in ('position', 'velocity', 'effort'):
            data['records'][0]['observation'][key] = []
        self.assertTrue(invoke_intake(host(data), 'ncp_intake_record', {'record_id': 'ros/../inert'})['interpreted']['empty_observation'])

    def test_malformed_raw_capture_and_numeric_boundaries(self):
        raw = encode(fixture())
        cases = [b'[]', b'{"schema":1,"schema":2}', b'\xff', b'{"a":"\\ud800"}',
                 b'['*100+b'0'+b']'*100, b'{"a":NaN}', b'{"a":1e309}', b'{"a":1e-999}',
                 b'{"a":'+b'9'*5000+b'}', b'x'*(MAX_CAPTURE+1)]
        for candidate in cases:
            with self.subTest(size=len(candidate)), self.assertRaises(IntakeRefusal):
                host(capture_bytes=candidate, expected_digest=byte_digest(candidate))
        self.assertGreater(len(raw), 0)

    def test_schema_extras_duplicate_ids_and_nonfinite_refuse(self):
        for change in (lambda d: d.update(command='move'),
                       lambda d: d['records'].append(deepcopy(d['records'][0])),
                       lambda d: d['records'][0].update(authorizes_execution=True),
                       lambda d: d['records'][0]['observation'].update(command='home')):
            data = fixture()
            change(data)
            with self.assertRaises(IntakeRefusal):
                host(data)
        for value in (float('nan'), float('inf'), True):
            data = fixture()
            data['records'][0]['observation']['position'][0] = value
            raw = json.dumps(data).encode()
            with self.assertRaises(IntakeRefusal):
                host(capture_bytes=raw, expected_digest=byte_digest(raw))

    def test_invalid_ros_lengths_clocks_time_and_joint_names(self):
        mutations = [lambda x: x['stamp'].update(nanosec=1000000000), lambda x: x['stamp'].update(sec=True),
                     lambda x: x['clock'].update(domain='UTC'), lambda x: x.update(position=[1]),
                     lambda x: x.update(name=['same','same']), lambda x: x.update(joint_type=['unknown','prismatic'])]
        for mutate in mutations:
            data = fixture()
            mutate(data['records'][0]['observation'])
            with self.assertRaises(IntakeRefusal):
                host(data)
        for time in ('2026-02-30T01:02:03Z', '2026-11-03T01:02:60Z', '2026-11-03T01:02:03+00:00', TIME+'x'):
            data = fixture()
            data['captured_at'] = time
            with self.assertRaises(IntakeRefusal):
                host(data)

    def test_invalid_opcua_scalar_types_and_quality(self):
        for change in ({'datatype':'Int32','value':True}, {'datatype':'UInt32','value':-1},
                       {'datatype':'Boolean','value':1}, {'datatype':'Double','value':'1'},
                       {'status_code':True}, {'status_code':2**32}, {'value':None},
                       {'datatype':'Int64','value':1}, {'source_timestamp':'not-time'}):
            data = fixture()
            data['records'][1]['observation'].update(change)
            with self.subTest(change=change), self.assertRaises(IntakeRefusal):
                host(data)

    def test_mqtt_commands_raw_duplicates_topic_and_payload_limits(self):
        mutations = [lambda x: x.update(message_type='command'), lambda x: x.update(topic='a/#'),
                     lambda x: x.update(topic='a/\0/b'), lambda x: x.update(qos=True),
                     lambda x: x.update(retain=1), lambda x: x.update(payload_b64='?'),
                     lambda x: x.update(payload_b64=base64.b64encode(b'x'*4097).decode()),
                     lambda x: x.update(payload_b64=base64.b64encode(b'{"value":1,"value":2}').decode()),
                     lambda x: x.update(payload_b64=base64.b64encode(encode({'schema':'command','value':1,'unit':'','observed_at':TIME})).decode())]
        for change in mutations:
            data = fixture()
            change(data['records'][2]['observation'])
            with self.assertRaises(IntakeRefusal):
                host(data)

    def test_argument_and_complete_mcp_envelope_limits(self):
        h = host()
        for args in ({'offset':True}, {'limit':True}, {'limit':33}, {'offset':4}, {'protocol':'rosbag'}, {'protocol':None}):
            self.assertEqual(h.call('ncp_intake_records', args)['status'], 'refused')
        self.assertEqual(h.call('ncp_intake_verify', {'record_id':'opc-1','receipt_json':'x'*65536})['status'], 'refused')
        for name, args in (('ncp_intake_describe', {}), ('ncp_intake_records', {}),
                           ('ncp_intake_record', {'record_id':'ros/../inert'})):
            response = h.call(name, args)
            self.assertLessEqual(len(encode(h.mcp_result(name, response))), MAX_RESPONSE)
        value = {'status':'ok','data':{'text':'\u2603'*30000}}
        response = h.mcp_result('ncp_intake_record', value)
        self.assertTrue(response['isError'])
        self.assertEqual(response['structuredContent']['reason'], 'intake_response_budget')

    def test_bounded_deterministic_mutation_fuzz(self):
        # Exercise independent scalar/type/closed-field assertions through host construction.
        for index in range(64):
            data = fixture()
            row = data['records'][index % 3]
            row['unexpected_'+str(index)] = [None, True, index, 'ignored'][index % 4]
            with self.assertRaises(IntakeRefusal):
                host(data)

    def test_real_external_mcp_discovery_read_verify_denial_and_reconnect(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        raw = encode(fixture())
        with tempfile.TemporaryDirectory() as root:
            launcher = Path(root)/'host.py'
            launcher.write_text(launch_text(raw))
            async def run():
                for _ in range(2):
                    params = StdioServerParameters(command=sys.executable, args=['-B', str(launcher)], env=dict(os.environ))
                    async with stdio_client(params) as (reader, writer):
                        async with ClientSession(reader, writer) as client:
                            await client.initialize()
                            self.assertEqual({t.name for t in (await client.list_tools()).tools}, set(TOOLS))
                            self.assertEqual((await client.list_resources()).resources, [])
                            self.assertEqual((await client.list_resource_templates()).resource_templates, [])
                            for record_id in ('ros/../inert', 'opc-1', 'mqtt-1'):
                                result = await client.call_tool('ncp_intake_record', {'record_id':record_id})
                                receipt = result.structured_content['data']
                                self.assertEqual(receipt, invoke_intake(host(), 'ncp_intake_record', {'record_id':record_id}))
                                verified = await client.call_tool('ncp_intake_verify', {'record_id':record_id,'receipt_json':encode(receipt).decode()})
                                self.assertFalse(verified.is_error)
                            refused = await client.call_tool('ncp_intake_publish', {})
                            self.assertTrue(refused.is_error)
                            refused = await client.call_tool('ncp_intake_describe', {'endpoint':'mqtt://host'})
                            self.assertTrue(refused.is_error)
            asyncio.run(run())

    def test_raw_mcp_duplicate_frame_closes_before_sdk_decoding(self):
        raw = encode(fixture())
        with tempfile.TemporaryDirectory() as root:
            launcher = Path(root)/'host.py'
            launcher.write_text(launch_text(raw))
            result = subprocess.run([sys.executable, '-B', str(launcher)],
                input=b'{"jsonrpc":"2.0","id":1,"method":"initialize","method":"tools/list","params":{}}\n',
                capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'"tools":', result.stdout)
            self.assertIn(b'invalid_mcp_frame', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
