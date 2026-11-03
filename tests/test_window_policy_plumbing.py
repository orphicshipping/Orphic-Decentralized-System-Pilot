"""Host-policy wiring and refusal checks; adapter doubles do not qualify science.

Real namespace and native provider checks live in the existing window/monitor
suites. These tests isolate authority propagation before any native subprocess.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from ciw import calibrated_window as window, residual_monitor as monitor
from ciw.process_policy import ProcessPolicyHeld
from companion_policy_helpers import test_policy as make_policy


@pytest.fixture
def bindings(tmp_path):
    repositories = {role: tmp_path / role for role in window.ROLES | monitor.ROLES}
    for repository in repositories.values():
        repository.mkdir()
    policies = {role: make_policy(repository, repositories=repositories)
                for role, repository in repositories.items()}
    return repositories, policies


def selected(bindings, workflow):
    repositories, policies = bindings
    return ({role: repositories[role] for role in workflow.ROLES},
            {role: policies[role] for role in workflow.ROLES})


def double_adapter(monkeypatch, workflow):
    calls = []

    class Adapter:
        def __init__(self, repository, revision, module, **kwargs):
            calls.append((repository, kwargs))
            self.repository = Path(repository)
            self.source_root = self.repository / kwargs['source_root']
            self.identity = {
                'schema': 'ciw.subprocess-runtime.v1', 'adapter_version': 'test-double',
                'repository_root': str(repository), 'revision': revision,
                'source_tree': monitor.SOURCE_TREES.get(self.repository.name, 'a' * 40),
                'module': module, 'source_root': kwargs['source_root'],
                'python_executable': '/test/wiring-only/python', 'python_sha256': 'b' * 64,
                'python_version': 'wiring-only', 'dependencies': {},
                'process_policy': kwargs['process_policy'].identity(),
            }

        def runtime_identity(self):
            return deepcopy(self.identity)

    monkeypatch.setattr(workflow, 'PinnedSubprocessAdapter', Adapter)
    return calls


@pytest.mark.parametrize('workflow', [window, monitor], ids=['window', 'monitor'])
def test_host_policy_required_before_any_adapter_probe(bindings, monkeypatch, workflow):
    repositories, _ = selected(bindings, workflow)
    calls = double_adapter(monkeypatch, workflow)
    with pytest.raises(ProcessPolicyHeld, match='companion_isolation_required'):
        workflow._adapters(repositories)
    assert calls == []


@pytest.mark.parametrize('workflow', [window, monitor], ids=['window', 'monitor'])
@pytest.mark.parametrize('fault', ['limits', 'missing-role', 'extra-role', 'receipt-as-policy'])
def test_wrong_host_bindings_refuse_before_any_probe(bindings, monkeypatch, workflow, fault):
    repositories, policies = selected(bindings, workflow)
    role = sorted(policies)[-1]
    if fault == 'limits':
        policies[role] = replace(policies[role], mode='limits')
    elif fault == 'missing-role':
        del policies[role]
    elif fault == 'extra-role':
        policies['unbound'] = policies[role]
    else:
        policies[role] = policies[role].identity()
    calls = double_adapter(monkeypatch, workflow)
    with pytest.raises((ValueError, ProcessPolicyHeld)):
        workflow._adapters(repositories, process_policies=policies)
    assert calls == []


@pytest.mark.parametrize('workflow', [window, monitor], ids=['window', 'monitor'])
def test_every_adapter_receives_exact_host_policy_and_replay_identity(bindings, monkeypatch, workflow):
    repositories, policies = selected(bindings, workflow)
    calls = double_adapter(monkeypatch, workflow)
    bound = workflow._adapters(repositories, process_policies=policies)
    if workflow is window:
        retained = {role: adapter.runtime_identity() for role, adapter in bound.items()}
    else:
        retained = {'fdir': bound[1]}
    assert len(calls) == len(repositories)
    for repository, kwargs in calls:
        assert kwargs['process_policy'] is policies[repository.name]
    calls.clear()
    workflow._adapters(repositories, retained, process_policies=policies)
    assert len(calls) == len(repositories)
    for repository, kwargs in calls:
        assert kwargs['process_policy'] is policies[repository.name]
        assert kwargs['expected_python_sha256'] == 'b' * 64


@pytest.mark.parametrize('workflow', [window, monitor], ids=['window', 'monitor'])
@pytest.mark.parametrize('fault', ['missing-history', 'changed-policy', 'forged-isolation'])
def test_replay_history_never_selects_policy_or_partially_probes(bindings, monkeypatch, workflow, fault):
    repositories, policies = selected(bindings, workflow)
    calls = double_adapter(monkeypatch, workflow)
    bound = workflow._adapters(repositories, process_policies=policies)
    if workflow is window:
        retained = {role: adapter.runtime_identity() for role, adapter in bound.items()}
        target = retained[sorted(retained)[-1]]
    else:
        retained = {'fdir': bound[1]}
        target = retained['fdir']['companions']['oit']
    if fault == 'missing-history':
        del target['process_policy']
    elif fault == 'changed-policy':
        target['process_policy']['policy_sha256'] = 'sha256:' + '0' * 64
    else:
        target['process_policy']['network_isolation_required'] = False
    calls.clear()
    with pytest.raises((ValueError, ProcessPolicyHeld)):
        workflow._adapters(repositories, retained, process_policies=policies)
    assert calls == []


def test_fdir_policy_requires_read_only_oit_import(bindings, monkeypatch):
    repositories, policies = selected(bindings, monitor)
    policies['fdir'] = replace(policies['fdir'], read_only_roots=tuple(
        root for root in policies['fdir'].read_only_roots if root != str(repositories['oit'])))
    calls = double_adapter(monkeypatch, monitor)
    with pytest.raises(ProcessPolicyHeld, match='monitor_companion_read_only_root_required'):
        monitor._adapters(repositories, process_policies=policies)
    assert calls == []


def test_oit_cannot_claim_limits_that_its_shared_fdir_process_does_not_apply(bindings, monkeypatch):
    repositories, policies = selected(bindings, monitor)
    policies['oit'] = replace(policies['oit'], wall_seconds=1.0)
    calls = double_adapter(monkeypatch, monitor)
    with pytest.raises(ProcessPolicyHeld, match='monitor_shared_process_policy_required'):
        monitor._adapters(repositories, process_policies=policies)
    assert calls == []


@pytest.mark.parametrize('workflow', [window, monitor], ids=['window', 'monitor'])
@pytest.mark.parametrize('replay', [False, True])
def test_public_entrypoint_forwards_only_supplied_host_policy(bindings, monkeypatch, workflow, replay):
    repositories, policies = selected(bindings, workflow)
    calls = []

    class ReachedAdapterBoundary(Exception):
        pass

    def stop(paths, expected=None, *, process_policies=None):
        calls.append((paths, expected, process_policies))
        raise ReachedAdapterBoundary

    target = workflow if workflow is window else workflow.workflow
    monkeypatch.setattr(target, '_adapters', stop)
    # Source/retained validation is independent of this narrow routing check.
    if replay:
        monkeypatch.setattr(target, '_validate', lambda _: b'validated-offline')
        bundle = {'runtimes': {}, 'upstream_windows': {}}
        with pytest.raises(ReachedAdapterBoundary):
            workflow.replay_session(bundle, repositories, process_policies=policies)
        assert calls == [(repositories, bundle['runtimes'], policies)]
    else:
        monkeypatch.setattr(workflow, '_source', lambda _: {})
        if workflow is monitor:
            monkeypatch.setattr(workflow, '_request', lambda *_: {})
        with pytest.raises(ReachedAdapterBoundary):
            if workflow is window:
                workflow.create_session(b'validated-source', repositories, process_policies=policies)
            else:
                workflow.create_session(b'validated-source', {}, repositories, process_policies=policies)
        assert calls == [(repositories, None, policies)]
