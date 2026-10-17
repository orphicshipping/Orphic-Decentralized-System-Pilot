"""Real NET components, synthetic data, independent linear-algebra oracles."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from ciw import sensor_manufacturing as m, sensor_manufacturing_timing as t
from ciw import sensor_manufacturing_uncertainty as u, sensor_manufacturing_analysis as a
from ciw.control_plane import ObservationBus
from ciw.control_contracts import bytes_ref
from ciw.core.covariance import create_covariance_artifact, covariance_identity
from ciw.core.identities import content_identity
from test_sensor_manufacturing_timing import timing_fixture
from test_sensor_manufacturing import raw, tree


def attach(root, spec, matrix=None, factor=2):
    base=t.characterize(m.project.source(root,spec),m.PARAMETERS)
    items=[r for group in base['timing_characterization']['series'] for r in group['samples']]
    refs=[r['native']['observation']['record_digest'] for r in items]
    protocol=base['timing_characterization']['protocol_ref'];study=base['timing_characterization']['study_ref']
    options={'schema':u.SCHEMA,'protocol_ref':protocol,'study_ref':study,
             'covariance_path':'timing-covariance.json' if matrix is not None else None,'coverage_multiplier':factor}
    if matrix is not None:
        scalar=[r['native']['observation']['uncertainty'] for r in items]
        cov=create_covariance_artifact(matrix=matrix,quantity_ids=refs,units=['s']*len(refs),frame=u.FRAME,
            reference_values=[r['native']['observation']['value'] for r in items],method='explicit synthetic test covariance',
            basis={'kind':'observation','id':content_identity({'protocol_ref':protocol,'observation_refs':refs})},
            provenance={'provider':'test-fixture','source_evidence_ids':[protocol,study,*refs],
                        'source_covariance_ids':[v['covariance_id'] for v in scalar if v]},
            assumptions=['synthetic error model; no physical process measured'])
        (root/'timing-covariance.json').write_bytes(raw(cov))
    (root/u.OPTIONS_PATH).write_bytes(raw(options))
    for path in [u.OPTIONS_PATH]+(['timing-covariance.json'] if matrix is not None else []):
        if not any(r['path']==path for r in spec['artifacts']):
            spec['artifacts'].append({'path':path,'kind':'model.input','parents':[],
                'tool':{'name':'synthetic-covariance','version':'1'},'origin':'synthetic'})
    return options


def result(root,spec):return u.characterize(m.project.source(root,spec),m.PARAMETERS)

def cov_matrix(n=5, common=0., variance=.01):
    return [[common+(variance if i==j else 0) for j in range(n)] for i in range(n)]


def change_cov(root,change):
    path=root/'timing-covariance.json';cov=json.loads(path.read_bytes());change(cov)
    cov['covariance_id']=covariance_identity(cov);path.write_bytes(raw(cov))


def reanchor(root, spec, plan, protocol, study):
    """New synthetic trial generation after a synthetic protocol change, not old evidence relabeling."""
    (root/m.PLAN_PATH).write_bytes(raw(plan));protocol['definition_ref']=bytes_ref((root/m.PLAN_PATH).read_bytes())
    steps={r['operation_id']:r for r in plan['operations']};resources={r['resource_id']:r for r in plan['resources']}
    for g in protocol['series']:
        g['operation_ref']=content_identity(steps[g['operation_id']]);g['resource_ref']=content_identity(resources[g['resource_id']])
    (root/'timing-protocol.json').write_bytes(raw(protocol));bus=ObservationBus();study['bindings']={}
    for group in protocol['series']:
        for trial in group['trials']:
            count=trial['batch_count'];tid=trial['trial_id']
            val=10+2*count if group['operation_id']=='inspect' else 5+count
            row=bus.observe(identity={'model_id':group['model_id'],'entity_id':tid,'execution_id':None},
                clock={'id':group['clock_id'],'time_s':float(len(study['bindings']))},frame=group['frame'],quantity='duration',
                value=val,unit='s',uncertainty=None,provenance={'provider':'new-synthetic-trial','sources':[
                    bytes_ref((root/'timing-protocol.json').read_bytes()),protocol['definition_ref']], 'semantics':'simulated'})
            study['bindings'][tid]=row['record_digest']
    (root/'timing-observations.json').write_bytes(raw(bus.snapshot()));(root/t.STUDY_PATH).write_bytes(raw(study))


def test_no_covariance_preserves_original_fit_without_independence(tmp_path):
    root,spec,*_=timing_fixture(tmp_path)
    base=t.characterize(m.project.source(root,spec),m.PARAMETERS);got=result(root,spec)
    assert got['timing_characterization']==base['timing_characterization']
    assert got['timing_uncertainty']['status']=='JOINT_COVARIANCE_NOT_SUPPLIED'
    assert got['timing_uncertainty']['propagated_covariance'] is None
    attach(root,spec,None)
    assert result(root,spec)['timing_uncertainty']['status']=='JOINT_COVARIANCE_NOT_SUPPLIED'


def test_covariance_propagation_preserves_all_original_bytes_and_means(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);before=tree(root);attach(root,spec,cov_matrix())
    original=t.characterize(m.project.source(root,spec),m.PARAMETERS);got=result(root,spec);x=got['timing_uncertainty']
    assert got['timing_characterization']==original['timing_characterization']
    assert all(tree(root)[k]==v for k,v in before.items())
    assert x['series'][0]['review_status']=='CANDIDATE_WITH_CONDITIONAL_UNCERTAINTY'
    assert x['source_covariance']==json.loads((root/'timing-covariance.json').read_bytes())
    assert x['propagated_covariance']['provenance']['source_covariance_ids']==[x['source_covariance']['covariance_id']]
    assert x['route_component']['total_route_uncertainty'] is None
    assert x['route_component']['status']=='LOCAL_MEASUREMENT_COMPONENT_ONLY'
    assert x['future_prediction_interval'] is None and x['coverage_probability'] is None


def test_common_error_cancels_from_residual_but_not_prediction(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix())
    independent=result(root,spec)['timing_uncertainty']['series'][0]
    attach(root,spec,cov_matrix(common=4.))
    shared=result(root,spec)['timing_uncertainty']['series'][0]
    assert shared['target_mean']['variance_s2']-independent['target_mean']['variance_s2']==pytest.approx(4.)
    assert shared['parameters'][0]['variance_s2']-independent['parameters'][0]['variance_s2']==pytest.approx(4.)
    assert shared['parameters'][1]==independent['parameters'][1]
    assert shared['heldout_residuals']==independent['heldout_residuals']


def test_singular_covariance_keeps_zero_residual_uncertainty_and_exposes_mismatch(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix(common=4.,variance=0))
    x=result(root,spec)['timing_uncertainty'];r=x['series'][0]
    assert r['target_mean']['standard_uncertainty_s']==2
    assert r['parameters'][1]['variance_s2']==0
    assert all(v['variance_s2']==0 and v['zero_variance_consistency']=='NONZERO_RESIDUAL_WITH_ZERO_VARIANCE' for v in r['heldout_residuals'])
    assert r['review_status']=='NEEDS_INVESTIGATION'
    assert x['route_component']['status']=='WITHHELD_UNCERTAINTY_REVIEW'


def test_exact_shared_error_with_consistent_holdout_does_not_make_prediction_exact(tmp_path):
    root,spec,*_=timing_fixture(tmp_path,values=[12,14,18,12,18]);attach(root,spec,cov_matrix(common=4.,variance=0))
    r=result(root,spec)['timing_uncertainty']['series'][0]
    assert r['review_status']=='CANDIDATE_WITH_CONDITIONAL_UNCERTAINTY'
    assert r['target_mean']['standard_uncertainty_s']==2
    assert all(v['variance_s2']==0 for v in r['heldout_residuals'])


def test_large_uncertainty_does_not_turn_failed_validation_into_success(tmp_path):
    root,spec,*_=timing_fixture(tmp_path,values=[12,14,18,30,50]);attach(root,spec,cov_matrix(variance=100))
    r=result(root,spec)['timing_uncertainty']['series'][0]
    assert r['review_status']=='NEEDS_INVESTIGATION'


def test_unknown_cross_dependence_never_inferred_from_scalar_uncertainties(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,None)
    assert result(root,spec)['timing_uncertainty']['matrix_exact'] is None


def test_rank_deficiency_retains_covariance_without_inventing_coefficients(tmp_path):
    root,spec,*_=timing_fixture(tmp_path,counts=[4]*5,values=[18]*5);attach(root,spec,cov_matrix())
    x=result(root,spec)['timing_uncertainty'];assert x['status']=='NO_IDENTIFIABLE_SERIES'
    assert x['propagated_covariance'] is None and x['source_covariance'] is not None


def test_full_cross_operation_covariance_reaches_route_component(tmp_path):
    root,spec,plan,protocol,study=timing_fixture(tmp_path)
    g=deepcopy(protocol['series'][0]);g.update(operation_id='assemble',resource_id='assembly')
    g['trials']=[{**r,'trial_id':'assembly-'+r['trial_id']} for r in g['trials']]
    protocol['series'].append(g);reanchor(root,spec,plan,protocol,study)
    attach(root,spec,cov_matrix(n=10,common=4.))
    x=result(root,spec)['timing_uncertainty'];targets=[r['target_index'] for r in x['series']]
    assert len(targets)==2
    assert a.decoded(x['matrix_exact'][targets[0]][targets[1]])==4
    route=x['route_component'];assert route['status']=='LOCAL_MEASUREMENT_COMPONENT_ONLY'
    diagonal=sum(a.decoded(x['matrix_exact'][i][i]) for i in targets)
    assert a.decoded(route['variance_exact'])==diagonal+8


def test_tied_critical_paths_do_not_receive_a_fabricated_route_derivative(tmp_path):
    root,spec,plan,protocol,study=timing_fixture(tmp_path)
    next(r for r in plan['operations'] if r['operation_id']=='procure')['per_batch_s']=30
    reanchor(root,spec,plan,protocol,study);attach(root,spec,cov_matrix())
    route=result(root,spec)['timing_uncertainty']['route_component']
    assert route['status']=='WITHHELD_NONDIFFERENTIABLE_PATH_TIE'
    assert route['standard_uncertainty_s'] is None


@pytest.mark.parametrize('seed',range(24))
def test_independent_dense_signed_covariance_oracle(seed,tmp_path):
    root,spec,*_=timing_fixture(tmp_path,limit=100)
    rng=np.random.default_rng(seed);r=rng.integers(-4,5,size=(5,5));cov=(r@r.T+np.eye(5,dtype=int)).tolist()
    attach(root,spec,cov)
    x=result(root,spec)['timing_uncertainty'];design=np.array([[1.,1.],[1.,2.],[1.,4.]])
    h=np.linalg.solve(design.T@design,design.T)
    w=np.zeros((5,5));w[:2,:3]=h;w[2,:3]=[1.,4.]@h
    w[3,:3]=-np.array([1.,1.])@h;w[3,3]=1
    w[4,:3]=-np.array([1.,4.])@h;w[4,4]=1
    native=np.array(x['propagated_covariance']['matrix'])[:5,:5]
    np.testing.assert_allclose(native,w@np.array(cov)@w.T,rtol=1e-12,atol=1e-12)
    assert x['linear_map_exact'][0][3:]==[['0','1'],['0','1']]
    assert x['linear_map_exact'][1][3:]==[['0','1'],['0','1']]


@pytest.mark.parametrize('change',[
    lambda c:c['quantity_ids'].reverse(),lambda c:c['quantity_ids'].pop(),
    lambda c:c['units'].__setitem__(0,'ms'),lambda c:c.update(frame='other'),
    lambda c:c['reference_values'].__setitem__(0,13.),lambda c:c['reference_values'].__setitem__(0,True),
    lambda c:c['basis'].update(id='other'),lambda c:c['basis'].update(kind='parameter'),
    lambda c:c['provenance'].update(source_evidence_ids=[]),lambda c:c.update(extra=True),
    lambda c:c['matrix'][0].__setitem__(0,-1),lambda c:c['matrix'][0].__setitem__(0,True),
])
def test_original_covariance_binding_refusals(change,tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix());change_cov(root,change)
    with pytest.raises(ValueError):result(root,spec)


@pytest.mark.parametrize('field,value',[('schema','other'),('study_ref','sha256:'+'0'*64),
    ('protocol_ref','sha256:'+'0'*64),('coverage_multiplier',True),('coverage_multiplier',0),
    ('coverage_multiplier',-1),('coverage_multiplier',11),('covariance_path',u.OPTIONS_PATH)])
def test_options_binding_refusals(field,value,tmp_path):
    root,spec,*_=timing_fixture(tmp_path);options=attach(root,spec,cov_matrix());options[field]=value
    (root/u.OPTIONS_PATH).write_bytes(raw(options))
    with pytest.raises(ValueError):result(root,spec)


def test_no_covariance_symmetrizing_or_small_negative_clipping(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix(variance=1))
    change_cov(root,lambda c:(c['matrix'][0].__setitem__(1,1.000000000001),c['matrix'][1].__setitem__(0,1.000000000001)))
    with pytest.raises(ValueError,match='positive semidefinite'):result(root,spec)
    attach(root,spec,cov_matrix(variance=1))
    change_cov(root,lambda c:c['matrix'][0].__setitem__(1,1e-13))
    with pytest.raises(ValueError,match='exact covariance symmetry'):result(root,spec)


def test_tiny_positive_covariance_is_not_zero(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix(variance=1e-40))
    x=result(root,spec)['timing_uncertainty']['series'][0]
    assert 0<x['target_mean']['standard_uncertainty_s']<1e-19
    assert a.decoded(x['target_mean']['variance_exact'])>0


@pytest.mark.parametrize('mean,var,k,limit,status',[(0,1,2,2,'BAND_WITHIN_PREDECLARED_LIMIT'),
    (3,0,2,2,'BAND_OUTSIDE_PREDECLARED_LIMIT'),(3,1,1,2,'BAND_OVERLAPS_PREDECLARED_LIMIT'),
    (-1,1,1,2,'BAND_WITHIN_PREDECLARED_LIMIT')])
def test_expanded_screen_inclusive_exact_boundaries(mean,var,k,limit,status):
    assert u.band(F(mean),F(var),F(k),F(limit))['screen']==status


def test_source_covariance_id_drift_is_rejected(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix())
    path=root/'timing-covariance.json';x=json.loads(path.read_bytes());x['matrix'][0][0]=2;path.write_bytes(raw(x))
    with pytest.raises(ValueError,match='identity'):result(root,spec)


def test_scalar_covariance_conflict_and_ancestry_are_not_silently_overridden(tmp_path):
    from test_sensor_manufacturing_timing import rebind
    root,spec,_,_,study=timing_fixture(tmp_path)
    def update(row):
        row['uncertainty']=create_covariance_artifact(matrix=[[.02]],quantity_ids=['duration'],units=['s'],frame=row['frame'],
            reference_values=[row['value']],method='synthetic marginal',basis={'kind':'observation','id':'scalar'},
            provenance={'provider':'test','source_evidence_ids':row['provenance']['sources'],'source_covariance_ids':[]},assumptions=['test'])
    rebind(root,study,0,update)
    attach(root,spec,cov_matrix(variance=.01))
    with pytest.raises(ValueError,match='scalar uncertainty'):result(root,spec)
    attach(root,spec,cov_matrix(variance=.02));assert result(root,spec)['timing_uncertainty']['propagated_covariance']
    change_cov(root,lambda c:c['provenance'].update(source_covariance_ids=[]))
    with pytest.raises(ValueError,match='scalar uncertainty'):result(root,spec)


def test_real_session_replay_exact_recovery_and_historical_modes(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix());before=tree(root);source=m.project.source(root,spec)
    first=m.execute(source,tmp_path/'session',timing_uncertainty=True);assert first['status']=='RETAINED'
    second=m.replay(tmp_path/'session',tmp_path/'replay',first['snapshot_ref'])
    assert first['data']==second['data'] and first['evidence_id']==second['evidence_id']
    assert first['execution_id']!=second['execution_id'] and first['result_id']!=second['result_id']
    assert first['execution_id']!=first['result_id']
    m.export_inputs(tmp_path/'session',tmp_path/'restored');assert before==tree(tmp_path/'restored')==tree(root)
    for name,kwargs in [('v1',{}),('v2',{'analyze':True}),('timing',{'characterize_timing':True})]:
        x=m.execute(source,tmp_path/name,**kwargs);assert m.inspect(tmp_path/name)==x
    from ciw.sensor_manufacturing_cli import report
    page=tmp_path/'report.html';report(tmp_path/'session',page)
    assert 'Correlated timing uncertainty' in page.read_text() and 'Parameter uncertainty is not established.' not in page.read_text()
    with pytest.raises(FileExistsError):report(tmp_path/'session',page)
    with pytest.raises(ValueError):m.inspect(tmp_path/'session','sha256:'+'0'*64)


def test_native_refusal_is_retained(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix());change_cov(root,lambda c:c['quantity_ids'].reverse())
    got=m.execute(m.project.source(root,spec),tmp_path/'refused',timing_uncertainty=True)
    assert got['status']=='REFUSED' and got['data'] is None
    assert (tmp_path/'refused/workspace.json').is_file()


def test_cli_and_no_network(tmp_path,monkeypatch):
    import socket
    def denied(*args,**kwargs):raise AssertionError('No network is allowed')
    monkeypatch.setattr(socket,'create_connection',denied)
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix())
    assert result(root,spec)['timing_uncertainty']['propagated_covariance'] is not None
    request=tmp_path/'request.json';request.write_bytes(raw(spec))
    child=subprocess.run([sys.executable,'-m','ciw.sensor_manufacturing_cli','run',str(root),str(request),
        '--timing-uncertainty','--output-dir',str(tmp_path/'cli')],capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)['data']['schema']==u.RESULT


@pytest.mark.parametrize('kwargs',[{'timing_uncertainty':1},{'timing_uncertainty':True,'analyze':True},
    {'timing_uncertainty':True,'characterize_timing':True}])
def test_explicit_exclusive_mode_before_output(tmp_path,kwargs):
    with pytest.raises(ValueError):m.execute({},tmp_path/'bad',**kwargs)
    assert not (tmp_path/'bad').exists()


def test_linear_map_budget_and_underflow_refuse():
    with pytest.raises(ValueError):u.propagate([[F(1)]],[[F(1)]]*65)
    with pytest.raises(ValueError):u._float(F(1,10**400))


def test_changing_only_holdout_variances_never_enters_parameter_covariance(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix())
    first=result(root,spec)['timing_uncertainty']
    change_cov(root,lambda c:(c['matrix'][3].__setitem__(3,100),c['matrix'][4].__setitem__(4,100)))
    second=result(root,spec)['timing_uncertainty']
    assert [row[:3] for row in first['matrix_exact'][:3]]==[row[:3] for row in second['matrix_exact'][:3]]
    assert first['series'][0]['heldout_residuals']!=second['series'][0]['heldout_residuals']


def test_all_scalar_uncertainties_still_do_not_supply_cross_trial_covariance(tmp_path):
    from test_sensor_manufacturing_timing import rebind
    root,spec,_,_,study=timing_fixture(tmp_path)
    def update(row):
        row['uncertainty']=create_covariance_artifact(matrix=[[1]],quantity_ids=['duration'],units=['s'],frame=row['frame'],
            reference_values=[row['value']],method='synthetic scalar',basis={'kind':'observation','id':row['identity']['entity_id']},
            provenance={'provider':'test','source_evidence_ids':row['provenance']['sources'],'source_covariance_ids':[]},assumptions=['test'])
    for i in range(5):rebind(root,study,i,update)
    got=result(root,spec)
    assert all(v['native']['observation']['uncertainty'] for v in got['timing_characterization']['series'][0]['samples'])
    assert got['timing_uncertainty']['status']=='JOINT_COVARIANCE_NOT_SUPPLIED'


def test_semantic_validator_rejects_resealed_derived_covariance(tmp_path):
    root,spec,*_=timing_fixture(tmp_path);attach(root,spec,cov_matrix());source=m.project.source(root,spec)
    got=u.characterize(source,m.PARAMETERS);matrix=got['timing_uncertainty']['propagated_covariance']
    matrix['matrix'][0][0]+=1;matrix['covariance_id']=covariance_identity(matrix)
    with pytest.raises(ValueError,match='differs from retained'):
        m._validate_uncertainty(u.OPERATION,got,source,m.PARAMETERS,m.PARAMETERS)


def test_unknown_covariance_report_does_not_claim_zero_uncertainty(tmp_path):
    from ciw.sensor_manufacturing_cli import report
    root,spec,*_=timing_fixture(tmp_path);m.execute(m.project.source(root,spec),tmp_path/'run',timing_uncertainty=True)
    report(tmp_path/'run',tmp_path/'report.html');text=(tmp_path/'report.html').read_text()
    assert 'joint covariance not supplied' in text and 'Parameter uncertainty is not established.' in text


def test_exact_singular_negative_cross_terms_are_preserved():
    c=[[F(1),F(-1)],[F(-1),F(1)]]
    assert u.propagate([[F(1),F(1)],[F(1),F(-1)]],c)==[[F(0),F(0)],[F(0),F(4)]]


def test_preserving_venv_interpreter_path_keeps_the_qualification_environment(tmp_path):
    import venv
    env=tmp_path/'venv';venv.EnvBuilder(with_pip=False).create(env)
    executable=env/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    child=subprocess.run([str(executable.absolute()),'-I','-c','import sys; print(sys.prefix); print(sys.base_prefix)'],
                         capture_output=True,text=True,check=True,timeout=30)
    prefix,base=child.stdout.strip().splitlines()
    assert Path(prefix)==env and prefix!=base
