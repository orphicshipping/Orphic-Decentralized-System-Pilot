"""Independent references and retained lifecycle for the numerical built-ins."""
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pytest

from ciw.core.covariance import create_covariance_artifact, covariance_identity
from ciw.core.identities import content_identity
from ciw.instruments import make_demo_run
from ciw.operations.registry import default_registry
from ciw.session import Session

NAMES = ('uncertainty.propagate.v1', 'uncertainty.budget.v1', 'calibration.fit.v1',
         'calibration.residuals.v1', 'sensitivity.local.v1', 'observability.rank.v1',
         'model.discrepancy.v1')


def axis(ids, units, values, frame='declared-frame'):
    return dict(quantity_ids=ids, units=units, reference_values=values, frame=frame)


def covariance(a, matrix, evidence, metadata=None):
    return create_covariance_artifact(**a, matrix=matrix, method='declared_test_covariance',
        basis={'kind': 'parameter', 'id': 'test-basis'},
        provenance={'provider': 'test-fixture', 'source_evidence_ids': [evidence],
                    'source_covariance_ids': [], 'metadata': metadata or {}},
        assumptions=['Synthetic supplied covariance; no physical traceability claimed.'])


def request_for(name, run):
    evidence = run['evidence_id']
    base = {'source_evidence_id': evidence, 'assumptions': ['Operator-supplied model; synthetic fixture.']}
    if name.startswith(('uncertainty.', 'sensitivity.')):
        inp = axis(['a', 'b'], ['m', 's'], [2., 3.])
        out = axis(['z'], ['kg'], [7.])
        base.update(input=inp, output=out, jacobian=[[2., -1.]])
        if name.startswith('uncertainty.'):
            base['covariance'] = covariance(inp, [[4., 1.], [1., 9.]], evidence)
        else:
            base.update(input_scales=[2., 3.], output_scales=[7.])
    elif name.startswith('calibration.'):
        base.update(sample_ids=['s0', 's1', 's2'], x=[0., 1., 2.], y=[1., 3., 5.],
                    x_unit='count', y_unit='V', frame='sensor', model='affine_exact_x')
        a = axis(base['sample_ids'], ['V']*3, base['y'], 'sensor')
        base['observation_covariance'] = covariance(a, np.eye(3).tolist(), evidence)
        if name == 'calibration.fit.v1':
            base['rcond'] = 1e-12
        else:
            base.update(training_sample_ids=['train0', 'train1'],
                independence='held_out_observations_independent_of_coefficients',
                coefficients=covariance(axis(['offset', 'slope'], ['V', 'V/count'], [1., 2.], 'sensor'),
                    [[.25, .05], [.05, .04]], evidence, {'training_sample_ids': ['train0', 'train1']}))
    elif name == 'observability.rank.v1':
        base.update(states=axis(['position', 'velocity'], ['m', 'm/s'], [0., 0.]),
            outputs=axis(['position_readout'], ['m'], [0.]),
            transition=[[1., 1.], [0., 1.]], observation=[[1., 0.]],
            state_scales=[1., 1.], output_scales=[1.], horizon=2, sample_period_s=1., rcond=1e-12)
    elif name == 'model.discrepancy.v1':
        base.update(sample_ids=['s0', 's1'], observed=[2., 4.], predicted=[1., 2.], unit='m', frame='sensor')
        a = axis(['observed:s0', 'observed:s1', 'predicted:s0', 'predicted:s1'], ['m']*4, [2., 4., 1., 2.], 'sensor')
        base['joint_covariance'] = covariance(a, [[1., 0., .5, 0.], [0., 1., 0., .5],
                                                [.5, 0., 1., 0.], [0., .5, 0., 1.]], evidence)
    return base


def evaluate(name, request, run=None):
    from ciw.numerical_workflow import execute
    return execute(name, run or make_demo_run(), {'request': request})


def test_all_seven_are_builtin_but_discovery_grants_nothing():
    from ciw.control_plane import builtin_registry
    catalog = builtin_registry().catalog()
    assert set(catalog['operations']) == set(NAMES) | {'statistics.v1', 'spectrum.periodogram.v1'}
    assert not catalog['authorizes_execution']
    assert not any(v['bound'] for v in catalog['operations'].values())
    bound = builtin_registry(bind=True).catalog()
    assert all(v['bound'] for v in bound['operations'].values())


