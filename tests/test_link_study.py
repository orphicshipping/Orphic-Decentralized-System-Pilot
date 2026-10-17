"""Software/numerical qualification only; all instrument records are synthetic."""
from copy import deepcopy
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from ciw import link_contract as c, link_study as s, link_study_io as io
from ciw.link_study_cli import example
from ciw.link_profiles import example_manifest
from ciw.link_runtime import Authority,DeviceRuntime
from ciw.link_store import Journal,decode_bound
from ciw.link_gateway import capture
from ciw.link_protocol import encode,MAX_FRAME
from ciw.core.covariance import covariance_identity,create_covariance_artifact


def reseal_cov(x):
    x['covariance_id']=covariance_identity(x)


def request_from(chunks, tmp_path, *, ticks=None, context=None, acquired_at=None):
    d=example_manifest('GaugeLink')
    d['channels'][0].update(quantity='force',unit='N',encoding='opaque.v1')
    d['clock'].update(id='link.host.monotonic_ns',tick_hz=1000000000)
    with Journal(tmp_path/'input.sqlite',create=True) as j:
        rt=DeviceRuntime(d,j,origin='synthetic',boot_id='synthetic.test.boot')
        for i,b in enumerate(chunks):rt.emit('CH0',b,tick=(100+i*100 if ticks is None else ticks[i]),context=context,acquired_at=acquired_at)
        cap=capture(j,expected_manifest=d,boot_id=rt.boot_id)
    return {'schema':s.SCHEMA,'capture':cap,'channel_id':'CH0','calibration':{
        'gain':1.0,'offset':0.0,'input_range':[-100,100],'declaration':None,'joint_covariance':None},
        'model':{'reference_kind':'declared_exact','expected_mean':2.0,'residual_limit':0.1}}


@pytest.mark.parametrize('split',range(1,17))
def test_every_split_preserves_bytes(split,tmp_path):
    raw=b'1.0 N\r\n2.0 N\r\n3.0 N\r\n'
    req=request_from([raw[:split],raw[split:]],tmp_path)
    result=s.decode(req)
    assert c.unpack(result['stream'],8192)==raw
    assert [r['value'] for r in result['rows']]==[1,2,3]
    for row in result['rows']:
        start,stop=row['byte_range'];assert c.unpack(row['raw'],128)==raw[start:stop]
        assert sum(x['range'][1]-x['range'][0] for x in row['chunks'])==stop-start


@pytest.mark.parametrize('raw',[b'1 N\n',b'1 N\r',b'1 N\r\n2',b'NaN N\r\n',b'1 kg\r\n',b'1e3 N\r\n',
    b'1,2 N\r\n',b'\xff N\r\n',b'1 N\r\n\r\n',b'1  N\r\n',b'*10\r\n',b'ZERO\r\n',b'1 N\r\n'+b'2'*130+b' N\r\n'])
def test_malformed_refuses_without_repair(raw,tmp_path):
    req=request_from([raw],tmp_path)
    with pytest.raises(ValueError):s.decode(req)
    out=io.run(req,tmp_path/'study')
    assert out['status']=='incomplete'
    assert not out['results']
    assert io.inspect(tmp_path/'study')['status']=='incomplete'
    assert json.loads((tmp_path/'study/request.json').read_bytes())==req


@pytest.mark.parametrize('path,value',[(('calibration','gain'),True),(('calibration','gain'),float('nan')),
    (('calibration','offset'),float('inf')),(('calibration','input_range'),[1,0]),
    (('model','residual_limit'),-1),(('model','reference_kind'),'measured'),(('channel_id',),'CH9'),
    (('schema',),'other.v1')])
def test_request_refusals(path,value):
    r=example();target=r
    for key in path[:-1]:target=target[key]
    target[path[-1]]=value
    with pytest.raises((ValueError,KeyError)):s.decode(r)


def test_shared_uncertainty_not_averaged_away():
    r=example();d=s.decode(r);a=s.affine(r,d);m=s.mean_model(r,a)
    assert m['mean']==pytest.approx(2.03)
    assert m['variance']==pytest.approx(0.00190808)
    independent=sum(a['covariance']['matrix'][i][i] for i in range(3))/9
    assert m['variance']>independent
    assert a['applicability']==['ACQUISITION_TIME_UNKNOWN']*3
    assert a['covariance']['provenance']['source_covariance_ids']==[r['calibration']['joint_covariance']['covariance_id']]
    assert m['inspection_qualification']=='HELD'


