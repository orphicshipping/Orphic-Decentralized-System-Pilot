"""Source-cited review declarations do not grant empirical or machine authority."""
from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest

from ciw import engineering_evidence as evidence, engineering_thread_io as thread, link_contract as c
from ciw.session import Session
from ciw.operations.runner import seal
from test_ncp_supporting_evidence import history, attachment, snapshot, prepare as support
from test_engineering_thread import execute, raw


def api():
    return importlib.import_module('ciw.engineering_evidence_assessment')


@pytest.fixture
def packet(history, attachment, tmp_path):
    folder=tmp_path/'packet'
    result=support(history,attachment,folder)
    context=dict(packet_directory=folder,expected_packet_ref=result['packet_ref'],
                 thread_directory=history[0],expected_thread_ref=history[1]['thread_ref'],host_profile='scalar')
    return context, attachment, result


def draft(packet):
    return api().template_assessment(**packet[0])


def completed(packet, status='SUPPORTED'):
    value=draft(packet)
    payload=packet[1][0].read_bytes()
    for item in value['checks'].values():
        item.update(status=status,rationale='Synthetic declared review for test only.',
            citations=[{'artifact_ref':packet[1][1],'start_byte':0,'end_byte':9,'span_ref':c.bytes_ref(payload[:9])}])
    return value


def record(packet, out, value):
    data=raw(value)
    return api().record_assessment(data,out,expected_input_ref=c.bytes_ref(data),**packet[0])


def inspect(packet, folder, ref):
    return api().inspect_assessment(folder,expected_assessment_ref=ref,**packet[0])


def test_template_defaults_unknown_and_binds_original_finding(packet):
    value=draft(packet)
    assert value['packet_ref']==packet[0]['expected_packet_ref']
    assert value['action_id']==packet[2]['action_id']
    assert set(value['checks'])==set(api().DIMENSIONS)
    assert all(x=={'status':'NOT_ASSESSED','rationale':'Not assessed.','citations':[]} for x in value['checks'].values())


@pytest.mark.parametrize('status,outcome',[('SUPPORTED','DECLARATIONS_COMPLETE_NOT_ADMITTED'),
    ('CONTRADICTED','CONTRADICTIONS_DECLARED'),('NOT_ASSESSED','REVIEW_INCOMPLETE')])
def test_actual_review_roundtrip_without_promoting_findings(packet,tmp_path,status,outcome):
    context,_,source=packet
    before={str(p):snapshot(p) for p in (context['packet_directory'],context['thread_directory'])}
    value=completed(packet,status)
    result=record(packet,tmp_path/'review',value)
    checked=inspect(packet,tmp_path/'review',result['assessment_ref'])
    assert checked==result
    assert result['status']==outcome
    assert result['checks']==value['checks']
    assert result['finding_status']=='INDETERMINATE'
    assert result['evidence_admitted'] is False and result['requirement_satisfied'] is False
    assert result['authorizes_execution'] is False and result['new_scientific_executions']==0
    assert result['semantic_support_checked'] is False
    assert result['reviewer_authority']=='NOT_ESTABLISHED'
    assert result['scope']['physical_qualification']=='HELD'
    assert (tmp_path/'review/assessment-input.json').read_bytes()==raw(value)
    assert before=={str(p):snapshot(p) for p in (context['packet_directory'],context['thread_directory'])}


def test_mixed_missing_and_contradiction_does_not_collapse_into_complete(packet,tmp_path):
    value=draft(packet);chosen=next(iter(value['checks']))
    value['checks'][chosen]=completed(packet,'CONTRADICTED')['checks'][chosen]
    r=record(packet,tmp_path/'review',value)
    assert r['status']=='CONTRADICTIONS_DECLARED'
    assert r['counts']=={'supported':0,'contradicted':1,'not_assessed':5}
    assert len(r['unresolved_dimensions'])==5
    assert r['contradicted_dimensions']==[chosen]


