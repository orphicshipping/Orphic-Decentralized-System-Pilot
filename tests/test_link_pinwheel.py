"""Local feedback mathematics and retained lifecycle, with synthetic inputs only."""
from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest
from ciw import link_contract as c, link_study as study, link_study_io as prior
from ciw import link_improvement as imp, link_improvement_audit as check
from ciw import link_pinwheel as p, link_measurements as m, link_measurement_io as exchange
from ciw.link_study_cli import example
from ciw.core.covariance import covariance_identity


def bundle_for(tmp_path, request=None):
    r = example() if request is None else request
    raw = json.dumps(r, indent=3).encode() + b'\n'
    assert prior.run(r, tmp_path/'study', raw=raw)['status'] == 'completed'
    return m.build(tmp_path/'study')


@pytest.fixture
def bundle(tmp_path):
    return bundle_for(tmp_path)


def plan(bundle):
    return p.plan_for(bundle, instance_id='synthetic.local.lab', max_standard_uncertainty=.03)


def run(bundle, tmp_path, declaration=None):
    return p.run(bundle, plan(bundle) if declaration is None else declaration, tmp_path/'review')


def test_five_blades_share_existing_source_with_no_physical_authority(bundle,tmp_path):
    result=run(bundle,tmp_path)
    review=result['results'][imp.OPS[0]]['data']; audit=result['results'][imp.OPS[1]]['data']
    assert result['status']=='completed' and audit['status']=='PASS'
    assert [b['name'] for b in review['blades']]==list(imp.BLADES)
    assert review['hub']['status']=='NOT_ESTABLISHED' and review['hub']['origin']=='synthetic'
    assert review['status']=='PROPOSAL_ONLY'
    assert review['limits']['deployment']=='not_performed'
    assert review['limits']['network_sharing']=='not_performed'
    assert len(result['original_execution_ids'])==4 and len(result['review_execution_ids'])==2
    assert set(result['original_execution_ids']).isdisjoint(result['review_execution_ids'])
    assert review['blades'][1]['applicability']==['ACQUISITION_TIME_UNKNOWN']*3


def test_covariance_cross_terms_and_best_hypothesis(bundle,tmp_path):
    result=run(bundle,tmp_path); d=result['results'][imp.OPS[0]]['data']['feedback']
    assert d['linearized_variance']==pytest.approx(.00190808)
    assert d['proposed_candidate_id']=='offset-half'
    assert len(d['cross_terms'])==10 and len(d['sensitivities'])==5
    total=sum(t['diagonal_variance_term'] for t in d['sensitivities'])+sum(t['signed_variance_term'] for t in d['cross_terms'])
    assert total==pytest.approx(d['linearized_variance'])
    assert d['retained_model_variance']!=d['linearized_variance']  # retain both rounding paths
    assert any(t['signed_variance_term']!=0 for t in d['cross_terms'])
    for row in d['candidates']:
        assert len(row['counterfactual_matrix'])==5
        assert row['scope']=='hypothetical_D_Sigma_D_at_fixed_means_not_new_observations'
    assert d['candidates'][2]['variance']==pytest.approx(.00190808-.000875)


def test_correlation_cancellation_can_make_better_sensor_worse(tmp_path):
    r=example(); r['calibration']['gain']=1.; r['calibration']['offset']=0.
    cov=r['calibration']['joint_covariance']; vector=[1,1,1,0,-1]
    cov['matrix']=[[a*b for b in vector] for a in vector]
    cov['reference_values'][-2:]=[1.,0.]
    cov['basis']['id']=study.calibration_basis(r);cov['covariance_id']=covariance_identity(cov)
    b=bundle_for(tmp_path,r);result=run(b,tmp_path)
    d=result['results'][imp.OPS[0]]['data']['feedback']
    assert d['linearized_variance']==0
    assert d['proposed_candidate_id'] is None
    assert [x['status'] for x in d['candidates']]==['INCREASES_DECLARED_VARIANCE','UNCHANGED','INCREASES_DECLARED_VARIANCE']
    assert d['candidates'][0]['variance']==.25
    assert any(t['signed_variance_term']<0 for t in d['cross_terms'])
    assert result['results'][imp.OPS[1]]['data']['status']=='PASS'


