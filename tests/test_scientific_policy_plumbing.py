"""Host routing tests with labelled adapter doubles, not native qualification."""
import base64
from dataclasses import replace
import json
from pathlib import Path

import pytest

from ciw import calibrated_window as window, scientific
from ciw.instruments import make_demo_run
from ciw.process_policy import ProcessPolicy, ProcessPolicyHeld
from ciw.session import Session
from ciw.workbench import Workbench


@pytest.fixture
def bindings(tmp_path):
    repositories = {}
    for role in window.ROLES:
        path = tmp_path / role
        path.mkdir()
        repositories[role] = path
    policies = {role: ProcessPolicy(mode='bubblewrap', read_only_roots=(str(path),),
                expected_prlimit_sha256='1'*64, expected_bwrap_sha256='2'*64)
                for role, path in repositories.items()}
    return repositories, policies


def test_workbench_binding_refuses_before_probe_and_never_restores_authority(bindings, monkeypatch):
    repositories, policies = bindings
    seen = []
    monkeypatch.setattr(window, '_adapters', lambda *a, **k: seen.append((a, k)))
    host = Workbench()
    with pytest.raises(ProcessPolicyHeld, match='isolation_required'):
        host.bind_workflow('calibrated-window', repositories)
    weak = {role: replace(policy, mode='limits') for role, policy in policies.items()}
    with pytest.raises(ProcessPolicyHeld, match='isolation_required'):
        host.bind_workflow('calibrated-window', repositories, process_policies=weak)
    assert seen == [] and 'calibrated-window' not in host._bindings
    host.bind_workflow('calibrated-window', repositories, process_policies=policies)
    assert seen[-1][1]['process_policies'] == policies
    assert 'process_policies' not in json.dumps(host.serialize())
    restored = Workbench.restore(host.serialize())
    assert not restored._companion_policies and 'calibrated-window' not in restored._bindings
    with pytest.raises(ValueError):
        Workbench.restore({**host.serialize(), 'process_policies': {'mode': 'limits'}})
    # Caller dictionary mutation cannot alter the paired launch snapshot.
    original = dict(policies)
    policies.clear()
    with host._lock:
        paths, selected, reserved = host._reserve_with_policy('calibrated-window')
        host._pending -= 1
        host._reserved_bytes -= reserved
    assert paths == repositories and selected == original


def test_scientific_execute_forwards_host_policy_and_failed_attempt_releases_capacity(bindings, tmp_path, monkeypatch):
    repositories, policies = bindings
    monkeypatch.setattr(window, '_adapters', lambda *a, **k: {})
    captured = []
    def stop(raw, paths, *, process_policies):
        captured.append((raw, paths, dict(process_policies)))
        raise ValueError('labelled wiring-only provider refusal')
    monkeypatch.setattr(window, 'create_session', stop)
    session = Session(make_demo_run(), tmp_path / 'session')
    raw = (Path(__file__).resolve().parents[1] / 'examples/calibrated-window/source.json').read_bytes()
    with pytest.raises(Exception, match='wiring-only provider refusal'):
        scientific.execute(session, 'calibrated-window', raw, label='wiring fixture',
                           repositories=repositories, process_policies=policies)
    assert captured == [(raw, repositories, policies)]
    assert session.workbench.pending_operations == 0
    assert session.workbench._reserved_bytes == 0
    assert session.workbench.list_bundles() == []
    assert len(session.workbench.list_failed_executions()) == 1


def test_request_cannot_supply_host_policy(bindings, tmp_path, monkeypatch):
    repositories, policies = bindings
    monkeypatch.setattr(window, '_adapters', lambda *a, **k: {})
    session = Session(make_demo_run(), tmp_path / 'session')
    scientific.bind(session, 'calibrated-window', repositories, process_policies=policies)
    raw = (Path(__file__).resolve().parents[1] / 'examples/calibrated-window/source.json').read_bytes()
    source = scientific.request(session, 'source.add', scientific.source_payload('calibrated-window', raw, 'fixture'))
    def forbidden(*args, **kwargs):
        raise AssertionError('caller policy reached companion dispatch')
    monkeypatch.setattr(window, 'create_session', forbidden)
    response = session.handle({'protocol_version': 1, 'request_id': 'untrusted-policy', 'type': 'operation.execute',
        'payload': {'operation_id': 'ciw.calibrated-window.v1',
                    'parameters': {'source_id': source['source_id'], 'process_policies': {'mode': 'limits'}}}})
    assert response['type'] == 'error'
    assert not session.workbench.list_bundles()
    with pytest.raises(ValueError, match='explicit host repository'):
        scientific.execute(session, 'calibrated-window', raw, label='fixture', process_policies=policies)