@pytest.mark.parametrize('mutation',['packet','action','extra','dimension','status','no_citation',
    'unknown_artifact','negative','bool_start','reversed','past_end','wrong_span','duplicate','too_many','empty_reason'])
def test_invalid_or_unbound_review_refuses_before_writing(packet,tmp_path,mutation):
    value=completed(packet);item=value['checks'][next(iter(value['checks']))];cite=item['citations'][0]
    if mutation=='packet':value['packet_ref']='sha256:'+'0'*64
    elif mutation=='action':value['action_id']='sha256:'+'0'*64
    elif mutation=='extra':value['verified']=True
    elif mutation=='dimension':value['checks']['automatic_release']=value['checks'].pop(next(iter(value['checks'])))
    elif mutation=='status':item['status']='PASS'
    elif mutation=='no_citation':item['citations']=[]
    elif mutation=='unknown_artifact':cite['artifact_ref']='sha256:'+'0'*64
    elif mutation=='negative':cite['start_byte']=-1
    elif mutation=='bool_start':cite['start_byte']=False
    elif mutation=='reversed':cite['end_byte']=0
    elif mutation=='past_end':cite['end_byte']=1000000
    elif mutation=='wrong_span':cite['span_ref']='sha256:'+'0'*64
    elif mutation=='duplicate':item['citations']*=2
    elif mutation=='too_many':item['citations']*=5
    else:item['rationale']=''
    with pytest.raises((ValueError,TypeError,KeyError)):record(packet,tmp_path/'blocked',value)
    assert not (tmp_path/'blocked').exists()


def test_wrong_external_input_and_record_hashes_refuse(packet,tmp_path):
    data=raw(completed(packet))
    with pytest.raises(ValueError):api().record_assessment(data,tmp_path/'blocked',expected_input_ref='sha256:'+'0'*64,**packet[0])
    assert not (tmp_path/'blocked').exists()
    r=record(packet,tmp_path/'review',completed(packet))
    with pytest.raises(ValueError):inspect(packet,tmp_path/'review','sha256:'+'0'*64)


@pytest.mark.parametrize('fault',['status','authority','finding','counts','basis','extra_file','input','missing'])
def test_resealed_summary_or_changed_input_cannot_pass_reinspection(packet,tmp_path,fault):
    folder=tmp_path/'review';r=record(packet,folder,completed(packet));p=folder/'assessment.json'
    if fault in ('extra_file','input','missing'):
        if fault=='extra_file':(folder/'extra').write_text('extra')
        elif fault=='input':(folder/'assessment-input.json').write_bytes(raw(draft(packet)))
        else:(folder/'assessment-input.json').unlink()
        ref=r['assessment_ref']
    else:
        obj=c.loads(p.read_bytes())
        if fault=='status':obj['status']='QUALIFIED'
        elif fault=='authority':obj['authorizes_execution']=True
        elif fault=='finding':obj['finding_status']='PASS'
        elif fault=='counts':obj['counts']['supported']=900
        else:obj['basis']['request_ref']='sha256:'+'0'*64
        obj=seal({k:v for k,v in obj.items() if k!='record_digest'});data=raw(obj);p.write_bytes(data);ref=c.bytes_ref(data)
    with pytest.raises((ValueError,OSError)):inspect(packet,folder,ref)


def test_byte_offsets_refer_to_bytes_not_unicode_characters(packet,tmp_path):
    context,attachment,_=packet
    # Binary payload is allowed and never interpreted as text or code.
    content=attachment[0].read_bytes()
    value=completed(packet)
    cite={'artifact_ref':attachment[1],'start_byte':len(content)-2,'end_byte':len(content),'span_ref':c.bytes_ref(content[-2:])}
    for row in value['checks'].values():row['citations']=[cite]
    result=record(packet,tmp_path/'review',value)
    assert result['cited_artifact_count']==1
    assert result['semantic_support_checked'] is False