@pytest.mark.parametrize('factor',[0.,1e-100,1e-280])
def test_zero_and_tiny_covariance_not_discarded(tmp_path,factor):
    r=example();cov=r['calibration']['joint_covariance']
    cov['matrix']=[[factor*x for x in row] for row in cov['matrix']]
    cov['covariance_id']=covariance_identity(cov)
    result=run(bundle_for(tmp_path,r),tmp_path)
    d=result['results'][imp.OPS[0]]['data']['feedback']
    assert result['results'][imp.OPS[1]]['data']['status']=='PASS'
    assert (d['linearized_variance']==0) == (factor==0)
    if factor==0: assert d['proposed_candidate_id'] is None


def test_unknown_budget_is_not_zero_or_ranked(tmp_path):
    r=example();r['calibration']['joint_covariance']=None
    result=run(bundle_for(tmp_path,r),tmp_path)
    d=result['results'][imp.OPS[0]]['data']['feedback']
    assert d['status']=='UNCERTAINTY_UNKNOWN' and d['linearized_variance'] is None
    assert d['ranking']==[] and d['proposed_candidate_id'] is None
    assert d['candidates']==[] and d['sensitivities']==[]
    assert result['results'][imp.OPS[1]]['data']['status']=='PASS'


@pytest.mark.parametrize('value',[True,False,0,-1,float('nan'),float('inf'),'0.03',None])
def test_bad_objective_refuses_before_output(bundle,tmp_path,value):
    declaration=plan(bundle);declaration['objective']['max_standard_uncertainty']=value
    with pytest.raises((ValueError,TypeError)):run(bundle,tmp_path,declaration)
    assert not (tmp_path/'review').exists()


@pytest.mark.parametrize('mutation',['quantity','unit','bundle','source','model','audit','extra','empty','repeat','many','length','range','bool','noop','kind','field','instance'])
def test_strict_plan_and_exact_occurrences(bundle,tmp_path,mutation):
    d=plan(bundle)
    if mutation=='quantity':d['objective']['quantity']='peak'
    elif mutation=='unit':d['objective']['unit']='m'
    elif mutation in ('bundle','source','model','audit'):
        field={'bundle':'bundle_id','source':'source_evidence_id','model':'model_result_id','audit':'audit_result_id'}[mutation]
        d[field]=c.bytes_ref(b'other')
    elif mutation=='extra':d['control']=True
    elif mutation=='empty':d['candidates']=[]
    elif mutation=='repeat':d['candidates'].append(deepcopy(d['candidates'][0]))
    elif mutation=='many':d['candidates']*=3
    elif mutation=='length':d['candidates'][0]['scales'].pop()
    elif mutation=='range':d['candidates'][0]['scales'][0]=1.1
    elif mutation=='bool':d['candidates'][0]['scales'][0]=False
    elif mutation=='noop':d['candidates'][0]['scales']=[1]*5
    elif mutation=='kind':d['candidates'][0]['kind']='set_sample_rate'
    elif mutation=='field':d['candidates'][0]['assume_independence']=True
    elif mutation=='instance':d['instance_id']='\x00'
    with pytest.raises((ValueError,TypeError)):run(bundle,tmp_path,d)
    assert not (tmp_path/'review').exists()


def test_candidate_tie_has_deterministic_identity_order(bundle,tmp_path):
    d=plan(bundle);a=d['candidates'][2];b=deepcopy(a);a['id']='z';b['id']='a';d['candidates']=[a,b]
    out=run(bundle,tmp_path,d)['results'][imp.OPS[0]]['data']['feedback']
    assert out['ranking']==['a','z'] and out['proposed_candidate_id']=='a'


