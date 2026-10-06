"""Retained measurement/roster/exchange tests; synthetic evidence only."""
import base64
from copy import deepcopy
import csv
import io as stdio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from ciw import link_contract as c, link_devices as devices, link_measurements as m
from ciw import link_measurement_io as io, link_study as study, link_study_io as prior
from ciw.link_measurement_agent import MeasurementHost
from ciw.link_study_cli import example
from ciw.link_store import Journal
from ciw.link_profiles import example_manifest
from ciw.link_protocol import encode


@pytest.fixture
def bundle(tmp_path):
    request=example()
    # Source JSON whitespace is intentional, and must survive round trip.
    raw=json.dumps(request,indent=3).encode()+b'\n'
    prior.run(request,tmp_path/'study',raw=raw)
    return m.build(tmp_path/'study')


def reseal(value):
    value['bundle_id']=c.content_identity({k:v for k,v in value.items() if k!='bundle_id'})
    return value


def test_measurement_stages_original_bytes_and_identity(bundle):
    rows=bundle['measurements'];assert len(rows)==6
    assert len({r['identity']['measurement_id'] for r in rows})==6
    for indicated,corrected in zip(rows[::2],rows[1::2]):
        assert indicated['quantity']['stage']=='indicated' and corrected['quantity']['stage']=='corrected'
        assert indicated['identity']['line_id']==corrected['identity']['line_id']
        assert indicated['identity']['measurement_id']!=indicated['identity']['line_id']
        assert indicated['provenance']['execution_id']!=corrected['provenance']['execution_id']
        assert corrected['provenance']['parents']==[indicated['identity']['measurement_id']]
        assert c.unpack(indicated['provenance']['original_line'],128).endswith(b' N\r\n')
        assert indicated['time']['acquisition_time'] is None
        assert corrected['metrology']['declared_applicability']=='ACQUISITION_TIME_UNKNOWN'
        assert corrected['metrology']['inspection_qualification']=='HELD'
        assert corrected['verification']['arithmetic_status']=='PASS'
    cov=bundle['native']['covariance']
    assert cov['matrix'][0][1]!=0
    for i,row in enumerate(rows[1::2]):
        u=row['metrology']['uncertainty']
        assert u['covariance_ref']==cov['covariance_id'] and u['index']==i
        assert u['variance']==cov['matrix'][i][i]
    assert cov['provenance']['source_covariance_ids']==[bundle['input_covariance']['covariance_id']]


@pytest.mark.parametrize('area,key,value',[
    ('identity','device_id','other'),('identity','measurement_id','fake'),('identity','capture_ref','fake'),
    ('quantity','stage','calibrated'),('quantity','value',False),('quantity','unit','kg'),
    ('time','acquisition_time','2026-01-01T00:00:00Z'),('time','clock_relation','SYNCED'),
    ('metrology','traceability','TRACEABLE'),('metrology','inspection_qualification','PASS'),
    ('provenance','decoder_id','wrong'),('provenance','parents',[]),
    ('verification','status','VERIFIED'),('native','observation_ref','fake'),
])
def test_resealed_view_forgery_rejected(bundle,area,key,value):
    bundle['measurements'][1][area][key]=value;reseal(bundle)
    with pytest.raises(ValueError):m.validate(bundle)


@pytest.mark.parametrize('mutation',['drop_row','reorder','drop_cross','wrong_index','host_time','raw','extra_field','missing_source','source_bytes'])
def test_bundle_and_lineage_tamper(bundle,mutation):
    if mutation=='drop_row':bundle['measurements'].pop()
    elif mutation=='reorder':bundle['measurements'].reverse()
    elif mutation=='drop_cross':bundle['native']['covariance']['matrix'][0][1]=0
    elif mutation=='wrong_index':bundle['measurements'][1]['metrology']['uncertainty']['index']=2
    elif mutation=='host_time':bundle['measurements'][0]['time']['chunk_receipts'][0]['received_ns']+=1
    elif mutation=='raw':bundle['measurements'][0]['provenance']['original_line']=c.packed(b'42 N\r\n')
    elif mutation=='extra_field':bundle['approved']=True
    elif mutation=='missing_source':del bundle['source_files']['graph.json']
    else:bundle['source_files']['request.json']['chunks'][0]['bytes_b64']='AAAA'
    reseal(bundle)
    with pytest.raises((ValueError,KeyError)):m.validate(bundle)


