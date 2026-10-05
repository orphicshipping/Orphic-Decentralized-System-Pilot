from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from ciw import thermal_identification as thermal
from ciw import thermal_identification_workflow as training_workflow
from ciw import thermal_experiment as experiment
from ciw import thermal_experiment_workflow as workflow
from ciw.core.covariance import covariance_identity, validate_covariance_artifact
from ciw.core.identities import content_identity
from ciw.operations.runner import seal


@pytest.fixture
def request_data(tmp_path):
    directory = tmp_path / 'training'
    training_workflow.run(thermal.example_request(), directory)
    return experiment.example_request(*training_workflow._read(directory))


def refresh_observation_covariance(request):
    trace = request['experiment']
    cov = trace['observation_covariance']
    cov['reference_values'] = [s['temperature_k'] for s in trace['samples']]
    cov['quantity_ids'] = [s['sample_id'] for s in trace['samples']]
    cov['covariance_id'] = covariance_identity(cov)


@pytest.mark.parametrize('uncertainty', [1e-9, 1e-6, 0.2])
@pytest.mark.parametrize('tamper', ['zero_all', 'zero_cross', 'halve'])
def test_covariance_cannot_be_erased_below_absolute_floor(uncertainty, tamper):
    request = thermal.example_request()
    for sample in request['samples']:
        sample['standard_uncertainty_k'] = uncertainty
    result = thermal.fit(request)
    cov = result['parameter_covariance']
    if tamper == 'zero_all':
        cov['matrix'] = [[0.0, 0.0], [0.0, 0.0]]
    elif tamper == 'zero_cross':
        cov['matrix'][0][1] = cov['matrix'][1][0] = 0.0
    else:
        cov['matrix'] = (np.array(cov['matrix']) * 0.5).tolist()
    cov['covariance_id'] = covariance_identity(cov)
    with pytest.raises(ValueError, match='dimensionless'):
        thermal.validate_result(request, seal(result))


def test_tiny_covariance_roundoff_still_allowed():
    request = thermal.example_request()
    for sample in request['samples']:
        sample['standard_uncertainty_k'] = 1e-9
    result = thermal.fit(request)
    cov = result['parameter_covariance']
    cov['matrix'][0][0] = math.nextafter(cov['matrix'][0][0], math.inf)
    cov['covariance_id'] = covariance_identity(cov)
    thermal.validate_result(request, seal(result))


def test_candidate_requires_parameter_state(tmp_path):
    directory = tmp_path / 'training'
    training_workflow.run(thermal.example_request(), directory)
    (directory / 'parameter-state.json').unlink()
    with pytest.raises(ValueError, match='missing'):
        training_workflow.inspect(directory)


def test_frozen_model_with_separate_synthetic_experiment(request_data):
    before = deepcopy(request_data)
    result = experiment.assess(request_data)
    assert result['status'] == 'numerically_consistent'
    assert result['metrics']['rmse_k'] < 1e-10
    assert result['work'] == {'fitting_evaluations': 0, 'predictions': 12}
    assert result['training_execution_id'] == request_data['training_result']['execution_id']
    assert request_data == before
    assert experiment.validate_result(request_data, result) == result
    assert result['authority'] == thermal.AUTHORITY
    assert all(row['origin'] == 'synthetic' for row in result['residuals'])


def test_assessment_and_review_never_fit(request_data, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Separate assessment attempted to fit or search')
    monkeypatch.setattr(thermal, 'fit', forbidden)
    monkeypatch.setattr(thermal, '_profile', forbidden)
    result = experiment.assess(request_data)
    experiment.validate_result(request_data, result)


def test_full_covariance_matches_independent_formula(request_data):
    result = experiment.assess(request_data)
    model = request_data['training_result']['data']['model']
    a, tau = model['initial_excess_k'], model['tau_s']
    t = np.array([s['time_s'] for s in request_data['experiment']['samples']])
    e = np.exp(-t / tau)
    j = np.column_stack((e, a * e * t / tau ** 2))
    p = np.array(request_data['training_result']['data']['parameter_covariance']['matrix'])
    o = np.array(request_data['experiment']['observation_covariance']['matrix'])
    expected = o + j @ p @ j.T
    actual = np.array(result['residual_covariance']['matrix'])
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-15)
    assert actual[0, 1] != 0  # Shared fitted parameters correlate separate predictions.
    validate_covariance_artifact(result['residual_covariance'])