def test_other_original_replay_cannot_be_substituted(bundle,tmp_path):
    declaration=plan(bundle)
    prior.replay(tmp_path/'study',tmp_path/'repeated-study')
    other=m.build(tmp_path/'repeated-study')
    with pytest.raises(ValueError):run(other,tmp_path,declaration)
    assert not (tmp_path/'review').exists()


def test_raw_plan_whitespace_and_original_bytes_survive_restore(bundle,tmp_path,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')))
    declaration=plan(bundle);raw=json.dumps(declaration,indent=4).encode()+b'\n'
    first=p.run(bundle,declaration,tmp_path/'review',raw_plan=raw)
    assert (tmp_path/'review/plan.json').read_bytes()==raw
    before=(tmp_path/'review/workspace.json').read_bytes()
    p.restore_source(tmp_path/'review',tmp_path/'restored')
    for name in m.FILES: assert (tmp_path/'restored'/name).read_bytes()==m._unpack(bundle['source_files'][name])
    assert m.build(tmp_path/'restored')==bundle
    second=p.replay(tmp_path/'review',tmp_path/'second')
    assert first['original_execution_ids']==second['original_execution_ids']
    assert set(first['review_execution_ids']).isdisjoint(second['review_execution_ids'])
    assert (tmp_path/'review/workspace.json').read_bytes()==before
    assert first['results'][imp.OPS[0]]['data']==second['results'][imp.OPS[0]]['data']


def test_plan_byte_mismatch_refuses(bundle,tmp_path):
    d=plan(bundle)
    with pytest.raises(ValueError):p.run(bundle,d,tmp_path/'review',raw_plan=b'{}')
    assert not (tmp_path/'review').exists()


@pytest.mark.parametrize('field',['variance','cross','matrix','ranking','status','hub','source'])
def test_independent_arithmetic_audit_detects_forgery(bundle,tmp_path,field):
    out=run(bundle,tmp_path);candidate=deepcopy(out['results'][imp.OPS[0]])
    data=candidate['data'];d=data['feedback']
    if field=='variance':d['linearized_variance']+=.01
    elif field=='cross':d['cross_terms'][0]['signed_variance_term']+=1
    elif field=='matrix':d['candidates'][0]['counterfactual_matrix'][0][0]+=1
    elif field=='ranking':d['ranking'].reverse()
    elif field=='status':data['status']='AUTHORIZED'
    elif field=='hub':data['hub']['status']='VERIFIED'
    elif field=='source':data['source_evidence_id']=c.bytes_ref(b'other')
    _,_,s,_=p.read(tmp_path/'review')
    assert check.audit(s.run,candidate)['status']=='FAIL'


@pytest.mark.parametrize('file',['bundle.json','plan.json','workspace.json','graph.json'])
def test_retained_tampering_rejected(bundle,tmp_path,file):
    run(bundle,tmp_path)
    path=tmp_path/'review'/file
    value=json.loads(path.read_bytes())
    value['tampered']=True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):p.inspect(tmp_path/'review')


def test_false_original_prerequisite_refuses_before_runtime(bundle,tmp_path,monkeypatch):
    run(bundle,tmp_path)
    _,declaration,session,_=p.read(tmp_path/'review');session.operations=imp.registry().operations
    original={r['operation_id']:r for r in session.results.values()}
    model=deepcopy(original[study.OPS[2]]);model['data']['mean']=100
    response=session.handle({'protocol_version':1,'request_id':'bad','type':'operation.execute','payload':{
        'operation_id':imp.OPS[0],'parameters':{'model':model,'audit':original[study.OPS[3]],'plan':declaration}}})
    assert response['payload']['status']=='refused'