def test_missing_uncertainty_not_fabricated(tmp_path):
    r=example();r['calibration']['joint_covariance']=None;prior.run(r,tmp_path/'s')
    b=m.build(tmp_path/'s');assert b['input_covariance'] is None and b['native']['covariance'] is None
    assert all(x['metrology']['uncertainty']['status']=='UNKNOWN' for x in b['measurements'])
    a=io.export_objects(b,tmp_path/'archive');assert 'input-covariance.json' not in a['files']


def test_query_and_detached_results(bundle):
    rows=m.query(bundle,stage='corrected');assert len(rows)==3
    assert m.query(bundle,device_id='unmatched')==[]
    rows[0]['quantity']['value']=9;assert m.query(bundle,stage='corrected')[0]['quantity']['value']!=9
    with pytest.raises(ValueError):m.query(bundle,stage='decision')
    with pytest.raises(ValueError):m.query(bundle,device_id=False)


def test_offline_restore_same_bytes_and_replay_fresh_occurrences(bundle,tmp_path,monkeypatch):
    import socket
    def no_network(*a,**k):raise AssertionError('network contact')
    monkeypatch.setattr(socket,'create_connection',no_network)
    io.save(bundle,tmp_path/'bundle.json');io.restore(io.load(tmp_path/'bundle.json'),tmp_path/'restored')
    for name in m.FILES:assert (tmp_path/'restored'/name).read_bytes()==m._unpack(bundle['source_files'][name])
    assert m.build(tmp_path/'restored')==bundle
    prior.replay(tmp_path/'restored',tmp_path/'replayed');other=m.build(tmp_path/'replayed')
    assert {x['identity']['line_id'] for x in bundle['measurements']}=={x['identity']['line_id'] for x in other['measurements']}
    assert {x['identity']['measurement_id'] for x in bundle['measurements']}.isdisjoint(x['identity']['measurement_id'] for x in other['measurements'])
    assert other['measurements'][1]['quantity']==bundle['measurements'][1]['quantity']
    assert (tmp_path/'restored/workspace.json').read_bytes()==m._unpack(bundle['source_files']['workspace.json'])


@pytest.mark.parametrize('method',['save','restore','export'])
def test_create_only(bundle,tmp_path,method):
    path=tmp_path/'target'
    if method=='save':path.write_bytes(b'keep');call=lambda:io.save(bundle,path)
    else:path.mkdir();(path/'keep').write_bytes(b'keep');call=lambda:(io.restore if method=='restore' else io.export_objects)(bundle,path)
    with pytest.raises(FileExistsError):call()
    assert (path if method=='save' else path/'keep').read_bytes()==b'keep'


def test_export_raw_covariance_and_csv_roundtrip(bundle,tmp_path):
    a=io.export_objects(bundle,tmp_path/'a');assert io.review_objects(tmp_path/'a')==bundle
    files=a['files'];raw=(tmp_path/'a/objects'/(files['measurements.csv']['sha256'][7:]+'.bin')).read_bytes()
    rows=list(csv.DictReader(stdio.StringIO(raw.decode())))
    assert [json.loads(row['record_json']) for row in rows]==bundle['measurements']
    assert tuple(rows[0])==io.COLUMNS
    assert a['postgres']=='STAGING_FILES_ONLY_NOT_LOADED'
    assert a['parquet']=='NOT_REQUESTED'
    raw=(tmp_path/'a/objects'/(files['output-covariance.json']['sha256'][7:]+'.bin')).read_bytes()
    assert json.loads(raw)==bundle['native']['covariance']