@pytest.mark.parametrize('axis',range(5))
def test_covariance_reference_binding(axis):
    r=example();cov=r['calibration']['joint_covariance'];cov['reference_values'][axis]+=1;reseal_cov(cov)
    with pytest.raises(ValueError):s.affine(r,s.decode(r))


@pytest.mark.parametrize('kind',['axis','unit','frame','basis','ancestry','negative','zero_cross','asymmetry','bool'])
def test_covariance_refusals(kind):
    r=example();v=r['calibration']['joint_covariance']
    if kind=='axis':v['quantity_ids'][0]='other'
    elif kind=='unit':v['units'][0]='m'
    elif kind=='frame':v['frame']='other'
    elif kind=='basis':v['basis']['id']=c.bytes_ref(b'wrong')
    elif kind=='ancestry':v['provenance']['source_evidence_ids']=[]
    elif kind=='negative':v['matrix'][0][0]=-1
    elif kind=='zero_cross':v['matrix'][0][0]=0
    elif kind=='asymmetry':v['matrix'][0][1]=0.01
    else:v['matrix'][0][0]=True
    reseal_cov(v)
    with pytest.raises(ValueError):s.affine(r,s.decode(r))


@pytest.mark.parametrize('stamp,status',[('2026-01-01T00:00:00Z','DECLARED_APPLICABLE'),
    ('2026-12-31T23:59:59Z','DECLARED_APPLICABLE'),('2027-01-01T00:00:00Z','OUTSIDE_DECLARED_VALIDITY')])
def test_half_open_applicability(tmp_path,stamp,status):
    r=request_from([b'2 N\r\n'],tmp_path,acquired_at=stamp)
    declaration=example()['calibration']['declaration']
    declaration['manifest_ref']=c.content_identity(r['capture']['manifest'])
    r['calibration']['declaration']=declaration
    assert s.affine(r,s.decode(r))['applicability']==[status]


def test_missing_covariance_not_zero(tmp_path):
    r=request_from([b'2 N\r\n'],tmp_path);a=s.affine(r,s.decode(r));m=s.mean_model(r,a)
    assert a['covariance'] is None and m['variance'] is None and m['standard_uncertainty'] is None


def test_native_duplicate_receipt_withheld(tmp_path):
    r=request_from([b'1 N\r\n2 N\r\n'],tmp_path)
    d=s.decode(r);a=s.affine(r,d)
    v=s.native(r,d,a,execution_id='execution-test',source_evidence=c.content_identity(r))
    assert len(v['stream']['observations'])==1 and len(v['withheld'])==1
    assert v['rows'][1]['value']==2
    assert v['mappings'][0]['line_id']==d['rows'][0]['id']


def test_unknown_ticks_do_not_substitute_host_time(tmp_path):
    r=request_from([b'2 N\r\n'],tmp_path,ticks=[None]);d=s.decode(r)
    v=s.native(r,d,s.affine(r,d),execution_id='e',source_evidence=c.content_identity(r))
    assert not v['stream']['observations'] and len(v['withheld'])==1


def test_run_replay_export_mcp(tmp_path):
    r=example();out=io.run(r,tmp_path/'first');assert out['status']=='completed'
    assert list(out['results'])==list(s.OPS)
    audit=out['results'][s.OPS[-1]];assert audit['data']['status']=='PASS'
    old=(tmp_path/'first/workspace.json').read_bytes()
    again=io.replay(tmp_path/'first',tmp_path/'again')
    assert out['evidence_id']==again['evidence_id']
    assert set(out['executions']).isdisjoint(again['executions'])
    assert old==(tmp_path/'first/workspace.json').read_bytes()
    io.export(tmp_path/'first',tmp_path/'native')
    cov=json.loads((tmp_path/'native/covariance.json').read_bytes())
    assert cov==out['results'][s.OPS[1]]['data']['covariance']
    stream=json.loads((tmp_path/'native/observations.json').read_bytes())
    assert stream['observations'][0]['uncertainty']['provenance']['source_covariance_ids']==[cov['covariance_id']]
    with io.agent(tmp_path/'first') as host:
        modelid=out['results'][s.OPS[2]]['result_id']
        assert host.inspect('investigation',['results',modelid,'data','mean'])['data']==pytest.approx(2.03)
        assert host.capabilities()['host_policy']['allowed_operations']==[]
    report=tmp_path/'panel.html';io.report(tmp_path/'first',report)
    assert b'PHYSICAL QUALIFICATION HELD' in report.read_bytes()
    with pytest.raises(FileExistsError):io.run(r,tmp_path/'first')
    with pytest.raises(FileExistsError):io.report(tmp_path/'first',report)


