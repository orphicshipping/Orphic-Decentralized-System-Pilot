"""Synthetic survey through the real source ledger and retained owner store."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ciw.campaign_contracts import canonical
from ciw.data_instruments import DataInstrumentLedger, InstrumentRefusal, content_ref
from ciw.offline_survey import OfflineSurveyFlow, SurveyOutcomeUnknown, SurveyRefusal
from ciw.principal_policy import PrincipalPolicy


FRAME = {'clock_id': 'synthetic-reference', 'time_scale': 'synthetic-seconds', 'unit': 's'}
TABLE = {'key': 'point_id', 'columns': {
    'point_id': 'string', 'role': 'string', 'easting_m': 'number', 'northing_m': 'number',
    'height_m': 'number', 'height_standard_uncertainty_m': 'number', 'frame_id': 'string',
    'unit': 'string', 'instrument_ref': 'string', 'calibration_ref': 'string',
    'sample_time_s': 'number', 'clock_id': 'string', 'time_scale': 'string'}, 'nullable': []}


def ref(label):
    return content_ref(label.encode())


def inputs():
    binding = {'schema': 'odsp.offline-survey.binding.v1', 'origin': 'synthetic',
        'frame_id': 'synthetic-site-grid', 'linear_unit': 'm',
        'axis_order': ['easting', 'northing', 'height'],
        'instrument_ref': ref('synthetic survey generator'),
        'calibration_ref': ref('synthetic control comparison only'),
        'clock_frame': FRAME}
    control = {'point_id': 'CONTROL', 'role': 'control', 'easting_m': 1000,
        'northing_m': 2000, 'height_m': 10, 'height_standard_uncertainty_m': 0.005,
        'frame_id': binding['frame_id'], 'unit': 'm',
        'instrument_ref': binding['instrument_ref'], 'calibration_ref': binding['calibration_ref'],
        'sample_time_s': 990, 'clock_id': FRAME['clock_id'], 'time_scale': FRAME['time_scale']}
    good = canonical([control])
    rows = [{**control, 'height_m': 10.002},
            {**control, 'point_id': 'ASSET-A', 'role': 'observation', 'easting_m': 1002,
             'northing_m': 2001, 'height_m': 10.125, 'height_standard_uncertainty_m': 0.012},
            {**control, 'point_id': 'ASSET-B', 'role': 'observation', 'easting_m': 1005,
             'northing_m': 2003, 'height_m': 10.25, 'height_standard_uncertainty_m': 0.015}]
    usage = {'status': 'permitted', 'document_ref': ref('synthetic usage only'),
             'review_ref': ref('synthetic host review')}
    source = {'source_id': 'synthetic-survey', 'version': 1, 'kind': 'infrastructure',
        'origin': 'https://example.invalid/offline-survey',
        'license': {**usage, 'expression': 'LicenseRef-Synthetic-Fixture'},
        'terms': usage, 'robots': usage, 'scope_review_ref': ref('synthetic no-person point set'),
        'identification': {'product': 'ODSP', 'version': '1.0', 'contact': 'https://example.invalid/contact'},
        'minimum_interval_seconds': 10, 'table': TABLE,
        'calibration': {'known_good_ref': content_ref(good), 'evidence_ref': binding['calibration_ref'],
                        'absolute_tolerances': {'height_m': 0.005}},
        'freshness': {'frame': FRAME, 'max_age_seconds': 100, 'future_tolerance_seconds': 0}}
    return binding, good, rows, source


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    state = {'now': 1000, 'allowed': True, 'policy': {
        'schema': 'ciw.principal-policy.v1', 'revision': 'synthetic-1', 'principals': [{
            'principal': 'survey-operator', 'enabled': True, 'operations': [],
            'scientific_operations': [], 'resources': [], 'reference_scopes': [], 'handoff_recipients': []}]}}
    policy = PrincipalPolicy(lambda: state['policy'])
    allowed = {'open', 'register', 'capture', 'read', 'assess', 'verify_chain'}
    def make_ledger(tenant='synthetic-company'):
        return DataInstrumentLedger(private / (tenant + '.sqlite'), tenant=tenant,
            principal='survey-operator', policy=policy,
            admit=lambda action, source, context: state['allowed'] and action in allowed
                and source in (None, 'synthetic-survey'),
            clock=lambda: {'value': state['now'], 'frame': FRAME})
    binding, baseline, rows, source = inputs()
    ledger = make_ledger()
    source_ref = ledger.register_source(source, baseline)
    def flow(owned=ledger):
        return OfflineSurveyFlow(ledger=owned, source_id=source['source_id'],
            source_ref=source_ref, binding=binding)
    return state, ledger, rows, source, source_ref, make_ledger, flow


def capture(flow, rows, attempt='survey-1'):
    raw = canonical(rows) if not isinstance(rows, bytes) else rows
    return flow.capture(raw, expected_ref=content_ref(raw), attempt=attempt,
                        observed={'value': 990, 'frame': FRAME})


def test_real_intake_retention_held_result_and_restart(configured):
    _, ledger, rows, _, source_ref, reopen, make_flow = configured
    flow = make_flow()
    result = capture(flow, rows)
    view = flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    native = view['retained_result']
    assert native['status'] == view['status'] == 'held'
    assert native['source_ref'] == source_ref
    assert native['reported_points'] == sorted(rows, key=lambda row: row['point_id'])
    assert native['reported_points'][0]['height_standard_uncertainty_m'] == 0.012
    assert native['coordinate_transformation'] == 'not_performed'
    assert native['calibration_status'] == 'synthetic_control_comparison_only'
    assert native['timestamp_status'] == 'host_observation_only'
    assert native['claims']['source_authenticated'] is False
    assert native['claims']['trusted_for_downstream'] is False
    assert native['claims']['nsl_admission'] == 'not_verified'
    assert native['claims']['physical_calibration_established'] is False
    assert ledger.snapshot('synthetic-survey', result['capture_ref']) == canonical(rows)
    assert ledger.receipt('synthetic-survey', native['assessment_ref'])['kind'] == 'assessment'
    assert ledger.verify_chain()['local_chain_consistent'] is True
    assert make_flow(reopen()).read(result['artifact_id'], capture_ref=result['capture_ref']) == view


def test_capture_attempt_is_idempotent_conflict_refuses(configured):
    _, ledger, rows, _, _, _, make_flow = configured
    flow = make_flow()
    first = capture(flow, rows)
    again = capture(flow, rows)
    assert first['capture_ref'] == again['capture_ref']
    changed = deepcopy(rows)
    changed[1]['height_m'] = 11
    before = ledger.verify_chain()
    with pytest.raises(InstrumentRefusal, match='attempt_conflict'):
        capture(flow, changed)
    assert ledger.verify_chain() == before


def test_wrong_pin_refuses_before_intake(configured):
    _, ledger, rows, _, _, _, make_flow = configured
    before = ledger.verify_chain()
    with pytest.raises(SurveyRefusal, match='capture_pin_mismatch'):
        make_flow().capture(canonical(rows), expected_ref=ref('wrong'), attempt='wrong',
                            observed={'value': 990, 'frame': FRAME})
    assert ledger.verify_chain() == before


def test_changed_host_binding_refuses_before_intake(configured):
    _, ledger, rows, _, _, _, make_flow = configured
    flow = make_flow()
    before = ledger.verify_chain()
    flow.binding_ref = ref('changed binding')
    with pytest.raises(SurveyRefusal, match='survey_host_binding_changed'):
        capture(flow, rows)
    assert ledger.verify_chain() == before


def test_caller_binding_changes_are_detached(configured):
    _, ledger, rows, _, source_ref, _, _ = configured
    binding, _, _, _ = inputs()
    flow = OfflineSurveyFlow(ledger=ledger, source_id='synthetic-survey',
        source_ref=source_ref, binding=binding)
    binding['frame_id'] = 'caller-mutated-frame'
    result = capture(flow, rows)
    assert flow.read(result['artifact_id'], capture_ref=result['capture_ref'])[
        'retained_result']['binding']['frame_id'] == 'synthetic-site-grid'


@pytest.mark.parametrize('raw', [b'{', b'[{"point_id":"a","point_id":"b"}]', b'NaN'])
def test_schema_drift_is_retained_with_sticky_hold(configured, raw):
    _, ledger, rows, _, _, _, make_flow = configured
    flow = make_flow()
    bad = capture(flow, raw, 'bad')
    view = flow.read(bad['artifact_id'], capture_ref=bad['capture_ref'])
    assert ledger.snapshot('synthetic-survey', bad['capture_ref']) == raw
    assert view['retained_result']['reported_points'] == []
    assert 'survey_table_contract_failed' in view['retained_result']['semantic_issues']
    hold = view['current_assessment']['hold_ref']
    assert hold is not None
    good = capture(flow, rows, 'recovery')
    assert flow.read(good['artifact_id'], capture_ref=good['capture_ref'])['current_assessment']['hold_ref'] == hold


@pytest.mark.parametrize('field,value,issue', [
    ('frame_id', 'other-frame', 'survey_frame_mismatch'),
    ('unit', 'mm', 'survey_unit_mismatch'),
    ('height_standard_uncertainty_m', -1, 'survey_uncertainty_refused'),
    ('clock_id', 'other-clock', 'survey_clock_mismatch'),
    ('instrument_ref', ref('other'), 'survey_instrument_or_calibration_mismatch')])
def test_semantic_mismatch_preserves_raw_without_projecting_points(configured, field, value, issue):
    _, ledger, rows, _, _, _, make_flow = configured
    rows[1][field] = value
    flow = make_flow()
    result = capture(flow, rows)
    view = flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    assert issue in view['retained_result']['semantic_issues']
    assert view['retained_result']['reported_points'] == []
    assert ledger.snapshot('synthetic-survey', result['capture_ref']) == canonical(rows)


def test_control_drift_and_stale_data_remain_held(configured):
    state, ledger, rows, _, _, _, make_flow = configured
    rows[0]['height_m'] = 10.1
    flow = make_flow()
    result = capture(flow, rows)
    view = flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    assert view['current_assessment']['checks']['calibration'] == 'out_of_tolerance'
    state['now'] = 1200
    view = flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    assert view['current_assessment']['checks']['freshness'] == 'out_of_tolerance'
    assert view['status'] == 'held'


def test_current_authority_and_cross_tenant_reads_refuse(configured):
    state, _, rows, source, _, make_ledger, make_flow = configured
    flow = make_flow()
    result = capture(flow, rows)
    state['allowed'] = False
    with pytest.raises(InstrumentRefusal, match='host_admission_required'):
        flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    state['allowed'] = True
    other = make_ledger('other-company')
    _, baseline, _, _ = inputs()
    other.register_source(source, baseline)
    with pytest.raises(InstrumentRefusal):
        make_flow(other).read(result['artifact_id'], capture_ref=result['capture_ref'])


def test_late_revocation_withholds_retained_result_and_does_not_claim_rollback(configured, monkeypatch):
    state, _, rows, _, _, _, make_flow = configured
    flow = make_flow()
    original = flow.store.keep
    def keep(*args, **kwargs):
        artifact = original(*args, **kwargs)
        state['allowed'] = False
        return artifact
    monkeypatch.setattr(flow.store, 'keep', keep)
    with pytest.raises(SurveyOutcomeUnknown) as error:
        capture(flow, rows)
    assert error.value.artifact_id.startswith('retained-')
    with pytest.raises(InstrumentRefusal):
        flow.read(error.value.artifact_id, capture_ref=error.value.capture_ref)
    state['allowed'] = True
    assert flow.read(error.value.artifact_id, capture_ref=error.value.capture_ref)['status'] == 'held'


def test_revocation_during_retained_read_withholds_data(configured, monkeypatch):
    state, _, rows, _, _, _, make_flow = configured
    flow = make_flow()
    result = capture(flow, rows)
    original = flow.store.native
    def native(artifact):
        value = original(artifact)
        state['allowed'] = False
        return value
    monkeypatch.setattr(flow.store, 'native', native)
    with pytest.raises(InstrumentRefusal, match='host_admission_required'):
        flow.read(result['artifact_id'], capture_ref=result['capture_ref'])


def test_retained_tampering_with_rehashed_body_still_refuses(configured):
    _, _, rows, _, _, _, make_flow = configured
    flow = make_flow()
    result = capture(flow, rows)
    path = flow.store._path(result['artifact_id'])
    record = json.loads(path.read_bytes())
    record['native']['reported_points'][0]['height_m'] = 999
    body = {k: v for k, v in record['native'].items() if k != 'result_ref'}
    record['native']['result_ref'] = content_ref(canonical(body))
    path.write_bytes(canonical(record))
    with pytest.raises(SurveyRefusal, match='retained_survey_mismatch'):
        flow.read(result['artifact_id'], capture_ref=result['capture_ref'])


def test_full_retained_store_refuses_without_evicting_capture_evidence(configured):
    _, ledger, rows, _, _, _, make_flow = configured
    flow = make_flow()
    for n in range(32):
        flow.store.keep({'synthetic': n}, kind='fixture', catalog={})
    before = sorted(flow.store._path('retained-placeholder').parent.glob('retained-*.json'))
    with pytest.raises(ValueError, match='capacity exhausted'):
        capture(flow, rows)
    assert sorted(flow.store._path('retained-placeholder').parent.glob('retained-*.json')) == before
    assert ledger.verify_chain()['records'] == 3


def test_source_supersession_blocks_saved_result(configured):
    _, ledger, rows, source, _, _, make_flow = configured
    flow = make_flow()
    result = capture(flow, rows)
    source['version'] = 2
    _, baseline, _, _ = inputs()
    ledger.register_source(source, baseline)
    with pytest.raises(InstrumentRefusal, match='source_version_superseded'):
        flow.read(result['artifact_id'], capture_ref=result['capture_ref'])


def test_duplicate_sample_is_preserved_and_held(configured):
    _, ledger, rows, _, _, _, make_flow = configured
    rows.append(deepcopy(rows[1]))
    flow = make_flow()
    result = capture(flow, rows)
    view = flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    assert view['current_assessment']['checks']['schema'] == 'out_of_tolerance'
    assert view['current_assessment']['hold_ref'] is not None
    assert ledger.snapshot('synthetic-survey', result['capture_ref']) == canonical(rows)


def test_runnable_example_reopens_in_separate_process_without_network_or_children(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    script = repo / 'examples/offline-survey/run.py'
    output = tmp_path / 'survey'
    environment = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in os.environ}
    environment.update(PYTHONPATH=str(repo / 'src'), PYTHONDONTWRITEBYTECODE='1')
    launcher = """
