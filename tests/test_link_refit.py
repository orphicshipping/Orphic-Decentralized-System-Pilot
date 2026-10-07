from copy import deepcopy
import pytest
from ciw import link_refit as refit, link_study_io, link_measurements, link_study
from ciw.link_study_cli import example
from ciw.board_refit import BoardRefitHost, BoardRefitRefusal
from ciw.core.identities import content_identity


def inputs(tmp_path, request=None):
    link_study_io.run(example() if request is None else request, tmp_path / 'study')
    bundle = link_measurements.build(tmp_path / 'study')
    plan = refit.plan_for(bundle, instance_id='synthetic.station', max_standard_uncertainty=.03)
    profile = refit.GaugeLinkRefitProfile(bundle, plan)
    board = profile.board()
    grant = {'schema': 'ciw.board-refit-grant.v1', 'grant_id': 'synthetic-scenario-test',
        'board_ref': board['record_digest'], 'source_evidence_id': profile.source['evidence_id'],
        'source_ref': content_identity(profile.source), 'allow_baseline': True,
        'targets': [{'node_id': 'hypothesis', 'parameter': 'offset_scale'}],
        'max_candidate_runs': 3, 'max_previews': 10}
    return profile, board, grant


def host_for(tmp_path, values, **kwargs):
    profile, board, grant = values
    return BoardRefitHost(board, tmp_path / 'runtime', source=profile.source,
        grant=grant, execution_profile=profile, **kwargs)


def preview(host, scale=.25):
    return host.call('ncp_board_preview', {'base_board_ref': host.board['record_digest'],
        'node_id': 'hypothesis', 'parameter': 'offset_scale', 'replacement': scale})


def journey(host):
    p = preview(host)
    assert p['affected_nodes'] == ['hypothesis', 'audit'] or set(p['affected_nodes']) == {'hypothesis','audit'}
    assert p['unaffected_nodes'] == ['basis']
    v = host.call('ncp_board_validate', {'preview_ref': p['preview_ref']})
    b = host.call('ncp_board_baseline', {'attempt_id': 'baseline'})
    args = {'preview_ref': p['preview_ref'], 'validation_ref': v['validation_ref'],
            'baseline_ref': b['baseline_ref'], 'attempt_id': 'candidate'}
    return host.call('ncp_board_recompute', args), args


def test_retained_journey_and_reconnect(tmp_path, monkeypatch):
    values = inputs(tmp_path)
    # Any attempt to execute the original scalar providers is a test failure.
    from ciw.operations.registry import OperationRegistry
    original_get = OperationRegistry.get
    executed = []
    def checked_get(registry, operation_id):
        operation = original_get(registry, operation_id)
        from ciw.operations.registry import Operation
        def execute(source, parameters):
            executed.append(operation_id)
            assert operation_id not in link_study.OPS
            return operation.execute(source, parameters)
        return Operation(operation_id, operation.role, execute, operation.runtime_identity)
    monkeypatch.setattr(OperationRegistry, 'get', checked_get)
    with host_for(tmp_path, values) as host:
        candidate, args = journey(host)
        assert candidate['status'] == 'completed'
        assert candidate['reused_nodes'] == ['basis']
        assert set(candidate['rerun_nodes']) == {'hypothesis', 'audit'}
        assert candidate['accepted'] is False and candidate['review_status'] == 'unreviewed'
        baseline = host._baseline()
        result = host._candidate_bundle(candidate['candidate_ref'])
        assert len(baseline['workspace']['results']) == 7
        assert len(result['workspace']['results']) == 9
        assert result['run']['nodes']['audit']['result']['data']['status'] == 'PASS'
        native = result['run']['nodes']['hypothesis']['result']['data']['feedback']
        assert len(native['cross_terms']) > 0
        assert result['run']['nodes']['hypothesis']['result']['parameters']['offset_scale'] == .25
        assert candidate['node_result_refs']['basis'] == host._baseline_summary()['node_result_refs']['basis']
        compare = host.call('ncp_board_compare', {'candidate_ref': candidate['candidate_ref']})
        assert compare['accepted'] is False
    with host_for(tmp_path, values, resume=True) as host:
        assert host.call('ncp_board_recompute', args) == candidate
        assert host.call('ncp_board_candidate', {'candidate_ref': candidate['candidate_ref']}) == candidate
    assert executed == [refit.OPS[0], refit.OPS[1], refit.OPS[2], refit.OPS[1], refit.OPS[2]]


