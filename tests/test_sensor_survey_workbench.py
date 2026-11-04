"""Real retained sources, CIW occurrences, hostile joins and offline playback."""
from copy import deepcopy
from decimal import Decimal, localcontext
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import content_ref
from ciw.sensor_survey_workbench import (SensorSurveyWorkbench, ReconciliationRefusal,
    ReconciliationOutcomeUnknown, inspect_workbench)


def example_module():
    path = Path(__file__).resolve().parents[1] / 'examples/sensor-survey-workbench/run.py'
    spec = importlib.util.spec_from_file_location('sensor_survey_example', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared(tmp_path):
    return example_module().prepare(tmp_path / 'inputs')


def run(prepared, name='workbench'):
    return prepared.active.execute(prepared.root / name)


def inspect(prepared, receipt, name='playback'):
    return inspect_workbench(receipt['output'], expected_manifest_ref=receipt['manifest_ref'],
                             playback_output=prepared.root / name)


def test_actual_workbench_joint_covariance_decimal_reference_and_no_state_claim(prepared):
    result = run(prepared)
    view = inspect(prepared, result)
    p = view['projection']
    with localcontext() as context:
        context.prec = 50
        residual = Decimal('10.129') - Decimal('10.125')
        variance = Decimal('0.010') ** 2 + Decimal('0.012') ** 2 - 2 * Decimal('0.004') ** 2
        uncertainty = variance.sqrt()
    assert p['sensor_minus_survey_m'] == pytest.approx(float(residual), abs=1e-14)
    assert p['residual_covariance']['matrix'] == [[float(variance)]]
    assert p['residual_standard_uncertainty_m'] == pytest.approx(float(uncertainty), rel=1e-14)
    assert p['residual_covariance']['matrix'] != [[float(Decimal('0.010')**2 + Decimal('0.012')**2)]]
    assert p['point']['easting_m'] == 1002 and p['point']['northing_m'] == 2001
    assert p['point']['sample_time_s'] == 990 and p['point']['clock_id'] == 'synthetic-reference'
    assert p['status'] == 'held' and p['review'] == {'status': 'pending_human_review', 'decision': None}
    assert p['claims']['state_admission'] == p['claims']['physical_validation'] == 'not_performed'
    assert p['claims']['nsl_admission'] == 'not_verified' and not p['claims']['equipment_authority']
    assert p['model_id'] == 'coincident-height-discrepancy.v1'
    assert 'admitted' not in p and 'accepted' not in p and 'fused_state' not in p
    assert prepared.source.read_calls == 2  # final chunk declares EOF; fusion did not reacquire
    workspace = json.loads((Path(result['output']) / 'workspace.json').read_bytes())
    assert len(workspace['workbench']['sources']) == 5
    sources = workspace['workbench']['sources']
    assert sources[2]['input_source_ids'] == [sources[0]['source_id']]
    assert sources[3]['input_source_ids'] == [sources[1]['source_id']]
    assert set(sources[4]['input_source_ids']) == {s['source_id'] for s in sources[2:4]}


def test_retained_byte_history_retransmission_identity_and_provider_free_playback(prepared):
    result = run(prepared)
    output = Path(result['output'])
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    exported = parse((output / 'journal-export.json').read_bytes())
    assert exported['summary']['arrivals'] == 2
    assert exported['summary']['unique_samples'] == 1
    assert exported['summary']['duplicate_arrivals'] == 1
    arrivals = exported['summary']['arrival_receipts']
    assert [a['received_at']['seconds'] for a in arrivals] == [992, 995]
    assert len({a['sample_ref'] for a in arrivals}) == 1
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('playback executed')), \
         patch.object(prepared.survey, 'read', side_effect=AssertionError('playback read source')), \
         patch.object(prepared.journal, 'export', side_effect=AssertionError('playback read journal')):
        prepared.state['allowed'] = False  # offline history does not restore this host authority
        view = inspect(prepared, result)
    assert view['playback'] == 'retained_history_only_no_source_read_or_new_execution'
    assert {p: p.read_bytes() for p in before} == before


def test_explicit_reprocessing_is_fresh_occurrence_with_same_source_measurement(prepared):
    first, second = run(prepared, 'first'), run(prepared, 'second')
    a, b = inspect(prepared, first, 'a')['projection'], inspect(prepared, second, 'b')['projection']
    assert first['execution_id'] != second['execution_id'] and first['result_id'] != second['result_id']
    assert a['lineage'] == b['lineage'] and a['run_evidence_id'] == b['run_evidence_id']
    assert a['residual_covariance'] == b['residual_covariance']
    assert prepared.source.read_calls == 2


@pytest.mark.parametrize('attack', [
    'unit', 'frame', 'quantity', 'source_time', 'acquisition_time', 'clock_scale',
    'coordinates', 'point_id', 'quality', 'missing', 'unknown_uncertainty',
    'unknown_calibration', 'expired_calibration', 'missing_dependency', 'excess_shared_variance'])
