"""Private container transport over the installed, principal-bound MCP service.

The authenticated Worker is this endpoint's only network authority. Local Python
access is operator authority, not remote authentication. The Worker owns durable
commit/fencing; this adapter never declares a transaction durably committed and
never clears a historical or uncertain fence. Reference disclosure uses the
same principal facade, separately from scientific execution over real MCP.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import gzip
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import uuid

from ciw.services import open_reference
from ciw.reference_profile import REFERENCE_NAMES
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# The source candidate is assembled at REPO in the container; local qualification
# uses the repository's identical console and synthetic preparation modules.
if not (REPO / 'tools' / 'mcp-operator-console' / 'console.py').is_file():
    REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / 'tools' / 'mcp-operator-console'))
sys.path.insert(0, str(REPO / 'examples' / 'numerical_operations'))
from console import ConsoleConfig, ConsoleError, MCPBridge
from prepare import prepare

STATE = Path(os.environ.get('NCP_TRANSACTION_STATE', '/srv/ncp/runtime/state')).absolute()
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_REQUEST = 24 * 1024 * 1024
MAX_PAYLOAD = 256 * 1024
MAX_RESPONSE = 32 * 1024 * 1024
MAX_RESULT = 2 * 1024 * 1024
MAX_JOURNAL = 2 * 1024 * 1024
MAX_EVENTS = 4096
MAX_RUNS = 128
MAX_INFLIGHT = 8
MAX_WORKSPACES = 8
MAX_LOCAL_BYTES = 128 * 1024 * 1024
QUEUE_SECONDS = 2
TRANSACTION_SECONDS = 90
CLOSE_SECONDS = 5
LOCK = asyncio.Lock()
INFLIGHT = 0
ACTIVE = set()
UNCERTAIN_WORKERS = {}
LOCAL_FENCES = set()
TOKEN = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
ACTIONS = frozenset({'call', 'resource', 'tools', 'runs', 'events', 'status', 'diagnostics'})
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}


class Refused(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


class Uncertain(RuntimeError):
    """Retain both exception objects locally without exposing their messages."""
    def __init__(self, primary=None, cleanup=None):
        super().__init__('outcome_unknown')
        self.primary, self.cleanup = primary, cleanup


def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise Refused('invalid_json') from None


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Refused('duplicate_json_key')
            result[key] = value
        return result

    def number(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise Refused('invalid_json')
        return value

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_float=number,
                           parse_constant=lambda _: (_ for _ in ()).throw(Refused('invalid_json')))
        # UTF-8 validation includes escaped lone surrogates; recursion is bounded
        # before schema checks and before filesystem/provider work.
        encoded(value)
        stack = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > 64:
                raise Refused('invalid_json')
            if isinstance(item, dict):
                stack.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, Refused):
            raise
        raise Refused('invalid_json') from None


def response(value, status=200):
    raw = encoded(value)
    if len(raw) > MAX_RESPONSE:
        raise Refused('response_budget_exceeded', 413)
    return Response(raw, status_code=status, media_type='application/json', headers=HEADERS)


def refusal(error):
    return response({'error': {'code': error.code}}, error.status)


def exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise Refused('invalid_envelope')


def safe_path(path):
    if not path.is_absolute() or any(part == '..' for part in path.parts):
        raise Refused('host_configuration_refused', 503)
    for component in (path, *path.parents):
        if component.is_symlink():
            raise Refused('host_configuration_refused', 503)


def read_regular(path, maximum):
    safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Refused('unsafe_retained_file')
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise Refused('retained_budget_exceeded', 413)
    return raw


@dataclass(frozen=True)
class Configuration:
    owner_id: str
    generation: str
    release: dict
    policy_path: Path
    policy: object


def configuration():
    try:
        from ciw.principal_policy import PrincipalPolicy
        from ciw.private_release import verify_release
        owner = os.environ['ODSP_OWNER_ID']
        generation = os.environ['ODSP_CHECKPOINT_GENERATION']
        if not TOKEN.fullmatch(owner) or not TOKEN.fullmatch(generation):
            raise ValueError
        safe_path(STATE)
        if STATE.name != 'state' or len(STATE.parts) < 4:
            raise ValueError
        policy_path = Path(os.environ['ODSP_PRINCIPAL_POLICY'])
        manifest = Path(os.environ['ODSP_RELEASE_MANIFEST'])
        safe_path(policy_path)
        safe_path(manifest)
        if policy_path.is_relative_to(STATE) or manifest.is_relative_to(STATE):
            raise ValueError
        release = verify_release(str(manifest))
        policy = PrincipalPolicy.from_file(policy_path)
        # Loading the file is read-only. No checkpoint, child, or provider is
        # started by readiness. Principal checks are repeated per request.
        policy.snapshot()
        return Configuration(owner, generation, release, policy_path, policy)
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        raise Refused('host_configuration_refused', 503) from None


def reference_host():
    """Only trusted launch configuration can select reference snapshots."""
    catalog = os.environ.get('ODRP_CATALOG')
    ref = os.environ.get('ODRP_CATALOG_REF')
    evidence = os.environ.get('ODRP_EVIDENCE')
    evidence_ref = os.environ.get('ODRP_EVIDENCE_REF')
    if not any((catalog, ref, evidence, evidence_ref)):
        return None
    if not catalog or not ref or not Path(catalog).is_absolute():
        raise Refused('reference_binding_refused', 503)
    if bool(evidence) != bool(evidence_ref) or (evidence and not Path(evidence).is_absolute()):
        raise Refused('reference_binding_refused', 503)
    try:
        return open_reference(catalog_path=catalog, catalog_ref=ref,
                              evidence_path=evidence, evidence_ref=evidence_ref)
    except (OSError, ValueError, TypeError):
        raise Refused('reference_binding_refused', 503) from None


def validate_envelope(body, config):
    exact(body, {'action', 'payload', 'state', 'identity', 'transaction'})
    action, payload = body['action'], body['payload']
    if type(action) is not str or action not in ACTIONS or type(payload) is not dict:
        raise Refused('invalid_envelope')
    if len(encoded(payload)) > MAX_PAYLOAD:
        raise Refused('payload_budget_exceeded', 413)
    if action == 'call':
        if set(payload) - {'name', 'arguments', 'arguments_json', 'confirmed'}:
            raise Refused('invalid_payload')
        if type(payload.get('name')) is not str or not re.fullmatch(r'ncp_[a-z0-9_]{1,100}', payload['name']):
            raise Refused('invalid_payload')
        if 'confirmed' in payload and type(payload['confirmed']) is not bool:
            raise Refused('invalid_payload')
        if 'arguments_json' in payload:
            if 'arguments' in payload or type(payload['arguments_json']) is not str:
                raise Refused('invalid_arguments')
            payload = {key: val for key, val in payload.items() if key != 'arguments_json'} | {
                'arguments': strict_json(body['payload']['arguments_json'])}
        else:
            payload = {**payload, 'arguments': payload.get('arguments', {})}
        if type(payload['arguments']) is not dict:
            raise Refused('invalid_arguments')
    elif action == 'resource':
        exact(payload, {'uri'})
        if type(payload['uri']) is not str:
            raise Refused('invalid_resource')
    elif payload:
        raise Refused('invalid_payload')
    transaction = body['transaction']
    exact(transaction, {'id', 'owner_id', 'generation', 'epoch', 'base_commit', 'release'})
    if (type(transaction['id']) is not str or not UUID.fullmatch(transaction['id'])
            or transaction['owner_id'] != config.owner_id or transaction['generation'] != config.generation
            or type(transaction['epoch']) is not int or not 1 <= transaction['epoch'] <= 2**53 - 1
            or (transaction['base_commit'] is not None and (type(transaction['base_commit']) is not str
                or not UUID.fullmatch(transaction['base_commit'])))
            or transaction['id'] == transaction['base_commit']
            or transaction['release'] != config.release):
        raise Refused('transaction_binding_refused', 409)
    state = body['state']
    if state is not None and (type(state) is not str or len(state) > (MAX_ARCHIVE + 2) // 3 * 4):
        raise Refused('checkpoint_budget_exceeded', 413)
    if (state is None) != (transaction['base_commit'] is None):
        raise Refused('checkpoint_binding_refused', 409)
    try:
        principal = config.policy.validate_context(body['identity'])
        if action == 'call':
            config.policy.authorize(principal, payload['name'], payload['arguments'])
        elif action == 'resource':
            config.policy.authorize_resource(principal, payload['uri'])
        elif action == 'runs':
            # Inventory must not turn into a retained-read authority shortcut.
            grant = config.policy.check_principal(principal)
            if not any(rule['name'] == 'ncp_run_get' for rule in grant['operations']):
                raise ValueError
    except ValueError:
        raise Refused('principal_policy_refused', 403) from None
    if action == 'call' and payload['name'] not in REFERENCE_NAMES:
        from ciw.control_gateway import TOOLS
        if payload['name'] not in TOOLS:
            raise Refused('unsupported_tool', 404)
        if not TOOLS[payload['name']]['annotations']['readOnlyHint'] and payload.get('confirmed') is not True:
            raise Refused('confirmation_required', 409)
    return {**body, 'payload': payload}, principal


def workspace_path(body):
    return STATE / 'transactions' / body['transaction']['id'] / 'state'


def principal_path(directory, identity):
    return directory / 'principals' / sha256(identity['principal'].encode('ascii')).hexdigest()


def fence_path(identity):
    return STATE / 'fences' / (sha256(identity['principal'].encode('ascii')).hexdigest() + '.json')


def returned_path(transaction_id):
    return STATE / 'returned' / (transaction_id + '.json')


def validate_members(members):
    if len(members) > MAX_MEMBERS or sum(item.size for item in members) > MAX_ARCHIVE:
        raise Refused('expanded_checkpoint_budget_exceeded', 413)
    names, files = set(), set()
    for member in members:
        name = member.name.rstrip('/') if member.isdir() else member.name
        parts = name.split('/')
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or any(part in {'', '.', '..'} for part in parts)
                or '\\' in name or '\x00' in name or ':' in name or len(parts) > 20
                or len(name.encode('utf-8')) > 512 or name in names
                or not (member.isfile() or member.isdir()) or member.size < 0
                or (member.isdir() and member.size != 0) or member.pax_headers
                or any('/'.join(parts[:i]) in files for i in range(1, len(parts)))):
            raise Refused('unsafe_checkpoint_entry')
        if member.isfile() and any(old.startswith(name + '/') for old in names):
            raise Refused('unsafe_checkpoint_entry')
        names.add(name)
        if member.isfile():
            files.add(name)


def archive_bytes(encoded_state):
    try:
        raw = base64.b64decode(encoded_state, validate=True)
        if len(raw) > MAX_ARCHIVE:
            raise Refused('checkpoint_budget_exceeded', 413)
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            # Bound gzip expansion, including headers/padding, before tarfile
            # sees a PAX or extended header that could allocate unbounded data.
            maximum = MAX_ARCHIVE + MAX_MEMBERS * 1024 + 10240
            expanded = stream.read(maximum + 1)
        if len(expanded) > maximum:
            raise Refused('expanded_checkpoint_budget_exceeded', 413)
        return expanded
    except (OSError, EOFError, ValueError) as exc:
        if isinstance(exc, Refused):
            raise
        raise Refused('invalid_checkpoint') from None


def marker_for(body, config):
    return {'schema': 'odsp.private-checkpoint.v1', 'owner_id': config.owner_id,
            'generation': config.generation, 'identity': {key: body['identity'][key] for key in ('principal', 'owner')},
            'transaction_id': body['transaction']['id'], 'epoch': body['transaction']['epoch'],
            'release': config.release}


def load_journal(directory):
    path = directory / 'operator.json'
    if not path.exists():
        return {'runs': [], 'events': []}
    journal = strict_json(read_regular(path, MAX_JOURNAL))
    exact(journal, {'runs', 'events'})
    if (type(journal['runs']) is not list or len(journal['runs']) > MAX_RUNS
            or any(type(item) is not str or not re.fullmatch(r'run-[0-9a-f]{32}', item) for item in journal['runs'])
            or len(set(journal['runs'])) != len(journal['runs'])
            or type(journal['events']) is not list or len(journal['events']) > MAX_EVENTS):
        raise Refused('invalid_operator_journal')
    for event in journal['events']:
        exact(event, {'transaction_id', 'action', 'outcome'})
        if (type(event['transaction_id']) is not str or not UUID.fullmatch(event['transaction_id'])
                or type(event['action']) is not str or event['action'] not in ACTIONS
                or type(event['outcome']) is not str or event['outcome'] not in {'started', 'returned', 'refused', 'unknown'}):
            raise Refused('invalid_operator_journal')
    return journal


def journal_capacity(journal, body):
    if len(journal['events']) + 3 > MAX_EVENTS or len(encoded(journal)) + 1024 > MAX_JOURNAL:
        raise Refused('audit_capacity_exhausted', 507)
    if body['action'] == 'call' and body['payload']['name'] == 'ncp_run_submit' and len(journal['runs']) >= MAX_RUNS:
        raise Refused('run_capacity_exhausted', 507)


def frozen_profile(directory, expected):
    # Checkpoint bytes are data, never host-selected paths or grants. Validate
    # all three immutable inputs against trusted, deterministic preparation.
    for name in ('profile.json', 'source.json', 'experiment.json'):
        if read_regular(directory / 'profile' / name, MAX_ARCHIVE) != read_regular(expected / name, MAX_ARCHIVE):
            raise Refused('frozen_profile_mismatch', 409)


def workspace_capacity():
    root = STATE / 'transactions'
    if not root.exists():
        return
    count, total = 0, 0
    for child in root.iterdir():
        count += 1
        for path in child.rglob('*'):
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise Refused('unsafe_working_state', 409)
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
        if count >= MAX_WORKSPACES or total + MAX_ARCHIVE > MAX_LOCAL_BYTES:
            raise Refused('workspace_capacity_exhausted', 507)


def acknowledge_returned(body, config):
    """Only a later Worker base_commit can acknowledge a returned checkpoint.

    Until then, local effects and their completion receipt survive a Worker
    publication failure. A receipt exists only after verified child cleanup;
    this function never removes an uncertain/live writer's workspace.
    """
    previous = body['transaction']['base_commit']
    if previous is None:
        return
    root = STATE / 'transactions' / previous
    receipt_path = returned_path(previous)
    if not receipt_path.exists():
        return  # A restarted container can have only the durable Worker bytes.
    safe_path(root)
    receipt = strict_json(read_regular(receipt_path, 16384))
    exact(receipt, {'schema', 'transaction', 'checkpoint_sha256'})
    prior = receipt['transaction']
    exact(prior, {'id', 'owner_id', 'generation', 'epoch', 'base_commit', 'release'})
    if (receipt['schema'] != 'odsp.private-returned-checkpoint.v1'
            or prior['id'] != previous or prior['owner_id'] != config.owner_id
            or prior['generation'] != config.generation or type(prior['epoch']) is not int
            or not 1 <= prior['epoch'] <= body['transaction']['epoch']
            or receipt['checkpoint_sha256'] != sha256(body['state'].encode('ascii')).hexdigest()):
        raise Refused('checkpoint_acknowledgement_refused', 409)
    # Keep the receipt until the acknowledged state has been removed; a cleanup
    # failure before a new operation cannot erase its provenance or start work.
    if root.exists():
        shutil.rmtree(root)
    receipt_path.unlink()


def restore(encoded_state, *, body, config):
    """Stage a global snapshot in a new, never-reused transaction workspace.

    An old live writer can only touch its old workspace. The durable Worker's
    epoch/CAS additionally prevents publishing its result as the current state.
    """
    from ciw.principal_policy import owner_for
    destination = workspace_path(body)
    safe_path(destination)
    workspace_capacity()
    if destination.parent.exists():
        raise Refused('transaction_workspace_reuse_refused', 409)
    destination.parent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=destination.parent.parent, prefix='.candidate-') as directory:
        root = Path(directory)
        staged = root / 'state'
        staged.mkdir(mode=0o700)
        if encoded_state is not None:
            try:
                with tarfile.open(fileobj=io.BytesIO(archive_bytes(encoded_state)), mode='r:') as archive:
                    members = []
                    for member in archive:
                        members.append(member)
                        if len(members) > MAX_MEMBERS:
                            raise Refused('expanded_checkpoint_budget_exceeded', 413)
                    validate_members(members)
                    for member in members:
                        target = staged / member.name
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True, mode=0o700)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                            with archive.extractfile(member) as source, target.open('xb') as output:
                                shutil.copyfileobj(source, output, 65536)
                            target.chmod(0o600)
            except (tarfile.TarError, OSError, EOFError, UnicodeError):
                raise Refused('invalid_checkpoint') from None
            marker_path = staged / 'deployment-generation.json'
            if not marker_path.is_file():
                raise Refused('historical_checkpoint_migration_required', 409)
            marker = strict_json(read_regular(marker_path, 16384))
            exact(marker, {'schema', 'owner_id', 'generation', 'identity', 'transaction_id', 'epoch', 'release'})
            from ciw.private_release import validate_identity
            try:
                validate_identity(marker['release'])
            except ValueError:
                raise Refused('checkpoint_release_identity_refused', 409) from None
            expected = marker_for(body, config)
            if (marker['schema'] != expected['schema'] or marker['owner_id'] != expected['owner_id']
                    or marker['generation'] != expected['generation']
                    or marker['transaction_id'] != body['transaction']['base_commit']
                    or type(marker['epoch']) is not int or not 1 <= marker['epoch'] <= body['transaction']['epoch']):
                raise Refused('checkpoint_binding_refused', 409)
            exact(marker['identity'], {'principal', 'owner'})
            if marker['identity']['owner'] != owner_for(marker['identity']['principal']):
                raise Refused('checkpoint_binding_refused', 409)
        allowed = {'principals', 'retained', 'deployment-generation.json'}
        if set(path.name for path in staged.iterdir()) - allowed:
            raise Refused('unsafe_checkpoint_entry')
        prepare(root / 'expected-profile')
        principals = staged / 'principals'
        principals.mkdir(exist_ok=True, mode=0o700)
        partitions = list(principals.iterdir())
        if len(partitions) > 64:
            raise Refused('principal_capacity_exhausted', 507)
        for partition in partitions:
            if not partition.is_dir() or not re.fullmatch(r'[0-9a-f]{64}', partition.name):
                raise Refused('unsafe_checkpoint_entry')
            identity = strict_json(read_regular(partition / 'principal.json', 4096))
            exact(identity, {'principal', 'owner'})
            if (identity['owner'] != owner_for(identity['principal']) or
                    partition.name != sha256(identity['principal'].encode('ascii')).hexdigest()):
                raise Refused('checkpoint_binding_refused', 409)
            if set(path.name for path in partition.iterdir()) - {'profile', 'operator.json', 'principal.json'}:
                raise Refused('unsafe_checkpoint_entry')
            frozen_profile(partition, root / 'expected-profile')
            load_journal(partition)
        partition = principal_path(staged, body['identity'])
        resumed = partition.exists()
        if not resumed:
            if len(partitions) >= 64:
                raise Refused('principal_capacity_exhausted', 507)
            partition.mkdir(mode=0o700)
            shutil.copytree(root / 'expected-profile', partition / 'profile')
            (partition / 'principal.json').write_bytes(encoded({key: body['identity'][key] for key in ('principal', 'owner')}))
        journal = load_journal(partition)
        journal_capacity(journal, body)
        # mkdir(exist_ok=False) is the no-reuse fence, including failed writers.
        destination.parent.mkdir(mode=0o700)
        staged.rename(destination)
    return resumed, journal


def atomic_write(path, raw):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def checkpoint(directory):
    paths = sorted(directory.rglob('*'))
    if len(paths) > MAX_MEMBERS:
        raise Refused('expanded_checkpoint_budget_exceeded', 413)
    total = 0
    for path in paths:
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise Refused('unsafe_checkpoint_entry')
        total += info.st_size if stat.S_ISREG(info.st_mode) else 0
    if total > MAX_ARCHIVE:
        raise Refused('expanded_checkpoint_budget_exceeded', 413)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz', format=tarfile.USTAR_FORMAT) as archive:
        for path in paths:
            info = archive.gettarinfo(str(path), arcname=path.relative_to(directory).as_posix())
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            info.mode = 0o700 if info.isdir() else 0o600
            if info.isfile():
                with path.open('rb') as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
            if data.tell() > MAX_ARCHIVE:
                raise Refused('checkpoint_budget_exceeded', 413)
    if len(data.getvalue()) > MAX_ARCHIVE:
        raise Refused('checkpoint_budget_exceeded', 413)
    with tarfile.open(fileobj=io.BytesIO(data.getvalue()), mode='r:gz') as archive:
        validate_members(archive.getmembers())
    return base64.b64encode(data.getvalue()).decode('ascii')


class PrincipalBridge(MCPBridge):
    """Existing MCP actor, launched with immutable host-selected authority."""
    def __init__(self, config, principal, policy_path, directory):
        super().__init__(config)
        self.principal, self.policy_path, self.directory = principal, policy_path, directory

    async def _connect(self, stack):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        cfg = self.config
        args = ['-I', '-B', '-m', 'ciw.principal_mcp', '--profile', str(cfg.profile),
                '--policy', str(self.policy_path), '--principal', self.principal, '--instrument', 'builtin']
        if cfg.resume:
            args.append('--resume')
        # No ambient credential/provider/config environment reaches the child.
        child_env = {'NCP_RETAIN_DIR': str(self.directory / 'retained'),
                     'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                     'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
        # Raw child diagnostics can contain private paths/arguments. The durable
        # operator journal retains only closed reason codes and transaction IDs.
        errors = stack.enter_context(open(os.devnull, 'w'))
        read, write = await stack.enter_async_context(stdio_client(
            StdioServerParameters(command=str(cfg.python), args=args, env=child_env,
                                  cwd=str(self.directory)), errlog=errors))
        self.client = await stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=30))
        await self.client.discover()
        self.protocol = self.client.protocol_version
        await self._discover()
        # Grant checks live in the common facade. Console confirmation is an
        # additional user-intent check; its old static list is not a grant.
        self.read_tools = frozenset(name for name, tool in self.tools.items()
                                    if (tool.get('annotations') or {}).get('readOnlyHint') is True)
        self.write_tools = frozenset(self.tools) - self.read_tools
        self.connection = 'connected'
        return self.snapshot()

    async def _dispatch(self, action, payload):
        if action == 'resource':
            # The caller already used the shared authorize_resource method;
            # the MCP facade repeats it. Include the retained-resource family
            # that the older standalone console does not expose.
            return (await self.client.read_resource(payload['uri'])).model_dump(mode='json', by_alias=True)
        return await super()._dispatch(action, payload)


def mark_fence(body, outcome):
    identity = body['identity']
    key = identity['owner']
    LOCAL_FENCES.add(key)
    path = fence_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, encoded({'schema': 'odsp.private-local-fence.v1',
                               'transaction_id': body['transaction']['id'], 'outcome': outcome}))


def fenced(identity):
    root = STATE / 'fences'
    return bool(LOCAL_FENCES) or (root.is_dir() and any(root.iterdir()))


def audit(directory, journal, body, outcome):
    event = {'transaction_id': body['transaction']['id'], 'action': body['action'], 'outcome': outcome}
    if len(journal['events']) >= MAX_EVENTS:
        raise Refused('audit_capacity_exhausted', 507)
    journal['events'].append(event)
    raw = encoded(journal)
    if len(raw) > MAX_JOURNAL:
        raise Refused('audit_capacity_exhausted', 507)
    atomic_write(directory / 'operator.json', raw)


async def dispatch(bridge, body, config, principal, journal, ref_host):
    # Policy reload at each transport action, including resource/retained reads.
    validate_envelope(body, config)
    action, payload = body['action'], body['payload']
    if action == 'call' and payload['name'] in REFERENCE_NAMES:
        if ref_host is None:
            raise Refused('reference_not_configured', 409)
        result = config.policy.facade(principal, reference=ref_host).call(payload['name'], payload['arguments'])
        return {'structuredContent': result, 'isError': result.get('status') != 'ok'}
    if action == 'resource' and payload['uri'].startswith(('ncp://proto-corpus/', 'ncp://evidence/')):
        if ref_host is None:
            raise Refused('reference_not_configured', 409)
        value = config.policy.facade(principal, reference=ref_host).read_resource(payload['uri'])
        return {'contents': [{'uri': payload['uri'], 'mimeType': 'application/json', 'text': encoded(value).decode()}]}
    if action == 'events':
        return list(journal['events'])
    if action == 'runs':
        items = []
        for run_id in journal['runs']:
            config.policy.authorize(principal, 'ncp_run_get', {'run_id': run_id})
            raw = await bridge.request('call', {'name': 'ncp_run_get', 'arguments': {'run_id': run_id}})
            if raw.get('isError'):
                raise Refused('principal_policy_refused', 403)
            items.append(raw['structuredContent']['data'])
        return {'items': items, 'complete': True, 'scope': 'Runs observed for this principal through this host.'}
    result = await bridge.request(action, payload)
    if action == 'call' and ((result.get('structuredContent') or {}).get('error') or {}).get('code') == 'outcome_unknown':
        raise Uncertain()
    if action == 'tools' and ref_host is not None:
        result += config.policy.facade(principal, reference=ref_host).descriptions()
    if action == 'call' and payload['name'] == 'ncp_run_submit' and not result.get('isError'):
        data = (result.get('structuredContent') or {}).get('data') or {}
        async with asyncio.timeout(60):
            while data.get('state') in {'queued', 'running'}:
                await asyncio.sleep(.05)
                config.policy.authorize(principal, 'ncp_run_get', {'run_id': data['run_id']})
                result = await bridge.request('call', {'name': 'ncp_run_get', 'arguments': {'run_id': data['run_id']}})
                if result.get('isError'):
                    raise Uncertain()
                data = (result.get('structuredContent') or {}).get('data') or {}
    if action == 'call':
        data = (result.get('structuredContent') or {}).get('data') or {}
        if isinstance(data, dict) and isinstance(data.get('run_id'), str) and 'state' in data:
            if data['run_id'] not in journal['runs']:
                if len(journal['runs']) >= MAX_RUNS:
                    raise Refused('run_capacity_exhausted', 507)
                journal['runs'].append(data['run_id'])
    if action in {'status', 'diagnostics'}:
        # Return only transport metadata; the underlying console snapshot also
        # contains launch/profile information inappropriate to a remote client.
        result = {'connection': result['connection'], 'protocol': result['protocol'],
                  'known_runs': len(journal['runs']), 'transport': 'private HTTPS → stdio',
                  'authorizes_execution': False}
    return result


async def execute(body, config, principal, abandoned):
    try:
        await asyncio.wait_for(LOCK.acquire(), QUEUE_SECONDS)
    except TimeoutError:
        raise Refused('host_busy', 429) from None
    bridge = None
    started = False
    created = False
    journal = None
    directory = workspace_path(body)
    partition = principal_path(directory, body['identity'])
    try:
        if abandoned.is_set():
            raise Refused('request_abandoned', 409)
        validate_envelope(body, config)
        if fenced(body['identity']):
            raise Refused('outcome_unknown', 409)
        acknowledge_returned(body, config)
        ref_host = reference_host()
        if ((body['action'] == 'call' and body['payload']['name'] in REFERENCE_NAMES)
                or (body['action'] == 'resource' and body['payload']['uri'].startswith(('ncp://proto-corpus/', 'ncp://evidence/')))) and ref_host is None:
            raise Refused('reference_not_configured', 409)
        resumed, journal = restore(body['state'], body=body, config=config)
        created = True
        if abandoned.is_set():
            raise Refused('request_abandoned', 409)
        mark_fence(body, 'pending')
        started = True
        audit(partition, journal, body, 'started')
        bridge = PrincipalBridge(ConsoleConfig(Path(sys.executable).absolute(), partition / 'profile' / 'profile.json',
                                 resume=resumed, state_dir=partition / 'console'),
                                 principal, config.policy_path, directory)
        primary, cleanup = None, None
        try:
            await bridge.open()
            result = await dispatch(bridge, body, config, principal, journal, ref_host)
            if len(encoded(result)) > MAX_RESULT:
                raise Refused('result_budget_exceeded', 413)
        except BaseException as exc:
            primary = exc
        try:
            # Shield actor cleanup: a request cancellation cannot unlock a
            # still-running provider. Keep an explicit reference if it outlives
            # bounded cleanup; the principal fence continues to refuse work.
            closing = asyncio.create_task(bridge.close())
            await asyncio.wait_for(asyncio.shield(closing), CLOSE_SECONDS)
        except BaseException as exc:
            cleanup = exc
            UNCERTAIN_WORKERS[body['identity']['owner']] = (bridge, closing)
            closing.add_done_callback(consume)
        if primary is not None or cleanup is not None or abandoned.is_set():
            raise Uncertain(primary, cleanup) from primary
        config.policy.validate_context(body['identity'])
        audit(partition, journal, body, 'returned')
        atomic_write(directory / 'deployment-generation.json', encoded(marker_for(body, config)))
        archive = checkpoint(directory)
        result_body = {'data': result, 'raw_json': encoded(result).decode('utf-8')}
        if body['action'] == 'call' and body['payload']['name'] == 'ncp_plan_template' and not result.get('isError'):
            result_body['plan_arguments_json'] = encoded({'source': 'source', 'baseline': body['payload']['arguments']['baseline'],
                                                       'graph': result['structuredContent']['data']['graph']}).decode('utf-8')
        value = {'result': result_body, 'status': 200, 'state': archive, 'transaction': body['transaction'],
                 'identity': body['identity'],
                 'checkpoint_sha256': sha256(archive.encode('ascii')).hexdigest()}
        reply = response(value)
        if abandoned.is_set():
            raise Uncertain()
        receipt = returned_path(body['transaction']['id'])
        receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(receipt, encoded({
            'schema': 'odsp.private-returned-checkpoint.v1', 'transaction': body['transaction'],
            'checkpoint_sha256': value['checkpoint_sha256']}))
        fence_path(body['identity']).unlink()
        LOCAL_FENCES.discard(body['identity']['owner'])
        return reply
    except BaseException as exc:
        if started:
            try:
                mark_fence(body, 'unknown')
                if journal is not None and len(journal['events']) < MAX_EVENTS:
                    audit(partition, journal, body, 'unknown')
            except BaseException as sink_error:
                raise Uncertain(exc, sink_error) from exc
            if not isinstance(exc, Uncertain):
                raise Uncertain(exc) from exc
        elif created and directory.exists():
            # No actor was launched and no fence was written, so this new
            # disposable workspace cannot contain an in-flight writer.
            shutil.rmtree(directory.parent)
        if not started and isinstance(exc, OSError):
            raise Refused('working_state_refused', 503) from None
        raise
    finally:
        LOCK.release()


def consume(task):
    if not task.cancelled():
        task.exception()


async def transact(request: Request):
    global INFLIGHT
    if INFLIGHT >= MAX_INFLIGHT or len(ACTIVE) >= MAX_INFLIGHT:
        return refusal(Refused('host_busy', 429))
    INFLIGHT += 1
    try:
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            raise Refused('invalid_content_type', 415)
        raw = bytearray()
        async with asyncio.timeout(QUEUE_SECONDS):
            async for part in request.stream():
                if len(raw) + len(part) > MAX_REQUEST:
                    raise Refused('request_budget_exceeded', 413)
                raw.extend(part)
        body = strict_json(raw.decode('utf-8', errors='strict'))
        config = configuration()
        body, principal = validate_envelope(body, config)
        abandoned = asyncio.Event()
        task = asyncio.create_task(execute(body, config, principal, abandoned))
        ACTIVE.add(task)
        task.add_done_callback(ACTIVE.discard)
        task.add_done_callback(consume)
        async def disconnect():
            while True:
                message = await request.receive()
                if message['type'] == 'http.disconnect':
                    abandoned.set()
                    return
        disconnected = asyncio.create_task(disconnect())
        try:
            done, _ = await asyncio.wait({task, disconnected}, timeout=TRANSACTION_SECONDS,
                                         return_when=asyncio.FIRST_COMPLETED)
            if task in done and not abandoned.is_set():
                return task.result()
            abandoned.set()
            return refusal(Refused('outcome_unknown', 503))
        except (asyncio.CancelledError, TimeoutError):
            abandoned.set()
            # The shielded actor retains its child and fence. It is never
            # automatically rerun, even when the caller has disconnected.
            if isinstance(sys.exception(), asyncio.CancelledError):
                raise
            return refusal(Refused('outcome_unknown', 503))
        except Uncertain:
            return refusal(Refused('outcome_unknown', 503))
        finally:
            disconnected.cancel()
            try:
                await disconnected
            except asyncio.CancelledError:
                pass
    except Refused as exc:
        return refusal(exc)
    except (UnicodeError, ValueError):
        return refusal(Refused('invalid_request'))
    except TimeoutError:
        return refusal(Refused('request_timeout', 408))
    finally:
        INFLIGHT -= 1


async def health(request):
    value = {'live': True, 'ready': False, 'fenced': False, 'release': None,
             'owner_id': None, 'generation': None, 'checkpoint_generation': None}
    try:
        config = configuration()
        reference_host()
        local_fences = bool(LOCAL_FENCES) or ((STATE / 'fences').is_dir() and any((STATE / 'fences').iterdir()))
        value.update(ready=not local_fences, fenced=local_fences, release=config.release,
                     owner_id=config.owner_id, generation=config.generation, checkpoint_generation=config.generation)
    except (Refused, ImportError, OSError):
        pass
    return response(value, 200 if value['ready'] else 503)


app = Starlette(routes=[Route('/health', health), Route('/transaction', transact, methods=['POST'])])
