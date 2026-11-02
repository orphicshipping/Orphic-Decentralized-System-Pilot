import asyncio
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import secrets
import time
import socket
from dataclasses import replace
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console import ConsoleConfig, MCPBridge, ConsoleError, security_error, create_app, launch_arguments


class SecurityTests(unittest.TestCase):
    def test_native_launch_is_explicit_and_incompatible_flags_refuse(self):
        trusted = Path(tempfile.gettempdir()).resolve()
        base = ConsoleConfig(Path(sys.executable), trusted / 'profile.json')
        valid = replace(base, instrument='bim-clearance', provider_checkout=trusted / 'cse',
                        provider_revision='a' * 40, provider_python=Path(sys.executable), resume=True)
        args = launch_arguments(valid)
        self.assertEqual(args[:4], ['-I', '-B', '-m', 'ciw.net'])
        self.assertIn('--provider-checkout', args)
        self.assertIn('--provider-revision', args)
        self.assertIn('--resume', args)
        for invalid in [replace(base, provider_checkout=trusted / 'cse'),
                        replace(valid, provider_revision='main'), replace(valid, provider_checkout=Path('relative')),
                        replace(valid, surface='operational'), replace(valid, instrument='leakage')]:
            with self.assertRaises(ConsoleError):
                launch_arguments(invalid)

    def test_rejects_foreign_origin_even_with_token(self):
        self.assertEqual(security_error('127.0.0.1:8765', 'https://evil.example', 'Bearer secret', 'secret', 8765), 403)

    def test_requires_token_and_loopback_host(self):
        self.assertEqual(security_error('127.0.0.1:8765', None, '', 'secret', 8765), 401)
        self.assertEqual(security_error('evil.example:8765', None, 'Bearer secret', 'secret', 8765), 403)
        self.assertIsNone(security_error('localhost:8765', 'http://localhost:8765', 'Bearer secret', 'secret', 8765))


