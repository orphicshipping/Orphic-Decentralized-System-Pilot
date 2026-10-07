"""Retained source inspection precedes, and never authorizes, scientific work."""
from copy import deepcopy
import importlib
import json

import pytest

from ciw import engineering_thread as engineering, engineering_thread_io as thread
from ciw import link_contract as c, link_study as study
from ciw.link_protocol import decode_one, encode
from ciw.link_study_cli import example
from ciw.session import Session
from test_engineering_thread import raw
from test_ncp_next_actions import snapshot


def inspect(data, ref=None, profile='scalar'):
    return importlib.import_module('ciw.engineering_source').inspect_source(
        data, expected_request_ref=c.bytes_ref(data) if ref is None else ref,
        host_profile=profile)


def change_observation(request, index=0, **updates):
    request = deepcopy(request)
    entry = request['capture']['frames'][index]
    value = c.loads(decode_one(c.unpack(entry['wire'], 70000)).payload)
    value.update(updates)
    entry['wire'] = c.packed(encode('observation', c.bounded(value)), 70000)
    return request


def test_native_source_inspection_preserves_bytes_context_and_unknowns(monkeypatch):
    request = example()
    context = dict.fromkeys(c.CONTEXT_FIELDS)
    context.update(part_id='synthetic.part', part_revision='A', lot_id='synthetic.lot')
    request = change_observation(request, context=context, procedure_ref='sha256:'+'1'*64)
    data = raw(request)
    monkeypatch.setattr(Session, 'handle', lambda *a, **kw: pytest.fail('source dispatched Session'))
    monkeypatch.setattr(study, 'decode', lambda *a, **kw: pytest.fail('source decoded scalar values'))
    monkeypatch.setattr(c, 'scalar', lambda *a, **kw: pytest.fail('source interpreted opaque bytes'))
    result = inspect(data)
    assert result['status'] == 'SOURCE_INSPECTED_NOT_EXECUTED'
    assert result['request_ref'] == c.bytes_ref(data)
    assert result['request'] == request
    assert result['origins'] == ['synthetic']
    rows = result['capture_review']['observations']
    assert b''.join(c.unpack(row['raw']) for row in rows) == b'1.000 N\r\n2.000 N\r\n3.000 N\r\n'
    assert rows[0]['context'] == context
    assert rows[0]['procedure_ref'] == 'sha256:'+'1'*64
    assert rows[0]['context']['tool_id'] is None
    assert all(row['acquired_at'] is None and row['value'] is None for row in rows)
    assert [row['device_tick'] for row in rows] == [1000000, 2000000, 3000000]
    assert result['request']['calibration']['joint_covariance']['matrix'][0][1] == 0.0001
    assert result['capture_review']['source_authentication'] == 'not_established'
    assert result['scope']['physical_qualification'] == 'HELD'
    assert result['authorizes_execution'] is False
    assert result['new_scientific_executions'] == 0


@pytest.mark.parametrize('origin', ['synthetic', 'recorded'])
def test_flags_and_native_origin_remain_visible_without_claiming_readiness(origin):
    request = example()
    for index in range(len(request['capture']['frames'])):
        request = change_observation(request, index, origin=origin, quality_flags=['overload'])
    result = inspect(raw(request))
    assert result['origins'] == [origin]
    assert result['capture_review']['observations'][0]['quality_flags'] == ['overload']
    assert result['computation_readiness'] == 'NOT_EVALUATED'
    assert result['authorizes_execution'] is False


def test_malformed_scalar_bytes_remain_inspectable():
    request = change_observation(example(), raw=c.packed(b'NOT A SCALAR\r\n'))
    result = inspect(raw(request))
    assert c.unpack(result['capture_review']['observations'][0]['raw']) == b'NOT A SCALAR\r\n'
    assert result['computation_readiness'] == 'NOT_EVALUATED'


