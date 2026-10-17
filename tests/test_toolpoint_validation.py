"""Independent arithmetic, hostile-input, detector-outcome and durable-store journeys."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ciw.manufacturing_contracts import content_identity as identity, save_new
from ciw.toolpoint_binomial import confidence_bounds, _tail_at_least, GRID_BITS
from ciw.toolpoint_validation import assess, evaluate, verify
from ciw.toolpoint_validation_example import example_request
from ciw.toolpoint_stream import CaptureJournal


@lru_cache(maxsize=2)
def _fixture(profile='native'):
    if profile == 'legacy' and importlib.util.find_spec('ciw.toolpoint_quality') is None:
        pytest.skip('Optional legacy evaluator is not installed')
    return example_request(profile=profile)


@pytest.fixture
def request_data():
    return deepcopy(_fixture())


def _reseal_manifest(request):
    request['manifest']['protocol_ref'] = identity(request['protocol'])
    inventory = {s['sample_id']: s for s in request['manifest']['samples']}
    for outcome in request['outcomes']:
        if outcome['sample_id'] in inventory:
            outcome['sample_ref'] = identity(inventory[outcome['sample_id']])


def _case(result, sid):
    return next(r for r in result['cases'] if r['sample_id'] == sid)


def _replace_stream(request, index, report):
    request['streams'][index]['report'] = report
    cid = report['spec']['binding']['cycle_id']
    for sample in request['manifest']['samples']:
        if sample['cycle_id'] == cid:
            sample.update(stream_spec_ref=report['spec_ref'], stream_head_ref=report['receipts'][-1]['receipt_ref'])
    _reseal_manifest(request)


def _replay(report, edit):
    journal = CaptureJournal(report['spec'])
    for original in report['updates']:
        update = deepcopy(original)
        edit(update)
        journal.append(journal.update(update['observation'], received_at_s=update['received_at_s'], final=update['final']))
    return journal.report()


@pytest.mark.parametrize('profile', ['native', 'legacy'])
def test_synthetic_gold_confusion(profile):
    result = assess(deepcopy(_fixture(profile)))
    assert result['status'] == 'SYNTHETIC_ONLY'
    assert result['criteria_status'] == 'CRITERIA_NOT_MET'
    m = result['part_metrics']
    assert m['enrolled'] == 16
    assert m['defect_present'] == {'ALARM': 2, 'NO_ALARM': 1, 'ABSTAIN': 1}
    assert m['defect_absent'] == {'ALARM': 2, 'NO_ALARM': 7, 'ABSTAIN': 2}
    assert m['unresolved_labels'] == 1
    assert m['missing_captures'] == 2
    assert m['alarm_sensitivity']['value'] == .5
    assert m['timely_alarm_sensitivity']['value'] == .25
    assert m['nondefect_hold_rate']['numerator'] == 4
    assert m['defect_without_stream_hold_rate']['numerator'] == 1
    assert result['group_inference']['defect_group_count'] == 4
    assert result['group_inference']['nondefect_group_count'] == 3
    assert len(result['review_queue']) == 8


def test_sensor_fault_not_true_positive(request_data):
    r = _case(assess(request_data), 'case.2.2')
    assert r['label'] == 'DEFECT_PRESENT'
    assert r['prediction'] == 'ABSTAIN'
    assert r['stream_hold'] is True
    assert r['timely_alarm'] is False
    assert not r['alarm_finding_refs']


def test_missed_defect_is_visible_not_a_pass(request_data):
    r = _case(assess(request_data), 'case.4.1')
    assert r['prediction'] == 'NO_ALARM'
    assert r['label'] == 'DEFECT_PRESENT'
    assert r['stream_hold'] is False


def test_late_alarm_not_backdated_to_sample(request_data):
    result = assess(request_data)
    a = _case(result, 'case.0.2')
    b = _case(result, 'case.5.2')
    assert a['timing_status'] == 'TIMELY'
    assert b['timing_status'] == 'LATE'
    assert b['prediction'] == 'ALARM'
    assert Decimal(b['first_alarm_receipt_relative_interval']['lower_decimal']) == Decimal('.905')


def test_one_packet_fault_uses_receipt_not_original_sample(request_data):
    report = request_data['streams'][0]['report']
    j = CaptureJournal(report['spec'])
    final = report['updates'][-1]
    j.append(j.update(final['observation'], received_at_s=final['received_at_s'], final=True))
    _replace_stream(request_data, 0, j.report())
    c = _case(assess(request_data), 'case.0.2')
    assert c['prediction'] == 'ALARM'
    assert c['timing_status'] == 'LATE'


@pytest.mark.parametrize('uncertainty,expected', [(None,'UNKNOWN'),(.021,'UNKNOWN'),(.02,'TIMELY')])
def test_receipt_uncertainty_not_ignored(request_data, uncertainty, expected):
    request_data['streams'][0]['receipt_time_uncertainty_s'] = uncertainty
    assert _case(assess(request_data),'case.0.2')['timing_status'] == expected


@pytest.mark.parametrize('deadline,status', [(.6,'LATE'),(.605,'AMBIGUOUS'),(.61,'AMBIGUOUS'),(.615,'TIMELY')])
def test_closed_deadline_boundaries(request_data, deadline, status):
    request_data['protocol']['decision_deadline_s'] = deadline
    _reseal_manifest(request_data)
    assert _case(assess(request_data),'case.0.2')['timing_status'] == status


def test_tiny_nonzero_receipt_uncertainty_preserved(request_data):
    request_data['protocol']['decision_deadline_s'] = .61
    request_data['streams'][0]['receipt_time_uncertainty_s'] = 1e-100
    _reseal_manifest(request_data)
    row = _case(assess(request_data), 'case.0.2')
    assert row['timing_status'] == 'AMBIGUOUS'
    assert Decimal(row['first_alarm_receipt_relative_interval']['upper_decimal']) > Decimal('.61')


def test_local_fault_does_not_count_on_other_cavity(request_data):
    r = assess(request_data)
    assert _case(r, 'case.0.1')['prediction'] == 'NO_ALARM'
    assert _case(r, 'case.0.2')['prediction'] == 'ALARM'
    assert _case(r, 'case.3.1')['prediction'] == 'ALARM'
    assert _case(r, 'case.3.2')['prediction'] == 'ALARM'


def test_target_rule_filter_keeps_stream_hold(request_data):
    request_data['protocol']['target_rule_ids'] = ['sensor.cavity.1.pressure']
    _reseal_manifest(request_data)
    row = _case(assess(request_data),'case.0.2')
    assert row['prediction'] == 'ABSTAIN'
    assert row['stream_hold']


def test_nonfinal_does_not_become_negative(request_data):
    report = request_data['streams'][1]['report']
    j = CaptureJournal(report['spec'])
    for u in report['updates'][:-1]:
        j.append(u)
    _replace_stream(request_data, 1, j.report())
    r = assess(request_data)
    assert r['part_metrics']['open_captures'] == 2
    assert _case(r, 'case.1.1')['prediction'] == 'ABSTAIN'


def test_missing_capture_and_missing_label_remain_enrolled(request_data):
    request_data['streams'] = []
    request_data['outcomes'] = []
    r = assess(request_data)
    assert r['part_metrics']['enrolled'] == r['part_metrics']['missing_captures'] == r['part_metrics']['unresolved_labels'] == 16
    assert all(row['stream_hold'] for row in r['cases'])
    assert r['part_metrics']['alarm_sensitivity']['value'] is None
    assert r['group_inference']['timely_detection']['status'] == 'NO_TRIALS'


@pytest.mark.parametrize('field,value,reason', [
    ('label','INDETERMINATE','INDETERMINATE_LABEL'),
    ('independence','derived_from_detector','INDEPENDENCE_NOT_DECLARED'),
    ('independence','not_established','INDEPENDENCE_NOT_DECLARED'),
    ('evidence_ref',None,'MISSING_OUTCOME_EVIDENCE'),
    ('calibration_ref',None,'MISSING_OUTCOME_EVIDENCE')])
def test_bad_truth_never_becomes_negative(request_data, field, value, reason):
    request_data['outcomes'][1][field] = value
    row = _case(assess(request_data), 'case.0.2')
    assert row['label'] == 'UNRESOLVED'
    assert row['label_reason'] == reason


@pytest.mark.parametrize('target', ['result','receipt','update','assessment','observation','signal'])
def test_circular_ground_truth_flagged(request_data,target):
    report=request_data['streams'][0]['report']
    values={'result':report['result_ref'], 'receipt':report['receipts'][-1]['receipt_ref'],
        'update':report['receipts'][-1]['update_ref'], 'assessment':report['receipts'][-1]['assessment_ref'],
        'observation':identity(report['updates'][-1]['observation']),
        'signal':report['updates'][-1]['observation']['signals'][0]['source_ref']}
    request_data['outcomes'][1]['evidence_ref']=values[target]
    row=_case(assess(request_data),'case.0.2')
    assert row['label_reason']=='DETECTOR_ARTIFACT_USED_AS_GROUND_TRUTH'
    assert row['label']=='UNRESOLVED'


@pytest.mark.parametrize('field,value', [
    ('schema','unknown'),('source_kind','observed'),('confidence',True),('confidence',1),
    ('confidence',.4),('confidence',float('nan')),('minimum_defect_groups',0),
    ('minimum_defect_groups',1.0),('minimum_nondefect_groups',257),('minimum_timely_detection',-1),
    ('maximum_false_hold',1.1),('maximum_receipt_uncertainty_s',-1),('decision_deadline_s',0),
    ('development_group_ids',['duplicate','duplicate']),('development_cycle_refs',['bad']),
    ('target_rule_ids',[]),('target_rule_ids',['unknown.rule']),('frozen_at_s',11),
    ('inspection_method_ref','bad'),('source_kind','retained_observation')])
def test_invalid_protocol_refused(request_data,field,value):
    request_data['protocol'][field]=value
    with pytest.raises((ValueError,TypeError)):
        _reseal_manifest(request_data)
        assess(request_data)


@pytest.mark.parametrize('field', ['unknown','action_authorized','hardware_actuation'])
def test_unknown_or_escalated_authority_refused(request_data,field):
    request_data['protocol'][field]=True
    _reseal_manifest(request_data)
    with pytest.raises(ValueError): assess(request_data)


def test_plan_hash_freezes_thresholds(request_data):
    request_data['protocol']['monitoring_plan']['rules'][0]['limits']['upper']=16000000
    _reseal_manifest(request_data)
    with pytest.raises(ValueError,match='Changed thresholds'):
        assess(request_data)


@pytest.mark.parametrize('selector',['group','cycle'])
def test_no_development_holdout_leakage(request_data,selector):
    sample=request_data['manifest']['samples'][0]
    request_data['protocol']['development_group_ids' if selector=='group' else 'development_cycle_refs']=[sample['group_id' if selector=='group' else 'cycle_ref']]
    _reseal_manifest(request_data)
    with pytest.raises(ValueError,match='leakage'): assess(request_data)


@pytest.mark.parametrize('field,value', [
    ('sample_id','case.0.1'),('serial','s0.1'),('cavity_id','cavity.1'),('cavity_id','unknown'),
    ('group_id','other.group'),('started_at_s',11),('cycle_ref','bad'),
    ('stream_spec_ref','bad'),('stream_head_ref','bad'),('mapping_evidence_ref','bad')])
def test_inventory_integrity(request_data,field,value):
    request_data['manifest']['samples'][1][field]=value
    _reseal_manifest(request_data)
    with pytest.raises(ValueError): assess(request_data)


def test_whole_cycle_cannot_be_counted_twice(request_data):
    samples=request_data['manifest']['samples']
    for s in samples[2:4]: s['cycle_ref']=samples[0]['cycle_ref']
    _reseal_manifest(request_data)
    with pytest.raises(ValueError,match='renamed'): assess(request_data)


def test_omitting_bad_cavity_is_refused(request_data):
    request_data['manifest']['samples'].pop(1)
    request_data['outcomes'].pop(1)
    with pytest.raises(ValueError,match='every configured cavity'): assess(request_data)


def test_external_manifest_anchor_detects_whole_trial_removal(request_data):
    expected=identity(request_data['manifest'])
    request_data['manifest']['samples']=request_data['manifest']['samples'][2:]
    request_data['outcomes']=request_data['outcomes'][2:]
    request_data['streams']=request_data['streams'][1:]
    with pytest.raises(ValueError,match='anchor'): assess(request_data,expected_manifest_ref=expected)


def test_external_protocol_anchor_detects_relaxed_acceptance(request_data):
    expected=identity(request_data['protocol'])
    request_data['protocol']['minimum_timely_detection']=0
    _reseal_manifest(request_data)
    with pytest.raises(ValueError,match='anchor'): assess(request_data,expected_protocol_ref=expected)


@pytest.mark.parametrize('field,value', [
    ('schema','bad'),('sample_id','unenrolled'),('sample_ref','sha256:'+'0'*64),
    ('label','PASS'),('independence','guaranteed'),('method_ref','sha256:'+'0'*64),
    ('characteristic_id','other.defect'),('condition','ejection'),('source_kind','retained_observation'),
    ('measured_at_s',0),('measured_at_s',31),('measured_at_s',True),('assessor_id',''),
    ('evidence_ref','bad'),('calibration_ref','bad')])
def test_invalid_outcome_refused(request_data,field,value):
    request_data['outcomes'][0][field]=value
    with pytest.raises(ValueError): assess(request_data)


@pytest.mark.parametrize('collection',['streams','outcomes'])
def test_duplicate_inputs_refused(request_data,collection):
    request_data[collection].append(deepcopy(request_data[collection][0]))
    with pytest.raises(ValueError): assess(request_data)


def test_grouping_not_part_level_confidence(request_data):
    # Two distinct defective cycles in one run count as one defective group.
    for s in request_data['manifest']['samples']:
        if s['group_id']=='synthetic.run.5': s['group_id']='synthetic.run.0'
    _reseal_manifest(request_data)
    r=assess(request_data)
    assert r['part_metrics']['alarm_sensitivity']['denominator']==4
    assert r['group_inference']['defect_group_count']==3
    # The timely first cycle cannot mask the late defective part in the same run.
    group=next(g for g in r['groups'] if g['group_id']=='synthetic.run.0')
    assert group['all_defects_timely_detected'] is False


@pytest.mark.parametrize('mode,reference',[('descriptive_only','ref'),('independent_exchangeable_groups',None)])
def test_descriptive_mode_has_no_confidence_promotion(request_data,mode,reference):
    request_data['protocol']['sampling_design']={'mode':mode,'evidence_ref':identity('design') if reference else None}
    _reseal_manifest(request_data)
    r=assess(request_data)
    assert r['group_inference']['timely_detection'] is None
    assert 'SAMPLING_ASSUMPTIONS_NOT_DECLARED' in r['reasons']


def test_byte_type_tampering_rehashed_cannot_pass(request_data):
    report=evaluate(request_data)
    report['result']['cases'][0]['stream_hold']=0
    report['result_ref']=identity(report['result'])
    with pytest.raises(ValueError,match='fresh replay'): verify(report)


@pytest.mark.parametrize('path',[('result','status'),('result','authority'),('request','schema')])
def test_report_tampering(request_data,path):
    report=evaluate(request_data)
    report[path[0]][path[1]]='tampered'
    with pytest.raises(ValueError): verify(report)


def test_execution_and_verification_identities_separate(request_data):
    one=evaluate(request_data); two=evaluate(request_data)
    assert one['result_ref']==two['result_ref']
    assert one['execution_id']!=two['execution_id']
    a=verify(one,expected_protocol_ref=identity(request_data['protocol']),expected_manifest_ref=identity(request_data['manifest']))
    b=verify(one)
    assert a['verification_id']!=b['verification_id']!=one['execution_id']
    assert a['protocol_anchor_checked'] and a['manifest_anchor_checked']
    assert not b['manifest_anchor_checked']


def test_inputs_never_modified(request_data):
    before=identity(request_data)
    assess(request_data);evaluate(request_data)
    assert identity(request_data)==before


@pytest.mark.parametrize('n', [1,2,3,5,10,20,50,100,256])
@pytest.mark.parametrize('fraction', [0,.1,.5,.9,1])
def test_confidence_bounds_against_independent_scipy(n,fraction):
    binomtest = pytest.importorskip('scipy.stats').binomtest
    k=round(n*fraction)
    ci=binomtest(k,n).proportion_ci(confidence_level=.95,method='exact')
    r=confidence_bounds(k,n)
    lo,hi=map(lambda x:float(r[x]), ('lower_decimal','upper_decimal'))
    # SciPy's floating root solver has its own tolerance; bracket width is exact below.
    assert abs(lo-ci.low)<2e-11
    assert abs(hi-ci.high)<2e-11
    for bounds in (r['lower_root_bracket'],r['upper_root_bracket']):
        assert Fraction(bounds[1])-Fraction(bounds[0]) <= Fraction(1,2**GRID_BITS)


@pytest.mark.parametrize('n',[1,2,4,10,40,100])
def test_zero_failures_not_zero_failure_risk(n):
    r=confidence_bounds(0,n)
    expected=1-.025**(1/n)
    assert float(r['upper_decimal'])==pytest.approx(expected,abs=1e-14)
    assert float(r['upper_decimal'])>0
    assert r['lower_decimal']=='0'


@pytest.mark.parametrize('n',range(1,8))
@pytest.mark.parametrize('p',[Fraction(1,5),Fraction(1,2),Fraction(4,5)])
def test_exact_tail_integer_comparison_against_enumeration(n,p):
    # Independently enumerate all 2**n Bernoulli outcomes, not a binomial routine.
    from itertools import product
    for k in range(1,n+1):
        tail=sum((p**sum(bits)*(1-p)**(n-sum(bits)) for bits in product((0,1),repeat=n) if sum(bits)>=k),Fraction())
        for alpha in (Fraction(1,40),Fraction(1,4),Fraction(1,2)):
            assert _tail_at_least(k,n,p.numerator,p.denominator,alpha)==(tail>=alpha)


@pytest.mark.parametrize('k,n,alpha', [(True,5,'0.025'),(1.0,5,'0.025'),(1,0,'0.025'),(-1,5,'0.025'),
    (1,257,'0.025'),(1,5,'0'),(1,5,'NaN'),(1,5,'0.9'),(1,5,0.025),(1,5,'abc')])
def test_bad_binomial_contracts(k,n,alpha):
    with pytest.raises(ValueError): confidence_bounds(k,n,tail_probability=alpha)


def test_zero_trials_is_unknown():
    r=confidence_bounds(0,0)
    assert r['lower_decimal'] is r['upper_decimal'] is r['estimate'] is None


def test_binomial_complement_symmetry():
    for k in range(11):
        a,b=confidence_bounds(k,10),confidence_bounds(10-k,10)
        assert Fraction(a['lower_decimal'])==1-Fraction(b['upper_decimal'])


def test_zero_error_endpoint_exact_comparison():
    # n=1, alpha=1/4 is dyadic: lower_root is bracketed, never rounded upward.
    r=confidence_bounds(1,1,tail_probability='0.25')
    assert Fraction(r['lower_decimal'])<=Fraction(1,4)
    assert Fraction(r['lower_root_bracket'][1])==Fraction(1,4)


def test_store_reopen_join_preserves_study(tmp_path,request_data):
    from ciw.toolpoint_stream_store import initialize,StreamStore
    from ciw.toolpoint_validation_store import resolve
    db=tmp_path/'captures.sqlite3';initialize(db)
    bindings=[]
    store=StreamStore(db)
    for i,envelope in enumerate(request_data['streams']):
        report=envelope['report'];sid=f'capture{i}'
        store.import_report(sid,report,expected_spec_ref=report['spec_ref'],expected_head_ref=report['receipts'][-1]['receipt_ref'])
        bindings.append({'stream_id':sid,'cycle_id':report['spec']['binding']['cycle_id'],
                         'receipt_time_uncertainty_s':envelope['receipt_time_uncertainty_s']})
    before=db.read_bytes()
    empty=deepcopy(request_data);empty['streams']=[]
    restored=resolve(empty,db,bindings)
    assert identity(assess(restored))==identity(assess(request_data))
    assert db.read_bytes()==before
    assert not empty['streams']
    wrong=deepcopy(bindings);wrong[0]['stream_id']='capture1'
    with pytest.raises(ValueError): resolve(empty,db,wrong)
    with pytest.raises(ValueError): resolve(request_data,db,bindings)
    with pytest.raises(ValueError): resolve(empty,db,bindings+[bindings[0]])


def _cli(*args):
    env=dict(os.environ)  # Respect the source or isolated-installed qualification environment.
    return subprocess.run([sys.executable,'-m','ciw.toolpoint_validation_cli',*map(str,args)],env=env,capture_output=True,text=True)


def test_cli_actual_journey(tmp_path):
    request=tmp_path/'request.json';report=tmp_path/'report.json';receipt=tmp_path/'receipt.json'
    assert _cli('example','--output',request).returncode==0
    run=_cli('evaluate',request,'--output',report)
    assert run.returncode==2,run.stderr
    assert json.loads(run.stdout)['status']=='SYNTHETIC_ONLY'
    checked=_cli('verify',report,'--output',receipt)
    assert checked.returncode==0,checked.stderr
    assert json.loads(checked.stdout)['status']=='PASS'
    summary=_cli('summary',report)
    assert summary.returncode==2
    assert json.loads(summary.stdout)['part_metrics']['enrolled']==16
    assert _cli('evaluate',request,'--output',report).returncode==1
    assert _cli('verify',report,'--expected-manifest-ref','sha256:'+'0'*64).returncode==1


def test_cli_duplicate_json_refused(tmp_path):
    bad=tmp_path/'bad.json';bad.write_text('{"schema":"a","schema":"b"}')
    assert _cli('evaluate',bad,'--output',tmp_path/'out.json').returncode==1


def test_cli_invalid_output_directory_fails_no_partial(tmp_path):
    target=tmp_path/'missing'/'output.json'
    assert _cli('demo','--output',target).returncode==1
    assert not target.exists()


def _small_permissive_study(request):
    keep={'synthetic.validation.cycle.'+str(i) for i in (0,1,3)}
    request['manifest']['samples']=[s for s in request['manifest']['samples'] if s['cycle_id'] in keep]
    selected={s['sample_id'] for s in request['manifest']['samples']}
    request['outcomes']=[o for o in request['outcomes'] if o['sample_id'] in selected]
    request['streams']=[e for e in request['streams'] if e['report']['spec']['binding']['cycle_id'] in keep]
    for o in request['outcomes']:
        if o['sample_id'].startswith('case.3.'):
            o['label']='DEFECT_PRESENT'
    p=request['protocol']
    p.update(minimum_defect_groups=1,minimum_nondefect_groups=1,minimum_timely_detection=.1,maximum_false_hold=.99)
    _reseal_manifest(request)
    return request


def test_permissive_synthetic_success_never_qualifies(request_data):
    r=assess(_small_permissive_study(request_data))
    assert not r['reasons']
    assert r['criteria_status']=='CRITERIA_MET_FOR_REVIEW'
    assert r['status']=='SYNTHETIC_ONLY'
    assert r['authority']['physical_qualification']=='not_established'
    assert r['authority']['action_authorized'] is False


def test_declared_observation_is_review_not_certification(request_data):
    request=_small_permissive_study(request_data)
    request['protocol']['source_kind']='retained_observation'
    request['protocol']['monitoring_plan']['source_kind']='retained_observation'
    for index,e in enumerate(request['streams']):
        report=deepcopy(e['report'])
        spec=report['spec'];spec['plan']['source_kind']='retained_observation'
        spec['binding']['source_kind']='retained_observation'
        j=CaptureJournal(spec)
        for update in report['updates']:
            update['observation']['source_kind']='retained_observation'
            j.append(j.update(update['observation'],received_at_s=update['received_at_s'],final=update['final']))
        _replace_stream(request,index,j.report())
    for o in request['outcomes']: o['source_kind']='retained_observation'
    _reseal_manifest(request)
    r=assess(request)
    assert r['status']=='CRITERIA_MET_FOR_REVIEW'
    assert r['authority']['physical_qualification']=='not_established'
    assert r['authority']['source_authentication']=='not_established'
    assert r['authority']['action_authorized'] is False


def test_unresolved_mapping_is_not_a_local_true_positive(request_data):
    original=request_data['streams'][0]['report']
    def edit(update): update['observation']['part_mapping']['status']='unresolved'
    modified=_replay(original,edit)
    _replace_stream(request_data,0,modified)
    r=assess(request_data)
    for sid in ('case.0.1','case.0.2'):
        c=_case(r,sid)
        assert c['prediction']=='ABSTAIN'
        assert c['reason']=='UNRESOLVED_CAVITY_MAPPING'
        assert c['stream_hold']


def test_missing_legacy_mapping_evidence_is_not_a_pass():
    request=deepcopy(_fixture('legacy'))
    for s in request['manifest']['samples'][:2]: s['mapping_evidence_ref']=None
    _reseal_manifest(request)
    r=assess(request)
    assert _case(r,'case.0.2')['prediction']=='ABSTAIN'


@pytest.mark.parametrize('mutation',['unknown_cycle','start_type','source','bad_head','future_receipt'])
def test_stream_manifest_contracts(request_data,mutation):
    if mutation=='bad_head':
        for s in request_data['manifest']['samples'][:2]:s['stream_head_ref']=identity('wrong')
    elif mutation=='start_type':
        for s in request_data['manifest']['samples'][:2]:s['started_at_s']=10.0
    elif mutation=='source':
        request_data['protocol']['source_kind']='retained_observation'
    elif mutation=='unknown_cycle':
        for s in request_data['manifest']['samples'][:2]:s['cycle_id']='unknown'
    else:
        request_data['assessed_at_s']=24.1
    _reseal_manifest(request_data)
    with pytest.raises(ValueError):assess(request_data)


def test_missing_cavity_class_blocks_review(request_data):
    r=_small_permissive_study(request_data)
    for o in r['outcomes']:
        if o['sample_id'].endswith('.1'):o['label']='DEFECT_ABSENT'
    result=assess(r)
    assert 'CAVITY_CLASS_COVERAGE_INCOMPLETE:cavity.1' in result['reasons']


def test_uncertain_outcome_in_defective_group_prevents_group_success(request_data):
    request_data['outcomes'][0]['independence']='not_established'
    r=assess(request_data)
    g=next(g for g in r['groups'] if g['group_id']=='synthetic.run.0')
    assert g['contains_known_defect']
    assert not g['all_defects_timely_detected']


@pytest.mark.parametrize('collection,field,value',[
    ('manifest','schema','bad'),('manifest','protocol_ref','sha256:'+'0'*64),
    ('manifest','samples',[]),('protocol','monitoring_plan',{}),
    ('protocol','sampling_design',{'mode':'wrong','evidence_ref':None})])
def test_additional_contract_refusals(request_data,collection,field,value):
    request_data[collection][field]=value
    with pytest.raises(ValueError):assess(request_data)


def test_no_future_manifest_or_freeze(request_data):
    request_data['assessed_at_s']=0
    with pytest.raises(ValueError):assess(request_data)
    request_data['assessed_at_s']=1
    request_data['protocol']['frozen_at_s']=2
    _reseal_manifest(request_data)
    with pytest.raises(ValueError):assess(request_data)


def test_report_refusal_on_rehashed_false_perfect_statistics(request_data):
    report=evaluate(request_data)
    report['result']['part_metrics']['timely_alarm_sensitivity']={'numerator':4,'denominator':4,'value':1.0}
    report['result']['criteria_status']='CRITERIA_MET_FOR_REVIEW'
    report['result_ref']=identity(report['result'])
    with pytest.raises(ValueError,match='fresh replay'):verify(report)


def test_cli_demo_and_store_actual_journey(tmp_path,request_data):
    from ciw.toolpoint_stream_store import initialize,StreamStore
    db=tmp_path/'qc.sqlite3';initialize(db);store=StreamStore(db)
    bindings=[]
    for i,e in enumerate(request_data['streams']):
        r=e['report'];sid='s'+str(i)
        store.import_report(sid,r,expected_spec_ref=r['spec_ref'],expected_head_ref=r['receipts'][-1]['receipt_ref'])
        bindings.append({'cycle_id':r['spec']['binding']['cycle_id'],'stream_id':sid,'receipt_time_uncertainty_s':.005})
    request_data['streams']=[]
    request=tmp_path/'template.json';bindingfile=tmp_path/'bindings.json';out=tmp_path/'evaluation.json'
    save_new(request,request_data);save_new(bindingfile,bindings)
    run=_cli('evaluate-store',request,'--database',db,'--bindings',bindingfile,'--output',out)
    assert run.returncode==2,run.stderr
    assert _cli('verify',out).returncode==0
    demo=tmp_path/'demo.json'
    assert _cli('demo','--profile','native','--output',demo).returncode==2
    assert _cli('verify',demo).returncode==0


def test_save_publication_failure_leaves_no_output(monkeypatch,tmp_path,request_data):
    import ciw.manufacturing_contracts as contracts
    out=tmp_path/'fail.json'
    def fail(*args,**kwargs):raise OSError('injected link failure')
    monkeypatch.setattr(contracts.os,'link',fail)
    with pytest.raises(OSError):save_new(out,evaluate(request_data))
    assert not out.exists()
    assert not list(tmp_path.iterdir())


def test_stream_result_tampering_cannot_feed_study(request_data):
    report=request_data['streams'][0]['report']
    report['result']['latched_findings']=[]
    report['result_ref']=identity(report['result'])
    with pytest.raises(ValueError):assess(request_data)


@pytest.mark.parametrize('change',['serial','missing_item','evidence'])
def test_cavity_mapping_join_refusals(request_data,change):
    if change=='serial':
        request_data['manifest']['samples'][0]['serial']='foreign.serial'
    elif change=='evidence':
        for s in request_data['manifest']['samples'][:2]:s['mapping_evidence_ref']=identity('different-mapping')
    else:
        report=request_data['streams'][0]['report']
        def edit(update):update['observation']['part_mapping']['items'].pop()
        _replace_stream(request_data,0,_replay(report,edit))
    _reseal_manifest(request_data)
    with pytest.raises(ValueError,match='mapping|Mapping'):
        assess(request_data)


def test_bad_request_schema(request_data):
    request_data['schema']='wrong'
    with pytest.raises(ValueError):assess(request_data)


@pytest.mark.parametrize('field,value',[('schema','wrong'),('operation_ref','sha256:'+'0'*64),('execution_id','wrong')])
def test_bad_report_contracts(request_data,field,value):
    report=evaluate(request_data);report[field]=value
    with pytest.raises(ValueError):verify(report)


def test_published_nist_binomial_example():
    # NIST e-Handbook 7.2.4.1: 4/20, two-sided 90% confidence.
    r=confidence_bounds(4,20,tail_probability='0.05')
    assert float(r['lower_decimal'])==pytest.approx(.071354,abs=1e-6)
    assert float(r['upper_decimal'])==pytest.approx(.401029,abs=1e-6)


def test_store_conflicting_manifest_anchors(tmp_path,request_data):
    from ciw.toolpoint_validation_store import resolve
    request_data['streams']=[]
    request_data['manifest']['samples'][1]['stream_head_ref']=identity('different-head')
    with pytest.raises(ValueError,match='Conflicting'):
        resolve(request_data,tmp_path/'no-database-needed',[])


def test_store_cannot_relabel_cycle(tmp_path,request_data):
    from ciw.toolpoint_stream_store import initialize,StreamStore
    from ciw.toolpoint_validation_store import resolve
    report=request_data['streams'][0]['report']; db=tmp_path/'source.sqlite3';initialize(db)
    StreamStore(db).import_report('original',report,expected_spec_ref=report['spec_ref'],expected_head_ref=report['receipts'][-1]['receipt_ref'])
    request_data['streams']=[]
    for s in request_data['manifest']['samples'][:2]:s['cycle_id']='relabeled'
    binding=[{'cycle_id':'relabeled','stream_id':'original','receipt_time_uncertainty_s':0}]
    with pytest.raises(ValueError,match='different enrolled cycle'):
        resolve(request_data,db,binding)


def test_cli_inprocess_paths(tmp_path,capsys,request_data):
    from ciw.toolpoint_validation_cli import main
    request=tmp_path/'source.json';report=tmp_path/'report.json';out=tmp_path/'receipt.json'
    save_new(request,request_data)
    assert main(['example','--output',str(tmp_path/'example.json')])==0
    assert main(['demo','--output',str(tmp_path/'demo.json')])==2
    assert main(['evaluate',str(request),'--output',str(report)])==2
    assert main(['verify',str(report),'--output',str(out)])==0
    assert main(['summary',str(report)])==2
    assert main(['verify',str(tmp_path/'missing.json')])==1
    assert capsys.readouterr().out


def test_cli_inprocess_store(tmp_path,capsys,request_data):
    from ciw.toolpoint_validation_cli import main
    from ciw.toolpoint_stream_store import initialize,StreamStore
    report=request_data['streams'][0]['report'];db=tmp_path/'source.sqlite3';initialize(db)
    StreamStore(db).import_report('c0',report,expected_spec_ref=report['spec_ref'],expected_head_ref=report['receipts'][-1]['receipt_ref'])
    request_data['streams']=[]
    request=tmp_path/'request.json';bindings=tmp_path/'bindings.json'
    save_new(request,request_data)
    save_new(bindings,[{'cycle_id':report['spec']['binding']['cycle_id'],'stream_id':'c0','receipt_time_uncertainty_s':0}])
    assert main(['evaluate-store',str(request),'--database',str(db),'--bindings',str(bindings),'--output',str(tmp_path/'study.json')])==2
    assert capsys.readouterr().out


def test_default_targets_are_reachable_within_declared_capacity(request_data,tmp_path):
    # 36/36 detected defective GROUPS plus 0/36 falsely held nondefective GROUPS
    # meet the stated 90% / 10% targets with the 95% family-confidence split.
    from ciw.toolpoint_stream_example import fixture,prefix
    from ciw.toolpoint_stream import make_spec
    plan,_=fixture(case='nominal')
    plan['cavity_ids']=['cavity.1']
    plan['sensors']=[s for s in plan['sensors'] if s['sensor_id']!='sensor.cavity.2']
    plan['rules']=[r for r in plan['rules'] if r['sensor_id']!='sensor.cavity.2']
    p=request_data['protocol'];p['monitoring_plan']=plan;p['target_rule_ids']=[r['rule_id'] for r in plan['rules']]
    request_data['streams']=[];request_data['outcomes']=[];request_data['manifest']['samples']=[]
    for i in range(72):
        _,obs=fixture(case='high-nozzle' if i<36 else 'nominal')
        start=i+1;cid=f'capacity.cycle.{i}';cref=identity({'synthetic_capacity_cycle':i})
        obs.update(cycle_id=cid,cycle_evidence_ref=cref,started_at_s=start,completed_at_s=start+.8)
        obs['signals']=[s for s in obs['signals'] if s['sensor_id']!='sensor.cavity.2']
        mapping_ref=identity({'synthetic_capacity_mapping':i})
        obs['part_mapping']={'status':'cavity_resolved','evidence_ref':mapping_ref,'items':[{'serial':f'capacity.{i}','cavity_id':'cavity.1'}]}
        j=CaptureJournal(make_spec(plan,obs))
        for through,final in ((.6,False),(.8,True)):
            capture=prefix(plan,obs,through,final=final)
            received=float(Decimal(start)+Decimal(str(through))+Decimal('.01'))
            j.append(j.update(capture,received_at_s=received,final=final))
        report=j.report()
        request_data['streams'].append({'report':report,'receipt_time_uncertainty_s':.005})
        sample={'sample_id':f'capacity.case.{i}','group_id':f'capacity.run.{i}','cycle_id':cid,'cycle_ref':cref,
                'cavity_id':'cavity.1','serial':f'capacity.{i}','started_at_s':start,'stream_spec_ref':report['spec_ref'],
                'stream_head_ref':report['receipts'][-1]['receipt_ref'],'mapping_evidence_ref':mapping_ref}
        request_data['manifest']['samples'].append(sample)
        request_data['outcomes'].append({'schema':'ciw.toolpoint-independent-outcome.v1','sample_id':sample['sample_id'],
                'sample_ref':identity(sample),'characteristic_id':p['characteristic_id'],'source_kind':'synthetic',
                'method_ref':p['inspection_method_ref'],'condition':'conditioned','label':'DEFECT_PRESENT' if i<36 else 'DEFECT_ABSENT',
                'independence':'independent_measurement','evidence_ref':identity({'synthetic_label':i}),
                'calibration_ref':identity('synthetic-cal'),'assessor_id':'synthetic.inspector','measured_at_s':start+1})
    request_data['assessed_at_s']=75;_reseal_manifest(request_data)
    r=assess(request_data)
    assert r['criteria_status']=='CRITERIA_MET_FOR_REVIEW',r['reasons']
    assert r['status']=='SYNTHETIC_ONLY'
    assert r['group_inference']['defect_group_count']==36
    assert r['group_inference']['nondefect_group_count']==36
    save_new(tmp_path/'capacity-study.json',evaluate(request_data))
