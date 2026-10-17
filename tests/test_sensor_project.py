"""Synthetic-source integration tests; no native EDA/GUI/physical qualification."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from ciw import sensor_project as p, sensor_project_mems as m, sensor_project_cli as cli, sensor_project_kicad as k
from ciw.control_contracts import bytes_ref
from ciw.control_plane import ObservationBus
from ciw.operations.runner import seal
from ciw.agent_mcp import Server


def profile():
    return {'schema':'ciw.mems-csv-profile.v1','studio_version':'2.0.0-fixture','configuration_ref':bytes_ref(b'{}\n'),
            'origin':'synthetic','delimiter':',','header':['Time','ax','temp','data_changed'],'time_column':'Time',
            'time_unit':'ms','clock_id':'synthetic-sensor-clock','time_basis':'export_timestamp','segments':[0],
            'freshness':{'kind':'datalog2-column-mask','column':'data_changed','base':10,'data_columns':['ax','temp']},
            'channels':[{'column':'ax','bit':0,'entity_id':'sensor-1:accel','model_id':'declared-export','quantity':'acceleration.x','unit':'mg','frame':'board'},
                        {'column':'temp','bit':1,'entity_id':'sensor-1:temp','model_id':'declared-export','quantity':'temperature','unit':'degC','frame':'board'}],
            'ignored_columns':[]}


RAW=b'Time,ax,temp,data_changed\r\n0,1,20,3\r\n1,1,20,1\r\n2,2,21,2\r\n3,2,21,1\r\n'


def make_inputs(tmp_path):
    root=tmp_path/'inputs';root.mkdir()
    files={'board.kicad_pro':b'{"fixture":"synthetic NOT a qualified board"}\n',
           'board.kicad_sch':b'(kicad_sch (synthetic_test_only))\n',
           'board.kicad_pcb':b'(kicad_pcb (synthetic_test_only))\n',
           'board.step':b'ISO-10303-21;\n/* synthetic test bytes, not CAD */\n',
           'assembly.blend':b'BLENDER-fixture-not-native-tested',
           'assembly.glb':b'glTF-fixture-not-native-tested',
           'sensor.json':b'{}\n','mems.csv':RAW}
    native=ObservationBus()
    native.observe(identity={'model_id':'link-fixture','entity_id':'sensor-2','execution_id':None},clock={'id':'separate-clock','time_s':4.0},
        frame='mount',quantity='length',value=24.982,unit='mm',uncertainty=None,
        provenance={'provider':'Link-export-fixture','sources':[bytes_ref(b'authored Link fixture')],'semantics':'simulated'})
    files['link.json']=(json.dumps(native.snapshot())+'\n').encode()
    roles=['electronics.project','electronics.schematic','electronics.pcb','geometry.step','asset.blend','asset.glb','sensor.configuration','sensor.mems-csv','observations.native']
    parents={'board.step':['board.kicad_pcb'],'assembly.blend':['board.step'],'assembly.glb':['assembly.blend']}
    for name,raw in files.items():(root/name).write_bytes(raw)
    spec={'schema':'ciw.sensor-project-request.v1','project_id':'SYNTHETIC-SENSOR','hardware_revision':'fixture-not-manufactured',
          'mode':'REPLAY','baseline_ref':None,
          'artifacts':[{'path':name,'kind':role,'parents':parents.get(name,[]),'tool':{'name':'synthetic-fixture','version':'1'},'origin':'synthetic'} for name,role in zip(files,roles)],
          'mems':[{'path':'mems.csv','configuration_path':'sensor.json','profile':profile()}],
          'native_streams':['link.json'],
          'bindings':[{'asset_path':'assembly.glb','node':'Machine/Sensor','entity_id':'sensor-1',
                       'channels':[{'model_id':'declared-export','entity_id':'sensor-1:accel','quantity':'acceleration.x'},
                                   {'model_id':'declared-export','entity_id':'sensor-1:temp','quantity':'temperature'}],
                       'design_paths':['board.kicad_pcb'],'source_frame':'board-mm','asset_frame':'gltf-y-up-m',
                       'metres_per_source_unit':0.001,'transform':[[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]}]}
    return root,spec


@pytest.fixture
def inputs(tmp_path):return make_inputs(tmp_path)


@pytest.fixture
def retained(inputs,tmp_path):
    root,spec=inputs;dest=tmp_path/'retained'
    return dest,p.execute(p.source(root,spec),dest)


def tree(path):return {str(q.relative_to(path)):bytes_ref(q.read_bytes()) for q in path.rglob('*') if q.is_file()}


def test_freshness_preserves_equal_new_values_and_excludes_held_rows():
    report=m.project(RAW,profile());rows=report['native_stream']['observations']
    assert [v['value'] for v in rows if v['quantity']=='acceleration.x']==[1.,1.,2.]
    assert [v['value'] for v in rows if v['quantity']=='temperature']==[20.,21.]
    assert len(rows)==5 and len(report['mapping'])==8
    assert report['channel_counts']['temp']=={'new':2,'held':2,'missing':0}
    assert all(v['uncertainty'] is None for v in rows)
    assert all(v['provenance']['semantics']=='simulated' for v in rows)
    for item in report['mapping']:
        start,end=item['byte_span'];assert RAW[start:end].endswith(b'\r\n')
    assert [v['clock']['time_s'] for v in rows if v['quantity']=='acceleration.x']==[0.,.001,.003]


@pytest.mark.parametrize('unit,scale',[('s',1),('ms',.001),('us',.000001)])
def test_explicit_time_scale(unit,scale):
    config=profile();config['time_unit']=unit
    rows=m.project(RAW,config)['native_stream']['observations']
    assert rows[-1]['clock']['time_s']==float(Fraction(3)*m.TIME_SCALES[unit])


@pytest.mark.parametrize('token',['NaN','inf','-inf','=1+2','1e1000000','1e-99999','1e-400','true','0x12'])
def test_invalid_or_unrepresentable_measurements_refuse(token):
    raw=RAW.replace(b'0,1,20,3',('0,'+token+',20,3').encode())
    with pytest.raises((ValueError,OverflowError)):m.project(raw,profile())


@pytest.mark.parametrize('token',['-1','4','true','','1.0','1e2','0x3'])
def test_invalid_mask_refuses(token):
    raw=RAW.replace(b'0,1,20,3',('0,1,20,'+token).encode())
    with pytest.raises(ValueError):m.project(raw,profile())


def test_missing_fresh_value_is_not_zero():
    out=m.project(RAW.replace(b'0,1,20,3',b'0,,20,3'),profile())
    assert len(out['native_stream']['observations'])==4
    assert len(out['withheld'])==1 and out['channel_counts']['ax']['missing']==1


def test_reset_requires_separate_explicit_clock_segment():
    raw=RAW.replace(b'2,2,21,2',b'0,2,21,2').replace(b'3,2,21,1',b'1,2,21,1')
    with pytest.raises(ValueError,match='segment'):m.project(raw,profile())
    pr=profile();pr['segments']=[0,2]
    out=m.project(raw,pr)
    assert {v['clock']['id'] for v in out['native_stream']['observations']}=={'synthetic-sensor-clock:segment:0','synthetic-sensor-clock:segment:1'}


def test_duplicate_fresh_time_same_channel_refused():
    with pytest.raises(Exception):m.project(RAW.replace(b'1,1,20,1',b'0,1,20,1'),profile())


def test_same_row_time_different_channels_allowed():
    out=m.project(RAW.replace(b'2,2,21,2',b'1,2,21,2'),profile())
    assert len(out['native_stream']['observations'])==5


@pytest.mark.parametrize('change',[
    lambda p:p.update(delimiter='|'),lambda p:p.update(time_unit='ticks'),lambda p:p.update(origin='verified'),
    lambda p:p.update(header=['Time','ax','ax','data_changed']),lambda p:p['channels'][0].update(bit=True),
    lambda p:p['channels'][0].update(bit=1),lambda p:p.update(segments=[False]),lambda p:p.update(segments=[0,0]),
    lambda p:p.update(segments=[0,20]),lambda p:p.update(ignored_columns=['temp']),
    lambda p:p['freshness'].update(base=True),lambda p:p['freshness'].update(kind='auto'),
])
def test_profile_refusals(change):
    pr=profile();change(pr)
    with pytest.raises((ValueError,TypeError)):m.project(RAW,pr)


def test_explicit_sensor_bits_are_distinct_from_datalog_column_bits():
    pr=profile();pr['freshness'].update(kind='explicit-sensor-mask',base=16)
    pr['channels'][0]['bit']=3;pr['channels'][1]['bit']=0
    raw=RAW.replace(b',3\r\n',b',0x9\r\n').replace(b',1\r\n',b',0x8\r\n').replace(b',2\r\n',b',0x1\r\n')
    assert len(m.project(raw,pr)['native_stream']['observations'])==5


@pytest.mark.parametrize('raw',[b'',b'Time,ax,temp,data_changed\n',RAW+b'4,1\n',RAW.replace(b'Time,',b'time,'),RAW+b'\x00',RAW.replace(b'0,1,',b'0,"1\n1",')])
def test_bad_csv_structure(raw):
    with pytest.raises(Exception):m.project(raw,profile())


def test_explicit_ignored_column_is_retained_but_not_projected():
    pr=profile();pr['channels']=pr['channels'][:1];pr['ignored_columns']=['temp']
    assert len(m.project(RAW,pr)['native_stream']['observations'])==3


def test_session_native_and_mems_design_asset_binding(retained,inputs):
    directory,summary=retained;root,spec=inputs
    assert summary['status']=='RETAINED',summary
    assert len(summary['data']['observations'])==6
    assert len(summary['data']['bindings'][0]['observation_refs'])==5
    assert summary['data']['bindings'][0]['design_refs']==[bytes_ref((root/'board.kicad_pcb').read_bytes())]
    assert summary['claims']['equipment_control'] is False
    assert summary['data']['native_execution']['kicad']=='not_executed_by_retention'
    sess,_=p.load(directory)
    _,inv,raw=p.context(sess.run)
    assert all(raw[n]==(root/n).read_bytes() for n in inv['files'])


def test_exact_request_format_and_bytes_retained(inputs):
    root,spec=inputs;raw=json.dumps(spec,indent=4).encode()+b'\r\n'
    src=p.source(root,spec,raw)
    assert p._unpack(src['metadata']['sensor_project']['spec_bytes'])==raw
    with pytest.raises(ValueError):p.source(root,spec,b'{}')


def test_replay_new_execution_same_evidence_and_data(retained,tmp_path):
    directory,summary=retained;before=tree(directory)
    after=p.replay(directory,tmp_path/'replay',summary['snapshot_ref'])
    assert after['evidence_id']==summary['evidence_id']
    assert after['execution_id']!=summary['execution_id'] and after['result_id']!=summary['result_id']
    assert after['data']==summary['data'] and tree(directory)==before


def test_offline_reopen_no_network_or_native_engine(retained):
    directory,summary=retained;before=tree(directory)
    with patch('subprocess.Popen',side_effect=AssertionError('native')),patch('socket.socket',side_effect=AssertionError('network')):
        assert p.inspect(directory)==summary
        h=cli.Host(directory);assert h.call('net_sensor_project',{})['status']=='RETAINED'
    assert tree(directory)==before


def test_wrong_anchor_refuses_before_restore(retained):
    directory,_=retained
    with patch.object(p.Session,'from_workspace',side_effect=AssertionError('restore')),pytest.raises(ValueError,match='anchor'):
        p.inspect(directory,'sha256:'+'0'*64)


def test_native_export_exact_roundtrip(retained,inputs,tmp_path):
    directory,_=retained;root,_=inputs
    out=tmp_path/'export'
    cli.export_inputs(directory,out)
    assert p.packets.inventory(out)==p.packets.inventory(root)


@pytest.mark.parametrize('field,value',[('mode','LIVE'),('mode','live'),('hardware_revision',''),('baseline_ref','sha256:'+'0'*64)])
def test_invalid_project_metadata(inputs,field,value):
    root,spec=inputs;spec[field]=value
    with pytest.raises(ValueError):p.source(root,spec)


@pytest.mark.parametrize('case',['unlisted','duplicate','missing-parent','cycle','extension','bad-role','wrong-config','duplicate-csv','bad-node','singular','scale','missing-native','bad-design'])
def test_project_contract_failures(inputs,case):
    root,spec=inputs
    if case=='unlisted':(root/'secret.txt').write_text('unlisted')
    if case=='duplicate':spec['artifacts'].append(deepcopy(spec['artifacts'][0]))
    if case=='missing-parent':spec['artifacts'][0]['parents']=['absent']
    if case=='cycle':spec['artifacts'][0]['parents']=['board.kicad_pro']
    if case=='extension':spec['artifacts'][0]['kind']='asset.blend'
    if case=='bad-role':spec['artifacts'][0]['kind']='semiconductor.layout'
    if case=='wrong-config':spec['mems'][0]['profile']['configuration_ref']='sha256:'+'0'*64
    if case=='duplicate-csv':spec['mems'].append(deepcopy(spec['mems'][0]))
    if case=='bad-node':spec['bindings'][0]['node']='../other'
    if case=='singular':spec['bindings'][0]['transform'][0]=[0,0,0,0]
    if case=='scale':spec['bindings'][0]['metres_per_source_unit']=True
    if case=='missing-native':spec['native_streams']=['missing.json']
    if case=='bad-design':spec['bindings'][0]['design_paths']=['assembly.blend']
    with pytest.raises((ValueError,TypeError)):p.source(root,spec)


def test_counterfactual_requires_retained_baseline(inputs,tmp_path):
    root,spec=inputs;spec['mode']='COUNTERFACTUAL'
    with pytest.raises(ValueError):p.source(root,spec)
    spec['baseline_ref']=bytes_ref((root/'link.json').read_bytes())
    result=p.execute(p.source(root,spec),tmp_path/'cf')
    assert result['data']['baseline_ref']==spec['baseline_ref']
    assert result['data']['mode']=='COUNTERFACTUAL'


def test_observed_data_not_relabelled_simulation(inputs,tmp_path):
    root,spec=inputs;spec['mode']='SIMULATION';spec['mems'][0]['profile']['origin']='caller_declared_acquisition'
    with pytest.raises(ValueError):p.source(root,spec)
    spec['mems'][0]['profile']['origin']='synthetic'
    path=root/'link.json';native=json.loads(path.read_bytes());native['observations'][0]['provenance']['semantics']='observed';seal(native['observations'][0]);seal(native);path.write_text(json.dumps(native))
    result=p.execute(p.source(root,spec),tmp_path/'refused')
    assert result['status']=='REFUSED' and result['result_id'] is None


def test_missing_channel_not_invented(inputs,tmp_path):
    root,spec=inputs;spec['bindings'][0]['channels'].append({'model_id':'absent','entity_id':'missing','quantity':'pressure'})
    result=p.execute(p.source(root,spec),tmp_path/'missing')
    assert result['data']['bindings'][0]['missing_channels']==[{'model_id':'absent','entity_id':'missing','quantity':'pressure'}]


def test_selection_anchored_to_asset_and_node(retained):
    directory,summary=retained;b=summary['data']['bindings'][0]
    page=p.select(summary,asset_ref=b['asset_ref'],node=b['binding']['node'],limit=2)
    assert page['count']==5 and page['next_offset']==2 and page['mode']=='REPLAY'
    page['items'][0]['observation']['value']=999
    assert p.select(summary,asset_ref=b['asset_ref'],node=b['binding']['node'])['items'][0]['observation']['value']!=999
    with pytest.raises(ValueError):p.select(summary,asset_ref='sha256:'+'0'*64,node=b['binding']['node'])
    with pytest.raises(ValueError):p.select(summary,asset_ref=b['asset_ref'],node='Machine/Other')


@pytest.mark.parametrize('args',[{'offset':True},{'limit':False},{'limit':17},{'offset':-1}])
def test_selection_bounds(retained,args):
    _,summary=retained;b=summary['data']['bindings'][0]
    with pytest.raises(ValueError):p.select(summary,asset_ref=b['asset_ref'],node=b['binding']['node'],**args)


def test_mutation_and_path_injection_refused_in_existing_mcp(retained):
    directory,summary=retained;host=cli.Host(directory);server=Server(host)
    def call(i,method,params):return server.handle(json.dumps({'jsonrpc':'2.0','id':i,'method':method,'params':params}).encode())
    assert 'result' in call(1,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
    server.handle(b'{"jsonrpc":"2.0","method":"notifications/initialized"}')
    tools=call(2,'tools/list',{})['result']['tools'];assert len(tools)==2
    b=summary['data']['bindings'][0]
    ok=call(3,'tools/call',{'name':'net_sensor_pick','arguments':{'asset_ref':b['asset_ref'],'node':b['binding']['node']}})
    assert ok['result']['structuredContent']['count']==5
    for i,name in enumerate(['net_execute','machine_control','propose_edit','flash'],4):
        assert call(i,'tools/call',{'name':name,'arguments':{}})['result']['isError']
    with pytest.raises(ValueError):host.call('net_sensor_pick',{'asset_ref':b['asset_ref'],'node':b['binding']['node'],'path':'/etc/passwd'})


@pytest.mark.parametrize('change',[lambda d:d.update(mode='LIVE'),lambda d:d['claims'].update(equipment_control=True),lambda d:d['observations'][0]['observation'].update(value=100),lambda d:d['bindings'][0].update(asset_ref='sha256:'+'0'*64)])
def test_resealed_results_rejected(retained,change):
    directory,_=retained;path=directory/'workspace.json';w=json.loads(path.read_bytes());result=w['results'][0]
    change(result['data']);seal(result);path.write_text(json.dumps(w))
    with pytest.raises(ValueError):p.inspect(directory)


def test_fixed_scene_projection_and_exact_json_export(retained,tmp_path):
    directory,summary=retained;v=cli.view(directory)
    assert v['source_snapshot_ref']==summary['snapshot_ref'] and v['mode']=='REPLAY'
    assert v['claims']['geometry_equivalence']=='not_established'
    assert len(v['observations'])==6
    cli.save_new(tmp_path/'view.json',v)
    with pytest.raises(FileExistsError):cli.save_new(tmp_path/'view.json',v)


def test_output_creation_and_source_identity(inputs,retained,tmp_path):
    root,spec=inputs;directory,_=retained;before=tree(directory)
    with pytest.raises(FileExistsError):p.execute(p.source(root,spec),directory)
    assert tree(directory)==before
    src=p.source(root,spec);(root/'sensor.json').write_text('{"new":1}')
    with pytest.raises(ValueError):p.source(root,spec)
    assert p.context(src)[2]['sensor.json']==b'{}\n'


def test_kicad_plan_is_electronics_not_link(inputs):
    root,spec=inputs;plan=k.command_plan(p.source(root,spec))
    assert [v['stage'] for v in plan]==['erc','drc','bom','position','gerber','drill','step','glb']
    for stage in plan[:2]:assert '--severity-all' in stage['args'] and '--exit-code-violations' in stage['args']
    assert '--schematic-parity' in plan[1]['args']
    assert all('--save-board' not in v['args'] and '--force' not in v['args'] for v in plan)


@pytest.mark.parametrize('version',['nightly','10.99.0','9.0.6','10.0.6-rc1','10.0.6 --evil'])
def test_kicad_rejects_nonbaseline_versions(tmp_path,version):
    path=tmp_path/'exe';path.write_bytes(b'x')
    with pytest.raises(ValueError):k.Binding(path,expected_sha256=bytes_ref(b'x'),expected_version=version)


def test_missing_native_binary_has_no_fake_qualification(tmp_path):
    with pytest.raises(ValueError,match='installed'):k.Binding(tmp_path/'no-kicad',expected_sha256='sha256:'+'0'*64,expected_version='10.0.6')


def fake_cli(command,cwd,watched=(),timeout=60):
    """Declared process double, not KiCad. Exercise fixed mapping/custody only."""
    cwd=Path(cwd);(cwd/'stdout.log').write_text('');(cwd/'stderr.log').write_text('')
    if command[-1]=='version':(cwd/'stdout.log').write_text('10.0.6\n')
    else:
        output=Path(command[command.index('--output')+1]);output=cwd/output
        if '--format' in command and command[command.index('--format')+1]=='json':output.write_text('{"violations":[]}')
        elif output.is_dir():(output/'synthetic-output.txt').write_text('synthetic native-output double')
        else:output.write_bytes(b'synthetic native-output double')
    return {'returncode':0,'elapsed_wall_s':.01,'failure':None,'stderr_prefix':''}


def test_kicad_session_with_explicit_process_double(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'synthetic-binary';exe.write_bytes(b'not-KiCad')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'not-KiCad'),expected_version='10.0.6')
    src=p.source(root,spec)
    with patch.object(k,'run_process',side_effect=fake_cli):out=p.execute(src,tmp_path/'native-double',binding=binding,operation=k.OPERATION)
    assert out['status']=='RETAINED',out
    assert out['data']['manufacturing_approval'] is False
    assert len(out['data']['stages'])==8
    assert out['data']['runtime']['executable_sha256']==bytes_ref(b'not-KiCad')
    with patch.object(k,'run_process',side_effect=AssertionError('no native reopen')):
        assert p.inspect(tmp_path/'native-double')==out


def test_kicad_violation_code_is_retained_not_export_permission(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    def fail(command,cwd,watched=(),timeout=60):
        result=fake_cli(command,cwd,watched,timeout)
        if 'erc' in command:raise RuntimeError(json.dumps({**result,'failure':'runtime exit 5','returncode':5}))
        return result
    with patch.object(k,'run_process',side_effect=fail):out=p.execute(p.source(root,spec),tmp_path/'violations',binding=binding,operation=k.OPERATION)
    assert out['data']['stages'][0]['status']=='VIOLATIONS_REPORTED'
    assert out['data']['manufacturing_approval'] is False


def test_missing_exports_refuse_and_keep_original_source(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    def missing(command,cwd,watched=(),timeout=60):
        result=fake_cli(command,cwd,watched,timeout)
        if 'glb' in command:(Path(cwd)/'board.glb').unlink()
        return result
    with patch.object(k,'run_process',side_effect=missing):out=p.execute(p.source(root,spec),tmp_path/'missing',binding=binding,operation=k.OPERATION)
    assert out['status']=='REFUSED' and out['result_id'] is None
    assert p.load(tmp_path/'missing')[0].run['evidence_id']==out['evidence_id']


def test_binary_drift_refuses(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    exe.write_bytes(b'changed')
    with pytest.raises(ValueError,match='drift'):binding.runtime_identity()


def test_actual_cli_subprocess_and_stdout_mcp(retained,tmp_path):
    directory,summary=retained
    cmd=[sys.executable,'-m','ciw.sensor_project_cli']
    run=subprocess.run(cmd+['inspect',str(directory)],text=True,capture_output=True,timeout=20)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)['evidence_id']==summary['evidence_id']
    msgs=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
          {'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_sensor_project','arguments':{}}}]
    run=subprocess.run(cmd+['mcp',str(directory)],input='\n'.join(json.dumps(v) for v in msgs)+'\n',text=True,capture_output=True,timeout=20)
    assert run.returncode==0,run.stderr
    assert len(run.stdout.splitlines())==2 and json.loads(run.stdout.splitlines()[-1])['result']['isError'] is False


def test_native_replay_never_silently_runs_another_operation(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    with patch.object(k,'run_process',side_effect=fake_cli):
        p.execute(p.source(root,spec),tmp_path/'native',binding=binding,operation=k.OPERATION)
    with pytest.raises(ValueError,match='rebinding'):
        p.replay(tmp_path/'native',tmp_path/'replay')
    assert not (tmp_path/'replay').exists()


def test_visual_design_binding_requires_real_declared_ancestry(inputs):
    root,spec=inputs
    next(a for a in spec['artifacts'] if a['path']=='assembly.blend')['parents']=[]
    with pytest.raises(ValueError,match='descend'):p.source(root,spec)


def test_native_binding_advertisement_is_distinct(inputs,tmp_path):
    exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    r=p.registry(binding)
    assert r.contract(k.OPERATION)['runtime']==binding.runtime_identity()
    assert r.contract(p.OPERATION)['runtime']==p.runtime_identity()
    assert r.catalog()['authorizes_execution'] is False


def test_native_stage_added_input_refuses(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    def mutated(command,cwd,watched=(),timeout=60):
        out=fake_cli(command,cwd,watched,timeout)
        if 'erc' in command:(Path(command[-1]).parent/'untracked.kicad_dru').write_bytes(b'new rules')
        return out
    with patch.object(k,'run_process',side_effect=mutated):
        out=p.execute(p.source(root,spec),tmp_path/'bad',binding=binding,operation=k.OPERATION)
    assert out['status']=='REFUSED' and out['data'] is None
    assert not (root/'untracked.kicad_dru').exists()


@pytest.mark.parametrize('field', ['manufacturing_approval','dependency_closure','library_closure','report'])
def test_native_output_forgery_refused(inputs,tmp_path,field):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    with patch.object(k,'run_process',side_effect=fake_cli):
        p.execute(p.source(root,spec),tmp_path/'native',binding=binding,operation=k.OPERATION)
    file=tmp_path/'native/workspace.json';saved=json.loads(file.read_bytes());result=saved['results'][0];data=result['data']
    if field=='manufacturing_approval':data[field]=True
    elif field=='dependency_closure':data['runtime'][field]='attested'
    elif field=='library_closure':data[field]='qualified'
    else:
        raw=b'[]';data['artifacts']['erc/erc.json']={'sha256':bytes_ref(raw),'bytes':len(raw),'base64':p._pack(raw)}
    seal(result);file.write_text(json.dumps(saved))
    with pytest.raises(ValueError):p.inspect(tmp_path/'native')


def test_native_summary_does_not_dump_binary_payloads_to_mcp(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    with patch.object(k,'run_process',side_effect=fake_cli):
        out=p.execute(p.source(root,spec),tmp_path/'native',binding=binding,operation=k.OPERATION)
    response=cli.Host(tmp_path/'native').call('net_sensor_project',{})
    assert all('base64' not in a for a in response['data']['artifacts'].values())
    assert all('base64' in a for a in out['data']['artifacts'].values())
    with pytest.raises(ValueError,match='Map'):p.select(out,asset_ref=bytes_ref(b'fake'),node='Machine/Sensor')


def test_blender_annotation_recipe_is_bound_and_not_geometry_authority(retained):
    from ciw.sensor_project_blender import annotations
    directory,_=retained;v=cli.view(directory);raw=json.dumps(v).encode();a=v['bindings'][0]['asset_ref']
    properties=annotations(raw,a)
    assert properties['Machine/Sensor']['notations_mode']=='REPLAY'
    assert properties['Machine/Sensor']['notations_authority']=='representation_only'
    assert properties['Machine/Sensor']['notations_evidence_id']==v['evidence_id']


@pytest.mark.parametrize('mutation', ['live','authority','duplicate','path','wrongasset'])
def test_blender_annotation_recipe_refusals(retained,mutation):
    from ciw.sensor_project_blender import annotations
    directory,_=retained;v=cli.view(directory);a=v['bindings'][0]['asset_ref']
    if mutation=='live':v['mode']='LIVE'
    if mutation=='authority':v['claims']['equipment_control']=True
    if mutation=='duplicate':v['bindings'].append(deepcopy(v['bindings'][0]))
    if mutation=='path':v['bindings'][0]['binding']['node']='../escape'
    if mutation=='wrongasset':a='sha256:'+'0'*64
    with pytest.raises(ValueError):annotations(json.dumps(v).encode(),a)


def test_blender_operator_hash_reader(tmp_path):
    from ciw.sensor_project_blender import read_pinned
    f=tmp_path/'original.blend';f.write_bytes(b'not native Blender, hash test only')
    expected=bytes_ref(f.read_bytes());assert read_pinned(f,expected)==f.read_bytes()
    f.write_bytes(b'changed')
    with pytest.raises(ValueError):read_pinned(f,expected)


def test_export_kicad_candidate_is_byte_bound_and_never_approval(inputs,tmp_path):
    root,spec=inputs;exe=tmp_path/'double';exe.write_bytes(b'double')
    binding=k.Binding(exe,expected_sha256=bytes_ref(b'double'),expected_version='10.0.6')
    with patch.object(k,'run_process',side_effect=fake_cli):
        out=p.execute(p.source(root,spec),tmp_path/'native',binding=binding,operation=k.OPERATION)
    with patch.object(k,'run_process',side_effect=AssertionError('offline')):
        receipt=cli.export_kicad(tmp_path/'native',tmp_path/'manufacturing-candidate',out['snapshot_ref'])
    assert receipt['claims']['manufacturing_approval'] is False
    for path,item in out['data']['artifacts'].items():
        assert (tmp_path/'manufacturing-candidate'/path).read_bytes()==p._unpack(item['base64'])
    manifest=json.loads((tmp_path/'manufacturing-candidate/candidate-manifest.json').read_bytes())
    assert manifest['inventory_id']==out['data']['inventory_id']
    with pytest.raises(FileExistsError):cli.export_kicad(tmp_path/'native',tmp_path/'manufacturing-candidate')


def test_export_kicad_refuses_a_mapping_only_investigation(retained,tmp_path):
    directory,_=retained
    with pytest.raises(ValueError):cli.export_kicad(directory,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_dense_dag_ancestry_is_bounded_not_exponentially_reexpanded(inputs):
    root,spec=inputs
    paths=['board.kicad_pcb']
    for i in range(25):
        name=f'mapping-{i}.json';(root/name).write_bytes(b'{}')
        spec['artifacts'].append({'path':name,'kind':'transformation.record','parents':list(paths),
                                  'tool':{'name':'synthetic','version':'1'},'origin':'synthetic'})
        paths.append(name)
    next(a for a in spec['artifacts'] if a['path']=='assembly.blend')['parents']=[paths[-1]]
    assert p.source(root,spec)['evidence_id'].startswith('sha256:')