@pytest.mark.parametrize('mutation',['object','resealed_csv','extra','missing','path'])
def test_export_tampering(bundle,tmp_path,mutation):
    root=tmp_path/'archive';a=io.export_objects(bundle,root)
    ref=a['files']['measurements.csv']['sha256'];p=root/'objects'/(ref[7:]+'.bin')
    if mutation=='object':p.write_bytes(b'bad')
    elif mutation=='missing':p.unlink()
    elif mutation=='extra':(root/'objects/extra').write_bytes(b'bad')
    elif mutation=='path':a['files']['measurements.csv']['sha256']='../../escape'
    else:
        p.unlink();raw=b'not the retained measurement table\n';new=c.bytes_ref(raw)
        (root/'objects'/(new[7:]+'.bin')).write_bytes(raw)
        a['files']['measurements.csv']={'sha256':new,'byte_count':len(raw)}
    a['export_id']=c.content_identity({k:v for k,v in a.items() if k!='export_id'})
    (root/'manifest.json').write_bytes(c.bounded(a,65536))
    with pytest.raises((OSError,ValueError)):io.review_objects(root)


def test_parquet_missing_dependency_refusal_before_output(bundle,tmp_path,monkeypatch):
    import builtins
    original=builtins.__import__
    def blocked(name,*a,**k):
        if name.startswith('pyarrow'):raise ImportError('explicit unavailable fixture')
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',blocked)
    with pytest.raises(RuntimeError,match='pyarrow'):io.export_objects(bundle,tmp_path/'no',parquet=True)
    assert not (tmp_path/'no').exists()


def test_device_registry_original_manifest_retry_and_ambiguous_revision(tmp_path):
    device=example_manifest('GaugeLink');raw=json.dumps(device,indent=3).encode()
    with Journal(tmp_path/'devices',create=True) as j:
        first=devices.register(j,raw,approved=True)
        again=devices.register(j,c.bounded(device,65536),approved=True)
        assert first['receipt']==again['receipt'] and len(j.rows())==1
        r=devices.inspect_device(j,device['device_id']);assert c.unpack(r['original_manifest'],65536)==raw
        assert r['connection']=='NOT_PROBED' and r['identity_verification']=='NOT_ESTABLISHED'
        device['configuration']['revision']+=1
        devices.register(j,c.bounded(device,65536),approved=True)
        with pytest.raises(ValueError,match='ambiguous'):devices.inspect_device(j,device['device_id'])
        assert devices.inspect_device(j,device['device_id'],manifest_ref=first['manifest_ref'])['configuration_revision']==1


def test_device_consent_and_read_only_no_writes(tmp_path):
    d=example_manifest('GaugeLink');raw=c.bounded(d,65536);path=tmp_path/'j'
    with Journal(path,create=True) as j:
        with pytest.raises(PermissionError):devices.register(j,raw)
        assert j.rows()==[];devices.register(j,raw,approved=True)
    before=path.read_bytes()
    with Journal(path,read_only=True) as j:
        assert len(devices.list_devices(j))==1
        with pytest.raises(PermissionError):
            with j.transaction():pass
        with pytest.raises(sqlite3.OperationalError):j.db.execute('DELETE FROM messages')
    assert path.read_bytes()==before
    with pytest.raises(ValueError):Journal(tmp_path/'other',create=True,read_only=True)
    assert not (tmp_path/'other').exists()


def test_missing_device_and_host_snapshot(bundle,tmp_path):
    d=example_manifest('GaugeLink')
    with Journal(tmp_path/'j',create=True) as j:
        devices.register(j,c.bounded(d,65536),approved=True);host=MeasurementHost(bundle,j)
        with pytest.raises(ValueError):devices.inspect_device(j,'missing')
    bundle['measurements'][0]['quantity']['value']=999
    assert host.call('measurements_query',{'limit':1})['measurements'][0]['quantity']['value']!=999
    assert len(host.call('devices_list',{})['devices'])==1
    assert host.call('device_inspect',{'device_id':d['device_id']})['device']['qualification']=='NOT_ESTABLISHED'
    row=host.call('measurements_query',{'stage':'corrected','limit':1});mid=row['measurements'][0]['identity']['measurement_id']
    assert row['next_offset']==1
    detail=host.call('measurement_inspect',{'measurement_id':mid});assert detail['full_covariance']['matrix'][0][1]!=0
    trace=host.call('evidence_trace',{'measurement_id':mid});assert c.unpack(trace['provenance']['original_line'],128)