def test_correlated_observations_use_full_matrix(request_data):
    n = len(request_data['experiment']['samples'])
    covariance = request_data['experiment']['observation_covariance']
    covariance['matrix'] = (0.03 * np.eye(n) + 0.01 * np.ones((n, n))).tolist()
    for i, sample in enumerate(request_data['experiment']['samples']):
        sample['temperature_k'] += 0.1 * (-1) ** i
    refresh_observation_covariance(request_data)
    result = experiment.assess(request_data)
    matrix = np.array(result['residual_covariance']['matrix'])
    errors = np.array([row['residual_k'] for row in result['residuals']])
    # Independent direct quadratic-form check; production uses Cholesky whitening.
    expected = math.sqrt(float(errors @ np.linalg.solve(matrix, errors)) / n)
    assert result['metrics']['whitened_rms'] == pytest.approx(expected, rel=1e-12)
    diagonal_only = math.sqrt(float(np.mean(errors ** 2 / np.diag(matrix))))
    assert abs(expected - diagonal_only) > 1e-3


def test_explicit_cross_covariance_preserves_joint_terms(request_data):
    p = np.array(request_data['training_result']['data']['parameter_covariance']['matrix'])
    o = np.array(request_data['experiment']['observation_covariance']['matrix'])
    n = len(o)
    contraction = np.zeros((2, n)); contraction[0, 0] = 0.3; contraction[1, 1] = -0.2
    cross = np.linalg.cholesky(p) @ contraction @ np.linalg.cholesky(o).T
    request_data['dependence']['kind'] = 'explicit_cross_covariance'
    request_data['dependence']['parameter_observation_cross_covariance'] = cross.tolist()
    result = experiment.assess(request_data)
    m = request_data['training_result']['data']['model']; tau = m['tau_s']; a = m['initial_excess_k']
    t = np.array([s['time_s'] for s in request_data['experiment']['samples']]); e = np.exp(-t / tau)
    j = np.column_stack((e, a * e * t / tau ** 2))
    expected = o + j @ p @ j.T - j @ cross - cross.T @ j.T
    np.testing.assert_allclose(result['residual_covariance']['matrix'], expected, rtol=1e-12, atol=1e-15)


def test_unknown_cross_covariance_is_indeterminate(request_data):
    request_data['dependence']['kind'] = 'unknown'
    result = experiment.assess(request_data)
    assert result['status'] == 'indeterminate'
    assert result['residual_covariance'] is None
    assert result['metrics']['whitened_rms'] is None
    assert 'parameter_observation_dependence_unknown' in result['reasons']
    assert all(row['marginal_standardized_residual'] is None for row in result['residuals'])


def test_bad_cross_covariance_rejected_not_clipped(request_data):
    request_data['dependence']['kind'] = 'explicit_cross_covariance'
    request_data['dependence']['parameter_observation_cross_covariance'] = [[1.0] * 12] * 2
    with pytest.raises(ValueError, match='positive semidefinite'):
        experiment.assess(request_data)


def test_ill_conditioned_covariance_is_not_regularized(request_data):
    request_data['gates']['max_residual_condition'] = 1.01
    result = experiment.assess(request_data)
    assert result['status'] == 'indeterminate'
    assert result['covariance_status'] == 'ill_conditioned'
    assert result['metrics']['whitened_rms'] is None


def test_singular_covariance_has_no_pseudoinverse_promotion(request_data):
    n = len(request_data['experiment']['samples'])
    request_data['experiment']['observation_covariance']['matrix'] = (0.04 * np.ones((n, n))).tolist()
    refresh_observation_covariance(request_data)
    result = experiment.assess(request_data)
    assert result['status'] == 'indeterminate'
    assert result['covariance_status'] in {'singular', 'ill_conditioned'}
    assert result['metrics']['whitened_rms'] is None


@pytest.mark.parametrize('tau', [8.0, 16.0, 25.0])
def test_changed_thermal_response_rejected_without_refit(request_data, tau):
    original = deepcopy(request_data['training_result'])
    for sample in request_data['experiment']['samples']:
        sample['temperature_k'] = 300 + 80 * math.exp(-sample['time_s'] / tau)
    refresh_observation_covariance(request_data)
    result = experiment.assess(request_data)
    assert result['status'] == 'rejected'
    assert 'rmse_gate_failed' in result['reasons']
    assert 'whitened_residual_gate_failed' in result['reasons']
    assert request_data['training_result'] == original


