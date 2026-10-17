"""Published-data tests, not synthetic data mislabeled as an acquired instrument."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from ciw import link_contract as c, link_references as refs, link_reference_io as io
from ciw.core.covariance import validate_covariance_artifact
from ciw.control_contracts import save_new, validate_artifact
from ciw.operations.runner import seal

# Data-only fixtures are copied here for tests from the attributed product data
# pack. No fixture bypasses the fixed reference profile source hashes.
DATA=Path(__file__).parent/'reference_data'
NAMES={'nist-pontius':'PONTIUS.DAT','ptb-mh44-first17':'MW_MH44ReIm.lines-1-18.csv'}

@pytest.fixture(params=list(NAMES))
def req(request):
    return refs.request(request.param,(DATA/NAMES[request.param]).read_bytes())


def test_source_hash_scope_is_explicit(req):
    checked=refs.validate_request(req);p=refs.PROFILES[req['profile_id']]
    raw=c.unpack(checked['payload'],8192)
    assert sha256(raw).hexdigest()==p['sha256']
    assert len(raw)==p['byte_count']
    if p['whole_file_hash_checked']:
        assert sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()==p['upstream_blob']
    else:
        assert p['upstream_lines']==[1,18]
        assert sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()!=p['upstream_blob']
    assert p['acquisition_time'] is None
    assert req['source_artifact']['metadata']['created_at_meaning'].endswith('not_physical_acquisition')

@pytest.mark.parametrize('mutation',['raw','length','digest','profile','extra','artifact','auth_claim','boolean'])
def test_resealed_inputs_cannot_forge_published_data(req,tmp_path,mutation):
    q=deepcopy(req)
    if mutation=='raw':q['payload']=c.packed(c.unpack(q['payload'],8192).replace(b'1',b'2',1),8192)
    elif mutation=='length':q['payload']['byte_count']+=1
    elif mutation=='digest':q['payload']['sha256']='sha256:'+'0'*64
    elif mutation=='profile':q['profile_id']='other'
    elif mutation=='extra':q['control']='calibrate'
    elif mutation=='artifact':q['source_artifact']['metadata']['source_url']='https://example.org'
    elif mutation=='auth_claim':q['source_artifact']['metadata']['whole_file_hash_checked']=True if q['profile_id']!='nist-pontius' else False;q['source_artifact']=seal(q['source_artifact'])
    else:q['payload']['byte_count']=True
    with pytest.raises((ValueError,TypeError,KeyError)):io.run(q,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_pontius_whole_data_numeric_benchmark():
    q=refs.request('nist-pontius',(DATA/'PONTIUS.DAT').read_bytes());r=refs.review(q);data=r['data']
    assert len(data['rows'])==40
    assert [v['set_index'] for v in data['rows']]==[0]*20+[1]*20
    assert data['rows'][0]['deflection']=='.11019' and data['rows'][20]['deflection']=='.11052'
    assert data['published_reference_match'] is True
    assert abs(Decimal(data['residual_sum_squares'])-Decimal(refs.PONTIUS_RSS))<=Decimal('5e-21')
    assert data['fit_performed'] is False and data['covariance'] is None
    assert r['source']['units']==[None,None]
    assert refs.audit(q,r)['status']=='PASS'
    assert all('source' in row or 'byte_range' in row for row in data['rows'])


def test_ptb_actual_paired_covariance_and_original_columns():
    raw=(DATA/NAMES['ptb-mh44-first17']).read_bytes();q=refs.request('ptb-mh44-first17',raw)
    result=refs.review(q);data=result['data'];rows=data['rows']
    assert len(rows)==17 and len(data['original_header'])==12
    assert Decimal(rows[0]['frequency_hz'])==100000
    assert Decimal(rows[-1]['frequency_hz'])==1700000
    assert data['joint_cross_frequency_covariance'] is None
    assert data['deconvolution']=='not_performed'
    for row in rows:
        assert len(row['original_fields'])==12
        assert raw[slice(*row['byte_range'])].strip()
        original=row['original_fields'];joint=row['covariance'];mapped=row['magnitude_covariance']
        validate_covariance_artifact(joint);validate_covariance_artifact(mapped)
        assert joint['matrix']==[[float(original[3]),float(original[5])],[float(original[5]),float(original[4])]]
        assert mapped['provenance']['source_covariance_ids']==[joint['covariance_id']]
        assert row['magnitude_table_agreement'] and row['uncertainty_table_agreement']
    assert rows[0]['covariance']['matrix'][0][1]<0
    assert refs.audit(q,result)['status']=='PASS'


def test_independent_audit_detects_new_math_errors(req):
    result=refs.review(req)
    if req['profile_id']=='nist-pontius':result['data']['rows'][0]['predicted']='0'
    else:result['data']['rows'][0]['magnitude_variance']*=2
    assert refs.audit(req,result)['status']=='FAIL'


def test_primary_does_not_depend_on_global_decimal_precision(req):
    original=refs.review(req)
    with localcontext() as ctx:
        ctx.prec=7
        assert c.canonical_json(refs.review(req))==c.canonical_json(original)


def test_session_reopen_replay_and_export_actual(req,tmp_path):
    raw=(json.dumps(req,indent=3)+'\n').encode()
    a=io.run(req,tmp_path/'first',raw=raw)
    assert a['status']=='completed' and a['results'][refs.OPS[1]]['data']['status']=='PASS'
    assert (tmp_path/'first/request.json').read_bytes()==raw
    before={p.name:p.read_bytes() for p in (tmp_path/'first').iterdir() if p.is_file()}
    b=io.inspect(tmp_path/'first');assert b==a
    again=io.replay(tmp_path/'first',tmp_path/'again')
    assert again['evidence_id']==a['evidence_id']
    assert set(again['executions']).isdisjoint(a['executions'])
    assert before=={p.name:p.read_bytes() for p in (tmp_path/'first').iterdir() if p.is_file()}
    manifest=io.export(tmp_path/'first',tmp_path/'export')
    assert io.inspect(tmp_path/'export')==a
    for name,digest in manifest['files'].items():assert c.bytes_ref((tmp_path/'export'/name).read_bytes())==digest
    assert (tmp_path/'export/source.bin').read_bytes()==c.unpack(req['payload'],8192)
    for name in io.FILES:assert (tmp_path/'export'/name).read_bytes()==before[name]
    assert a['results'][refs.OPS[0]]['data']['limits']['calibration_transfer']=='not_authorized'

@pytest.mark.parametrize('target',['request.json','workspace.json','graph.json'])
def test_changed_retained_bytes_refuse(req,tmp_path,target):
    io.run(req,tmp_path/'run');p=tmp_path/'run'/target
    data=json.loads(p.read_text())
    if target=='request.json':data['profile_id']='wrong'
    elif target=='workspace.json':data['results'][0]['data']['limits']['physical_qualification']='PASS';data['results'][0]=seal(data['results'][0])
    else:data['source_evidence_id']='sha256:'+'f'*64;data=seal(data)
    p.write_text(json.dumps(data))
    with pytest.raises((ValueError,KeyError)):io.inspect(tmp_path/'run')


def test_wrong_original_prerequisite_refuses_before_runtime(req,tmp_path,monkeypatch):
    info=io.run(req,tmp_path/'run');_,_,session,_=io.read(tmp_path/'run')
    prior=deepcopy(info['results'][refs.OPS[0]]);prior['result_id']='result-'+'f'*32
    registry=refs.registry();called=[]
    from ciw.operations.registry import Operation, OperationRegistry
    old=registry.operations.get(refs.OPS[1]); guarded=OperationRegistry()
    guarded.register(Operation(refs.OPS[1],old.role,old.execute,lambda:called.append(1)))
    from ciw.operations.runner import execute
    e,result=execute(guarded,session.run,session.selection,str(tmp_path/'file'),refs.OPS[1],{'candidate':prior},retained_results=session.results)
    assert e['status']=='refused' and result is None and not called


def test_real_stdio_mcp_reference_inspection_and_denial(req,tmp_path):
    info=io.run(req,tmp_path/'run');audit=info['results'][refs.OPS[1]]['result_id']
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
      {'jsonrpc':'2.0','method':'notifications/initialized'},
      {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_inspect','arguments':{'artifact':'investigation','selector':['results',audit,'data','status']}}},
      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'net_execute','arguments':{'source':'investigation','graph':'other','attempt':'x'}}}]
    proc=subprocess.run([sys.executable,'-m','ciw.link_reference_io','mcp',str(tmp_path/'run')],input=b''.join(json.dumps(x).encode()+b'\n' for x in messages),capture_output=True,timeout=20)
    assert proc.returncode==0,proc.stderr
    replies=[json.loads(line) for line in proc.stdout.splitlines()]
    assert 'PASS' in json.dumps(replies[1])
    assert replies[2]['result']['isError'] is True


def test_create_only_and_no_implicit_network(req,tmp_path,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'socket',lambda *a,**k:(_ for _ in ()).throw(AssertionError('network forbidden')))
    first=io.run(req,tmp_path/'run')
    with pytest.raises(FileExistsError):io.run(req,tmp_path/'run')
    assert io.inspect(tmp_path/'run')==first
    panel=tmp_path/'panel.html';io.report(tmp_path/'run',panel)
    assert 'NOT QUALIFIED' in panel.read_text()
    with pytest.raises(FileExistsError):io.report(tmp_path/'run',panel)


def test_export_semantics_cannot_be_resealed(req,tmp_path):
    io.run(req,tmp_path/'run');io.export(tmp_path/'run',tmp_path/'export')
    assert io.review_export(tmp_path/'export')['new_execution'] is False
    f=tmp_path/'export/reference-audit.json';value=json.loads(f.read_text());value['limits']['physical_qualification']='PASS'
    f.write_text(json.dumps(value));m=tmp_path/'export/export-manifest.json';manifest=json.loads(m.read_text())
    manifest['files'][f.name]=c.bytes_ref(f.read_bytes());m.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):io.review_export(tmp_path/'export')


def test_export_extra_and_symlink_refused(req,tmp_path):
    io.run(req,tmp_path/'run');io.export(tmp_path/'run',tmp_path/'export')
    extra=tmp_path/'export/extra';extra.write_bytes(b'not source')
    with pytest.raises(ValueError):io.review_export(tmp_path/'export')
    extra.unlink();source=tmp_path/'export/source.bin';original=source.read_bytes();source.unlink()
    other=tmp_path/'other';other.write_bytes(original);source.symlink_to(other)
    with pytest.raises(ValueError):io.review_export(tmp_path/'export')


def test_unknown_workspace_fields_refuse(req,tmp_path):
    io.run(req,tmp_path/'run')
    f=tmp_path/'run/workspace.json'; value=json.loads(f.read_text());value['authority']='approved'
    f.write_text(json.dumps(value))
    with pytest.raises(ValueError):io.inspect(tmp_path/'run')
