"""Operator surface checks over real Session work; no qualified instrument fixtures."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest

from ciw import link_contract as c, link_inspection_cli as ui, link_study as s, link_study_io as io
from ciw.link_study_cli import example


@pytest.fixture
def study(tmp_path):
    request = example()
    root = tmp_path / 'inspection'
    summary = ui.run(request, root)
    return root, request, summary


def test_one_action_real_existing_graph_and_product_card(study):
    root, request, summary = study
    assert summary['capability'] == 'Digital Inspection'
    assert summary['status'] == 'NEEDS_QUALIFICATION'
    assert summary['reading'] == {'kind':'declared_corrected_mean',
                                  'value':io.inspect(root)['results'][s.OPS[2]]['data']['mean'],'unit':'N','count':3}
    assert summary['reading']['value'] == pytest.approx(2.03, abs=1e-14)
    assert summary['checks']['arithmetic'] == 'PASS'
    assert summary['checks']['acquisition_time'] == 'UNKNOWN'
    assert summary['qualification'] == 'HELD'
    assert set(io.inspect(root)['results']) == set(s.OPS)
    assert 'advanced' not in summary
    assert (root/'inspection.html').is_file()
    assert summary['report']['sha256'] == sha256((root/'inspection.html').read_bytes()).hexdigest()
    assert all(v is False for v in summary['authority'].values())


def test_tvalue_is_not_confused_with_processing_time(study):
    root, _, summary = study
    record = json.loads((root/'operator-processing.json').read_bytes())
    assert type(record['recorded_processing_duration_ns']) is int
    assert record['recorded_processing_duration_ns'] >= 0
    assert record['clock'] == 'host_monotonic'
    assert record['first_qualified_measurement_ns'] is None
    assert summary['time_to_value']['first_qualified_measurement_ns'] is None
    assert record['request_ref'] == summary['reference']
    assert 'install' in record['scope']


def test_status_is_not_granted_by_forged_ui_or_processing_record(study):
    root, _, summary = study
    (root/'operator-processing.json').write_text('{"first_qualified_measurement_ns":1,"status":"QUALIFIED"}')
    (root/'inspection.html').write_text('QUALIFIED')
    checked = ui.view(root, request_ref=summary['reference'])
    assert checked['status'] == 'NEEDS_QUALIFICATION'
    assert checked['qualification'] == 'HELD'
    assert checked['time_to_value']['first_qualified_measurement_ns'] is None
    assert 'processing' not in checked


def test_status_offline_no_restored_authority(study, monkeypatch):
    root, _, summary = study
    def forbidden(*a, **kw): raise AssertionError('No network in inspection')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    output = ui.view(root, request_ref=summary['reference'], advanced=True)
    assert all(v is False for v in output['authority'].values())
    assert len(output['advanced']['executions']) == 4
    assert before == {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_default_console_is_small_and_advanced_is_optional(tmp_path,capsys):
    root=tmp_path/'demo'
    assert ui.main(['demo','--output-dir',str(root)]) == 0
    output=capsys.readouterr().out
    assert len(output.splitlines()) <= 8
    assert 'SYNTHETIC' in output and 'qualification HELD' in output
    assert 'execution_id' not in output and 'covariance' not in output
    ref=ui.view(root)['reference']
    assert ui.main(['status',str(root),'--request-ref',ref,'--advanced']) == 0
    value=json.loads(capsys.readouterr().out)
    assert 'advanced' in value and len(value['advanced']['executions']) == 4


@pytest.mark.parametrize('bad_ref',['sha256:'+'0'*64,'garbage',''])
def test_run_wrong_external_selection_refuses_before_creating_output(tmp_path,bad_ref):
    raw=c.bounded(example(),s.MAX_BYTES);(tmp_path/'request.json').write_bytes(raw)
    assert ui.main(['run',str(tmp_path/'request.json'),'--request-ref',bad_ref,
                    '--output-dir',str(tmp_path/'refused')]) == 2
    assert not (tmp_path/'refused').exists()


def test_exact_prepared_request_bytes_are_preserved(tmp_path,capsys):
    raw=json.dumps(example(),indent=3).encode()+b'\n'
    (tmp_path/'request.json').write_bytes(raw)
    root=tmp_path/'run'
    assert ui.main(['run',str(tmp_path/'request.json'),'--request-ref',c.bytes_ref(raw),
                    '--output-dir',str(root),'--advanced']) == 0
    assert (root/'request.json').read_bytes()==raw
    result=json.loads(capsys.readouterr().out)
    assert result['reference']==c.bytes_ref(raw) and 'advanced' in result


def test_changed_nominal_reference_yields_review_not_release(tmp_path):
    request=example(); request['model']['expected_mean']=0
    summary=ui.run(request,tmp_path/'outside')
    assert summary['status']=='REVIEW_REQUIRED'
    assert summary['checks']['nominal_comparison']=='OUTSIDE'
    assert summary['checks']['arithmetic']=='PASS'
    assert summary['qualification']=='HELD'
    assert 'not a qualified reject' in summary['next_action']


def test_missing_uncertainty_and_calibration_are_not_hidden(tmp_path):
    request=example(); request['calibration']['declaration']=None
    request['calibration']['joint_covariance']=None
    result=ui.run(request,tmp_path/'unknown')
    assert result['checks']['uncertainty']=='NOT_SUPPLIED'
    assert result['checks']['calibration']=='MISSING_DECLARATION'
    assert result['qualification']=='HELD'


def test_refused_affine_step_retains_raw_and_no_model(tmp_path):
    request=example(); request['calibration']['input_range']=[0,0.5]
    request['calibration']['joint_covariance']=None
    root=tmp_path/'refused-model'
    result=ui.run(request,root)
    assert result['status']=='INCOMPLETE' and result['reading']['value'] is None
    assert (root/'request.json').is_file() and (root/'workspace.json').is_file()
    assert result['checks']['arithmetic']=='NOT_RUN'
    assert 'NOT LIVE' in (root/'inspection.html').read_text()


def test_source_tamper_refuses_instead_of_reusing_ui_status(study):
    root,request,summary=study
    modified=deepcopy(request);modified['model']['expected_mean']=9
    (root/'request.json').write_bytes(c.bounded(modified,s.MAX_BYTES))
    with pytest.raises(ValueError):ui.view(root,request_ref=summary['reference'])
    with pytest.raises(ValueError):ui.view(root)


def test_no_clobber_existing_investigation(study):
    root,request,_=study
    before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    with pytest.raises(FileExistsError):ui.run(request,root)
    assert before=={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_panel_offline_escaped_and_advanced_collapsed(study):
    root,_,_=study
    text=(root/'inspection.html').read_text()
    assert '<details><summary>Advanced:' in text and '<details open' not in text
    assert "default-src 'none'" in text and '<script' not in text
    assert 'http://' not in text and 'https://' not in text
    assert 'Qualified measurement: not yet available.' in text
    assert 'SYNTHETIC' in text and 'HELD' in text
    assert 'No part release' in text


def test_net_link_delegates_to_same_product_entrypoint(tmp_path,capsys):
    from ciw.link_cli import main
    assert main(['inspection','demo','--output-dir',str(tmp_path/'entry')])==0
    assert 'Digital Inspection' in capsys.readouterr().out
