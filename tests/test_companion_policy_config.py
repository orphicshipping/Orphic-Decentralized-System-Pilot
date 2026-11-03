"""Host configuration and policy identity tests; no namespace success is mocked."""
from copy import deepcopy
from dataclasses import replace
import json
import os

import pytest

from ciw import companion_policy_config as config
from ciw.process_policy import ProcessPolicy, ProcessPolicyHeld, minimal_environment
from companion_policy_helpers import policy_config_document


@pytest.fixture
def selected(tmp_path):
    repo = tmp_path / 'provider'
    repo.mkdir()
    policy = ProcessPolicy(mode='bubblewrap', read_only_roots=(str(repo),),
                           expected_prlimit_sha256='1'*64, expected_bwrap_sha256='2'*64)
    return repo, policy


def store(tmp_path, document):
    path = tmp_path / 'operator.json'
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    return path


def test_closed_operator_file_detaches_configuration_and_has_fixed_environment(tmp_path, selected, monkeypatch):
    repo, policy = selected
    document = policy_config_document({'geodesic': {'gte': policy}})
    path = store(tmp_path, document)
    monkeypatch.setenv('ODSP_PRIVATE_SYNTHETIC_CANARY', 'never-inherit')
    loaded = config.load_companion_policies(path)
    bound = config.bind_process_policies({'gte': repo}, loaded['geodesic'])
    assert dict(bound['gte'].environment) == minimal_environment()
    assert bound['gte'].mode == 'bubblewrap'
    assert bound['gte'].expected_bwrap_sha256 == '2'*64  # No auto-derived launcher trust.
    document['workflows'].clear()
    assert set(loaded) == {'geodesic'}
    with pytest.raises(TypeError):
        loaded['geodesic']['gte'] = policy
    with pytest.raises(TypeError):
        bound['gte'].environment['SECRET'] = 'x'
    assert config.policy_set(loaded, 'investigation') is None
    assert not config.load_companion_policies()


@pytest.mark.parametrize('mutation', [
    lambda doc: doc.update(schema='wrong'),
    lambda doc: doc.update(request_policy={}),
    lambda doc: doc['workflows'].update(unknown={}),
    lambda doc: doc['workflows']['geodesic'].update(extra={}),
    lambda doc: doc['workflows']['geodesic'].clear(),
    lambda doc: doc['workflows']['geodesic']['gte'].update(mode='limits'),
    lambda doc: doc['workflows']['geodesic']['gte'].update(environment={'TOKEN': 'untrusted'}),
    lambda doc: doc['workflows']['geodesic']['gte'].update(cpu_seconds=True),
    lambda doc: doc['workflows']['geodesic']['gte'].update(wall_seconds=301),
    lambda doc: doc['workflows']['geodesic']['gte'].update(expected_bwrap_sha256='A'*64),
    lambda doc: doc['workflows']['geodesic']['gte'].update(expected_prlimit_sha256=123),
    lambda doc: doc['workflows']['geodesic']['gte'].update(bwrap_executable='relative'),
    lambda doc: doc['workflows']['geodesic']['gte'].update(read_only_roots=['/']),
    lambda doc: doc['workflows']['geodesic']['gte'].update(read_only_roots='not-a-list'),
])
def test_untrusted_fields_and_policy_downgrades_refuse(tmp_path, selected, mutation):
    _, policy = selected
    document = policy_config_document({'geodesic': {'gte': policy}})
    mutation(document)
    with pytest.raises(ValueError):
        config.load_companion_policies(store(tmp_path, document))


@pytest.mark.parametrize('raw', [b'{"schema":1,"schema":2}', b'{"x":NaN}', b'{"x":1e999}',
                                 b'\xff', b'{}' + b' '*65536, b''])
def test_strict_bounded_json(tmp_path, raw):
    path = tmp_path / 'operator.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        config.load_companion_policies(path)


def test_config_symlink_shared_write_fifo_and_relative_path_refuse(tmp_path, selected, monkeypatch):
    _, policy = selected
    path = store(tmp_path, policy_config_document({'geodesic': {'gte': policy}}))
    link = tmp_path / 'link.json'
    link.symlink_to(path)
    with pytest.raises(OSError):
        config.load_companion_policies(link)
    path.chmod(0o666)
    with pytest.raises(ValueError):
        config.load_companion_policies(path)
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        config.load_companion_policies(fifo)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        config.load_companion_policies('operator.json')


def test_exact_roles_required_and_checkout_must_be_read_only(selected, tmp_path):
    repo, policy = selected
    with pytest.raises(ProcessPolicyHeld, match='companion_isolation_required'):
        config.bind_process_policies({'gte': repo}, None)
    for policies in ({}, {'gte': policy, 'unknown': policy}, {'wrong': policy}):
        with pytest.raises(ValueError):
            config.bind_process_policies({'gte': repo}, policies)
    with pytest.raises(ProcessPolicyHeld, match='companion_isolation_required'):
        config.bind_process_policy(repo, replace(policy, mode='limits'))
    other = tmp_path / 'different-provider'
    other.mkdir()
    with pytest.raises(ProcessPolicyHeld, match='read_only_root_required'):
        config.bind_process_policy(other, policy)
    mutable = {'gte': policy}
    bound = config.bind_process_policies({'gte': repo}, mutable)
    mutable.clear()
    assert bound['gte'] is policy


def test_replay_identity_cannot_supply_authority_or_change_policy(selected):
    _, policy = selected
    saved = {'process_policy': policy.identity()}
    config.check_replay_policy(policy, saved)
    with pytest.raises(ProcessPolicyHeld, match='isolation_required'):
        config.check_replay_policy(None, saved)
    with pytest.raises(ProcessPolicyHeld, match='history_missing'):
        config.check_replay_policy(policy, {})
    with pytest.raises(ProcessPolicyHeld, match='replay_policy_changed'):
        config.check_replay_policy(replace(policy, max_output_bytes=1234), saved)
    forged = deepcopy(saved)
    forged['process_policy']['max_open_files'] = True
    with pytest.raises(ValueError):
        config.check_replay_policy(policy, forged)
    # Historical evidence may describe limits mode, but cannot select that mode.
    old = replace(policy, mode='limits').identity()
    config.validate_policy_identity(old)
    with pytest.raises(ProcessPolicyHeld, match='replay_policy_changed'):
        config.check_replay_policy(policy, {'process_policy': old})
