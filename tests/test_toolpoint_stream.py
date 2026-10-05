"""Append-only, fault-preservation and containment checks for both QC adapters."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from ciw.manufacturing_contracts import content_identity as identity, load, save_new
from ciw.manufacturing_example import example_request as manufacturing_example
from ciw.manufacturing_runtime import evaluate as manufacture
from ciw.toolpoint_stream import CaptureJournal, LEGACY, NATIVE, final_observation, make_spec, verify
from ciw.toolpoint_stream_example import CASES, demo, fixture, prefix
from ciw.toolpoint_stream_cli import main
from ciw.toolpoint_stream_containment import evaluate as contain, gate, project, verify as verify_containment


@pytest.fixture(params=['legacy', 'native'])
def profile(request):
    if request.param == "legacy":
        pytest.importorskip("ciw.toolpoint_quality", reason="Optional attached-snapshot QC adapter is not installed")
    return request.param


def channels(plan, obs):
    return obs['streams' if plan['schema'] == LEGACY else 'signals']


def reseal(plan, obs):
    for channel in channels(plan, obs):
        if plan['schema'] == LEGACY:
            channel['samples_ref'] = identity(channel['samples'])
        else:
            from ciw.polymer_toolpoint_qc import signal_ref
            channel['source_ref'] = signal_ref(channel)


def opened(profile, case='nominal'):
    plan, obs = fixture(profile=profile, case=case)
    journal = CaptureJournal(make_spec(plan, obs))
    update = journal.update(prefix(plan, obs, .2), received_at_s=.21)
    receipt = journal.append(update)
    return plan, obs, journal, update, receipt


def caught_unchanged(journal, update, match=None):
    before = journal.report()
    with pytest.raises((ValueError, TypeError, KeyError), match=match):
        journal.append(update)
    assert before == journal.report()


@pytest.mark.parametrize('case', CASES)
def test_all_demo_scenarios(profile, case):
    report = demo(profile=profile, case=case)
    assert report['result']['status'] == ('NO_PROCESS_HOLD_IDENTIFIED' if case == 'nominal' else 'HELD')
    assert verify(report)['status'] == 'PASS'
    assert report['result']['authority']['action_authorized'] is False
    assert report['result']['authority']['physical_containment'] == 'not_performed'
    assert report['result']['authority']['shipment_release'] == 'not_authorized'
    final = final_observation(report)
    assert final['status'] == report['result']['status']
    assert final['stream_head_ref'] == report['receipts'][-1]['receipt_ref']


def test_existing_evaluator_loses_diagnosis_new_stream_preserves_witness(profile):
    plan, obs = fixture(profile=profile, case='single-packet-fault')
    if profile == 'legacy':
        from ciw.toolpoint_quality import screen
        result = screen(plan, obs)
        old = result['decisions'][1]
    else:
        from ciw.polymer_toolpoint_qc import assess
        old = assess(plan, obs)['rules'][1]
    assert old['status'] == 'INSUFFICIENT_EVIDENCE'
    report = demo(profile=profile, case='single-packet-fault')
    findings = report['result']['latched_findings']
    witness = next(f for f in findings if f['kind']=='SAMPLED_LIMIT_VIOLATION')
    assert witness['cavity_id'] == 'cavity.2'
    assert Decimal(witness['detail']['sample_time_s_decimal']) == Decimal('.6')
    assert Decimal(witness['detail']['lower_decimal']) == 17990000
    assert report['result']['cavities']['cavity.1']['state']=='NO_PROCESS_HOLD_IDENTIFIED'
    assert report['result']['cavities']['cavity.2']['state']=='HELD'


def test_early_finding_survives_unknown_last_sample(profile):
    report = demo(profile=profile, case='late-unknown')
    witness = next(f for f in report['result']['latched_findings'] if f['kind']=='SAMPLED_LIMIT_VIOLATION')
    assert witness['first_sequence'] == 1
    assert witness['first_update_ref'] == identity(report['updates'][1])
    assert report['receipts'][1]['status'] == 'HELD'
    assert report['receipts'][2]['status'] == 'HELD'
    assert Decimal(report['result']['declared_capture_to_receipt_s_decimal']) == Decimal('.01')


def test_health_recovery_cannot_clear_fault_history(profile):
    report = demo(profile=profile, case='health-recovery')
    assessment = report['result']['latest_assessment']
    assert assessment['status'] in {'NO_ALARM', 'PROCESS_SIGNATURE_ACCEPTABLE'}
    assert report['result']['status'] == 'HELD'
    assert report['result']['cavities']['cavity.2']['state'] == 'HELD'
    assert report['result']['cavities']['cavity.1']['state'] == 'NO_PROCESS_HOLD_IDENTIFIED'
    assert report['result']['latched_findings'][0]['kind'] == 'SENSOR_HEALTH_EVENT'


def test_shared_alarm_contains_all_cavities(profile):
    report = demo(profile=profile, case='high-nozzle')
    assert all(c['state']=='HELD' for c in report['result']['cavities'].values())
    assert any(f['cavity_id'] is None for f in report['result']['latched_findings'])


def test_idempotent_duplicate_and_defensive_copies(profile):
    plan, obs, journal, update, receipt = opened(profile)
    before = journal.report()
    assert journal.append(deepcopy(update)) == receipt
    assert before == journal.report()
    receipt['status']='FAKE'; update['observation']['cycle_id']='FAKE'
    before['result']['status']='FAKE'
    plan['rules'].clear(); obs.clear()
    assert journal.report()['result']['status']=='PENDING'


def test_conflicting_duplicate_refused(profile):
    plan, obs, journal, update, _ = opened(profile)
    update['received_at_s']=.22
    caught_unchanged(journal, update, 'Conflicting duplicate')


@pytest.mark.parametrize('mutation', ['sequence', 'parent', 'clock', 'watermark', 'rewrite', 'remove', 'backfill', 'sensor_drop', 'calibration', 'source', 'mapping'])
def test_continuation_refusals(profile, mutation):
    plan, obs, journal, _, _ = opened(profile)
    value = prefix(plan, obs, .6)
    update = journal.update(value, received_at_s=.61)
    cs = channels(plan, update['observation'])
    if mutation=='sequence': update['sequence']=2
    elif mutation=='parent': update['previous_receipt_ref']=identity('wrong')
    elif mutation=='clock': update['received_at_s']=.1
    elif mutation=='watermark':
        update['observation']=prefix(plan, obs, .1)
    elif mutation=='rewrite': cs[0]['samples'][0]['value']+=1; reseal(plan,update['observation'])
    elif mutation=='remove': cs[0]['samples'].pop(0); reseal(plan,update['observation'])
    elif mutation=='backfill':
        cs[0]['samples'].insert(1,{'time_s':.1,'value':5,'expanded_uncertainty':0}); reseal(plan,update['observation'])
    elif mutation=='sensor_drop': cs.pop(0)
    elif mutation=='calibration':
        if profile=='legacy': cs[0]['calibration']['record_ref']=identity('new')
        else: cs[0]['calibration_ref']=identity('new')
    elif mutation=='source':
        update['observation']['source_kind']='retained_observation'
    elif mutation=='mapping':
        if profile=='legacy': update['observation']['cycle_event_ref']=identity('new')
        else: update['observation']['part_mapping']['evidence_ref']=identity('new')
    caught_unchanged(journal,update)


@pytest.mark.parametrize('field', ['cycle_id','cycle_ref','started_at_s','source_kind'])
def test_spec_binding_is_enforced(profile,field):
    plan, obs=fixture(profile=profile,case='nominal')
    spec=make_spec(plan,obs)
    spec['binding'][field]={'cycle_id':'foreign','cycle_ref':identity('foreign'),'started_at_s':1,
                            'source_kind':'retained_observation'}[field]
    if profile=='native' and field=='source_kind':
        with pytest.raises(ValueError): CaptureJournal(spec)
    else:
        journal=CaptureJournal(spec)
        with pytest.raises(ValueError): journal.append(journal.update(obs, received_at_s=5, final=True))


def test_future_sample_refused(profile):
    plan,obs,journal,_,_=opened(profile)
    value=prefix(plan,obs,.6)
    channels(plan,value)[0]['samples'].append({'time_s':.7,'value':1,'expanded_uncertainty':0})
    reseal(plan,value)
    caught_unchanged(journal,journal.update(value,received_at_s=.61))


def test_late_new_sensor_cannot_backfill_watermark(profile):
    plan,obs=fixture(profile=profile,case='nominal')
    journal=CaptureJournal(make_spec(plan,obs))
    value=prefix(plan,obs,.2)
    channels(plan,value).pop(1)
    journal.append(journal.update(value,received_at_s=.21))
    caught_unchanged(journal,journal.update(prefix(plan,obs,.6),received_at_s=.61),'Late sample')


def test_new_sensor_with_only_forward_samples_is_accepted_but_coverage_stays_held(profile):
    plan,obs=fixture(profile=profile,case='nominal')
    journal=CaptureJournal(make_spec(plan,obs))
    value=prefix(plan,obs,.2)
    channels(plan,value).pop(1)
    journal.append(journal.update(value,received_at_s=.21))
    value=prefix(plan,obs,.8,final=True)
    channels(plan,value)[1]['samples']=channels(plan,value)[1]['samples'][2:]
    reseal(plan,value)
    journal.append(journal.update(value,received_at_s=.81,final=True))
    assert journal.report()['result']['cavities']['cavity.2']['state']=='HELD'


def test_noop_update_requires_retry(profile):
    _, _,journal,original,_=opened(profile)
    value=deepcopy(original['observation'])
    caught_unchanged(journal,journal.update(value,received_at_s=.22),'No-op')


def test_finalize_same_watermark_and_no_reopen(profile):
    plan,obs=fixture(profile=profile,case='nominal')
    journal=CaptureJournal(make_spec(plan,obs))
    journal.append(journal.update(prefix(plan,obs,.8),received_at_s=.81))
    assert journal.report()['result']['status']=='PENDING'
    value=prefix(plan,obs,.8,final=True)
    update=journal.update(value,received_at_s=.82,final=True)
    receipt=journal.append(update)
    assert journal.finalized
    assert journal.append(deepcopy(update))==receipt
    caught_unchanged(journal,journal.update(value,received_at_s=.83,final=True),'sealed')
    assert journal.report()['result']['status']=='NO_PROCESS_HOLD_IDENTIFIED'


def test_cannot_export_unfinalized_capture(profile):
    _,_,journal,_,_=opened(profile)
    with pytest.raises(ValueError,match='finalized'): final_observation(journal.report())


def test_short_final_is_held(profile):
    plan,obs=fixture(profile=profile,case='nominal')
    journal=CaptureJournal(make_spec(plan,obs))
    journal.append(journal.update(prefix(plan,obs,.2,final=True),received_at_s=.21,final=True))
    assert journal.report()['result']['status']=='HELD'


def test_restore_fresh_occurrence_and_exact_replay(profile):
    plan,obs,journal,_,_=opened(profile)
    report=journal.report(); restored=CaptureJournal.restore(report)
    assert restored.report()['execution_id'] != report['execution_id']
    assert restored.report()['receipts'] == report['receipts']
    for obj in (journal,restored): obj.append(obj.update(prefix(plan,obs,.8,final=True),received_at_s=.81,final=True))
    assert restored.report()['result']==journal.report()['result']
    assert verify(report)['verification_id'] != verify(report)['verification_id']


@pytest.mark.parametrize('target',['result','receipt','update','operation','spec','count','authority','finding'])
def test_tampering_even_rehashed_is_refused(profile,target):
    report=demo(profile=profile)
    if target=='result': report['result']['status']='NO_PROCESS_HOLD_IDENTIFIED';report['result_ref']=identity(report['result'])
    elif target=='receipt':
        r=report['receipts'][-1];r['status']='NO_PROCESS_HOLD_IDENTIFIED';r['receipt_ref']=identity({k:v for k,v in r.items() if k!='receipt_ref'})
    elif target=='update': report['updates'][0]['received_at_s']=.22
    elif target=='operation': report['operation_ref']=identity('other')
    elif target=='spec': report['spec']['binding']['cycle_id']='different';report['spec_ref']=identity(report['spec'])
    elif target=='count': report['receipts'].pop()
    elif target=='authority': report['result']['authority']['action_authorized']=True;report['result_ref']=identity(report['result'])
    elif target=='finding': report['result']['latched_findings'].clear();report['result_ref']=identity(report['result'])
    with pytest.raises(ValueError): verify(report)


@pytest.mark.parametrize('field,value', [('max_updates',0),('max_updates',65),('max_updates',True),('max_samples_per_sensor',0),('max_total_samples',32769)])
def test_invalid_limits_refused(profile,field,value):
    plan,obs=fixture(profile=profile)
    spec=make_spec(plan,obs);spec['limits'][field]=value
    with pytest.raises(ValueError): CaptureJournal(spec)


def test_budget_exhaustion_leaves_state_unchanged(profile):
    plan,obs=fixture(profile=profile,case='nominal')
    spec=make_spec(plan,obs,max_updates=1)
    journal=CaptureJournal(spec)
    journal.append(journal.update(prefix(plan,obs,.2),received_at_s=.21))
    caught_unchanged(journal,journal.update(prefix(plan,obs,.8,final=True),received_at_s=.81,final=True),'budget')


@pytest.mark.parametrize('field,value',[('max_samples_per_sensor',2),('max_total_samples',6)])
def test_sample_budget_exhaustion(profile,field,value):
    plan,obs=fixture(profile=profile,case='nominal');spec=make_spec(plan,obs);spec['limits'][field]=value
    journal=CaptureJournal(spec);journal.append(journal.update(prefix(plan,obs,.2),received_at_s=.21))
    caught_unchanged(journal,journal.update(prefix(plan,obs,.8,final=True),received_at_s=.81,final=True),'budget')


@pytest.mark.parametrize('field,value',[('sequence',True),('sequence',-1),('final',1),('received_at_s',float('nan')),('received_at_s',float('inf'))])
def test_malformed_updates(profile,field,value):
    plan,obs,journal,_,_=opened(profile);update=journal.update(prefix(plan,obs,.6),received_at_s=.61)
    update[field]=value;caught_unchanged(journal,update)


@pytest.mark.parametrize('u',[0,1e-20,1,10000,2000000])
def test_independent_rational_interval_witness(profile,u):
    plan,obs=fixture(profile=profile,case='nominal')
    channel=channels(plan,obs)[1]
    channel['samples'][3]['value']=15000001
    channel['samples'][3]['expanded_uncertainty']=u
    channel['samples'][-1]['expanded_uncertainty']=None
    reseal(plan,obs)
    journal=CaptureJournal(make_spec(plan,obs))
    journal.append(journal.update(obs,received_at_s=.81,final=True))
    witnesses=[f for f in journal.report()['result']['latched_findings'] if f['kind']=='SAMPLED_LIMIT_VIOLATION']
    expected=Fraction(15000001)-Fraction(str(u)) > Fraction(15000000)
    assert bool(witnesses) is expected
    if expected:
        assert Fraction(witnesses[0]['detail']['lower_decimal'])==Fraction(15000001)-Fraction(str(u))


@pytest.mark.parametrize('seed',range(20))
def test_rational_randomized_witness_oracle(profile,seed):
    rng=random.Random(seed)
    y=15000000+rng.randrange(-100,101);u=rng.randrange(0,100)/10
    plan,obs=fixture(profile=profile,case='nominal')
    ch=channels(plan,obs)[1];ch['samples'][3].update(value=y,expanded_uncertainty=u)
    ch['samples'][-1]['expanded_uncertainty']=None;reseal(plan,obs)
    journal=CaptureJournal(make_spec(plan,obs));journal.append(journal.update(obs,received_at_s=.81,final=True))
    witness=any(f['kind']=='SAMPLED_LIMIT_VIOLATION' for f in journal.report()['result']['latched_findings'])
    assert witness == (Fraction(y)-Fraction(str(u))>15000000)


@pytest.mark.parametrize('invalid',['uncertainty','calibration','health','clock','window'])
def test_invalid_high_sample_is_not_a_valid_witness(profile,invalid):
    plan,obs=fixture(profile=profile,case='single-packet-fault')
    ch=channels(plan,obs)[1]
    if invalid=='uncertainty':ch['samples'][3]['expanded_uncertainty']=None
    elif invalid=='calibration':
        if profile=='legacy':ch['calibration']=None
        else:ch['calibration_ref']=None
    elif invalid=='health':ch['health']='saturated'
    elif invalid=='clock':ch['time_uncertainty_s' if profile=='legacy' else 'clock_skew_s']=.1
    else:
        # Put the only high witness at an uncertain phase boundary.
        ch['samples'][3]['value']=8000000;ch['samples'][-1]['value']=18000000
        ch['samples'][-1]['expanded_uncertainty']=10000
        ch['time_uncertainty_s' if profile=='legacy' else 'clock_skew_s']=.001
    reseal(plan,obs)
    j=CaptureJournal(make_spec(plan,obs));j.append(j.update(obs,received_at_s=.81,final=True))
    assert not any(f['kind']=='SAMPLED_LIMIT_VIOLATION' for f in j.report()['result']['latched_findings'])
    assert j.report()['result']['status']=='HELD'


def containment_request(profile,case='health-recovery'):
    return {'schema':'ciw.toolpoint-stream-containment-request.v1','manufacturing_report':manufacture(manufacturing_example()),
            'streams':[demo(profile=profile,case=case),demo(profile=profile,case='nominal',cycle_index=1)],'assessed_at_s':12}


def test_full_genealogy_contains_recovered_sensor_fault(profile):
    request=containment_request(profile);before=deepcopy(request)
    report=contain(request);q=report['result']['quality']
    assert request==before
    assert q['p002']['recorded_inspection']=='PASS'
    assert q['p002']['state']=='HELD' and q['a001']['state']=='HELD'
    assert q['p001']['state']=='ELIGIBLE_FOR_REVIEW' and q['p003']['state']=='ELIGIBLE_FOR_REVIEW'
    assert q['p004']['state']=='SCRAPPED'
    assert report['result']['packages']['synthetic.package']['state']=='HELD'
    assert verify_containment(report)['status']=='PASS'
    assert gate(report,serials=['a001'],action='release_review')['status']=='HELD'
    assert gate(report,serials=['p003'],action='release_review')['status']=='ELIGIBLE_FOR_REVIEW'
    assert gate(report,serials=['p001'],action='assemble')['status']=='HELD' # already consumed


def test_missing_cycle_evidence_contains_only_that_cycle_and_descendants(profile):
    request=containment_request(profile,'nominal');request['streams'].pop(0)
    result=project(request)
    assert result['quality']['p001']['state']=='HELD'
    assert result['quality']['p002']['state']=='HELD'
    assert result['quality']['a001']['state']=='HELD'
    assert result['quality']['p003']['state']=='ELIGIBLE_FOR_REVIEW'


def test_clean_process_cannot_clear_scrap_or_final_inspection(profile):
    r=contain(containment_request(profile,'nominal'))
    assert r['result']['quality']['p004']['state']=='SCRAPPED'
    assert r['result']['quality']['a001']['state']=='ELIGIBLE_FOR_REVIEW'
    assert r['result']['authority']['shipment_release']=='not_authorized'


@pytest.mark.parametrize('fault',['duplicate_cycle','future','absent_cycle','wrong_ref','changed_quality','authority'])
def test_containment_refusals(profile,fault):
    request=containment_request(profile)
    if fault=='duplicate_cycle':request['streams'].append(deepcopy(request['streams'][0]))
    elif fault=='future':request['assessed_at_s']=0
    elif fault in {'absent_cycle','wrong_ref'}:
        plan,obs=fixture(profile=profile)
        if fault=='absent_cycle':obs['cycle_id']='foreign'
        else:obs['cycle_event_ref' if profile=='legacy' else 'cycle_evidence_ref']=identity('wrong')
        j=CaptureJournal(make_spec(plan,obs));j.append(j.update(obs,received_at_s=.81,final=True));request['streams'][0]=j.report()
    elif fault=='changed_quality':
        m=request['manufacturing_report'];m['result']['instances']['p004']['quality']='PASS';m['result_ref']=identity(m['result'])
    else:request['streams'][0]['result']['authority']['action_authorized']=True;request['streams'][0]['result_ref']=identity(request['streams'][0]['result'])
    with pytest.raises(ValueError):project(request)


@pytest.mark.parametrize('target',['unknown','duplicate','execute'])
def test_review_gate_rejects_invalid_targets(profile,target):
    report=contain(containment_request(profile,'nominal'))
    serials=['unknown'] if target=='unknown' else ['a001','a001'] if target=='duplicate' else ['a001']
    with pytest.raises(ValueError):gate(report,serials=serials,action='execute' if target=='execute' else 'release_review')


def test_containment_rehashed_tampering(profile):
    report=contain(containment_request(profile));report['result']['quality']['a001']['state']='ELIGIBLE_FOR_REVIEW'
    report['result_ref']=identity(report['result'])
    with pytest.raises(ValueError):verify_containment(report)


def test_cli_lifecycle_and_create_only(profile,tmp_path,capsys):
    path=tmp_path/'report.json'
    assert main(['demo','--profile',profile,'--case','late-unknown','--output',str(path)])==2
    assert main(['verify',str(path)])==0
    assert main(['summary',str(path)])==2
    final=tmp_path/'final.json'
    assert main(['final',str(path),'--output',str(final)])==2
    before=path.read_bytes()
    assert main(['demo','--profile',profile,'--output',str(path)])==1
    assert path.read_bytes()==before
    assert load(final)['status']=='HELD'
    capsys.readouterr()


def test_cli_start_append_resume(profile,tmp_path,capsys):
    plan,obs=fixture(profile=profile,case='nominal')
    spec=tmp_path/'spec.json';p1=tmp_path/'prefix1.json';p2=tmp_path/'prefix2.json'
    r1=tmp_path/'report1.json';r2=tmp_path/'report2.json'
    save_new(spec,make_spec(plan,obs));save_new(p1,prefix(plan,obs,.2));save_new(p2,prefix(plan,obs,.8,final=True))
    assert main(['start',str(spec),str(p1),'--received-at-s','.21','--output',str(r1)])==2
    assert main(['append',str(r1),str(p2),'--received-at-s','.81','--final','--output',str(r2)])==0
    assert main(['verify',str(r2)])==0
    assert load(r1)['result']['finalized'] is False
    assert load(r2)['result']['finalized'] is True
    capsys.readouterr()


def test_real_subprocess(profile,tmp_path):
    result=subprocess.run([sys.executable,'-m','ciw.toolpoint_stream_cli','demo','--profile',profile,'--case','nominal','--output',str(tmp_path/'demo.json')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([sys.executable,'-m','ciw.toolpoint_stream_cli','verify',str(tmp_path/'demo.json')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='PASS'


def test_invalid_json_duplicate_and_nonfinite(tmp_path,capsys):
    for index,text in enumerate(['{"schema":1,"schema":2}','{"schema":NaN}','{"schema":1e-9999}']):
        p=tmp_path/f'invalid{index}.json';p.write_text(text)
        assert main(['verify',str(p)])==1
    capsys.readouterr()


def test_failed_publication_preserves_existing_snapshot(profile,tmp_path,monkeypatch,capsys):
    import os
    def fail(*a,**k):raise OSError('injected publication failure')
    monkeypatch.setattr(os,'link',fail)
    output=tmp_path/'new.json'
    assert main(['demo','--profile',profile,'--output',str(output)])==1
    assert not output.exists()
    assert not list(tmp_path.iterdir())
    capsys.readouterr()


@pytest.mark.parametrize('case',['nominal','late-unknown','high-nozzle','sampling-gap'])
@pytest.mark.parametrize('seed',range(10))
def test_packetization_does_not_change_final_scope(profile,case,seed):
    plan,obs=fixture(profile=profile,case=case)
    rng=random.Random(seed)
    cuts=sorted(rng.sample([0,.2,.4,.6],rng.randrange(5)))+[.8]
    j=CaptureJournal(make_spec(plan,obs))
    last_held=set()
    for cut in cuts:
        final=cut==.8
        j.append(j.update(prefix(plan,obs,cut,final=final),received_at_s=float(Decimal(str(cut))+Decimal('.01')),final=final))
        held={c for c,q in j.report()['result']['cavities'].items() if q['state']=='HELD'}
        assert last_held.issubset(held)
        last_held=held
    one=CaptureJournal(make_spec(plan,obs));one.append(one.update(obs,received_at_s=.81,final=True))
    assert {c:q['state'] for c,q in j.report()['result']['cavities'].items()} == {c:q['state'] for c,q in one.report()['result']['cavities'].items()}
    assert j.report()['result']['status']==one.report()['result']['status']


def test_report_budget_failure_is_atomic(profile,monkeypatch):
    import ciw.toolpoint_stream as stream
    plan,obs,journal,_,_=opened(profile)
    old_bound=stream.bounded
    def fail_report(value):
        if type(value) is dict and value.get('schema')==stream.REPORT_SCHEMA:
            raise ValueError('injected report budget exceeded')
        old_bound(value)
    monkeypatch.setattr(stream,'bounded',fail_report)
    caught_unchanged(journal,journal.update(prefix(plan,obs,.8,final=True),received_at_s=.81,final=True),'budget')


@pytest.mark.parametrize('bad',[None,[],True,'not-a-plan',{}])
def test_invalid_plan_objects_fail_closed(bad):
    plan,obs=fixture(profile='native')
    spec=make_spec(plan,obs);spec['plan']=bad
    with pytest.raises(ValueError):CaptureJournal(spec)


def test_native_model_residual_and_integral_keep_original_feature_meaning():
    from ciw.polymer_toolpoint_qc import assess
    plan,obs=fixture(profile='native',case='nominal')
    rule=plan['rules'][0]
    integral={**deepcopy(rule),'rule_id':'native.integral','feature':'pressure_integral',
              'limits':{'lower':0,'upper':1e9,'unit':'Pa*s'}}
    curve=deepcopy(obs['signals'][0]['samples'])
    residual={**deepcopy(rule),'rule_id':'native.residual','feature':'max_abs_residual',
              'limits':{'lower':0,'upper':1e5,'unit':'Pa'},
              'reference_curve':{'model_ref':identity('synthetic.model'),'samples':curve,'samples_ref':identity(curve)}}
    plan['rules'] += [integral,residual]
    expected=assess(plan,obs)
    j=CaptureJournal(make_spec(plan,obs))
    for t in [.2,.6,.8]:j.append(j.update(prefix(plan,obs,t,final=t==.8),received_at_s=t+.01,final=t==.8))
    assert j.report()['result']['latest_assessment']==expected
    rules={r['rule_id']:r for r in expected['rules']}
    samples=obs['signals'][0]['samples']
    lower=sum((Fraction(str(b['time_s']))-Fraction(str(a['time_s'])))*
              (Fraction(a['value'])-Fraction(a['expanded_uncertainty'])+Fraction(b['value'])-Fraction(b['expanded_uncertainty']))/2
              for a,b in zip(samples,samples[1:]))
    assert Fraction(rules['native.integral']['feature_interval']['lower_decimal'])==lower
    assert Decimal(rules['native.residual']['feature_interval']['lower_decimal'])==0
    assert Decimal(rules['native.residual']['feature_interval']['upper_decimal'])==20000


def test_native_unresolved_mapping_keeps_entire_cohort_held():
    plan,obs=fixture(profile='native',case='nominal')
    obs['part_mapping']['status']='unresolved'
    j=CaptureJournal(make_spec(plan,obs));j.append(j.update(obs,received_at_s=.81,final=True))
    assert all(c['state']=='HELD' for c in j.report()['result']['cavities'].values())


def test_empty_export_refused():
    plan,obs=fixture(profile='native')
    with pytest.raises(ValueError):CaptureJournal(make_spec(plan,obs)).report()


def test_cli_containment_and_gate(tmp_path,capsys):
    request=tmp_path/'request.json';report=tmp_path/'report.json'
    save_new(request,containment_request('native'))
    assert main(['contain',str(request),'--output',str(report)])==0
    assert main(['verify-containment',str(report)])==0
    assert main(['gate',str(report),'--action','release_review','--serial','a001'])==2
    assert main(['gate',str(report),'--action','release_review','--serial','p003'])==0
    capsys.readouterr()


def test_external_head_anchor_rejects_rollback(profile):
    plan,obs,journal,_,_=opened(profile)
    old=journal.report()
    journal.append(journal.update(prefix(plan,obs,.8,final=True),received_at_s=.81,final=True))
    newest=journal.report();head=newest['receipts'][-1]['receipt_ref']
    # Old checkpoints replay legitimately, but cannot impersonate a known newer head.
    assert verify(old)['status']=='PASS'
    with pytest.raises(ValueError,match='expected head'):verify(old,expected_head_ref=head)
    with pytest.raises(ValueError,match='expected head'):CaptureJournal.restore(old,expected_head_ref=head)
    checked=verify(newest,expected_head_ref=head,expected_spec_ref=newest['spec_ref'])
    assert checked['head_anchor_checked'] is True and checked['spec_anchor_checked'] is True


def test_external_spec_anchor_rejects_substitution(profile):
    report=demo(profile=profile)
    with pytest.raises(ValueError,match='expected specification'):verify(report,expected_spec_ref=identity('foreign'))
    with pytest.raises(ValueError):verify(report,expected_head_ref='not-a-ref')


def test_cli_anchored_verification(profile,tmp_path,capsys):
    report=demo(profile=profile);path=tmp_path/'report.json';save_new(path,report)
    assert main(['verify',str(path),'--expected-head-ref',report['receipts'][-1]['receipt_ref'],'--expected-spec-ref',report['spec_ref']])==0
    assert main(['verify',str(path),'--expected-head-ref',identity('old')])==1
    capsys.readouterr()