def test_actual_stdio_mcp(tmp_path):
    out=io.run(example(),tmp_path/'study');mid=out['results'][s.OPS[2]]['result_id']
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'link-test','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_inspect','arguments':{'artifact':'investigation','selector':['results',mid,'data','mean']}}},
        {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'net_execute','arguments':{'source':'investigation','graph':'other','attempt':'a'}}}]
    proc=subprocess.run([sys.executable,'-m','ciw.link_study_cli','mcp',str(tmp_path/'study')],
        input=b''.join(json.dumps(x).encode()+b'\n' for x in messages),capture_output=True,timeout=20)
    assert proc.returncode==0,proc.stderr.decode()
    lines=[json.loads(x) for x in proc.stdout.splitlines()]
    assert len(lines)==3 and lines[1]['result']['structuredContent']['data']==pytest.approx(2.03)
    assert lines[2]['result']['isError'] is True


def test_forged_prerequisite_refuses_before_execute(tmp_path):
    from ciw.session import Session
    r=example();reg=s.registry();session=Session(s.make_source(r,c.content_identity(r)),tmp_path/'s',operations=reg.operations)
    first=session.handle({'protocol_version':1,'request_id':'1','type':'operation.execute','payload':{'operation_id':s.OPS[0],'parameters':{}}})['payload']['result']
    altered=deepcopy(first);altered['data']['rows'][0]['value']=100
    response=session.handle({'protocol_version':1,'request_id':'2','type':'operation.execute','payload':{'operation_id':s.OPS[1],'parameters':{'decoded':altered}}})
    assert response['payload']['status']=='refused'


def test_audit_independently_detects_corruption(tmp_path):
    out=io.run(example(),tmp_path/'s');request,_,_,_=io.read(tmp_path/'s')
    candidate=deepcopy(out['results'][s.OPS[2]])
    candidate['data']['mean']+=0.1
    assert s.audit(request,candidate)['status']=='FAIL'


def test_whole_covariance_tiny_values_not_erased(tmp_path):
    r=example();cov=r['calibration']['joint_covariance'];cov['matrix']=[[x*1e-280 for x in row] for row in cov['matrix']];reseal_cov(cov)
    result=io.run(r,tmp_path/'s');assert result['status']=='completed'
    assert 0<result['results'][s.OPS[2]]['data']['variance']<1e-270
    assert result['results'][s.OPS[-1]]['data']['status']=='PASS'


@pytest.mark.skipif(sys.platform!='linux',reason='Linux TTY backend; other platforms explicitly unsupported')
def test_real_pty_disconnect_local_sync_and_study(tmp_path):
    import pty
    from ciw.link_serial import capture_serial
    d=example()['capture']['manifest'];master,slave=pty.openpty();path=os.ttyname(slave)
    raw=b'1.0 N\r\n2.0 N\r\n3.0 N\r\n'
    def producer():
        time.sleep(.08);os.write(master,raw[:4]);time.sleep(.03);os.write(master,raw[4:]);time.sleep(.08);os.close(master)
    t=threading.Thread(target=producer);t.start()
    try:
        with Journal(tmp_path/'out.sqlite',create=True) as j:
            auth=Authority('test',d['device_id'],frozenset({'READ'}),time.time_ns()+5_000_000_000)
            receipt=capture_serial(path,device=d,journal=j,authority=auth,channel_id='CH0',duration_s=1,allow_pty=True)
            assert receipt['status'].startswith('DISCONNECTED') and receipt['bytes']==len(raw)
            assert receipt['bytes_transmitted']==0 and receipt['origin']=='synthetic'
            with Journal(tmp_path/'in.sqlite',create=True) as receiver:
                first=j.sync_to(receiver,manifests=[d]);retry=j.sync_to(receiver,manifests=[d])
                assert first['new_acknowledgements']>0 and retry['new_acknowledgements']==0
                cap=capture(receiver,expected_manifest=d,boot_id=receipt['boot_id'])
        r={'schema':s.SCHEMA,'capture':cap,'channel_id':'CH0','calibration':{'gain':1,'offset':0,'input_range':[0,10],'declaration':None,'joint_covariance':None},
            'model':{'reference_kind':'declared_exact','expected_mean':2,'residual_limit':0}}
        out=io.run(r,tmp_path/'study');assert out['status']=='completed'
        assert out['results'][s.OPS[2]]['data']['mean']==2
        assert c.unpack(out['results'][s.OPS[0]]['data']['stream'],8192)==raw
    finally:
        t.join();os.close(slave)