def test_covariance_and_budget_keep_signed_cross_terms():
    run = make_demo_run()
    for name in NAMES[:2]:
        request = request_for(name, run)
        before = deepcopy(request)
        value = evaluate(name, request, run)['result']
        assert value['covariance']['matrix'] == [[21.]]  # 2²*4 + (-1)²*9 + 2*2*(-1)*1
        assert value['standard_uncertainty'] == pytest.approx([21.**.5])
        assert value['covariance']['units'] == ['kg']
        assert value['covariance']['provenance']['source_covariance_ids'] == [request['covariance']['covariance_id']]
        if name.endswith('budget.v1'):
            assert value['diagonal_terms'] == [[16., 9.]]
            assert value['cross_terms'] == [{'pair': ['a', 'b'], 'contributions': [-4.]}]
        assert request == before


def test_random_covariance_matches_decimal_reference():
    rng = np.random.default_rng(20261017)
    run = make_demo_run()
    for _ in range(20):
        b = rng.integers(-4, 5, size=(3, 3))
        c = (b @ b.T).tolist()
        j = rng.integers(-4, 5, size=(2, 3)).tolist()
        request = request_for(NAMES[0], run)
        request.update(input=axis(['a','b','c'], ['m','s','K'], [0.,0.,0.]),
            output=axis(['u','v'], ['N','V'], [0.,0.]), jacobian=j)
        request['covariance'] = covariance(request['input'], c, run['evidence_id'])
        actual = evaluate(NAMES[0], request, run)['result']['covariance']['matrix']
        expected = [[float(sum(Decimal(j[r][i])*Decimal(c[i][k])*Decimal(j[s][k])
                    for i in range(3) for k in range(3))) for s in range(2)] for r in range(2)]
        np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-12)


def test_affine_gls_preserves_absolute_covariance_on_perfect_fit():
    run = make_demo_run()
    value = evaluate(NAMES[2], request_for(NAMES[2], run), run)['result']
    assert value['coefficients']['reference_values'] == pytest.approx([1., 2.])
    np.testing.assert_allclose(value['coefficients']['matrix'], [[5/6, -.5], [-.5, .5]], rtol=1e-12, atol=1e-12)
    assert value['degrees_of_freedom'] == 1
    assert value['chi_square'] == pytest.approx(0., abs=1e-24)
    assert value['coefficients']['provenance']['metadata']['training_sample_ids'] == ['s0','s1','s2']


def test_gls_correlated_noise_matches_independent_normal_equations():
    run = make_demo_run()
    req = request_for(NAMES[2], run)
    req['y'] = [1., 4., 5.]
    c = np.array([[2., .5, 0.], [.5, 1., .25], [0., .25, 3.]])
    req['observation_covariance'] = covariance(axis(req['sample_ids'], ['V']*3, req['y'], 'sensor'), c.tolist(), run['evidence_id'])
    # Independent normal-equation reference; implementation must use whitening/SVD.
    design = np.column_stack((np.ones(3), req['x']))
    precision = np.linalg.inv(c)
    expected_cov = np.linalg.inv(design.T @ precision @ design)
    expected = expected_cov @ design.T @ precision @ req['y']
    value = evaluate(NAMES[2], req, run)['result']
    np.testing.assert_allclose(value['coefficients']['reference_values'], expected, rtol=1e-12)
    np.testing.assert_allclose(value['coefficients']['matrix'], expected_cov, rtol=1e-12)


def test_held_out_residuals_include_coefficient_covariance():
    run = make_demo_run()
    value = evaluate(NAMES[3], request_for(NAMES[3], run), run)['result']
    assert value['residuals'] == pytest.approx([0.,0.,0.])
    # H C_beta H^T + C_y, including off-diagonal prediction correlation.
    np.testing.assert_allclose(value['covariance']['matrix'], [[1.25,.30,.35],[.30,1.39,.48],[.35,.48,1.61]])


def test_sensitivity_is_scaled_declared_jacobian_not_invented_derivative():
    run = make_demo_run()
    value = evaluate(NAMES[4], request_for(NAMES[4], run), run)['result']
    assert value['jacobian'] == [[2., -1.]]
    np.testing.assert_allclose(value['scaled_jacobian'], [[4/7, -3/7]])
    assert value['method'] == 'supplied_jacobian'


def test_observability_rank_and_missing_state():
    run = make_demo_run()
    req = request_for(NAMES[5], run)
    value = evaluate(NAMES[5], req, run)['result']
    assert value['rank'] == 2 and value['full_rank_over_horizon']
    assert value['observability_matrix'] == [[1.,0.], [1.,1.]]
    req['transition'] = [[1.,0.],[0.,1.]]
    value = evaluate(NAMES[5], req, run)['result']
    assert value['rank'] == 1 and not value['full_rank_over_horizon']
    assert value['condition_number'] is None