def test_cli_installed_dispatch_and_create_only(bundle,tmp_path):
    exchange.save(bundle,tmp_path/'bundle.json')
    commands=[['plan',str(tmp_path/'bundle.json'),'--instance-id','local','--target-uncertainty','.03','--output',str(tmp_path/'plan.json')],
        ['run',str(tmp_path/'bundle.json'),str(tmp_path/'plan.json'),'--output-dir',str(tmp_path/'review')],
        ['inspect',str(tmp_path/'review')],['report',str(tmp_path/'review'),'--output',str(tmp_path/'panel.html')],
        ['replay',str(tmp_path/'review'),'--output-dir',str(tmp_path/'replay')],
        ['restore-source',str(tmp_path/'review'),'--output-dir',str(tmp_path/'restored')]]
    for args in commands:
        proc=subprocess.run([sys.executable,'-m','ciw.net','link','pinwheel',*args],capture_output=True,timeout=30)
        assert proc.returncode==0,proc.stderr.decode()
        assert json.loads(proc.stdout)
    content=(tmp_path/'panel.html').read_bytes()
    assert b'NOT ESTABLISHED' in content and b'PROPOSAL ONLY' in content
    with pytest.raises(FileExistsError):p.report(tmp_path/'review',tmp_path/'panel.html')
    with pytest.raises(FileExistsError):run(bundle,tmp_path)


def test_real_stdio_mcp_inspect_and_denied_execution(bundle,tmp_path):
    out=run(bundle,tmp_path);identity=out['results'][imp.OPS[0]]['result_id']
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'pinwheel-test','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_inspect','arguments':{'artifact':'investigation','selector':['results',identity,'data','feedback','proposed_candidate_id']}}},
        {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'net_execute','arguments':{'source':'investigation','graph':'other','attempt':'a'}}}]
    proc=subprocess.run([sys.executable,'-m','ciw.link_pinwheel_cli','mcp',str(tmp_path/'review')],
        input=b''.join(json.dumps(x).encode()+b'\n' for x in messages),capture_output=True,timeout=30)
    assert proc.returncode==0,proc.stderr.decode()
    lines=list(map(json.loads,proc.stdout.splitlines()))
    assert lines[1]['result']['structuredContent']['data']=='offset-half'
    assert lines[2]['result']['isError'] is True


def test_html_escapes_operator_text(bundle,tmp_path):
    d=plan(bundle);d['candidates'][0]['id']='<script>alert(1)</script>'
    run(bundle,tmp_path,d);p.report(tmp_path/'review',tmp_path/'report.html')
    raw=(tmp_path/'report.html').read_text()
    assert '<script>' not in raw and '&lt;script&gt;' in raw
    assert "default-src 'none'" in raw


def test_objective_already_met_requires_no_augmentation(bundle,tmp_path):
    d=plan(bundle);d['objective']['max_standard_uncertainty']=1.
    result=run(bundle,tmp_path,d)
    review=result['results'][imp.OPS[0]]['data']
    assert review['feedback']['objective_met'] is True and review['feedback']['proposed_candidate_id'] is None
    assert review['feedback']['ranking']  # alternatives remain inspectable
    assert result['results'][imp.OPS[1]]['data']['status']=='PASS'


@pytest.mark.parametrize('seed',range(8))
def test_seeded_full_covariance_differential(tmp_path,seed):
    import random
    rng=random.Random(seed);r=example();cov=r['calibration']['joint_covariance']
    factors=[[rng.randint(-3,3) for _ in range(5)] for _ in range(5)]
    cov['matrix']=[[sum(factors[i][k]*factors[j][k] for k in range(5)) for j in range(5)] for i in range(5)]
    cov['covariance_id']=covariance_identity(cov)
    b=bundle_for(tmp_path,r);d=plan(b)
    d['candidates']=[{'id':str(i),'label':'Synthetic hypothesis','kind':'covariance_scale_counterfactual',
        'rationale':'Authored test, not empirical improvement','scales':[rng.choice([0,.25,.5,.75]) for _ in range(5)]} for i in range(8)]
    result=run(b,tmp_path,d)
    assert result['results'][imp.OPS[1]]['data']['status']=='PASS'
    assert len(result['results'][imp.OPS[0]]['data']['feedback']['candidates'])==8
