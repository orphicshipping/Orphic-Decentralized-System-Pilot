"""Real retained ingestion -> numerical consumer -> correction/reopen pipeline."""
import base64
from copy import deepcopy
import json

import pytest

from ciw.core.identities import content_identity
from ciw.data_sources import csv_observation_stream, retain_record
from ciw.data_workflow import COMPARE, comparison_parameters
from ciw.instruments import make_demo_run
from ciw.session import Session
from ciw.workbench import Workbench


def mapping(unit='K', frame='specimen', clock='test-clock'):
    return {'identity': {'model_id': 'temperature-test', 'entity_id': 'part-1', 'execution_id': None},
            'clock': {'id': clock, 'column': 'time', 'unit': 's'}, 'frame': frame,
            'provenance': {'provider': 'recorded-test', 'semantics': 'observed'},
            'quantities': [{'column': 'temperature', 'quantity': 'temperature', 'unit': unit}]}


def source(dataset='sensor', value='300', revision=1, parent=None, **kwargs):
    return csv_observation_stream(f'time,temperature\n1,{value}\n2,301\n'.encode(),
        mapping=mapping(**kwargs), dataset_id=dataset, revision=revision,
        parent_source_id=parent, recorded_at='2026-10-05T19:00:00Z')


def add(workbench, value, label='readings'):
    return workbench.add_source({'kind': 'data-record', 'label': label,
        'bytes_b64': base64.b64encode(json.dumps(value).encode()).decode()})


def call(session, kind, payload={}):
    answer = session.handle({'protocol_version': 1, 'request_id': 'test', 'type': kind, 'payload': payload})
    assert answer['type'] == 'response', answer
    return answer['payload']


def execute(session, left, right):
    return call(session, 'operation.execute', {'operation_id': COMPARE,
        'parameters': comparison_parameters(session.workbench, left_source_id=left['source_id'],
             right_source_id=right['source_id'], quantity='temperature', atol=0.0)})


def test_source_idempotency_conservation_and_reopen(tmp_path):
    session = Session(make_demo_run(), tmp_path/'live')
    original = source()
    left = add(session.workbench, original)
    assert add(session.workbench, original, 'another label') == left
    assert session.workbench.revision == 1
    assert call(session, 'data.get', {'source_id': left['source_id']}) == original
    right = add(session.workbench, source(dataset='reference'), 'reference')
    result = execute(session, left, right)
    assert result['status'] == 'completed', result
    data = result['result']['data']
    assert data['comparison']['outcome']['status'] == 'PASS'
    assert data['comparison']['outcome']['sample_count'] == 2
    assert data['inputs']['left']['selected_observation_count'] == 2
    assert data['inputs']['left']['unselected_observation_count'] == 0
    assert call(session, 'data.list')['sources'] == session.workbench.list_sources()
    path = session.save_workspace(tmp_path/'workspace.json')
    restored = Session.from_workspace(path, output_dir=tmp_path/'reopened')
    assert restored.workbench.serialize() == session.workbench.serialize()
    assert restored.results == session.results
    replay = execute(restored, left, right)
    assert replay['result']['data'] == data
    assert replay['execution']['execution_id'] != result['execution']['execution_id']


@pytest.mark.parametrize('change', [{'unit':'degC'}, {'frame':'another'}, {'clock':'other'}])
def test_incompatible_domains_stay_indeterminate(tmp_path, change):
    session = Session(make_demo_run(), tmp_path)
    result = execute(session, add(session.workbench, source()),
                     add(session.workbench, source(dataset='reference', **change)))
    assert result['status'] == 'completed'
    assert result['result']['data']['comparison']['outcome']['status'] == 'INDETERMINATE'
    assert result['result']['verification_status'] == 'not_verified'


def test_revision_collision_missing_parent_and_schema_change_refused():
    workbench = Workbench()
    first = source()
    add(workbench, first)
    before = workbench.serialize()
    with pytest.raises(ValueError, match='revision'):
        add(workbench, source(value='302'))
    with pytest.raises(ValueError, match='preceding revision'):
        add(workbench, source(revision=2, parent='sha256:'+'f'*64))
    with pytest.raises(ValueError, match='First dataset'):
        add(workbench, source(dataset='other', parent=first['source_id']))
    native = first['native_record']['observations'][0]
    changed = retain_record(native, dataset_id='sensor', revision=2,
        parent_source_id=first['source_id'], recorded_at='2026-10-05T19:01:00Z', data_family='observation')
    with pytest.raises(ValueError, match='schema change'):
        add(workbench, changed)
    assert workbench.serialize() == before


def test_forged_unretained_inputs_refuse_without_result(tmp_path):
    session = Session(make_demo_run(), tmp_path)
    other = Workbench()
    left = add(other, source())
    right = add(other, source(dataset='reference'))
    request = comparison_parameters(other, left_source_id=left['source_id'], right_source_id=right['source_id'],
                                    quantity='temperature', atol=0.0)
    result = call(session, 'operation.execute', {'operation_id': COMPARE, 'parameters': request})
    assert result['status'] == 'refused'
    assert result['result'] is None
    assert not session.results
    assert len(session.executions) == 1