import runpy, sys
def deny(event, args):
    if event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo',
                 'subprocess.Popen', 'os.system', 'os.posix_spawn'):
        raise AssertionError('offline survey attempted network or child execution')
sys.addaudithook(deny)
script = sys.argv.pop(1)
sys.argv[0] = script
runpy.run_path(script, run_name='__main__')
"""
    command = [sys.executable, '-B', '-c', launcher, str(script), '--output', str(output)]
    first = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    manifest = json.loads((output / 'manifest.json').read_bytes())
    hashes = {name: content_ref((output / name).read_bytes()) for name in manifest['files']}
    assert hashes == manifest['files']
    second = subprocess.run(command + ['--inspect'], env=environment, capture_output=True, text=True, timeout=15)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == json.loads(first.stdout)
    assert {name: content_ref((output / name).read_bytes()) for name in manifest['files']} == hashes
    duplicate = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert duplicate.returncode != 0 and 'FileExistsError' in duplicate.stderr
    assert json.loads((output / 'manifest.json').read_bytes()) == manifest
    with (output / 'capture.json').open('ab') as stream:
        stream.write(b'\n')
    changed = subprocess.run(command + ['--inspect'], env=environment, capture_output=True, text=True, timeout=15)
    assert changed.returncode != 0 and 'fixture_input_pin_mismatch' in changed.stderr