def test_real_journal_semantic_mismatches_stop_before_numerical_execution(attack, tmp_path):
    example = example_module()
    original = example.declarations
    def declarations():
        uncertainty, shared, bound, sample = original()
        if attack == 'unit':
            sample['unit'] = bound['channels'][0]['unit'] = 'mm'
        elif attack == 'frame':
            sample['frame'] = bound['channels'][0]['frame'] = 'other-local-grid'
        elif attack == 'quantity': bound['channels'][0]['quantity'] = 'temperature'
        elif attack == 'source_time': sample['source_stamp']['seconds'] = 989
        elif attack == 'acquisition_time': sample['acquired_at']['seconds'] = 991
        elif attack == 'clock_scale': bound['source_clock']['scale'] = 'other-seconds'
        elif attack == 'coordinates': bound['configuration']['easting_m'] = 1003
        elif attack == 'point_id': bound['configuration']['point_id'] = 'ASSET-B'
        elif attack == 'quality': sample['quality'] = 'reported_uncertain'
        elif attack == 'missing':
            sample.update(value=None, missing_reason='dropout', quality='missing')
        elif attack == 'unknown_uncertainty':
            bound['uncertainty'].update(status='unknown', covariance_ref=None)
        elif attack == 'unknown_calibration':
            bound['calibration'].update(status='unknown', ref=None, valid_from_s=None, valid_until_s=None)
        elif attack == 'expired_calibration': bound['calibration']['valid_until_s'] = 995
        elif attack == 'missing_dependency': bound['uncertainty']['dependency_refs'] = []
        elif attack == 'excess_shared_variance':
            shared['standard_uncertainty_m'] = .02
            bound['uncertainty']['dependency_refs'] = [content_ref(canonical(shared))]
        return uncertainty, shared, bound, sample
    with patch.object(example, 'declarations', side_effect=declarations):
        configured = example.prepare(tmp_path / 'inputs')
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('incompatible input executed')) as execute:
        with pytest.raises(ReconciliationRefusal): run(configured)
    assert execute.call_count == 0 and not (configured.root / 'workbench').exists()


@pytest.mark.parametrize('attack', ['missing_point', 'missing_sample', 'wrong_model_version', 'wrong_pin'])
def test_explicit_model_selection_and_pin_fail_closed(attack, prepared):
    model = deepcopy(prepared.model)
    if attack == 'missing_point': model['survey']['point_id'] = 'NOT-A-POINT'
    elif attack == 'missing_sample': model['sensor']['sample_ref'] = content_ref(b'absent sample')
    elif attack == 'wrong_model_version': model['model_id'] = 'latest'
    pin = content_ref(b'wrong pin') if attack == 'wrong_pin' else content_ref(canonical(model))
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('bad selection executed')) as execute:
        with pytest.raises(ReconciliationRefusal):
            SensorSurveyWorkbench(survey=prepared.survey, journal=prepared.journal, model=model,
                expected_model_ref=pin, authorize=lambda *args: True).execute(prepared.root / 'wrong')
    assert execute.call_count == 0


@pytest.mark.parametrize('stage', ['initial', 'source_io', 'input_retention', 'result_retention', 'final_disclosure'])
def test_fresh_host_admission_around_io_and_retention_withholds_late_disclosure(stage, prepared):
    import ciw.sensor_survey_workbench as workflow
    original = workflow._write
    original_read = prepared.survey.read
    def read(*args, **kwargs):
        view = original_read(*args, **kwargs)
        if stage == 'source_io': prepared.state['allowed'] = False
        return view
    def write(root, name, value):
        original(root, name, value)
        if ((stage == 'input_retention' and name == 'journal-export.json')
                or (stage == 'result_retention' and name == 'projection.json')
                or (stage == 'final_disclosure' and name == 'manifest.json')):
            prepared.state['allowed'] = False
    if stage == 'initial': prepared.state['allowed'] = False
    with patch.object(prepared.survey, 'read', side_effect=read), patch.object(workflow, '_write', side_effect=write), \
         patch('ciw.numerical_workflow.execute', wraps=__import__('ciw.numerical_workflow', fromlist=['execute']).execute) as execute:
        with pytest.raises(ReconciliationRefusal): run(prepared)
    assert execute.call_count == (1 if stage in {'result_retention', 'final_disclosure'} else 0)
    if stage == 'final_disclosure':
        assert (prepared.root / 'workbench/manifest.json').is_file()
        assert parse((prepared.root / 'workbench/projection.json').read_bytes())['status'] == 'held'


