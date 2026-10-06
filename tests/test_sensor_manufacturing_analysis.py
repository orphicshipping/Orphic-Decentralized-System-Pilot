"""Actual NET analysis Sessions plus an independent exhaustive Decimal oracle."""
from copy import deepcopy
from decimal import Decimal
from itertools import permutations, product
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest
from ciw import sensor_manufacturing as m, sensor_manufacturing_analysis as a, sensor_project as p
from ciw.control_contracts import bytes_ref
from test_sensor_manufacturing import fixture, raw, tree


def attach(root, spec, plan, factor=.2, budget=2048):
    data = {'schema':'ciw.manufacturing-analysis-options.v1',
            'definition_ref':bytes_ref((root/m.PLAN_PATH).read_bytes()), 'node_budget':budget,
            'basis':'declared_duration_intervals_not_probability',
            'duration_bounds':{s['operation_id']:{f:[round(s[f]*(1-factor),8),round(s[f]*(1+factor),8)]
                for f in ('setup_s','per_batch_s')} for s in plan['operations']}}
    (root/a.OPTIONS_PATH).write_bytes(raw(data))
    spec['artifacts'].append({'path':a.OPTIONS_PATH,'kind':'model.input','parents':[m.PLAN_PATH],
                             'tool':{'name':'explicit test assumptions','version':'1'},'origin':'synthetic'})
    return data


def basic_problem(seed=0, count=4):
    rng=random.Random(seed)
    resources=[{'resource_id':r,'kind':'cell','capabilities':['work'],'limits':[],
                'batch_capacity':4,'rate_per_hour':None,'currency':None} for r in ('r1','r2')]
    steps=[]
    for i in range(count):
        steps.append({'operation_id':f's{i}','kind':'assemble',
            'depends_on':[f's{j}' for j in range(i) if rng.random()<.25],
            'artifact_paths':['board.kicad_pcb'],'requires':['work'],'demands':[],
            'allowed_resources':rng.choice([['r1'],['r2'],['r1','r2']]),
            'setup_s':0,'per_batch_s':rng.randint(1,8),'batch_size':1,'adapter_id':'planning.only'})
    return {'order':{'quantity':1},'resources':resources,'operations':steps}


def solve(plan,budget=20000):
    steps={s['operation_id']:s for s in plan['operations']}
    resources={r['resource_id']:r for r in plan['resources']}
    baseline=m._schedule(plan,steps,resources)
    return a.optimize(plan,baseline,budget)


def oracle(plan):
    """Independent arithmetic and exhaustive ordering/assignment, no optimizer calls."""
    steps={s['operation_id']:s for s in plan['operations']}
    best=None
    for order in permutations(steps):
        seen=set();valid=True
        for name in order:
            if not set(steps[name]['depends_on'])<=seen: valid=False;break
            seen.add(name)
        if not valid:continue
        for choices in product(*(steps[n]['allowed_resources'] for n in order)):
            finishes={};available={r['resource_id']:Decimal(0) for r in plan['resources']}
            for n,rid in zip(order,choices):
                s=steps[n]; start=max([available[rid]]+[finishes[p] for p in s['depends_on']])
                batches=(plan['order']['quantity']+s['batch_size']-1)//s['batch_size']
                finishes[n]=start+Decimal(str(s['setup_s']))+batches*Decimal(str(s['per_batch_s']))
                available[rid]=finishes[n]
            end=max(finishes.values())
            best=end if best is None else min(best,end)
    return best


@pytest.mark.parametrize('seed',range(32))
def test_exact_optimum_matches_independent_exhaustive_oracle(seed):
    plan=basic_problem(seed)
    result=solve(plan)
    assert result['audit']['makespan_s']==float(oracle(plan))
    assert result['optimality']=='OPTIMAL_FOR_DECLARED_MODEL'
    assert result['absolute_gap_s']==0


def trap():
    plan=basic_problem(1,2)
    for step in plan['operations']:
        step.update(depends_on=[],setup_s=0,per_batch_s=10)
    plan['operations'][0]['allowed_resources']=['r1','r2']
    plan['operations'][1]['allowed_resources']=['r1']
    return plan


def test_corrects_greedy_flexible_resource_trap():
    result=solve(trap())
    assert result['initial_makespan_s']==20
    assert result['audit']['makespan_s']==10 and result['improvement_s']==10
    assert result['audit']['status']=='PASS'


def test_budget_does_not_fabricate_optimality_and_keeps_valid_incumbent():
    result=solve(trap(),1)
    assert result['optimality']=='BEST_FOUND_NOT_PROVEN_OPTIMAL'
    assert result['audit']['makespan_s']==20 and result['lower_bound_s']==10
    assert result['absolute_gap_s']==10 and result['search_nodes']==1


