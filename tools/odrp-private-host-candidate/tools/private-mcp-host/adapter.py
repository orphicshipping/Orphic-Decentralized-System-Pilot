"""Container-only HTTP adapter; real MCP is the sole scientific execution path.

The private Worker supplies the last committed archive. Each transaction reopens
the retained MCP host, executes one bounded request, closes it, and returns a
checkpoint. The Worker commits that checkpoint before acknowledging the request.
No direct browser route, shell command, or filesystem operation is exposed.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from ciw.services import invoke, open_reference, reference_tools
from ciw.reference_profile import REFERENCE_NAMES

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / 'tools' / 'mcp-operator-console'))
sys.path.insert(0, str(REPO / 'examples' / 'numerical_operations'))
from console import ConsoleConfig, ConsoleError, MCPBridge
from prepare import prepare

STATE = Path(os.environ.get('NCP_TRANSACTION_STATE', '/srv/ncp/runtime/state')).resolve()
MAX_ARCHIVE = 16 * 1024 * 1024
LOCK = asyncio.Lock()
# Fixed candidate generation: never accept historical/unversioned checkpoints.
GENERATION = 'odsp-24f6ef-odrp-v3-isolated-candidate'
CORE_REVISION = '24f6ef074c76da0ddf2a405001c00020061e98fc+odrp-v3'

def reference_host():
    """Trusted launch configuration only; no tool argument can select corpus."""
    catalog = os.environ.get('ODRP_CATALOG')
    ref = os.environ.get('ODRP_CATALOG_REF')
    evidence = os.environ.get('ODRP_EVIDENCE')
    evidence_ref = os.environ.get('ODRP_EVIDENCE_REF')
    if not any((catalog, ref, evidence, evidence_ref)):
        return None
    if not catalog or not ref or not Path(catalog).is_absolute():
        raise ValueError('reference_binding_refused')
    if evidence and not Path(evidence).is_absolute():
        raise ValueError('reference_binding_refused')
    return open_reference(catalog_path=catalog, catalog_ref=ref, evidence_path=evidence, evidence_ref=evidence_ref)



def validate_members(members):
    if sum(m.size for m in members) > MAX_ARCHIVE or len(members) > 10000:
        raise ValueError('Expanded checkpoint exceeds the deployment bound')
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
            raise ValueError('Checkpoint contains an unsafe entry')


def restore(encoded):
    """Only the committed private archive can populate this disposable directory."""
    if STATE.name != 'state' or STATE == Path('/state'):
        raise ValueError('Unsafe transaction state path')
    if STATE.is_symlink():
        raise ValueError('Transaction state must not be a symlink')
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # Validate in a disposable sibling before touching the current local state.
    with tempfile.TemporaryDirectory(dir=STATE.parent, prefix='.candidate-') as directory:
        staged = Path(directory) / 'state'
        staged.mkdir(mode=0o700)
        if encoded is None:
            prepare(staged / 'profile')
            (staged / 'deployment-generation.json').write_text(json.dumps({'generation': GENERATION}))
        else:
            if not isinstance(encoded, str) or len(encoded) > 24 * 1024 * 1024:
                raise ValueError('Checkpoint exceeds the deployment bound')
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_ARCHIVE:
                raise ValueError('Checkpoint exceeds the deployment bound')
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
                members = archive.getmembers()
                validate_members(members)
                archive.extractall(staged, members=members, filter='data')
            marker = staged / 'deployment-generation.json'
            if not marker.is_file() or json.loads(marker.read_text()).get('generation') != GENERATION:
                raise ValueError('Historical checkpoint requires explicit offline migration; automatic restore refused')
        if STATE.exists():
            shutil.rmtree(STATE)
        shutil.move(str(staged), str(STATE))
    return encoded is not None


def checkpoint():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        for path in sorted(STATE.iterdir()):
            archive.add(path, arcname=path.name)
    if data.tell() > MAX_ARCHIVE:
        raise ValueError('Checkpoint exceeds the deployment bound')
    data.seek(0)
    with tarfile.open(fileobj=data, mode='r:gz') as archive:
        validate_members(archive.getmembers())
    return base64.b64encode(data.getvalue()).decode('ascii')


async def transact(request: Request):
    async with LOCK:
        body = await request.json()
        resumed = restore(body.get('state'))
        journal_path = STATE / 'operator.json'
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else {'runs': {}, 'events': []}
        bridge = MCPBridge(ConsoleConfig(Path(sys.executable).absolute(), STATE / 'profile' / 'profile.json',
            resume=resumed, state_dir=STATE / 'console'))
        try:
            await bridge.open()
            action = body['action']
            payload = body.get('payload', {})
            if action == 'call' and 'arguments_json' in payload:
                if 'arguments' in payload or not isinstance(payload['arguments_json'], str):
                    raise ConsoleError('invalid_arguments', 'Supply either arguments or arguments_json, not both.')
                try:
                    arguments = json.loads(payload['arguments_json'])
                except (ValueError, TypeError):
                    raise ConsoleError('invalid_arguments', 'arguments_json must contain valid JSON.') from None
                payload = {key: value for key, value in payload.items() if key != 'arguments_json'}
                payload['arguments'] = arguments
            ref_host = reference_host()
            if action == 'call' and payload.get('name') in REFERENCE_NAMES:
                if ref_host is None:
                    raise ConsoleError('reference_not_configured', 'No approved reference bundle is bound.')
                try:
                    data = invoke(payload['name'], payload.get('arguments', {}), host=ref_host)
                    result = {'structuredContent': {'data': data}, 'isError': False}
                except ValueError:
                    raise ConsoleError('reference_refused', 'Reference operation refused.') from None
            elif action == 'runs':
                items = []
                for run_id in journal['runs']:
                    raw = await bridge.request('call', {'name': 'ncp_run_get', 'arguments': {'run_id': run_id}})
                    items.append(raw['structuredContent']['data'])
                result = {'items': items, 'complete': False, 'scope': 'Runs observed through this hosted panel; other hosts are not enumerated.'}
            elif action == 'events':
                result = journal['events']
            else:
                result = await bridge.request(action, payload)
            if action == 'tools' and ref_host is not None:
                result = result + reference_tools(ref_host)
            # This deployment grants only the seven bounded numerical models.
            # Finish a submitted run before closing its owning stdio process.
            if action == 'call' and payload.get('name') == 'ncp_run_submit' and not result.get('isError'):
                data = (result.get('structuredContent') or {}).get('data', {})
                async with asyncio.timeout(60):
                    while data.get('state') in {'queued', 'running'}:
                        await asyncio.sleep(.05)
                        result = await bridge.request('call', {'name': 'ncp_run_get', 'arguments': {'run_id': data['run_id']}})
                        data = (result.get('structuredContent') or {}).get('data', {})
            if action == 'call':
                data = (result.get('structuredContent') or {}).get('data', {})
                if isinstance(data, dict) and isinstance(data.get('run_id'), str) and 'state' in data:
                    journal['runs'][data['run_id']] = data
            if action in {'status', 'diagnostics'}:
                result['known_runs'] = len(journal['runs'])
                result['transport'] = 'private HTTPS → stdio'
            journal['events'] = (journal['events'] + await bridge.request('events'))[-300:]
            # JSON text is carried as a string across JavaScript boundaries.
            # Do not coerce 1.0 to 1 or round large integers in hashed records.
            response, status = {'data': result, 'raw_json': json.dumps(result)}, 200
            if action == 'call' and payload.get('name') == 'ncp_plan_template' and not result.get('isError'):
                response['plan_arguments_json'] = json.dumps({'source': 'source',
                    'baseline': payload['arguments']['baseline'], 'graph': result['structuredContent']['data']['graph']}, indent=2)
        except ConsoleError as error:
            if error.code in {'outcome_unknown', 'invalid_server_response'}:
                raise  # Worker leaves its durable uncertainty marker in place.
            response, status = {'error': {'code': error.code, 'message': str(error)}}, error.status
        finally:
            await bridge.close()
        journal_path.write_text(json.dumps(journal))
        return JSONResponse({'result': response, 'status': status, 'state': checkpoint()})


async def health(request):
    return JSONResponse({'ready': True, 'core_revision': CORE_REVISION, 'generation': GENERATION, 'reference_configured_not_qualified': bool(os.environ.get('ODRP_CATALOG'))})


app = Starlette(routes=[Route('/health', health), Route('/transaction', transact, methods=['POST'])])

