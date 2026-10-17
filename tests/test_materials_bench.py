from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
from ciw import materials_bench as mb, thermochemical_benchmark as tc, polymer_viscoelastic as pv, polymer_population as pp

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(params=['tc','pv','pp'])
def request_data(request):
    if request.param=='tc':return json.loads((ROOT/'examples/thermochemical/request.json').read_text())
    if request.param=='pv':return {'schema':pv.SCHEMA,'model':pv.MODEL,'equilibrium_modulus_pa':1e6,
        'modes':[{'id':'mode','modulus_pa':2e6,'relaxation_time_s':.2}],
        'temperature_k':300,'initial_condition':'instantaneous-elastic-at-t0',
        'time_s':[0,.1,1,10],'strain':[0,.01,0,-.01],'frequency_hz':[0,1,10]}
    return {'schema':pp.SCHEMA,'model':pp.MODEL,'weight_basis':'number',
            'bins':[{'id':'a','molar_mass_kg_mol':10,'weight':1},{'id':'b','molar_mass_kg_mol':100,'weight':2}]}


def reseal(r):
    r['content_sha256']=mb.digest({k:v for k,v in r.items() if k not in ['content_sha256','execution_id','request_bytes_sha256']})


def test_run_inspect_reproduce_separate_identities(request_data):
    a=mb.run(request_data);b=mb.run(request_data)
    assert a['execution_id']!=b['execution_id'] and a['content_sha256']==b['content_sha256']
    before=deepcopy(a);i=mb.inspect(a);j=mb.inspect(a)
    assert a==before
    assert i['verification_id']!=j['verification_id'] and i['verification_id']!=a['execution_id']
    assert i['solver_rerun'] is False and i['physical_validation'] is False
    assert i['complete_report_sha256']==mb.digest(a)
    repro=mb.reproduce(a)
    assert repro['original_execution_id']==a['execution_id']
    assert repro['fresh_report']['execution_id']!=a['execution_id']
    assert repro['fresh_report']['content_sha256']==a['content_sha256']


def test_offline_inspection_never_integrates(request_data,monkeypatch):
    report=mb.run(request_data)
    def fail(*a,**k):raise AssertionError('inspection attempted integration')
    monkeypatch.setattr(tc,'simulate',fail);monkeypatch.setattr(tc,'constant_rate_reference',fail)
    monkeypatch.setattr(pv,'simulate',fail)
    assert mb.inspect(report)['solver_rerun'] is False


@pytest.mark.parametrize('key',['content_sha256','request_sha256','kernel_source_sha256'])
def test_hash_forgery(request_data,key):
    r=mb.run(request_data);r[key]='0'*64
    with pytest.raises(ValueError):mb.inspect(r)


@pytest.mark.parametrize('value',[False,None,'bogus',str(uuid.uuid1())])
def test_invalid_execution_id(request_data,value):
    r=mb.run(request_data);r['execution_id']=value
    with pytest.raises(ValueError):mb.inspect(r)


def test_authority_promotion_and_boolean_alias(request_data):
    for value in [True,0,'false']:
        r=mb.run(request_data)
        authority=r['authority'] if r['schema']==tc.RESULT_SCHEMA else r['result']['authority']
        authority['physical_validation']=value;reseal(r)
        with pytest.raises(ValueError):mb.inspect(r)


def test_request_binding_and_extra_fields(request_data):
    r=mb.run(request_data);r['request_sha256']='0'*64;reseal(r)
    with pytest.raises(ValueError):mb.inspect(r)
    r=mb.run(request_data);r['made_up']='anything';reseal(r)
    with pytest.raises(ValueError):mb.inspect(r)


def test_source_metadata_is_not_attestation(request_data):
    r=mb.run(request_data);r['kernel_source_sha256']='0'*64
    if r['schema']==mb.RESULT_SCHEMA:
        module=mb._module(r['request']);r['source_files_sha256'][Path(module.__file__).name]='0'*64
    reseal(r);i=mb.inspect(r)
    assert i['source_matches_current_kernel'] is False and i['runtime_authenticated'] is False


def test_complete_report_digest_binds_transport_identity(request_data):
    r=mb.run(request_data);original=mb.inspect(r)
    r['request_bytes_sha256']='0'*64
    changed=mb.inspect(r)
    assert original['content_sha256']==changed['content_sha256']
    assert original['complete_report_sha256']!=changed['complete_report_sha256']
    assert changed['raw_request_bytes_verified'] is False