def test_correction_invalidates_consumer_transitively_without_mutation(tmp_path):
    session = Session(make_demo_run(), tmp_path/'live')
    first = source()
    old = add(session.workbench, first)
    right = add(session.workbench, source(dataset='reference'))
    original = execute(session, old, right)['result']
    revision = source(value='302', revision=2, parent=first['source_id'])
    replacement = add(session.workbench, revision)
    # Retention alone is neither correction approval nor physical acceptance.
    assert session.dependency_status()['artifact_status'][original['result_id']]['status'] == 'current'
    proposal = call(session, 'correction.propose', {'old_source_id': old['source_id'],
        'new_source_id': replacement['source_id'], 'kind':'source_data', 'reason':'corrected retained values'})
    call(session, 'correction.review', {'correction_id': proposal['correction_id'], 'decision':'accept',
        'expected_revision': session.correction_journal.revision, 'reviewer':'operator', 'reason':'reviewed correction'})
    status = session.dependency_status()['artifact_status']
    for identity in (old['source_id'], original['execution_id'], original['result_id']):
        assert status[identity]['status'] == 'stale'
    assert status[replacement['source_id']]['status'] == 'current'
    assert session.results[original['result_id']] == original
    current = execute(session, replacement, right)['result']
    assert current['data']['comparison']['outcome']['status'] == 'FAIL'
    assert session.dependency_status()['artifact_status'][current['result_id']]['status'] == 'current'
    path = session.save_workspace(tmp_path/'workspace.json')
    restored = Session.from_workspace(path, output_dir=tmp_path/'reopened')
    assert restored.dependency_status() == session.dependency_status()
    assert restored.results == session.results


def test_reopen_rejects_missing_source_even_with_resealed_results(tmp_path):
    session = Session(make_demo_run(), tmp_path/'live')
    left = add(session.workbench, source())
    right = add(session.workbench, source(dataset='reference'))
    execute(session, left, right)
    value = session._workspace()
    value['workbench']['sources'] = value['workbench']['sources'][1:]
    value['workbench']['revision'] -= 1
    path = tmp_path/'broken.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='present unchanged'):
        Session.from_workspace(path, output_dir=tmp_path/'reopened')


def test_explicit_cross_source_dependencies_invalidate_mapping(tmp_path):
    session = Session(make_demo_run(), tmp_path)
    first = source()
    upstream = add(session.workbench, first)
    downstream_payload = {'kind':'data-record', 'label':'mapped observation',
        'bytes_b64':base64.b64encode(json.dumps(source(dataset='mapped')).encode()).decode(),
        'input_source_ids':[upstream['source_id']]}
    downstream = session.workbench.add_source(downstream_payload)
    reference = add(session.workbench, source(dataset='reference'))
    completed = execute(session, downstream, reference)['result']
    corrected = add(session.workbench, source(value='303', revision=2, parent=first['source_id']))
    proposal = call(session, 'correction.propose', {'old_source_id':upstream['source_id'],
        'new_source_id':corrected['source_id'], 'kind':'source_data', 'reason':'source correction'})
    call(session, 'correction.review', {'correction_id':proposal['correction_id'], 'decision':'accept',
        'expected_revision':session.correction_journal.revision, 'reviewer':'operator', 'reason':'reviewed'})
    status = session.dependency_status()['artifact_status']
    assert status[downstream['source_id']]['status'] == 'stale'
    assert status[completed['result_id']]['status'] == 'stale'
    assert session.workbench.restore(session.workbench.serialize()).serialize() == session.workbench.serialize()


def test_unknown_source_link_refuses_without_retention():
    workbench = Workbench()
    with pytest.raises(ValueError, match='missing dependency'):
        workbench.add_source({'kind':'data-record', 'label':'bad',
            'bytes_b64':base64.b64encode(json.dumps(source()).encode()).decode(),
            'input_source_ids':['source:sha256:'+'f'*64]})
    assert not workbench.list_sources()


