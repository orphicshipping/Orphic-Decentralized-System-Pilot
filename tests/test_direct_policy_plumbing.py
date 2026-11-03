"""Host-policy wiring and refusals; adapter doubles do not qualify companion execution."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ciw import covariance_workflow, geodesic, investigation
from ciw.process_policy import ProcessPolicy, ProcessPolicyHeld
from companion_policy_helpers import test_policy as strict_test_policy


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repositories(tmp_path):
    paths = {role: tmp_path / role for role in ('rci', 'fsrt', 'jspt', 'gte')}
    for path in paths.values():
        path.mkdir()
    return paths


@pytest.fixture
def policies(repositories):
    # Explicit test fixture pins. No namespace or provider launch is mocked here;
    # these tests stop at the adapter constructor and claim wiring only.
    return {role: strict_test_policy(path) for role, path in repositories.items()}


class RuntimeDouble:
    def __init__(self, identity=None):
        self.identity = identity or {}

    def runtime_identity(self):
        return deepcopy(self.identity)

    def invoke(self, *args):
        return {'fixture_measurement': True}


def runtime_record(role, policy):
    spec = json.loads((ROOT / 'src/ciw/adapter-runtimes.json').read_text())[role]
    return {'revision': spec['revision'], 'module': spec['module'],
            'python_sha256': '0' * 64, 'python_version': '3.12.0',
            'dependencies': {}, 'process_policy': policy.identity()}


@pytest.mark.parametrize('role', ('rci', 'fsrt', 'jspt', 'gte'))
def test_runtime_passes_host_policy_and_retained_runtime_pins(monkeypatch, repositories, policies, role):
    calls = []
    monkeypatch.setattr(investigation, 'PinnedSubprocessAdapter',
                        lambda *args, **kwargs: calls.append((args, kwargs)) or RuntimeDouble())
    expected = runtime_record(role, policies[role])
    investigation._runtime(role, repositories[role], '/host/python', expected,
                           process_policy=policies[role])
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == repositories[role]
    assert kwargs['process_policy'] is policies[role]
    assert kwargs['python_executable'] == '/host/python'
    assert kwargs['expected_python_sha256'] == expected['python_sha256']
    assert kwargs['expected_dependencies'] == expected['dependencies']


@pytest.mark.parametrize('role', ('rci', 'fsrt', 'jspt', 'gte'))
@pytest.mark.parametrize('state', ('missing', 'limits', 'history_missing', 'history_changed'))
def test_runtime_refuses_policy_absence_downgrade_or_replay_drift_before_adapter(
        monkeypatch, repositories, policies, role, state):
    def no_adapter(*args, **kwargs):
        raise AssertionError('invalid policy reached companion constructor')
    monkeypatch.setattr(investigation, 'PinnedSubprocessAdapter', no_adapter)
    policy = policies[role]
    expected = runtime_record(role, policy)
    if state == 'missing':
        policy = None
    elif state == 'limits':
        policy = ProcessPolicy(mode='limits', expected_prlimit_sha256='0' * 64)
    elif state == 'history_missing':
        expected.pop('process_policy')
    else:
        policy = replace(policy, cpu_seconds=policy.cpu_seconds + 1)
    with pytest.raises(ProcessPolicyHeld):
        investigation._runtime(role, repositories[role], expected=expected, process_policy=policy)


@pytest.mark.parametrize('role,bind', [('fsrt', investigation.bind_fsrt),
                                     ('jspt', covariance_workflow.bind_jspt),
                                     ('gte', geodesic.bind_gte)])
def test_binding_factory_preserves_exact_policy(monkeypatch, repositories, policies, role, bind):
    calls, operations = [], []
    module = {'fsrt': investigation, 'jspt': covariance_workflow, 'gte': geodesic}[role]
    adapter = RuntimeDouble()
    monkeypatch.setattr(module, '_runtime', lambda *args, **kwargs: calls.append((args, kwargs)) or adapter)
    session = SimpleNamespace(operations=SimpleNamespace(register=operations.append), run={'metadata': {}})
    assert bind(session, repositories[role], process_policy=policies[role]) is adapter
    assert calls[0][1]['process_policy'] is policies[role]
    assert len(operations) == 1


def test_investigation_creation_keeps_policies_separate_from_scientific_input(monkeypatch, repositories, policies, tmp_path):
    calls, registrations = [], []
    selected = {r: policies[r] for r in ('rci', 'fsrt')}
    monkeypatch.setattr(investigation, '_runtime',
        lambda role, *args, **kwargs: calls.append((role, kwargs['process_policy'])) or RuntimeDouble())
    monkeypatch.setattr(investigation, '_validate_batch', lambda *args: None)
    monkeypatch.setattr(investigation, '_make_run', lambda *args: {})
    monkeypatch.setattr(investigation, 'Session', lambda *args: SimpleNamespace(
        operations=SimpleNamespace(register=registrations.append), run={'metadata': {}}))
    monkeypatch.setattr(investigation, '_evaluate_and_save', lambda *args: {'fixture': 'wiring-only'})
    inputs = json.loads((ROOT / 'examples/adapters/two-reservoir.json').read_text())
    original = deepcopy(inputs)
    outcome = investigation.create_investigation(inputs, repositories['rci'], repositories['fsrt'],
        tmp_path / 'out', process_policies=selected)
    assert outcome == {'fixture': 'wiring-only'}
    assert calls == [('rci', policies['rci']), ('fsrt', policies['fsrt'])]
    assert inputs == original
    assert not (tmp_path / 'out').exists()
    calls.clear()
    with pytest.raises(ValueError):
        investigation.create_investigation({**inputs, 'process_policies': {}},
            repositories['rci'], repositories['fsrt'], tmp_path / 'out', process_policies=selected)
    assert calls == []


def test_geodesic_creation_and_replay_forward_current_host_policy(monkeypatch, repositories, policies, tmp_path):
    calls, registrations = [], []
    expected = runtime_record('gte', policies['gte'])
    previous = {'operation_id': geodesic.GTE_OPERATION, 'runtime': expected,
                'parameters': {}, 'channel': 'x', 'interval_s': [0, 1], 'result_id': 'old'}
    source = SimpleNamespace(run={}, executions={'old': previous},
        results={'old': {'data': {'value': 1}}}, selection={'channel': 'x', 'interval_s': [0, 1]},
        operations=SimpleNamespace(register=registrations.append))
    class Sessions:
        def __new__(cls, *args):
            return source
        @staticmethod
        def from_workspace(*args):
            return source
    monkeypatch.setattr(geodesic, 'Session', Sessions)
    monkeypatch.setattr(geodesic, '_make_run', lambda raw: {})
    monkeypatch.setattr(geodesic, 'validate_source', lambda run: None)
    monkeypatch.setattr(geodesic, '_runtime', lambda *args, **kwargs:
        calls.append((args, kwargs)) or RuntimeDouble())
    monkeypatch.setattr(geodesic, '_evaluate_and_save', lambda *args:
        {'status': 'completed', 'results': [{'data': {'value': 1}}]})
    path = tmp_path / 'source.json'
    path.write_text('{}')
    geodesic.create_investigation(path, repositories['gte'], tmp_path / 'created',
                                 process_policies={'gte': policies['gte']})
    replay = geodesic.replay_investigation(path, repositories['gte'], tmp_path / 'replayed',
                                         process_policies={'gte': policies['gte']})
    assert replay['replay_data_digest_matches'] is True
    assert len(calls) == 4
    assert all(kwargs['process_policy'] is policies['gte'] for _, kwargs in calls)
    assert calls[2][0][-1] is expected and calls[3][0][-1] is expected
    assert not (tmp_path / 'replayed').exists()


def test_covariance_execution_and_replay_forward_current_host_policy(monkeypatch, repositories, policies, tmp_path):
    calls = []
    expected = runtime_record('jspt', policies['jspt'])
    source_result = {'operation_id': 'fsrt.tank-reconstruct.v2', 'result_id': 'old', 'data': {'value': 1}}
    source = SimpleNamespace(results={'old': source_result}, executions={'event': {
        'operation_id': covariance_workflow.JSPT_OPERATION, 'runtime': expected,
        'parameters': {}, 'result_id': 'old'}}, operations=SimpleNamespace(register=lambda value: None))
    monkeypatch.setattr(covariance_workflow, 'Session', SimpleNamespace(from_workspace=lambda *args: source))
    monkeypatch.setattr(covariance_workflow, 'result_covariance', lambda *args: {})
    monkeypatch.setattr(covariance_workflow, '_runtime', lambda *args, **kwargs:
        calls.append((args, kwargs)) or RuntimeDouble())
    monkeypatch.setattr(covariance_workflow, '_execute', lambda *args:
        {'status': 'completed', 'results': [source_result]})
    parameters = {field: None for field in covariance_workflow.MAP_FIELDS}
    covariance_workflow.execute_covariance('workspace', repositories['jspt'], parameters,
        tmp_path / 'created', process_policies={'jspt': policies['jspt']})
    result = covariance_workflow.replay_covariance('workspace', repositories['jspt'], tmp_path / 'replayed',
        process_policies={'jspt': policies['jspt']})
    assert result['replay_data_digest_matches'] is True
    assert len(calls) == 4
    assert all(kwargs['process_policy'] is policies['jspt'] for _, kwargs in calls)
    assert calls[2][0][-1] is expected and calls[3][0][-1] is expected
    assert not (tmp_path / 'replayed').exists()


@pytest.mark.parametrize('changed_role', ('rci', 'fsrt'))
def test_investigation_replay_checks_both_policy_histories_before_any_companion(
        monkeypatch, repositories, policies, tmp_path, changed_role):
    selected = {r: policies[r] for r in ('rci', 'fsrt')}
    source = {'runtime': runtime_record('rci', policies['rci']), 'sensors': []}
    prior = {'operation_id': investigation.FSRT_OPERATION, 'runtime': runtime_record('fsrt', policies['fsrt']),
             'parameters': {}, 'status': 'refused'}
    original = SimpleNamespace(run={'metadata': {}}, results={}, executions={'last': prior})
    monkeypatch.setattr(investigation, 'Session', SimpleNamespace(from_workspace=lambda *args: original))
    monkeypatch.setattr(investigation, '_validate_source', lambda run: source)
    calls = []
    monkeypatch.setattr(investigation, '_runtime',
                        lambda *args, **kwargs: calls.append((args, kwargs)) or RuntimeDouble())
    selected[changed_role] = replace(selected[changed_role], cpu_seconds=31)
    with pytest.raises(ProcessPolicyHeld, match='companion_replay_policy_changed'):
        investigation.replay_investigation('workspace', repositories['rci'], repositories['fsrt'],
            tmp_path / 'out', process_policies=selected)
    assert calls == []
    assert not (tmp_path / 'out').exists()