def test_unknown_uncertainty_does_not_hide_raw_residual_failure(request_data):
    request_data['dependence']['kind'] = 'unknown'
    request_data['experiment']['samples'][0]['temperature_k'] += 10
    refresh_observation_covariance(request_data)
    assert experiment.assess(request_data)['status'] == 'rejected'


@pytest.mark.parametrize('field', ['facility_id', 'machine_id', 'tool_id', 'material_lot_id'])
def test_applicability_change_refused(request_data, field):
    request_data['experiment']['identity'][field] = 'other.' + field
    with pytest.raises(ValueError, match='applicability'):
        experiment.assess(request_data)


@pytest.mark.parametrize('field', ['cycle_id', 'part_id'])
def test_reusing_training_identity_refused(request_data, field):
    request_data['experiment']['identity'][field] = request_data['training_request']['identity'][field]
    with pytest.raises(ValueError, match='different cycle and part'):
        experiment.assess(request_data)


@pytest.mark.parametrize('field,value', [('unit', 'degC'), ('quantity', 'mold_temperature'),
    ('frame', 'other.frame'), ('source_kind', 'retained_observation'), ('boundary_temperature_k', 301.0)])
def test_source_semantics_and_boundary_not_implicitly_changed(request_data, field, value):
    request_data['experiment'][field] = value
    with pytest.raises(ValueError):
        experiment.assess(request_data)


def test_training_source_and_sample_reuse_rejected(request_data):
    altered = deepcopy(request_data)
    altered['experiment']['source_ref'] = altered['training_request']['source_ref']
    with pytest.raises(ValueError, match='source cannot be reused'):
        experiment.assess(altered)
    request_data['experiment']['samples'][0]['sample_id'] = request_data['training_request']['samples'][0]['sample_id']
    with pytest.raises(ValueError, match='reused sample'):
        experiment.assess(request_data)


@pytest.mark.parametrize('field,value', [('time_s', -1.0), ('time_s', 46.1), ('time_s', True),
    ('time_s', float('inf')), ('temperature_k', 0.0), ('temperature_k', None),
    ('temperature_k', float('nan')), ('temperature_k', True)])
def test_invalid_samples_refused(request_data, field, value):
    request_data['experiment']['samples'][0][field] = value
    with pytest.raises(ValueError):
        experiment.assess(request_data)


def test_unordered_times_and_duplicate_ids_refused(request_data):
    for field in ['time_s', 'sample_id']:
        altered = deepcopy(request_data)
        altered['experiment']['samples'][1][field] = altered['experiment']['samples'][0][field]
        with pytest.raises(ValueError):
            experiment.assess(altered)


@pytest.mark.parametrize('count', [0, 1, 2, 129])
def test_dense_sample_budget(request_data, count):
    request_data['experiment']['samples'] = request_data['experiment']['samples'][:1] * count
    with pytest.raises(ValueError, match='3..128'):
        experiment.assess(request_data)


@pytest.mark.parametrize('change', ['unit', 'frame', 'axis', 'reference', 'basis', 'source'])
def test_covariance_binding_tamper_refused(request_data, change):
    cov = request_data['experiment']['observation_covariance']
    if change == 'unit': cov['units'][0] = 's'
    if change == 'frame': cov['frame'] = 'other.frame'
    if change == 'axis': cov['quantity_ids'][0] = 'other.axis'
    if change == 'reference': cov['reference_values'][0] += 1
    if change == 'basis': cov['basis']['id'] = content_identity({'wrong': True})
    if change == 'source': cov['provenance']['source_evidence_ids'] = []
    cov['covariance_id'] = covariance_identity(cov)
    with pytest.raises(ValueError):
        experiment.assess(request_data)


def test_bad_covariance_not_repaired(request_data):
    cov = request_data['experiment']['observation_covariance']
    cov['matrix'][0][1] = 1.0
    cov['covariance_id'] = covariance_identity(cov)
    with pytest.raises(ValueError, match='symmetric'):
        experiment.assess(request_data)