@pytest.mark.parametrize('fault', ['wrong_hash', 'bad_channel', 'bad_schema', 'bad_wire', 'bad_configuration', 'extra', 'oversize', 'wrong_profile', 'mixed_origins'])
def test_invalid_selection_refuses(fault):
    request = example()
    ref, profile = None, 'scalar'
    if fault == 'wrong_hash': ref = 'sha256:'+'0'*64
    elif fault == 'bad_channel': request['channel_id'] = 'missing'
    elif fault == 'bad_schema': request['schema'] = 'other.v1'
    elif fault == 'bad_wire': request['capture']['frames'][0]['wire']['sha256'] = 'sha256:'+'0'*64
    elif fault == 'bad_configuration': request = change_observation(request, configuration_ref='sha256:'+'0'*64)
    elif fault == 'extra': request['authorizes_execution'] = True
    elif fault == 'wrong_profile': profile = 'installation'
    elif fault == 'mixed_origins': request = change_observation(request, origin='recorded')
    data = raw(request) if fault != 'oversize' else b' '*(study.MAX_BYTES+1)
    with pytest.raises(ValueError): inspect(data, ref, profile)


def test_dispatcher_exposes_source_with_exact_hash_and_no_writes(tmp_path, capsys):
    from ciw.production_workflow import main
    data = raw(example())
    source = tmp_path/'request.json'; source.write_bytes(data)
    before = snapshot(tmp_path)
    args = ['engineering', 'source', str(source), '--profile', 'scalar', '--request-ref', c.bytes_ref(data)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['request'] == json.loads(data)
    assert snapshot(tmp_path) == before
    assert main(args[:-1]+['sha256:'+'0'*64]) == 1
    assert json.loads(capsys.readouterr().err)['status'] == 'REFUSED'
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('flagged', [False, True])
def test_source_to_authorized_workflow_and_exact_recovery(tmp_path, flagged, monkeypatch):
    request = example()
    if flagged: request = change_observation(request, quality_flags=['overload'])
    data = raw(request)
    view = inspect(data)
    intent = raw(engineering.example_intent('scalar', view['request_ref']))
    planned = thread.compile_packet(intent, data, tmp_path/'compiled', host_profile='scalar')
    with pytest.raises(PermissionError):
        thread.run_packet(tmp_path/'compiled', tmp_path/'denied',
            expected_compiled_ref=planned['compiled_ref'], host_profile='scalar')
    assert not (tmp_path/'denied').exists()
    run = tmp_path/'run'
    result = thread.run_packet(tmp_path/'compiled', run, expected_compiled_ref=planned['compiled_ref'],
                               host_profile='scalar', allow_compute=True)
    assert result['status'] == ('ANALYSIS_UNRESOLVED' if flagged else 'EVIDENCE_REQUIRED')
    assert result['request_ref'] == view['request_ref']
    assert result['scope']['physical_qualification'] == 'HELD'
    assert len(result['result_ids']) == (0 if flagged else 4)
    if not flagged:
        workspace = json.loads((run/'run/session/workspace.json').read_bytes())
        model = next(row for row in workspace['results'] if row['operation_id'] == study.OPS[2])
        # Independently: (1 + 2 + 3) / 3 * 1.02 - 0.01 = 2.03 N.
        assert model['data']['mean'] == pytest.approx(2.03)
    before = snapshot(run)
    monkeypatch.setattr(Session, 'handle', lambda *a, **kw: pytest.fail('inspection dispatched Session'))
    assert inspect((run/'request.json').read_bytes()) == view
    recovered = tmp_path/'recovered'
    receipt = thread.restore_thread(run, recovered, expected_thread_ref=result['thread_ref'], host_profile='scalar')
    assert receipt['execution_ids'] == result['execution_ids']
    assert receipt['result_ids'] == result['result_ids']
    assert receipt['new_scientific_executions'] == 0
    assert snapshot(recovered) == before == snapshot(run)
    assert inspect((recovered/'request.json').read_bytes()) == view
