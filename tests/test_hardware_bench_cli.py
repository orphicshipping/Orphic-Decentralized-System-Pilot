"""Portable bench command journeys without physical qualification claims."""
import base64
import json

import pytest

from ciw import hardware_bench as bench, hardware_bench_cli as cli, hardware_cli
from ciw.control_contracts import save_new


@pytest.mark.parametrize('case,expected',[('public',0),('pass',0),('fail',2),('missing',3),('stale',1)])
def test_offline_example_flows_into_existing_hardware_assessment(tmp_path,capsys,case,expected):
    directory=tmp_path/case
    assert cli.main(['example','--case',case,'--output-dir',str(directory)])==0
    assert cli.main(['verify',str(directory/'evidence.json'),'--configuration',str(directory/'configuration.json')])==0
    assert hardware_cli.main(['run',str(directory/'request.json'),'--output-dir',str(tmp_path/'run')])==expected
    output=capsys.readouterr()
    assert 'synthetic' in output.out
    before=(directory/'evidence.json').read_bytes()
    assert cli.main(['example','--case',case,'--output-dir',str(directory)])==1
    assert (directory/'evidence.json').read_bytes()==before


def test_request_command_reconstructs_original_bound_request(tmp_path):
    directory=tmp_path/'example'
    assert cli.main(['example','--output-dir',str(directory)])==0
    target=tmp_path/'request.json'
    assert cli.main(['request','--configuration',str(directory/'configuration.json'),'--plan',str(directory/'plan.json'),
                     '--evidence',str(directory/'evidence.json'),'--mappings',str(directory/'mappings.json'),'--output',str(target)])==0
    assert json.loads(target.read_text())==json.loads((directory/'request.json').read_text())


def test_inventory_cli_saves_identifiers_locally_and_prints_only_summary(tmp_path,monkeypatch,capsys):
    record={'schema':bench.INVENTORY_SCHEMA,'status':'available','record_digest':'sha256:'+'1'*64,
            'private_serial':'DO-NOT-PRINT-LOCAL-ID'}
    monkeypatch.setattr(bench,'capture_inventory',lambda *args:record)
    output=tmp_path/'inventory.json'
    assert cli.main(['inventory','--asset-id','local','--operator','local operator','--output',str(output)])==0
    captured=capsys.readouterr()
    assert 'DO-NOT-PRINT-LOCAL-ID' not in captured.out+captured.err
    assert json.loads(output.read_text())['private_serial']=='DO-NOT-PRINT-LOCAL-ID'


def test_smart_unavailable_retains_diagnostic_status_without_reading_device(tmp_path,monkeypatch,capsys):
    configuration=bench.example_bundle()['configuration']
    configuration['components'][0]['serial_number']='TEST-OPERATOR-001'
    source=tmp_path/'configuration.json';save_new(source,configuration)
    monkeypatch.setattr(bench,'_trusted_tool',lambda name:None)
    output=tmp_path/'capture.json'
    assert cli.main(['smart','--configuration',str(source),'--slot','storage-0','--device','/dev/sda',
                     '--operator','test fixture','--output',str(output)])==3
    assert json.loads(output.read_text())['status']=='unavailable'
    assert 'TEST-OPERATOR-001' not in capsys.readouterr().out


def test_import_and_plan_commands_are_bound_and_non_actuating(tmp_path):
    bundle=bench.example_bundle()
    configuration=bundle['configuration'];configuration['components'][0]['serial_number']='TEST-OPERATOR-001'
    document=json.loads(base64.b64decode(bundle['evidence']['raw_base64']))
    document['serial_number']='TEST-OPERATOR-001'
    config=tmp_path/'configuration.json';save_new(config,configuration)
    raw=tmp_path/'smart.json';save_new(raw,document)
    evidence=tmp_path/'evidence.json'
    assert cli.main(['import',str(raw),'--configuration',str(config),'--slot','storage-0','--operator','test fixture',
                     '--captured-at','2026-01-01T00:00:00Z','--procedure-id','snapshot','--output',str(evidence)])==0
    assert cli.main(['verify',str(evidence),'--configuration',str(config)])==0
    plan=tmp_path/'maintenance.json'
    assert cli.main(['plan','--configuration',str(config),'--expected-configuration',str(config),'--slot','storage-0',
                     '--action','sanitize','--procedure-id','operator.erase.v1','--operator','test fixture','--output',str(plan)])==0
    assert json.loads(plan.read_text())['execution']=='not_supported'
    assert cli.main(['verify',str(plan)])==0


def test_verify_refuses_missing_configuration_and_unrecognized_schema(tmp_path):
    path=tmp_path/'evidence.json';save_new(path,bench.example_evidence())
    assert cli.main(['verify',str(path)])==1
    unknown=tmp_path/'unknown.json';save_new(unknown,{'schema':'unknown','command':'erase'})
    assert cli.main(['verify',str(unknown)])==1
