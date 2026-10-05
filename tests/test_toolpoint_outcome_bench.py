"""Independent numerical checks, leakage guards, deadline replay and store journey."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from math import comb
from pathlib import Path
import subprocess
import sys

import pytest

from ciw.manufacturing_contracts import content_identity as identity, load, save_new
from ciw import toolpoint_outcome_bench as bench
from ciw.toolpoint_outcome_example import example_request
from ciw.toolpoint_outcome_stats import binomial_upper
from ciw.toolpoint_stream import CaptureJournal
from ciw.toolpoint_stream_store import StreamStore, initialize


@pytest.fixture
def request_data():
    return example_request()


def rows_by_serial(result):
    return {r["serial"]: r for r in result["rows"]}


@pytest.mark.parametrize("n,k", [(1,0),(1,1),(2,0),(2,1),(2,2),(5,0),(5,2),(5,4),
                                    (20,0),(20,1),(20,10),(20,19),(128,0),(128,32),(128,127)])
@pytest.mark.parametrize("alpha", [.05, .025, .001])
def test_exact_binomial_upper_independent_scipy_and_rational_oracle(n,k,alpha):
    result=binomial_upper(k,n,alpha=alpha)
    p=Fraction(result["upper_decimal"])
    beta=pytest.importorskip("scipy.stats").beta
    expected=1.0 if k==n else beta.ppf(1-alpha,k+1,n-k)
    assert float(p)==pytest.approx(expected,abs=3e-14)
    if k<n:
        cdf=sum(Fraction(comb(n,i))*p**i*(1-p)**(n-i) for i in range(k+1))
        assert cdf<=Fraction(str(alpha))


@pytest.mark.parametrize("n", [1,3,20,100,128])
def test_zero_events_is_not_zero_risk(n):
    with localcontext() as context:
        context.prec=100
        expected=1-Decimal('.025')**(Decimal(1)/n)
        upper=Decimal(binomial_upper(0,n,alpha='.025')["upper_decimal"])
        assert upper>=expected
        assert upper-expected < Decimal('3e-22')
        assert upper>0


@pytest.mark.parametrize("args", [(True,3,.05),(0,True,.05),(-1,3,.05),(4,3,.05),(0,129,.05),
    (0,1,0),(0,1,1),(0,1,True),(0,1,float('nan')),(0,1,'nan'),(0,1,'x'),(0,1,'1e-100'),(0,1,'0'*65)])
def test_statistical_contract_refuses_invalid_counts_and_probabilities(args):
    with pytest.raises(ValueError): binomial_upper(args[0],args[1],alpha=args[2])


def test_no_trials_is_unknown_not_zero():
    assert binomial_upper(0,0)["estimate"] is None
    assert binomial_upper(0,0)["upper_decimal"] is None


@pytest.mark.parametrize("n", [1,2,3,5,8])
def test_finite_binomial_coverage_on_independent_rational_grid(n):
    alpha=Fraction(1,20)
    bounds=[Fraction(binomial_upper(k,n)["upper_decimal"]) for k in range(n+1)]
    for j in range(1,20):
        p=Fraction(j,20)
        undercoverage=sum(Fraction(comb(n,k))*p**k*(1-p)**(n-k)
            for k in range(n+1) if bounds[k]<p)
        assert undercoverage<=alpha


def test_mixed_fixture_exposes_miss_false_hold_and_real_process_alarm(request_data):
    result=bench.analyze(request_data)
    by=rows_by_serial(result)
    assert [(by[s]["decision"],by[s]["outcome"]) for s in ('p001','p002','p003','p004')]==[
        ('CLEAR','CONFORMING'),('PROCESS_ALARM','NONCONFORMING'),
        ('PRECAUTIONARY_HOLD','CONFORMING'),('CLEAR','NONCONFORMING')]
    assert by['p002']['first_alarm_receipt_after_start_s_decimal']=='0.61'
    rates=result['descriptive_part_rates']
    assert rates['process_alarm_sensitivity']=={'events':1,'trials':2,'estimate':.5}
    assert rates['containment_miss_rate']['estimate']==.5
    assert rates['false_hold_rate_for_this_endpoint']['estimate']==.5
    assert result['qualification']=='NOT_ESTABLISHED'
    assert result['evidence_class']=='SYNTHETIC_ONLY'
    assert result['authority']['action_authorized'] is False
    # Manufacturing PASS must NOT substitute for the independent study endpoint.
    assert request_data['manufacturing_reports'][0]['result']['instances']['p002']['quality']=='PASS'
    # A downstream scrap cannot be counted as this process detector's successful alarm.
    assert request_data['manufacturing_reports'][0]['result']['instances']['p004']['disposition']=='SCRAPPED'


def test_material_lot_is_one_trial_not_cavity_samples_or_prefixes(request_data):
    result=bench.analyze(request_data)
    assert len(result['lot_trials'])==1
    assert result['lot_trials'][0]['enrolled_parts']==4
    b=result['conditional_lot_bounds']
    assert b['lot_any_miss']['trials']==b['lot_any_false_hold']['trials']==1
    assert b['per_endpoint_alpha_decimal']=='0.025'
    assert 'TOO_FEW_DEFECTIVE_LOTS' in result['criteria_screen']['reasons']


def test_late_updates_cannot_retroactively_improve_early_detection():
    result=bench.analyze(example_request(case='late-alarm'))
    row=rows_by_serial(result)['p002']
    assert row['decision']=='PRECAUTIONARY_HOLD'
    assert row['first_alarm_receipt_after_start_s_decimal'] is None
    assert row['later_updates_excluded']==2
    assert result['descriptive_part_rates']['process_alarm_sensitivity']['estimate']==0


def test_missing_independent_inspection_keeps_complete_cohort_visible():
    result=bench.analyze(example_request(case='missing-inspection'))
    assert result['enrolled_parts']==4 and result['known_outcomes']==3
    assert result['confusion_by_endpoint']['UNKNOWN']['CLEAR']==1
    assert 'UNRESOLVED_INDEPENDENT_OUTCOMES' in result['criteria_screen']['reasons']


def test_missing_streams_hold_but_are_not_successful_process_detections(request_data):
    request_data['streams']=[]
    result=bench.analyze(request_data)
    assert len(result['rows'])==4
    assert result['descriptive_part_rates']['process_alarm_sensitivity']['estimate']==0
    assert result['descriptive_part_rates']['containment_miss_rate']['estimate']==0
    assert result['descriptive_part_rates']['false_hold_rate_for_this_endpoint']['estimate']==1
    assert 'MISSING_CYCLE_STREAMS' in result['criteria_screen']['reasons']


@pytest.mark.parametrize('field',['evidence_ref','method_ref','calibration_ref','independence_basis_ref','blinded_to_qc','label'])
def test_unknown_or_unblinded_outcomes_not_silently_scored(request_data,field):
    request_data['outcomes'][0][field]={'blinded_to_qc':False,'label':'INDETERMINATE'}.get(field)
    result=bench.analyze(request_data)
    assert result['known_outcomes']==3
    assert rows_by_serial(result)['p001']['outcome']=='UNKNOWN'


@pytest.mark.parametrize('field',['source_kind','part_ref','characteristic_id','specification_ref','condition'])
def test_outcome_scope_substitution_refused(request_data,field):
    request_data['outcomes'][0][field]='wrong'
    with pytest.raises(ValueError,match='scope'): bench.analyze(request_data)


@pytest.mark.parametrize('kind',['lot','cycle','signal','label_from_signal','label_from_reference','label_from_training'])
def test_development_or_label_leakage_refused(request_data,kind):
    p=request_data['protocol']; stream=request_data['streams'][0]
    signal=stream['updates'][0]['observation']['signals'][0]['source_ref']
    if kind=='lot': p['development_lot_ids']=[request_data['manufacturing_reports'][0]['request']['configuration']['material']['lot_id']]
    elif kind=='cycle': p['development_cycle_refs']=[stream['spec']['binding']['cycle_ref']]
    elif kind=='signal': p['development_evidence_refs']=[signal]
    elif kind=='label_from_signal': request_data['outcomes'][0]['evidence_ref']=signal
    elif kind=='label_from_reference': request_data['outcomes'][0]['evidence_ref']=stream['spec']['plan']['reference_evidence_refs'][0]
    else: p['development_evidence_refs']=[request_data['outcomes'][0]['evidence_ref']]
    with pytest.raises(ValueError): bench.analyze(request_data)


@pytest.mark.parametrize('mutation',['drop_enrollment','duplicate_enrollment','duplicate_manufacturing','duplicate_stream',
                                    'unknown_outcome','duplicate_outcome','wrong_plan','future_outcome','precycle_outcome',
                                    'late_freeze','early_assessment','mixed_source','bad_independence'])
def test_identity_cohort_and_chronology_guards(request_data,mutation):
    p=request_data['protocol']
    if mutation=='drop_enrollment': p['enrollment'].pop()
    elif mutation=='duplicate_enrollment': p['enrollment'].append(deepcopy(p['enrollment'][0]))
    elif mutation=='duplicate_manufacturing': request_data['manufacturing_reports']*=2
    elif mutation=='duplicate_stream': request_data['streams'].append(deepcopy(request_data['streams'][0]))
    elif mutation=='unknown_outcome': request_data['outcomes'][0]['unit_ref']=identity('unknown')
    elif mutation=='duplicate_outcome': request_data['outcomes'].append(deepcopy(request_data['outcomes'][0]))
    elif mutation=='wrong_plan': p['monitoring_plan_refs']=[identity('wrong-plan')]
    elif mutation=='future_outcome': request_data['outcomes'][0]['observed_at_s']=21
    elif mutation=='precycle_outcome': request_data['outcomes'][0]['observed_at_s']=0
    elif mutation=='late_freeze': p['frozen_at_s']=.1
    elif mutation=='early_assessment': request_data['assessed_at_s']=.5
    elif mutation=='mixed_source': p['source_kind']='retained_observation'
    else: p['sampling_basis_ref']=None
    with pytest.raises(ValueError): bench.analyze(request_data)


def test_correlated_lots_can_have_descriptive_rates_but_no_ci(request_data):
    request_data['protocol']['iid_lot_sampling_declared']=False
    request_data['protocol']['sampling_basis_ref']=None
    result=bench.analyze(request_data)
    assert result['descriptive_part_rates']['containment_miss_rate']['estimate']==.5
    assert result['conditional_lot_bounds']['lot_any_miss'] is None
    assert result['criteria_screen']['status']=='NOT_ESTABLISHED'


def test_label_changes_do_not_change_detector_decisions(request_data):
    before=rows_by_serial(bench.analyze(request_data))
    for o in request_data['outcomes']:
        o['label']='CONFORMING' if o['label']=='NONCONFORMING' else 'NONCONFORMING'
    after=rows_by_serial(bench.analyze(request_data))
    assert {k:r['decision'] for k,r in before.items()}=={k:r['decision'] for k,r in after.items()}


def test_deadline_shorter_than_first_receipt_is_no_evidence_not_clear(request_data):
    request_data['protocol']['decision_deadline_after_start_s']=.001
    result=bench.analyze(request_data)
    assert all(r['decision']=='PRECAUTIONARY_HOLD' for r in result['rows'])
    assert all(r['asof_head_ref'] is None for r in result['rows'])


def test_upstream_tampering_is_refused(request_data):
    request_data['streams'][0]['result']['status']='NO_PROCESS_HOLD_IDENTIFIED'
    request_data['streams'][0]['result_ref']=identity(request_data['streams'][0]['result'])
    with pytest.raises(ValueError): bench.analyze(request_data)


def test_replay_verifies_actual_computation_not_just_rehashed_summary(request_data):
    report=bench.evaluate(request_data)
    report['result']['descriptive_part_rates']['containment_miss_rate']['estimate']=0
    report['result_ref']=identity(report['result'])
    with pytest.raises(ValueError,match='replay'): bench.verify(report)


def test_externally_frozen_protocol_anchor_detects_coherent_protocol_rewrite(request_data):
    original=identity(request_data['protocol'])
    request_data['protocol']['revision']='B'
    report=bench.evaluate(request_data)
    assert bench.verify(report)['status']=='PASS'
    with pytest.raises(ValueError,match='anchor'): bench.verify(report,expected_protocol_ref=original)


def test_distinct_occurrence_ids_deterministic_results_and_no_mutation(request_data):
    retained=deepcopy(request_data)
    a,b=bench.evaluate(request_data),bench.evaluate(request_data)
    assert request_data==retained
    assert a['result_ref']==b['result_ref'] and a['execution_id']!=b['execution_id']
    va,vb=bench.verify(a),bench.verify(a)
    assert va['verification_id']!=vb['verification_id']
    a['request']['protocol']['revision']='unrelated'
    assert request_data==retained


def test_store_import_reopen_export_preserves_benchmark_result(request_data,tmp_path):
    expected=bench.analyze(request_data)
    path=tmp_path/'qc.sqlite3'; initialize(path);store=StreamStore(path)
    for i,r in enumerate(request_data['streams']):
        store.import_report(f'cycle{i}',r,expected_spec_ref=r['spec_ref'],expected_head_ref=r['receipts'][-1]['receipt_ref'])
    reopened=StreamStore(path)
    request_data['streams']=[reopened.export(f'cycle{i}') for i in range(2)]
    assert bench.analyze(request_data)==expected


def cli(*args):
    return subprocess.run([sys.executable,'-m','ciw.toolpoint_outcome_cli',*map(str,args)],capture_output=True,text=True,timeout=20)


@pytest.mark.parametrize('case',['mixed','missing-inspection','late-alarm','all-clear'])
def test_actual_cli_lifecycle_and_create_only_output(tmp_path,case):
    file=tmp_path/'report.json'
    run=cli('demo','--case',case,'--output',file)
    assert run.returncode==2,run.stderr
    original=file.read_bytes()
    assert cli('verify',file).returncode==0
    assert cli('summary',file).returncode==2
    assert cli('demo','--case',case,'--output',file).returncode==1
    assert file.read_bytes()==original
    assert load(file)['result']['qualification']=='NOT_ESTABLISHED'


def test_cli_example_run_verify_anchor_and_error(tmp_path):
    source=tmp_path/'request.json';dest=tmp_path/'result.json';out=tmp_path/'summary.json'
    assert cli('example','--output',source).returncode==0
    assert cli('run',source,'--output',dest).returncode==2
    anchor=identity(load(source)['protocol'])
    assert cli('verify',dest,'--expected-protocol-ref',anchor).returncode==0
    assert cli('summary',dest,'--output',out).returncode==2
    assert cli('verify',dest,'--expected-protocol-ref',identity('wrong')).returncode==1
    assert cli('run',tmp_path/'absent','--output',tmp_path/'bad').returncode==1


def test_bad_json_and_atomic_publication_failure(request_data,tmp_path,monkeypatch):
    bad=tmp_path/'bad.json';bad.write_text('{"schema":1,"schema":2}')
    assert cli('run',bad,'--output',tmp_path/'report.json').returncode==1
    report=bench.evaluate(request_data)
    import ciw.manufacturing_contracts as c
    def failure(*a,**k): raise OSError('injected link failure')
    monkeypatch.setattr(c.os,'link',failure)
    with pytest.raises(OSError):save_new(tmp_path/'result.json',report)
    assert not (tmp_path/'result.json').exists()
    assert not list(tmp_path.glob('.net-manufacturing-*'))


def rebuilt(report, edit):
    spec,updates=deepcopy(report['spec']),deepcopy(report['updates'])
    edit(spec,updates)
    j=CaptureJournal(spec)
    for u in updates:
        j.append(j.update(u['observation'],received_at_s=u['received_at_s'],final=u['final']))
    return j.report()


@pytest.mark.parametrize('mutation',['foreign_serial','partial_mapping','wrong_part','capture_after_cycle','future_receipt'])
def test_coherently_rebuilt_stream_cannot_change_physical_join(request_data,mutation):
    def edit(spec,updates):
        if mutation=='wrong_part':spec['plan']['part_ref']=identity('wrong-part')
        if mutation=='capture_after_cycle':spec['binding']['started_at_s']+=.4
        for u in updates:
            o=u['observation']
            if mutation=='foreign_serial': o['part_mapping']['items'][0]['serial']='foreign'
            elif mutation=='partial_mapping':o['part_mapping']['items']=o['part_mapping']['items'][:1]
            elif mutation=='wrong_part':o['part_ref']=spec['plan']['part_ref']
            elif mutation=='capture_after_cycle':
                o['started_at_s']+=.4;o['completed_at_s']+=.4;u['received_at_s']+=.4
            else:u['received_at_s']+=30
    request_data['streams'][0]=rebuilt(request_data['streams'][0],edit)
    request_data['protocol']['monitoring_plan_refs']=sorted({identity(r['spec']['plan']) for r in request_data['streams']})
    with pytest.raises(ValueError): bench.analyze(request_data)


def test_unresolved_cavity_mapping_is_precaution_not_attributed_detection(request_data):
    def edit(spec,updates):
        for u in updates:u['observation']['part_mapping']['status']='unresolved'
    request_data['streams'][0]=rebuilt(request_data['streams'][0],edit)
    result=bench.analyze(request_data)
    assert rows_by_serial(result)['p002']['decision']=='PRECAUTIONARY_HOLD'
    assert rows_by_serial(result)['p002']['reason']=='UNRESOLVED_MAPPING'


@pytest.mark.parametrize('mutation',['bad_schema','bad_protocol_schema','bad_confidence','zero_deadline',
    'nonboolean_independence','over_one_rate','duplicate_lot','bad_label','numeric_blind','no_defect','no_conforming'])
def test_remaining_strict_contract_and_absent_denominators(request_data,mutation):
    p=request_data['protocol']
    if mutation=='bad_schema':request_data['schema']='unknown'
    elif mutation=='bad_protocol_schema':p['schema']='unknown'
    elif mutation=='bad_confidence':p['confidence_level']=1
    elif mutation=='zero_deadline':p['decision_deadline_after_start_s']=0
    elif mutation=='nonboolean_independence':p['iid_lot_sampling_declared']=1
    elif mutation=='over_one_rate':p['criteria']['max_lot_miss_upper']=1.1
    elif mutation=='duplicate_lot':p['development_lot_ids']*=2
    elif mutation=='bad_label':request_data['outcomes'][0]['label']='PASS'
    elif mutation=='numeric_blind':request_data['outcomes'][0]['blinded_to_qc']=1
    else:
        for o in request_data['outcomes']:o['label']='CONFORMING' if mutation=='no_defect' else 'NONCONFORMING'
        r=bench.analyze(request_data)
        field='process_alarm_sensitivity' if mutation=='no_defect' else 'false_hold_rate_for_this_endpoint'
        assert r['descriptive_part_rates'][field]['estimate'] is None
        assert r['criteria_screen']['status']=='NOT_ESTABLISHED'
        return
    with pytest.raises(ValueError):bench.analyze(request_data)


def test_satisfying_loose_declared_criteria_never_physically_qualifies_synthetic_data(request_data):
    request_data['protocol']['criteria']={'min_defective_lots':1,'min_conforming_lots':1,'max_lot_miss_upper':1,'max_lot_false_hold_upper':1}
    result=bench.analyze(request_data)
    assert result['criteria_screen']['status']=='DECLARED_CRITERIA_SATISFIED'
    assert result['qualification']=='NOT_ESTABLISHED'
    assert result['evidence_class']=='SYNTHETIC_ONLY'
    assert result['authority']['shipment_release']=='not_authorized'


@pytest.mark.parametrize('mutation',['schema','operation_ref','result_hash','result_type'])
def test_report_identity_and_exact_type_guards(request_data,mutation):
    r=bench.evaluate(request_data)
    if mutation in ('schema','operation_ref'):r[mutation]='wrong'
    elif mutation=='result_hash':r['result_ref']=identity('wrong')
    else:
        r['result']['enrolled_parts']=4.0
        r['result_ref']=identity(r['result'])
    with pytest.raises(ValueError):bench.verify(r)


def test_no_changes_allowed_after_independently_retained_protocol_anchor(request_data):
    r=bench.evaluate(request_data)
    v=bench.verify(r,expected_protocol_ref=identity(request_data['protocol']))
    assert v['protocol_anchor_checked'] is True


def test_inprocess_cli_branches_and_authority(tmp_path,capsys):
    from ciw.toolpoint_outcome_cli import main
    p=tmp_path/'request.json';r=tmp_path/'report.json';v=tmp_path/'verification.json'
    assert main(['example','--output',str(p)])==0
    assert main(['run',str(p),'--output',str(r)])==2
    assert main(['verify',str(r),'--output',str(v)])==0
    assert main(['summary',str(r)])==2
    assert main(['run',str(tmp_path/'absent'),'--output',str(v)])==1
    assert main(['demo','--output',str(tmp_path/'demo.json')])==2
    assert load(v)['authority']['action_authorized'] is False
    with pytest.raises(ValueError):example_request(case='unknown')
