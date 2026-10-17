from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import pytest
from ciw import materials_workflow as mw, materials_cli as cli, materials_bench as mb
from ciw import materials_uncertainty as mu, rheology_empirical as er
from ciw.operations.runner import seal
from ciw.operations import schemas


@pytest.fixture(params=cli.PROFILES)
def request_data(request):
    return cli.example(request.param)


def evaluate(p, tmp_path):
    s = mw.create_session(p, tmp_path/'records')
    output = mw.execute(s, mw.EVALUATE, {})
    assert output['status'] == 'completed', output
    return s, output['result']


def test_all_profiles_use_real_session_retention(request_data,tmp_path):
    before = deepcopy(request_data)
    s,c = evaluate(request_data,tmp_path)
    for op in (mw.INSPECT,mw.VERIFY,mw.REPLAY):
        result = mw.execute(s,op,{'candidate':c})
        assert result['status']=='completed', result
        assert result['result']['verification_status']=='not_verified'
        assert result['result']['evidence_id']==s.run['evidence_id']
    assert request_data==before
    assert len(s.results)==len(s.executions)==4
    ids={i for r in s.results.values() for i in (r['result_id'],r['execution_id'])}
    assert len(ids)==8
    s.save_workspace(tmp_path/'workspace.json')
    restored=mw.reopen(tmp_path/'workspace.json',tmp_path/'inspection')
    assert restored.results==s.results and restored.executions==s.executions
    assert restored.operations.describe()==[]
    assert s.run['metadata']['provenance']['measurement_by_NET'] is False
    assert s.run['metadata']['manifest']['sampling']['time_semantics']=='synthetic-selection-not-acquisition-time'


def test_reopen_never_launches_solver(request_data,tmp_path,monkeypatch):
    s,c=evaluate(request_data,tmp_path)
    assert mw.execute(s,mw.VERIFY,{'candidate':c})['status']=='completed'
    s.save_workspace(tmp_path/'w.json')
    def forbidden(*a,**kw): raise AssertionError('Reopen attempted a solver or fresh empirical audit')
    monkeypatch.setattr(mw,'_compute',forbidden)
    monkeypatch.setattr(mw.tc,'simulate',forbidden)
    monkeypatch.setattr(mw.pv,'simulate',forbidden)
    monkeypatch.setattr(mu,'analyze',forbidden)
    monkeypatch.setattr(er,'verify_result',forbidden)
    assert mw.reopen(tmp_path/'w.json',tmp_path/'read').results==s.results


@pytest.mark.parametrize('fault',['missing','changed-result-id','changed-data','changed-execution-id','foreign-source','wrong-operation'])
def test_dependency_refused_before_compute(tmp_path,fault,monkeypatch):
    s,c=evaluate(cli.example('population'),tmp_path)
    bad=deepcopy(c)
    if fault=='missing':params={}
    else:
        if fault=='changed-result-id':bad['result_id']='result-'+'0'*32
        elif fault=='changed-data':bad['data']['source_ref']='sha256:'+'0'*64
        elif fault=='changed-execution-id':bad['execution_id']='execution-'+'0'*32
        elif fault=='foreign-source':bad['evidence_id']='sha256:'+'0'*64
        else:bad['operation_id']='statistics.v1'
        seal(bad);params={'candidate':bad}
    def forbidden(*a,**kw):raise AssertionError('Bad dependency reached provider')
    monkeypatch.setattr(mw,'_compute',forbidden)
    out=mw.execute(s,mw.REPLAY,params)
    assert out['status']=='refused' and out['result'] is None
    assert len(s.results)==1 and len(s.executions)==2


def test_cross_source_exact_retained_dependency_still_refused(tmp_path):
    s,c=evaluate(cli.example('population'),tmp_path)
    foreign=deepcopy(c);foreign['evidence_id']='sha256:'+'1'*64;seal(foreign)
    s.results[foreign['result_id']]=foreign
    out=mw.execute(s,mw.VERIFY,{'candidate':foreign})
    assert out['status']=='refused'


@pytest.mark.parametrize('fault',['missing-parent','altered-parent','source-change','authority','verification-scope','verification-method'])
def test_forged_workspace_refuses(tmp_path,fault):
    s,c=evaluate(cli.example('empirical-rheology'),tmp_path)
    out=mw.execute(s,mw.VERIFY,{'candidate':c});assert out['status']=='completed'
    p=tmp_path/'w.json';s.save_workspace(p);d=json.loads(p.read_text())
    if fault=='missing-parent':d['results']=d['results'][1:]
    elif fault=='altered-parent':d['results'][0]['created_at']='2001-01-01T00:00:00+00:00';seal(d['results'][0])
    elif fault=='source-change':d['run']['metadata']['materials_request']['report_unit']='kPa'
    elif fault=='authority':d['results'][1]['data']['authority']['part_release']=True;seal(d['results'][1])
    else:
        report=d['results'][1]['data']['report']
        if fault=='verification-scope':report['scope']='physical-material-qualified'
        else:
            proof=report['proof'];proof['method']='SP1-verified';er._seal_record(proof)
        seal(report);seal(d['results'][1])
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):mw.reopen(p,tmp_path/'rejected')


