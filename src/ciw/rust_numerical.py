"""Host-selected Rust numerical execution; no automatic fallback or authorization.

The existing Python contracts validate scientific declarations and retained records.
The selected native executable computes mathematical results. Native commitments
bind transport bytes; they are separate from CIW content identities and are not proofs.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import re
import stat
import struct
import tempfile

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import _bounded_process, _guard_cleanup
from .core.identities import content_identity
from .core.records import finite_tree
from .numerical_contracts import (OPERATIONS, MAX_BYTES, parameters_request, validate_request,
                                  sample_axis, coefficient_axis, keys, matrix, vector)
from .numerical_runtime import SCOPE
from .process_policy import ProcessPolicy
from .session import loads_json

MAX_BINARY = 16 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
MAX_LIBRARY = 64 * 1024 * 1024
LAPACK_ABI = 'scipy-openblas-ilp64-v1'
ENVIRONMENT_KEYS = {'PATH', 'LANG', 'LC_ALL', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS'}
PYTHON_SOURCES = ('rust_numerical.py', 'numerical_contracts.py', 'numerical_workflow.py',
    'numerical_records.py', 'numerical_runtime.py', 'numerical_kernels.py', 'core/covariance.py',
    'core/identities.py', 'adapters/subprocess.py', 'process_policy.py', 'session.py')
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _digest(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _sources():
    root = Path(__file__).parent
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in PYTHON_SOURCES}


def validate_runtime(value, *, allow_absent=False):
    """Offline declaration validation does not require executing an old binary."""
    if value is None and allow_absent:
        return
    fields = {'provider', 'version', 'scope', 'source_files_sha256', 'native_sources_sha256',
              'environment', 'binary_sha256', 'process_policy', 'native_libraries_sha256', 'native_abi', 'native_configuration'}
    if (type(value) is not dict or set(value) != fields or value['provider'] != 'ciw.numerical.rust'
            or value['version'] != '1' or value['scope'] != SCOPE or not _digest(value['binary_sha256'])
            or value['native_abi'] != LAPACK_ABI):
        raise ValueError('Invalid Rust numerical runtime declaration')
    sources = value['source_files_sha256']
    if type(sources) is not dict or set(sources) != set(PYTHON_SOURCES) or not all(map(_digest, sources.values())):
        raise ValueError('Invalid Rust numerical Python boundary commitments')
    native = value['native_sources_sha256']
    required = {'native/odsp-core/Cargo.toml', 'native/odsp-core/Cargo.lock',
                'native/odsp-core/src/main.rs', 'native/odsp-core/src/lib.rs',
                'native/odsp-core/src/lapack.rs', 'native/odsp-core/build.rs', 'native/odsp-core/rust-toolchain.toml'}
    imported = 'instruments/execution/compute-runtime/crates/'
    required.add(imported+'Cargo.toml')
    required.update(imported+crate+'/'+file for crate in (
        'execution-core', 'execution-commitment', 'execution-serialization', 'execution-model',
        'execution-kernel', 'execution-trace', 'execution-native', 'execution-verification')
        for file in ('Cargo.toml', 'src/lib.rs'))
    required.update({imported+'execution-commitment/src/sha256.rs', imported+'execution-native/src/reference.rs'})
    if (type(native) is not dict or not required <= set(native) or not len(native) <= 512
            or any(type(k) is not str or len(k) > 256 or k.startswith('/') or '..' in k.split('/') for k in native)
            or not all(map(_digest, native.values()))):
        raise ValueError('Invalid Rust native source commitments')
    configuration = value['native_configuration']
    if (type(configuration) is not dict or set(configuration) != {'config', 'core', 'threads'}
            or type(configuration['config']) is not str or not 1 <= len(configuration['config']) <= 1024
            or 'USE64BITINT' not in configuration['config']
            or type(configuration['core']) is not str or not 1 <= len(configuration['core']) <= 128
            or type(configuration['threads']) is not int or configuration['threads'] != 1):
        raise ValueError('Invalid pinned LAPACK runtime configuration')
    libraries = value['native_libraries_sha256']
    if (type(libraries) is not dict or len(libraries) != 3
            or not all(type(k) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', k) for k in libraries)
            or not all(map(_digest, libraries.values()))
            or any(sum(k.startswith(prefix) for k in libraries) != 1
                   for prefix in ('libscipy_openblas64_', 'libgfortran-', 'libquadmath-'))):
        raise ValueError('Invalid pinned LAPACK library inventory')
    env = value['environment']
    if (type(env) is not dict or set(env) != {'python', 'numpy', 'rustc', 'target'}
            or any(type(v) is not str or not 1 <= len(v) <= 256 for v in env.values())):
        raise ValueError('Invalid Rust numerical environment')
    policy = value['process_policy']
    fields = {'schema', 'mode', 'policy_sha256', 'environment_keys', 'network_isolation_required',
        'filesystem_isolation_required', 'process_limit_scope', 'memory_limit_scope',
        'descendant_cleanup', 'wall_seconds', 'max_input_bytes', 'max_output_bytes',
        'cpu_seconds', 'memory_bytes', 'file_bytes', 'max_open_files', 'max_processes'}
    if (type(policy) is not dict or set(policy) != fields or policy['schema'] != 'ciw.process-policy.v1'
            or type(policy['mode']) is not str or policy['mode'] not in {'limits', 'bubblewrap'}
            or type(policy['policy_sha256']) is not str
            or not policy['policy_sha256'].startswith('sha256:')
            or not _digest(policy['policy_sha256'][7:])):
        raise ValueError('Invalid Rust process-policy declaration')
    for field in ('network_isolation_required', 'filesystem_isolation_required'):
        if policy[field] is not (policy['mode'] == 'bubblewrap'):
            raise ValueError('Invalid Rust process isolation declaration')
    for field, expected in {
        'process_limit_scope': 'Linux RLIMIT_NPROC per real UID; not a per-job cgroup',
        'memory_limit_scope': 'address space per process; not aggregate descendants',
        'descendant_cleanup': 'owned process group; escaped sessions are not contained in limits mode',
    }.items():
        if policy[field] != expected:
            raise ValueError('Invalid Rust process scope declaration')
    for field, low, high in (('cpu_seconds', 1, 30), ('memory_bytes', 32*1024*1024, 1024**3),
        ('max_input_bytes', 1, MAX_BYTES), ('max_output_bytes', 1, MAX_OUTPUT),
        ('file_bytes', 0, 1024*1024), ('max_open_files', 16, 4096), ('max_processes', 1, 4096)):
        if type(policy[field]) is not int or not low <= policy[field] <= high:
            raise ValueError('Invalid Rust process resource bound')
    wall = policy['wall_seconds']
    if type(wall) not in (int, float) or not math.isfinite(wall) or not 0 < wall <= 30:
        raise ValueError('Invalid Rust process time bound')
    keys = policy['environment_keys']
    if (type(keys) is not list or len(keys) > 64
            or any(type(k) is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', k) is None for k in keys)
            or keys != sorted(set(keys)) or not set(keys) <= ENVIRONMENT_KEYS
            or not {'PATH', 'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS'} <= set(keys)):
        raise ValueError('Invalid Rust process environment declaration')


def _pinned_bytes(path, expected, maximum, *, executable=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o6000
                or executable and not info.st_mode & 0o111 or not 0 < info.st_size <= maximum):
            raise ValueError('Unsafe pinned native file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum+1)
        if len(raw) > maximum or sha256(raw).hexdigest() != expected:
            raise AdapterRefusal('RUST_BINARY_CHANGED', 'Native executable or library differs from the selected pin')
        return raw
    finally:
        os.close(fd)


def _commit(tag, value):
    """Decode-check the existing SCR tagged byte commitment, not CIW JSON identity."""
    tag = tag.encode('utf-8')
    raw = struct.pack('<Q', len(tag)) + tag + struct.pack('<Q', 1) + struct.pack('<Q', len(value)) + value
    return sha256(raw).hexdigest()


def _request(operation, request):
    if operation.startswith('uncertainty.'):
        value = {'jacobian': request['jacobian'], 'covariance': request['covariance']['matrix']}
    elif operation == 'sensitivity.local.v1':
        value = {key: request[key] for key in ('jacobian', 'input_scales', 'output_scales')}
    elif operation == 'calibration.fit.v1':
        value = {key: request[key] for key in ('x', 'y', 'rcond')}
        value['covariance'] = request['observation_covariance']['matrix']
    elif operation == 'calibration.residuals.v1':
        value = {key: request[key] for key in ('x', 'y')}
        value.update(coefficients=request['coefficients']['reference_values'],
            coefficient_covariance=request['coefficients']['matrix'],
            observation_covariance=request['observation_covariance']['matrix'])
    elif operation == 'observability.rank.v1':
        value = {key: request[key] for key in ('transition', 'observation', 'state_scales', 'output_scales', 'horizon', 'rcond')}
    else:
        value = {key: request[key] for key in ('observed', 'predicted')}
        value['joint_covariance'] = request['joint_covariance']['matrix']
    return {'schema': 'odsp.rust-numerical.v1', 'request': {'operation': operation, 'input': value}}


def _record(operation, request, result):
    # Only metadata construction and existing shared validation happen in Python.
    from .numerical_kernels import artifact
    if type(result) is not dict:
        raise ValueError('Invalid native numerical result object')
    result = deepcopy(result)
    if operation.startswith('uncertainty.'):
        keys(result, {'covariance', 'standard_uncertainty'} | (
            {'diagonal_terms', 'cross_terms'} if operation == 'uncertainty.budget.v1' else set()))
        size = len(request['output']['quantity_ids'])
        matrix(result['covariance'], size, size)
        result['covariance'] = artifact(request, request['output'], result['covariance'],
            'first_order_supplied_jacobian', [request['covariance']])
        if operation == 'uncertainty.budget.v1':
            for term in result['cross_terms']:
                pair = term['pair']
                if (type(pair) is not list or len(pair) != 2 or any(type(i) is not int for i in pair)
                        or not 0 <= pair[0] < pair[1] < len(request['input']['quantity_ids'])):
                    raise ValueError('Native covariance pair is invalid')
                term['pair'] = [request['input']['quantity_ids'][i] for i in pair]
    elif operation == 'calibration.fit.v1':
        keys(result, {'coefficients', 'coefficient_covariance', 'predicted', 'residuals',
            'degrees_of_freedom', 'chi_square', 'condition_number', 'covariance_scale'})
        matrix(result['coefficient_covariance'], 2, 2)
        vector(result['coefficients'], 2)
        values = result['coefficients']
        result['coefficients'] = artifact(request, coefficient_axis(request, values),
            result.pop('coefficient_covariance'), 'affine_gls_known_absolute_covariance',
            [request['observation_covariance']], metadata={'training_sample_ids': request['sample_ids']})
    elif operation == 'calibration.residuals.v1':
        matrix(result.get('covariance'), len(request['sample_ids']), len(request['sample_ids']))
        result['covariance'] = artifact(request, sample_axis(request, result['residuals']), result['covariance'],
            'held_out_affine_independent_observations', [request['observation_covariance'], request['coefficients']])
    elif operation == 'model.discrepancy.v1':
        matrix(result.get('covariance'), len(request['sample_ids']), len(request['sample_ids']))
        axes = dict(quantity_ids=request['sample_ids'], units=[request['unit']]*len(request['sample_ids']),
                    reference_values=result['residuals'], frame=request['frame'])
        result['covariance'] = artifact(request, axes, result['covariance'],
            'observed_minus_predicted_full_joint_covariance', [request['joint_covariance']])
    return result


class RustNumericalBackend:
    """Trusted launcher binding. Paths, pins and policy are never model arguments.

    The supplied ProcessPolicy is mandatory. Limits mode is not a network/filesystem
    sandbox; select and qualify Bubblewrap if that isolation is required. Numerical
    code performs no network, child creation or disk writes. Every call executes a
    private byte-for-byte binary snapshot after pin validation; no PATH resolution.
    """
    def __init__(self, binary: Path, expected_sha256: str, *, process_policy: ProcessPolicy,
                 lapack_libraries: dict[Path, str]):
        self.binary = Path(binary)
        if not self.binary.is_absolute() or not _digest(expected_sha256):
            raise ValueError('An absolute native executable and exact SHA-256 are required')
        if not isinstance(process_policy, ProcessPolicy):
            raise TypeError('A host-created ProcessPolicy is required')
        if (process_policy.wall_seconds > 30 or process_policy.cpu_seconds > 30
                or process_policy.memory_bytes > 1024**3 or process_policy.max_input_bytes > MAX_BYTES
                or process_policy.max_output_bytes > MAX_OUTPUT or process_policy.file_bytes > 1024*1024):
            raise ValueError('Rust numerical process limits exceed the backend contract')
        if type(lapack_libraries) is not dict or len(lapack_libraries) != 3:
            raise ValueError('Exact host-pinned LAPACK and bundled dependencies are required')
        if not set(process_policy.environment) <= ENVIRONMENT_KEYS:
            raise ValueError('Native process environment contains an unsupported loader or tuning variable')
        self._libraries = tuple((Path(path), digest) for path, digest in lapack_libraries.items())
        if (any(not path.is_absolute() or not _digest(digest) for path, digest in self._libraries)
                or len({path.name for path, _ in self._libraries}) != 3):
            raise ValueError('Invalid LAPACK host binding')
        inventory = {path.name: digest for path, digest in self._libraries}
        for prefix in ('libscipy_openblas64_', 'libgfortran-', 'libquadmath-'):
            if sum(name.startswith(prefix) for name in inventory) != 1:
                raise ValueError('LAPACK binding must contain OpenBLAS, gfortran and quadmath')
        if any(re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name) is None for name in inventory):
            raise ValueError('Invalid LAPACK library basename')
        for path, digest in self._libraries:
            _pinned_bytes(path, digest, MAX_LIBRARY)
        if any(process_policy.environment.get(key) != '1' for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')):
            raise ValueError('Native linear algebra requires explicit single-thread policy')
        self.expected_sha256 = expected_sha256
        self.process_policy = process_policy
        self._python_sources = _sources()
        self._cleanup_uncertain = None
        identity = self._exchange('identity-v1', b'')
        if (type(identity) is not dict or set(identity) != {'schema', 'native_sources_sha256', 'rustc', 'target', 'profile', 'native_configuration'}
                or identity['schema'] != 'odsp.rust-numerical-build.v1' or identity['profile'] != 'release'):
            raise ValueError('Native build identity is invalid or not a release build')
        self._runtime = {'provider': 'ciw.numerical.rust', 'version': '1', 'scope': SCOPE,
            'source_files_sha256': self._python_sources, 'native_sources_sha256': identity['native_sources_sha256'],
            'environment': {'python': platform.python_version(), 'numpy': version('numpy'),
                            'rustc': identity['rustc'], 'target': identity['target']},
            'binary_sha256': expected_sha256, 'process_policy': process_policy.identity(),
            'native_abi': LAPACK_ABI, 'native_libraries_sha256': inventory,
            'native_configuration': identity['native_configuration']}
        validate_runtime(self._runtime)

    def runtime_identity(self):
        if _sources() != self._python_sources:
            raise AdapterRefusal('RUST_SOURCE_CHANGED', 'Rust numerical boundary changed after host binding')
        return deepcopy(self._runtime)

    @_guard_cleanup
    def _exchange(self, mode, raw):
        if len(raw) > MAX_BYTES:
            raise AdapterRefusal('RUST_INPUT_LIMIT', 'Native numerical input exceeds its byte budget')
        if _sources() != self._python_sources:
            raise AdapterRefusal('RUST_SOURCE_CHANGED', 'Rust numerical boundary changed after host binding')
        binary = _pinned_bytes(self.binary, self.expected_sha256, MAX_BINARY, executable=True)
        with tempfile.TemporaryDirectory(prefix='odsp-rust-numerical-') as temporary:
            root = Path(temporary)
            executable = root / 'numerical'
            executable.write_bytes(binary)
            executable.chmod(0o500)
            command = [str(executable), mode]
            if mode in {'compute-v1', 'identity-v1'}:
                for path, digest in self._libraries:
                    target = root / path.name
                    target.write_bytes(_pinned_bytes(path, digest, MAX_LIBRARY))
                    target.chmod(0o400)
                    if path.name.startswith('libscipy_openblas64_'):
                        command.append(str(target))
            code, output = _bounded_process(command, cwd=root, stdin=raw,
                timeout=self.process_policy.wall_seconds, limit=MAX_OUTPUT, process_policy=self.process_policy)
            if code:
                raise AdapterRefusal('RUST_PROCESS_EXIT', 'Native numerical process exited without a committed response')
        try:
            return loads_json(output.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise AdapterRefusal('RUST_PROTOCOL', 'Native numerical process returned an invalid response') from exc

    def calculate(self, operation, request):
        request = deepcopy(request)
        validate_request(operation, request, request.get('source_evidence_id'))
        raw = json.dumps(_request(operation, request), sort_keys=True, separators=(',', ':'),
                         allow_nan=False).encode('utf-8')
        reply = self._exchange('compute-v1', raw)
        if (type(reply) is not dict or reply.get('schema') != 'odsp.rust-numerical.reply.v1'
                or reply.get('proof_established') is not False):
            raise AdapterRefusal('RUST_PROTOCOL', 'Native numerical reply lost its protocol binding')
        if reply.get('status') == 'refused':
            if set(reply) != {'schema', 'status', 'reason', 'proof_established'} or type(reply['reason']) is not str:
                raise AdapterRefusal('RUST_PROTOCOL', 'Native numerical refusal shape is invalid')
            raise AdapterRefusal('numerical_domain_error', 'Native numerical computation refused: ' + reply['reason'][:128])
        if (set(reply) != {'schema', 'status', 'input_commitment', 'output_commitment', 'result_json', 'proof_established'}
                or reply['status'] != 'computed' or type(reply['result_json']) is not str
                or reply['input_commitment'] != _commit('scout.execution.input.v1', raw)
                or reply['output_commitment'] != _commit('scout.execution.output.v1', reply['result_json'].encode('utf-8'))):
            raise AdapterRefusal('RUST_PROTOCOL', 'Native numerical result commitments do not match the exchanged bytes')
        return _record(operation, request, loads_json(reply['result_json']))

    def execute(self, operation, run, parameters):
        from .numerical_records import validate_payload
        request = deepcopy(parameters_request(operation, run, parameters))
        result = self.calculate(operation, request)
        data = {'schema': 'ciw.numerical-result.v1', 'operation_id': operation,
                'request_digest': content_identity(request), 'source_evidence_id': run['evidence_id'],
                'scope': 'declared_model_only', 'result': result}
        finite_tree(data, 'Rust numerical result')
        validate_payload(operation, data, run, parameters, None)
        return data

    def operations(self):
        from .operations.registry import Operation
        return [Operation(name, 'calibration' if name == 'calibration.fit.v1' else 'backend',
            lambda run, parameters, op=name: self.execute(op, run, parameters), self.runtime_identity)
            for name in OPERATIONS]