@pytest.mark.parametrize('workflow,action', [
    ('investigation', 'create'), ('investigation', 'replay'),
    ('covariance', 'create'), ('covariance', 'replay'),
    ('geodesic', 'create'), ('geodesic', 'replay'),
])
def test_direct_cli_routes_only_operator_policy(tmp_path, monkeypatch, capsys, workflow, action):
    from ciw import cli, investigation, covariance_workflow, geodesic
    from ciw.companion_policy_config import WORKFLOW_ROLES
    from companion_policy_helpers import policy_config_document
    repositories = {}
    policies = {}
    for role in WORKFLOW_ROLES[workflow]:
        path = tmp_path / role
        path.mkdir()
        repositories[role] = path
        policies[role] = ProcessPolicy(mode='bubblewrap', read_only_roots=(str(path),),
            expected_prlimit_sha256='1'*64, expected_bwrap_sha256='2'*64)
    operator = tmp_path / 'operator.json'
    operator.write_text(json.dumps(policy_config_document({workflow: policies})))
    parameters = tmp_path / 'inputs.json'
    parameters.write_text('{}')
    capture = []
    def stop(*args, **kwargs):
        capture.append(kwargs)
        return {'status': 'wiring_only_no_companion_launched'}
    if workflow == 'covariance':
        monkeypatch.setattr(covariance_workflow, 'execute_covariance' if action == 'create' else 'replay_covariance', stop)
        args = ['covariance' if action == 'create' else 'covariance-replay', str(tmp_path/'saved.json')]
        if action == 'create':
            args += ['--parameters', str(parameters)]
    else:
        module = investigation if workflow == 'investigation' else geodesic
        monkeypatch.setattr(module, 'create_investigation' if action == 'create' else 'replay_investigation', stop)
        args = [workflow, action]
        args += ['--inputs', str(parameters)] if action == 'create' else [str(tmp_path/'saved.json')]
    for role, path in repositories.items():
        args += ['--'+role+'-repo', str(path)]
    args += ['--output-dir', str(tmp_path/'out'), '--companion-policies', str(operator), '--json']
    assert cli.main(args) == 0
    assert len(capture) == 1
    assert {r: p.identity() for r, p in capture[0]['process_policies'].items()} == {
        r: p.identity() for r, p in policies.items()}
    assert 'wiring_only' in capsys.readouterr().out
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('action', ['run', 'replay'])
def test_scientific_cli_routes_operator_policy_separately_from_workspace(bindings, tmp_path, monkeypatch, capsys, action):
    from ciw.scientific_cli import main
    from companion_policy_helpers import policy_config_document
    repositories, policies = bindings
    operator = tmp_path / 'operator.json'
    operator.write_text(json.dumps(policy_config_document({'calibrated-window': policies})))
    original = Session(make_demo_run(), tmp_path/'seed').save_workspace(tmp_path/'original.json')
    before = original.read_bytes()
    captured = []
    def stop(*args, **kwargs):
        captured.append(kwargs)
        return {'status': 'wiring_only_no_companion_launched'}
    monkeypatch.setattr(scientific, 'execute' if action == 'run' else 'replay', stop)
    args = [action, '--workspace', str(original), '--output-dir', str(tmp_path/'out'),
            '--companion-policies', str(operator), '--json']
    for role, path in repositories.items():
        args += ['--binding', role+'='+str(path)]
    if action == 'run':
        source = Path(__file__).resolve().parents[1]/'examples/calibrated-window/source.json'
        args += ['--kind', 'calibrated-window', '--source', str(source), '--label', 'routing']
    else:
        monkeypatch.setattr(scientific, 'selected_bundle', lambda *a: {'kind': 'calibrated-window'})
        args += ['--bundle', 'wiring-only-bundle']
    assert main(args) == 0
    assert len(captured) == 1 and captured[0]['repositories'] == repositories
    assert {r: p.identity() for r, p in captured[0]['process_policies'].items()} == {
        r: p.identity() for r, p in policies.items()}
    assert 'wiring_only' in capsys.readouterr().out
    assert original.read_bytes() == before
    retained = (tmp_path/'out/workspace.json').read_text()
    assert 'companion-policies' not in retained and 'operator.json' not in retained