@pytest.mark.parametrize('name,args',[('control',{}),('net_execute',{}),('measurements_query',{'limit':True}),
    ('measurements_query',{'offset':-1}),('measurements_query',{'stage':'inferred'}),('measurements_query',{'unknown':'field'}),
    ('measurement_inspect',{}),('measurement_inspect',{'measurement_id':'missing'}),('devices_list',{'token':'no'})])
def test_mcp_refusals(bundle,name,args):
    with pytest.raises((ValueError,PermissionError)):MeasurementHost(bundle).call(name,args)


def test_stdio_real_mcp_and_cli(bundle,tmp_path):
    io.save(bundle,tmp_path/'b.json')
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/list'},
        {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'measurements_query','arguments':{'stage':'corrected','limit':1}}},
        {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'net_execute','arguments':{}}}]
    proc=subprocess.run([sys.executable,'-m','ciw.net','observation','mcp',str(tmp_path/'b.json')],
        input=b''.join(json.dumps(x).encode()+b'\n' for x in messages),capture_output=True,timeout=30)
    assert proc.returncode==0,proc.stderr.decode()
    replies=[json.loads(x) for x in proc.stdout.splitlines()]
    assert len(replies[1]['result']['tools'])==5
    assert replies[2]['result']['structuredContent']['total']==3
    assert replies[3]['result']['isError'] is True


def test_missing_workbench_dependency_is_explicit(bundle,tmp_path,monkeypatch):
    import builtins
    old=builtins.__import__
    def blocked(name,*a,**k):
        if name=='data_sources':raise ImportError('explicit missing owner integration')
        return old(name,*a,**k)
    _,_,session,_=prior.read(tmp_path/'study');before=session.workbench.serialize()
    monkeypatch.setattr(builtins,'__import__',blocked)
    with pytest.raises(RuntimeError,match='PR185/200'):io.attach_workbench(bundle,session,dataset_id='d',recorded_at='2026-10-06T00:00:00Z')
    assert session.workbench.serialize()==before


def test_wrong_replayed_session_rejected(bundle,tmp_path):
    prior.replay(tmp_path/'study',tmp_path/'replay');_,_,session,_=prior.read(tmp_path/'replay')
    with pytest.raises(ValueError,match='exact retained result'):io.attach_workbench(bundle,session,dataset_id='d',recorded_at='2026-10-06T00:00:00Z')


def test_incomplete_study_not_promoted(tmp_path):
    r=example();r['model']['residual_limit']=-1
    out=prior.run(r,tmp_path/'s');assert out['status']=='incomplete'
    with pytest.raises(ValueError):m.build(tmp_path/'s')


def test_primary_recovery_without_optional_derivative_runtime(bundle,tmp_path,monkeypatch):
    root=tmp_path/'archive';manifest=io.export_objects(bundle,root)
    # Explicit opaque invalid derivative fixture. This test does NOT validate
    # Parquet; it proves primary evidence recovery is independent of it.
    raw=b'opaque unqualified derivative fixture'
    ref=c.bytes_ref(raw);(root/'objects'/(ref[7:]+'.bin')).write_bytes(raw)
    manifest['files']['measurements.parquet']={'sha256':ref,'byte_count':len(raw)}
    manifest['parquet']='EXPORTED'
    manifest['export_id']=c.content_identity({k:v for k,v in manifest.items() if k!='export_id'})
    (root/'manifest.json').write_bytes(c.bounded(manifest,65536))
    def unavailable():raise RuntimeError('no optional runtime')
    monkeypatch.setattr(io,'_parquet_modules',unavailable)
    with pytest.raises(RuntimeError):io.review_objects(root)
    primary=io.review_objects(root,verify_parquet=False);assert primary==bundle
    result=io.restore(primary,tmp_path/'recovered');assert result['new_executions']==0
    assert (tmp_path/'recovered/workspace.json').read_bytes()==m._unpack(bundle['source_files']['workspace.json'])