@pytest.mark.parametrize('kind,cross', [('unknown', [[0.0] * 12] * 2),
    ('declared_independent', [[0.0] * 12] * 2), ('explicit_cross_covariance', None),
    ('explicit_cross_covariance', [[0.0] * 12]), ('explicit_cross_covariance', [[0.0], [0.0]]),
    ('bogus', None)])
def test_dependence_contract_is_explicit(request_data, kind, cross):
    request_data['dependence']['kind'] = kind
    request_data['dependence']['parameter_observation_cross_covariance'] = cross
    with pytest.raises(ValueError):
        experiment.assess(request_data)


@pytest.mark.parametrize('value', [True, 0.0, -1.0, None, 1e13])
def test_gates_are_bounded_numbers(request_data, value):
    request_data['gates']['max_rmse_k'] = value
    with pytest.raises(ValueError):
        experiment.assess(request_data)


@pytest.mark.parametrize('target', ['training_result', 'training_execution'])
def test_retained_training_occurrence_seal_required(request_data, target):
    request_data[target]['runtime']['version'] = 'changed'
    with pytest.raises(ValueError):
        experiment.assess(request_data)


def test_training_execution_result_binding_even_after_reseal(request_data):
    execution = request_data['training_execution']
    execution['result_id'] = 'result-' + '0' * 32
    request_data['training_execution'] = seal(execution)
    with pytest.raises(ValueError):
        experiment.assess(request_data)


@pytest.mark.parametrize('field,value', [('verification_status', 'verified'), ('role', 'verification'),
    ('operation_id', 'untrusted.fit.v1')])
def test_training_authority_cannot_be_laundered(request_data, field, value):
    result = request_data['training_result']
    result[field] = value
    request_data['training_result'] = seal(result)
    with pytest.raises(ValueError):
        experiment.assess(request_data)


def test_rejected_training_cannot_assess(tmp_path):
    request = thermal.example_request()
    for sample in request['samples']:
        sample['temperature_k'] = 300.0
    directory = tmp_path / 'rejected'
    training_workflow.run(request, directory)
    with pytest.raises(ValueError, match='usable training'):
        experiment.example_request(*training_workflow._read(directory))


def test_result_roundoff_accepts_but_semantics_remain_exact(request_data):
    result = experiment.assess(request_data)
    altered = deepcopy(result)
    altered['metrics']['rmse_k'] = math.nextafter(altered['metrics']['rmse_k'], math.inf)
    experiment.validate_result(request_data, seal(altered))
    for field, value in [('status', 'qualified'), ('work', {'fitting_evaluations': 1, 'predictions': 12}),
                         ('source_kind', 'retained_observation'), ('reasons', ['other'])]:
        altered = deepcopy(result); altered[field] = value
        with pytest.raises(ValueError):
            experiment.validate_result(request_data, seal(altered))


def test_result_residual_covariance_cannot_be_erased(request_data):
    result = experiment.assess(request_data)
    cov = result['residual_covariance']
    cov['matrix'] = np.diag(np.diag(cov['matrix'])).tolist()
    cov['covariance_id'] = covariance_identity(cov)
    with pytest.raises(ValueError, match='dimensionless'):
        experiment.validate_result(request_data, seal(result))


def test_input_and_results_detached(request_data):
    before = deepcopy(request_data)
    result = experiment.assess(request_data)
    altered = experiment.validate_result(request_data, result)
    altered['metrics']['sample_count'] = 0
    assert result['metrics']['sample_count'] == 12
    assert request_data == before


