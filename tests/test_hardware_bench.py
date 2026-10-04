"""Acquisition boundaries, raw provenance, and non-actuating bench plans."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from ciw import hardware_bench as bench, hardware_contract as hardware
from ciw.operations.runner import seal


def operator_fixture():
    value = bench.example_bundle()
    document = json.loads(base64.b64decode(value['evidence']['raw_base64']))
    document['serial_number'] = 'TEST-OPERATOR-UNIT-001'
    value['configuration']['components'][0]['serial_number'] = document['serial_number']
    value['evidence'] = bench.import_diagnostic(json.dumps(document).encode(), value['configuration'], 'storage-0',
        operator='Test operator fixture', captured_at='2026-01-01T00:00:00Z', procedure_id='net.smart-snapshot.v1')
    for mapping in value['mappings']: mapping['evidence_ref'] = value['evidence']['record_digest']
    value['request'] = bench.assessment_request(value['configuration'], value['plan'], [value['evidence']], value['mappings'])
    return value


@pytest.mark.parametrize('case,status', [('public','PASS'),('pass','PASS'),('fail','FAIL'),('missing','INDETERMINATE')])
def test_public_and_derived_examples_are_offline_explicit_and_assessable(case, status):
    bundle = bench.example_bundle(case)
    assert hardware.assess(bundle['request'])['status'] == status
    assert bundle['request']['observations']['source_kind'] == 'synthetic'
    evidence = bench.validate_evidence(bundle['evidence'], bundle['configuration'])
    assert evidence['source_kind'] == ('external_public' if case == 'public' else 'synthetic')
    assert evidence['provenance']['source']['raw_sha256'] == bench.PUBLIC_SHA256
    assert evidence['authority'] == bench.AUTHORITY
    bench.validate_assessment_binding(bundle['request'], [evidence])


def test_stale_configuration_example_is_refused():
    with pytest.raises(ValueError, match='different configuration'):
        hardware.assess(bench.example_request('stale'))


def test_public_source_and_license_are_exact_and_attributed():
    root = Path(bench.__file__).parent / 'bench_examples'
    manifest = json.loads((root / 'attribution.json').read_text(encoding='utf-8'))
    assert bench.bytes_ref((root / 'smartctl-public.json').read_bytes()) == manifest['raw_sha256'] == bench.PUBLIC_SHA256
    assert bench.bytes_ref((root / 'LICENSE.txt').read_bytes()) == manifest['license_sha256']
    assert bench.PUBLIC_REVISION in manifest['source_url']
    assert manifest['license'] == 'Apache-2.0'


@pytest.mark.parametrize('fault', ['metric','tool','authority','target','raw','source','extra'])
def test_resealing_changed_evidence_cannot_bypass_raw_rederivation(fault):
    bundle = bench.example_bundle()
    evidence = deepcopy(bundle['evidence'])
    if fault == 'metric': evidence['metrics'][0]['value'] = 999
    elif fault == 'tool': evidence['tool']['version'] = 'forged'
    elif fault == 'authority': evidence['authority']['physical_validation'] = 'established'
    elif fault == 'target': evidence['target']['serial_number'] = 'OTHER'
    elif fault == 'raw': evidence['raw_base64'] = base64.b64encode(b'{}').decode()
    elif fault == 'source': evidence['source_kind'] = 'operator_import'
    else: evidence['executable'] = 'forbidden'
    seal(evidence)
    with pytest.raises((ValueError, KeyError)):
        bench.validate_evidence(evidence, bundle['configuration'])


def test_operator_import_has_binding_but_no_authentication():
    bundle = operator_fixture()
    assert bundle['request']['observations']['source_kind'] == 'operator_import'
    assert hardware.assess(bundle['request'])['authority']['source_authenticity'] == 'not_established'
    bench.validate_assessment_binding(bundle['request'], [bundle['evidence']])
    bundle['request']['observations']['records'][0]['value'] = 7
    with pytest.raises(ValueError, match='exactly derived'):
        bench.validate_assessment_binding(bundle['request'], [bundle['evidence']])


@pytest.mark.parametrize('serial', ['REDACTED','UNKNOWN','NET-SYNTHETIC-001',None])
def test_operator_target_needs_explicit_nonfixture_identity(serial):
    bundle = operator_fixture()
    bundle['configuration']['components'][0]['serial_number'] = serial
    with pytest.raises(ValueError):
        bench.import_diagnostic(base64.b64decode(bundle['evidence']['raw_base64']), bundle['configuration'], 'storage-0',
            operator='fixture', captured_at='2026-01-01T00:00:00Z', procedure_id='snapshot')


@pytest.mark.parametrize('raw', [b'{}', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'\xff', b'[]', b'x'*32769], ids=['missing-fields','duplicate','nan','infinite','non-utf8','array','too-large'])
def test_malformed_or_unbounded_raw_is_refused(raw):
    bundle = operator_fixture()
    with pytest.raises((ValueError, UnicodeError)):
        bench.import_diagnostic(raw, bundle['configuration'], 'storage-0', operator='fixture',
            captured_at='2026-01-01T00:00:00Z', procedure_id='snapshot')


@pytest.mark.parametrize('exit_status,expected', [(1,'error'),(2,'error'),(4,'error'),(8,'available'),(16,'available'),(128,'available')])
def test_smart_command_failure_bits_do_not_hide_health_failure_bits(exit_status, expected):
    bundle = operator_fixture()
    raw = json.loads(base64.b64decode(bundle['evidence']['raw_base64']))
    raw['smartctl']['exit_status'] = exit_status
    raw['smart_status']['passed'] = False
    evidence = bench.import_diagnostic(json.dumps(raw).encode(), bundle['configuration'], 'storage-0',
        operator='fixture', captured_at='2026-01-01T00:00:00Z', procedure_id='snapshot')
    assert evidence['metrics'][0]['status'] == expected
    assert evidence['metrics'][0]['value'] == (None if expected == 'error' else 0)


def test_missing_smart_metrics_are_null_not_successful_zeroes():
    evidence = bench.example_evidence('missing')
    metric = next(item for item in evidence['metrics'] if item['metric_id']=='nvme-media-errors')
    assert metric == {'metric_id':'nvme-media-errors','unit':'count','value':None,'status':'unavailable'}


def test_smart_snapshot_cannot_be_relabelled_as_sustained_test():
    bundle = operator_fixture()
    with pytest.raises(ValueError, match='sustained'):
        bench.import_diagnostic(base64.b64decode(bundle['evidence']['raw_base64']), bundle['configuration'], 'storage-0',
            operator='fixture', captured_at='2026-01-01T00:00:00Z', procedure_id='snapshot', duration_s=600)


def test_strict_diagnostic_import_preserves_duration_and_error():
    bundle = operator_fixture()
    raw = {'schema':bench.DIAGNOSTIC_SCHEMA,'device':{k:bundle['evidence']['target'][k] for k in ('model','serial_number')},
           'tool':{'name':'fixture tester','version':'1'},'procedure_id':'diagnostic','duration_s':600,
           'metrics':[{'metric_id':'memory-errors','unit':'count','value':None,'status':'error'}]}
    evidence = bench.import_diagnostic(json.dumps(raw).encode(),bundle['configuration'],'storage-0',format='diagnostic-json',
        operator='fixture',captured_at='2026-01-01T00:00:00Z',procedure_id='diagnostic',duration_s=600)
    assert evidence['metrics'][0]['value'] is None
    assert evidence['duration_s']==600
    raw['duration_s']=True
    with pytest.raises(ValueError):
        bench.import_diagnostic(json.dumps(raw).encode(),bundle['configuration'],'storage-0',format='diagnostic-json',
            operator='fixture',captured_at='2026-01-01T00:00:00Z',procedure_id='diagnostic',duration_s=1)


def test_data_only_maintenance_plan_binds_target_and_expected_configuration():
    bundle = operator_fixture(); configuration = bundle['configuration']
    expected = deepcopy(configuration); expected['revision']='2'; expected['software']=[{'name':'OS','version':'declared-v2'}]
    plan = bench.maintenance_plan(configuration,'storage-0',action='provision',expected_configuration=expected,
                                  procedure_id='operator.provision.v1',operator='fixture')
    assert plan['destructive_operation'] is True and plan['execution']=='not_supported'
    assert all(item['status']=='pending' for item in plan['preconditions'])
    bench.validate_maintenance_plan(plan)
    forged = deepcopy(plan); forged['execution']='authorized'; seal(forged)
    with pytest.raises(ValueError): bench.validate_maintenance_plan(forged)
    expected['components'][0]['serial_number']='OTHER'
    with pytest.raises(ValueError,match='target device identity'):
        bench.maintenance_plan(configuration,'storage-0',action='sanitize',expected_configuration=expected,
                               procedure_id='operator.erase.v1',operator='fixture')


def test_imported_maintenance_result_checks_binding_without_certifying_erase():
    bundle=operator_fixture(); configuration=bundle['configuration']
    plan=bench.maintenance_plan(configuration,'storage-0',action='sanitize',expected_configuration=configuration,
                                procedure_id='operator.erase.v1',operator='fixture')
    value={'schema':'ciw.hardware-maintenance-result.v1','plan_ref':plan['record_digest'],
           **{k:plan[k] for k in ('target','configuration_ref','expected_configuration_ref','action','procedure_id')},
           'operator':'fixture','performed_at':'2026-01-01T00:00:00Z','tool':{'name':'external fixture','version':'1'},
           'status':'completed','verification':{'status':'not_performed','evidence_ref':None}}
    record=bench.import_maintenance_result(json.dumps(value).encode(),plan)
    bench.validate_maintenance_result(record,plan)
    assert record['authority']['data_sanitization']=='not_performed'
    value['target']['serial_number']='OTHER'
    with pytest.raises(ValueError): bench.import_maintenance_result(json.dumps(value).encode(),plan)


def test_inventory_unavailable_is_explicit_and_never_executes_user_paths(monkeypatch):
    monkeypatch.setattr(bench.platform,'system',lambda:'Windows')
    monkeypatch.setattr(bench,'_trusted_tool',lambda name:None)
    monkeypatch.setattr(bench,'_bounded_command',lambda *a,**kw:pytest.fail('unexpected execution'))
    record=bench.capture_inventory('asset','operator')
    assert record['status']=='unavailable' and record['tool'] is None
    assert record['groups']=={} and record['raw_base64']==''


def test_inventory_keeps_null_field_status_and_does_not_infer_hardware_pass(monkeypatch,tmp_path):
    executable=tmp_path/'powershell.exe';executable.write_bytes(b'fixture-executable')
    monkeypatch.setattr(bench.platform,'system',lambda:'Windows')
    monkeypatch.setattr(bench,'_trusted_tool',lambda name:executable)
    raw=json.dumps({'storage':{'status':'available','rows':[{'Size':None,'SerialNumber':'fixture-private'}]}}).encode()
    monkeypatch.setattr(bench,'_bounded_command',lambda command:{'status':'available','reason':None,'returncode':0,'raw':raw})
    record=bench.capture_inventory('asset','operator')
    assert record['groups']['storage']['rows'][0]['Size']=={'status':'unavailable','value':None}
    assert record['sensitive_identifiers']=='local_only'
    assert record['authority']['physical_validation']=='not_established'


@pytest.mark.parametrize('device',['--test=long','/dev/sda;rm','../disk','C:','/dev/tw_cli/stdin'])
def test_smart_path_cannot_inject_options_or_select_alternate_executors(monkeypatch,device):
    monkeypatch.setattr(bench,'_trusted_tool',lambda *a:pytest.fail('must validate first'))
    with pytest.raises(ValueError): bench.capture_smart(operator_fixture()['configuration'],'storage-0',device,'fixture')


def test_smart_missing_tool_is_unavailable_without_installation_or_device_io(monkeypatch):
    monkeypatch.setattr(bench,'_trusted_tool',lambda name:None)
    record=bench.capture_smart(operator_fixture()['configuration'],'storage-0','/dev/sda','fixture')
    assert record['status']=='unavailable' and record['evidence'] is None


def test_bounded_child_captures_output_limit_and_timeout():
    limited=bench._bounded_command([sys.executable,'-I','-c',"import sys;sys.stdout.write('x'*100000)"],maximum=1024)
    assert limited['status']=='error' and limited['reason']=='output_limit' and len(limited['raw'])==1024
    timed=bench._bounded_command([sys.executable,'-I','-c',"import time;print('started',flush=True);time.sleep(10)"],timeout=0.1)
    assert timed['status']=='error' and timed['reason']=='timeout'


def test_verification_never_uses_executable_fields_in_recorded_smart_input(monkeypatch):
    monkeypatch.setattr(bench,'_bounded_command',lambda *a,**kw:pytest.fail('must not execute recorded argv'))
    bundle=bench.example_bundle()
    bench.validate_evidence(bundle['evidence'],bundle['configuration'])
    bench.validate_assessment_binding(bundle['request'],[bundle['evidence']])


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows system collector integration')
def test_windows_fixed_system_collector_has_its_required_modules(monkeypatch):
    # Read-only host inventory; never print or retain its sensitive identifiers.
    monkeypatch.setenv('PSModulePath', 'nonexistent-untrusted-module-path')
    record = bench.capture_inventory('INVENTORY-TEST', 'automated local collector test')
    assert record['status'] == 'available', record['reason']
    assert set(record['groups']) == {'system','os','processors','memory','storage'}
    assert any(group['status'] == 'available' for group in record['groups'].values())


def test_all_failed_inventory_categories_do_not_report_success(monkeypatch,tmp_path):
    executable=tmp_path/'powershell.exe';executable.write_bytes(b'fixture')
    monkeypatch.setattr(bench.platform,'system',lambda:'Windows')
    monkeypatch.setattr(bench,'_trusted_tool',lambda name:executable)
    raw=json.dumps({'storage':{'status':'error','rows':[],'reason':'row_limit'}}).encode()
    monkeypatch.setattr(bench,'_bounded_command',lambda command:{'status':'available','reason':None,'returncode':0,'raw':raw})
    record=bench.capture_inventory('asset','operator')
    assert record['status']=='error'
    assert record['groups']['storage']['reason']=='row_limit'
