"""Local evidence custody is not empirical admission or execution permission."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from ciw import engineering_thread_io as thread, engineering_thread as e, link_contract as c
from ciw.link_study_cli import example
from ciw.operations.runner import seal, check_seal
from ciw.session import Session
from test_engineering_thread import execute, raw, paired


def api():
    return importlib.import_module('ciw.engineering_evidence')


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture(scope='module')
def history(tmp_path_factory):
    root, review = execute(tmp_path_factory.mktemp('support-source'), 'scalar', raw(example()))
    actions = thread.next_actions(root, expected_thread_ref=review['thread_ref'], host_profile='scalar')
    return root, review, actions


@pytest.fixture
def attachment(tmp_path):
    path = tmp_path/'synthetic-reference.txt'
    # Purposely untrusted authority claims must remain opaque source text.
    data = b'SYNTHETIC TEST ONLY\r\n{"verified":true,"release_part":true}\x00\xff'
    path.write_bytes(data)
    return path, c.bytes_ref(data)


def prepare(history, attachment, out, **overrides):
    root, review, actions = history
    args = dict(expected_thread_ref=review['thread_ref'], host_profile='scalar',
                action_id=actions['actions'][0]['action_id'], attachments=[attachment], note=None)
    args.update(overrides)
    return api().prepare_evidence(root, out, **args)


def inspect(history, root, ref):
    source, review, _ = history
    return api().inspect_evidence(root, expected_packet_ref=ref, thread_directory=source,
                                  expected_thread_ref=review['thread_ref'], host_profile='scalar')


def test_exact_opaque_bytes_bound_to_original_action_without_admission(history, attachment, tmp_path):
    source, review, actions = history
    before = snapshot(source)
    out = tmp_path/'packet'
    result = prepare(history, attachment, out, note='Synthetic reference evidence for human review only')
    packet_raw = (out/'packet.json').read_bytes()
    packet = c.loads(packet_raw)
    check_seal(packet)
    assert result['packet_ref'] == c.bytes_ref(packet_raw)
    assert packet['basis'] == actions['basis']
    assert packet['action'] == actions['actions'][0]
    assert packet['attachments'][0]['sha256'] == attachment[1]
    assert (out/'artifacts/0000.bin').read_bytes() == attachment[0].read_bytes()
    assert packet['attachments'][0]['label'] == attachment[0].name
    assert str(attachment[0]) not in packet_raw.decode()
    checked = inspect(history, out, result['packet_ref'])
    assert checked['status'] == 'INTEGRITY_CHECKED_NOT_ADMITTED'
    assert checked['attachment_count'] == 1
    assert checked['finding_status'] == 'INDETERMINATE'
    assert checked['assessment_status'] == 'EVIDENCE_REQUIRED'
    assert checked['evidence_admitted'] is False
    assert checked['requirement_satisfied'] is False
    assert checked['authorizes_execution'] is False
    assert checked['new_scientific_executions'] == 0
    assert checked['scope'] == e.SCOPE
    assert snapshot(source) == before
    assert thread.next_actions(source, expected_thread_ref=review['thread_ref'], host_profile='scalar') == actions


def test_identical_retention_does_not_manufacture_independent_measurements(history, attachment, tmp_path):
    one = prepare(history, attachment, tmp_path/'one')
    two = prepare(history, attachment, tmp_path/'two')
    assert one['packet_ref'] == two['packet_ref']
    assert (tmp_path/'one/packet.json').read_bytes() == (tmp_path/'two/packet.json').read_bytes()
    assert one['new_scientific_executions'] == 0


@pytest.mark.parametrize('fault', ['thread', 'action', 'profile', 'artifact_hash'])
def test_wrong_selection_refuses_before_output(history, attachment, tmp_path, fault):
    options = {'thread': {'expected_thread_ref':'sha256:'+'0'*64},
               'action': {'action_id':'sha256:'+'0'*64},
               'profile': {'host_profile':'installation'},
               'artifact_hash': {'attachments':[(attachment[0], 'sha256:'+'0'*64)]}}
    with pytest.raises(ValueError):
        prepare(history, attachment, tmp_path/'blocked', **options[fault])
    assert not (tmp_path/'blocked').exists()


def test_equal_filenames_use_fixed_slots_not_source_paths(history, attachment, tmp_path):
    other_dir = tmp_path/'other'; other_dir.mkdir()
    second = other_dir/attachment[0].name; second.write_bytes(b'SYNTHETIC different report')
    result = prepare(history, attachment, tmp_path/'packet',
        attachments=[attachment, (second, c.bytes_ref(second.read_bytes()))])
    checked = inspect(history, tmp_path/'packet', result['packet_ref'])
    assert checked['attachment_count'] == 2
    assert (tmp_path/'packet/artifacts/0001.bin').read_bytes() == second.read_bytes()


def test_duplicate_content_not_counted_twice(history, attachment, tmp_path):
    other = tmp_path/'renamed.txt'; other.write_bytes(attachment[0].read_bytes())
    with pytest.raises(ValueError, match='Duplicate'):
        prepare(history, attachment, tmp_path/'blocked', attachments=[attachment,(other,attachment[1])])
    assert not (tmp_path/'blocked').exists()


@pytest.mark.parametrize('fault', ['empty_list','too_many','empty_file','directory','symlink','aggregate','file_bound'])
def test_bounded_regular_inputs_only(history, attachment, tmp_path, monkeypatch, fault):
    options={}
    if fault=='empty_list': options['attachments']=[]
    elif fault=='too_many': options['attachments']=[attachment]*17
    elif fault=='empty_file': attachment[0].write_bytes(b'')
    elif fault=='directory': options['attachments']=[(tmp_path,attachment[1])]
    elif fault=='symlink':
        link=tmp_path/'link'; link.symlink_to(attachment[0]); options['attachments']=[(link,attachment[1])]
    elif fault=='aggregate': monkeypatch.setattr(api(),'MAX_TOTAL_BYTES',1)
    else: monkeypatch.setattr(api(),'MAX_ARTIFACT_BYTES',1)
    with pytest.raises((ValueError,OSError)):
        prepare(history,attachment,tmp_path/'blocked',**options)
    assert not (tmp_path/'blocked').exists()


def test_no_overwrite_or_writing_inside_history(history, attachment, tmp_path):
    source,_,_=history; before=snapshot(source)
    with pytest.raises(ValueError,match='outside'):
        prepare(history,attachment,source/'support')
    out=tmp_path/'packet'; result=prepare(history,attachment,out); original=snapshot(out)
    with pytest.raises(FileExistsError): prepare(history,attachment,out)
    assert snapshot(out)==original and snapshot(source)==before
    assert out.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('fault', ['bytes','missing','extra_file','extra_dir','symlink','packet_ref'])
def test_corruption_or_incomplete_inventory_never_passes(history, attachment, tmp_path, fault):
    out=tmp_path/'packet'; result=prepare(history,attachment,out); ref=result['packet_ref']
    if fault=='bytes': (out/'artifacts/0000.bin').write_bytes(b'changed')
    elif fault=='missing': (out/'artifacts/0000.bin').unlink()
    elif fault=='extra_file': (out/'unlisted.bin').write_bytes(b'extra')
    elif fault=='extra_dir': (out/'artifacts/unlisted').mkdir()
    elif fault=='symlink':
        (out/'artifacts/0000.bin').unlink();(out/'artifacts/0000.bin').symlink_to(attachment[0])
    else: ref='sha256:'+'0'*64
    with pytest.raises((ValueError,OSError)): inspect(history,out,ref)


@pytest.mark.parametrize('fault', ['authority','qualification','scope','action','finding','basis','path','duplicate'])
def test_resealed_claims_cannot_promote_or_rebind_evidence(history, attachment, tmp_path, fault):
    out=tmp_path/'packet'; prepare(history,attachment,out)
    p=c.loads((out/'packet.json').read_bytes())
    if fault=='authority': p['authorizes_execution']=True
    elif fault=='qualification': p['requirement_satisfied']=True
    elif fault=='scope': p['scope']['physical_qualification']='PASS'
    elif fault=='action': p['action']['action_id']='sha256:'+'0'*64
    elif fault=='finding': p['action']['finding_status']='PASS'
    elif fault=='basis': p['basis']['request_ref']='sha256:'+'0'*64
    elif fault=='path': p['attachments'][0]['path']='../outside'
    else: p['attachments'].append(deepcopy(p['attachments'][0]))
    changed=raw(seal({k:v for k,v in p.items() if k!='record_digest'}))
    (out/'packet.json').write_bytes(changed)
    # Even an operator supplying the new outer hash cannot bypass semantic checks.
    with pytest.raises(ValueError): inspect(history,out,c.bytes_ref(changed))


def test_removal_of_attachment_sources_does_not_break_retained_review(history, attachment, tmp_path):
    out=tmp_path/'packet'; result=prepare(history,attachment,out); attachment[0].unlink()
    assert inspect(history,out,result['packet_ref'])['status']=='INTEGRITY_CHECKED_NOT_ADMITTED'


def test_receipt_does_not_select_files_or_execute_their_contents(history, attachment, tmp_path, monkeypatch):
    marker=tmp_path/'must-not-run'
    attachment[0].write_bytes(('from pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")\n').encode())
    attachment=(attachment[0],c.bytes_ref(attachment[0].read_bytes()))
    monkeypatch.setattr(Session,'handle',lambda *a,**k:pytest.fail('evidence handling executed Session'))
    monkeypatch.setattr(thread.production,'run_production',lambda *a,**k:pytest.fail('evidence handling dispatched'))
    result=prepare(history,attachment,tmp_path/'packet')
    inspect(history,tmp_path/'packet',result['packet_ref'])
    assert not marker.exists()


def test_frozen_attachment_bytes_survive_source_replacement(history, attachment, tmp_path, monkeypatch):
    ev=api(); original=attachment[0].read_bytes(); real=ev._write_raw
    def write(path,data):
        if Path(path).name=='0000.bin': attachment[0].write_bytes(b'replaced after review')
        return real(path,data)
    monkeypatch.setattr(ev,'_write_raw',write)
    result=prepare(history,attachment,tmp_path/'packet')
    assert (tmp_path/'packet/artifacts/0000.bin').read_bytes()==original
    inspect(history,tmp_path/'packet',result['packet_ref'])


def test_exact_recovery_keeps_evidence_binding_but_new_execution_does_not(history, attachment, tmp_path):
    source,review,_=history; out=tmp_path/'packet'; packet=prepare(history,attachment,out)
    recovered=tmp_path/'recovered'
    thread.restore_thread(source,recovered,expected_thread_ref=review['thread_ref'],host_profile='scalar')
    checked=api().inspect_evidence(out,expected_packet_ref=packet['packet_ref'],thread_directory=recovered,
        expected_thread_ref=review['thread_ref'],host_profile='scalar')
    assert checked['packet_ref']==packet['packet_ref']
    fresh_root=tmp_path/'fresh';fresh_root.mkdir()
    fresh,fresh_review=execute(fresh_root,'scalar',(source/'request.json').read_bytes(),(source/'intent.json').read_bytes())
    with pytest.raises(ValueError,match='basis|thread|source'):
        api().inspect_evidence(out,expected_packet_ref=packet['packet_ref'],thread_directory=fresh,
            expected_thread_ref=fresh_review['thread_ref'],host_profile='scalar')


def test_packet_cannot_be_submitted_as_a_computation(history, attachment, tmp_path):
    source,_,_=history;out=tmp_path/'packet';prepare(history,attachment,out)
    fake=tmp_path/'fake';fake.mkdir()
    for name in ('intent.json','request.json'):shutil.copyfile(source/name,fake/name)
    shutil.copyfile(out/'packet.json',fake/'compilation.json')
    with pytest.raises(ValueError):thread.run_packet(fake,tmp_path/'must-not-execute',
        expected_compiled_ref=c.bytes_ref((fake/'compilation.json').read_bytes()),host_profile='scalar',allow_compute=True)
    assert not (tmp_path/'must-not-execute').exists()


def test_numerical_discrepancy_can_have_support_without_becoming_pass(paired, attachment, tmp_path):
    source,review=execute(tmp_path,'installation',paired)
    actions=thread.next_actions(source,expected_thread_ref=review['thread_ref'],host_profile='installation')
    result=api().prepare_evidence(source,tmp_path/'packet',expected_thread_ref=review['thread_ref'],
        host_profile='installation',action_id=actions['actions'][0]['action_id'],attachments=[attachment])
    checked=api().inspect_evidence(tmp_path/'packet',expected_packet_ref=result['packet_ref'],thread_directory=source,
        expected_thread_ref=review['thread_ref'],host_profile='installation')
    assert checked['finding_status']=='FAIL' and checked['requirement_satisfied'] is False
    assert checked['sampled_points_to_review']==[{'frequency_hz':384.0,'status':'FAIL'}]


@pytest.mark.parametrize('after_manifest',[False,True])
def test_process_termination_does_not_turn_partial_packet_into_complete_evidence(history, attachment, tmp_path, after_manifest):
    source,review,actions=history
    control=prepare(history,attachment,tmp_path/'control')
    destination=tmp_path/'interrupted'
    code='''import os,sys,json
from pathlib import Path
from ciw import engineering_evidence as ev
x=json.loads(sys.stdin.read()); actual=ev._write_raw
def stop(path,raw):
    if Path(path).name=='packet.json':
        if x['after']:actual(path,raw)
        os._exit(73)
    actual(path,raw)
ev._write_raw=stop
ev.prepare_evidence(x['source'],x['out'],expected_thread_ref=x['thread_ref'],host_profile='scalar',action_id=x['action'],attachments=[(x['artifact'],x['artifact_ref'])])
'''
    p=subprocess.run([sys.executable,'-c',code],input=json.dumps({'source':str(source),'out':str(destination),
        'thread_ref':review['thread_ref'],'action':actions['actions'][0]['action_id'],'artifact':str(attachment[0]),
        'artifact_ref':attachment[1],'after':after_manifest}),capture_output=True,text=True,timeout=20)
    assert p.returncode==73,p.stderr
    if after_manifest:
        assert inspect(history,destination,control['packet_ref'])['new_scientific_executions']==0
    else:
        assert not (destination/'packet.json').exists()
        with pytest.raises((ValueError,OSError)):inspect(history,destination,control['packet_ref'])
    assert (destination/'artifacts/0000.bin').read_bytes()==attachment[0].read_bytes()


def test_existing_dispatcher_exposes_the_same_local_evidence_workflow(history, attachment, tmp_path, capsys):
    from ciw.production_workflow import main
    source,review,actions=history;out=tmp_path/'packet'
    args=['engineering','evidence','prepare',str(source),'--profile','scalar','--thread-ref',review['thread_ref'],
        '--action-id',actions['actions'][0]['action_id'],'--attach',str(attachment[0]),attachment[1],
        '--output-dir',str(out)]
    assert main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['evidence_admitted'] is False
    args=['engineering','evidence','inspect',str(out),'--packet-ref',result['packet_ref'],
        '--thread',str(source),'--thread-ref',review['thread_ref'],'--profile','scalar']
    assert main(args)==0
    assert json.loads(capsys.readouterr().out)['requirement_satisfied'] is False
