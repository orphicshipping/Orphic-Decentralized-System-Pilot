"""Synthetic data, actual NET Sessions; no successful runtime/provider stubs."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from ciw import sensor_project as p, sensor_manufacturing as m, sensor_manufacturing_cli as cli
from ciw.control_plane import ObservationBus
from ciw.control_contracts import bytes_ref
from ciw.core.identities import content_identity


def raw(value): return (json.dumps(value,indent=2,allow_nan=False)+'\r\n').encode()


def fixture(tmp_path, *, measured=True, units=4):
    root=tmp_path/'inputs';root.mkdir()
    files={'board.kicad_pcb':b'(kicad_pcb (synthetic_test_only))\r\n',
           'board.kicad_sch':b'(kicad_sch (synthetic_test_only))\r\n',
           'design.json':raw({'width_mm':10}),
           'bom.csv':b'Reference,Quantity,Manufacturer,MPN,Note\r\nU1,1,Fixture,SENSOR,synthetic\r\nC1,2,Fixture,CAP,"two, grouped"\r\n',
           'procedure.txt':b'Illustrative fixture-force reading; not a physical product acceptance procedure.\r\n'}
    requirement={'requirement_id':'example-force','quantity':'force','unit':'N','lower':2.,'upper':2.1,
                 'procedure_path':'procedure.txt','criterion_basis':'synthetic_example'}
    files['requirements.json']=raw({'product_id':'SYNTHETIC-MODULE','revision':'A', 'requirements':[requirement]})
    def resource(name,kind,tags,cap=4):
        return {'resource_id':name,'kind':kind,'capabilities':tags,'batch_capacity':cap,
                'limits':[{'parameter':'width','unit':'mm','lower':0,'upper':20}],
                'rate_per_hour':36.,'currency':'CAD'}
    resources=[resource('fab','supplier',['pcb.fabricate']),resource('buy','supplier',['parts.source']),
               resource('assembly','cell',['assemble']),resource('gauge','instrument',['force.read'],1)]
    def op(name,kind,deps,res,tags,setup,per,bs):
        return {'operation_id':name,'kind':kind,'depends_on':deps,'artifact_paths':['board.kicad_pcb'],
                'requires':tags,'demands':[{'parameter':'width','value':10,'unit':'mm','source':{'path':'design.json','keys':['width_mm']}}],
                'allowed_resources':[res],'setup_s':setup,'per_batch_s':per,'batch_size':bs,'adapter_id':'declared.'+name}
    operations=[op('fabricate','fabricate',[],'fab',['pcb.fabricate'],10,20,4),
                op('procure','procure',[],'buy',['parts.source'],0,15,4),
                op('assemble','assemble',['fabricate','procure'],'assembly',['assemble'],5,3,1),
                op('inspect','inspect',['assemble'],'gauge',['force.read'],2,10,1)]
    bus=ObservationBus(); inspections=[]
    for i in range(units):
        unit=f'UNIT-{i+1:03d}'
        row=None
        if measured:
            row=bus.observe(identity={'model_id':'synthetic-gaugelink','entity_id':f'gauge-reading-{i}','execution_id':None},
                clock={'id':'declared-receipt-clock','time_s':float(i)},frame='fixture',quantity='force',value=2.03+i*.01,unit='N',uncertainty=None,
                provenance={'provider':'synthetic-gaugelink-peer','sources':[bytes_ref(files['procedure.txt']),bytes_ref(files['board.kicad_pcb'])], 'semantics':'simulated'})
        inspections.append({'unit_id':unit,'requirement_id':'example-force','operation_id':'inspect',
            'observation_ref':row['record_digest'] if row else None,'expected_identity':{'model_id':'synthetic-gaugelink','entity_id':f'gauge-reading-{i}'},
            'frame':'fixture','clock_id':'declared-receipt-clock','required_source_paths':['procedure.txt','board.kicad_pcb']})
    plan={'schema':m.DEFINITION,'product':{'product_id':'SYNTHETIC-MODULE','revision':'A','native_paths':['board.kicad_pcb','board.kicad_sch']},
          'order':{'work_order_id':'PLANNED-WO-001','quantity':units,'unit_ids':[f'UNIT-{i+1:03d}' for i in range(units)]},
          'requirements_path':'requirements.json','bom':{'path':'bom.csv','columns':{'reference':'Reference','quantity':'Quantity','manufacturer':'Manufacturer','part_number':'MPN'}},
          'resources':resources,'operations':operations,'inspections':inspections,'basis':'declared_planning_assumptions'}
    files['link.json']=raw(bus.snapshot());files[m.PLAN_PATH]=raw(plan)
    kinds={'board.kicad_pcb':'electronics.pcb','board.kicad_sch':'electronics.schematic','design.json':'model.input',
           'bom.csv':'manufacturing.output','procedure.txt':'source.notice','requirements.json':'model.input',
           'link.json':'observations.native',m.PLAN_PATH:'model.input'}
    for name,data in files.items():(root/name).write_bytes(data)
    spec={'schema':'ciw.sensor-project-request.v1','project_id':'SYNTHETIC-MODULE','hardware_revision':'A','mode':'SIMULATION','baseline_ref':None,
          'artifacts':[{'path':name,'kind':kinds[name],'parents':[],'tool':{'name':'synthetic fixture','version':'1'},'origin':'synthetic'} for name in files],
          'mems':[],'native_streams':['link.json'],'bindings':[]}
    return root,spec,plan


def compile_(root,spec,plan):
    (root/m.PLAN_PATH).write_bytes(raw(plan))
    return m.compile_definition(p.source(root,spec),deepcopy(m.PARAMETERS))


def tree(root):return {str(q.relative_to(root)):bytes_ref(q.read_bytes()) for q in root.rglob('*') if q.is_file()}


def test_bom_and_independent_schedule_arithmetic(tmp_path):
    root,spec,plan=fixture(tmp_path)
    result=compile_(root,spec,plan)
    assert result['bom']['components_per_unit']==3 and result['bom']['total_components']==12
    assert [r['quantity'] for r in result['bom']['procurement']]==[8,4]
    assert result['bom']['lines'][1]['source_record']['Note']=='two, grouped'
    expected=max(Fraction(10)+20,Fraction(15))+(5+4*3)+(2+4*10)
    assert result['schedule']['makespan_s']==float(expected)==89
    assert result['schedule']['modeled_processing_cost']==pytest.approx((30+15+17+42)/100)
    assert result['physical_units_established']==0 and result['claims']['equipment_control'] is False


def test_batch_redesign_requires_actual_declared_resource_capacity(tmp_path):
    root,spec,plan=fixture(tmp_path)
    before=compile_(root,spec,plan)
    plan['operations'][-1]['batch_size']=4
    blocked=compile_(root,spec,plan)
    assert blocked['schedule']['status']=='INFEASIBLE' and blocked['schedule']['makespan_s'] is None
    assert 'INSUFFICIENT_BATCH_CAPACITY' in blocked['schedule']['instructions'][-1]['rejected_resources']['gauge']
    plan['resources'][-1]['batch_capacity']=4
    after=compile_(root,spec,plan)
    assert after['schedule']['makespan_s']==59
    assert before['definition_ref']!=after['definition_ref']
    assert before['product_artifacts']==after['product_artifacts']
    assert before['inspections']==after['inspections']


def test_capability_mismatch_retained_but_other_branches_scheduled(tmp_path):
    root,spec,plan=fixture(tmp_path)
    plan['resources'][0]['capabilities']=['wrong']
    result=compile_(root,spec,plan)
    states={r['operation_id']:r['status'] for r in result['schedule']['instructions']}
    assert states=={'fabricate':'NO_COMPATIBLE_RESOURCE','procure':'PLANNED','assemble':'BLOCKED_PREDECESSOR','inspect':'BLOCKED_PREDECESSOR'}
    assert result['schedule']['modeled_processing_cost'] is None


@pytest.mark.parametrize('change,reason',[(lambda r:r['limits'].clear(),'UNKNOWN_ENVELOPE:width'),
    (lambda r:r['limits'][0].update(unit='m'),'UNIT_MISMATCH:width'),
    (lambda r:r['limits'][0].update(upper=9),'OUTSIDE_ENVELOPE:width')])
def test_resource_envelopes_do_not_guess_or_convert(change,reason,tmp_path):
    root,spec,plan=fixture(tmp_path);change(plan['resources'][0]);result=compile_(root,spec,plan)
    assert reason in result['schedule']['instructions'][0]['rejected_resources']['fab']


def test_all_original_observation_fields_and_unknown_uncertainty_retained(tmp_path):
    root,spec,plan=fixture(tmp_path);original=json.loads((root/'link.json').read_bytes())['observations']
    result=compile_(root,spec,plan)
    assert [r['measurement']['observation'] for r in result['inspections']]==original
    assert all(r['qualification']=='NOT_ESTABLISHED' and r['acceptance']=='NOT_AUTHORIZED' for r in result['inspections'])
    assert all(r['measurement']['observation']['uncertainty'] is None for r in result['inspections'])


def test_missing_measurements_cover_each_unit_without_fabrication(tmp_path):
    root,spec,plan=fixture(tmp_path,measured=False)
    result=compile_(root,spec,plan)
    assert len(result['inspections'])==4
    assert all(r['nominal_comparison']=='PENDING' and r['measurement'] is None for r in result['inspections'])


def test_unspecified_criterion_stays_unknown_even_with_reading(tmp_path):
    root,spec,plan=fixture(tmp_path)
    value=json.loads((root/'requirements.json').read_bytes());value['requirements'][0].update(lower=None,upper=None,criterion_basis='unspecified')
    (root/'requirements.json').write_bytes(raw(value))
    result=compile_(root,spec,plan)
    assert all(r['nominal_comparison']=='CRITERION_UNSPECIFIED' for r in result['inspections'])


def test_outside_nominal_bounds_is_not_release_or_rejection(tmp_path):
    root,spec,plan=fixture(tmp_path)
    value=json.loads((root/'requirements.json').read_bytes());value['requirements'][0].update(lower=3.,upper=4.)
    (root/'requirements.json').write_bytes(raw(value))
    assert all(r['nominal_comparison']=='NOMINAL_OUTSIDE' and r['acceptance']=='NOT_AUTHORIZED' for r in compile_(root,spec,plan)['inspections'])


@pytest.mark.parametrize('mutate',[
    lambda d:d['product'].update(revision='B'),lambda d:d['product'].update(product_id='other'),
    lambda d:d['product'].update(native_paths=['procedure.txt']),lambda d:d['order'].update(quantity=5),
    lambda d:d['order'].update(quantity=True),lambda d:d['order']['unit_ids'].append('UNIT-001'),
    lambda d:d['operations'][0]['depends_on'].append('inspect'),lambda d:d['operations'][0]['depends_on'].append('absent'),
    lambda d:d['operations'][0]['allowed_resources'].append('unbound'),lambda d:d['resources'].append(deepcopy(d['resources'][0])),
    lambda d:d['operations'].append(deepcopy(d['operations'][0])),lambda d:d['inspections'].pop(),
    lambda d:d['inspections'].append(deepcopy(d['inspections'][0])),lambda d:d['inspections'][0].update(operation_id='assemble'),
    lambda d:d['inspections'][0].update(unit_id='unknown'),lambda d:d['inspections'][0].update(frame='other'),
    lambda d:d['inspections'][0].update(clock_id='other'),lambda d:d['inspections'][0]['expected_identity'].update(entity_id='other'),
    lambda d:d['inspections'][1].update(observation_ref=d['inspections'][0]['observation_ref']),
    lambda d:d['inspections'][0].update(required_source_paths=['missing-procedure.txt']),
    lambda d:d['operations'][0].update(shell='rm -rf /'),lambda d:d.update(physical_execution=True),
    lambda d:d['operations'][0]['demands'][0].update(value=11),
    lambda d:d['operations'][0]['demands'][0]['source'].update(keys=['absent']),
    lambda d:d['operations'][0].update(per_batch_s=True),lambda d:d['operations'][0].update(setup_s=-1),
    lambda d:d['operations'][0].update(batch_size=0),lambda d:d['resources'][0].update(batch_capacity=True),
    lambda d:d['resources'][0].update(rate_per_hour=1,currency=None),
    lambda d:d['resources'][0]['limits'].append(deepcopy(d['resources'][0]['limits'][0])),
])
def test_structural_binding_refusals(mutate,tmp_path):
    root,spec,plan=fixture(tmp_path);mutate(plan)
    with pytest.raises(ValueError):compile_(root,spec,plan)


@pytest.mark.parametrize('payload',[
 b'Reference,Quantity,Manufacturer,MPN\nU1,-1,A,B\n',
 b'Reference,Quantity,Manufacturer,MPN\nU1,1.0,A,B\n',
 b'Reference,Quantity,Manufacturer,MPN\nU1,01,A,B\n',
 b'Reference,Quantity,Manufacturer,MPN\nU1,1,A,B\nU1,1,A,B\n',
 b'Reference,Quantity,Manufacturer,MPN\nU1,1,A\n',
 b'Reference,Quantity,Manufacturer,MPN,MPN\nU1,1,A,B,B\n',
 b'Reference,Quantity,Manufacturer,MPN\n',
])
def test_invalid_bom_refusals(payload,tmp_path):
    root,spec,plan=fixture(tmp_path);(root/'bom.csv').write_bytes(payload)
    with pytest.raises(ValueError):compile_(root,spec,plan)


def test_original_procedure_change_breaks_measurement_binding(tmp_path):
    root,spec,plan=fixture(tmp_path);(root/'procedure.txt').write_bytes(b'Changed procedure\n')
    with pytest.raises(ValueError,match='references'):compile_(root,spec,plan)


def test_design_change_is_not_silently_relabelled(tmp_path):
    root,spec,plan=fixture(tmp_path);(root/'board.kicad_pcb').write_bytes(b'changed native board')
    with pytest.raises(ValueError,match='references'):compile_(root,spec,plan)


def test_real_session_replay_recovery_and_retained_occurrences(tmp_path):
    root,spec,plan=fixture(tmp_path);src=p.source(root,spec);before=tree(root)
    one=m.execute(src,tmp_path/'first');two=m.replay(tmp_path/'first',tmp_path/'second',one['snapshot_ref'])
    assert one['data']==two['data'] and one['evidence_id']==two['evidence_id']
    assert one['execution_id']!=two['execution_id'] and one['result_id']!=two['result_id']
    assert one['execution_id']!=one['result_id']
    old=tree(tmp_path/'first'); assert m.inspect(tmp_path/'first',one['snapshot_ref'])==one
    m.export_inputs(tmp_path/'first',tmp_path/'recovered',one['snapshot_ref'])
    assert tree(tmp_path/'recovered')==before and tree(root)==before and tree(tmp_path/'first')==old
    assert one['claims']['verification_id'] is None


def test_semantic_refusal_is_retained_in_native_execution(tmp_path):
    root,spec,plan=fixture(tmp_path);plan['inspections'].clear();(root/m.PLAN_PATH).write_bytes(raw(plan))
    result=m.execute(p.source(root,spec),tmp_path/'failed')
    assert result['status']=='REFUSED' and result['result_id'] is None and result['refusal']
    assert (tmp_path/'failed/workspace.json').is_file()
    assert m.inspect(tmp_path/'failed')['status']=='REFUSED'


def test_cost_unknown_and_mixed_currency_not_summed(tmp_path):
    root,spec,plan=fixture(tmp_path);plan['resources'][0]['rate_per_hour']=None
    assert compile_(root,spec,plan)['schedule']['modeled_processing_cost'] is None
    plan['resources'][0].update(rate_per_hour=36.,currency='USD')
    result=compile_(root,spec,plan)
    assert result['schedule']['modeled_processing_cost'] is None and result['schedule']['currency'] is None


def test_same_resource_operations_do_not_overlap(tmp_path):
    root,spec,plan=fixture(tmp_path)
    plan['resources'][0]['capabilities'].append('parts.source');plan['operations'][1]['allowed_resources']=['fab']
    result=compile_(root,spec,plan);rows={r['operation_id']:r for r in result['schedule']['instructions']}
    assert rows['procure']['start_s']>=rows['fabricate']['finish_s']
    assert rows['assemble']['start_s']>=rows['procure']['finish_s']


def test_reordering_declarations_preserves_simulation(tmp_path):
    root,spec,plan=fixture(tmp_path);one=compile_(root,spec,plan)
    plan['resources'].reverse();plan['operations'].reverse();plan['inspections'].reverse()
    two=compile_(root,spec,plan)
    assert one['schedule']==two['schedule'] and one['inspections']==two['inspections']
    assert one['definition_ref']!=two['definition_ref']


def test_nonmultiple_batch_count_rounds_up(tmp_path):
    root,spec,plan=fixture(tmp_path,units=5)
    result=compile_(root,spec,plan);rows={r['operation_id']:r for r in result['schedule']['instructions']}
    assert rows['fabricate']['batches']==2 and result['bom']['total_components']==15
    assert result['schedule']['makespan_s']==50+20+52


def test_output_clobber_wrong_anchor_and_symlink_refused(tmp_path):
    root,spec,plan=fixture(tmp_path);src=p.source(root,spec);result=m.execute(src,tmp_path/'first')
    with pytest.raises(FileExistsError):m.execute(src,tmp_path/'first')
    with pytest.raises(ValueError):m.inspect(tmp_path/'first','sha256:'+'0'*64)
    link=tmp_path/'alias';link.symlink_to(tmp_path/'first',target_is_directory=True)
    with pytest.raises(ValueError):m.inspect(link)
    original=tree(tmp_path/'first')
    with pytest.raises(FileExistsError):m.export_inputs(tmp_path/'first',root)
    assert original==tree(tmp_path/'first')


def test_no_network_or_hardware_needed_for_native_session(tmp_path,monkeypatch):
    import socket
    def denied(*a,**k):raise AssertionError('Unexpected network')
    monkeypatch.setattr(socket,'create_connection',denied)
    root,spec,plan=fixture(tmp_path)
    assert m.execute(p.source(root,spec),tmp_path/'run')['status']=='RETAINED'


def test_cli_report_and_subprocess(tmp_path):
    root,spec,plan=fixture(tmp_path);request=tmp_path/'request.json';request.write_bytes(raw(spec))
    r=subprocess.run([sys.executable,'-m','ciw.sensor_manufacturing_cli','run',str(root),str(request),'--output-dir',str(tmp_path/'run')],capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr
    snapshot=json.loads(r.stdout)
    assert snapshot['data']['schedule']['makespan_s']==89
    out=tmp_path/'report.html';cli.report(tmp_path/'run',out,snapshot['snapshot_ref'])
    assert 'Capability-bound route' in out.read_text() and 'NOMINAL_WITHIN' in out.read_text()
    with pytest.raises(FileExistsError):cli.report(tmp_path/'run',out)
    with pytest.raises(ValueError):cli.report(tmp_path/'run',tmp_path/'run/report.html')


def test_original_stream_without_flattened_procedure_ref_remains_association_only(tmp_path):
    root,spec,plan=fixture(tmp_path)
    # The real legacy Link projection does not flatten its context/procedure into
    # provenance.sources. Retain that absence rather than fabricating a link.
    stream=json.loads((root/'link.json').read_bytes())
    from ciw.operations.runner import seal
    stream['observations']=[seal({**row, 'provenance':{**row['provenance'],'sources':[bytes_ref(b'original gauge frame')]}}) for row in stream['observations']]
    stream=seal(stream);(root/'link.json').write_bytes(raw(stream))
    for item,row in zip(plan['inspections'],stream['observations']):
        item.update(observation_ref=row['record_digest'],required_source_paths=[])
    result=compile_(root,spec,plan)
    assert all(v['procedure_binding']=='PLANNING_ASSOCIATION_ONLY' for v in result['inspections'])
    assert all(v['acceptance']=='NOT_AUTHORIZED' for v in result['inspections'])


def test_declared_original_source_requirement_cannot_be_removed_from_record(tmp_path):
    root,spec,plan=fixture(tmp_path)
    plan['inspections'][0]['required_source_paths'].append('design.json')
    with pytest.raises(ValueError,match='references'):compile_(root,spec,plan)
