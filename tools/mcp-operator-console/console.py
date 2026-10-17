"""Loopback-only operator console; all scientific operations use the MCP client.

No imports from ciw and no calls into its private host/session implementation.
The operator selects the executable and frozen profile at process launch.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import stat
import time

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

READ_TOOLS = frozenset({
    'ncp_workflows_list', 'ncp_capabilities_list', 'ncp_capability_describe',
    'ncp_plan_template', 'ncp_plan_get', 'ncp_run_get', 'ncp_artifact_read', 'ncp_observe',
})
WRITE_TOOLS = frozenset({
    'ncp_plan_create', 'ncp_plan_admit', 'ncp_run_submit', 'ncp_run_cancel',
    'ncp_compare', 'ncp_qualify',
})
MAX_BODY = 262144
ASSETS = Path(__file__).resolve().parent / 'web'


def now():
    return datetime.now(timezone.utc).isoformat()


class ConsoleError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ConsoleConfig:
    python: Path
    profile: Path
    instrument: str = 'builtin'
    resume: bool = False
    state_dir: Path = Path('console-state')


def security_error(host, origin, authorization, token, port):
    allowed = {f'localhost:{port}', f'127.0.0.1:{port}'}
    if host not in allowed or (origin is not None and origin != f'http://{host}'):
        return 403
    if not hmac.compare_digest(authorization.encode(), ('Bearer ' + token).encode()):
        return 401
    return None


class MCPBridge:
    """One actor owns the SDK's stdio context and serializes bounded requests.

    A transport timeout is an unknown outcome, never a reason to resubmit.
    Scientific jobs keep their host-owned run/attempt identities.
    """
    def __init__(self, config):
        self.config = config
        self.connection = 'disconnected'
        self.protocol = None
        self.server = None
        self.last_ok = None
        self.last_error = None
        self.tools = {}
        self.runs = {}
        self.events = deque(maxlen=300)
        self.queue = asyncio.Queue(maxsize=32)
        self.task = None
        self.client = None
        self.logger = None
        self.started = time.monotonic()

    def event(self, action, outcome, **fields):
        value = {'at': now(), 'action': action, 'outcome': outcome, **fields}
        self.events.append(value)
        if self.logger:
            self.logger.info(json.dumps(value))

    async def open(self):
        if self.task is not None:
            raise ConsoleError('already_started', 'This console has already started.', 409)
        self.task = asyncio.create_task(self._worker())
        return await self.request('connect')

    async def close(self):
        if self.task and not self.task.done():
            # Closing the parent is a lifecycle action; SDK cleanup owns its child.
            await self.request('shutdown')
            await self.task
        if self.logger:
            for handler in self.logger.handlers[:]:
                handler.close()
                self.logger.removeHandler(handler)

    async def request(self, action, payload=None):
        if self.task is None or self.task.done():
            raise ConsoleError('disconnected', 'MCP worker is not available.', 503)
        future = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((action, payload or {}, future))
        except asyncio.QueueFull:
            raise ConsoleError('busy', 'Console request queue is full.', 429) from None
        try:
            return await asyncio.wait_for(asyncio.shield(future), 90)
        except TimeoutError:
            # Consume eventual errors but leave the actor's operation untouched.
            future.add_done_callback(lambda result: result.exception() if not result.cancelled() else None)
            raise ConsoleError('outcome_unknown', 'Response timed out. Do not repeat a write blindly; inspect its retained state.', 504) from None

    async def _worker(self):
        stack = AsyncExitStack()
        try:
            while True:
                action, payload, future = await self.queue.get()
                stop = action == 'shutdown'
                try:
                    if action == 'connect':
                        result = await self._connect(stack)
                    elif stop:
                        self.connection = 'closing'
                        await stack.aclose()
                        self.client = None
                        self.connection = 'disconnected'
                        self.event('session', 'closed')
                        result = self.snapshot()
                    else:
                        result = await self._dispatch(action, payload)
                except ConsoleError as exc:
                    self.event(action, 'refused', code=exc.code)
                    if not future.done():
                        # Do not transfer the actor's live coroutine traceback to
                        # callers (test/reporting code may clear its frames).
                        future.set_exception(ConsoleError(exc.code, str(exc), exc.status))
                except Exception:
                    self.last_error = 'transport_or_server_error'
                    self.connection = 'degraded'
                    self.event(action, 'unknown', code=self.last_error)
                    if not future.done():
                        future.set_exception(ConsoleError('outcome_unknown',
                            'MCP response unavailable. Inspect retained state before retrying a write. See local server-stderr.log for startup errors.', 502))
                else:
                    if not future.done():
                        future.set_result(result)
                finally:
                    self.queue.task_done()
                if stop:
                    break
        finally:
            await stack.aclose()
            self.connection = 'disconnected'

    async def _connect(self, stack):
        cfg = self.config
        if not cfg.python.is_absolute() or not cfg.python.is_file() or not cfg.profile.is_absolute() or not cfg.profile.is_file():
            raise ConsoleError('invalid_configuration', 'Executable and profile must be existing absolute paths.')
        if cfg.instrument not in {'builtin', 'polymer', 'temperature', 'link-scalar'}:
            raise ConsoleError('unsupported_instrument', 'Native provider launch configuration is not supported by this console increment.')
        cfg.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(cfg.state_dir, 0o700)
        self.logger = logging.getLogger('ncp-console.' + str(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        audit_path = cfg.state_dir / 'events.jsonl'
        audit_path.touch(mode=0o600, exist_ok=True)
        os.chmod(audit_path, 0o600)
        handler = RotatingFileHandler(audit_path, maxBytes=2*1024*1024, backupCount=3)
        self.logger.addHandler(handler)
        error_path = cfg.state_dir / 'server-stderr.log'
        error_path.touch(mode=0o600, exist_ok=True)
        os.chmod(error_path, 0o600)
        errors = stack.enter_context(error_path.open('a', encoding='utf-8'))
        args = ['-I', '-m', 'ciw.net', 'control', 'serve', '--profile', str(cfg.profile), '--instrument', cfg.instrument]
        if cfg.resume:
            args.append('--resume')
        self.connection = 'connecting'
        read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(command=str(cfg.python), args=args), errlog=errors))
        self.client = await stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=30))
        hello = await self.client.discover()
        self.protocol = self.client.protocol_version
        self.server = hello.model_dump(mode='json', by_alias=True)
        await self._discover()
        self.connection = 'connected'
        self.last_ok = now()
        self.event('session', 'connected', protocol=self.protocol, tool_count=len(self.tools))
        return self.snapshot()

    async def _discover(self):
        listed = await self.client.list_tools()
        if listed.next_cursor:
            raise ConsoleError('unsupported_pagination', 'This control profile unexpectedly paginates MCP discovery.', 502)
        self.tools = {tool.name: tool.model_dump(mode='json', by_alias=True) for tool in listed.tools}

    def snapshot(self):
        return {'connection': self.connection, 'protocol': self.protocol, 'transport': 'stdio',
            'profile': self.config.profile.name, 'instrument': self.config.instrument,
            'resume': self.config.resume, 'tools': len(self.tools), 'last_ok': self.last_ok,
            'last_error': self.last_error, 'uptime_seconds': round(time.monotonic() - self.started),
            'known_runs': len(self.runs), 'run_inventory_complete': False,
            'restart_available': False,
            'restart_reason': 'The server does not expose a complete job inventory or drain handshake. Restart is intentionally unavailable.',
            'authority': 'Existing frozen host grants only. Discovery is not qualification.'}

    async def _dispatch(self, action, payload):
        if action == 'status':
            return self.snapshot()
        if action == 'events':
            return list(self.events)
        if self.client is None:
            raise ConsoleError('disconnected', 'MCP is not connected.', 503)
        if action == 'tools':
            return [{**tool, 'console_access': 'read' if name in READ_TOOLS else 'confirm' if name in WRITE_TOOLS else 'unsupported'} for name, tool in sorted(self.tools.items())]
        if action == 'diagnostics':
            started = time.monotonic()
            ping_ok = None
            warnings = []
            try:
                await self.client.send_ping()
                ping_ok = True
            except MCPError as exc:
                if exc.code != -32601:
                    raise
                warnings.append('Server returned method-not-found for ping; tool discovery is the available liveness check.')
            await self._discover()
            self.last_ok, self.last_error, self.connection = now(), None, 'connected'
            self.event('diagnostics', 'warning' if warnings else 'passed', tool_count=len(self.tools))
            return {**self.snapshot(), 'ping_ok': ping_ok, 'discovery_ok': True, 'warnings': warnings,
                'latency_ms': round((time.monotonic()-started)*1000),
                'checks': (['MCP ping acknowledged'] if ping_ok else []) + ['Tool discovery refreshed'],
                'not_checked': ['Provider numerical validity', 'Physical qualification', 'All retained jobs', 'Dependency upgrades']}
        if action == 'call':
            return await self._call(payload)
        if action == 'runs':
            for run_id, run in list(self.runs.items()):
                if run.get('state') in {'queued', 'running'}:
                    await self._call({'name': 'ncp_run_get', 'arguments': {'run_id': run_id}})
            return {'items': list(self.runs.values()), 'complete': False, 'scope': 'Runs observed by this console process. Inspect another retained run by ID.'}
        if action == 'resource':
            uri = payload.get('uri')
            if not isinstance(uri, str) or not re.fullmatch(r'ncp://(?:plans|runs|artifacts)/[A-Za-z0-9_.:-]{1,200}', uri):
                raise ConsoleError('invalid_resource', 'Use an ncp://plans, runs, or artifacts URI with a retained ID.')
            result = (await self.client.read_resource(uri)).model_dump(mode='json', by_alias=True)
            self.last_ok = now()
            self.event('resource', 'read')
            return result
        raise ConsoleError('unknown_action', 'Unsupported console operation.', 404)

    async def _call(self, payload):
        if set(payload) - {'name', 'arguments', 'confirmed'}:
            raise ConsoleError('invalid_request', 'Unknown tool-call fields.')
        name, arguments = payload.get('name'), payload.get('arguments', {})
        if not isinstance(name, str) or name not in self.tools or name not in READ_TOOLS | WRITE_TOOLS:
            raise ConsoleError('unsupported_tool', 'Tool is not on the discovered control-server allowlist.')
        if name in WRITE_TOOLS and payload.get('confirmed') is not True:
            raise ConsoleError('confirmation_required', 'Review and confirm this state-changing MCP call.', 409)
        try:
            jsonschema.validate(arguments, self.tools[name]['inputSchema'])
        except jsonschema.ValidationError:
            raise ConsoleError('invalid_arguments', 'Arguments do not match the discovered MCP input schema.', 422) from None
        self.event('tool', 'requested', tool=name)
        started = time.monotonic()
        raw = (await self.client.call_tool(name, arguments)).model_dump(mode='json', by_alias=True)
        output_schema = self.tools[name].get('outputSchema')
        if output_schema:
            try:
                jsonschema.validate(raw.get('structuredContent'), output_schema)
            except jsonschema.ValidationError:
                raise ConsoleError('invalid_server_response', 'Server output failed its published schema. The operation may have applied; do not blindly retry.', 502) from None
        data = (raw.get('structuredContent') or {}).get('data')
        if isinstance(data, dict) and isinstance(data.get('run_id'), str) and 'state' in data:
            self.runs[data['run_id']] = data
        self.last_ok = now()
        self.event('tool', 'refused' if raw.get('isError') else 'returned', tool=name, elapsed_ms=round((time.monotonic()-started)*1000))
        return raw


def create_app(bridge, *, token, port=8765):
    if len(token) < 32:
        raise ValueError('Console token must contain at least 32 characters.')

    @asynccontextmanager
    async def lifespan(app):
        try:
            await bridge.open()
        except ConsoleError:
            pass  # Keep diagnostics UI reachable when the configured child refuses startup.
        try:
            yield
        finally:
            await bridge.close()

    async def endpoint(request: Request):
        path = request.url.path
        if path in {'/', '/app.js', '/styles.css'} and request.method == 'GET':
            if request.headers.get('host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                return JSONResponse({'error': {'code': 'invalid_host'}}, status_code=403)
            name = 'index.html' if path == '/' else path[1:]
            return FileResponse(ASSETS / name, headers={
                'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
                'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store'})
        rejected = security_error(request.headers.get('host'), request.headers.get('origin'), request.headers.get('authorization', ''), token, port)
        if rejected:
            return JSONResponse({'error': {'code': 'unauthorized' if rejected == 401 else 'origin_or_host_denied'}}, status_code=rejected)
        routes = {('GET', '/api/status'): 'status', ('GET', '/api/tools'): 'tools',
            ('GET', '/api/events'): 'events', ('GET', '/api/runs'): 'runs',
            ('POST', '/api/calls'): 'call', ('POST', '/api/resources'): 'resource',
            ('POST', '/api/diagnostics'): 'diagnostics'}
        action = routes.get((request.method, path))
        if action is None:
            return JSONResponse({'error': {'code': 'not_found'}}, status_code=404)
        try:
            body = {}
            if request.method == 'POST':
                if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                    raise ConsoleError('invalid_content_type', 'Expected application/json.', 415)
                raw = bytearray()
                async for part in request.stream():
                    raw.extend(part)
                    if len(raw) > MAX_BODY:
                        raise ConsoleError('body_too_large', 'Request exceeds 256 KiB.', 413)
                try:
                    body = json.loads(raw or b'{}')
                except (ValueError, UnicodeDecodeError):
                    raise ConsoleError('invalid_json', 'Request is not valid JSON.') from None
                if not isinstance(body, dict):
                    raise ConsoleError('invalid_request', 'Expected a JSON object.')
            return JSONResponse({'data': await bridge.request(action, body)}, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        except ConsoleError as exc:
            return JSONResponse({'error': {'code': exc.code, 'message': str(exc)}}, status_code=exc.status, headers={'Cache-Control': 'no-store'})

    return Starlette(lifespan=lifespan, routes=[Route('/{path:path}', endpoint, methods=['GET', 'POST'])])


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True, help='Absolute installed NCP Python interpreter; launched with -I.')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--instrument', choices=['builtin', 'polymer', 'temperature', 'link-scalar'], default='builtin')
    parser.add_argument('--resume', action='store_true', help='Reopen an existing host; never automatically reruns jobs.')
    parser.add_argument('--state-dir', type=Path, required=True, help='Private local directory for console diagnostics.')
    parser.add_argument('--token-file', type=Path, required=True, help='Mode-0600 file containing at least 32 random characters.')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Choose a port between 1024 and 65535.')
    info = args.token_file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        parser.error('Token file must be a regular private file (mode 0600).')
    token = args.token_file.read_text().strip()
    config = ConsoleConfig(args.python, args.profile, args.instrument, args.resume, args.state_dir.resolve())
    uvicorn.run(create_app(MCPBridge(config), token=token, port=args.port), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