def test_cli_three_actions_and_no_overwrite(request_data,tmp_path):
    p=tmp_path/'request.json';p.write_text(json.dumps(request_data))
    paths=[]
    for action in ['run','inspect','reproduce']:
        target=tmp_path/f'{action}.json'
        src=p if action=='run' else paths[0]
        command=[sys.executable,'-m','ciw.materials_bench',action,str(src),'--output',str(target)]
        run=subprocess.run(command,capture_output=True,text=True,timeout=30)
        assert run.returncode==0,run.stderr
        raw=target.read_bytes();paths.append(target)
        again=subprocess.run(command,capture_output=True,text=True,timeout=30)
        assert again.returncode==2 and target.read_bytes()==raw


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'\xff',b'{',b'['*2000+b']'*2000,b'x'*(mb.MAX_BYTES+1)],ids=['duplicate','nonfinite','encoding','syntax','depth','byte-budget'])
def test_parser_refuses(raw):
    with pytest.raises(ValueError):mb._load(raw)


@pytest.mark.parametrize('value',[None,[],True,{}, {'schema':['bad']},{'schema':'unregistered'}])
def test_dispatch_refuses(value):
    with pytest.raises(ValueError):mb.run(value)


def test_legacy_constant_rate_inspection_no_analytic_execution(monkeypatch):
    p=json.loads((ROOT/'examples/thermochemical/request.json').read_text());p['activation_energy_j_mol']=0
    r=tc.make_report(p)
    def fail(*a):raise AssertionError('oracle was rerun')
    monkeypatch.setattr(tc,'constant_rate_reference',fail)
    mb.inspect(r)
    for path,value in [('within_policy',1),('status','not_applicable'),('heat_max_abs_j',-1)]:
        bad=deepcopy(r);bad['analytic_reference'][path]=value;reseal(bad)
        with pytest.raises(ValueError):mb.inspect(bad)


@pytest.mark.parametrize('mutation',['balanced_count','fractional_count','bool_count','metric','unsupported_analytic'])
def test_thermochemical_retention_forgery(mutation):
    p=json.loads((ROOT/'examples/thermochemical/request.json').read_text());r=tc.make_report(p)
    if mutation=='balanced_count':r['solver']['attempted_steps']+=1
    elif mutation=='fractional_count':r['solver']['accepted_steps']=float(r['solver']['accepted_steps'])
    elif mutation=='bool_count':r['solver']['rejected_steps']=False
    elif mutation=='metric':r['balance_metrics']['energy_balance_max_abs_j']+=1
    else:r['analytic_reference']={'status':'passed'}
    reseal(r)
    with pytest.raises(ValueError):mb.inspect(r)


def test_conservation_is_not_trajectory_verification():
    p=json.loads((ROOT/'examples/thermochemical/request.json').read_text());p['activation_energy_j_mol']=0
    wrong=deepcopy(p);wrong['reference_rate_s_inv']*=2
    r=tc.make_report(wrong)
    r['request']=tc.validate_request(p);r['request_sha256']=mb.digest(r['request']);reseal(r)
    assert mb.inspect(r)['solver_rerun'] is False
    with pytest.raises(ValueError,match='reproduction'):mb.reproduce(r)


@pytest.mark.parametrize('which',['depth','nodes','string','cycles'])
def test_explicit_programmatic_tree_budgets(which):
    if which=='nodes':value={'x':[0]*250001}
    elif which=='string':value={'x':'a'*1025}
    elif which=='cycles':value={};value['x']=value
    else:
        value={};node=value
        for _ in range(17):node['x']={};node=node['x']
    with pytest.raises(ValueError):mb.inspect(value)


def test_reproduction_retains_quantity_policies(request_data):
    result = mb.reproduce(mb.run(request_data))
    assert result['comparison']['within_policy'] is True
    if request_data['schema'] == pv.SCHEMA:
        policy = result['comparison']['quantity_policies']
        assert policy['stress_pa']['unit'] == 'Pa'
        assert policy['work_j_m3']['unit'] == 'J/m^3'
        assert policy['stress_pa']['atol'] != policy['work_j_m3']['atol']
    elif request_data['schema'] == pp.SCHEMA:
        policy = result['comparison']['quantity_policies']
        assert policy['number_average_kg_mol']['unit'] == 'kg/mol'
        assert policy['dispersity']['unit'] == '1'
    else:
        assert result['comparison']['policy']['atol_heat_j'] == 2e-5