@pytest.mark.parametrize('permission,expiry',[('CONFIGURE',5_000_000_000),('READ',-1)])
def test_serial_permission_refuses_before_open(permission,expiry,tmp_path):
    from ciw.link_serial import capture_serial
    d=example()['capture']['manifest']
    with Journal(tmp_path/'j.sqlite',create=True) as j:
        auth=Authority('test',d['device_id'],frozenset({permission}),time.time_ns()+expiry)
        with pytest.raises(PermissionError):capture_serial('/no/such/device',device=d,journal=j,authority=auth,channel_id='CH0')
        assert j.rows()==[]


@pytest.mark.parametrize('size',[1,2,4,8])
def test_independent_random_joint_covariance(size,tmp_path):
    import random
    rng=random.Random(40+size)
    r=request_from([f'{i+1}.0 N\r\n'.encode() for i in range(size)],tmp_path)
    r['calibration'].update(gain=0.75,offset=-0.125)
    d=s.decode(r); k=size+2
    L=[[rng.uniform(-.03,.03) if j<=i else 0.0 for j in range(k)] for i in range(k)]
    mat=[[sum(L[i][a]*L[j][a] for a in range(k)) for j in range(k)] for i in range(k)]
    r['calibration']['joint_covariance']=create_covariance_artifact(matrix=mat,
        quantity_ids=[x['id']+':input' for x in d['rows']]+['gain','offset'], units=['N']*size+['1','N'],
        frame=d['channel']['frame'],reference_values=[x['value'] for x in d['rows']]+[.75,-.125],
        method='synthetic_Gram',basis={'kind':'parameter','id':s.calibration_basis(r)},
        provenance={'provider':'synthetic.test','source_evidence_ids':[d['capture_ref']],'source_covariance_ids':[]},assumptions=['synthetic'])
    out=io.run(r,tmp_path/'s')
    assert out['status']=='completed' and out['results'][s.OPS[-1]]['data']['status']=='PASS'


def test_cancellation_preserves_small_covariance(tmp_path):
    # Two perfectly anticorrelated large contributions cancel, leaving a tiny
    # independently declared offset variance. Never erase that surviving term.
    r=request_from([b'1 N\r\n'],tmp_path);d=s.decode(r)
    r['calibration']['joint_covariance']=create_covariance_artifact(matrix=[[1.,-1.,0.],[-1.,1.,0.],[0.,0.,1e-280]],
        quantity_ids=[d['rows'][0]['id']+':input','gain','offset'],units=['N','1','N'],frame=d['channel']['frame'],
        reference_values=[1.,1.,0.],method='synthetic_cancellation',basis={'kind':'parameter','id':s.calibration_basis(r)},
        provenance={'provider':'synthetic.test','source_evidence_ids':[d['capture_ref']],'source_covariance_ids':[]},assumptions=['synthetic'])
    out=io.run(r,tmp_path/'s')
    assert out['results'][s.OPS[2]]['data']['variance']==1e-280
    assert out['results'][s.OPS[-1]]['data']['status']=='PASS'


def test_foreign_source_prerequisite_refused(tmp_path):
    from ciw.session import Session
    r=example();reg=s.registry();first=Session(s.make_source(r,c.content_identity(r)),tmp_path/'a',operations=reg.operations)
    result=first.handle({'protocol_version':1,'request_id':'a','type':'operation.execute','payload':{'operation_id':s.OPS[0],'parameters':{}}})['payload']['result']
    other=example();second=Session(s.make_source(other,c.content_identity(other)),tmp_path/'b',operations=reg.operations)
    second.results[result['result_id']]=result
    response=second.handle({'protocol_version':1,'request_id':'b','type':'operation.execute','payload':{'operation_id':s.OPS[1],'parameters':{'decoded':result}}})
    assert response['payload']['status']=='refused'


def test_receipt_reopen_performs_no_serial_io(tmp_path,monkeypatch):
    out=io.run(example(),tmp_path/'s')
    import ciw.link_serial
    def forbidden(*a,**k):raise AssertionError('unexpected serial access')
    monkeypatch.setattr(ciw.link_serial,'capture_serial',forbidden)
    assert io.inspect(tmp_path/'s')['evidence_id']==out['evidence_id']


def test_original_input_reformat_is_not_silent_rewrite(tmp_path):
    r=example();raw=(json.dumps(r,indent=4)+'\n').encode()
    io.run(r,tmp_path/'s',raw=raw)
    assert (tmp_path/'s/request.json').read_bytes()==raw
    io.replay(tmp_path/'s',tmp_path/'r')
    assert (tmp_path/'r/request.json').read_bytes()==raw