def test_canonical_principal_policy_revocation_prevents_read_and_calculation(prepared):
    policy = deepcopy(prepared.ledger.policy.snapshot())
    policy['principals'][0]['enabled'] = False
    prepared.ledger.policy._reader = lambda: policy
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('revoked source executed')) as execute:
        with pytest.raises(ValueError): run(prepared)
    assert execute.call_count == 0


def test_stale_capture_is_held_even_though_saved_report_was_nominal(prepared):
    prepared.ledger._clock = lambda: {'value': 1100, 'frame':
        {'clock_id': 'synthetic-reference', 'time_scale': 'synthetic-seconds', 'unit': 's'}}
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('stale source executed')) as execute:
        with pytest.raises(ValueError): run(prepared)
    assert execute.call_count == 0


@pytest.mark.parametrize('file', ['journal-export.json', 'survey-raw.json', 'projection.json', 'workspace.json'])
def test_immutable_artifact_tampering_is_refused(file, prepared):
    result = run(prepared)
    path = Path(result['output']) / file
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ReconciliationRefusal, match='artifact_pin'):
        inspect(prepared, result)


def test_rehashed_projection_still_has_to_match_original_numeric_occurrence(prepared):
    result = run(prepared)
    output = Path(result['output'])
    projection = parse((output / 'projection.json').read_bytes())
    projection['sensor_minus_survey_m'] = 123
    raw = canonical(projection)
    (output / 'projection.json').write_bytes(raw)
    manifest = parse((output / 'manifest.json').read_bytes())
    manifest['files']['projection.json'] = content_ref(raw)
    (output / 'manifest.json').write_bytes(canonical(manifest))
    result['manifest_ref'] = content_ref(canonical(manifest))
    with pytest.raises(ReconciliationRefusal, match='projection_mismatch'):
        inspect(prepared, result)


def test_create_only_retention_does_not_overwrite_prior_journey(prepared):
    result = run(prepared)
    original = (Path(result['output']) / 'manifest.json').read_bytes()
    with pytest.raises(ReconciliationRefusal, match='new_local_output'):
        run(prepared)
    assert (Path(result['output']) / 'manifest.json').read_bytes() == original


def test_retention_failure_keeps_recording_and_never_claims_completed(prepared):
    with patch('ciw.sensor_survey_workbench.Session.save_workspace', side_effect=OSError('injected local disk error')):
        with pytest.raises(ReconciliationOutcomeUnknown) as error:
            run(prepared)
    assert Path(error.value.output).is_dir()
    assert list((Path(error.value.output) / 'session').glob('execution-*.json'))
    assert not (Path(error.value.output) / 'manifest.json').exists()


def test_journal_response_loss_holds_even_previous_completed_observation(prepared):
    from atelier_mcp.observation_gateway import RecordedSensor, OutcomeUnknown
    from atelier_mcp.observation_gateway.contracts import encode
    sample = prepared.history['summary']['samples'][0]['sample']
    source = RecordedSensor(prepared.journal.device, (encode([sample]),))
    plan = prepared.journal.prepare(source, {'max_arrivals': 2, 'window_s': 1})
    with patch.object(source, 'read_next', side_effect=RuntimeError('injected response loss')):
        with pytest.raises(OutcomeUnknown):
            prepared.journal.acquire(plan['plan_ref'], key='later-uncertain-read', source=source)
    readiness = prepared.journal.readiness(prepared.history['job_id'])
    assert readiness['outcome'] == 'complete' and readiness['binding_hold_job_id'] is not None
    assert readiness['ready_for_held_projection'] is False
    assert prepared.journal.history(prepared.history['job_id'])['outcome'] == 'complete'
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('held binding executed')) as execute:
        with pytest.raises(ReconciliationRefusal, match='current_journal_binding_hold'):
            run(prepared)
    assert execute.call_count == 0


def test_small_clock_advance_within_nominal_window_does_not_rewrite_source(prepared):
    clock = [1000]
    def now():
        clock[0] += .01
        return {'value': clock[0], 'frame': {'clock_id': 'synthetic-reference',
                'time_scale': 'synthetic-seconds', 'unit': 's'}}
    prepared.ledger._clock = now
    result = run(prepared)
    assert inspect(prepared, result)['projection']['point']['sample_time_s'] == 990


def test_readiness_revocation_never_uses_saved_grant(prepared):
    with patch.object(prepared.journal, '_authorize', return_value=None):
        with pytest.raises(ValueError):
            prepared.journal.readiness(prepared.history['job_id'])


def test_playback_must_not_write_into_preserved_workbench(prepared):
    result = run(prepared)
    original = (Path(result['output']) / 'workspace.json').read_bytes()
    with pytest.raises(ReconciliationRefusal, match='separate_playback'):
        inspect_workbench(result['output'], expected_manifest_ref=result['manifest_ref'],
                          playback_output=Path(result['output']) / 'nested')
    assert (Path(result['output']) / 'workspace.json').read_bytes() == original
