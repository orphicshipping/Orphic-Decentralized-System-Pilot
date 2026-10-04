"""Temperature processing uses existing MCP permissions, identities and replay."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ciw.agent_api import encode, parse
from ciw.agent_mcp import VERSIONS, demo_config, from_profile, main, temperature_config
from ciw.operations.runner import digest, execute, seal
from ciw.session import Session
from ciw.temperature_contract import AUTHORITY
from ciw.temperature_workflow import PROCESS, VERIFY, make_source, operations, source_request


def test_temperature_mcp_execution_retry_and_replay_retain_separate_authority(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    before = {p.name: p.read_bytes() for p in profile.parent.iterdir()}
    host = from_profile(profile, instrument='temperature')
    catalog = host.capabilities()['catalog']
    assert set(catalog['operations']) == {PROCESS, VERIFY}
    assert catalog['authorizes_execution'] is False
    first = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'first'})
    assert first['status'] == 'completed', first
    workspace = parse((profile.parent / 'agent-output/first/workspace.json').read_bytes())
    result = workspace['results'][0]
    assert result['operation_id'] == PROCESS
    assert result['data']['authority'] == AUTHORITY
    assert result['verification_status'] == 'not_verified'
    assert result['verification_id'] is None
    assert len(workspace['results']) == 2
    audited = next(row for row in workspace['results'] if row['operation_id'] == VERIFY)
    assert audited['role'] == 'verification'
    assert audited['verification_status'] == 'not_verified' and audited['verification_id'] is None
    assert audited['data']['authority'] == AUTHORITY
    assert audited['data']['processing_result_id'] == result['result_id']
    assert audited['data']['processing_execution_id'] == result['execution_id']
    assert audited['data']['processing_record_digest'] == result['record_digest']
    assert audited['data']['processing_report_ref'] == digest(result['data'])
    assert audited['data']['report']['status'] == 'PASS'
    assert result['data']['acceptance']['status'] == 'indeterminate'
    retry = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'first'})
    assert retry['reused_response'] is True
    assert retry['execution_ids'] == first['execution_ids']
    replay = host.call('net_replay', {'original_attempt': 'first', 'new_attempt': 'second'})
    assert replay['status'] == 'completed', replay
    assert set(first['execution_ids']).isdisjoint(replay['execution_ids'])
    assert set(first['result_ids']).isdisjoint(replay['result_ids'])
    second = parse((profile.parent / 'agent-output/second/workspace.json').read_bytes())
    assert second['results'][0]['data'] == result['data']
    assert second['run']['evidence_id'] == workspace['run']['evidence_id']
    replayed_audit = next(row for row in second['results'] if row['operation_id'] == VERIFY)
    assert replayed_audit['data']['verification_id'] != audited['data']['verification_id']
    assert replayed_audit['data']['processing_result_id'] != result['result_id']
    assert replayed_audit['data']['report'] == audited['data']['report']
    assert before == {name: (profile.parent / name).read_bytes() for name in before}


def test_temperature_requires_explicit_instrument_and_execution_grant(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    with pytest.raises(ValueError, match='not advertised'):
        from_profile(profile)
    value = parse(profile.read_bytes())
    value['allow_operations'] = []
    profile.write_bytes(encode(value))
    host = from_profile(profile, instrument='temperature')
    op = host.capabilities()['catalog']['operations'][PROCESS]
    assert not op['bound'] and not op['agent_execution_enabled']
    verifier = host.capabilities()['catalog']['operations'][VERIFY]
    assert not verifier['bound'] and not verifier['agent_execution_enabled']
    with pytest.raises(ValueError, match='not enabled'):
        host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'denied'})
    builtin = from_profile(demo_config(tmp_path / 'builtin'))
    assert PROCESS not in builtin.capabilities()['catalog']['operations']


def test_temperature_profile_cannot_grant_candidate_calibration_or_policy_edits(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    value = parse(profile.read_bytes())
    value['candidate_domains'] = {'baseline': {}}
    profile.write_bytes(encode(value))
    with pytest.raises(ValueError, match='no agent-editable'):
        from_profile(profile, instrument='temperature')


def test_temperature_profile_creation_is_inert_and_create_only(tmp_path, monkeypatch):
    from ciw import temperature_processing, temperature_workflow, temperature_verification

    def forbidden(*args, **kwargs):
        pytest.fail('Creating a profile dispatched measurement processing')

    monkeypatch.setattr(temperature_processing, 'process_request', forbidden)
    monkeypatch.setattr(temperature_verification, 'verify', forbidden)
    monkeypatch.setattr(temperature_workflow, 'runtime_identity', forbidden)
    profile = temperature_config(tmp_path / 'profile')
    assert not (profile.parent / 'agent-output').exists()
    before = {p.name: p.read_bytes() for p in profile.parent.iterdir()}
    with pytest.raises(FileExistsError):
        temperature_config(profile.parent)
    assert before == {p.name: p.read_bytes() for p in profile.parent.iterdir()}


def test_temperature_restore_never_executes_the_processing_provider(tmp_path, monkeypatch):
    from ciw import temperature_processing, temperature_workflow, temperature_verification
    profile = temperature_config(tmp_path / 'profile')
    host = from_profile(profile, instrument='temperature')
    result = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'first'})
    assert result['status'] == 'completed', result

    def forbidden(*args, **kwargs):
        pytest.fail('Restoring a workspace dispatched a processing provider')

    monkeypatch.setattr(temperature_processing, 'process_request', forbidden)
    monkeypatch.setattr(temperature_verification, 'verify', forbidden)
    monkeypatch.setattr(temperature_workflow, '_process', forbidden)
    monkeypatch.setattr(temperature_workflow, '_verify', forbidden)
    monkeypatch.setattr(temperature_workflow, 'runtime_identity', forbidden)
    saved = profile.parent / 'agent-output/first/workspace.json'
    restored = Session.from_workspace(saved, tmp_path / 'restored')
    assert len(restored.results) == 2
    assert next(iter(restored.results.values()))['data']['authority'] == AUTHORITY


def test_source_envelope_cannot_relabel_declared_sensor_data(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    source = parse((profile.parent / 'source.json').read_bytes())
    source['channels']['measurement_declaration']['values'] = [2.0]
    from ciw.core.identities import evidence_id
    source['evidence_id'] = evidence_id(source)
    with pytest.raises(ValueError, match='exact retained'):
        source_request(source)


def test_temperature_pipeline_refuses_unspecified_operation_parameters(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    path = profile.parent / 'experiment.json'
    graph = parse(path.read_bytes())
    graph['nodes'][0]['parameters'] = {'coverage_factor': 1}
    seal(graph)
    path.write_bytes(encode(graph))
    host = from_profile(profile, instrument='temperature')
    result = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'bad-parameters'})
    assert result['status'] != 'completed'
    workspace = parse((profile.parent / 'agent-output/bad-parameters/workspace.json').read_bytes())
    assert workspace['results'] == []
    assert workspace['executions'][0]['status'] == 'refused'


def test_temperature_mcp_stdio_launch_processes_without_protocol_noise(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': VERSIONS[0], 'capabilities': {},
            'clientInfo': {'name': 'temperature-contract-test', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'net_execute', 'arguments': {'source': 'source', 'graph': 'baseline', 'attempt': 'first'}}},
    ]
    environment = os.environ.copy()
    from ciw import temperature_workflow
    environment['PYTHONPATH'] = str(Path(temperature_workflow.__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, '-m', 'ciw.agent_mcp', 'serve', '--instrument', 'temperature',
                             '--profile', str(profile)], input=b'\n'.join(encode(v) for v in messages) + b'\n',
                            capture_output=True, timeout=30, env=environment)
    assert result.returncode == 0, result.stderr.decode()
    responses = {m['id']: m for m in map(json.loads, result.stdout.splitlines())}
    assert set(responses) == {1, 2}
    assert responses[2]['result']['isError'] is False
    assert responses[2]['result']['structuredContent']['status'] == 'completed'


def test_temperature_config_cli(tmp_path, capsys):
    assert main(['temperature-config', '--output-dir', str(tmp_path / 'profile')]) == 0
    assert Path(capsys.readouterr().out.strip()).is_file()


def test_temperature_config_accepts_operator_request_and_freezes_source(tmp_path, capsys):
    from ciw.temperature_contract import example_request
    request = example_request()
    request['identity']['cycle_id'] = 'operator-selected-cycle'
    input_path = tmp_path / 'input.json'
    input_path.write_bytes(encode(request))
    destination = tmp_path / 'profile'
    assert main(['temperature-config', '--output-dir', str(destination), '--request', str(input_path)]) == 0
    capsys.readouterr()
    source = parse((destination / 'source.json').read_bytes())
    assert source_request(source) == request
    request['identity']['cycle_id'] = 'changed-after-binding'
    input_path.write_bytes(encode(request))
    (destination / 'request.json').write_bytes(encode(request))
    host = from_profile(destination / 'profile.json', instrument='temperature')
    result = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'frozen'})
    assert result['status'] == 'completed', result
    workspace = parse((destination / 'agent-output/frozen/workspace.json').read_bytes())
    assert workspace['results'][0]['data']['measurement_identity']['cycle_id'] == 'operator-selected-cycle'


def _direct_processing():
    from ciw.operations.registry import OperationRegistry
    from ciw.session import _recording_file
    from ciw.temperature_contract import example_request
    source = make_source(example_request())
    registry = OperationRegistry()
    for item in operations():
        registry.register(item)
    selection = {'revision': 0, 'channel': 'measurement_declaration', 'interval_s': [0.0, 1.0]}
    execution, processing = execute(registry, source, selection, _recording_file(source), PROCESS, {})
    assert execution['status'] == 'completed'
    return registry, source, selection, processing


@pytest.mark.parametrize('fault', ['absent', 'tampered'])
def test_temperature_verification_requires_actual_retained_processing_before_audit(fault, monkeypatch):
    from ciw import temperature_verification
    from ciw.session import _recording_file
    registry, source, selection, processing = _direct_processing()
    candidate = deepcopy(processing)
    retained = {} if fault == 'absent' else {processing['result_id']: processing}
    if fault == 'tampered':
        candidate['runtime']['code_sha256'] = '0' * 64
        seal(candidate)

    def forbidden(*args, **kwargs):
        pytest.fail('An unretained or changed parent dispatched numerical audit')

    monkeypatch.setattr(temperature_verification, 'verify', forbidden)
    execution, result = execute(registry, source, selection, _recording_file(source), VERIFY,
                                {'processing': candidate}, retained_results=retained)
    assert execution['status'] == 'refused' and result is None
    assert execution['runtime'] is None
    assert 'actually retained' in execution['refusal']['message']


def test_temperature_numeric_failure_is_a_retained_verification_result(tmp_path, monkeypatch):
    from ciw import temperature_processing
    original = temperature_processing.process_request

    def faulty_provider(request):
        report = original(request)
        report['calibrated_sensor_temperature']['samples'][0]['value_K'] += 1
        return seal(report)

    profile = temperature_config(tmp_path / 'profile')
    monkeypatch.setattr(temperature_processing, 'process_request', faulty_provider)
    host = from_profile(profile, instrument='temperature')
    response = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'faulty'})
    assert response['status'] == 'completed', response
    saved = profile.parent / 'agent-output/faulty/workspace.json'
    workspace = parse(saved.read_bytes())
    processing = next(row for row in workspace['results'] if row['operation_id'] == PROCESS)
    result = next(row for row in workspace['results'] if row['operation_id'] == VERIFY)
    assert result['data']['report']['status'] == 'FAIL'
    assert result['data']['processing_record_digest'] == processing['record_digest']
    assert result['data']['authority'] == AUTHORITY
    assert result['verification_status'] == 'not_verified' and result['verification_id'] is None
    restored = Session.from_workspace(saved, tmp_path / 'restored')
    assert restored.results[result['result_id']]['data']['report']['status'] == 'FAIL'


@pytest.mark.parametrize('fault', ['missing_parent', 'changed_parent', 'outer_authority', 'audit_authority'])
def test_temperature_restore_refuses_dependency_and_authority_tampering(tmp_path, fault):
    profile = temperature_config(tmp_path / 'profile')
    host = from_profile(profile, instrument='temperature')
    result = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'first'})
    assert result['status'] == 'completed', result
    saved = profile.parent / 'agent-output/first/workspace.json'
    workspace = parse(saved.read_bytes())
    audited = next(row for row in workspace['results'] if row['operation_id'] == VERIFY)
    if fault == 'missing_parent':
        workspace['results'] = [row for row in workspace['results'] if row['operation_id'] != PROCESS]
    elif fault == 'changed_parent':
        candidate = audited['parameters']['processing']
        candidate['runtime']['code_sha256'] = '0' * 64
        seal(candidate)
        audited['data']['processing_record_digest'] = candidate['record_digest']
        verification_execution = next(row for row in workspace['executions'] if row['operation_id'] == VERIFY)
        verification_execution['parameters'] = deepcopy(audited['parameters'])
        seal(verification_execution)
    elif fault == 'outer_authority':
        audited['data']['authority']['physical_qualification'] = 'established'
    else:
        audited['data']['report']['authority']['proof'] = 'verified'
        seal(audited['data']['report'])
    seal(audited)
    saved.write_bytes(encode(workspace))
    with pytest.raises(ValueError):
        Session.from_workspace(saved, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


def test_temperature_process_only_profile_does_not_authorize_verification(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    config = parse(profile.read_bytes())
    config['allow_operations'] = [PROCESS]
    config['max_nodes'] = 1
    profile.write_bytes(encode(config))
    graph_path = profile.parent / 'experiment.json'
    graph = parse(graph_path.read_bytes())
    graph['nodes'] = graph['nodes'][:1]
    seal(graph)
    graph_path.write_bytes(encode(graph))
    host = from_profile(profile, instrument='temperature')
    assert not host.capabilities()['catalog']['operations'][VERIFY]['agent_execution_enabled']
    result = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'legacy'})
    assert result['status'] == 'completed', result
    saved = profile.parent / 'agent-output/legacy/workspace.json'
    restored = Session.from_workspace(saved, tmp_path / 'restored')
    assert len(restored.results) == 1
    assert next(iter(restored.results.values()))['operation_id'] == PROCESS
    # Keep the original declaration identity across the addition of a verifier.
    assert restored.run['evidence_id'] == 'sha256:73e1fe826f41e49ca015d19e6f49de5fb7ae3be949e30455358af5a24d0fb473'
    assert restored.run['metadata']['manifest']['supported_operations'] == [PROCESS]


def test_temperature_advertised_verification_cannot_override_operator_grant(tmp_path):
    profile = temperature_config(tmp_path / 'profile')
    config = parse(profile.read_bytes())
    config['allow_operations'] = [PROCESS]
    profile.write_bytes(encode(config))
    host = from_profile(profile, instrument='temperature')
    assert VERIFY in host.capabilities()['catalog']['operations']
    assert not host.capabilities()['catalog']['operations'][VERIFY]['agent_execution_enabled']
    with pytest.raises(ValueError, match='not enabled'):
        host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'denied-audit'})


def test_temperature_verification_role_and_registration_are_reserved():
    from ciw.operations.schemas import register_payload_validator, validate_role
    validate_role(VERIFY, 'verification')
    with pytest.raises(ValueError, match='role contradicts'):
        validate_role(VERIFY, 'backend')
    with pytest.raises(ValueError, match='already registered'):
        register_payload_validator(VERIFY, lambda *args: None)


def test_temperature_verification_refuses_another_source_before_oracle(monkeypatch):
    from ciw import temperature_verification
    from ciw.session import _recording_file
    registry, source, selection, processing = _direct_processing()
    request = source_request(source)
    request['identity']['cycle_id'] = 'another-cycle'
    another_source = make_source(request)

    def forbidden(*args, **kwargs):
        pytest.fail('A source-mismatched processing result dispatched the oracle')

    monkeypatch.setattr(temperature_verification, 'verify', forbidden)
    execution, result = execute(registry, another_source, selection, _recording_file(another_source), VERIFY,
                                {'processing': processing}, retained_results={processing['result_id']: processing})
    assert execution['status'] == 'refused' and result is None
    assert 'source-bound' in execution['refusal']['message']


def test_temperature_restore_refuses_duplicate_verification_occurrences(tmp_path):
    from ciw.core.identities import new_identity
    profile = temperature_config(tmp_path / 'profile')
    host = from_profile(profile, instrument='temperature')
    response = host.call('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'first'})
    assert response['status'] == 'completed', response
    saved = profile.parent / 'agent-output/first/workspace.json'
    workspace = parse(saved.read_bytes())
    duplicate = deepcopy(next(row for row in workspace['results'] if row['operation_id'] == VERIFY))
    execution = deepcopy(next(row for row in workspace['executions'] if row['operation_id'] == VERIFY))
    duplicate['result_id'] = new_identity('result')
    duplicate['execution_id'] = new_identity('execution')
    execution['result_id'] = duplicate['result_id']
    execution['execution_id'] = duplicate['execution_id']
    workspace['results'].append(seal(duplicate))
    workspace['executions'].append(seal(execution))
    saved.write_bytes(encode(workspace))
    with pytest.raises(ValueError, match='Duplicate temperature verification'):
        Session.from_workspace(saved, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()
