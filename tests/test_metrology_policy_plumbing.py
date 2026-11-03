"""Host binding and refusal checks; doubles here do not qualify native execution."""
from dataclasses import replace
from pathlib import Path

import pytest

from ciw import calibrated_window as window
from ciw import metrology_clearance as clearance
from ciw import metrology_operations as metrology
from ciw import operational_engineering as engineering
from ciw import operational_native as native_workflow
from ciw.agent_api import encode, parse
from ciw.operational_agent import OperationalHost
from ciw.operational_cli import default_context, prepare
from ciw.process_policy import ProcessPolicyHeld
from companion_policy_helpers import policy_config_document, test_policy

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / 'examples/calibrated-window/source.json').read_bytes()


def bindings(directory):
    paths = {}
    for role in window.ROLES:
        path = directory / role
        path.mkdir(parents=True)
        paths[role] = str(path)
    policies = {role: test_policy(path, repositories=paths) for role, path in paths.items()}
    return paths, policies


def profile(directory, paths):
    return prepare(directory, investigation_id='policy-plumbing', sources={'window': {
        'raw': RAW, 'family': 'calibrated-window',
        'context': default_context(clock=parse(RAW)['device_clock']['clock_id']),
        'configurations': []}}, models={'window': {'source': 'window', 'engine': 'calibrated-window',
        'request': None, 'policy': {'unit': 'm', 'coverage_factor': 2,
                                   'max_abs_error': 1, 'max_standard_uncertainty': 1}}},
        providers={'calibrated-window': paths})


def test_missing_policy_is_inspectable_but_native_dispatch_refuses(tmp_path, monkeypatch):
    paths, _ = bindings(tmp_path / 'repositories')
    selected = profile(tmp_path / 'host', paths)
    monkeypatch.setattr(window, 'create_session', lambda *a, **k: pytest.fail('Unisolated native dispatch'))
    with OperationalHost(selected) as host:
        assert host.model_adapters['window']['provider_status'] == 'provider_unavailable'
        assert host.agent._attempts == {}
    with pytest.raises(ProcessPolicyHeld, match='companion_isolation_required'):
        native_workflow.execute(RAW, 'calibrated-window', {'calibrated-window': paths})


@pytest.mark.parametrize('fault', ['limits', 'missing-role', 'wrong-checkout'])
def test_wrong_host_policy_refuses_before_host_output(tmp_path, fault):
    paths, policies = bindings(tmp_path / 'repositories')
    selected = profile(tmp_path / 'host', paths)
    role = next(iter(policies))
    if fault == 'limits':
        policies[role] = replace(policies[role], mode='limits')
    elif fault == 'missing-role':
        policies.pop(role)
    else:
        policies[role] = replace(policies[role], read_only_roots=('/usr',))
    with pytest.raises(ValueError):
        OperationalHost(selected, process_policies={'calibrated-window': policies})
    spec = parse(selected.read_bytes())
    assert not (selected.parent / spec['execution_output']).exists()


def test_native_forwarding_preserves_host_policy_identity(tmp_path, monkeypatch):
    paths, policies = bindings(tmp_path / 'repositories')
    observed = []
    marker = object()
    def capture(raw, repositories, *, process_policies):
        observed.append((raw, repositories, process_policies))
        return marker
    monkeypatch.setattr(window, 'create_session', capture)
    assert native_workflow.execute(RAW, 'calibrated-window', {'calibrated-window': paths},
        process_policies={'calibrated-window': policies}) is marker
    assert observed[0][0] == RAW and observed[0][1] == paths
    assert all(observed[0][2][role] is policies[role] for role in policies)
    with pytest.raises(TypeError):
        observed[0][2]['tbrt'] = None


def test_registry_detaches_host_bindings_and_records_policy_identity(tmp_path, monkeypatch):
    paths, policies = bindings(tmp_path / 'repositories')
    original_paths, original_policies = dict(paths), dict(policies)
    selected = {'calibrated-window': policies}
    seen = []
    def assess(source, parameters, *, providers, process_policies):
        seen.append((providers, process_policies))
        return {'wiring_only': True}
    monkeypatch.setattr(engineering, 'assess', assess)
    registry = engineering.registry(providers={'calibrated-window': paths}, process_policies=selected)
    runtime = registry.contract(engineering.ASSESS)['runtime']
    assert runtime['companion_policies']['calibrated-window'] == {
        role: policy.identity() for role, policy in original_policies.items()}
    assert 'read_only_roots' not in encode(runtime).decode()
    paths.clear(); policies.clear(); selected.clear()
    operation = registry.operations.get(engineering.ASSESS)
    assert operation.execute({}, {}) == {'wiring_only': True}
    assert dict(seen[0][0]['calibrated-window']) == original_paths
    assert dict(seen[0][1]['calibrated-window']) == original_policies


def test_empty_policy_map_preserves_builtin_identity_shape():
    identity = engineering.runtime_identity()
    assert engineering.runtime_identity(process_policies={}) == identity
    assert 'companion_policies' not in identity
    registry = engineering.registry()
    assert registry.contract(engineering.ASSESS)['runtime'] == identity