class RealMCPTests(unittest.TestCase):
    def test_real_discovery_validation_and_retained_plan(self):
        from ciw.agent_mcp import demo_config

        async def journey(root):
            profile = demo_config(root / 'profile')
            bridge = MCPBridge(ConsoleConfig(Path(sys.executable), profile, state_dir=root / 'console'))
            await bridge.open()
            try:
                status = await bridge.request('status')
                self.assertEqual(status['connection'], 'connected')
                self.assertEqual(status['protocol'], '2026-07-28')
                tools = await bridge.request('tools')
                self.assertIn('ncp_plan_create', [tool['name'] for tool in tools])
                catalog = await bridge.request('call', {'name': 'ncp_integrations_list', 'arguments': {'section': 'imports', 'limit': 32}})
                self.assertEqual(catalog['structuredContent']['data']['total'], 21)
                with self.assertRaises(ConsoleError) as invalid:
                    await bridge.request('call', {'name': 'ncp_run_get', 'arguments': {}})
                self.assertEqual(invalid.exception.code, 'invalid_arguments')
                with self.assertRaises(ConsoleError) as denied:
                    await bridge.request('call', {'name': 'ncp_plan_create', 'arguments': {}})
                self.assertEqual(denied.exception.code, 'confirmation_required')
                with self.assertRaises(ConsoleError):
                    await bridge.request('call', {'name': 'arbitrary_shell', 'arguments': {}})
                template = await bridge.request('call', {'name': 'ncp_plan_template', 'arguments': {'baseline': 'baseline'}})
                graph = template['structuredContent']['data']['graph']
                plan = await bridge.request('call', {'name': 'ncp_plan_create', 'arguments': {'source': 'source', 'baseline': 'baseline', 'graph': graph}, 'confirmed': True})
                data = plan['structuredContent']['data']
                self.assertFalse(data['admitted'])
                resource = await bridge.request('resource', {'uri': data['uri']})
                self.assertEqual(json.loads(resource['contents'][0]['text'])['plan_id'], data['plan_id'])
                digest = {'plan_id': data['plan_id'], 'expected_digest': data['digest']}
                await bridge.request('call', {'name': 'ncp_plan_admit', 'arguments': digest, 'confirmed': True})
                submission = {'name': 'ncp_run_submit', 'arguments': {**digest, 'attempt': 'console-test-001'}, 'confirmed': True}
                submitted = (await bridge.request('call', submission))['structuredContent']['data']
                async with asyncio.timeout(30):
                    while True:
                        run = (await bridge.request('call', {'name': 'ncp_run_get', 'arguments': {'run_id': submitted['run_id']}}))['structuredContent']['data']
                        if run['state'] not in {'queued', 'running'}:
                            break
                        await asyncio.sleep(.05)
                self.assertEqual(run['state'], 'completed')
                self.assertTrue(run['result']['execution_ids'])
                retried = (await bridge.request('call', submission))['structuredContent']['data']
                self.assertEqual(retried, run)
                observed = await bridge.request('runs')
                self.assertFalse(observed['complete'])
                self.assertEqual(len(observed['items']), 1)
                with self.assertRaises(ConsoleError):
                    await bridge.request('resource', {'uri': 'file:///etc/passwd'})
                diagnostics = await bridge.request('diagnostics')
                self.assertTrue(diagnostics['discovery_ok'])
                # This pinned host does not implement ping; do not invent a pass.
                self.assertIsNone(diagnostics['ping_ok'])
                self.assertTrue(diagnostics['warnings'])
                self.assertFalse(diagnostics['restart_available'])
                events = await bridge.request('events')
                self.assertTrue(any(e.get('tool') == 'ncp_plan_create' for e in events))
                self.assertNotIn('graph', json.dumps(events))
            finally:
                await bridge.close()
            self.assertEqual(bridge.connection, 'disconnected')

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(journey(Path(directory)))

    def test_http_boundary_and_no_restart_route(self):
        async def check():
            config = ConsoleConfig(Path(sys.executable), Path('/unused/profile.json'))
            app = create_app(MCPBridge(config), token='s' * 40, port=8765)

            async def request(path, headers, method='GET', body=b''):
                messages = []
                sent = False
                async def receive():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {'type': 'http.request', 'body': body, 'more_body': False}
                    await asyncio.Event().wait()
                async def send(message):
                    messages.append(message)
                await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
                    'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
                    'headers': headers, 'server': ('127.0.0.1', 8765), 'client': ('127.0.0.1', 1234)}, receive, send)
                return next(m['status'] for m in messages if m['type'] == 'http.response.start')

            host = [(b'host', b'127.0.0.1:8765')]
            self.assertEqual(await request('/api/status', host), 401)
            self.assertEqual(await request('/api/status', host + [(b'authorization', b'Bearer ' + b's'*40), (b'origin', b'https://evil.example')]), 403)
            self.assertEqual(await request('/api/restart', host + [(b'authorization', b'Bearer ' + b's'*40)], 'POST'), 404)
            self.assertEqual(await request('/', host), 200)
        asyncio.run(check())

    def test_actual_http_server_and_mcp_child(self):
        from ciw.agent_mcp import demo_config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = demo_config(root / 'profile')
            token = secrets.token_urlsafe(48)
            token_path = root / 'access.token'
            token_path.touch(mode=0o600)
            token_path.write_text(token)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f'http://127.0.0.1:{port}'
            with (root / 'http.log').open('w') as log:
                process = subprocess.Popen([sys.executable, 'console.py', '--python', sys.executable,
                    '--profile', str(profile), '--state-dir', str(root / 'state'),
                    '--token-file', str(token_path), '--port', str(port)],
                    cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=log)
                try:
                    until = time.monotonic() + 20
                    while True:
                        try:
                            response = opener.open(base + '/', timeout=1)
                            break
                        except urllib.error.URLError:
                            if time.monotonic() > until or process.poll() is not None:
                                self.fail('HTTP console did not become ready')
                            time.sleep(.1)
                    with response:
                        self.assertIn(b'Operator console', response.read())
                        self.assertIn("frame-ancestors 'self'", response.headers['Content-Security-Policy'])
                    for asset in ['/app.js', '/styles.css']:
                        with opener.open(base + asset) as asset_response:
                            self.assertEqual(asset_response.status, 200)
                    with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                        opener.open(base + '/api/status')
                    self.assertEqual(unauthorized.exception.code, 401)
                    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
                    with opener.open(urllib.request.Request(base + '/api/tools', headers=headers)) as tools_response:
                        self.assertEqual(len(json.load(tools_response)['data']), 16)
                    request = urllib.request.Request(base + '/api/calls',
                        data=json.dumps({'name': 'ncp_capabilities_list', 'arguments': {}}).encode(), headers=headers)
                    with opener.open(request) as tool_response:
                        result = json.load(tool_response)['data']
                        self.assertFalse(result['isError'])
                        self.assertTrue(result['structuredContent']['data']['items'])
                    for body, content_type, expected in [(b'[]', 'application/json', 400), (b'{', 'application/json', 400),
                        (b'{}','text/plain',415), (b'x' * 262145, 'application/json',413)]:
                        with self.assertRaises(urllib.error.HTTPError) as rejected:
                            opener.open(urllib.request.Request(base + '/api/calls', data=body,
                                headers={**headers, 'Content-Type': content_type}))
                        self.assertEqual(rejected.exception.code, expected)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()


class OperationalMCPTests(unittest.TestCase):
    def test_same_operational_attempts_and_results_through_existing_console(self):
        from ciw.operational_cli import example
        from ciw.operational_agent import OperationalHost
        async def journey(root):
            profile=example(root/'profile')
            with OperationalHost(profile):pass
            bridge=MCPBridge(ConsoleConfig(Path(sys.executable),profile,resume=True,state_dir=root/'console',surface='operational'))
            await bridge.open()
            try:
                tools=await bridge.request('tools')
                self.assertIn('oe_query',[t['name'] for t in tools])
                with self.assertRaises(ConsoleError):
                    await bridge.request('call',{'name':'net_execute','arguments':{'source':'assessment','graph':'assessment-graph','attempt':'console-ops'}})
                run=await bridge.request('call',{'name':'net_execute','arguments':{'source':'assessment','graph':'assessment-graph','attempt':'console-ops'},'confirmed':True})
                inventory=await bridge.request('runs')
                self.assertEqual(inventory['items'][0]['attempt'],'console-ops')
                self.assertEqual(inventory['items'][0]['result_ids'],run['structuredContent']['result_ids'])
                self.assertNotIn('run_id',inventory['items'][0])
                finding=(await bridge.request('call',{'name':'oe_model_support','arguments':{'attempt':'console-ops'},'confirmed':True}))['structuredContent']
                resource=await bridge.request('resource',{'uri':'ncp://operational-records/'+finding['record'][7:]})
                self.assertEqual(json.loads(resource['contents'][0]['text'])['record'],finding['record'])
            finally:await bridge.close()
        with tempfile.TemporaryDirectory() as temp:asyncio.run(journey(Path(temp)))