@pytest.mark.parametrize('budget',[True,0,-1,20001,1.5,'100'])
def test_invalid_search_budgets(budget):
    with pytest.raises(ValueError):solve(trap(),budget)


def test_bound_can_close_at_budget_without_exhaustive_enumeration():
    plan=trap();plan['operations'][0]['allowed_resources']=['r2']
    result=solve(plan,1)
    assert result['optimality']=='OPTIMAL_FOR_DECLARED_MODEL' and result['termination']=='BOUND_CLOSED'


def test_reordered_input_is_deterministic():
    plan=trap();one=solve(plan)
    plan['operations'].reverse();plan['resources'].reverse()
    for step in plan['operations']:step['allowed_resources'].reverse()
    assert solve(plan)==one


def test_exact_decimal_durations_not_binary_accumulation():
    plan=trap();plan['operations'][0].update(per_batch_s=.1,setup_s=.2)
    plan['operations'][1].update(depends_on=['s0'],per_batch_s=.3)
    result=solve(plan)
    assert result['audit']['makespan_q']==['3','5']


def test_zero_duration_dependency_and_resource_case():
    plan=trap();plan['operations'][0]['per_batch_s']=0
    plan['operations'][1]['depends_on']=['s0']
    assert solve(plan)['audit']['makespan_s']==10


@pytest.mark.parametrize('mutation',[
    lambda r:r.pop(),lambda r:r.append(deepcopy(r[0])),
    lambda r:r[0].update(resource_id='unknown'),lambda r:r[0].update(operation_id='unknown'),
    lambda r:r[0].update(start_q=['1','0']),lambda r:r[0].update(start_q=['00','1']),
    lambda r:r[0].update(start_q=['0','2']),lambda r:r[0].update(start_s=True),
    lambda r:r[0].update(finish_q=['999','1']),lambda r:r[0].update(finish_s=999),
    lambda r:r[0].update(start_q=['-1','1']),lambda r:r[0].update(execute=True),
])
def test_separate_checker_rejects_tampered_schedule(mutation):
    plan=trap();rows=solve(plan)['schedule'];mutation(rows)
    with pytest.raises(ValueError):a.audit_schedule(plan,rows)


def test_checker_rejects_overlap_even_with_valid_declared_durations():
    plan=trap();rows=solve(plan)['schedule']
    rows[0]['resource_id']='r1'
    with pytest.raises(ValueError,match='occupancy'):a.audit_schedule(plan,rows)


def test_checker_rechecks_capabilities_independently():
    plan=trap();rows=solve(plan)['schedule'];plan['resources'][0]['capabilities']=[]
    with pytest.raises(ValueError,match='capability'):a.audit_schedule(plan,rows)


def test_checker_rechecks_unit_and_envelope_independently():
    plan=trap();rows=solve(plan)['schedule']
    plan['operations'][0]['demands']=[{'parameter':'size','unit':'mm','value':2,'source':None}]
    with pytest.raises(ValueError,match='envelope'):a.audit_schedule(plan,rows)


def test_infeasibility_is_not_a_successful_empty_schedule():
    plan=trap();plan['operations'][1]['requires']=['unavailable']
    result=solve(plan)
    assert result['status']=='INFEASIBLE' and result['schedule'] is None and result['audit'] is None


def test_original_v1_data_is_preserved_in_v2(tmp_path):
    root,spec,plan=fixture(tmp_path);src=p.source(root,spec)
    old=m.compile_definition(src,m.PARAMETERS);new=a.analyze(src,m.PARAMETERS)
    assert {k:v for k,v in new.items() if k not in ('analysis','schema')}=={k:v for k,v in old.items() if k!='schema'}
    assert new['analysis']['options']['duration_bounds'] is None
    assert new['analysis']['diagnostics']['timing_envelope'] is None
    assert new['analysis']['selection']['audit']['makespan_s']==89


def test_bottleneck_and_noncritical_work_distinguished(tmp_path):
    root,spec,plan=fixture(tmp_path);result=a.analyze(p.source(root,spec),m.PARAMETERS)
    d=result['analysis']['diagnostics'];rows={r['operation_id']:r for r in d['duration_sensitivity']}
    assert d['critical_operations']==['assemble','fabricate','inspect']
    assert rows['inspect']['makespan_reduction_s']==4.2
    assert rows['procure']['makespan_reduction_s']==0 and rows['procure']['total_slack_s']==15


def test_critical_parallel_paths_do_not_imply_one_at_a_time_gain():
    plan=trap();plan['operations'][0]['allowed_resources']=['r2']
    rows=solve(plan)['schedule'];d=a.diagnostics(plan,rows,None)
    assert len(d['critical_operations'])==2
    assert all(r['makespan_reduction_s']==0 for r in d['duration_sensitivity'])