def test_data_cli_ingest_compare_reopen(tmp_path, capsys):
    from ciw.data_cli import main
    session = Session(make_demo_run(), tmp_path/'initial')
    workspace = session.save_workspace(tmp_path/'initial.json')
    csv = tmp_path/'input.csv'
    csv.write_text('time,temperature\n1,300\n2,301\n')
    spec = tmp_path/'mapping.json'
    spec.write_text(json.dumps(mapping()))
    native = tmp_path/'native.json'
    assert main(['from-csv', str(csv), '--mapping', str(spec), '--dataset-id','sensor',
                 '--revision','1', '--recorded-at','2026-10-05T19:00:00Z', '--output', str(native)]) == 0
    first = tmp_path/'first.json'
    assert main(['add', str(native), '--workspace', str(workspace), '--label','sensor', '--output',str(first)]) == 0
    restored = Session.from_workspace(first, output_dir=tmp_path/'restored')
    identity = restored.workbench.list_sources()[0]['source_id']
    output = tmp_path/'compared.json'
    assert main(['compare','--workspace',str(first),'--left',identity,'--right',identity,
                 '--quantity','temperature','--atol','0','--output',str(output)]) == 0
    final = Session.from_workspace(output, output_dir=tmp_path/'final')
    assert next(iter(final.results.values()))['data']['comparison']['outcome']['status'] == 'PASS'
    assert main(['inspect',str(native)]) == 0
    assert main(['retain', str(native), '--dataset-id','bad','--revision','1',
                 '--recorded-at','2026-10-05T19:00:00Z','--family','observation','--output',str(tmp_path/'bad.json')]) == 2


def test_session_inventory_reports_actual_host_bindings(tmp_path):
    from ciw.operations.registry import Operation
    session = Session(make_demo_run(), tmp_path)
    session.operations.register(Operation('test.extra.v1', 'backend', lambda r,p:{}, lambda:{'test':'1'}))
    snapshot = call(session, 'data.inventory')
    assert any(item['operation_id'] == 'test.extra.v1' for item in snapshot['operations'])
    assert all(item['registration'] == 'current_session' for item in snapshot['operations'])


def test_comparison_payload_rejects_boolean_for_integer_count(tmp_path):
    from ciw.data_workflow import validate_payload
    session = Session(make_demo_run(), tmp_path)
    left = add(session.workbench, source())
    result = execute(session, left, left)['result']
    bad = deepcopy(result['data'])
    bad['inputs']['left']['unselected_observation_count'] = False
    with pytest.raises(ValueError, match='differs'):
        validate_payload(COMPARE, bad, session.run, result['parameters'], session.selection)


def test_historical_reexecution_discloses_staleness(tmp_path):
    session = Session(make_demo_run(), tmp_path)
    first = source()
    old = add(session.workbench, first)
    new = add(session.workbench, source(value='302', revision=2, parent=first['source_id']))
    proposal = call(session, 'correction.propose', {'old_source_id':old['source_id'],
        'new_source_id':new['source_id'], 'kind':'source_data', 'reason':'corrected values'})
    call(session, 'correction.review', {'correction_id':proposal['correction_id'], 'decision':'accept',
        'expected_revision':session.correction_journal.revision, 'reviewer':'operator', 'reason':'checked'})
    historical = execute(session, old, old)
    assert historical['status'] == 'completed'
    assert historical['dependency_status']['status'] == 'stale'
    assert 'dependency_status' not in historical['result']


def test_native_thermal_stream_projection_retains_automatic_dependency(tmp_path):
    from test_measured_thermal import _source
    from ciw.data_sources import native_record
    from ciw.measured_thermal import streams
    session = Session(make_demo_run(), tmp_path)
    original = _source()
    upstream = session.workbench.add_source({'kind':'measured-thermal','label':'thermal recording',
        'bytes_b64':base64.b64encode(json.dumps(original).encode()).decode()})
    derived = call(session, 'data.project-observations', {'source_id':upstream['source_id'],
        'selector':{'channel':'shell'}, 'dataset_id':'shell-observations','revision':1,
        'parent_source_id':None,'recorded_at':'2026-10-05T19:00:00Z','label':'shell'})
    assert derived['input_source_ids'] == [upstream['source_id']]
    assert native_record(session.workbench.get_source(derived['source_id'])) == streams(original)['shell']
    result = call(session, 'operation.execute', {'operation_id':COMPARE,
        'parameters':comparison_parameters(session.workbench,left_source_id=derived['source_id'],
            right_source_id=derived['source_id'],quantity='shell_temperature',atol=0.0)})
    assert result['result']['data']['comparison']['outcome']['status'] == 'PASS'
    assert upstream['source_id'] in session._artifact_graph()[derived['source_id']]['dependencies']


def test_cli_projects_native_thermal_channel_with_automatic_lineage(tmp_path, capsys):
    from ciw.data_cli import main
    from test_measured_thermal import _source
    session = Session(make_demo_run(), tmp_path/'live')
    original = _source()
    upstream = session.workbench.add_source({'kind':'measured-thermal','label':'thermal',
        'bytes_b64':base64.b64encode(json.dumps(original).encode()).decode()})
    before = session.save_workspace(tmp_path/'before.json')
    after = tmp_path/'after.json'
    assert main(['project-observations','--workspace',str(before),'--source-id',upstream['source_id'],
        '--channel','shell','--dataset-id','shell','--revision','1','--recorded-at','2026-10-05T19:00:00Z',
        '--label','shell','--output',str(after)]) == 0
    restored = Session.from_workspace(after, output_dir=tmp_path/'reopened')
    derived = next(s for s in restored.workbench.list_sources() if s['kind']=='data-record')
    assert derived['input_source_ids'] == [upstream['source_id']]
