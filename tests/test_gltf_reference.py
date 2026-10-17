"""Synthetic glTF fixtures only; native tests require explicit operator bindings."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import struct

import pytest

from ciw import gltf_reference as gltf, gltf_reference_runtime as bridge
from ciw.data_sources import retain_record
from ciw.instruments import make_demo_run
from ciw.interactive_simulation import file_sha
from ciw.operations.registry import default_registry
from ciw.session import Session
from ciw.telemetry import canonical

STAMP = "2026-10-05T22:00:00Z"
PROVENANCE = {"source": "independent synthetic two-seam-patch test", "revision": "1",
              "license": "test fixture", "attribution": "NET test authors"}


def fixture(index_type=5123):
    # Two disjoint index components share coordinates. Interleaved 32-byte
    # vertex records, nonzero bufferView/accessor offsets, and nested TRS.
    positions = [[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[1.,1.,0.]] * 2
    binary = bytearray(b'\0' * 16)
    for p in positions:
        binary.extend(struct.pack('<8f', 17., *p, 0., 0., 1., 99.))
    start = len(binary)
    binary.extend(b'\0' * 4)
    indices = [0,1,2,3,2,1,4,5,6,7,6,5]
    fmt = {5121:'B',5123:'H',5125:'I'}[index_type]
    binary.extend(struct.pack('<'+fmt*len(indices), *indices))
    doc = {'asset':{'version':'2.0'}, 'scene':0, 'scenes':[{'nodes':[0]}],
        'nodes':[{'translation':[2,3,4],'children':[1]}, {'mesh':0,'scale':[2,3,1]}],
        'meshes':[{'name':'SyntheticSeams','primitives':[{'attributes':{'POSITION':0,'NORMAL':1},'indices':2}]}],
        'buffers':[{'uri':'mesh.bin','byteLength':len(binary)}],
        'bufferViews':[{'buffer':0,'byteOffset':16,'byteLength':32*8,'byteStride':32},
                       {'buffer':0,'byteOffset':start,'byteLength':len(binary)-start}],
        'accessors':[{'bufferView':0,'byteOffset':4,'componentType':5126,'type':'VEC3','count':8,
                      'min':[0,0,0],'max':[1,1,0]},
                     {'bufferView':0,'byteOffset':16,'componentType':5126,'type':'VEC3','count':8},
                     {'bufferView':1,'byteOffset':4,'componentType':index_type,'type':'SCALAR','count':12}]}
    return doc, {'mesh.bin':bytes(binary)}


def record():
    doc, buffers = fixture()
    return gltf.make_record(canonical(doc), buffers, provenance=PROVENANCE)


def add(session, native=None, revision=1, parent=None):
    envelope = retain_record(record() if native is None else native, dataset_id='synthetic-seams',
        revision=revision, parent_source_id=parent, recorded_at=STAMP, data_family='gltf-reference')
    source = session.workbench.add_source({'kind':'data-record','label':'seam reference',
                                          'bytes_b64':base64.b64encode(canonical(envelope)).decode()})
    return source, envelope


def call(session, kind, payload):
    answer = session.handle({'protocol_version':1,'request_id':'gltf-test','type':kind,'payload':payload})
    assert answer['type'] == 'response', answer
    return answer['payload']


def execute(session, operation, params):
    return call(session, 'operation.execute', {'operation_id':operation,'parameters':params})


@pytest.mark.parametrize('index_type',[5121,5123,5125])
def test_accessor_offsets_stride_types_transforms_and_seams(index_type):
    doc,buffers = fixture(index_type)
    value = gltf.make_record(canonical(doc),buffers,provenance=PROVENANCE)
    geometry = value['decoded']
    assert geometry['world_positions'][:4] == [[2,3,4],[4,3,4],[2,6,4],[4,6,4]]
    assert geometry['positions'][:4] == geometry['positions'][4:]
    assert geometry['components'] == [[0,1,2,3],[4,5,6,7]]
    assert geometry['coincident_vertex_count'] == 4
    assert geometry['indices'] == [0,1,2,3,2,1,4,5,6,7,6,5]
    projected = gltf.mesh_projection(value,source_vertex=4,target_vertex=7)
    assert projected['local_to_source_vertices'] == [4,5,6,7]
    assert projected['source_triangle_indices'] == [2,3]
    assert projected['source']['request']['mesh']['triangles'] == [[0,1,2],[3,2,1]]
    with pytest.raises(ValueError,match='Cross-component'):
        gltf.mesh_projection(value,source_vertex=0,target_vertex=4)


def test_matrix_column_major_rotation_and_trs_refusal():
    doc,buffers = fixture()
    doc['nodes'][0] = {'children':[1], 'matrix':[0,1,0,0,-1,0,0,0,0,0,1,0,2,3,4,1]}
    decoded = gltf.decode(canonical(doc),buffers)
    assert decoded['world_positions'][:4] == [[2,3,4],[2,5,4],[-1,3,4],[-1,5,4]]
    doc['nodes'][0]['translation'] = [0,0,0]
    with pytest.raises(ValueError,match='coexist'):
        gltf.decode(canonical(doc),buffers)


@pytest.mark.parametrize('uri',['../x.bin','/tmp/x.bin','https://example.com/x.bin','file:///x.bin',
    'data:application/octet-stream;base64,AAAA','x%2ebin','x.bin?token=a','x\\a.bin','..\\x.bin','a/b.bin'])
def test_external_and_traversal_uris_refuse_before_local_read(tmp_path,uri):
    doc,buffers = fixture(); doc['buffers'][0]['uri'] = uri
    path=tmp_path/'bad.gltf';path.write_bytes(canonical(doc))
    with pytest.raises(ValueError,match='URI or traversal'):
        gltf.read_local(path,provenance=PROVENANCE)


@pytest.mark.parametrize('mutate',[
    lambda d:d.update(extensionsUsed=['KHR_draco_mesh_compression']),
    lambda d:d['meshes'][0]['primitives'][0].update(extensions={'unknown':{}}),
    lambda d:d['accessors'][0].update(sparse={}),
    lambda d:d['accessors'][0].update(byteOffset=999999),
    lambda d:d['bufferViews'][0].update(byteStride=8),
    lambda d:d['bufferViews'][0].update(byteOffset=999999),
    lambda d:d['accessors'][2].update(count=1000),
    lambda d:d['accessors'][2].update(componentType=5122),
    lambda d:d['accessors'][0].update(componentType=5123),
    lambda d:d['accessors'][0].update(normalized=True),
    lambda d:d['accessors'][0].update(max=[99,99,99]),
    lambda d:d['nodes'][1].update(children=[0]),
    lambda d:d['nodes'][1].update(skin=0),
    lambda d:d.update(images=[{'uri':'https://example.com/image.png'}]),
    lambda d:d['meshes'][0]['primitives'][0].update(mode=5),
    lambda d:d['nodes'][0].update(rotation=[0,0,0,2]),
    lambda d:d['nodes'][1].update(mesh=True),
])
def test_unsupported_and_out_of_bounds_refusals(mutate):
    doc,buffers = fixture();mutate(doc)
    with pytest.raises(ValueError):gltf.decode(canonical(doc),buffers)


def test_symlink_buffer_and_resealed_geometry_corruption_refused(tmp_path):
    doc,buffers=fixture()
    (tmp_path/'model.gltf').write_bytes(canonical(doc))
    (tmp_path/'target.bin').write_bytes(buffers['mesh.bin'])
    (tmp_path/'mesh.bin').symlink_to(tmp_path/'target.bin')
    with pytest.raises(ValueError,match='regular sibling'):gltf.read_local(tmp_path/'model.gltf',provenance=PROVENANCE)
    value=record();value['decoded']['world_positions'][0][0]=5
    with pytest.raises(ValueError,match='authoritative'):gltf.validate_record(value)


def test_session_projection_retention_reopen_and_source_dependency(tmp_path,monkeypatch):
    s=Session(make_demo_run(),tmp_path/'live');source,_=add(s)
    params=bridge.source_parameters(s.workbench,source['source_id'],source_vertex=0,target_vertex=3)
    answer=execute(s,bridge.PROJECT,params);assert answer['status']=='completed',answer
    mapped=bridge.retain_mesh_source(s.workbench,params,label='native component')
    assert s.workbench.get_source(mapped['source_id'])['input_source_ids']==[source['source_id']]
    saved=s.save_workspace(tmp_path/'workspace.json')
    monkeypatch.setattr(bridge.GodotRuntime,'execute',lambda *_:pytest.fail('offline import'))
    restored=Session.from_workspace(saved,output_dir=tmp_path/'reopen')
    assert restored.results==s.results
    assert restored.workbench.serialize()==s.workbench.serialize()
    replay=execute(restored,bridge.PROJECT,params)
    assert replay['result']['data']==answer['result']['data']
    assert replay['execution']['execution_id']!=answer['execution']['execution_id']
    # Removing source or changing mapping cannot be hidden by re-sealing output.
    corrupted=deepcopy(answer['result']['data']);corrupted['local_to_source_vertices'].reverse()
    with pytest.raises(ValueError):bridge.validate_payload(bridge.PROJECT,corrupted,{},params,{})
    other=Session(make_demo_run(),tmp_path/'empty')
    refused=execute(other,bridge.PROJECT,params);assert refused['status']=='refused'


def test_correction_marks_projection_and_derived_source_stale(tmp_path):
    s=Session(make_demo_run(),tmp_path/'live');old,envelope=add(s)
    params=bridge.source_parameters(s.workbench,old['source_id'],source_vertex=0,target_vertex=3)
    answer=execute(s,bridge.PROJECT,params)
    mapped=bridge.retain_mesh_source(s.workbench,params,label='old component')
    doc,buffers=fixture();doc['nodes'][0]['translation'][0]=10
    new_record=gltf.make_record(canonical(doc),buffers,provenance=PROVENANCE)
    new,_=add(s,new_record,revision=2,parent=envelope['source_id'])
    proposal=call(s,'correction.propose',{'old_source_id':old['source_id'],'new_source_id':new['source_id'],
        'kind':'source_data','reason':'declared source transform correction'})
    call(s,'correction.review',{'correction_id':proposal['correction_id'],'decision':'accept',
        'expected_revision':s.correction_journal.revision,'reviewer':'operator','reason':'checked'})
    status=s.dependency_status()['artifact_status']
    for identity in (old['source_id'],mapped['source_id'],answer['result']['result_id']):
        assert status[identity]['status']=='stale'
    fresh=execute(s,bridge.PROJECT,bridge.source_parameters(s.workbench,new['source_id'],source_vertex=0,target_vertex=3))
    assert fresh['result']['data']['source']['request']['mesh']['vertices'][0]==[10,3,4]
    assert status[answer['result']['result_id']]['status']=='stale'
    assert s.dependency_status()['artifact_status'][fresh['result']['result_id']]['status']=='current'



@pytest.mark.integration
def test_real_godot_session_reopen_and_fresh_replay(tmp_path,monkeypatch):
    executable=os.environ.get('CIW_GLTF_GODOT')
    if not executable:pytest.skip('Set CIW_GLTF_GODOT for explicit native import')
    binding=bridge.GodotRuntime(executable,expected_sha256=file_sha(executable))
    registry=default_registry();registry.register(binding.operation())
    s=Session(make_demo_run(),tmp_path/'live',operations=registry);source,_=add(s)
    params=bridge.source_parameters(s.workbench,source['source_id'])
    first=execute(s,bridge.INSPECT,params);assert first['status']=='completed',first
    assert first['result']['data']['comparison']['vertex_count']==8
    path=s.save_workspace(tmp_path/'godot.json')
    with monkeypatch.context() as patch:
        patch.setattr(bridge.GodotRuntime,'execute',lambda *_:pytest.fail('offline runtime'))
        restored=Session.from_workspace(path,output_dir=tmp_path/'offline')
        assert restored.results==s.results
        assert execute(restored,bridge.INSPECT,params)['status']=='refused'
    restored.operations=registry
    replay=execute(restored,bridge.INSPECT,params);assert replay['status']=='completed',replay
    assert replay['result']['data']==first['result']['data']
    assert replay['execution']['execution_id']!=first['execution']['execution_id']
    bad=deepcopy(first['result']['data']);next(iter(bad['observation']['parts'].values()))['surfaces'][0]['indices'][0]=7
    with pytest.raises(ValueError):bridge.validate_payload(bridge.INSPECT,bad,{},params,{})


@pytest.mark.integration
def test_real_native_component_mesh_operation(tmp_path):
    provider=os.environ.get('CIW_ISGT_REPO')
    if not provider:pytest.skip('Set CIW_ISGT_REPO for pinned native mesh solve')
    s=Session(make_demo_run(),tmp_path/'live');s.workbench.bind_workflow('mesh-path',{'isgt':provider})
    source,_=add(s);params=bridge.source_parameters(s.workbench,source['source_id'],source_vertex=0,target_vertex=3)
    mapped=bridge.retain_mesh_source(s.workbench,params,label='native mesh')
    first=execute(s,'ciw.mesh-path.v1',{'source_id':mapped['source_id']})
    bundle=call(s,'bundle.get',{'bundle_id':first['bundle_id']})
    data=bundle['steps'][0]['result']['data']
    assert data['solution']['target_distance']==pytest.approx(5)
    assert data['solution']['target_path'] in ([0,1,3],[0,2,3])
    assert data['request']['mesh']['vertices']==record()['decoded']['world_positions'][:4]
    replay_ref=call(s,'bundle.replay',{'bundle_id':first['bundle_id']})['bundle']
    replay=call(s,'bundle.get',{'bundle_id':replay_ref['bundle_id']})
    assert replay['steps'][0]['numerical_result_id']==bundle['steps'][0]['numerical_result_id']
    assert replay['steps'][0]['execution_id']!=bundle['steps'][0]['execution_id']


@pytest.mark.parametrize('raw',[b'{}',b'[]',b'{"buffers":[0]}',b'{"buffers":null}'])
def test_malformed_local_document_is_valueerror(tmp_path,raw):
    path=tmp_path/'bad.gltf';path.write_bytes(raw)
    with pytest.raises(ValueError):gltf.read_local(path,provenance=PROVENANCE)


def test_preprocessing_text_does_not_limit_full_source_index_map():
    # Independent planar indexed grid exercises the declared maximum vertices.
    positions=[[float(i),float(j),0.] for j in range(16) for i in range(16)]
    triangles=[]
    for j in range(15):
        for i in range(15):
            a=j*16+i
            triangles.extend([[a,a+1,a+16],[a+1,a+17,a+16]])
    raw_positions=b''.join(struct.pack('<3f',*p) for p in positions)
    indices=[i for triangle in triangles for i in triangle]
    binary=raw_positions+struct.pack('<'+'H'*len(indices),*indices)
    doc={'asset':{'version':'2.0'},'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
         'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1}]}],
         'buffers':[{'uri':'grid.bin','byteLength':len(binary)}],
         'bufferViews':[{'buffer':0,'byteLength':len(raw_positions)},
                        {'buffer':0,'byteOffset':len(raw_positions),'byteLength':len(binary)-len(raw_positions)}],
         'accessors':[{'bufferView':0,'componentType':5126,'count':256,'type':'VEC3','min':[0,0,0],'max':[15,15,0]},
                      {'bufferView':1,'componentType':5123,'count':len(indices),'type':'SCALAR'}]}
    mapped=gltf.mesh_projection(gltf.make_record(canonical(doc),{'grid.bin':binary},provenance=PROVENANCE),
                                source_vertex=0,target_vertex=255)
    assert mapped['local_to_source_vertices']==list(range(256))
    assert len(mapped['source_triangle_indices'])==450
    assert all(len(text)<=512 for text in mapped['source']['request']['mesh']['provenance']['preprocessing'])


def test_godot_offline_observer_schema_requires_probe_records(tmp_path):
    # Explicit test double, not execution evidence. Geometry is independently
    # populated from source and native winding is the declared reversible map.
    s=Session(make_demo_run(),tmp_path/'live');source,_=add(s)
    params=bridge.source_parameters(s.workbench,source['source_id'])
    native=record();g=native['decoded']
    from ciw.telemetry import digest
    observation={'schema':'ciw.workbench-import.v1','request':bridge._request(params),
        'asset_sha256':native['gltf']['sha256'],'engine_version':'4.6.3-stable (official)',
        'parts':{'synthetic':{'vertices':8,'triangles':4,'min':[2,3,4],'max':[4,6,4],
            'surfaces':[{'local_positions':g['positions'],'world_positions':g['world_positions'],
                         'indices':[i for a,b,c in g['triangles'] for i in (a,c,b)]}]}},
        'rays':[{'from':[0,0,0],'to':[1,1,1],'hit':False,'position':None,'name':None}]*6}
    data={'schema':'ciw.gltf-import-evidence.v1','source_id':source['source_id'],
        'native_record_ref':digest(native),'observation':observation,
        'comparison':bridge._comparison(native,observation,bridge._request(params)),
        'logs':{key:{'utf8':'TEST DOUBLE','sha256':gltf.bytes_ref(b'TEST DOUBLE')} for key in ('stdout','stderr')}}
    bridge.validate_payload(bridge.INSPECT,data,{},params,{})
    for rays in (None,[], 'not a ray observation', [{'hit':False}]*6):
        changed=deepcopy(data);changed['observation']['rays']=rays
        with pytest.raises(ValueError):bridge.validate_payload(bridge.INSPECT,changed,{},params,{})
    changed=deepcopy(data);del changed['observation']['rays']
    with pytest.raises(ValueError):bridge.validate_payload(bridge.INSPECT,changed,{},params,{})


def test_cli_ingress_add_projection_and_reopen(tmp_path,capsys):
    from ciw.data_cli import main
    doc,buffers=fixture();asset=tmp_path/'fixture.gltf';asset.write_bytes(canonical(doc))
    (tmp_path/'mesh.bin').write_bytes(buffers['mesh.bin'])
    source_file=tmp_path/'source.json'
    assert main(['from-gltf',str(asset),'--dataset-id','cli-fixture','--revision','1',
        '--recorded-at',STAMP,'--source',PROVENANCE['source'],'--publisher-revision','1',
        '--license','test fixture','--attribution','NET test authors','--output',str(source_file)])==0
    capsys.readouterr()
    session=Session(make_demo_run(),tmp_path/'initial')
    initial=session.save_workspace(tmp_path/'initial.json')
    added=tmp_path/'added.json'
    assert main(['add',str(source_file),'--label','local synthetic glTF','--workspace',str(initial),'--output',str(added)])==0
    source=json.loads(capsys.readouterr().out)
    projected=tmp_path/'projected.json'
    assert main(['project-gltf','--source-id',source['source_id'],'--source-vertex','4','--target-vertex','7',
        '--label','second component','--workspace',str(added),'--output',str(projected)])==0
    response=json.loads(capsys.readouterr().out)
    restored=Session.from_workspace(projected,output_dir=tmp_path/'reopened')
    assert response['result']['result_id'] in restored.results
    mesh=restored.workbench.get_source(response['mesh_source']['source_id'])
    assert mesh['input_source_ids']==[source['source_id']]
    assert (restored.output_dir/restored.recording_file).is_file()
    # Existing output is never overwritten, including after a valid re-run.
    before=projected.read_bytes()
    assert main(['project-gltf','--source-id',source['source_id'],'--source-vertex','4','--target-vertex','7',
        '--label','second component','--workspace',str(added),'--output',str(projected)])==2
    assert projected.read_bytes()==before
    capsys.readouterr()