def test_failed_solver_retained_and_reopenable(tmp_path):
    p=cli.example('thermochemistry');p['solver']['max_steps']=1
    result=cli.run(p,tmp_path/'failure')
    assert result['executions'][0]['status']=='refused'
    assert result['results']==[]
    r=cli.inspect(tmp_path/'failure/workspace.json')
    assert r['executions']==result['executions']


def test_source_does_not_accept_other_instruments_or_code(tmp_path):
    p=cli.example('population');p['provider']='arbitrary'
    with pytest.raises(ValueError):mw.create_session(p,tmp_path)
    run=mw.make_source(cli.example('population'));run['metadata']['provenance']['measurement_by_NET']=True
    from ciw.core.identities import evidence_id
    run['evidence_id']=evidence_id(run)
    with pytest.raises(ValueError):mw.source_request(run)


def test_default_registry_not_mutated_and_repeated_binding():
    from ciw.operations.registry import default_registry
    before=default_registry().describe()
    a=mw.registry();b=mw.registry()
    assert a.describe()==b.describe()
    assert default_registry().describe()==before


def test_missing_dependency_extension_refuses(monkeypatch):
    monkeypatch.delattr(schemas,'register_dependency_fields')
    with pytest.raises(ValueError,match='PR184'):mw.registry()


def test_existing_unsafe_binding_not_replaced(monkeypatch):
    mw.register_schemas()
    monkeypatch.setitem(schemas._VALIDATORS,mw.EVALUATE,lambda *a:None)
    with pytest.raises(ValueError):mw.registry()


@pytest.mark.parametrize('profile',cli.PROFILES)
def test_installed_command_journey(tmp_path,profile):
    # This also runs under a noneditable wheel with -o pythonpath= and outside cwd.
    src=tmp_path/'request.json';src.write_text(json.dumps(cli.example(profile)))
    dest=tmp_path/'case'
    base=[sys.executable,'-m','ciw.net','materials']
    def call(args):
        run=subprocess.run(base+args,capture_output=True,text=True,timeout=30)
        assert run.returncode==0,run.stderr+'\n'+run.stdout
        return json.loads(run.stdout)
    call(['run',str(src),'--output-dir',str(dest)])
    before=(dest/'workspace.json').read_bytes()
    call(['inspect',str(dest/'workspace.json')])
    v=call(['verify',str(dest/'workspace.json'),'--output-dir',str(tmp_path/'verification')])
    r=call(['replay',str(dest/'workspace.json'),'--output-dir',str(tmp_path/'replay')])
    assert len(v['new_execution_ids'])==len(r['new_execution_ids'])==1
    assert v['new_execution_ids']!=r['new_execution_ids']
    assert (dest/'workspace.json').read_bytes()==before
    again=subprocess.run(base+['run',str(src),'--output-dir',str(dest)],capture_output=True,text=True,timeout=30)
    assert again.returncode==2 and (dest/'workspace.json').read_bytes()==before


def test_rehashed_comparison_policy_forgery_refused(tmp_path):
    s,c=evaluate(cli.example('viscoelasticity'),tmp_path)
    out=mw.execute(s,mw.VERIFY,{'candidate':c});r=deepcopy(out['result'])
    report=r['data']['report'];report['proof']['comparison']['quantity_policies']['stress_pa']['atol']=100
    seal(report);seal(r)
    with pytest.raises(ValueError): mw.validate_payload(mw.VERIFY,r['data'],s.run,{'candidate':c},{})

@pytest.mark.parametrize('profile,fault', [('joint-residual','invented-pvalue'),
    ('joint-residual','changed-criterion'),('complex-viscosity','invented-coverage'),
    ('complex-viscosity','changed-unit'),('complex-viscosity','wrong-covariance-ancestry')])
def test_resealed_uncertainty_metadata_cannot_promote_claim(tmp_path,profile,fault):
    s,c=evaluate(cli.example(profile),tmp_path)
    r=deepcopy(c);report=r['data']['report'];calc=report['result']['calculation']
    if fault=='invented-pvalue':calc['p_value']=.001
    elif fault=='changed-criterion':calc['criterion']=1e12
    elif fault=='invented-coverage':calc['nonlinear_coverage']='95%-validated'
    elif fault=='changed-unit':calc['unit']='kPa*s'
    else:
        from ciw.core.covariance import covariance_identity
        calc['output_covariance']['provenance']['source_covariance_ids']=['sha256:'+'0'*64]
        calc['output_covariance']['covariance_id']=covariance_identity(calc['output_covariance'])
    seal(report);seal(r)
    with pytest.raises(ValueError):mw.validate_payload(mw.EVALUATE,r['data'],s.run,{}, {})


def test_resealed_uncertainty_reexecution_must_match_retained_candidate(tmp_path):
    s,c=evaluate(cli.example('joint-residual'),tmp_path)
    out=mw.execute(s,mw.VERIFY,{'candidate':c});r=deepcopy(out['result'])
    report=r['data']['report'];fresh=report['proof']['fresh_report']
    calc=fresh['result']['calculation']
    calc['squared_mahalanobis']=10.0;calc['outcome']='OUTSIDE_DECLARED_THRESHOLD'
    seal(fresh);seal(report);seal(r)
    with pytest.raises(ValueError):mw.validate_payload(mw.VERIFY,r['data'],s.run,{'candidate':c},{})