def test_discrepancy_includes_cross_covariance_and_no_acceptance_claim():
    run = make_demo_run()
    value = evaluate(NAMES[6], request_for(NAMES[6], run), run)['result']
    assert value['residuals'] == [1.,2.]
    assert value['covariance']['matrix'] == [[1.,0.],[0.,1.]]
    assert value['bias'] == 1.5
    assert value['rmse'] == pytest.approx(2.5**.5)
    assert value['chi_square'] == pytest.approx(5.)
    assert 'pass' not in value and 'accepted' not in value


@pytest.mark.parametrize('name', NAMES)
def test_session_retains_reopens_without_reexecuting_and_fresh_replay(name, tmp_path):
    run = make_demo_run()
    session = Session(run, tmp_path / 'session')
    parameters = {'request': request_for(name, run)}
    message = {'protocol_version':1, 'request_id':'numerical-test', 'type':'operation.execute',
               'payload':{'operation_id':name, 'parameters':parameters}}
    reply = session.handle(message)
    assert reply['type'] == 'response', reply
    outcome = reply['payload']
    assert outcome['status'] == 'completed', outcome
    assert outcome['result']['verification_status'] == 'not_verified'
    path = session.save_workspace(tmp_path / 'workspace.json')
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('offline read must not execute')):
        restored = Session.from_workspace(path, tmp_path / 'restored')
    assert restored.results == session.results
    second = restored.handle(message)['payload']
    assert second['status'] == 'completed'
    assert second['result']['result_id'] != outcome['result']['result_id']
    assert second['result']['data'] == outcome['result']['data']


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('attack', ['unknown_field','wrong_evidence','missing_assumptions'])
def test_request_boundary_refuses(name, attack):
    run = make_demo_run()
    req = request_for(name, run)
    if attack == 'unknown_field': req['execute_shell'] = 'not allowed'
    elif attack == 'wrong_evidence': req['source_evidence_id'] = 'sha256:' + '0'*64
    else: req.pop('assumptions')
    with pytest.raises(ValueError): evaluate(name, req, run)


@pytest.mark.parametrize('attack', ['order','units','frame','negative_variance','indefinite','bool','nan','shape'])
def test_uncertainty_refuses_bad_binding_or_matrix(attack):
    run = make_demo_run()
    req = request_for(NAMES[0], run)
    if attack == 'order': req['input']['quantity_ids'].reverse()
    elif attack == 'units': req['input']['units'][0] = 'mm'
    elif attack == 'frame': req['input']['frame'] = 'another-frame'
    elif attack == 'negative_variance': req['covariance']['matrix'][0][0] = -1.
    elif attack == 'indefinite': req['covariance']['matrix'] = [[1.,2.],[2.,1.]]
    elif attack == 'bool': req['jacobian'][0][0] = True
    elif attack == 'nan': req['jacobian'][0][0] = float('nan')
    else: req['jacobian'] = [[1.]]
    if attack in {'negative_variance','indefinite'}:
        req['covariance']['covariance_id'] = covariance_identity(req['covariance'])
    with pytest.raises(ValueError): evaluate(NAMES[0], req, run)


def test_singular_calibration_and_training_overlap_refuse():
    run = make_demo_run()
    req = request_for(NAMES[2], run)
    req['x'] = [1.,1.,1.]
    with pytest.raises(ValueError): evaluate(NAMES[2], req, run)
    req = request_for(NAMES[3], run)
    req['training_sample_ids'][0] = 's0'
    with pytest.raises(ValueError): evaluate(NAMES[3], req, run)


def test_propagation_avoids_intermediate_underflow_and_overflow():
    run = make_demo_run()
    req = request_for(NAMES[0],run)
    req.update(input=axis(['a'],['m'],[0.]),output=axis(['u'],['m'],[0.]),jacobian=[[1e200]])
    req['covariance'] = covariance(req['input'],[[1e-300]],run['evidence_id'])
    result = evaluate(NAMES[0],req,run)['result']
    assert result['covariance']['matrix'][0][0] == pytest.approx(1e100,rel=1e-14)
    req['jacobian'] = [[1e-200]]
    req['covariance'] = covariance(req['input'],[[1e300]],run['evidence_id'])
    assert evaluate(NAMES[0],req,run)['result']['covariance']['matrix'][0][0] == pytest.approx(1e-100,rel=1e-14)
    req['covariance'] = covariance(req['input'],[[1.]],run['evidence_id'])
    with pytest.raises(ValueError): evaluate(NAMES[0],req,run)


