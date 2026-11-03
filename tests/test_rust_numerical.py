"""Actual native execution against independent oracles and the existing Python backend."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import numpy as np
import pytest

from ciw.adapters.protocol import AdapterRefusal
from ciw.instruments import make_demo_run
from ciw.process_policy import ProcessPolicy, executable_sha256
from ciw.rust_numerical import RustNumericalBackend, validate_runtime
from ciw.numerical_kernels import calculate as python_calculate
from test_builtin_numerical import NAMES, request_for, covariance, axis


@pytest.fixture(scope='module')
def binary():
    selected = os.environ.get('ODSP_RUST_NUMERICAL_BINARY')
    if selected is None:
        pytest.skip('Native Rust numerical qualification requires ODSP_RUST_NUMERICAL_BINARY')
    value = Path(selected)
    assert value.is_absolute() and value.is_file()
    return value


@pytest.fixture(scope='module')
def lapack_libraries():
    selected = os.environ.get('ODSP_RUST_LAPACK_DIR')
    if selected is None:
        pytest.skip('Native Rust qualification requires explicit ODSP_RUST_LAPACK_DIR')
    root = Path(selected)
    assert root.is_absolute()
    paths = [path for prefix in ('libscipy_openblas64_', 'libgfortran-', 'libquadmath-')
             for path in root.glob(prefix+'*')]
    assert len(paths) == 3
    return {path: sha256(path.read_bytes()).hexdigest() for path in paths}


@pytest.fixture
def backend(binary, lapack_libraries):
    policy = ProcessPolicy(expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'),
        wall_seconds=10, cpu_seconds=10, memory_bytes=256*1024*1024,
        max_input_bytes=512*1024, max_output_bytes=2*1024*1024, file_bytes=0)
    return RustNumericalBackend(binary, sha256(binary.read_bytes()).hexdigest(), process_policy=policy, lapack_libraries=lapack_libraries)


def comparable(value):
    """Result covariance identities may differ with last-bit native arithmetic."""
    if type(value) is dict:
        return {k: comparable(v) for k, v in value.items() if k != 'covariance_id'}
    if type(value) is list:
        return [comparable(v) for v in value]
    return value


def equivalent(a, b, *, rtol=1e-10, atol=1e-12):
    if type(a) is dict:
        assert set(a) == set(b)
        for key in a:
            equivalent(a[key], b[key], rtol=rtol, atol=atol)
    elif type(a) is list:
        assert len(a) == len(b)
        for av, bv in zip(a, b):
            equivalent(av, bv, rtol=rtol, atol=atol)
    elif type(a) in (int, float) and type(b) in (int, float):
        assert a == pytest.approx(b, rel=rtol, abs=atol), (a, b)
    else:
        assert a == b


def underflow_request(operation, run):
    request = request_for(operation, run)
    if operation == 'observability.rank.v1':
        request.update(transition=[[1e-200, 0.], [0., 1e-200]], observation=[[1e-200, 1e-200]])
    elif operation == 'calibration.residuals.v1':
        request['x'] = [1e-200, 2e-200, 3e-200]
        request['coefficients'] = covariance(axis(['offset', 'slope'], ['V', 'V/count'], [0., 1e-200], 'sensor'),
            [[0., 0.], [0., 0.]], run['evidence_id'], {'training_sample_ids': ['train0', 'train1']})
    elif operation == 'calibration.fit.v1':
        request['y'] = [1e-200, 3e-200, 6e-200]
        request['observation_covariance'] = covariance(axis(request['sample_ids'], ['V']*3, request['y'], 'sensor'),
            np.eye(3).tolist(), run['evidence_id'])
    else:
        request.update(observed=[1e-200, 2e-200], predicted=[0., 0.])
        request['joint_covariance'] = covariance(axis(
            ['observed:s0', 'observed:s1', 'predicted:s0', 'predicted:s1'], ['m']*4,
            request['observed']+request['predicted'], 'sensor'), np.eye(4).tolist(), run['evidence_id'])
    return request


@pytest.mark.parametrize('operation', [NAMES[2], NAMES[3], NAMES[5], NAMES[6]])
def test_blas_underflow_preserves_python_refusal(backend, operation):
    # These four real cases previously returned plausible finite native output.
    # The oracle refuses the underflow inside a BLAS reduction, including zero.
    from ciw.numerical_workflow import execute
    run = make_demo_run()
    request = underflow_request(operation, run)
    with pytest.raises(AdapterRefusal):
        execute(operation, run, {'request': request})
    with pytest.raises(AdapterRefusal):
        backend.execute(operation, run, {'request': request})


def test_gls_rank_decision_at_declared_threshold(backend):
    from ciw.numerical_workflow import execute
    run = make_demo_run()
    for scale in np.geomspace(1e4, 1e13, 10):
        request = request_for(NAMES[2], run)
        request['x'] = [-1., 0., 1.]
        matrix = np.diag([1., scale*scale, scale*scale])
        request['observation_covariance'] = covariance(axis(request['sample_ids'], ['V']*3, request['y'], 'sensor'),
            matrix.tolist(), run['evidence_id'])
        singular = np.linalg.svd(np.column_stack((np.ones(3), request['x']))/np.sqrt(np.diag(matrix))[:, None], compute_uv=False)
        cutoff = singular[-1]/singular[0]
        for rcond in (np.nextafter(cutoff, 0.), cutoff, np.nextafter(cutoff, 1.)):
            request['rcond'] = float(rcond)
            try:
                expected = execute(NAMES[2], run, {'request': request})['result']
            except (ValueError, AdapterRefusal):
                with pytest.raises(AdapterRefusal):
                    backend.execute(NAMES[2], run, {'request': request})
            else:
                actual = backend.execute(NAMES[2], run, {'request': request})['result']
                equivalent(comparable(actual), comparable(expected))


def test_maximum_calibration_and_discrepancy_dimensions(backend):
    run = make_demo_run()
    request = request_for(NAMES[2], run)
    n = 256
    request.update(x=np.linspace(-1., 2., n).tolist(),
        y=(2.+3.*np.linspace(-1., 2., n)+.1*np.sin(np.arange(n))).tolist(),
        sample_ids=[f's{i}' for i in range(n)])
    request['observation_covariance'] = covariance(axis(request['sample_ids'], ['V']*n, request['y'], 'sensor'),
        (np.eye(n)+.01*np.ones((n, n))).tolist(), run['evidence_id'])
    actual = backend.execute(NAMES[2], run, {'request': request})['result']
    equivalent(comparable(actual), comparable(python_calculate(NAMES[2], request)))
    n = 128
    request = request_for(NAMES[6], run)
    request.update(sample_ids=[f's{i}' for i in range(n)], observed=np.linspace(0., 3., n).tolist(), predicted=[0.]*n)
    request['joint_covariance'] = covariance(axis(
        [f'{prefix}:s{i}' for prefix in ('observed', 'predicted') for i in range(n)], ['m']*(2*n),
        request['observed']+request['predicted'], 'sensor'), np.eye(2*n).tolist(), run['evidence_id'])
    actual = backend.execute(NAMES[6], run, {'request': request})['result']
    equivalent(comparable(actual), comparable(python_calculate(NAMES[6], request)))


def test_loader_environment_cannot_replace_pinned_dependencies(binary, lapack_libraries):
    policy = ProcessPolicy(expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'),
        wall_seconds=10, cpu_seconds=10, memory_bytes=256*1024*1024,
        file_bytes=0, environment={'PATH': '/usr/bin:/bin', 'OPENBLAS_NUM_THREADS': '1',
            'OMP_NUM_THREADS': '1', 'LD_LIBRARY_PATH': '/tmp/unpinned'})
    with patch('ciw.rust_numerical._bounded_process', side_effect=AssertionError('Child must not launch')):
        with pytest.raises(ValueError, match='environment'):
            RustNumericalBackend(binary, sha256(binary.read_bytes()).hexdigest(),
                process_policy=policy, lapack_libraries=lapack_libraries)


@pytest.mark.parametrize('operation', NAMES)
def test_native_all_seven_execute_without_python_compute(backend, operation):
    run = make_demo_run()
    request = request_for(operation, run)
    expected = python_calculate(operation, request)
    before = deepcopy(request)
    with patch('ciw.numerical_kernels.calculate', side_effect=AssertionError('Python fallback')):
        actual = backend.execute(operation, run, {'request': request})['result']
    equivalent(comparable(actual), comparable(expected))
    assert request == before
    identity = backend.runtime_identity()
    assert identity['provider'] == 'ciw.numerical.rust'
    assert identity['native_sources_sha256']['native/odsp-core/Cargo.lock']
    validate_runtime(identity)


def test_native_propagation_integer_decimal_oracle(backend):
    run = make_demo_run()
    rng = np.random.default_rng(20261103)
    for _ in range(40):
        n, m = int(rng.integers(1, 6)), int(rng.integers(1, 5))
        b = rng.integers(-8, 9, size=(n, n))
        c = (b@b.T).tolist()
        j = rng.integers(-8, 9, size=(m, n)).tolist()
        request = request_for(NAMES[0], run)
        request.update(input=axis([f'i{i}' for i in range(n)], ['m']*n, [0.]*n),
            output=axis([f'o{i}' for i in range(m)], ['N']*m, [0.]*m), jacobian=j)
        request['covariance'] = covariance(request['input'], c, run['evidence_id'])
        expected = [[float(sum(Decimal(j[r][i])*Decimal(c[i][k])*Decimal(j[s][k])
            for i in range(n) for k in range(n))) for s in range(m)] for r in range(m)]
        for operation in NAMES[:2]:
            actual = backend.execute(operation, run, {'request': request})['result']
            np.testing.assert_array_equal(actual['covariance']['matrix'], expected)
            equivalent(comparable(actual), comparable(python_calculate(operation, request)))


def test_native_gls_against_independent_normal_equations(backend):
    rng = np.random.default_rng(19841217)
    run = make_demo_run()
    for n in (3, 4, 10, 24, 64):
        x = np.linspace(-3., 7., n)
        y = 1.25-2.5*x+rng.normal(0., .1, n)
        b = rng.normal(0., .1, (n, n))
        c = np.eye(n)+b@b.T
        request = request_for(NAMES[2], run)
        request.update(x=x.tolist(), y=y.tolist(), sample_ids=[f's{i}' for i in range(n)])
        request['observation_covariance'] = covariance(axis(request['sample_ids'], ['V']*n, request['y'], 'sensor'), c.tolist(), run['evidence_id'])
        h = np.column_stack((np.ones(n), x))
        precision = np.linalg.inv(c)
        coefficient_covariance = np.linalg.inv(h.T@precision@h)
        coefficients = coefficient_covariance@h.T@precision@y
        actual = backend.execute(NAMES[2], run, {'request': request})['result']
        np.testing.assert_allclose(actual['coefficients']['reference_values'], coefficients, rtol=1e-11, atol=1e-12)
        np.testing.assert_allclose(actual['coefficients']['matrix'], coefficient_covariance, rtol=1e-11, atol=1e-12)
        equivalent(comparable(actual), comparable(python_calculate(NAMES[2], request)))


def test_native_observability_rectangular_zero_and_scaled(backend):
    run = make_demo_run()
    rng = np.random.default_rng(987101)
    for n, m, horizon in ((1, 1, 1), (4, 2, 1), (3, 2, 4), (8, 3, 8), (32, 1, 4)):
        request = request_for(NAMES[5], run)
        request.update(states=axis([f'x{i}' for i in range(n)], ['m']*n, [0.]*n),
            outputs=axis([f'y{i}' for i in range(m)], ['m']*m, [0.]*m),
            transition=(rng.normal(size=(n, n))/n).tolist(), observation=rng.normal(size=(m, n)).tolist(),
            state_scales=np.geomspace(.5, 3., n).tolist(), output_scales=np.geomspace(.5, 3., m).tolist(), horizon=horizon)
        actual = backend.execute(NAMES[5], run, {'request': request})['result']
        expected = python_calculate(NAMES[5], request)
        assert actual['rank'] == expected['rank']
        equivalent(actual, expected)
        request['observation'] = [[0.]*n for _ in range(m)]
        actual = backend.execute(NAMES[5], run, {'request': request})['result']
        assert actual['rank'] == 0 and actual['condition_number'] is None


def test_native_float_covariances_and_discrepancy(backend):
    run = make_demo_run()
    rng = np.random.default_rng(17103)
    for _ in range(20):
        request = request_for(NAMES[6], run)
        b = rng.normal(0., 2., (4, 4)); c = b@b.T
        request['joint_covariance'] = covariance(axis(
            ['observed:s0','observed:s1','predicted:s0','predicted:s1'], ['m']*4, [2.,4.,1.,2.], 'sensor'), c.tolist(), run['evidence_id'])
        actual = backend.execute(NAMES[6], run, {'request': request})['result']
        equivalent(comparable(actual), comparable(python_calculate(NAMES[6], request)))


def test_native_extreme_products_and_singular_uncertainty(backend):
    run = make_demo_run()
    request = request_for(NAMES[0], run)
    request.update(input=axis(['a'], ['m'], [0.]), output=axis(['u'], ['m'], [0.]))
    for j, c in ((1e200, 1e-300), (1e-200, 1e300), (1e-100, 1e-100)):
        request['jacobian'] = [[j]]
        request['covariance'] = covariance(request['input'], [[c]], run['evidence_id'])
        actual = backend.execute(NAMES[0], run, {'request': request})['result']
        equivalent(comparable(actual), comparable(python_calculate(NAMES[0], request)), atol=0.)
    request['jacobian'] = [[1e-200]]
    request['covariance'] = covariance(request['input'], [[1.]], run['evidence_id'])
    with pytest.raises(AdapterRefusal):
        backend.execute(NAMES[0], run, {'request': request})
    request = request_for(NAMES[0], run)
    request['jacobian'] = [[1., 1.]]
    request['covariance'] = covariance(request['input'], [[1., -1.], [-1., 1.]], run['evidence_id'])
    assert backend.execute(NAMES[0], run, {'request': request})['result']['standard_uncertainty'] == [0.]


@pytest.mark.parametrize('operation', NAMES)
@pytest.mark.parametrize('attack', ['unknown_field', 'wrong_evidence', 'missing_assumptions'])
def test_semantic_refusals_do_not_start_native_process(backend, operation, attack):
    run = make_demo_run()
    request = request_for(operation, run)
    if attack == 'unknown_field': request['exec'] = 'no'
    elif attack == 'wrong_evidence': request['source_evidence_id'] = 'sha256:'+'0'*64
    else: request.pop('assumptions')
    with patch.object(backend, '_exchange', side_effect=AssertionError('Invalid contract reached native process')):
        with pytest.raises(ValueError):
            backend.execute(operation, run, {'request': request})


def test_native_direct_protocol_strictness(binary, lapack_libraries):
    valid = {'schema':'odsp.rust-numerical.v1', 'request':{'operation':'sensitivity.local.v1',
        'input':{'jacobian':[[1.]], 'input_scales':[1.], 'output_scales':[1.]}}}
    raw = json.dumps(valid).encode()
    cases = [b'', b'[]', b'NaN', b'\xff', raw[:-1], b' '* (512*1024+1),
             raw.replace(b'1.0', b'true', 1), raw.replace(b'1.0', b'1e999', 1),
             raw.replace(b'"jacobian":', b'"jacobian":[],"jacobian":'),
             raw.replace(b'"input_scales":', b'"unknown":0,"input_scales":')]
    for value in cases:
        completed = subprocess.run([str(binary), 'compute-v1', str(next(p for p in lapack_libraries if p.name.startswith('libscipy_openblas64_')))], input=value, env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1'), capture_output=True, timeout=5, check=True)
        reply = json.loads(completed.stdout)
        assert reply['status'] == 'refused', (value[:100], reply)
        assert reply['proof_established'] is False and len(completed.stdout) < 4096


def test_pins_and_commitments_refuse_without_fallback(backend, binary, tmp_path):
    run = make_demo_run(); request = request_for(NAMES[0], run)
    copied = tmp_path/'copied'; copied.write_bytes(binary.read_bytes()); copied.chmod(0o500)
    bound = RustNumericalBackend(copied, backend.expected_sha256, process_policy=backend.process_policy, lapack_libraries=dict(backend._libraries))
    copied.chmod(0o700); copied.write_bytes(b'changed executable')
    with pytest.raises(AdapterRefusal, match='selected pin'):
        bound.execute(NAMES[0], run, {'request': request})
    with patch.object(backend, '_exchange', return_value={'schema':'odsp.rust-numerical.reply.v1',
        'status':'computed','proof_established':False,'input_commitment':'0'*64,
        'output_commitment':'0'*64,'result_json':'{}'}):
        with pytest.raises(AdapterRefusal, match='commitments'):
            backend.execute(NAMES[0], run, {'request': request})


def test_runtime_cannot_lose_native_identity(backend):
    value = backend.runtime_identity()
    for field in ('binary_sha256', 'native_sources_sha256', 'process_policy'):
        bad = deepcopy(value); bad.pop(field)
        with pytest.raises(ValueError): validate_runtime(bad)
    value['native_sources_sha256']['native/odsp-core/Cargo.lock'] = 'not-a-digest'
    with pytest.raises(ValueError): validate_runtime(value)


@pytest.mark.parametrize('operation', NAMES)
def test_native_session_retains_distinct_occurrences_and_reopens_offline(backend, operation, tmp_path):
    from ciw.operations.registry import OperationRegistry
    from ciw.numerical_workflow import operations
    from ciw.session import Session
    registry = OperationRegistry()
    for item in operations(backend=backend): registry.register(item)
    run = make_demo_run()
    session = Session(run, tmp_path/'session', operations=registry)
    message = {'protocol_version':1,'request_id':'native-test','type':'operation.execute',
        'payload':{'operation_id':operation,'parameters':{'request':request_for(operation,run)}}}
    one = session.handle(message)['payload']
    two = session.handle(message)['payload']
    assert one['status'] == two['status'] == 'completed', (one, two)
    assert one['result']['runtime']['provider'] == 'ciw.numerical.rust'
    assert one['result']['result_id'] != two['result']['result_id']
    assert one['result']['data'] == two['result']['data']
    saved = session.save_workspace(tmp_path/'saved.json')
    with patch.object(backend, '_exchange', side_effect=AssertionError('Offline native replay')):
        restored = Session.from_workspace(saved, tmp_path/'restored')
    assert restored.results == session.results


def test_backend_binding_cannot_come_from_a_fake_object(backend):
    from ciw.numerical_workflow import operations, execute
    class Fake:
        def operations(self): raise AssertionError('fake selected')
        def execute(self, *a): raise AssertionError('fake selected')
    with pytest.raises(TypeError): operations(backend=Fake())
    with pytest.raises(TypeError): execute(NAMES[0], {}, {}, backend=Fake())


@pytest.mark.parametrize('bad', [None, [], {}, True, {'unexpected':'value'}])
def test_malformed_computed_child_data_refuses(backend, bad):
    from ciw.rust_numerical import _commit
    run = make_demo_run(); request = request_for(NAMES[0], run)
    def reply(mode, raw):
        value = json.dumps(bad)
        return {'schema':'odsp.rust-numerical.reply.v1','status':'computed','proof_established':False,
                'input_commitment':_commit('scout.execution.input.v1', raw),
                'output_commitment':_commit('scout.execution.output.v1', value.encode()), 'result_json':value}
    with patch.object(backend, '_exchange', side_effect=reply):
        with pytest.raises((ValueError, KeyError)):
            backend.execute(NAMES[0], run, {'request':request})


@pytest.mark.parametrize('operation', [NAMES[0], NAMES[1], NAMES[2], NAMES[3], NAMES[6]])
@pytest.mark.parametrize('bad_scalar', [True, '1.0'])
def test_raw_child_covariance_cannot_be_coerced_before_validation(backend, operation, bad_scalar):
    from ciw.rust_numerical import _commit, _request
    run = make_demo_run()
    request = request_for(operation, run)
    raw = json.dumps(_request(operation, request), sort_keys=True, separators=(',', ':')).encode()
    original = backend._exchange('compute-v1', raw)
    result = json.loads(original['result_json'])
    covariance_key = 'coefficient_covariance' if operation == NAMES[2] else 'covariance'
    result[covariance_key][0][0] = bad_scalar
    encoded = json.dumps(result)
    def reply(mode, request_bytes):
        return dict(original, result_json=encoded,
            input_commitment=_commit('scout.execution.input.v1', request_bytes),
            output_commitment=_commit('scout.execution.output.v1', encoded.encode()))
    with patch.object(backend, '_exchange', side_effect=reply):
        with pytest.raises(ValueError, match='number'):
            backend.execute(operation, run, {'request': request})


def test_mixed_boolean_covariance_with_consistent_uncertainty_refuses(backend):
    # Exact review reproduction: np.asarray would silently turn this mixed
    # boolean/float matrix into numeric identity, unlike a homogeneous bool array.
    from ciw.rust_numerical import _commit
    run = make_demo_run()
    request = request_for(NAMES[0], run)
    request['output'] = axis(['u', 'v'], ['m', 's'], [2., 3.])
    request['jacobian'] = [[1., 0.], [0., 1.]]
    encoded = json.dumps({'covariance': [[True, 0.], [0., 1.]],
                          'standard_uncertainty': [1., 1.]})
    def reply(mode, raw):
        return {'schema': 'odsp.rust-numerical.reply.v1', 'status': 'computed',
                'proof_established': False, 'result_json': encoded,
                'input_commitment': _commit('scout.execution.input.v1', raw),
                'output_commitment': _commit('scout.execution.output.v1', encoded.encode())}
    with patch.object(backend, '_exchange', side_effect=reply):
        with pytest.raises(ValueError, match='number'):
            backend.execute(NAMES[0], run, {'request': request})


def test_real_child_output_limit_and_timeout_are_refusals(backend, binary, tmp_path):
    import sys
    from dataclasses import replace
    identity = subprocess.run([str(binary),'identity-v1',str(next(p for p,_ in backend._libraries if p.name.startswith('libscipy_openblas64_')))],env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1'),capture_output=True,check=True).stdout.decode()
    run = make_demo_run(); request = request_for(NAMES[0],run)
    for kind, body in [('output','os.write(1,b"x"*(3*1024*1024))'), ('timeout','time.sleep(5)')]:
        executable=tmp_path/kind
        executable.write_text('#!'+sys.executable+'\nimport os,sys,time\n'
            +'if sys.argv[1]=="identity-v1":\n print('+repr(identity)+')\nelse:\n '+body+'\n')
        executable.chmod(0o500)
        policy = replace(backend.process_policy, wall_seconds=.5)
        bound = RustNumericalBackend(executable,sha256(executable.read_bytes()).hexdigest(),process_policy=policy, lapack_libraries=dict(backend._libraries))
        with pytest.raises(AdapterRefusal):
            bound.execute(NAMES[0],run,{'request':request})


def test_native_svd_rank_cutoff_matches_reference_without_reclassification(backend):
    # Reproduces the 87/400 disagreements found in the rejected nalgebra SVD.
    # A cutoff is an exact declared comparison; relative closeness is insufficient.
    rng = np.random.default_rng(71)
    run = make_demo_run()
    for n in (2, 3, 4, 8):
        for _ in range(20):
            u, _ = np.linalg.qr(rng.normal(size=(n, n)))
            v, _ = np.linalg.qr(rng.normal(size=(n, n)))
            for deviation in (-1e-3, -1e-5, 0., 1e-5, 1e-3):
                singular = np.geomspace(1., 1e-12, n)
                singular[-1] *= 1+deviation
                observation = u@np.diag(singular)@v.T
                request = request_for(NAMES[5], run)
                request.update(states=axis([f'x{i}' for i in range(n)], ['m']*n, [0.]*n),
                    outputs=axis([f'y{i}' for i in range(n)], ['m']*n, [0.]*n),
                    transition=np.eye(n).tolist(), observation=observation.tolist(),
                    state_scales=[1.]*n, output_scales=[1.]*n, horizon=1)
                expected = python_calculate(NAMES[5], request)
                actual = backend.execute(NAMES[5], run, {'request':request})['result']
                assert actual['rank'] == expected['rank'], (n, deviation, actual, expected)
                assert actual['singular_values'] == expected['singular_values']
                assert actual['threshold'] == expected['threshold']


def test_native_finite_horizon_matrix_reduction_matches_reference(backend):
    rng = np.random.default_rng(71107)
    run = make_demo_run()
    for n, m, horizon in ((1,1,32), (4,1,16), (4,2,8), (8,7,16), (32,32,32)):
        request = request_for(NAMES[5], run)
        request.update(states=axis([f'x{i}' for i in range(n)], ['m']*n, [0.]*n),
            outputs=axis([f'y{i}' for i in range(m)], ['m']*m, [0.]*m),
            transition=(rng.normal(size=(n,n))/n).tolist(), observation=rng.normal(size=(m,n)).tolist(),
            state_scales=[1.]*n, output_scales=[1.]*m, horizon=horizon)
        expected = python_calculate(NAMES[5], request)
        actual = backend.execute(NAMES[5], run, {'request':request})['result']
        assert actual['observability_matrix'] == expected['observability_matrix']
        assert actual['rank'] == expected['rank']
        assert actual['singular_values'] == expected['singular_values']


def test_native_runtime_policy_rejects_aliases_and_false_bounds(backend):
    for field, bad in [('cpu_seconds', True), ('wall_seconds', float('inf')),
        ('max_output_bytes', 2*1024*1024+1), ('network_isolation_required', 0),
        ('descendant_cleanup', 'all descendants proved contained'), ('environment_keys', ['PATH','PATH'])]:
        value = backend.runtime_identity(); value['process_policy'][field] = bad
        with pytest.raises(ValueError): validate_runtime(value)
    value = backend.runtime_identity(); value['native_configuration']['threads'] = True
    with pytest.raises(ValueError): validate_runtime(value)
    for source in ('native/odsp-core/src/lapack.rs', 'native/odsp-core/rust-toolchain.toml',
            'instruments/execution/compute-runtime/crates/execution-commitment/src/sha256.rs'):
        value = backend.runtime_identity(); value['native_sources_sha256'].pop(source)
        with pytest.raises(ValueError): validate_runtime(value)
    value = backend.runtime_identity(); value['native_libraries_sha256'].pop(next(iter(value['native_libraries_sha256'])))
    with pytest.raises(ValueError): validate_runtime(value)


def test_changed_lapack_dependency_refuses_before_native_call(backend, tmp_path):
    libraries = {}
    for source, digest in backend._libraries:
        target = tmp_path/source.name; target.write_bytes(source.read_bytes())
        libraries[target] = digest
    bound = RustNumericalBackend(backend.binary, backend.expected_sha256,
        process_policy=backend.process_policy, lapack_libraries=libraries)
    target = next(p for p in libraries if p.name.startswith('libquadmath-'))
    target.write_bytes(b'changed pinned dependency')
    run = make_demo_run()
    with patch('ciw.rust_numerical._bounded_process', side_effect=AssertionError('Changed dependency launched')):
        with pytest.raises(AdapterRefusal, match='selected pin'):
            bound.execute(NAMES[5], run, {'request':request_for(NAMES[5], run)})


def test_native_largest_declared_uncertainty_axes(backend):
    run = make_demo_run(); n = 32
    request = request_for(NAMES[0], run)
    request.update(input=axis([f'i{i}' for i in range(n)], ['m']*n, [0.]*n),
        output=axis([f'o{i}' for i in range(n)], ['m']*n, [0.]*n), jacobian=np.eye(n).tolist())
    request['covariance'] = covariance(request['input'], np.eye(n).tolist(), run['evidence_id'])
    for name in NAMES[:2]:
        actual = backend.execute(name, run, {'request':request})['result']
        equivalent(comparable(actual), comparable(python_calculate(name, request)))