def test_explicit_time_box_is_not_a_probability(tmp_path):
    root,spec,plan=fixture(tmp_path);attach(root,spec,plan)
    result=a.analyze(p.source(root,spec),m.PARAMETERS)
    env=result['analysis']['diagnostics']['timing_envelope']
    assert env['lower_s']==pytest.approx(89*.8) and env['upper_s']==pytest.approx(89*1.2)
    assert env['probability_or_coverage'] is None


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(definition_ref='sha256:'+'0'*64),lambda d:d.update(node_budget=True),
    lambda d:d.update(schema='wrong'),lambda d:d.update(basis='95% confidence'),
    lambda d:d['duration_bounds'].pop('inspect'),lambda d:d['duration_bounds']['inspect'].update(setup_s=[0,1]),
    lambda d:d['duration_bounds']['inspect'].update(per_batch_s=[True,100]),
    lambda d:d['duration_bounds']['inspect'].update(per_batch_s=[100,1]),
    lambda d:d.update(execute=True),
])
def test_invalid_options_rejected(mutation,tmp_path):
    root,spec,plan=fixture(tmp_path);data=attach(root,spec,plan);mutation(data)
    (root/a.OPTIONS_PATH).write_bytes(raw(data))
    with pytest.raises(ValueError):a.analyze(p.source(root,spec),m.PARAMETERS)


def two_runs(tmp_path,factor=.2,change_scope=False):
    one=tmp_path/'one';one.mkdir();root,spec,plan=fixture(one)
    attach(root,spec,plan,factor)
    src=p.source(root,spec);m.execute(src,tmp_path/'base',analyze=True)
    # Same original product and inspection bytes; only process definition changes.
    plan['operations'][-1]['batch_size']=4;plan['resources'][-1]['batch_capacity']=4
    if change_scope:plan['order']['work_order_id']='different-order'
    (root/m.PLAN_PATH).write_bytes(raw(plan));spec['artifacts']=[r for r in spec['artifacts'] if r['path']!=a.OPTIONS_PATH]
    attach(root,spec,plan,factor)
    m.execute(p.source(root,spec),tmp_path/'variant',analyze=True)
    return [tmp_path/'base',tmp_path/'variant']


def test_variant_comparison_covers_exact_native_occurrences(tmp_path):
    paths=two_runs(tmp_path,.01);before={str(q):tree(q) for q in paths}
    view=a.compare(paths);base,variant=view['rows']
    assert variant['nominal_seconds_saved_against_first']==30
    assert variant['robustness']=='GUARANTEED_FASTER_WITHIN_DECLARED_BOXES'
    assert base['snapshot_ref']!=variant['snapshot_ref'] and base['execution_id']!=variant['execution_id']
    assert view['new_execution'] is False and view['physical_savings_established'] is False
    assert {str(q):tree(q) for q in paths}==before


def test_overlapping_bounds_do_not_claim_robust_gain(tmp_path):
    view=a.compare(two_runs(tmp_path,.3));row=view['rows'][1]
    assert row['nominal_seconds_saved_against_first']==30
    assert row['robustness']=='INCONCLUSIVE_OVERLAPPING_BOUNDS'
    assert row['savings_interval_s'][0]<0<row['savings_interval_s'][1]


def test_changed_order_prevents_apples_to_oranges_speed_claim(tmp_path):
    row=a.compare(two_runs(tmp_path,change_scope=True))['rows'][1]
    assert row['scope']=='CHANGED_SCOPE_NO_SPEEDUP_CLAIM'
    assert row['nominal_seconds_saved_against_first'] is None


def test_duplicated_snapshot_not_independent_variant(tmp_path):
    paths=two_runs(tmp_path)
    with pytest.raises(ValueError,match='distinct'):a.compare([paths[0],paths[0]])


def test_same_byte_replay_retains_analysis_data_new_occurrences(tmp_path):
    root,spec,plan=fixture(tmp_path);attach(root,spec,plan)
    src=p.source(root,spec);one=m.execute(src,tmp_path/'one',analyze=True)
    original=tree(tmp_path/'one');two=m.replay(tmp_path/'one',tmp_path/'two',one['snapshot_ref'])
    assert one['data']==two['data'] and one['evidence_id']==two['evidence_id']
    assert one['execution_id']!=two['execution_id'] and one['result_id']!=two['result_id']
    assert tree(tmp_path/'one')==original
    m.export_inputs(tmp_path/'one',tmp_path/'restored')
    assert tree(tmp_path/'restored')==tree(root)


def test_native_refusal_retains_invalid_option_evidence(tmp_path):
    root,spec,plan=fixture(tmp_path);data=attach(root,spec,plan);data['node_budget']=0
    (root/a.OPTIONS_PATH).write_bytes(raw(data))
    result=m.execute(p.source(root,spec),tmp_path/'refused',analyze=True)
    assert result['status']=='REFUSED' and result['result_id'] is None
    assert m.inspect(tmp_path/'refused')['status']=='REFUSED'


