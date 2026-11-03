"""NCP aliases and read-only follow-up proposals over actual retained threads."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from ciw import engineering_thread as e, engineering_thread_io as io, link_contract as c
from ciw.link_study_cli import example
from ciw.operations.runner import check_seal
from ciw.session import Session
from test_engineering_thread import execute, raw, paired, mutate_pair


@pytest.fixture(scope='module')
def scalar_history(tmp_path_factory):
    folder = tmp_path_factory.mktemp('ncp-history')
    root, review = execute(folder, 'scalar', raw(example()))
    return root, review


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_ncp_console_alias_uses_exact_legacy_dispatcher():
    project = tomllib.loads((Path(__file__).parents[1]/'pyproject.toml').read_text())['project']
    assert project['scripts']['ncp'] == project['scripts']['net'] == 'ciw.net:main'
    assert project['name'] == 'computational-instrumentation-workbench'
    assert project['urls']['Repository'].endswith('/Orphic-Decentralized-System-Protocol')
    assert project['dependencies'] == ['numpy==2.4.3', 'websockets==16.0']


def test_passing_numerics_propose_five_evidence_tasks_not_more_simulations(scalar_history):
    root, review = scalar_history
    result = io.next_actions(root, expected_thread_ref=review['thread_ref'], host_profile='scalar')
    check_seal(result)
    assert result['status'] == 'FOLLOW_UP_PROPOSED_NOT_AUTHORIZED'
    assert result['assessment_status'] == 'EVIDENCE_REQUIRED'
    assert [x['predicate'] for x in result['actions']] == list(e.OBLIGATIONS)
    assert all(x['action_kind'] == 'ASSEMBLE_EMPIRICAL_EVIDENCE' for x in result['actions'])
    assert all(x['admission_status'] == 'QUALIFIED_IMPORTER_NOT_IMPLEMENTED' for x in result['actions'])
    assert result['counts'] == {'satisfied': 2, 'failed': 0, 'indeterminate': 5, 'proposed': 5}
    assert all(x['required_evidence'] for x in result['actions'])
    assert result['execution_plan_generated'] is False and result['automatic_retry'] is False
    assert result['authorizes_execution'] is False and result['scope'] == e.SCOPE
    assert result['metrics'] == {'estimated_cost': None, 'expected_information_gain': None}
    assert result['basis']['execution_ids'] == review['execution_ids']
    assert result['basis']['result_ids'] == review['result_ids']
    assert result['basis']['request_ref'] == review['request_ref']


def test_actions_bind_original_checks_and_results(scalar_history):
    root, review = scalar_history
    result = io.next_actions(root, expected_thread_ref=review['thread_ref'], host_profile='scalar')
    checks = {x['check']['check_id']:x for x in review['checks']}
    assert len({x['action_id'] for x in result['actions']}) == 5
    for action in result['actions']:
        source = checks[action['requirement_id']]
        assert action['finding_ref'] == c.content_identity(source)
        assert action['source_result_id'] == source['source_result_id']
        assert action['source_record_digest'] == source['source_record_digest']
        assert action['finding_status'] == source['status']
        assert action['requested_capability'] is None
        assert action['executable'] is False


def test_numerical_failure_is_not_mislabeled_as_missing_evidence(paired, tmp_path):
    root, review = execute(tmp_path, 'installation', paired)
    result = io.next_actions(root, expected_thread_ref=review['thread_ref'], host_profile='installation')
    action = result['actions'][0]
    assert result['assessment_status'] == 'REQUIREMENTS_NOT_MET'
    assert action['action_kind'] == 'REVIEW_NUMERICAL_DISCREPANCY'
    assert action['finding_status'] == 'FAIL'
    assert action['sampled_points_to_review'] == [{'frequency_hz':384.0, 'status':'FAIL'}]
    assert action['cause_identified'] is False
    assert result['counts'] == {'satisfied':0, 'failed':1, 'indeterminate':5, 'proposed':6}


@pytest.mark.parametrize('kind', ['unsampled', 'flagged', 'unknown_timing'])
def test_unresolved_analysis_stays_distinct_from_numerical_failure(paired, tmp_path, kind):
    source = paired
    if kind == 'flagged': source = mutate_pair(source, flag='sensor_fault')
    if kind == 'unknown_timing':
        source = mutate_pair(source, plan=lambda p:p['analysis'].update(phase_skew_bound_s=None))
    intent = e.example_intent('installation',c.bytes_ref(source))
    if kind == 'unsampled': intent['requirements'][0]['parameters']['frequencies_hz']=[33.]
    root, review = execute(tmp_path, 'installation', source, raw(intent))
    result = io.next_actions(root, expected_thread_ref=review['thread_ref'], host_profile='installation')
    assert result['assessment_status'] == 'ANALYSIS_UNRESOLVED'
    assert result['actions'][0]['action_kind'] == 'REVIEW_ANALYSIS_PREREQUISITES'
    assert result['actions'][0]['finding_status'] == 'INDETERMINATE'
    assert result['scope']['physical_qualification'] == 'HELD'
    assert not any(x['action_kind']=='REVIEW_NUMERICAL_DISCREPANCY' for x in result['actions'])


def test_read_only_review_does_not_execute_or_change_history(scalar_history, monkeypatch):
    root, review = scalar_history
    before = snapshot(root)
    monkeypatch.setattr(Session, 'handle', lambda *a,**k:pytest.fail('review dispatched execution'))
    monkeypatch.setattr(io.production, 'run_production', lambda *a,**k:pytest.fail('review ran controller'))
    first = io.next_actions(root, expected_thread_ref=review['thread_ref'], host_profile='scalar')
    second = io.next_actions(root, expected_thread_ref=review['thread_ref'], host_profile='scalar')
    assert first == second and snapshot(root) == before
    assert first['new_scientific_executions'] == 0
    assert first['active_authority_restored'] is False


def test_replayed_occurrences_produce_different_worklist_identity(scalar_history,tmp_path):
    root, review = scalar_history
    other, other_review = execute(tmp_path,'scalar',(root/'request.json').read_bytes(),(root/'intent.json').read_bytes())
    a=io.next_actions(root,expected_thread_ref=review['thread_ref'],host_profile='scalar')
    b=io.next_actions(other,expected_thread_ref=other_review['thread_ref'],host_profile='scalar')
    assert a['basis']['request_ref'] == b['basis']['request_ref']
    assert a['record_digest'] != b['record_digest']
    assert {x['action_id'] for x in a['actions']}.isdisjoint(x['action_id'] for x in b['actions'])


def test_recovery_preserves_worklist_identity(scalar_history,tmp_path):
    root, review=scalar_history
    io.restore_thread(root,tmp_path/'recovered',expected_thread_ref=review['thread_ref'],host_profile='scalar')
    assert io.next_actions(root,expected_thread_ref=review['thread_ref'],host_profile='scalar') == io.next_actions(
        tmp_path/'recovered',expected_thread_ref=review['thread_ref'],host_profile='scalar')


@pytest.mark.parametrize('fault',['wrong_ref','corrupt_request','extra_artifact','wrong_profile'])
def test_unverified_history_cannot_generate_actions(scalar_history,tmp_path,fault):
    import shutil
    root, review=scalar_history
    dest=tmp_path/'source';shutil.copytree(root,dest)
    ref=review['thread_ref'];profile='scalar'
    if fault=='wrong_ref':ref='sha256:'+'0'*64
    if fault=='corrupt_request':(dest/'request.json').write_bytes(b'{}')
    if fault=='extra_artifact':(dest/'invented.json').write_bytes(b'{"verified":true}')
    if fault=='wrong_profile':profile='installation'
    with pytest.raises(ValueError):io.next_actions(dest,expected_thread_ref=ref,host_profile=profile)


def test_proposal_cli_does_not_authorize_execution_or_write_into_evidence(scalar_history,tmp_path,capsys):
    root, review=scalar_history
    args=['next',str(root),'--profile','scalar','--thread-ref',review['thread_ref']]
    before=snapshot(root)
    assert io.main(args)==0
    report=json.loads(capsys.readouterr().out)
    assert report['authorizes_execution'] is False
    assert io.main(args+['--output',str(root/'actions.json')])==1
    assert 'REFUSED' in capsys.readouterr().err
    assert snapshot(root)==before
    out=tmp_path/'next.json'
    assert io.main(args+['--output',str(out)])==0
    capsys.readouterr()
    assert json.loads(out.read_bytes()) == report
    assert io.main(args+['--output',str(out)])==1
    assert json.loads(out.read_bytes()) == report


def test_proposals_are_not_executable_compilations(scalar_history,tmp_path):
    root, review=scalar_history
    actions=io.next_actions(root,expected_thread_ref=review['thread_ref'],host_profile='scalar')
    folder=tmp_path/'not-compiled';folder.mkdir()
    (folder/'compilation.json').write_bytes(raw(actions))
    (folder/'intent.json').write_bytes((root/'intent.json').read_bytes())
    (folder/'request.json').write_bytes((root/'request.json').read_bytes())
    with pytest.raises(ValueError):io.run_packet(folder,tmp_path/'must-not-run',
        expected_compiled_ref=c.bytes_ref(raw(actions)),host_profile='scalar',allow_compute=True)
    assert not (tmp_path/'must-not-run').exists()


def test_next_command_is_deterministic_across_processes(scalar_history):
    root,review=scalar_history
    results=[]
    for seed in ('7','8531'):
        p=subprocess.run([sys.executable,'-m','ciw.net','production','engineering','next',str(root),
            '--profile','scalar','--thread-ref',review['thread_ref']],env={**os.environ,'PYTHONHASHSEED':seed},
            capture_output=True,text=True,timeout=30)
        assert p.returncode==0,p.stderr
        results.append(json.loads(p.stdout))
    assert results[0]==results[1]


def test_each_action_identity_commits_its_content_and_source_thread(scalar_history):
    root,review=scalar_history
    result=io.next_actions(root,expected_thread_ref=review['thread_ref'],host_profile='scalar')
    for action in result['actions']:
        content={k:v for k,v in action.items() if k!='action_id'}
        assert action['action_id'] == c.content_identity({'thread_ref':review['thread_ref'],'action':content})
        changed=deepcopy(content);changed['title']='A different recommendation'
        assert action['action_id'] != c.content_identity({'thread_ref':review['thread_ref'],'action':changed})


def test_wrong_external_anchor_produces_no_saved_proposal(scalar_history,tmp_path,capsys):
    root,_=scalar_history
    out=tmp_path/'must-not-exist.json'
    assert io.main(['next',str(root),'--profile','scalar','--thread-ref','sha256:'+'0'*64,
                    '--output',str(out)])==1
    assert not out.exists()
    assert json.loads(capsys.readouterr().err)['status']=='REFUSED'