def test_singular_uncertainty_is_preserved_and_discrepancy_is_indeterminate():
    run = make_demo_run()
    req = request_for(NAMES[0],run)
    req['jacobian'] = [[1.,1.]]
    req['covariance'] = covariance(req['input'],[[1.,-1.],[-1.,1.]],run['evidence_id'])
    assert evaluate(NAMES[0],req,run)['result']['standard_uncertainty'] == [0.]
    req = request_for(NAMES[6],run)
    req['joint_covariance']['matrix'] = [[0.]*4 for _ in range(4)]
    req['joint_covariance']['covariance_id'] = covariance_identity(req['joint_covariance'])
    value = evaluate(NAMES[6],req,run)['result']
    assert value['chi_square'] is None
    assert value['standardized_residuals'] == [None,None]


@pytest.mark.parametrize('name',NAMES)
def test_resealed_result_cannot_lose_request_binding_or_add_claims(name,tmp_path):
    from ciw.operations.runner import seal
    from ciw.session import read_json,write_json
    run = make_demo_run()
    session = Session(run,tmp_path/'session')
    session.handle({'protocol_version':1,'request_id':'tamper','type':'operation.execute',
        'payload':{'operation_id':name,'parameters':{'request':request_for(name,run)}}})
    path = session.save_workspace(tmp_path/'workspace.json')
    saved = read_json(path)
    saved['results'][0]['data']['scope'] = 'physically_qualified'
    seal(saved['results'][0])
    write_json(path,saved)
    with pytest.raises(ValueError): Session.from_workspace(path,tmp_path/'tampered')


@pytest.mark.parametrize('horizon',[True,0,33,1.5])
def test_observability_horizon_is_bounded(horizon):
    run = make_demo_run()
    req = request_for(NAMES[5],run); req['horizon'] = horizon
    with pytest.raises(ValueError): evaluate(NAMES[5],req,run)


def test_observability_is_invariant_under_declared_coordinate_rescaling():
    run = make_demo_run()
    req = request_for(NAMES[5],run)
    original = evaluate(NAMES[5],req,run)['result']
    req['states']['units'][0] = 'mm'
    req['state_scales'][0] = 1000.
    req['transition'] = [[1.,1000.],[0.,1.]]
    req['observation'] = [[.001,0.]]
    assert evaluate(NAMES[5],req,run)['result'] == original


def test_fit_handles_large_offset_without_normal_equation_instability():
    run = make_demo_run()
    req = request_for(NAMES[2],run); req['x'] = [1e8,1e8+1,1e8+2]
    result = evaluate(NAMES[2],req,run)['result']
    assert result['coefficients']['reference_values'] == pytest.approx([1-2e8,2.],rel=1e-12)
    assert result['predicted'] == pytest.approx([1.,3.,5.],abs=1e-12)


def test_refused_operation_is_retained_without_publishing_result(tmp_path):
    run = make_demo_run(); req = request_for(NAMES[2],run); req['x'] = [1.,1.,1.]
    session = Session(run,tmp_path/'session')
    value = session.handle({'protocol_version':1,'request_id':'refuse','type':'operation.execute',
        'payload':{'operation_id':NAMES[2],'parameters':{'request':req}}})['payload']
    assert value['status'] == 'refused' and value['result'] is None
    path = session.save_workspace(tmp_path/'refused.json')
    assert Session.from_workspace(path,tmp_path/'restore').executions == session.executions


def test_saved_runtime_cannot_be_relabelled_as_another_provider(tmp_path):
    from ciw.operations.runner import seal
    from ciw.session import read_json,write_json
    run = make_demo_run(); session = Session(run,tmp_path/'session')
    session.handle({'protocol_version':1,'request_id':'runtime','type':'operation.execute',
        'payload':{'operation_id':NAMES[0],'parameters':{'request':request_for(NAMES[0],run)}}})
    path = session.save_workspace(tmp_path/'workspace.json'); saved = read_json(path)
    for collection in ('results','executions'):
        saved[collection][0]['runtime']['provider'] = 'another-provider'
        seal(saved[collection][0])
    write_json(path,saved)
    with pytest.raises(ValueError): Session.from_workspace(path,tmp_path/'restored')


@pytest.mark.parametrize('replacement', [True, None, '1.5'])
def test_offline_statistics_reject_non_numeric_scalars(replacement):
    from ciw.numerical_records import validate_payload
    run = make_demo_run(); req = request_for(NAMES[6],run)
    data = evaluate(NAMES[6],req,run)
    data['result']['bias'] = replacement
    with pytest.raises(ValueError):
        validate_payload(NAMES[6],data,run,{'request':req},None)


def test_offline_jacobian_rejects_boolean_equal_to_numeric_one():
    from ciw.numerical_records import validate_payload
    run = make_demo_run(); req = request_for(NAMES[4],run)
    req['jacobian'][0][0] = 1.
    data = evaluate(NAMES[4],req,run)
    data['result']['jacobian'][0][0] = True
    with pytest.raises(ValueError):
        validate_payload(NAMES[4],data,run,{'request':req},None)
