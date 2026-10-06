"""Type-exact outcomes, retained evidence after refusal and operator-held anchors."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from ciw import device_review as review, device_review_cli as cli, control_checks
from ciw.control_contracts import bytes_ref
from ciw.operations.runner import seal
from ciw.session import Session
from ciw.agent_mcp import Server
from test_device_review import fixture, retained, hashes, initialize, rpc


def _forge(directory, change):
    path = directory / 'workspace.json'
    workspace = json.loads(path.read_bytes())
    result = workspace['results'][0]
    change(result['data']['comparison']['outcome'])
    seal(result['data']['comparison'])
    seal(result)
    path.write_text(json.dumps(workspace))


@pytest.mark.parametrize('field', ['sample_count', 'component_count'])
def test_forged_integer_counts_do_not_accept_float_equality(retained, field):
    directory, _ = retained
    _forge(directory, lambda out: out.update({field: float(out[field])}))
    with pytest.raises(ValueError):
        review.inspect(directory)


@pytest.mark.parametrize('field', ['sample_count', 'component_count'])
def test_forged_unknown_counts_do_not_accept_boolean_zero(fixture, tmp_path, field):
    source, reference, request = fixture
    reference['observations'] = []
    seal(reference)
    directory = tmp_path / 'empty-reference'
    report = review.run(source, reference, request, directory)
    assert report['data']['comparison']['outcome']['status'] == 'INDETERMINATE'
    _forge(directory, lambda out: out.update({field: False}))
    with pytest.raises(ValueError):
        review.inspect(directory)


@pytest.mark.parametrize('field', ['rmse', 'max_abs_error', 'max_tolerance_excess'])
@pytest.mark.parametrize('forged', [False, 0], ids=['boolean', 'integer'])
def test_forged_zero_metrics_keep_their_native_float_type(fixture, tmp_path, field, forged):
    source, reference, request = fixture
    native = review.link_workflow.inspect(source)['data']['projection']['native_stream']['observations']
    for left, right in zip(native, reference['observations']):
        right['value'] = left['value']
        seal(right)
    seal(reference)
    request['atol'] = 0.0
    directory = tmp_path / 'exact-match'
    report = review.run(source, reference, request, directory)
    assert type(report['data']['comparison']['outcome']['metrics'][field]) is float
    _forge(directory, lambda out: out['metrics'].update({field: forged}))
    with pytest.raises(ValueError):
        review.inspect(directory)


@pytest.fixture
def refused(fixture, tmp_path):
    source, reference, request = fixture
    directory = tmp_path / 'refused-comparison'
    with patch.object(control_checks, 'compare', side_effect=RuntimeError('injected computation failure')):
        report = review.run(source, reference, request, directory)
    assert report['status'] == 'REFUSED' and report['result_id'] is None
    return directory, reference, report


@pytest.mark.parametrize('tool', ['net_device_observations', 'net_device_reference'])
def test_original_rows_survive_refusal_in_readonly_mcp(refused, tool):
    directory, reference, original = refused
    before = hashes(directory)
    with patch.object(control_checks, 'compare', side_effect=AssertionError('must not recompute')):
        host = cli.ReviewHost(directory)
        response = host.call(tool, {'limit': 2})
    assert response['count'] == len(reference['observations']) == 3
    assert len(response['items']) == 2 and response['next_offset'] == 2
    assert response['comparison_execution_status'] == 'refused'
    assert response['comparison_result_id'] is None
    assert response['authority']['verification_id'] is None
    assert response['authority']['equipment_control'] is False
    assert hashes(directory) == before
    assert host.call('net_device_review', {})['refusal'] == original['refusal']


@pytest.mark.parametrize('tool', ['net_device_observations', 'net_device_reference'])
def test_refused_pages_are_exact_retained_sources(refused, tool):
    directory, reference, _ = refused
    host = cli.ReviewHost(directory)
    response = host.call(tool, {})
    expected = (reference['observations'] if tool.endswith('reference') else
                review.link_workflow.inspect(directory/'link')['data']['projection']['native_stream']['observations'])
    assert response['items'] == expected
    response['items'][0]['value'] = 1000000
    assert host.call(tool, {})['items'] == expected
    server = Server(host)
    initialize(server)
    result = rpc(server, 'tools/call', {'name': tool, 'arguments': {}}, 2)['result']
    # Reading retained evidence succeeds, while computation status remains refused.
    assert result['isError'] is False
    assert result['structuredContent']['comparison_execution_status'] == 'refused'


def test_genuinely_empty_reference_is_distinct_from_failed_computation(fixture, tmp_path):
    source, reference, request = fixture
    reference['observations'] = []
    seal(reference)
    directory = tmp_path/'no-reference-rows'
    review.run(source, reference, request, directory)
    host = cli.ReviewHost(directory)
    assert host.call('net_device_observations', {})['count'] == 3
    response = host.call('net_device_reference', {})
    assert response['count'] == 0
    assert response['comparison_execution_status'] == 'completed'
    assert response['comparison_result_id'] is not None
    assert host.call('net_device_review', {})['outcome']['status'] == 'INDETERMINATE'


@pytest.mark.parametrize('function', ['inspect', 'replay', 'mcp'])
def test_matching_operator_content_anchor(retained, tmp_path, function):
    directory, original = retained
    anchor = original['snapshot_ref']
    if function == 'replay':
        response = review.replay(directory, tmp_path/'anchored-replay', expected_snapshot_ref=anchor)
        assert response['review_evidence_id'] == original['review_evidence_id']
        assert response['execution_id'] != original['execution_id']
    else:
        response = (review.inspect(directory, expected_snapshot_ref=anchor) if function == 'inspect' else
                    cli.ReviewHost(directory, expected_snapshot_ref=anchor).call('net_device_review', {}))
        assert response['content_anchor_check'] == {'status':'MATCHED', 'expected':anchor, 'authenticates_origin':False}
    assert response['authority']['verification_id'] is None


@pytest.mark.parametrize('function', ['inspect', 'replay', 'mcp'])
def test_wrong_anchor_refuses_before_parent_restore_or_destination(retained, tmp_path, function):
    directory, _ = retained
    before = hashes(directory)
    kwargs = dict(expected_snapshot_ref='sha256:'+'0'*64)
    with patch.object(review, '_link_snapshot', side_effect=AssertionError('must check anchor first')):
        with pytest.raises(ValueError, match='snapshot.*anchor'):
            if function == 'replay':
                review.replay(directory, tmp_path/'blocked-replay', **kwargs)
            elif function == 'mcp':
                cli.ReviewHost(directory, **kwargs)
            else:
                review.inspect(directory, **kwargs)
    assert not (tmp_path/'blocked-replay').exists()
    assert hashes(directory) == before


@pytest.mark.parametrize('anchor', [False, 3, '', 'bad', 'sha256:'+'F'*64, 'sha256:'+'0'*63])
def test_invalid_external_anchor_refused(retained, anchor):
    directory, _ = retained
    with pytest.raises(ValueError):
        review.inspect(directory, expected_snapshot_ref=anchor)


def test_anchor_detects_format_change_without_changing_scientific_identity(retained):
    directory, old = retained
    original = (directory/'workspace.json').read_bytes()
    (directory/'workspace.json').write_bytes(original+b'\n')
    current = review.inspect(directory)
    assert current['review_evidence_id'] == old['review_evidence_id']
    assert current['result_id'] == old['result_id']
    assert current['snapshot_ref'] != old['snapshot_ref']
    assert current['content_anchor_check']['status'] == 'NOT_REQUESTED'
    with pytest.raises(ValueError, match='snapshot.*anchor'):
        review.inspect(directory, expected_snapshot_ref=old['snapshot_ref'])
    assert review.inspect(directory, expected_snapshot_ref=current['snapshot_ref'])['data'] == current['data']


def test_mcp_reads_one_snapshot_and_never_accepts_anchor_from_tool_arguments(retained):
    directory, original = retained
    with patch.object(review, '_read', wraps=review._read) as reads:
        host = cli.ReviewHost(directory, expected_snapshot_ref=original['snapshot_ref'])
    assert reads.call_count == 1
    response = host.call('net_device_review', {})
    (directory/'workspace.json').write_text('{}')
    assert host.call('net_device_review', {}) == response
    assert host.call('net_device_observations', {})['count'] == 3
    for tool in ('net_device_review','net_device_observations','net_device_reference'):
        with pytest.raises(ValueError):
            host.call(tool, {'expected_snapshot_ref':original['snapshot_ref']})


def test_direct_saved_workspace_rejects_type_forgery_before_restore_write(retained, tmp_path):
    directory, _ = retained
    _forge(directory, lambda out: out.update(sample_count=3.0))
    target = tmp_path/'bad-restore'
    with pytest.raises(ValueError):
        Session.from_workspace(directory/'workspace.json', target)
    assert not target.exists()


def test_anchored_cli_inspection_and_mismatch_exit_contract(retained):
    directory, original = retained
    cmd = [sys.executable, '-m','ciw.device_review_cli','inspect',str(directory),'--expected-snapshot-ref']
    ok = subprocess.run(cmd+[original['snapshot_ref']], capture_output=True, text=True, timeout=20)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)['content_anchor_check']['status'] == 'MATCHED'
    fail = subprocess.run(cmd+['sha256:'+'0'*64], capture_output=True,text=True,timeout=20)
    assert fail.returncode == 2 and fail.stdout == ''
    assert json.loads(fail.stderr)['status'] == 'REFUSED'


def test_actual_mcp_subprocess_retains_rows_after_refusal(refused):
    directory, _, original = refused
    messages = [
        {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'hardening-test','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_device_observations','arguments':{}}},
        {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'net_device_reference','arguments':{}}},
        {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'net_execute','arguments':{}}},
    ]
    cmd=[sys.executable,'-m','ciw.device_review_cli','mcp',str(directory),'--expected-snapshot-ref',original['snapshot_ref']]
    run=subprocess.run(cmd,input='\n'.join(json.dumps(m) for m in messages)+'\n',capture_output=True,text=True,timeout=20)
    assert run.returncode == 0,run.stderr
    replies=[json.loads(line) for line in run.stdout.splitlines()]
    assert len(replies)==4
    for reply in replies[1:3]:
        assert reply['result']['structuredContent']['count']==3
        assert reply['result']['structuredContent']['comparison_execution_status']=='refused'
    assert replies[-1]['result']['isError'] is True
