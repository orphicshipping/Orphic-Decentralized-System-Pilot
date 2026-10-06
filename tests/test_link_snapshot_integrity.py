"""Use the exact bytes checked, and never headline a failed arithmetic result.

Every investigation and MCP host below uses the real Session and validators.
File replacement and primary-arithmetic faults are explicit synthetic injections.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from ciw import link_contract as c, link_study as study, link_study_io as io
from ciw import link_inspection_cli as ui
from ciw.link_study_cli import example


@pytest.fixture
def pair(tmp_path):
    first = example()
    first['calibration']['joint_covariance'] = None
    second = deepcopy(first)
    second['calibration']['gain'] = 2.0
    root, other = tmp_path/'first', tmp_path/'second'
    a = io.run(first, root)
    b = io.run(second, other)
    return root, other, a, b


def test_input_anchor_cannot_check_one_record_then_display_another(pair, monkeypatch):
    root, other, a, b = pair
    original = io.read_bytes
    calls = []
    def read_and_replace(path, **kwargs):
        raw = original(path, **kwargs)
        if Path(path) == root/'request.json':
            calls.append('request')
            if len(calls) == 1:
                # A complete valid B replaces A after A's bytes were fetched.
                for name in ('request.json', 'workspace.json', 'graph.json'):
                    (root/name).write_bytes((other/name).read_bytes())
        return raw
    monkeypatch.setattr(io, 'read_bytes', read_and_replace)
    monkeypatch.setattr(ui, 'read_bytes', read_and_replace)
    with pytest.raises(ValueError):
        ui.view(root, request_ref=a['request_ref'])
    assert calls == ['request']


def test_mcp_freezes_the_workspace_bytes_it_actually_validated(pair, monkeypatch):
    root, other, a, b = pair
    original = io.read_bytes
    checked = (root/'workspace.json').read_bytes()
    calls = []
    def read_and_replace(path, **kwargs):
        raw = original(path, **kwargs)
        if Path(path) == root/'workspace.json':
            calls.append('workspace')
            if len(calls) == 1:
                (root/'workspace.json').write_bytes((other/'workspace.json').read_bytes())
        return raw
    monkeypatch.setattr(io, 'read_bytes', read_and_replace)
    with io.agent(root) as host:
        assert host.capabilities()['inputs']['investigation']['sha256'] == c.bytes_ref(checked)
        result_id = a['results'][study.OPS[2]]['result_id']
        assert host.inspect('investigation', ['results', result_id, 'data', 'mean'])['data'] == a['results'][study.OPS[2]]['data']['mean']
        assert host.capabilities()['host_policy']['allowed_operations'] == []
    assert calls == ['workspace']


def test_failed_audit_withholds_default_readout_but_keeps_original_candidate(tmp_path, monkeypatch):
    original = study._transport
    monkeypatch.setattr(study, '_transport', lambda matrix,jac: [[2*v for v in row] for row in original(matrix,jac)])
    root = tmp_path/'fault'
    result = io.run(example(), root)
    assert result['results'][study.OPS[-1]]['data']['status'] == 'FAIL'
    before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    card = ui.view(root, advanced=True)
    assert card['reading']['value'] is None
    assert card['checks']['nominal_comparison'] == 'WITHHELD_FAILED_AUDIT'
    assert card['checks']['uncertainty'] == 'WITHHELD_FAILED_AUDIT'
    assert 'failed' in card['next_action'].lower()
    assert card['advanced']['results'] == result['results']
    assert card['qualification'] == 'HELD'
    assert before == {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    io.report(root, tmp_path/'fault-report.html')
    report = (tmp_path/'fault-report.html').read_text()
    assert '<div class="readout">UNKNOWN N</div>' in report
    assert 'WITHHELD' in report


@pytest.mark.parametrize('function', ['read','inspect','agent','replay','export','report'])
def test_wrong_anchor_refuses_each_reader_before_output_or_host(pair, tmp_path, function):
    root, other, a, b = pair
    destination = tmp_path/('blocked-'+function)
    call = getattr(io, function)
    kwargs = {'expected_request_ref': b['request_ref']}
    with pytest.raises(ValueError, match='externally selected request'):
        call(root, destination, **kwargs) if function in {'replay','export','report'} else call(root, **kwargs)
    assert not destination.exists()


@pytest.mark.parametrize('bad', ['', 'garbage', 'sha256:'+'0'*63, 42, True])
def test_malformed_anchor_is_not_silently_optional(pair, monkeypatch, bad):
    root, _, _, _ = pair
    monkeypatch.setattr(io, 'read_bytes', lambda *a, **k: pytest.fail('read before validating anchor'))
    with pytest.raises(ValueError): io.inspect(root, expected_request_ref=bad)


def test_valid_anchor_does_not_change_evidence_or_occurrences(pair, tmp_path):
    root, _, a, _ = pair
    checked = io.inspect(root, expected_request_ref=a['request_ref'])
    assert checked == io.inspect(root)
    original = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    replay = io.replay(root, tmp_path/'replay', expected_request_ref=a['request_ref'])
    assert replay['evidence_id'] == a['evidence_id']
    assert set(replay['executions']).isdisjoint(a['executions'])
    io.export(root, tmp_path/'native', expected_request_ref=a['request_ref'])
    assert original == {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_anchor_is_exact_request_bytes_not_normalized_json(pair):
    root, _, a, _ = pair
    raw = (root/'request.json').read_bytes()
    value = json.loads(raw)
    equivalent = (json.dumps(value, indent=3)+'\n').encode()
    assert raw != equivalent
    (root/'request.json').write_bytes(equivalent)
    with pytest.raises(ValueError): io.inspect(root, expected_request_ref=a['request_ref'])


def test_read_legacy_tuple_contract_preserved(pair):
    root, _, a, _ = pair
    result = io.read(root)
    assert type(result) is tuple and len(result) == 4
    assert c.bytes_ref(result[1]) == a['request_ref']
    assert set(result[2].executions) == set(a['executions'])


def test_mcp_frozen_inputs_remain_readonly_after_source_removed(pair, monkeypatch):
    root, _, a, _ = pair
    with io.agent(root, expected_request_ref=a['request_ref']) as host:
        mid = a['results'][study.OPS[2]]['result_id']
        for name in ('request.json','workspace.json','graph.json'): (root/name).unlink()
        def forbidden(*args, **kwargs): pytest.fail('inspection performed transport or original-file read')
        monkeypatch.setattr(io, 'read_bytes', forbidden)
        monkeypatch.setattr(socket, 'create_connection', forbidden)
        result = host.inspect('investigation',['results',mid,'data','mean'])
        assert result['data'] == a['results'][study.OPS[2]]['data']['mean']
        result['data'] = 12345
        assert host.inspect('investigation',['results',mid,'data','mean'])['data'] != 12345
        assert host.capabilities()['host_policy']['allowed_operations'] == []


@pytest.mark.parametrize('command', ['inspect','replay','export','report','mcp'])
def test_study_cli_wrong_anchor_refuses_without_any_output(pair,tmp_path,capsys,command):
    from ciw.link_study_cli import main
    root, _, a, _ = pair
    args = [command,str(root),'--request-ref','sha256:'+'0'*64]
    destination=tmp_path/('cli-'+command)
    if command in {'replay','export'}: args += ['--output-dir',str(destination)]
    if command == 'report': args += ['--output',str(destination)]
    assert main(args) == 2
    assert not destination.exists()
    result=json.loads(capsys.readouterr().err)
    assert result['status']=='REFUSED'


def test_actual_mcp_stdio_accepts_anchored_source_and_denies_execution(pair):
    root, _, a, _ = pair
    mid = a['results'][study.OPS[2]]['result_id']
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'snapshot-test','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_inspect','arguments':{'artifact':'investigation','selector':['results',mid,'data','mean']}}},
        {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'net_execute','arguments':{'source':'investigation','graph':'other','attempt':'test'}}}]
    proc=subprocess.run([sys.executable,'-m','ciw.link_study_cli','mcp',str(root),'--request-ref',a['request_ref']],
        input=b''.join(json.dumps(x).encode()+b'\n' for x in messages),capture_output=True,timeout=20)
    assert proc.returncode==0,proc.stderr.decode()
    lines=[json.loads(x) for x in proc.stdout.splitlines()]
    assert lines[1]['result']['structuredContent']['data']==a['results'][study.OPS[2]]['data']['mean']
    assert lines[2]['result']['isError'] is True


def test_frontdoor_run_preserves_selection_after_initial_processing(pair,tmp_path,monkeypatch):
    root, other, a, b=pair
    original=io.run
    destination=tmp_path/'fresh'
    def run_then_replace(request, output, **kwargs):
        result=original(request,output,**kwargs)
        for name in ('request.json','workspace.json','graph.json'):
            (Path(output)/name).write_bytes((other/name).read_bytes())
        return result
    monkeypatch.setattr(io,'run',run_then_replace)
    with pytest.raises(ValueError,match='externally selected request'):
        ui.run(json.loads((root/'request.json').read_bytes()), destination)
    assert not (destination/'inspection.html').exists()


def test_advanced_details_cannot_switch_input_after_summary(pair,tmp_path,monkeypatch,capsys):
    root, other, a, b=pair
    original=ui.run
    destination=tmp_path/'frontdoor'
    def run_then_replace(request,output,**kwargs):
        result=original(request,output,**kwargs)
        for name in ('request.json','workspace.json','graph.json'):
            (Path(output)/name).write_bytes((other/name).read_bytes())
        return result
    monkeypatch.setattr(ui,'run',run_then_replace)
    assert ui.main(['demo','--output-dir',str(destination),'--advanced'])==2
    captured=capsys.readouterr()
    assert captured.out==''
    assert json.loads(captured.err)['status']=='REFUSED'


def test_panel_anchor_refuses_before_output_creation(pair,tmp_path):
    root, other, a, b=pair
    with pytest.raises(ValueError,match='externally selected request'):
        ui.panel(root,tmp_path/'wrong.html',request_ref=b['request_ref'])
    assert not (tmp_path/'wrong.html').exists()
