"""Retained comparison and read-only MCP over synthetic Link records only."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from ciw import device_review as d, device_review_cli as cli, control_checks as checks
from ciw import link_contract as c, link_gateway as gateway, link_workflow as lw
from ciw.agent_mcp import Server, serve
from ciw.control_contracts import bytes_ref, record
from ciw.control_plane import ObservationBus
from ciw.link_profiles import example_manifest, example_observations
from ciw.link_protocol import encode, decode_one, MAX_FRAME
from ciw.link_store import Journal
from ciw.operations.runner import seal
from ciw.session import Session


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(directory).rglob('*') if p.is_file()}


def create_link(path, *, persona='GaugeLink', transform=None, covariance=None):
    device = example_manifest(persona)
    observations = example_observations(device)
    if transform:
        for row in observations:
            transform(row)
    with Journal(path / 'wire.sqlite', create=True) as journal:
        journal.append(encode('manifest', c.bounded(device)), expected_manifest=device)
        for row in observations:
            journal.append(encode('observation', c.bounded(row)), expected_manifest=device)
        capture = gateway.capture(journal, expected_manifest=device, boot_id=observations[0]['boot_id'], joint_covariance=covariance)
    lw.run(capture, path / 'link')
    return path / 'link'


def reference_for(directory, value=25., channel='CH0'):
    summary = lw.inspect(directory)
    data = summary['data']
    selected = {v['measurement_id'] for v in data['observations'] if v['channel_id'] == channel}
    refs = {m['observation_ref'] for m in data['projection']['mappings'] if m['measurement_id'] in selected}
    bus = ObservationBus(c.MAX_RECORDS)
    for row in data['projection']['native_stream']['observations']:
        if row['record_digest'] in refs:
            bus.observe(**{k:deepcopy(row[k]) for k in ('identity','clock','frame','quantity','unit')},
                        value=value, uncertainty=None, provenance={'provider':'authored-test-reference',
                        'sources':[bytes_ref(b'authored test target, not measurement')], 'semantics':'reference'})
    return bus.snapshot()


@pytest.fixture
def fixture(tmp_path):
    directory = create_link(tmp_path)
    request = dict(channel_id='CH0',atol=.05,rtol=0.,purpose='authored synthetic numerical test only')
    return directory, reference_for(directory), request


@pytest.fixture
def retained(fixture,tmp_path):
    source,ref,request = fixture
    dest = tmp_path / 'review'
    return dest,d.run(source,ref,request,dest)


def test_session_comparison_and_exact_parent_occurrences(fixture,tmp_path):
    original,ref,request = fixture
    before=hashes(original)
    summary=d.run(original,ref,request,tmp_path/'review')
    assert summary['data']['comparison']['outcome']['status']=='PASS'
    assert summary['authority']==d.AUTHORITY
    assert summary['data']['coverage']['clock_relation_to_host']=='UNKNOWN'
    assert all(v['status']=='MISSING' for v in summary['data']['coverage']['calibrations'])
    assert summary['data']['source_evidence_id']==lw.inspect(original)['evidence_id']
    assert summary['data']['source_execution_id']==lw.inspect(original)['execution_id']
    assert hashes(original)==before
    for file in ('capture.json','workspace.json'):
        assert (tmp_path/'review/link'/file).read_bytes()==(original/file).read_bytes()


def test_replay_same_inputs_new_occurrences(retained,tmp_path):
    path,original=retained;before=hashes(path)
    replay=d.replay(path,tmp_path/'replay')
    assert replay['review_evidence_id']==original['review_evidence_id']
    assert replay['input_bytes']==original['input_bytes']
    assert replay['execution_id']!=original['execution_id'] and replay['result_id']!=original['result_id']
    assert replay['data']==original['data']
    assert hashes(path)==before


def test_offline_inspection_calls_no_comparison_executor_or_network(retained):
    path,summary=retained;before=hashes(path)
    with patch.object(d,'_execute',side_effect=AssertionError('execute')), \
         patch.object(checks,'compare',side_effect=AssertionError('compare')), \
         patch('socket.socket',side_effect=AssertionError('network')), \
         patch('subprocess.Popen',side_effect=AssertionError('provider')):
        assert d.inspect(path)==summary
        host=cli.ReviewHost(path)
        assert host.call('net_device_review',{})['outcome']['status']=='PASS'
    assert hashes(path)==before


@pytest.mark.parametrize('field,value', [('unit','m'),('frame','other'),('quantity','other')])
def test_reference_mismatch_is_retained_indeterminate(fixture,tmp_path,field,value):
    path,ref,request=fixture
    for row in ref['observations']:
        row[field]=value;seal(row)
    seal(ref)
    result=d.run(path,ref,request,tmp_path/'review')
    assert result['status']=='RETAINED'
    assert result['data']['comparison']['outcome']['status']=='INDETERMINATE'


@pytest.mark.parametrize('mode',['clock','entity','model','grid','empty','missing'])
def test_incompatible_or_missing_reference_never_interpolates(fixture,tmp_path,mode):
    path,ref,request=fixture
    for row in ref['observations']:
        if mode=='clock':row['clock']['id']='unrelated-clock'
        if mode=='entity':row['identity']['entity_id']='unrelated-entity'
        if mode=='model':row['identity']['model_id']='unrelated-model'
        if mode=='grid':row['clock']['time_s']+=1
        if mode=='missing':row['value']=None
        seal(row)
    if mode=='empty':ref['observations']=[]
    seal(ref)
    result=d.run(path,ref,request,tmp_path/'review')
    assert result['data']['comparison']['outcome']['status']=='INDETERMINATE'


@pytest.mark.parametrize('target',[24.,25.5,100.])
def test_numerical_failure_retained_without_authority(fixture,tmp_path,target):
    path,_,request=fixture
    result=d.run(path,reference_for(path,target),request,tmp_path/'review')
    assert result['status']=='RETAINED'
    assert result['data']['comparison']['outcome']['status']=='FAIL'
    assert result['data']['authority']['verification_id'] is None
    assert not result['authority']['equipment_control']


@pytest.mark.parametrize('key,value',[('atol',True),('rtol',False),('atol',-1),('rtol',-1),('atol','1'),('purpose',''),('channel_id','unknown')])
def test_invalid_request_refuses_before_destination(fixture,tmp_path,key,value):
    path,ref,request=fixture;request[key]=value
    with pytest.raises(ValueError):d.run(path,ref,request,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_unknown_reference_fields_and_semantics_refuse(fixture,tmp_path):
    path,ref,request=fixture
    changed=deepcopy(ref)
    for row in changed['observations']:
        row['provenance']['semantics']='observed';seal(row)
    seal(changed)
    with pytest.raises(ValueError,match='reference semantics'):d.run(path,changed,request,tmp_path/'bad')
    changed=deepcopy(ref);changed['surprise']=True;seal(changed)
    with pytest.raises(ValueError):d.run(path,changed,request,tmp_path/'bad')


def test_original_reference_and_policy_whitespace_preserved(fixture,tmp_path):
    path,ref,request=fixture
    rr=json.dumps(ref,indent=4).encode()+b'\r\n'
    pr=json.dumps(request,separators=(', ', ': ')).encode()+b' \n'
    summary=d.run(path,ref,request,tmp_path/'review',reference_raw=rr,request_raw=pr)
    assert (tmp_path/'review/reference.json').read_bytes()==rr
    assert summary['input_bytes']['reference']==bytes_ref(rr)
    assert (tmp_path/'review/request.json').read_bytes()==pr


@pytest.mark.parametrize('file',['reference.json','request.json','link/capture.json','link/workspace.json'])
def test_even_semantically_equal_changed_bytes_refuse(retained,file):
    path,_=retained
    p=path/file;p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):d.inspect(path)


@pytest.mark.parametrize('kind',['outcome','left','policy','authority','source-result','coverage'])
def test_resealed_result_forgery_refuses(retained,kind):
    path,_=retained;p=path/'workspace.json';w=json.loads(p.read_text());r=w['results'][0];data=r['data']
    if kind=='outcome':data['comparison']['outcome']['status']='FAIL';seal(data['comparison'])
    if kind=='left':data['comparison']['left'][0]['value']=0.;seal(data['comparison']['left'][0]);seal(data['comparison'])
    if kind=='policy':data['comparison']['policy']['atol']=True;seal(data['comparison'])
    if kind=='authority':data['authority']['equipment_control']=True
    if kind=='source-result':data['source_result_id']='result-'+'0'*32
    if kind=='coverage':data['coverage']['clock_relation_to_host']='SYNCHRONIZED'
    seal(r);p.write_text(json.dumps(w))
    with pytest.raises(ValueError):d.inspect(path)


def test_resealed_parent_copy_mismatch_refuses(retained):
    path,_=retained;p=path/'workspace.json';w=json.loads(p.read_text())
    source=w['run'];source['metadata']['device_review']['parent']['result']['data']['source_authentication']='verified'
    seal(source['metadata']['device_review']['parent']['result'])
    from ciw.core.identities import evidence_id
    source['evidence_id']=evidence_id(source)
    p.write_text(json.dumps(w))
    with pytest.raises(ValueError):d.inspect(path)


def test_execution_failure_is_retained_not_fabricated_success(fixture,tmp_path):
    path,ref,request=fixture
    with patch.object(checks,'compare',side_effect=RuntimeError('backend unavailable')):
        result=d.run(path,ref,request,tmp_path/'review')
    assert result['status']=='REFUSED' and result['result_id'] is None
    assert result['execution_id'].startswith('execution-')
    assert d.inspect(tmp_path/'review')['status']=='REFUSED'


@pytest.mark.parametrize('action',['run','replay'])
def test_output_create_only(retained,fixture,action):
    path,_=retained;before=hashes(path);source,ref,request=fixture
    with pytest.raises(FileExistsError):
        (d.run(source,ref,request,path) if action=='run' else d.replay(path,path))
    assert hashes(path)==before


def test_changed_input_during_replay_refuses_before_write(retained,tmp_path):
    path,_=retained;original=d.run
    def changing(*args,**kwargs):
        raw=(path/'link/workspace.json').read_bytes();(path/'link/workspace.json').write_bytes(raw+b' ')
        return original(*args,**kwargs)
    with patch.object(d,'run',side_effect=changing),pytest.raises(ValueError,match='changed during'):
        d.replay(path,tmp_path/'replay')
    assert not (tmp_path/'replay').exists()


def test_unknown_ticks_retained_as_withheld(tmp_path):
    path=create_link(tmp_path,transform=lambda v:v.update(device_tick=None))
    ref=ObservationBus().snapshot();request=dict(channel_id='CH0',atol=.1,rtol=0.,purpose='unknown clock')
    result=d.run(path,ref,request,tmp_path/'review')
    assert result['data']['comparison']['outcome']['status']=='INDETERMINATE'
    assert len(result['data']['coverage']['withheld'])==3
    assert not result['data']['comparison']['left']


def test_thermal_channel_selection_does_not_mix_humidity(tmp_path):
    path=create_link(tmp_path,persona='ThermalLink')
    request=dict(channel_id='CH0',atol=.2,rtol=0.,purpose='synthetic AgroStore-like channel, not food acceptance')
    result=d.run(path,reference_for(path,296.15),request,tmp_path/'review')
    assert len(result['data']['comparison']['left'])==3
    assert all(v['quantity']=='temperature' and v['unit']=='K' for v in result['data']['comparison']['left'])
    assert result['data']['coverage']['source_origin']==['synthetic']
    workspace=json.loads((tmp_path/'review/workspace.json').read_text())
    parent=workspace['run']['metadata']['device_review']['parent']
    assert len(parent['result']['data']['observations'])==6


def test_advertising_does_not_bind_or_authorize():
    unbound=d.capability_registry();bound=d.capability_registry(bind=True)
    assert unbound.catalog()['authorizes_execution'] is False
    assert unbound.catalog()['operations'][d.OPERATION]['bound'] is False
    assert bound.catalog()['operations'][d.OPERATION]['bound'] is True


def rpc(server,method,params,request_id=1):
    return server.handle(json.dumps(dict(jsonrpc='2.0',id=request_id,method=method,params=params)).encode())


def initialize(server):
    reply=rpc(server,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
    assert reply['result']['protocolVersion']=='2025-11-25'
    server.handle(b'{"jsonrpc":"2.0","method":"notifications/initialized"}')


def test_actual_existing_mcp_server_readonly_protocol(retained):
    path,summary=retained;before=hashes(path)
    host=cli.ReviewHost(path);server=Server(host);initialize(server)
    tools=rpc(server,'tools/list',{},2)['result']['tools']
    assert len(tools)==3 and all(t['annotations']['readOnlyHint'] for t in tools)
    for i,tool in enumerate(tools,3):
        result=rpc(server,'tools/call',{'name':tool['name'],'arguments':{}},i)['result']
        assert not result['isError']
        assert result['structuredContent']['snapshot_ref']==summary['snapshot_ref']
    refused=rpc(server,'tools/call',{'name':'net_execute','arguments':{}},6)['result']
    assert refused['isError']
    assert hashes(path)==before


@pytest.mark.parametrize('tool',['net_execute','net_replay','device.observe','CONFIGURE','CALIBRATE','UPDATE','CONTROL'])
def test_host_rejects_all_mutations(retained,tool):
    path,_=retained;host=cli.ReviewHost(path)
    with pytest.raises(ValueError,match='Read-only'):host.call(tool,{})


@pytest.mark.parametrize('arguments',[{'path':'/etc/passwd'},{'limit':100},{'limit':True},{'offset':False},{'offset':-1},{'offset':257},{'limit':0}])
def test_readonly_host_rejects_path_and_budget_injection(retained,arguments):
    path,_=retained;host=cli.ReviewHost(path)
    with pytest.raises(ValueError):host.call('net_device_observations',arguments)


def test_mcp_responses_detached_and_pagination(retained):
    path,_=retained;host=cli.ReviewHost(path)
    one=host.call('net_device_observations',{'offset':0,'limit':1})
    assert len(one['items'])==1 and one['next_offset']==1
    one['items'][0]['value']=999
    assert host.call('net_device_observations',{})['items'][0]['value']!=999
    summary=host.call('net_device_review',{});summary['authority']['equipment_control']=True
    assert host.call('net_device_review',{})['authority']['equipment_control'] is False


def test_stdio_transport_has_no_extra_output(retained):
    path,_=retained
    msgs=[dict(jsonrpc='2.0',id=1,method='initialize',params={'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}),
          dict(jsonrpc='2.0',method='notifications/initialized'),
          dict(jsonrpc='2.0',id=2,method='tools/call',params={'name':'net_device_review','arguments':{}})]
    source=io.BytesIO(b'\n'.join(json.dumps(v).encode() for v in msgs)+b'\n');out=io.BytesIO()
    assert serve(cli.ReviewHost(path),source,out)==0
    lines=out.getvalue().splitlines();assert len(lines)==2
    assert json.loads(lines[1])['result']['structuredContent']['outcome']['status']=='PASS'


def test_cli_inspect_and_mcp_in_actual_subprocess(retained):
    path,_=retained
    cmd=[sys.executable,'-m','ciw.device_review_cli']
    p=subprocess.run(cmd+['inspect',str(path)],capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)['status']=='RETAINED'
    initialization={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'subprocess','version':'1'}}}
    lines=[initialization,{'jsonrpc':'2.0','method':'notifications/initialized'},
           {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_device_review','arguments':{}}}]
    p=subprocess.run(cmd+['mcp',str(path)],input='\n'.join(json.dumps(v) for v in lines)+'\n',capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    assert len(p.stdout.splitlines())==2
    assert json.loads(p.stdout.splitlines()[1])['result']['structuredContent']['outcome']['status']=='PASS'


def test_full_joint_covariance_retained_without_confidence_claim(tmp_path):
    from ciw.core.covariance import create_covariance_artifact
    device=example_manifest();rows=example_observations(device)
    # Use the existing covariance constructor and its original binding fields.
    covariance=create_covariance_artifact(quantity_ids=[v['measurement_id'] for v in rows], units=['mm']*3,
        frame=device['channels'][0]['frame'],reference_values=[v['value'] for v in rows],
        matrix=[[4.,1.,1.],[1.,4.,1.],[1.,1.,4.]], method='authored-correlated-test',
        basis={'kind':'observation','id':bytes_ref(b'constructed covariance, not calibrated data')},
        provenance={'provider':'test','source_evidence_ids':[bytes_ref(b'constructed covariance, not calibrated data')],
                    'source_covariance_ids':[]}, assumptions=['Constructed test covariance only'])
    path=create_link(tmp_path,covariance=covariance)
    result=d.run(path,reference_for(path),dict(channel_id='CH0',atol=.05,rtol=0.,purpose='synthetic'),tmp_path/'review')
    workspace=json.loads((tmp_path/'review/workspace.json').read_text())
    retained_cov=workspace['run']['metadata']['device_review']['parent']['result']['data']['projection']['joint_covariance']
    assert retained_cov==covariance
    assert result['data']['coverage']['joint_covariance_ref']==covariance['covariance_id']
    assert result['authority']['statistical_confidence']=='not_assessed'


@pytest.mark.parametrize('field',['atol','rtol'])
def test_finite_policy_required(fixture,tmp_path,field):
    path,ref,request=fixture
    request[field]=float('inf')
    with pytest.raises(ValueError):d.run(path,ref,request,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_reference_budget_checked_before_execution(fixture,tmp_path):
    path,ref,request=fixture
    ref['observations']=[deepcopy(ref['observations'][0]) for _ in range(257)];seal(ref)
    with patch.object(checks,'compare',side_effect=AssertionError('compute')),pytest.raises(ValueError,match='budget'):
        d.run(path,ref,request,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_raw_reference_mismatch_and_duplicate_keys_refuse(fixture,tmp_path):
    path,ref,request=fixture
    with pytest.raises(ValueError):d.run(path,ref,request,tmp_path/'bad',reference_raw=b'{}')
    with pytest.raises(ValueError):d.run(path,ref,request,tmp_path/'bad',request_raw=b'{"atol":0,"atol":1}')
    assert not (tmp_path/'bad').exists()


def test_symlink_inputs_and_outputs_refused(retained,tmp_path):
    path,_=retained;alias=tmp_path/'alias'
    try:alias.symlink_to(path,target_is_directory=True)
    except OSError as exc:pytest.skip('platform denies symlink creation: '+str(exc))
    with pytest.raises(ValueError,match='symlink'):d.inspect(alias)
    with pytest.raises(ValueError,match='symlink'):d.replay(path,alias/'new')
    assert not (path/'new').exists()


def test_invalid_selection_is_a_retained_refusal(fixture,tmp_path):
    directory,reference,request=fixture
    parent,raw,wr=d._link_snapshot(directory)
    source=d.make_source(parent,reference,request,dict(capture=bytes_ref(raw),link_workspace=bytes_ref(wr),
        reference=bytes_ref(d._raw(reference)),request=bytes_ref(d._raw(request))))
    registry=d.capability_registry(bind=True)
    session=Session(source,tmp_path/'work',operations=registry.operations)
    response=session.handle({'protocol_version':1,'request_id':'wrong-select','type':'operation.execute',
        'payload':{'operation_id':d.OPERATION,'parameters':{'channel':d.CHANNEL,'interval_s':[0.,.5]}}})
    assert response['payload']['status']=='refused'
    assert not session.results


def test_direct_workspace_reopen_never_executes_provider(retained,tmp_path):
    path,_=retained;before=hashes(path)
    with patch.object(d,'_execute',side_effect=AssertionError('provider')),patch.object(checks,'compare',side_effect=AssertionError('compare')):
        session=Session.from_workspace(path/'workspace.json',tmp_path/'reopened')
    assert len(session.results)==1 and len(session.executions)==1
    assert hashes(path)==before


def test_readonly_snapshot_does_not_follow_later_file_replacement(retained):
    path,_=retained;host=cli.ReviewHost(path)
    baseline=host.call('net_device_review',{})
    (path/'workspace.json').write_text('{}')
    assert host.call('net_device_review',{})==baseline
    with pytest.raises(ValueError):cli.ReviewHost(path)


def test_native_export_does_not_change_source_identity(retained,tmp_path):
    path,_=retained;original=lw.inspect(path/'link')
    lw.export_native(path/'link',tmp_path/'native')
    mapping=json.loads((tmp_path/'native/mapping.json').read_text())
    native=json.loads((tmp_path/'native/observations.json').read_text())
    assert mapping['source_evidence_id']==original['evidence_id']
    assert native==original['data']['projection']['native_stream']


@pytest.mark.parametrize('value',[{'provider':'fake'}, {'bad':'scope'}, None])
def test_coherently_resealed_runtime_forgery_refuses(retained,value):
    path,_=retained;p=path/'workspace.json';w=json.loads(p.read_text())
    for v in (w['results'][0],w['executions'][0]):
        v['runtime']=value;seal(v)
    p.write_text(json.dumps(w))
    with pytest.raises((ValueError,TypeError)):d.inspect(path)


def test_mcp_notification_cannot_execute_tool(retained):
    path,_=retained;host=cli.ReviewHost(path);server=Server(host);initialize(server)
    with patch.object(host,'call',side_effect=AssertionError('notification must not dispatch')):
        assert server.handle(b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"net_execute","arguments":{}}}') is None