def test_refuse_missing_covariance(tmp_path):
    request = example()
    request['calibration']['joint_covariance'] = None
    with pytest.raises(ValueError, match='covariance'):
        inputs(tmp_path, request)


def test_no_authority_to_edit_threshold_basis_or_gain(tmp_path):
    with host_for(tmp_path, inputs(tmp_path)) as host:
        for node, parameter in [('basis','basis_ref'), ('hypothesis','gain'), ('hypothesis','max_standard_uncertainty')]:
            with pytest.raises(BoardRefitRefusal):
                host.call('ncp_board_preview', {'base_board_ref': host.board['record_digest'],
                    'node_id': node, 'parameter': parameter, 'replacement': 1})
        for value in [-.1, 1, True]:
            with pytest.raises(BoardRefitRefusal):
                preview(host, value)


def test_provider_drift_refuses_live_calls_and_resume(tmp_path, monkeypatch):
    values = inputs(tmp_path)
    host = host_for(tmp_path, values)
    identity = refit.runtime_identity()
    monkeypatch.setattr(refit, 'runtime_identity', lambda: {**identity, 'version':'changed'})
    with pytest.raises(BoardRefitRefusal, match='retained_state_invalid'):
        host.call('ncp_board_status', {})
    host.close()
    with pytest.raises(ValueError):
        host_for(tmp_path, values, resume=True)


def test_cross_terms_match_independent_fraction_reference(tmp_path):
    from fractions import Fraction
    import math
    values = inputs(tmp_path)
    with host_for(tmp_path, values) as host:
        candidate, _ = journey(host)
        retained = host._candidate_bundle(candidate['candidate_ref'])
        feedback = retained['run']['nodes']['hypothesis']['result']['data']['feedback']
        request = link_study.source_request(values[0].source)
        readings = [Fraction(row['value']) for row in link_study.decode(request)['rows']]
        n = len(readings)
        g = [Fraction(request['calibration']['gain'])/n] * n + [sum(readings)/n, Fraction(1)]
        sigma = [[Fraction(v) for v in row] for row in request['calibration']['joint_covariance']['matrix']]
        d = [Fraction(1)] * (n + 1) + [Fraction(1,4)]
        expected = float(sum(g[i]*d[i]*sigma[i][j]*d[j]*g[j] for i in range(n+2) for j in range(n+2)))
        actual = feedback['candidates'][0]['variance']
        # Criterion specified before comparison: <= 8 ULP, matching the independent auditor.
        assert abs(actual - expected) <= 8 * math.ulp(expected)
        assert len(feedback['cross_terms']) == (n+2)*(n+1)//2
        diagonal_only = float(sum(g[i]**2*d[i]**2*sigma[i][i] for i in range(n+2)))
        assert actual != diagonal_only
        assert values[0].basis['plan']['candidates'][0]['scales'][-1] == .5


def test_refit_refuses_stale_validation_conflicting_attempt_and_changed_grant(tmp_path):
    values = inputs(tmp_path)
    with host_for(tmp_path, values) as host:
        candidate, args = journey(host)
        p = preview(host, .1)
        with pytest.raises(BoardRefitRefusal, match='validation_mismatch'):
            host.call('ncp_board_recompute', {**args,'preview_ref':p['preview_ref']})
        v = host.call('ncp_board_validate', {'preview_ref':p['preview_ref']})
        with pytest.raises(BoardRefitRefusal, match='attempt_conflict'):
            host.call('ncp_board_recompute', {**args,'preview_ref':p['preview_ref'],'validation_ref':v['validation_ref']})
        assert host.call('ncp_board_status', {})['budgets']['candidate_runs_used'] == 1
    changed = deepcopy(values[2]); changed['max_candidate_runs'] += 1
    with pytest.raises(ValueError, match='bindings differ'):
        host_for(tmp_path, (values[0],values[1],changed), resume=True)


