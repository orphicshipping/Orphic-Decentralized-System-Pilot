"""Evidence-bound continuation/recovery; genuine NET, synthetic observations only."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from ciw import engineering_thread as e, engineering_thread_io as io, link_contract as c
from ciw.link_study_cli import example
from ciw.session import Session


def raw(value):
    return (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()


def snapshot(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def completed(tmp_path):
    source = raw(example())
    intent = e.example_intent('scalar', c.bytes_ref(source))
    packet = io.compile_packet(raw(intent), source, tmp_path/'compiled', host_profile='scalar')
    result = io.run_packet(tmp_path/'compiled', tmp_path/'complete',
                          expected_compiled_ref=packet['compiled_ref'], host_profile='scalar', allow_compute=True)
    return tmp_path/'complete', result, intent, source


def proposed(root, view, intent, source, destination):
    changed=deepcopy(intent);changed['revision']+=1
    changed['request_ref']=c.bytes_ref(source)
    return io.revise(root,raw(changed),source,destination,expected_thread_ref=view['thread_ref'],
                     host_profile='scalar',reason='Synthetic reviewed continuation; no physical reacquisition')


def test_fabricated_predecessor_claim_cannot_execute(tmp_path):
    source=raw(example());intent=e.example_intent('scalar',c.bytes_ref(source));intent['revision']=2
    fiction={'previous_thread_ref':'sha256:'+'1'*64,'previous_intent_ref':'sha256:'+'2'*64,
             'previous_execution_ids':[],'previous_result_ids':[],'reason':'invented predecessor',
             'basis':'locally_reviewed_predecessor_not_signed_authority'}
    compiled=io.compile_packet(raw(intent),source,tmp_path/'forged',host_profile='scalar',continuation=fiction)
    with pytest.raises((ValueError,PermissionError)):
        io.run_packet(tmp_path/'forged',tmp_path/'must-not-run',expected_compiled_ref=compiled['compiled_ref'],
                      host_profile='scalar',allow_compute=True)
    assert not (tmp_path/'must-not-run').exists()


def test_legitimate_revision_requires_current_predecessor_review(completed,tmp_path):
    root,view,intent,source=completed
    proposal=proposed(root,view,intent,source,tmp_path/'revision')
    with pytest.raises((ValueError,PermissionError)):
        io.run_packet(tmp_path/'revision',tmp_path/'missing-predecessor',
            expected_compiled_ref=proposal['compilation']['compiled_ref'],host_profile='scalar',allow_compute=True)
    assert not (tmp_path/'missing-predecessor').exists()


def test_continuation_executes_original_graph_with_fresh_occurrences(completed,tmp_path):
    root,view,intent,source=completed;before=snapshot(root)
    proposal=proposed(root,view,intent,source,tmp_path/'revision')
    result=io.run_packet(tmp_path/'revision',tmp_path/'revised',
        expected_compiled_ref=proposal['compilation']['compiled_ref'],host_profile='scalar',
        allow_compute=True,predecessor=root)
    assert result['revision']==2 and result['evidence_id']==view['evidence_id']
    assert set(result['execution_ids']).isdisjoint(view['execution_ids'])
    assert result['status']=='EVIDENCE_REQUIRED' and result['scope']==e.SCOPE
    assert snapshot(root)==before


def test_restore_recovers_exact_occurrences_without_execution(completed,tmp_path,monkeypatch):
    root,view,_,_=completed;before=snapshot(root)
    def refuse(*a,**k):pytest.fail('Recovery executed a scientific operation')
    monkeypatch.setattr(Session,'handle',refuse)
    monkeypatch.setattr(e.production,'run_production',refuse)
    result=io.restore_thread(root,tmp_path/'recovered',expected_thread_ref=view['thread_ref'],host_profile='scalar')
    assert snapshot(tmp_path/'recovered')==before==snapshot(root)
    assert result['thread_ref']==view['thread_ref']
    assert result['active_authority_restored'] is False
    recovered=io.inspect_thread(tmp_path/'recovered',expected_thread_ref=view['thread_ref'],host_profile='scalar')
    assert recovered==view


def test_changed_predecessor_refuses_before_output(completed,tmp_path):
    root,view,intent,source=completed
    proposal=proposed(root,view,intent,source,tmp_path/'revision')
    (root/'request.json').write_text('{}')
    with pytest.raises((ValueError,PermissionError)):
        io.run_packet(tmp_path/'revision',tmp_path/'blocked',
            expected_compiled_ref=proposal['compilation']['compiled_ref'],host_profile='scalar',
            allow_compute=True,predecessor=root)
    assert not (tmp_path/'blocked').exists()


def test_restore_wrong_anchor_has_no_destination(completed,tmp_path):
    root,view,_,_=completed
    with pytest.raises(ValueError):
        io.restore_thread(root,tmp_path/'not-created',expected_thread_ref='sha256:'+'0'*64,host_profile='scalar')
    assert not (tmp_path/'not-created').exists()


def forged_packet(root, view, intent, source, destination, *, field=None, change=None):
    new=deepcopy(intent);new['revision']+=1
    claim={'previous_thread_ref':view['thread_ref'],'previous_intent_ref':view['intent_ref'],
           'previous_execution_ids':view['execution_ids'],'previous_result_ids':view['result_ids'],
           'reason':'Synthetic adversarial lineage fixture',
           'basis':'locally_reviewed_predecessor_not_signed_authority'}
    if field is not None:
        from ciw.core.identities import new_identity
        if field.endswith('_ids'):
            kind='execution' if field=='previous_execution_ids' else 'result'
            claim[field]=[new_identity(kind)]
        else:claim[field]='sha256:'+'a'*64
    if change=='identity':new['intent_id']='synthetic.other-program'
    elif change=='revision':new['revision']=4
    elif change=='requirements':new['requirements'][0]['requirement_id']='relabeled'
    elif change=='budget':new['constraints']['max_operations']=8
    elif change=='basis':
        data=json.loads(source);data['model']['residual_limit']=10.0;source=raw(data)
        new['request_ref']=c.bytes_ref(source)
    return io.compile_packet(raw(new),source,destination,host_profile='scalar',continuation=claim)


@pytest.mark.parametrize('field',['previous_thread_ref','previous_intent_ref','previous_execution_ids','previous_result_ids'])
def test_resealed_false_lineage_is_not_reviewed_evidence(completed,tmp_path,field):
    root,view,intent,source=completed
    p=forged_packet(root,view,intent,source,tmp_path/'forged',field=field)
    with pytest.raises(ValueError):
        io.run_packet(tmp_path/'forged',tmp_path/'blocked',expected_compiled_ref=p['compiled_ref'],
                      host_profile='scalar',allow_compute=True,predecessor=root)
    assert not (tmp_path/'blocked').exists()


@pytest.mark.parametrize('change',['identity','revision','requirements','budget','basis'])
def test_compile_api_cannot_bypass_revision_change_controls(completed,tmp_path,change):
    root,view,intent,source=completed
    p=forged_packet(root,view,intent,source,tmp_path/'forged',change=change)
    with pytest.raises(ValueError):
        io.run_packet(tmp_path/'forged',tmp_path/'blocked',expected_compiled_ref=p['compiled_ref'],
                      host_profile='scalar',allow_compute=True,predecessor=root)
    assert not (tmp_path/'blocked').exists()


def test_equal_input_different_occurrences_cannot_replace_parent(completed,tmp_path):
    root,view,intent,source=completed
    p=proposed(root,view,intent,source,tmp_path/'revision')
    packet=io.compile_packet(raw(intent),source,tmp_path/'same-input',host_profile='scalar')
    other=io.run_packet(tmp_path/'same-input',tmp_path/'other-occurrence',
        expected_compiled_ref=packet['compiled_ref'],host_profile='scalar',allow_compute=True)
    assert other['evidence_id']==view['evidence_id'] and other['thread_ref']!=view['thread_ref']
    with pytest.raises(ValueError):
        io.run_packet(tmp_path/'revision',tmp_path/'blocked',expected_compiled_ref=p['compilation']['compiled_ref'],
                      host_profile='scalar',allow_compute=True,predecessor=tmp_path/'other-occurrence')
    assert not (tmp_path/'blocked').exists()


def test_initial_revision_rejects_unrelated_predecessor(completed,tmp_path):
    root,view,intent,source=completed
    p=io.compile_packet(raw(intent),source,tmp_path/'initial',host_profile='scalar')
    with pytest.raises(ValueError):
        io.run_packet(tmp_path/'initial',tmp_path/'blocked',expected_compiled_ref=p['compiled_ref'],
                      host_profile='scalar',allow_compute=True,predecessor=root)
    assert not (tmp_path/'blocked').exists()


def test_recovery_does_not_turn_history_into_compute_permission(completed,tmp_path,monkeypatch):
    root,view,intent,source=completed
    result=io.restore_thread(root,tmp_path/'recovered',expected_thread_ref=view['thread_ref'],host_profile='scalar')
    p=proposed(tmp_path/'recovered',view,intent,source,tmp_path/'revision')
    def forbidden(*a,**k):pytest.fail('Read of source or predecessor before host permission')
    monkeypatch.setattr(io,'read_bytes',forbidden)
    with pytest.raises(PermissionError):
        io.run_packet(tmp_path/'revision',tmp_path/'blocked',expected_compiled_ref=p['compilation']['compiled_ref'],
                      host_profile='scalar',predecessor=tmp_path/'recovered')
    assert result['active_authority_restored'] is False and not (tmp_path/'blocked').exists()


@pytest.mark.parametrize('damage',['changed','missing','extra','manifest','symlink'])
def test_damaged_evidence_cannot_be_recovered_as_complete(completed,tmp_path,damage):
    root,view,_,_=completed;copy=tmp_path/'damaged';shutil.copytree(root,copy)
    if damage=='changed':(copy/'request.json').write_text('{}')
    elif damage=='missing':(copy/'run/session/workspace.json').unlink()
    elif damage=='extra':(copy/'extra.json').write_text('{}')
    elif damage=='manifest':(copy/'thread.json').write_text('{}')
    else:
        (copy/'request.json').unlink();(copy/'request.json').symlink_to(root/'request.json')
    with pytest.raises((ValueError,OSError)):
        io.restore_thread(copy,tmp_path/'blocked',expected_thread_ref=view['thread_ref'],host_profile='scalar')
    assert not (tmp_path/'blocked').exists()


def test_restore_no_clobber_or_nested_mutation(completed,tmp_path):
    root,view,_,_=completed;before=snapshot(root)
    for destination in [root,root/'child',root/'../complete/child']:
        with pytest.raises(ValueError):
            io.restore_thread(root,destination,expected_thread_ref=view['thread_ref'],host_profile='scalar')
    target=tmp_path/'existing';target.mkdir();(target/'sentinel').write_bytes(b'unchanged')
    with pytest.raises(FileExistsError):
        io.restore_thread(root,target,expected_thread_ref=view['thread_ref'],host_profile='scalar')
    assert (target/'sentinel').read_bytes()==b'unchanged' and snapshot(root)==before


def test_restore_freezes_source_before_writing_destination(completed,tmp_path,monkeypatch):
    root,view,_,_=completed;expected=snapshot(root);dest=tmp_path/'frozen-copy';writer=io._write_raw
    def replace_after_freeze(path,data):
        if Path(path).is_relative_to(dest) and (root/'request.json').exists():
            (root/'request.json').unlink();(root/'thread.json').write_bytes(b'changed')
        writer(path,data)
    monkeypatch.setattr(io,'_write_raw',replace_after_freeze)
    io.restore_thread(root,dest,expected_thread_ref=view['thread_ref'],host_profile='scalar')
    assert snapshot(dest)==expected
    assert io.inspect_thread(dest,expected_thread_ref=view['thread_ref'],host_profile='scalar')==view


def test_incomplete_recovery_has_no_completion_manifest(completed,tmp_path,monkeypatch):
    root,view,_,_=completed;before=snapshot(root);dest=tmp_path/'interrupted';writer=io._write_raw
    def fail_copy(path,data):
        writer(path,data)
        if Path(path).is_relative_to(dest):raise OSError('synthetic storage fault')
    monkeypatch.setattr(io,'_write_raw',fail_copy)
    with pytest.raises(OSError,match='storage fault'):
        io.restore_thread(root,dest,expected_thread_ref=view['thread_ref'],host_profile='scalar')
    assert snapshot(root)==before and not (dest/'thread.json').exists()
    monkeypatch.setattr(io,'_write_raw',writer)
    with pytest.raises(OSError):io.inspect_thread(dest,expected_thread_ref=view['thread_ref'],host_profile='scalar')
    assert io.restore_thread(root,tmp_path/'fresh-recovery',expected_thread_ref=view['thread_ref'],host_profile='scalar')['new_scientific_executions']==0


@pytest.mark.parametrize('phase',['before_manifest','after_manifest'])
def test_real_process_exit_at_recovery_boundary(completed,tmp_path,phase):
    import subprocess,sys
    root,view,_,_=completed;expected=snapshot(root);dest=tmp_path/phase
    script='''from pathlib import Path
import os,sys
from ciw import engineering_thread_io as io
source,dest,ref,phase=sys.argv[1:];target=Path(dest);write=io._write_raw
count=[0]
def crash(path,data):
    write(path,data)
    if Path(path).is_relative_to(target):
        count[0]+=1
        if (phase=="before_manifest" and count[0]==2) or (phase=="after_manifest" and Path(path)==target/"thread.json"):
            os._exit(73)
io._write_raw=crash
io.restore_thread(source,target,expected_thread_ref=ref,host_profile="scalar")
'''
    p=subprocess.run([sys.executable,'-c',script,str(root),str(dest),view['thread_ref'],phase],capture_output=True,text=True,timeout=30)
    assert p.returncode==73,p.stderr
    assert snapshot(root)==expected
    if phase=='before_manifest':
        assert not (dest/'thread.json').exists()
        with pytest.raises(OSError):io.inspect_thread(dest,expected_thread_ref=view['thread_ref'],host_profile='scalar')
    else:
        assert snapshot(dest)==expected
        assert io.inspect_thread(dest,expected_thread_ref=view['thread_ref'],host_profile='scalar')==view


def test_cli_recovery_and_continued_run(completed,tmp_path,capsys):
    from ciw.production_workflow import main
    root,view,intent,source=completed
    assert main(['engineering','restore',str(root),'--profile','scalar','--thread-ref',view['thread_ref'],
                 '--output-dir',str(tmp_path/'copy')])==0
    restored=json.loads(capsys.readouterr().out)
    assert restored['status']=='RESTORED_HISTORICAL_EVIDENCE'
    p=proposed(root,view,intent,source,tmp_path/'next')
    args=['engineering','run',str(tmp_path/'next'),'--profile','scalar','--compiled-ref',p['compilation']['compiled_ref'],
          '--allow-compute','--output-dir',str(tmp_path/'run')]
    assert main(args)==1 and not (tmp_path/'run').exists()
    capsys.readouterr()
    assert main(args+['--predecessor',str(tmp_path/'copy')])==2
    revised=json.loads(capsys.readouterr().out)
    assert revised['revision']==2 and revised['scope']['part_release'] is False


def test_existing_holds_cannot_disappear_through_restore_or_continuation(completed,tmp_path):
    root,view,intent,source=completed
    io.restore_thread(root,tmp_path/'copy',expected_thread_ref=view['thread_ref'],host_profile='scalar')
    p=proposed(tmp_path/'copy',view,intent,source,tmp_path/'next')
    result=io.run_packet(tmp_path/'next',tmp_path/'run',expected_compiled_ref=p['compilation']['compiled_ref'],
                        host_profile='scalar',allow_compute=True,predecessor=tmp_path/'copy')
    for r in [view,result]:
        unknown=[c for c in r['checks'] if c['check']['check_id'].startswith('industrial.')]
        assert len(unknown)==5 and all(c['status']=='INDETERMINATE' for c in unknown)
        assert r['scope']['physical_qualification']=='HELD' and r['scope']['verification_id'] is None


def test_installation_remount_continuation_and_exact_restore(tmp_path):
    from test_engineering_thread import mutate_pair
    baseline=io.demo(tmp_path/'installation',host_profile='installation')
    root=tmp_path/'installation/execution';view=baseline['review'];source=(root/'request.json').read_bytes()
    def remount(p):p['installation']['occurrence_id']='synthetic.next-installation';p['revision']+=1
    changed=mutate_pair(source,plan=remount)
    intent=json.loads((root/'intent.json').read_bytes());intent.update(revision=2,request_ref=c.bytes_ref(changed))
    p=io.revise(root,raw(intent),changed,tmp_path/'next',expected_thread_ref=view['thread_ref'],
                host_profile='installation',reason='New synthetic installation evidence; not a physical experiment')
    result=io.run_packet(tmp_path/'next',tmp_path/'assessed',expected_compiled_ref=p['compilation']['compiled_ref'],
                        host_profile='installation',allow_compute=True,predecessor=root)
    assert result['evidence_id']!=view['evidence_id']
    assert result['status']=='REQUIREMENTS_NOT_MET' and result['scope']['physical_qualification']=='HELD'
    io.restore_thread(tmp_path/'assessed',tmp_path/'restored',expected_thread_ref=result['thread_ref'],host_profile='installation')
    assert snapshot(tmp_path/'restored')==snapshot(tmp_path/'assessed')