def test_session_read_replay_and_no_files_changed(request_data, tmp_path, monkeypatch):
    directory = tmp_path / 'assessment'
    summary = workflow.run(request_data, directory)
    assert summary['assessment_status'] == 'numerically_consistent'
    _, execution, first = workflow._read(directory)
    assert first['verification_status'] == 'not_verified' and first['verification_id'] is None
    assert first['execution_id'] != request_data['training_result']['execution_id']
    replay = workflow.replay(directory, tmp_path / 'replay')
    _, _, second = workflow._read(tmp_path / 'replay')
    assert replay['numerical_replay'] == 'consistent_within_declared_float_tolerance'
    assert first['evidence_id'] == second['evidence_id']
    assert first['result_id'] != second['result_id']
    before = {str(p): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    monkeypatch.setattr(thermal, 'fit', lambda *a: pytest.fail('Inspection refitted model'))
    workflow.inspect(directory)
    after = {str(p): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert before == after


def test_missing_or_altered_sidecar_refused(request_data, tmp_path):
    directory = tmp_path / 'assessment'
    workflow.run(request_data, directory)
    path = directory / 'request.json'
    changed = json.loads(path.read_text()); changed['gates']['max_rmse_k'] *= 2
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='sidecar'):
        workflow.inspect(directory)
    path.unlink()
    with pytest.raises(ValueError, match='regular'):
        workflow.inspect(directory)


def test_symlink_sidecar_refused(request_data, tmp_path):
    directory = tmp_path / 'assessment'; workflow.run(request_data, directory)
    path = directory / 'request.json'; target = tmp_path / 'original.json'; path.rename(target)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip('Platform disallows symlinks for this account')
    with pytest.raises(ValueError, match='regular'):
        workflow.inspect(directory)


def test_cli_entire_installed_command_journey(tmp_path, capsys):
    from ciw.net import main
    directory = tmp_path / 'training'
    assert main(['polymer', 'identify', 'demo', '--output-dir', str(directory)]) == 0
    capsys.readouterr()
    path = tmp_path / 'experiment.json'
    args = ['polymer', 'identify', 'experiment']
    assert main(args + ['example', str(directory), '--output', str(path)]) == 0
    capsys.readouterr()
    assert main(args + ['run', str(path), '--output-dir', str(tmp_path / 'assessment')]) == 0
    assert json.loads(capsys.readouterr().out)['assessment_status'] == 'numerically_consistent'
    assert main(args + ['inspect', str(tmp_path / 'assessment')]) == 0
    capsys.readouterr()
    assert main(args + ['replay', str(tmp_path / 'assessment'), '--output-dir', str(tmp_path / 'replay')]) == 0
    capsys.readouterr()
    assert main(args + ['run', str(path), '--output-dir', str(tmp_path / 'assessment')]) == 2
    assert json.loads(capsys.readouterr().err)['status'] == 'refused'


def test_indeterminate_cli_exits_nonzero(request_data, tmp_path, capsys):
    request_data['dependence']['kind'] = 'unknown'
    path = tmp_path / 'experiment.json'; path.write_text(json.dumps(request_data))
    assert workflow.main(['run', str(path), '--output-dir', str(tmp_path / 'assessment')]) == 2
    assert json.loads(capsys.readouterr().out)['assessment_status'] == 'indeterminate'


def test_explicit_runtime_binding_only():
    from ciw.operations.registry import default_registry
    assert workflow.OPERATION not in {o['operation_id'] for o in default_registry().describe()}


def test_synthetic_generator_never_fabricates_physical_observations(request_data):
    training = deepcopy(request_data['training_request']); training['source_kind'] = 'retained_observation'
    with pytest.raises(ValueError, match='synthetic training'):
        experiment.example_request(training, request_data['training_execution'], request_data['training_result'])


def test_noisy_separate_experiment_fixture(request_data):
    rng = np.random.default_rng(90210)
    for sample, noise in zip(request_data['experiment']['samples'], rng.normal(0, 0.2, 12)):
        sample['temperature_k'] += float(noise)
    refresh_observation_covariance(request_data)
    result = experiment.assess(request_data)
    assert result['status'] == 'numerically_consistent'
    assert 0.2 < result['metrics']['whitened_rms'] < 2.0


def test_small_parameter_state_uncertainty_cannot_be_zeroed(tmp_path):
    request = thermal.example_request()
    request['tau_bounds_s'] = [1e-6, 1e-4]
    for i, sample in enumerate(request['samples']):
        sample['time_s'] = i * 1e-6
        sample['temperature_k'] = 300.0 + 80.0 * math.exp(-i / 6)
        sample['standard_uncertainty_k'] = 1e-9
    directory = tmp_path / 'fast'
    assert training_workflow.run(request, directory)['candidate_status'] == 'candidate'
    path = directory / 'parameter-state.json'; state = json.loads(path.read_text())
    assert 0 < state['states']['estimated']['standard_uncertainty'] < 1e-11
    state['states']['estimated']['standard_uncertainty'] = 0.0
    path.write_text(json.dumps(seal(state)))
    with pytest.raises(ValueError, match='physical scale'):
        training_workflow.inspect(directory)
