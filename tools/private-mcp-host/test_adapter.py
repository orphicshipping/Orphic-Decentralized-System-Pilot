"""Public HTTP boundary against the real installed MCP server."""
import json
from pathlib import Path
import sys
import unittest
import math

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapter import app


class AdapterJourney(unittest.IsolatedAsyncioTestCase):
    async def test_discover_and_resume_retained_profile(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://container') as client:
            first = await client.post('/transaction', json={'state': None, 'action': 'tools', 'payload': {}})
            self.assertEqual(first.status_code, 200, first.text)
            data = first.json()
            self.assertEqual(len(data['result']['data']), 14)
            self.assertTrue(data['state'])
            resumed = await client.post('/transaction', json={'state': data['state'], 'action': 'call',
                'payload': {'name': 'ncp_capabilities_list', 'arguments': {}}})
            self.assertEqual(resumed.status_code, 200, resumed.text)
            raw = resumed.json()['result']['data']
            self.assertFalse(raw.get('isError'))
            self.assertIn('uncertainty.propagate.v1', json.dumps(raw))
            self.assertIn('model.discrepancy.v1', json.dumps(raw))

    async def test_execute_all_seven_then_reopen_exact_retained_result(self):
        state = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://container') as client:
            async def call(name, arguments, confirmed=False):
                nonlocal state
                response = await client.post('/transaction', json={'state': state, 'action': 'call',
                    'payload': {'name': name, 'arguments_json': json.dumps(arguments), 'confirmed': confirmed}})
                self.assertEqual(response.status_code, 200, response.text)
                value = response.json()
                self.assertEqual(value['status'], 200, value['result'])
                state = value['state']
                raw = json.loads(value['result']['raw_json'])
                self.assertFalse(raw.get('isError'), raw)
                return raw['structuredContent']['data']
            template = await call('ncp_plan_template', {'baseline': 'baseline'})
            def browser_numbers(value):
                if isinstance(value, float) and math.isfinite(value) and value.is_integer():
                    return int(value)
                if isinstance(value, list): return [browser_numbers(item) for item in value]
                if isinstance(value, dict): return {key: browser_numbers(item) for key, item in value.items()}
                return value
            # A JS hop may parse/stringify the outer envelope, but never the
            # preserved raw_json string. An ordinary numeric graph is refused.
            self.assertNotEqual(json.dumps(browser_numbers(template)), json.dumps(template))
            plan = await call('ncp_plan_create', {'source': 'source', 'baseline': 'baseline', 'graph': template['graph']}, True)
            digest = {'plan_id': plan['plan_id'], 'expected_digest': plan['digest']}
            await call('ncp_plan_admit', digest, True)
            done = await call('ncp_run_submit', {**digest, 'attempt': 'host-acceptance'}, True)
            self.assertEqual(done['state'], 'completed')
            self.assertEqual(len(done['result']['execution_ids']), 7)
            self.assertEqual(await call('ncp_run_get', {'run_id': done['run_id']}), done)
            self.assertEqual(await call('ncp_run_submit', {**digest, 'attempt': 'host-acceptance'}, True), done)
            response = await client.post('/transaction', json={'state': state, 'action': 'runs', 'payload': {}})
            self.assertEqual(response.json()['result']['data']['items'][0]['run_id'], done['run_id'])


if __name__ == '__main__':
    unittest.main()
