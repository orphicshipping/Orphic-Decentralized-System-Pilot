"""Real SDK/ControlHost admission and output paths; controlled operation results."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from ciw import services
from ciw.control_gateway import ControlHost
from ciw.agent_api import MAX_RESPONSE, encode
from ciw.retention import RetainedFailure

class ResponseParity(unittest.TestCase):
    def setUp(self):
        self.host = object.__new__(ControlHost)
        self.host._closed = False
        self.host._store = SimpleNamespace(check_current=lambda: None)

    def both(self, value):
        with patch.dict(services.OPERATIONS, {'ncp_plug_in': lambda: value}), patch.object(services, 'plug_in', return_value=value):
            mcp = self.host.call('ncp_plug_in', {})
            try:
                oversized = len(encode(value)) > MAX_RESPONSE
            except ValueError:
                oversized = True
            if oversized:
                with self.assertRaises(ValueError):
                    services.invoke('ncp_plug_in')
                self.assertEqual(mcp['status'], 'refused')
            else:
                self.assertIs(services.invoke('ncp_plug_in'), value)
                self.assertEqual(mcp['status'], 'ok')
                self.assertIs(mcp['data'], value)

    def test_small_return_shapes(self):
        self.both({'small': True})

    def test_exact_budget_and_one_byte_over(self):
        value = {'value': ['x' * 60000 for _ in range(4)] + ['']}
        remaining = MAX_RESPONSE - len(encode(value))
        value['value'][-1] = 'x' * remaining
        self.assertEqual(len(encode(value)), MAX_RESPONSE)
        self.both(value)
        value['value'][-1] += 'x'
        self.both(value)

    def test_encoded_not_character_budget(self):
        self.both({'value': '\u00e9' * 50000})

    def test_reproduced_300000_character_result(self):
        self.both({'controlled_fixture': 'x' * 300000})

    def test_non_json_result_refuses_both(self):
        with patch.dict(services.OPERATIONS, {'ncp_plug_in': lambda: {'x': float('nan')}}), patch.object(services, 'plug_in', return_value={'x': float('nan')}):
            with self.assertRaises(ValueError): services.invoke('ncp_plug_in')
            self.assertEqual(self.host.call('ncp_plug_in', {})['status'], 'refused')

    def test_retained_failure_locator_survives(self):
        failure = RetainedFailure('failed', 'retained-test-artifact')
        def fail(): raise failure
        with patch.dict(services.OPERATIONS, {'ncp_plug_in': fail}), patch.object(services, 'plug_in', side_effect=failure):
            with self.assertRaises(RetainedFailure) as caught: services.invoke('ncp_plug_in')
            self.assertIs(caught.exception, failure)
            reply=self.host.call('ncp_plug_in', {})
            self.assertEqual(reply['error']['artifact_id'], failure.artifact_id)
            self.assertEqual(reply['data']['artifact_id'], failure.artifact_id)

    def test_store_route_is_bounded(self):
        with patch.dict(services.OPERATIONS, {'ncp_retained_read': lambda **kwargs: {'value': ['x'*60000]*5}}):
            with self.assertRaisesRegex(ValueError, 'Response exceeds budget'):
                services.invoke('ncp_retained_read', {'artifact_id': 'retained-test-artifact'})

    def test_session_failure_locator_survives(self):
        name = next(iter(services.SESSION))
        host=SimpleNamespace(call=lambda *args: {'status':'refused', 'error':{'artifact_id':'retained-test-artifact'}})
        with patch.object(services, 'admit'):
            with self.assertRaises(RetainedFailure) as caught: services.invoke(name, {}, host=host)
        self.assertEqual(caught.exception.artifact_id, 'retained-test-artifact')