def test_interrupted_candidate_is_not_replayed_or_renamed(tmp_path, monkeypatch):
    values = inputs(tmp_path)
    host = host_for(tmp_path, values)
    p = preview(host)
    v = host.call('ncp_board_validate', {'preview_ref':p['preview_ref']})
    b = host.call('ncp_board_baseline', {'attempt_id':'baseline'})
    args = {'preview_ref':p['preview_ref'],'validation_ref':v['validation_ref'],
            'baseline_ref':b['baseline_ref'],'attempt_id':'interrupted'}
    def stop(*args, **kwargs):
        raise KeyboardInterrupt('Simulated interruption after durable claim')
    monkeypatch.setattr(host, '_workbench', stop)
    with pytest.raises(KeyboardInterrupt):
        host.call('ncp_board_recompute', args)
    with host_for(tmp_path, values, resume=True) as recovered:
        for attempt in ('interrupted','renamed'):
            with pytest.raises(BoardRefitRefusal, match='attempt_outcome_unknown'):
                recovered.call('ncp_board_recompute', {**args,'attempt_id':attempt})
        state = recovered.call('ncp_board_status', {})
        assert state['budgets']['candidate_runs_used'] == 1 and state['candidate_refs'] == []


@pytest.mark.parametrize('field', ['bundle_id','source_evidence_id','model_result_id','audit_result_id'])
def test_mismatched_plan_bindings_refused(tmp_path, field):
    profile, _, _ = inputs(tmp_path)
    plan = deepcopy(profile.basis['plan'])
    plan[field] = 'sha256:' + '0'*64
    with pytest.raises(ValueError):
        refit.GaugeLinkRefitProfile(profile.bundle, plan)


def test_original_occurrences_cannot_be_substituted_or_dropped(tmp_path):
    profile, _, _ = inputs(tmp_path)
    workspace = deepcopy(profile.original)
    workspace['results'].pop()
    with pytest.raises(ValueError, match='changed or omitted'):
        profile.check_workspace(workspace, None)


def test_equivalent_numeric_preview_cannot_replay_unknown_attempt(tmp_path, monkeypatch):
    values = inputs(tmp_path)
    host = host_for(tmp_path, values)
    requests = []
    for replacement in (0, 0.0):
        p = preview(host, replacement)
        v = host.call('ncp_board_validate', {'preview_ref':p['preview_ref']})
        requests.append({'preview_ref':p['preview_ref'], 'validation_ref':v['validation_ref']})
    assert requests[0]['preview_ref'] != requests[1]['preview_ref']
    b = host.call('ncp_board_baseline', {'attempt_id':'baseline'})
    def crash(*args, **kwargs):
        raise KeyboardInterrupt('Unknown after durable execution claim')
    monkeypatch.setattr(host, '_workbench', crash)
    with pytest.raises(KeyboardInterrupt):
        host.call('ncp_board_recompute', {**requests[0], 'baseline_ref':b['baseline_ref'], 'attempt_id':'first'})
    with host_for(tmp_path, values, resume=True) as recovered:
        dispatched = []
        def forbidden(attempt):
            dispatched.append(attempt)
            raise AssertionError('Equivalent unknown computation dispatched twice')
        monkeypatch.setattr(recovered, '_workbench', forbidden)
        with pytest.raises(BoardRefitRefusal, match='attempt_outcome_unknown'):
            recovered.call('ncp_board_recompute', {**requests[1], 'baseline_ref':b['baseline_ref'], 'attempt_id':'renamed'})
        assert dispatched == []
        assert recovered.call('ncp_board_status', {})['budgets']['candidate_runs_used'] == 1
