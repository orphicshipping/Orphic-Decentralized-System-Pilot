"""Host-selected native principal policy decisions, without Python fallback.

Python transports exact plain JSON and retains host lifecycle/scope checks. The
selected Rust binary validates policy, request schemas, operation/scientific
grants, typed constraints and resource grants. This is not a complete native
session engine, a proof, or an isolation boundary against a local administrator.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .adapters.subprocess import _bounded_process, _guard_cleanup
from .principal_policy import PrincipalPolicy, PrincipalRefusal, _read_policy, _specs
from .process_policy import ProcessPolicy
from .session import loads_json

MAX_INPUT = 2 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
MAX_BINARY = 16 * 1024 * 1024
SOURCE_FILES = ('rust_principal_policy.py', 'principal_policy.py', 'agent_tools.py',
    'agent_api.py', 'control_contracts.py', 'campaign_contracts.py', 'nise_contract.py',
    'domain_surface.py', 'workbench.py', 'fluid_contract.py', 'declared_workload.py',
    'control_gateway.py', 'knowledge_mcp.py', 'state_read_profile.py', 'logistics_profile.py',
    'recompiler_profile.py', 'schematic_profile.py', 'clocksync_profile.py', 'nise_profile.py',
    'offline_intake.py', 'proof_profile.py', 'domain_read_profile.py', 'mcp_toolbox.py',
    'live_intake.py', 'metrology_operations.py', 'adapters/subprocess.py', 'process_policy.py', 'session.py')
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _wire(value, maximum):
    """Transport types only: never validate schemas, grants or policy decisions.

    Reject tuples/subclasses/non-string object keys before the JSON encoder can
    turn them into different plain JSON. Native code owns semantic validation.
    """
    active = set()
    nodes = 0
    def visit(item, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 1048576:
            raise PrincipalRefusal()
        kind = type(item)
        if item is None or kind in (str, bool, int, float):
            return
        if kind not in (list, dict) or id(item) in active:
            raise PrincipalRefusal()
        active.add(id(item))
        try:
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise PrincipalRefusal()
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
        finally:
            active.remove(id(item))
    visit(value)
    parts, size = [], 0
    for part in json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False,
                                  separators=(',', ':')).iterencode(value):
        size += len(part)
        if size > maximum:
            raise PrincipalRefusal()
        parts.append(part)
    return ''.join(parts)


def _catalog():
    from .domain_surface import OPERATIONS as DOMAINS
    from .workbench import OPERATIONS as WORKBENCH
    from .fluid_contract import ALL_OPERATIONS as FLUID
    from .recompiler_profile import OPERATION as RECOMPILER
    from .schematic_profile import OPERATION as SCHEMATIC
    from .clocksync_profile import OPERATION as CLOCKSYNC
    from .nise_profile import OPERATION as NISE
    from .declared_workload import ASSESSMENT_POLICY
    fixed = {'ncp_demo': ['synthetic-oscillator-demo'], 'ncp_linear_preview': ['linear-response.library'],
        'ncp_polymer_cycle': ['data.assess-polymer-cycle.v1'],
        'ncp_toolpoint': ['data.review-toolpoint-containment.v1'], 'ncp_hypergraph': ['hypergraph_rewriting']}
    for names, operation in (
        (('ncp_recompiler_reconcile', 'ncp_recompiler_verify_receipt'), RECOMPILER),
        (('ncp_schematic_validate', 'ncp_schematic_retrieve', 'ncp_schematic_assess', 'ncp_schematic_verify_receipt'), SCHEMATIC),
        (('ncp_clocksync_reconcile', 'ncp_clocksync_replay'), CLOCKSYNC),
        (('ncp_nise_validate', 'ncp_nise_compile', 'ncp_nise_inspect', 'ncp_nise_verify'), NISE)):
        fixed.update((name, [operation]) for name in names)
    return {'schema': 'odsp.policy-catalog.v1',
        'specs': {name: row['inputSchema'] for name, row in _specs().items()},
        'direct': {'domain': [[r[0], r[1], r[2]] for r in DOMAINS],
                   'workbench': dict(WORKBENCH), 'fluid': {k: list(v) for k, v in FLUID.items()}, 'fixed': fixed},
        'assessment_policy': deepcopy(ASSESSMENT_POLICY)}


def _escaped_text(value):
    """Preserve literal surrogate code points separately from astral scalars.

    JSON alone maps both a Python surrogate pair and an astral scalar to the
    same escape sequence. This reversible inner-wire encoding escapes every
    literal tilde and every surrogate code point before ordinary JSON encoding.
    It changes neither canonical policy hashes nor strings passed to services.
    """
    return ''.join('~~' if c == '~' else f'~u{ord(c):04x};' if 0xd800 <= ord(c) <= 0xdfff else c
                   for c in value)


def _unescaped_text(value):
    output, offset = [], 0
    while offset < len(value):
        if value[offset] != '~':
            output.append(value[offset]); offset += 1
        elif value[offset:offset + 2] == '~~':
            output.append('~'); offset += 2
        else:
            part = value[offset:offset + 7]
            if (len(part) != 7 or not part.startswith('~u') or part[-1] != ';'
                    or not re.fullmatch('[0-9a-f]{4}', part[2:6])
                    or not 0xd800 <= int(part[2:6], 16) <= 0xdfff):
                raise PrincipalRefusal()
            output.append(chr(int(part[2:6], 16))); offset += 7
    return ''.join(output)


def _map_text(value, transform):
    if type(value) is str:
        return transform(value)
    if type(value) is list:
        return [_map_text(v, transform) for v in value]
    if type(value) is dict:
        result = {}
        for key, child in value.items():
            decoded = transform(key)
            if decoded in result:
                raise PrincipalRefusal()
            result[decoded] = _map_text(child, transform)
        return result
    return value


def _transport(value, maximum):
    _wire(value, maximum)  # Check original types, cycles, work and byte bounds first.
    return _wire(_map_text(value, _escaped_text), 2 * maximum)


def _sources():
    root = Path(__file__).parent
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


class RustPrincipalPolicy(PrincipalPolicy):
    """Host-only selection; requests cannot choose the executable or catalog.

    Every decision reads one fresh policy and executes the pinned native engine.
    A timeout, protocol failure, changed binding or native refusal denies. The
    constructor does not install itself globally or alter existing launchers.
    """
    def __init__(self, read_policy, *, binary, expected_sha256, process_policy):
        super().__init__(read_policy)
        self.binary = Path(binary)
        if (not self.binary.is_absolute() or '..' in self.binary.parts
                or type(expected_sha256) is not str or _HEX.fullmatch(expected_sha256) is None):
            raise ValueError('A host-selected absolute native executable and SHA-256 are required')
        if type(process_policy) is not ProcessPolicy:
            raise TypeError('A host-created ProcessPolicy is required')
        if (process_policy.wall_seconds > 10 or process_policy.cpu_seconds > 10
                or process_policy.memory_bytes > 1024**3 or process_policy.file_bytes > 1024*1024
                or process_policy.max_input_bytes > MAX_INPUT or process_policy.max_output_bytes > MAX_OUTPUT
                or not set(process_policy.environment) <= {'PATH', 'LANG', 'LC_ALL'}):
            raise ValueError('Native policy process limits exceed the supported contract')
        self.expected_sha256, self.process_policy = expected_sha256, process_policy
        self._cleanup_uncertain = None
        self._source_hashes = _sources()
        self._catalog_json = _transport(_catalog(), MAX_OUTPUT)
        self._catalog_sha256 = sha256(self._catalog_json.encode('ascii')).hexdigest()
        native = self._exchange('identity-v1', b'')
        if (type(native) is not dict or set(native) != {'schema', 'native_sources_sha256', 'rustc', 'target', 'profile'}
                or native['schema'] != 'odsp.rust-control-build.v1' or native['profile'] != 'release'
                or type(native['native_sources_sha256']) is not dict
                or not {'native/odsp-control-core/src/main.rs', 'native/odsp-control-core/src/policy.rs',
                    'native/odsp-control-core/src/json.rs', 'native/odsp-control-core/Cargo.lock'} <= native['native_sources_sha256'].keys()
                or any(type(v) is not str or _HEX.fullmatch(v) is None for v in native['native_sources_sha256'].values())):
            raise ValueError('Native policy build identity is invalid')
        if any(native['native_sources_sha256'].get('src/ciw/' + name) != checksum
               for name, checksum in self._source_hashes.items()):
            raise ValueError('Native policy was not built against the current policy and catalog sources')
        self._runtime = {'schema': 'ciw.rust-principal-policy-runtime.v1', 'binary_sha256': expected_sha256,
            'source_files_sha256': self._source_hashes, 'catalog_sha256': self._catalog_sha256,
            'native_build': native, 'process_policy': process_policy.identity(), 'proof_established': False}
        if self._decision('catalog', snapshot=None) != self._catalog_sha256:
            raise ValueError('Native policy catalog qualification failed')

    @classmethod
    def from_file(cls, path, **binding):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError('An absolute host policy path is required')
        return cls(lambda: _read_policy(path), **binding)

    def runtime_identity(self):
        try:
            self._bindings()
            self._binary()
            return deepcopy(self._runtime)
        except Exception:
            raise PrincipalRefusal() from None

    def _bindings(self):
        if (_sources() != self._source_hashes
                or _transport(_catalog(), MAX_OUTPUT) != self._catalog_json):
            raise PrincipalRefusal()

    def _binary(self):
        fd = os.open(self.binary, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111 or info.st_mode & 0o6000
                    or not 0 < info.st_size <= MAX_BINARY):
                raise PrincipalRefusal()
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(MAX_BINARY + 1)
            if len(raw) > MAX_BINARY or sha256(raw).hexdigest() != self.expected_sha256:
                raise PrincipalRefusal()
            return raw
        finally:
            os.close(fd)

    @_guard_cleanup
    def _exchange(self, mode, raw):
        if len(raw) > MAX_INPUT:
            raise PrincipalRefusal()
        self._bindings()
        binary = self._binary()
        with tempfile.TemporaryDirectory(prefix='odsp-native-policy-') as temporary:
            root = Path(temporary)
            executable = root / 'policy'
            executable.write_bytes(binary)
            executable.chmod(0o500)
            code, output = _bounded_process([str(executable), mode], cwd=root, stdin=raw,
                timeout=self.process_policy.wall_seconds, limit=MAX_OUTPUT, process_policy=self.process_policy)
            if code:
                raise PrincipalRefusal()
        self._bindings()
        return loads_json(output.decode('utf-8'))

    def _decision(self, operation, *, principal=None, name=None, arguments=None, uri=None, snapshot=...):
        try:
            with self.lock:
                value = self._reader() if snapshot is ... else snapshot
                policy_json = '' if operation == 'catalog' else _transport(value, 262144)
                raw = _wire({'schema': 'odsp.rust-policy.v1', 'text_encoding': 'odsp.surrogate-escape.v1', 'operation': operation,
                    'catalog_json': self._catalog_json, 'catalog_sha256': self._catalog_sha256,
                    'policy_json': policy_json, 'principal': principal, 'name': name,
                    'arguments_json': _transport(arguments, 65536), 'uri': uri}, MAX_INPUT).encode('ascii')
                reply = self._exchange('policy-v1', raw)
                fields = {'schema', 'status', 'input_sha256', 'result_sha256', 'result_json', 'proof_established'}
                if (type(reply) is not dict or set(reply) != fields or reply['schema'] != 'odsp.rust-policy.reply.v1'
                        or reply['status'] != 'ok' or reply['proof_established'] is not False
                        or reply['input_sha256'] != sha256(raw).hexdigest() or type(reply['result_json']) is not str
                        or reply['result_sha256'] != sha256(reply['result_json'].encode('ascii')).hexdigest()):
                    raise PrincipalRefusal()
                result = _map_text(loads_json(reply['result_json']), _unescaped_text)
                if _transport(result, 262144) != reply['result_json']:
                    raise PrincipalRefusal()
                if operation == 'catalog':
                    if type(result) is not str or result != self._catalog_sha256:
                        raise PrincipalRefusal()
                elif operation == 'snapshot':
                    if (type(result) is not dict or set(result) != {'schema', 'revision', 'principals'}
                            or result.get('schema') != 'ciw.principal-policy.v1'
                            or _transport(result, 262144) != policy_json):
                        raise PrincipalRefusal()
                elif operation in ('context', 'authorize_context'):
                    if (type(result) is not dict or set(result) != {'schema', 'principal', 'owner', 'policy_revision', 'policy_sha256'}
                            or result.get('schema') != 'ciw.principal-context.v1' or result.get('principal') != principal
                            or type(result.get('owner')) is not str or result['owner'] != 'principal:' + sha256(principal.encode('ascii')).hexdigest()
                            or result.get('policy_revision') != value['revision']
                            or result.get('policy_sha256') != 'sha256:' + sha256(_wire(value, 262144).encode('ascii')).hexdigest()):
                        raise PrincipalRefusal()
                elif (type(result) is not dict or set(result) != {'principal', 'enabled', 'operations',
                        'scientific_operations', 'resources', 'reference_scopes', 'handoff_recipients'}
                        or type(result.get('principal')) is not str or result.get('principal') != principal
                        or result.get('enabled') is not True
                        or any(type(result[key]) is not list for key in ('operations', 'scientific_operations',
                            'resources', 'reference_scopes', 'handoff_recipients'))):
                    raise PrincipalRefusal()
                return result
        except Exception:
            raise PrincipalRefusal() from None

    def snapshot(self):
        return self._decision('snapshot')

    def check_principal(self, principal):
        return self._decision('check_principal', principal=principal)

    def context(self, principal):
        return self._decision('context', principal=principal)

    def authorize(self, principal, name, arguments):
        return self._decision('authorize', principal=principal, name=name, arguments=arguments)

    def authorize_context(self, principal, name, arguments):
        return self._decision('authorize_context', principal=principal, name=name, arguments=arguments)

    def authorize_resource(self, principal, uri):
        return self._decision('authorize_resource', principal=principal, uri=uri)