def test_operational_reopen_requires_same_host_policy(tmp_path):
    paths, policies = bindings(tmp_path / 'repositories')
    selected = profile(tmp_path / 'host', paths)
    with OperationalHost(selected, process_policies={'calibrated-window': policies}) as host:
        identity = host.agent._host_policy
        assert host.model_adapters['window']['provider_status'] == 'operator_bound_pin_check_at_dispatch'
    with OperationalHost(selected, resume=True, process_policies={'calibrated-window': policies}) as reopened:
        assert reopened.agent._host_policy == identity
    changed = dict(policies)
    changed['tbrt'] = replace(changed['tbrt'], wall_seconds=29)
    with pytest.raises(ValueError):
        OperationalHost(selected, resume=True, process_policies={'calibrated-window': changed})
    with pytest.raises(ValueError):
        OperationalHost(selected, resume=True)


def test_saved_profile_cannot_select_policy(tmp_path):
    paths, policies = bindings(tmp_path / 'repositories')
    selected = profile(tmp_path / 'host', paths)
    value = parse(selected.read_bytes())
    value['process_policies'] = policy_config_document({'calibrated-window': policies})
    selected.write_bytes(encode(value))
    with pytest.raises(ValueError):
        OperationalHost(selected)


def test_operational_launcher_reads_operator_file_without_saving_it(tmp_path, capsys):
    from ciw.operational_cli import main
    paths, policies = bindings(tmp_path / 'repositories')
    selected = profile(tmp_path / 'host', paths)
    original = selected.read_bytes()
    config = tmp_path / 'companion-policies.json'
    config.write_bytes(encode(policy_config_document({'calibrated-window': policies})))
    assert main(['init', '--profile', str(selected), '--companion-policies', str(config)]) == 0
    assert parse(capsys.readouterr().out.encode())
    with OperationalHost(selected, resume=True, process_policies={'calibrated-window': policies}) as host:
        assert host.model_adapters['window']['provider_status'] == 'operator_bound_pin_check_at_dispatch'
        for role, policy in policies.items():
            assert host._process_policies['calibrated-window'][role].identity() == policy.identity()
    assert selected.read_bytes() == original
    assert str(config) not in selected.read_text()


def test_clearance_launcher_supplies_reviewed_policy_before_provider_binding(tmp_path, monkeypatch, capsys):
    policy = test_policy(tmp_path)
    config = tmp_path / 'companions.json'
    config.write_bytes(encode(policy_config_document({'bim-clearance': {'cse': policy}})))
    called = []
    def provider(*args, **kwargs):
        called.append(kwargs)
        raise ValueError('wiring-only stop before native initialization')
    monkeypatch.setattr(clearance, 'Provider', provider)
    assert clearance.main(['run', str(tmp_path / 'profile.json'), '--provider', str(tmp_path),
        '--revision', 'a' * 40, '--attempt', 'first', '--companion-policies', str(config)]) == 2
    assert called[0]['process_policy'].identity() == policy.identity()
    assert 'wiring-only stop' in capsys.readouterr().err


def test_clearance_provider_requires_and_forwards_host_policy(tmp_path, monkeypatch):
    policy = test_policy(tmp_path)
    called = []
    class Adapter:
        def __init__(self, *args, **kwargs):
            called.append(kwargs)
        def runtime_identity(self):
            return {'source_tree': clearance.NATIVE_TREE, 'process_policy': policy.identity()}
    monkeypatch.setattr(clearance, 'PinnedSubprocessAdapter', Adapter)
    with pytest.raises(ProcessPolicyHeld, match='companion_isolation_required'):
        clearance.Provider(tmp_path, 'a' * 40)
    assert called == []
    provider = clearance.Provider(tmp_path, 'a' * 40, process_policy=policy)
    assert called[0]['process_policy'] is policy
    assert called[0]['max_output_bytes'] == 256 * 1024
    assert provider.runtime_identity()['native']['process_policy'] == policy.identity()


@pytest.mark.parametrize('include_policy', [False, True])
def test_metrology_launcher_native_catalog_uses_only_operator_policy(tmp_path, capsys, include_policy):
    paths, policies = bindings(tmp_path / 'repositories')
    selected = profile(tmp_path / 'host', paths)
    # Discovery is read-only and does not claim the namespace check has passed.
    operations = [engineering.ASSESS, engineering.VERIFY, *[name for _, name in window.OPERATIONS],
                  window.COMPOSITION['calibration_operation_id']]
    policy_document = {'schema': 'ciw.principal-policy.v1', 'revision': 'plumbing', 'principals': [
        {'principal': 'alice', 'enabled': True,
         'operations': [{'name': name, 'constraints': {}} for name in metrology.TOOLS],
         'scientific_operations': operations, 'resources': [], 'reference_scopes': [], 'handoff_recipients': []}]}
    principal = tmp_path / 'principal.json'; principal.write_bytes(encode(policy_document))
    config = tmp_path / 'metrology.json'
    config.write_bytes(encode({'schema': metrology.PROFILE, 'principal': 'alice', 'tenant': 'lab',
        'policy': str(principal), 'journal': str(tmp_path / 'metrology.sqlite'),
        'jobs': {'window': {'kind': 'operational', 'profile': str(selected), 'model': 'window'}}}))
    args = ['--config', str(config), '--initialize', '--tool', 'ncp_metrology_catalog']
    if include_policy:
        companion = tmp_path / 'companions.json'
        companion.write_bytes(encode(policy_config_document({'calibrated-window': policies})))
        args += ['--companion-policies', str(companion)]
    assert metrology.main(args) == 0
    row = parse(capsys.readouterr().out.encode())['data']['jobs'][0]
    assert row['provider_status'] == ('operator_bound_pin_check_at_dispatch' if include_policy else 'provider_unavailable')
    assert row['currently_executable'] is include_policy
    assert 'process_policies' not in parse(selected.read_bytes())