def test_no_network_or_hardware_for_analysis(tmp_path,monkeypatch):
    import socket
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected network')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    root,spec,plan=fixture(tmp_path)
    assert m.execute(p.source(root,spec),tmp_path/'run',analyze=True)['status']=='RETAINED'


def test_unseen_requirement_selection_change_invalidates_comparison_scope(tmp_path):
    one=tmp_path/'case';one.mkdir();root,spec,plan=fixture(one)
    spec['artifacts'].append({'path':'alternate-requirements.json','kind':'model.input','parents':[],
        'tool':{'name':'test','version':'1'},'origin':'synthetic'})
    requirement=json.loads((root/'requirements.json').read_bytes())
    requirement['requirements'][0].update(lower=0,upper=100)
    (root/'alternate-requirements.json').write_bytes(raw(requirement))
    m.execute(p.source(root,spec),tmp_path/'base',analyze=True)
    plan['requirements_path']='alternate-requirements.json';(root/m.PLAN_PATH).write_bytes(raw(plan))
    m.execute(p.source(root,spec),tmp_path/'other',analyze=True)
    row=a.compare([tmp_path/'base',tmp_path/'other'])['rows'][1]
    assert row['scope']=='CHANGED_SCOPE_NO_SPEEDUP_CLAIM'
    assert row['nominal_seconds_saved_against_first'] is None


def test_selected_resource_order_and_gaps_not_hidden_by_cost():
    plan=trap();plan['resources'][0].update(rate_per_hour=36,currency='CAD')
    plan['resources'][1].update(rate_per_hour=360,currency='CAD')
    result=solve(plan);diag=a.diagnostics(plan,result['schedule'],None)
    assert result['audit']['makespan_s']==10
    assert diag['modeled_processing_cost']==pytest.approx(1.1)
    # Faster can cost more. The optimization objective is time, not a cost claim.
    plan['resources'][1]['currency']='USD'
    assert a.diagnostics(plan,result['schedule'],None)['modeled_processing_cost'] is None


def test_v1_session_is_not_relabelled_v2_by_comparison(tmp_path):
    root,spec,plan=fixture(tmp_path);src=p.source(root,spec)
    m.execute(src,tmp_path/'old');m.execute(src,tmp_path/'new',analyze=True)
    with pytest.raises(ValueError,match='v2'):a.compare([tmp_path/'old',tmp_path/'new'])


def test_analysis_selector_rejects_nonboolean_before_output(tmp_path):
    root,spec,plan=fixture(tmp_path)
    with pytest.raises(ValueError,match='boolean'):m.execute(p.source(root,spec),tmp_path/'run',analyze='yes')
    assert not (tmp_path/'run').exists()


def test_native_validator_refuses_recomputed_fake_analysis():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root,spec,plan=fixture(Path(tmp));src=p.source(root,spec)
        data=a.analyze(src,m.PARAMETERS);data['analysis']['selection']['audit']['makespan_s']=0
        with pytest.raises(ValueError,match='differs'):
            m._validate_analysis(m.ANALYSIS_OPERATION,data,src,m.PARAMETERS,m.PARAMETERS)


def test_v2_cli_report_comparison_and_no_clobber(tmp_path):
    from ciw import sensor_manufacturing_cli as cli
    paths=two_runs(tmp_path)
    view=a.compare(paths);html=tmp_path/'compare.html';cli.comparison_report(view,html,paths)
    assert 'INCONCLUSIVE' not in html.read_text() or 'robustness' in html.read_text()
    with pytest.raises(FileExistsError):cli.comparison_report(view,html,paths)
    with pytest.raises(ValueError):cli.comparison_report(view,paths[0]/'compare.html',paths)
    report=tmp_path/'analysis.html';cli.report(paths[0],report)
    assert 'Remaining gap:' in report.read_text() and 'Total slack' in report.read_text()
    result=subprocess.run([sys.executable,'-m','ciw.sensor_manufacturing_cli','compare',
        *map(str,paths),'--output',str(tmp_path/'cli-compare.html')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==view


def test_v2_run_cli_uses_actual_analysis_operation(tmp_path):
    root,spec,plan=fixture(tmp_path);request=tmp_path/'request.json';request.write_bytes(raw(spec))
    result=subprocess.run([sys.executable,'-m','ciw.sensor_manufacturing_cli','run',str(root),str(request),
        '--analyze','--output-dir',str(tmp_path/'run')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    view=json.loads(result.stdout);assert view['data']['schema']=='ciw.sensor-manufacturing-plan.v2'
    session,_=m.load(tmp_path/'run')
    assert next(iter(session.executions.values()))['operation_id']==m.ANALYSIS_OPERATION
