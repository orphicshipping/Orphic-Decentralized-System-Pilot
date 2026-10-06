"""Original native observations + actual NET Sessions; all observations synthetic.

The 'observed' schema test deliberately checks preservation, not physical evidence.
No successful Session, sensor or solver is mocked.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction as F
import json
import random
import subprocess
import sys

import pytest
from ciw import sensor_manufacturing as m, sensor_manufacturing_analysis as a
from ciw import sensor_manufacturing_timing as t, sensor_manufacturing_cli as cli
from ciw.control_plane import ObservationBus
from ciw.control_contracts import bytes_ref
from ciw.core.identities import content_identity
from ciw.operations.runner import seal
from test_sensor_manufacturing import fixture, raw, tree


def timing_fixture(tmp_path, *, counts=(1, 2, 4, 1, 4), values=None,
                   semantics="simulated", limit=2, partitions=None):
    root, spec, plan = fixture(tmp_path, measured=False)
    step = next(r for r in plan['operations'] if r['operation_id']=='inspect')
    resource = next(r for r in plan['resources'] if r['resource_id']=='gauge')
    trials = [{'trial_id':f'timing-trial-{i}', 'partition':('train' if i<3 else 'holdout') if partitions is None else partitions[i],
               'batch_count':x} for i,x in enumerate(counts)]
    protocol = {'schema':t.PROTOCOL, 'definition_ref':bytes_ref((root/m.PLAN_PATH).read_bytes()),
        'timing_basis':'reported_elapsed_duration_not_receipt_timestamp_difference',
        'duration_scope':'one_setup_plus_declared_batches_excluding_queue','setup_policy':'reset_before_every_trial',
        'series':[{'operation_id':'inspect', 'operation_ref':content_identity(step),
            'resource_id':'gauge','resource_ref':content_identity(resource),'batch_size':1,
            'stream_path':'timing-observations.json','model_id':'synthetic-duration-timer',
            'clock_id':'same-local-duration-clock','frame':'resource-elapsed-time','semantics':semantics,
            'trials':trials,'max_holdout_error_s':limit}]}
    protocol_path='timing-protocol.json'; (root/protocol_path).write_bytes(raw(protocol))
    bindings = {}; bus = ObservationBus()
    for i, trial in enumerate(trials):
        duration = values[i] if values is not None else 10+2*trial['batch_count']+([0,0,0,1,-1][i])
        row=bus.observe(identity={'model_id':'synthetic-duration-timer','entity_id':trial['trial_id'],'execution_id':None},
            clock={'id':'same-local-duration-clock','time_s':float(i)},frame='resource-elapsed-time',quantity='duration',
            value=duration,unit='s',uncertainty=None,
            provenance={'provider':'synthetic-duration-fixture','sources':[bytes_ref((root/protocol_path).read_bytes()),protocol['definition_ref']], 'semantics':semantics})
        bindings[trial['trial_id']]=row['record_digest']
    (root/'timing-observations.json').write_bytes(raw(bus.snapshot()))
    study={'schema':t.SCHEMA,'protocol_path':protocol_path,'bindings':bindings}
    (root/t.STUDY_PATH).write_bytes(raw(study))
    for path,kind in [(protocol_path,'model.input'),(t.STUDY_PATH,'model.input'),('timing-observations.json','observations.native')]:
        spec['artifacts'].append({'path':path,'kind':kind,'parents':[], 'tool':{'name':'synthetic-timing-fixture','version':'1'},'origin':'synthetic'})
    spec['native_streams'].append('timing-observations.json')
    if semantics=='observed':spec['mode']='REPLAY'
    return root,spec,plan,protocol,study


def run(root,spec):return t.characterize(m.project.source(root,spec),m.PARAMETERS)


def write_protocol(root,protocol):
    (root/'timing-protocol.json').write_bytes(raw(protocol))


def rebind(root, study, index, mutate):
    stream=json.loads((root/'timing-observations.json').read_bytes())
    row=stream['observations'][index];mutate(row)
    stream['observations'][index]=seal({k:v for k,v in row.items() if k!='record_digest'})
    (root/'timing-observations.json').write_bytes(raw(seal({k:v for k,v in stream.items() if k!='record_digest'})))
    study['bindings'][f'timing-trial-{index}']=stream['observations'][index]['record_digest']
    (root/t.STUDY_PATH).write_bytes(raw(study))


def test_native_duration_setup_batch_fit_and_prediction(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path)
    original=tree(root); result=run(root,spec); study=result['timing_characterization']; row=study['series'][0]
    assert row['fit']['setup_s']=={'value':10.,'exact':['10','1']}
    assert row['fit']['per_batch_s']=={'value':2.,'exact':['2','1']}
    assert row['fit']['normal_equations']=={'sum_residual_q':['0','1'],'sum_x_residual_q':['0','1']}
    assert row['fitted_holdout_errors']['rmse_s']==1
    assert row['baseline_holdout_errors']['mse_s2']==313
    assert row['status']=='CANDIDATE_FOR_REVIEW'
    projection=study['fixed_dispatch_projection']
    assert projection['baseline_duration_s']['value']==89 and projection['candidate_duration_s']['value']==65
    assert projection['model_revision_delta_s']['value']==-24
    assert projection['unchanged_declared_operations']==['assemble','fabricate','procure']
    assert projection['physical_savings_established'] is False and study['model_promotion']=='NOT_PERFORMED'
    assert tree(root)==original


def test_nested_original_v2_analysis_unchanged(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path);source=m.project.source(root,spec)
    base=a.analyze(source,m.PARAMETERS);current=t.characterize(source,m.PARAMETERS)
    assert {k:v for k,v in current.items() if k not in {'schema','timing_characterization'}}=={k:v for k,v in base.items() if k!='schema'}
    assert m.compile_definition(source,m.PARAMETERS)['schedule']==base['schedule']


def test_all_native_observation_fields_and_unknowns_retained(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path)
    originals=json.loads((root/'timing-observations.json').read_bytes())['observations']
    row=run(root,spec)['timing_characterization']['series'][0]
    assert [s['native']['observation'] for s in row['samples']]==originals
    assert row['parameter_covariance'] is None and row['prediction_interval'] is None and row['future_coverage'] is None
    assert row['semantics']=='simulated'


def test_observed_schema_preserved_only_in_replay_mode(tmp_path):
    # Authored fixtures, not measurements: tests that native semantics stay intact.
    root,spec,_,_,_=timing_fixture(tmp_path,semantics='observed')
    assert run(root,spec)['timing_characterization']['series'][0]['semantics']=='observed'
    spec['mode']='SIMULATION'
    with pytest.raises(ValueError,match='Observed or estimated'):run(root,spec)


def test_holdout_never_used_in_fit(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,values=[12,14,18,120,180],limit=1000)
    row=run(root,spec)['timing_characterization']['series'][0]
    assert row['fit']['setup_s']['value']==10 and row['fit']['per_batch_s']['value']==2
    assert 'HELD_OUT_MSE_WORSE_THAN_ORIGINAL_MODEL' in row['reasons']


def test_holdout_drift_keeps_original_plan_and_asks_for_investigation(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,values=[12,14,18,112,118])
    result=run(root,spec);study=result['timing_characterization'];row=study['series'][0]
    assert 'HELD_OUT_ERROR_EXCEEDS_PREDECLARED_LIMIT' in row['reasons']
    assert study['fixed_dispatch_projection'] is None
    assert study['next_experiments'][0]['next_action']=='INVESTIGATE_HELD_OUT_RESIDUALS_AND_APPLICABILITY'
    assert result['analysis']['selection']['audit']['makespan_s']==89


def test_repeated_batch_count_not_identifiable(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,counts=[4]*5,values=[18]*5)
    study=run(root,spec)['timing_characterization'];row=study['series'][0]
    assert row['fit']['status']=='NOT_IDENTIFIABLE' and row['fit']['setup_s'] is None
    assert 'VARY_BATCH_COUNT_TO_SEPARATE_SETUP_AND_BATCH_TIME' in row['reasons']
    assert study['next_experiments'][0]['next_action']=='NEW_PROTOCOL_WITH_VARIED_BATCH_COUNTS'
    assert study['fixed_dispatch_projection'] is None


def test_insufficient_training_is_retained_not_fake_fit(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,partitions=['train','train','holdout','holdout','holdout'])
    row=run(root,spec)['timing_characterization']['series'][0]
    assert row['fit']['status']=='NOT_IDENTIFIABLE'
    assert 'AT_LEAST_THREE_TRAINING_TRIALS_REQUIRED' in row['reasons']


def test_insufficient_holdout_retains_fit_but_no_update(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,partitions=['train','train','train','train','holdout'])
    study=run(root,spec)['timing_characterization']
    assert 'AT_LEAST_TWO_HELD_OUT_TRIALS_REQUIRED' in study['series'][0]['reasons']
    assert study['fixed_dispatch_projection'] is None


def test_negative_intercept_never_clamped(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,values=[1,3,7,1,7])
    row=run(root,spec)['timing_characterization']['series'][0]
    assert row['fit']['setup_s']['value']==-1
    assert 'NEGATIVE_FITTED_SETUP_OR_BATCH_TIME_NO_CLAMP' in row['reasons']
    assert row['candidate_duration_s']['value']==7 and row['status']=='NEEDS_INVESTIGATION'


def test_negative_batch_time_never_clamped(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,values=[9,8,6,9,6])
    row=run(root,spec)['timing_characterization']['series'][0]
    assert row['fit']['per_batch_s']['value']==-1
    assert row['status']=='NEEDS_INVESTIGATION'


def test_target_extrapolation_not_silently_accepted(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,counts=[1,2,3,1,3],values=[12,14,16,12,16])
    study=run(root,spec)['timing_characterization']
    assert 'TARGET_BATCH_COUNT_OUTSIDE_TRAINING_SUPPORT' in study['series'][0]['reasons']
    assert study['fixed_dispatch_projection'] is None


def test_holdout_extrapolation_remains_separate(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path,counts=[1,2,4,1,5],values=[12,14,18,12,20])
    row=run(root,spec)['timing_characterization']['series'][0]
    assert 'HELD_OUT_BATCH_COUNT_OUTSIDE_TRAINING_SUPPORT' in row['reasons']


@pytest.mark.parametrize('seed',range(24))
def test_exact_fit_against_centered_decimal_arithmetic(seed):
    rng=random.Random(seed);xs=[1,2,4,8,16];ys=[3+7*x+rng.randrange(-3,4) for x in xs]
    actual=t._ols(list(zip(xs,map(F,ys))))
    with localcontext() as ctx:
        ctx.prec=60
        xd=list(map(Decimal,xs));yd=list(map(Decimal,ys))
        xb=sum(xd)/len(xd);yb=sum(yd)/len(yd)
        slope=sum((x-xb)*(y-yb) for x,y in zip(xd,yd))/sum((x-xb)**2 for x in xd)
        intercept=yb-slope*xb
        assert actual['setup_s']['value']==pytest.approx(float(intercept),abs=1e-12)
        assert actual['per_batch_s']['value']==pytest.approx(float(slope),abs=1e-12)
        rss=sum((y-intercept-slope*x)**2 for x,y in zip(xd,yd))
        assert actual['train_errors']['mse_s2']==pytest.approx(float(rss/len(xs)),abs=1e-12)


@pytest.mark.parametrize('mutate',[
 lambda x:x.update(schema='wrong'),lambda x:x.update(definition_ref='sha256:'+'0'*64),
 lambda x:x.update(timing_basis='difference_of_receipt_timestamps'),
 lambda x:x.update(duration_scope='includes_unknown_queue'),lambda x:x.update(setup_policy='one_setup_for_all_trials'),
 lambda x:x['series'][0].update(operation_id='absent'),lambda x:x['series'][0].update(operation_ref='sha256:'+'0'*64),
 lambda x:x['series'][0].update(resource_id='assembly'),lambda x:x['series'][0].update(resource_ref='sha256:'+'0'*64),
 lambda x:x['series'][0].update(batch_size=2),lambda x:x['series'][0].update(batch_size=True),
 lambda x:x['series'][0].update(stream_path='link.json'),lambda x:x['series'][0].update(model_id='other'),
 lambda x:x['series'][0].update(clock_id='other'),lambda x:x['series'][0].update(frame='other'),
 lambda x:x['series'][0].update(semantics='estimated'),lambda x:x['series'][0].update(max_holdout_error_s=-1),
 lambda x:x['series'][0].update(max_holdout_error_s=True),lambda x:x['series'][0]['trials'][0].update(batch_count=True),
 lambda x:x['series'][0]['trials'][0].update(batch_count=0),lambda x:x['series'][0]['trials'][0].update(batch_count=129),
 lambda x:x['series'][0]['trials'][0].update(partition='adaptive'),lambda x:x['series'][0]['trials'][0].update(trial_id='missing'),
 lambda x:x['series'][0]['trials'].append(deepcopy(x['series'][0]['trials'][0])),
 lambda x:x['series'].append(deepcopy(x['series'][0])),lambda x:x.update(automatic_promotion=True),
])
def test_protocol_binding_and_structure_refusals(mutate,tmp_path):
    root,spec,_,protocol,_=timing_fixture(tmp_path);mutate(protocol);write_protocol(root,protocol)
    with pytest.raises(ValueError):run(root,spec)


@pytest.mark.parametrize('mutate',[
 lambda x:x.update(unit='ms'),lambda x:x.update(quantity='force'),lambda x:x.update(value=-1),
 lambda x:x.update(value=True),lambda x:x.update(value=[1]),lambda x:x.update(value=None),
 lambda x:x['identity'].update(entity_id='other'),lambda x:x['clock'].update(id='other'),
 lambda x:x['provenance'].update(sources=['sha256:'+'0'*64]),
 lambda x:x['provenance'].update(semantics='estimated'),
])
def test_resealed_native_record_cannot_change_timing_meaning(mutate,tmp_path):
    root,spec,_,_,study=timing_fixture(tmp_path);rebind(root,study,0,mutate)
    with pytest.raises(ValueError):run(root,spec)


def test_duplicate_binding_cannot_leak_training_into_validation(tmp_path):
    root,spec,_,_,study=timing_fixture(tmp_path)
    study['bindings']['timing-trial-4']=study['bindings']['timing-trial-0']
    (root/t.STUDY_PATH).write_bytes(raw(study))
    with pytest.raises(ValueError,match='reused'):run(root,spec)


def test_extra_unmapped_observation_refused(tmp_path):
    root,spec,_,_,study=timing_fixture(tmp_path)
    stream=json.loads((root/'timing-observations.json').read_bytes())
    row=deepcopy(stream['observations'][0]);row['identity']['entity_id']='excluded-outlier'
    stream['observations'].append(seal({k:v for k,v in row.items() if k!='record_digest'}))
    (root/'timing-observations.json').write_bytes(raw(seal({k:v for k,v in stream.items() if k!='record_digest'})))
    with pytest.raises(ValueError,match='accounted'):run(root,spec)


def test_missing_or_extra_bindings_refuse(tmp_path):
    root,spec,_,_,study=timing_fixture(tmp_path)
    study['bindings']['unplanned']='sha256:'+'0'*64;(root/t.STUDY_PATH).write_bytes(raw(study))
    with pytest.raises(ValueError,match='Unplanned'):run(root,spec)
    study['bindings'].pop('unplanned');study['bindings'].pop('timing-trial-4');(root/t.STUDY_PATH).write_bytes(raw(study))
    with pytest.raises(ValueError,match='Missing'):run(root,spec)


def test_prospective_partition_bytes_cannot_be_changed_silently(tmp_path):
    root,spec,_,protocol,_=timing_fixture(tmp_path)
    protocol['series'][0]['trials'][0]['partition']='holdout';write_protocol(root,protocol)
    with pytest.raises(ValueError,match='protocol/definition'):run(root,spec)


def test_native_session_replay_and_exact_recovery(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path);before=tree(root)
    first=m.execute(m.project.source(root,spec),tmp_path/'session',characterize_timing=True)
    assert first['status']=='RETAINED'
    old=tree(tmp_path/'session')
    second=m.replay(tmp_path/'session',tmp_path/'replay',first['snapshot_ref'])
    assert first['data']==second['data'] and first['evidence_id']==second['evidence_id']
    assert first['execution_id']!=second['execution_id'] and first['result_id']!=second['result_id']
    m.export_inputs(tmp_path/'session',tmp_path/'restored',first['snapshot_ref'])
    assert tree(tmp_path/'restored')==before and tree(root)==before and tree(tmp_path/'session')==old
    assert first['claims']['verification_id'] is None and first['claims']['equipment_control'] is False


def test_semantic_failure_retained_without_timing_result(tmp_path):
    root,spec,_,protocol,_=timing_fixture(tmp_path);protocol['series'][0]['batch_size']=2;write_protocol(root,protocol)
    result=m.execute(m.project.source(root,spec),tmp_path/'refused',characterize_timing=True)
    assert result['status']=='REFUSED' and result['data'] is None and result['refusal']
    assert m.inspect(tmp_path/'refused')['result_id'] is None


def test_flags_no_clobber_anchors_and_report(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path);source=m.project.source(root,spec)
    for kw in [dict(analyze=True,characterize_timing=True),dict(characterize_timing=1)]:
        with pytest.raises(ValueError):m.execute(source,tmp_path/'invalid',**kw)
    assert not (tmp_path/'invalid').exists()
    result=m.execute(source,tmp_path/'session',characterize_timing=True)
    with pytest.raises(FileExistsError):m.execute(source,tmp_path/'session',characterize_timing=True)
    with pytest.raises(ValueError):m.inspect(tmp_path/'session','sha256:'+'0'*64)
    report=tmp_path/'timing.html';cli.report(tmp_path/'session',report,result['snapshot_ref'])
    assert 'Evidence-bound timing characterization' in report.read_text()
    with pytest.raises(FileExistsError):cli.report(tmp_path/'session',report)
    with pytest.raises(ValueError):cli.report(tmp_path/'session',tmp_path/'session/changed.html')


def test_actual_cli_and_no_hardware_contact(tmp_path,monkeypatch):
    import socket
    def denied(*args,**kwargs):raise AssertionError('No network expected')
    monkeypatch.setattr(socket,'create_connection',denied)
    root,spec,_,_,_=timing_fixture(tmp_path);request=tmp_path/'request.json';request.write_bytes(raw(spec))
    assert run(root,spec)['timing_characterization']['series'][0]['fit']['status']=='IDENTIFIED_DESCRIPTIVE_FIT'
    result=subprocess.run([sys.executable,'-m','ciw.sensor_manufacturing_cli','run',str(root),str(request),
        '--characterize-timing','--output-dir',str(tmp_path/'cli')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['data']['schema']==t.RESULT


def test_explicit_protocol_preparation_and_original_input_preservation(tmp_path):
    root,spec,_=fixture(tmp_path,measured=False);source=m.project.source(root,spec);before=tree(root)
    protocol=t.prepare_protocol(source,operation_id='inspect',resource_id='gauge',stream_path='new-timing.json',
        model_id='timer',clock_id='local-elapsed',frame='elapsed',semantics='observed',
        train_batches=[1,2,4],holdout_batches=[1,4],max_holdout_error_s=2.)
    assert protocol['definition_ref']==bytes_ref((root/m.PLAN_PATH).read_bytes())
    assert len(protocol['series'][0]['trials'])==5
    assert len({r['trial_id'] for r in protocol['series'][0]['trials']})==5
    assert tree(root)==before


@pytest.mark.parametrize('change',[
 {'train_batches':[4,4,4]}, {'train_batches':[1,2]}, {'holdout_batches':[1]},
 {'holdout_batches':[1,5]}, {'train_batches':[1,2,3]}, {'train_batches':[True,2,4]},
 {'semantics':'estimated'}, {'resource_id':'assembly'}, {'max_holdout_error_s':-1},
 {'stream_path':'manufacturing.json'}, {'stream_path':'../timing.json'},
])
def test_protocol_preparation_rejects_uninformative_or_invalid_designs(change,tmp_path):
    root,spec,_=fixture(tmp_path,measured=False)
    args=dict(operation_id='inspect',resource_id='gauge',stream_path='new-timing.json',model_id='timer',clock_id='local',
              frame='elapsed',semantics='simulated',train_batches=[1,2,4],holdout_batches=[1,4],max_holdout_error_s=2.)
    with pytest.raises(ValueError):t.prepare_protocol(m.project.source(root,spec),**{**args,**change})


def test_cli_prepares_protocol_without_touching_the_project(tmp_path):
    root,spec,_=fixture(tmp_path,measured=False);before=tree(root);request=tmp_path/'request.json';request.write_bytes(raw(spec))
    command=[sys.executable,'-m','ciw.sensor_manufacturing_cli','timing-protocol',str(root),str(request),
             '--operation-id','inspect','--resource-id','gauge','--stream-path','timing.json','--model-id','timer',
             '--clock-id','local','--frame','elapsed','--semantics','simulated',
             '--train-batches','1','2','4','--holdout-batches','1','4','--max-holdout-error-s','2',
             '--output',str(tmp_path/'protocol.json')]
    r=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)['device_contact'] is False and tree(root)==before
    again=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert again.returncode==2


def test_supplied_native_covariance_retained_without_fake_parameter_covariance(tmp_path):
    from ciw.core.covariance import create_covariance_artifact
    root,spec,_,_,study=timing_fixture(tmp_path)
    covariance=create_covariance_artifact(matrix=[[.25]],quantity_ids=['duration'],units=['s'],
        frame='resource-elapsed-time',reference_values=[12],method='synthetic-explicit-covariance',
        basis={'kind':'observation','id':'synthetic-timer'},
        provenance={'provider':'synthetic-timer','source_evidence_ids':[],'source_covariance_ids':[]},
        assumptions=['Authored test covariance, not a calibrated physical clock'])
    rebind(root,study,0,lambda row:row.update(uncertainty=covariance))
    result=run(root,spec)['timing_characterization']['series'][0]
    assert result['samples'][0]['native']['observation']['uncertainty']==covariance
    assert result['parameter_covariance'] is None
    assert result['fit']['setup_s']['value']==10


def test_characterization_cannot_transfer_to_another_eligible_resource(tmp_path):
    root,spec,plan,protocol,study=timing_fixture(tmp_path)
    alt=deepcopy(next(r for r in plan['resources'] if r['resource_id']=='gauge'))
    alt['resource_id']='gauge-alternative';plan['resources'].append(alt)
    step=next(r for r in plan['operations'] if r['operation_id']=='inspect');step['allowed_resources'].append(alt['resource_id'])
    (root/m.PLAN_PATH).write_bytes(raw(plan));protocol['definition_ref']=bytes_ref((root/m.PLAN_PATH).read_bytes())
    protocol['series'][0].update(operation_ref=content_identity(step),resource_id=alt['resource_id'],resource_ref=content_identity(alt))
    write_protocol(root,protocol)
    for i in range(5):
        rebind(root,study,i,lambda row:row['provenance'].update(sources=[bytes_ref((root/'timing-protocol.json').read_bytes()),protocol['definition_ref']]))
    result=run(root,spec)['timing_characterization']
    assert 'CHARACTERIZED_RESOURCE_IS_NOT_THE_SELECTED_DISPATCH_RESOURCE' in result['series'][0]['reasons']
    assert result['fixed_dispatch_projection'] is None


def test_successful_timing_review_cannot_masquerade_as_v2_process_speedup(tmp_path):
    root,spec,_,_,_=timing_fixture(tmp_path);source=m.project.source(root,spec)
    m.execute(source,tmp_path/'timing',characterize_timing=True)
    m.execute(source,tmp_path/'ordinary-v2',analyze=True)
    with pytest.raises(ValueError,match='completed v2'):
        a.compare([tmp_path/'ordinary-v2',tmp_path/'timing'])


def test_empty_holdout_and_empty_training_each_remain_unresolved(tmp_path):
    for part in ['train','holdout']:
        case=tmp_path/part;case.mkdir()
        root,spec,_,_,_=timing_fixture(case,partitions=[part]*5)
        result=run(root,spec)['timing_characterization']
        assert result['fixed_dispatch_projection'] is None
        assert result['series'][0]['status']=='NEEDS_INVESTIGATION'
