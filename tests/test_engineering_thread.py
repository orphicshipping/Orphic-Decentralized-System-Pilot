"""Actual existing controller/Link graphs; all input measurements are synthetic."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import socket
import subprocess
import sys

import pytest

from ciw import engineering_thread as e, engineering_thread_io as io
from ciw import link_contract as c, link_study as scalar, link_installation as installation
from ciw.link_study_cli import example as scalar_example
from ciw.link_installation_example import fixture
from ciw.link_gateway import capture
from ciw.link_protocol import encode, decode_one
from ciw.link_store import Journal
from ciw.operations.runner import seal
from ciw.session import Session


def raw(value): return (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()


def reseal(value): return seal({k:v for k,v in value.items() if k!='record_digest'})


@pytest.fixture(scope='module')
def paired(tmp_path_factory):
    directory=tmp_path_factory.mktemp('source')
    device, plan, plan_raw, records=fixture(compliant=True)
    with Journal(directory/'retained.sqlite',create=True) as journal:
        journal.append(encode('manifest',c.bounded(device)),expected_manifest=device)
        for rec in records: journal.append(encode('observation',c.bounded(rec)),expected_manifest=device)
        source=capture(journal,expected_manifest=device,boot_id=plan['boot_id'])
    cap_raw=raw(source)
    return raw(installation.prepare(plan_raw,cap_raw,expected_plan_ref=c.bytes_ref(plan_raw),expected_capture_ref=c.bytes_ref(cap_raw)))


def intent(name, source): return raw(e.example_intent(name,c.bytes_ref(source)))


def execute(tmp_path, name, source, intent_raw=None):
    intent_raw=intent_raw or intent(name,source)
    compiled=io.compile_packet(intent_raw,source,tmp_path/'compiled',host_profile=name)
    result=io.run_packet(tmp_path/'compiled',tmp_path/'run',expected_compiled_ref=compiled['compiled_ref'],host_profile=name,allow_compute=True)
    return tmp_path/'run',result


@pytest.fixture(scope='module')
def scalar_run(tmp_path_factory):
    source=raw(scalar_example())
    return (*execute(tmp_path_factory.mktemp('scalar-execution'),'scalar',source),source)


@pytest.fixture(scope='module')
def installation_run(tmp_path_factory,paired):
    return (*execute(tmp_path_factory.mktemp('paired-execution'),'installation',paired),paired)


def mutate_pair(source,*,plan=None,flag=None,truncate=False):
    value=c.loads(source,installation.MAX_BYTES)
    p,cap,pr,cr=installation.unpack_request(value)
    if plan:
        plan(p);pr=raw(p)
    for i,entry in enumerate(cap['frames']):
        rec=c.loads(decode_one(c.unpack(entry['wire'],70000)).payload)
        if plan: rec['procedure_ref']=c.bytes_ref(pr)
        if flag and i==9: rec['quality_flags']=[flag]
        entry['wire']=c.packed(encode('observation',c.bounded(rec)),70000)
    if truncate: cap['frames']=cap['frames'][:-2]
    return raw({'schema':installation.SCHEMA,'plan':c.packed(pr,installation.MAX_PLAN),'capture':installation.pack_capture(raw(cap))})


@pytest.mark.parametrize('name',['installation','scalar'])
def test_compiler_uses_existing_plan_preflight_without_execution(name,paired,monkeypatch):
    source=paired if name=='installation' else raw(scalar_example())
    monkeypatch.setattr(Session,'handle',lambda *a,**k:pytest.fail('Compiler executed a scientific operation'))
    compiled=e.compile_intent(intent(name,source),source,host_profile=name)
    module,caps,nodes,_=e.profile(name)
    assert compiled['typed_plan']==list(nodes)
    assert compiled['plan']['schema']=='ciw.production-plan.v1'
    job=compiled['plan']['jobs'][0]
    assert job['attempts']==[module.graph()] and job['requires']==list(caps)
    assert job['depends_on']==[] and compiled['bindings']['reserved_operations']==len(nodes)
    assert compiled['authorizes_execution'] is False
    assert len([x for x in job['checks'] if x['check_id'].startswith('industrial.')])==len(e.OBLIGATIONS)


def test_scalar_real_controller_keeps_all_four_occurrences_and_evidence_holds(scalar_run):
    root,result,source=scalar_run
    assert result['status']=='EVIDENCE_REQUIRED'
    assert len(result['execution_ids'])==len(result['result_ids'])==4
    assert len(set(result['execution_ids']+result['result_ids']))==8
    assert result['evidence_id']==scalar.make_source(c.loads(source,scalar.MAX_BYTES),c.bytes_ref(source))['evidence_id']
    assert result['controller']['status']=='incomplete' and result['controller']['attempt_count']==1
    assert [x['status'] for x in result['checks'][:2]]==['PASS','PASS']
    assert all(x['status']=='INDETERMINATE' for x in result['checks'][2:])
    assert result['scope']==e.SCOPE and result['automatic_retry'] is False
    assert (root/'request.json').read_bytes()==source


def test_coherent_resonance_fails_required_sample_without_becoming_physical_failure(installation_run):
    root,result,_=installation_run
    assert result['status']=='REQUIREMENTS_NOT_MET'
    assert len(result['execution_ids'])==len(result['result_ids'])==1
    check=result['checks'][0]
    assert check['status']=='FAIL'
    points=check['detail']['sampled_points']
    assert [x['status'] for x in points]==['PASS','PASS','FAIL']
    resonance=points[-1]['point']['observations']
    assert resonance['coherence']['value']==pytest.approx(1,abs=1e-12)
    assert resonance['relative_gain']['value']>4
    assert result['scope']['physical_qualification']=='HELD'
    assert check['detail']['continuous_band_claim'] is False


@pytest.mark.parametrize('kind',['unknown_timing','unknown_chain','not_sampled','flagged','incomplete'])
def test_unresolved_source_or_requirement_is_not_a_false_pass(paired,tmp_path,kind,monkeypatch):
    source=paired
    if kind=='unknown_timing':source=mutate_pair(source,plan=lambda p:p['analysis'].update(phase_skew_bound_s=None))
    elif kind=='unknown_chain':
        def change(p):p['analysis']['reference_response']='UNKNOWN';p['chain_evidence']=None
        source=mutate_pair(source,plan=change)
    elif kind=='flagged':
        source=mutate_pair(source,flag='sensor_fault')
        import numpy as np
        monkeypatch.setattr(np.fft,'rfft',lambda *a,**k:pytest.fail('flagged source entered FFT'))
    elif kind=='incomplete':source=mutate_pair(source,truncate=True)
    request=e.example_intent('installation',c.bytes_ref(source))
    if kind=='not_sampled':request['requirements'][0]['parameters']['frequencies_hz']=[33.]
    root,result=execute(tmp_path,'installation',source,raw(request))
    assert result['status']=='ANALYSIS_UNRESOLVED'
    assert result['checks'][0]['status']=='INDETERMINATE'
    assert result['scope']['physical_qualification']=='HELD'
    assert (root/'request.json').read_bytes()==source
    if kind=='incomplete':assert result['result_ids']==[] and len(result['execution_ids'])==1


def test_only_requested_resolved_points_can_pass_not_whole_response(paired,tmp_path):
    obj=e.example_intent('installation',c.bytes_ref(paired))
    obj['requirements'][0]['parameters']['frequencies_hz']=[32.,64.]
    _,result=execute(tmp_path,'installation',paired,raw(obj))
    assert result['status']=='EVIDENCE_REQUIRED'
    assert result['checks'][0]['status']=='PASS'
    assert all(x['status']=='INDETERMINATE' for x in result['checks'][1:])


@pytest.mark.parametrize('kind',['outside','arithmetic_fault'])
def test_scalar_failure_cannot_be_masked_by_completed_graph(tmp_path,kind,monkeypatch):
    source=scalar_example()
    if kind=='outside':source['model']['expected_mean']=0.0
    else:
        original=scalar._transport
        monkeypatch.setattr(scalar,'_transport',lambda matrix,jac:[[v*2 for v in row] for row in original(matrix,jac)])
    root,result=execute(tmp_path,'scalar',raw(source))
    assert result['status']=='REQUIREMENTS_NOT_MET'
    assert any(x['status']=='FAIL' for x in result['checks'][:2])
    assert len(result['execution_ids'])==4
    assert result['controller']['jobs']['assess']['status']=='rejected'
    assert result['automatic_retry'] is False and len(list((root/'run').glob('attempt-*-graph.json')))==1


@pytest.mark.parametrize('field,value',[
    ('schema','unknown'),('revision',True),('revision',0),('profile','installation'),
    ('request_ref','sha256:'+'0'*64),('intent_id',''),('extra','pass'),('requirements',[]),
    ('constraints',{'read_only':False,'max_operations':4}),('constraints',{'read_only':True,'max_operations':True}),
    ('constraints',{'read_only':True,'max_operations':1}),('constraints',{'read_only':True,'max_operations':99}),
])
def test_bad_or_underbudget_intent_refuses_before_output(tmp_path,field,value):
    source=raw(scalar_example());obj=e.example_intent('scalar',c.bytes_ref(source));obj[field]=value
    with pytest.raises((ValueError,TypeError,RuntimeError)):
        io.compile_packet(raw(obj),source,tmp_path/'no-output',host_profile='scalar')
    assert not (tmp_path/'no-output').exists()


@pytest.mark.parametrize('kind',['duplicate','missing_baseline','reserved_id','unknown_predicate','parameters','claims'])
def test_no_unregistered_code_or_claim_can_discharge_requirements(tmp_path,kind):
    source=raw(scalar_example());obj=e.example_intent('scalar',c.bytes_ref(source))
    if kind=='duplicate':obj['requirements'].append(deepcopy(obj['requirements'][0]))
    elif kind=='missing_baseline':obj['requirements']=obj['requirements'][:1]
    elif kind=='reserved_id':obj['requirements'][0]['requirement_id']='industrial.uncertainty_budget'
    elif kind=='unknown_predicate':obj['requirements'][0]['predicate']='import os'
    elif kind=='parameters':obj['requirements'][0]['parameters']={'expected':'PASS'}
    else:obj['physical_qualification']='PASSED'
    with pytest.raises((ValueError,TypeError)):
        io.compile_packet(raw(obj),source,tmp_path/'no-output',host_profile='scalar')
    assert not (tmp_path/'no-output').exists()


@pytest.mark.parametrize('freqs',[[0],[True],[32,32],[],[float('inf')],['32']])
def test_frequency_intent_has_finite_explicit_semantics(paired,freqs):
    obj=e.example_intent('installation',c.bytes_ref(paired));obj['requirements'][0]['parameters']['frequencies_hz']=freqs
    with pytest.raises((ValueError,TypeError)):e.validate_intent(obj,'installation')


@pytest.mark.parametrize('unavailable',['unbound','missing_capability'])
def test_preflight_uses_existing_registry_not_a_capability_label(unavailable):
    source=raw(scalar_example());reg=scalar.registry(bind=unavailable!='unbound')
    if unavailable=='missing_capability':
        for contract in reg._contracts.values():contract['capabilities']=['unrelated']
    with pytest.raises(Exception):e.compile_intent(intent('scalar',source),source,host_profile='scalar',registry=reg)


def test_explicit_host_grant_is_checked_before_any_source_read(tmp_path,monkeypatch):
    monkeypatch.setattr(io,'read_bytes',lambda *a,**kw:pytest.fail('read before grant'))
    with pytest.raises(PermissionError):io.run_packet('missing',tmp_path/'run',host_profile='scalar',expected_compiled_ref='sha256:'+'0'*64)
    assert not (tmp_path/'run').exists()


def test_changed_runtime_refuses_before_execution_output(tmp_path,monkeypatch):
    source=raw(scalar_example());plan=io.compile_packet(intent('scalar',source),source,tmp_path/'compiled',host_profile='scalar')
    original=scalar.runtime_identity
    monkeypatch.setattr(scalar,'runtime_identity',lambda:{**original(),'changed_dependency':'synthetic-version-drift'})
    with pytest.raises(ValueError,match='runtime'):
        io.run_packet(tmp_path/'compiled',tmp_path/'blocked',expected_compiled_ref=plan['compiled_ref'],host_profile='scalar',allow_compute=True)
    assert not (tmp_path/'blocked').exists()


def test_offline_review_creates_no_execution_and_freezes_prior_bytes(scalar_run,monkeypatch):
    root,old,source=scalar_run
    original={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    def forbidden(*a,**k):pytest.fail('Offline review dispatched a provider or network')
    monkeypatch.setattr(e.production,'run_production',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(Session,'handle',forbidden)
    result=io.inspect_thread(root,expected_thread_ref=old['thread_ref'],host_profile='scalar')
    assert result==old
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==original


def reseal_capture(root):
    m=json.loads((root/'thread.json').read_bytes())
    for entry in m['files']:
        content=(root/entry['path']).read_bytes();entry.update(bytes=len(content),sha256=c.bytes_ref(content))
    payload=raw(reseal(m));(root/'thread.json').write_bytes(payload)
    return c.bytes_ref(payload)


@pytest.mark.parametrize('part',['request','plan','compiled_scope','compiled_order','compiled_gates','result_count','grant','extra_artifact','missing_artifact','nested_manifest'])
def test_hash_resealing_is_not_semantic_acceptance(scalar_run,tmp_path,part):
    old,result,_=scalar_run;root=tmp_path/'copied';shutil.copytree(old,root)
    if part=='request':
        value=json.loads((root/'request.json').read_bytes());value['model']['expected_mean']=0
        (root/'request.json').write_bytes(raw(value))
    elif part.startswith('compiled_'):
        value=json.loads((root/'compilation.json').read_bytes())
        if part=='compiled_scope':value['scope']['physical_qualification']='PASS'
        elif part=='compiled_order':value['typed_plan'].reverse()
        else:value['plan']['jobs'][0]['checks']=value['plan']['jobs'][0]['checks'][:2];value['plan']=reseal(value['plan'])
        payload=raw(reseal(value));(root/'compilation.json').write_bytes(payload)
        manifest=json.loads((root/'thread.json').read_bytes());manifest['compiled_ref']=c.bytes_ref(payload)
        (root/'thread.json').write_bytes(raw(reseal(manifest)))
    elif part=='plan':
        value=json.loads((root/'run/plan.json').read_bytes());value['jobs'][0]['requires']=['other']
        (root/'run/plan.json').write_bytes(raw(reseal(value)))
    elif part=='result_count':
        value=json.loads((root/'run/production.json').read_bytes());value['result_count']=3
        (root/'run/production.json').write_bytes(raw(reseal(value)))
    elif part=='grant':
        value=json.loads((root/'thread.json').read_bytes());value['active_authority_restored']=True
        (root/'thread.json').write_bytes(raw(reseal(value)))
    elif part=='extra_artifact':(root/'unreviewed.json').write_text('{}')
    elif part=='nested_manifest':(root/'run/thread.json').write_text('{}')
    else:(root/'run/session/workspace.json').unlink()
    if part=='missing_artifact':anchor=result['thread_ref']
    else:anchor=reseal_capture(root)
    with pytest.raises((ValueError,KeyError,OSError)):
        io.inspect_thread(root,expected_thread_ref=anchor,host_profile='scalar')


@pytest.mark.parametrize('path',['../outside.json','/tmp/outside.json','run/../outside.json','run\\outside.json','run/a/b/c.json','run/not-json.sh'])
def test_reviewer_never_follows_unsafe_record_paths(path):
    with pytest.raises(ValueError):io._relative(path)


def test_new_revision_retains_prior_occurrences_and_needs_new_grant(scalar_run,tmp_path,monkeypatch):
    root,old,source=scalar_run
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    obj=json.loads((root/'intent.json').read_bytes());obj['revision']=2
    proposal=io.revise(root,raw(obj),source,tmp_path/'next',expected_thread_ref=old['thread_ref'],host_profile='scalar',reason='Repeat computation after review, no reacquisition')
    assert proposal['status']=='REVISION_PROPOSED_NOT_EXECUTED'
    assert proposal['continuation']['previous_execution_ids']==old['execution_ids']
    assert not (tmp_path/'next/run').exists() and proposal['execution_authorized'] is False
    ref=proposal['compilation']['compiled_ref']
    with pytest.raises(PermissionError):io.run_packet(tmp_path/'next',tmp_path/'not-permitted',expected_compiled_ref=ref,host_profile='scalar')
    revised=io.run_packet(tmp_path/'next',tmp_path/'second-run',expected_compiled_ref=ref,host_profile='scalar',allow_compute=True)
    assert revised['evidence_id']==old['evidence_id']
    assert set(revised['execution_ids']).isdisjoint(old['execution_ids'])
    assert revised['continuation']==proposal['continuation'] and revised['revision']==2
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('change',['id','revision','threshold','requirement','budget'])
def test_continuation_cannot_turn_failure_into_success_by_changing_criteria(scalar_run,tmp_path,change):
    root,old,source=scalar_run;obj=json.loads((root/'intent.json').read_bytes());obj['revision']=2
    if change=='id':obj['intent_id']='other'
    elif change=='revision':obj['revision']=4
    elif change=='budget':obj['constraints']['max_operations']=8
    elif change=='requirement':obj['requirements'][0]['requirement_id']='changed'
    else:
        request=json.loads(source);request['model']['residual_limit']=100;source=raw(request);obj['request_ref']=c.bytes_ref(source)
    with pytest.raises(ValueError):io.revise(root,raw(obj),source,tmp_path/'next',expected_thread_ref=old['thread_ref'],host_profile='scalar',reason='not a permitted waiver')
    assert not (tmp_path/'next').exists()


def test_new_remount_evidence_can_be_recompiled_but_old_measurements_are_not_reused(installation_run,tmp_path):
    root,old,source=installation_run
    def update(p):p['installation']['occurrence_id']='synthetic.new-mount';p['revision']+=1
    new_source=mutate_pair(source,plan=update)
    obj=json.loads((root/'intent.json').read_bytes());obj.update(revision=2,request_ref=c.bytes_ref(new_source))
    result=io.revise(root,raw(obj),new_source,tmp_path/'next',expected_thread_ref=old['thread_ref'],host_profile='installation',reason='Synthetic newly rebound acquisition, not a physical remount')
    assert result['computations_reused'] is False
    assert (tmp_path/'next/request.json').read_bytes()==new_source
    assert result['compilation']['request_ref']!=old['request_ref']


def test_interrupted_controller_keeps_partial_history_without_completed_thread(tmp_path,monkeypatch):
    source=raw(scalar_example());plan=io.compile_packet(intent('scalar',source),source,tmp_path/'compiled',host_profile='scalar')
    original=e.production._write
    def interrupted(root,name,value):
        original(root,name,value)
        if name=='attempt-0001.json':raise RuntimeError('synthetic interruption after retained attempt')
    monkeypatch.setattr(e.production,'_write',interrupted)
    with pytest.raises(RuntimeError,match='interruption'):
        io.run_packet(tmp_path/'compiled',tmp_path/'run',expected_compiled_ref=plan['compiled_ref'],host_profile='scalar',allow_compute=True)
    assert (tmp_path/'run/run/session/workspace.json').is_file()
    assert not (tmp_path/'run/thread.json').exists()
    with pytest.raises(FileExistsError):io.run_packet(tmp_path/'compiled',tmp_path/'run',expected_compiled_ref=plan['compiled_ref'],host_profile='scalar',allow_compute=True)


def test_external_anchor_mismatch_refuses_without_creating_report(scalar_run,tmp_path):
    root,old,_=scalar_run
    with pytest.raises(ValueError):io.report(root,tmp_path/'page.html',expected_thread_ref='sha256:'+'0'*64,host_profile='scalar')
    assert not (tmp_path/'page.html').exists()


def test_report_is_offline_escaped_held_and_nonclobber(scalar_run,tmp_path):
    root,old,_=scalar_run;target=tmp_path/'report.html'
    result=io.report(root,target,expected_thread_ref=old['thread_ref'],host_profile='scalar')
    html=target.read_text()
    assert '<details><summary>Advanced' in html and '<script' not in html
    assert "default-src 'none'" in html and 'qualification: HELD' in html
    assert 'http://' not in html and 'https://' not in html
    assert c.bytes_ref(target.read_bytes())==result['sha256']
    with pytest.raises(FileExistsError):io.report(root,target,expected_thread_ref=old['thread_ref'],host_profile='scalar')


def test_existing_cli_dispatch_and_subprocess_exit_do_not_label_held_as_success(scalar_run,tmp_path,capsys):
    from ciw.production_workflow import main
    root,result,_=scalar_run
    assert main(['engineering','inspect',str(root),'--profile','scalar','--thread-ref',result['thread_ref']])==2
    parsed=json.loads(capsys.readouterr().out);assert parsed['status']=='EVIDENCE_REQUIRED'
    out=subprocess.run([sys.executable,'-m','ciw.engineering_thread_io','inspect',str(root),
        '--profile','scalar','--thread-ref',result['thread_ref']],capture_output=True,text=True,timeout=30)
    assert out.returncode==2 and json.loads(out.stdout)['execution_ids']==result['execution_ids'],out.stderr
    assert main(['engineering','inspect',str(root),'--profile','scalar','--thread-ref','sha256:'+'0'*64])==1


def test_prior_occurrence_provenance_is_bound_into_next_compilation(scalar_run,tmp_path):
    root,old,source=scalar_run;obj=json.loads((root/'intent.json').read_bytes());obj['revision']=2
    proposed=io.revise(root,raw(obj),source,tmp_path/'next',expected_thread_ref=old['thread_ref'],host_profile='scalar',reason='Explicit review')
    path=tmp_path/'next/compilation.json';before=path.read_bytes();value=json.loads(before)
    value['continuation']['reason']='changed after approval';path.write_bytes(raw(reseal(value)))
    with pytest.raises(ValueError):io.run_packet(tmp_path/'next',tmp_path/'no',expected_compiled_ref=proposed['compilation']['compiled_ref'],host_profile='scalar',allow_compute=True)
    assert not (tmp_path/'no').exists()


def test_original_production_repair_dependency_controller_still_runs(tmp_path):
    from ciw.instruments import make_demo_run
    from ciw.control_plane import builtin_registry
    from ciw.production_workflow import builtin_plan
    from ciw.production_gates import builtin_gates
    source=make_demo_run()
    workers=(e.production.Worker('local-analysis',('statistics.v1','spectrum.periodogram.v1')),)
    result=e.production.run_production(source,builtin_plan(source),builtin_registry(bind=True),workers,builtin_gates(),tmp_path/'inherited')
    assert result['status']=='completed' and result['attempt_count']==3
    assert result['jobs']['select-window']['status']=='accepted'
    assert result['jobs']['dependent-analysis']['status']=='accepted'
    assert e.production.inspect_production(tmp_path/'inherited',builtin_gates())['execution_count']==3


def test_reviewer_does_not_reread_originals_after_freezing(scalar_run,tmp_path,monkeypatch):
    original,expected,_=scalar_run;root=tmp_path/'untrusted';shutil.copytree(original,root)
    old=e.production.inspect_production
    def after_freeze(path,gates):
        (root/'request.json').write_bytes(b'different source after validation')
        (root/'run/session/workspace.json').unlink()
        return old(path,gates)
    monkeypatch.setattr(e.production,'inspect_production',after_freeze)
    result=io.inspect_thread(root,expected_thread_ref=expected['thread_ref'],host_profile='scalar')
    assert result==expected
    with pytest.raises((ValueError,OSError)):
        io.inspect_thread(root,expected_thread_ref=expected['thread_ref'],host_profile='scalar')


def test_report_escapes_untrusted_intent_label(tmp_path):
    source=raw(scalar_example());obj=e.example_intent('scalar',c.bytes_ref(source))
    obj['intent_id']='<img src=x onerror=alert(1)>'
    root,result=execute(tmp_path,'scalar',source,raw(obj));page=tmp_path/'safe.html'
    io.report(root,page,expected_thread_ref=result['thread_ref'],host_profile='scalar')
    text=page.read_text()
    assert '<img' not in text and '&lt;img' in text


def test_readiness_counts_are_observed_not_invented_cost_or_information_gain(scalar_run):
    _,result,_=scalar_run
    metrics=result['workflow_observations']
    assert metrics['reserved_operations']==metrics['executed_operations']==metrics['retained_results']==4
    assert metrics['requirements_passed']==2 and metrics['requirements_failed']==0 and metrics['requirements_unresolved']==5
    assert metrics['queue_time_s'] is None and metrics['monetary_cost'] is None and metrics['information_gain'] is None


def test_compilation_identity_does_not_depend_on_python_hash_seed(tmp_path):
    import os
    source=raw(scalar_example());(tmp_path/'input.json').write_bytes(source)
    script='''from pathlib import Path
from ciw import engineering_thread as e,link_contract as c
raw=Path(__import__('sys').argv[1]).read_bytes()
compiled=e.compile_intent(c.bounded(e.example_intent('scalar',c.bytes_ref(raw))),raw,host_profile='scalar')
print(compiled['record_digest'])
'''
    results=[]
    for seed in ('7','8531'):
        env=dict(os.environ);env['PYTHONHASHSEED']=seed
        p=subprocess.run([sys.executable,'-c',script,str(tmp_path/'input.json')],env=env,text=True,capture_output=True,timeout=30)
        assert p.returncode==0,p.stderr
        results.append(p.stdout.strip())
    assert results[0]==results[1]


@pytest.mark.parametrize('part',['budget','worker','capability','grant','order','reservation'])
def test_historical_compilation_cannot_contradict_intent_limits(part):
    source=raw(scalar_example());ir=intent('scalar',source)
    compiled=e.compile_intent(ir,source,host_profile='scalar')
    b=compiled['bindings']
    if part=='budget':b['max_operations']=8
    elif part=='worker':b['workers']['retained-analysis'].reverse()
    elif part=='capability':next(iter(b['contracts'].values()))['capabilities']=['fabricate.pcb']
    elif part=='grant':b['authorizes_execution']=True
    elif part=='order':b['order']=['other']
    else:b['reserved_operations']=True
    compiled['bindings']=reseal(b);compiled=reseal(compiled)
    with pytest.raises(ValueError):e.check_compilation(compiled,ir,source,host_profile='scalar')