def test_same_source_file_is_read_once_for_all_review_citations(packet,tmp_path,monkeypatch):
    mod=api();actual=mod.read_bytes;reads=[]
    def read(path,**kwargs):
        if Path(path).name=='0000.bin':reads.append(str(path))
        return actual(path,**kwargs)
    monkeypatch.setattr(mod,'read_bytes',read)
    result=record(packet,tmp_path/'review',completed(packet))
    assert result['cited_artifact_count']==1 and len(reads)==1


def test_replacement_between_packet_validation_and_citation_use_is_detected(packet,tmp_path,monkeypatch):
    value=completed(packet);mod=api();actual=mod.evidence._read_packet
    def read(*a,**kw):
        result=actual(*a,**kw)
        (packet[0]['packet_directory']/'artifacts/0000.bin').write_bytes(b'X'*packet[2]['attachment_bytes'])
        return result
    monkeypatch.setattr(mod.evidence,'_read_packet',read)
    with pytest.raises(ValueError):record(packet,tmp_path/'blocked',value)
    assert not (tmp_path/'blocked').exists()


def test_no_dispatch_no_overwrite_no_write_inside_originals(packet,tmp_path,monkeypatch):
    monkeypatch.setattr(Session,'handle',lambda *a,**kw:pytest.fail('review dispatched Session'))
    value=completed(packet)
    for original in (packet[0]['packet_directory'],packet[0]['thread_directory']):
        with pytest.raises(ValueError):record(packet,original/'review',value)
    out=tmp_path/'review';r=record(packet,out,value);before=snapshot(out)
    with pytest.raises(FileExistsError):record(packet,out,value)
    assert snapshot(out)==before
    from ncp_test_permissions import assert_private_directory
    assert_private_directory(out)


def test_exact_recovery_keeps_review_binding_but_fresh_execution_does_not(packet,tmp_path):
    context=packet[0];out=tmp_path/'review';r=record(packet,out,completed(packet))
    recovered=tmp_path/'recovered'
    thread.restore_thread(context['thread_directory'],recovered,expected_thread_ref=context['expected_thread_ref'],host_profile='scalar')
    assert api().inspect_assessment(out,expected_assessment_ref=r['assessment_ref'],**{**context,'thread_directory':recovered})==r
    fresh=tmp_path/'fresh';fresh.mkdir()
    src=context['thread_directory'];root,new=execute(fresh,'scalar',(src/'request.json').read_bytes(),(src/'intent.json').read_bytes())
    with pytest.raises(ValueError):api().inspect_assessment(out,expected_assessment_ref=r['assessment_ref'],**{**context,'thread_directory':root,'expected_thread_ref':new['thread_ref']})


def test_partial_write_does_not_create_completion_record(packet,tmp_path,monkeypatch):
    mod=api();actual=mod._write_raw
    def fail(path,data):
        if Path(path).name=='assessment.json':raise OSError('synthetic interrupted write')
        return actual(path,data)
    monkeypatch.setattr(mod,'_write_raw',fail)
    with pytest.raises(OSError):record(packet,tmp_path/'partial',completed(packet))
    assert (tmp_path/'partial/assessment-input.json').is_file()
    assert not (tmp_path/'partial/assessment.json').exists()


def test_existing_ncp_dispatcher_exposes_review_family(packet,tmp_path,capsys):
    from ciw.production_workflow import main
    ctx=packet[0]
    common=[str(ctx['packet_directory']),'--packet-ref',ctx['expected_packet_ref'],'--thread',str(ctx['thread_directory']),
            '--thread-ref',ctx['expected_thread_ref'],'--profile','scalar']
    assert main(['engineering','evidence','review','template',*common])==0
    value=json.loads(capsys.readouterr().out);data=raw(value);input_path=tmp_path/'input.json';input_path.write_bytes(data)
    assert main(['engineering','evidence','review','record',*common,'--input',str(input_path),'--input-ref',c.bytes_ref(data),'--output-dir',str(tmp_path/'review')])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='REVIEW_INCOMPLETE'
    assert main(['engineering','evidence','review','inspect',*common,'--assessment',str(tmp_path/'review'),'--assessment-ref',result['assessment_ref']])==0
    assert json.loads(capsys.readouterr().out)==result
