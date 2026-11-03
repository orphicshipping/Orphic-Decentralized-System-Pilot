"""Actual pinned window/monitor engines through principal-bound metrology.

Set CIW_ACQUIRED_STREAM_STACK_ROOT to the seven exact role-named checkouts.
Synthetic input declarations qualify numerical integration, never equipment,
calibration authenticity, independent reference truth or live acquisition.
"""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import base64
import os
from pathlib import Path
import runpy
import shutil
from types import SimpleNamespace

import pytest

from ciw import calibrated_window as window, residual_monitor as monitor
from ciw import link_contract
from ciw.adapters.protocol import AdapterRefusal
from ciw.adapters.subprocess import PinnedSubprocessAdapter
from ciw.agent_api import encode, parse
from ciw.link_store import Journal
from ciw.metrology_operations import AUTHORITY, TOOLS, MetrologyHost, MetrologyJob
from ciw.operational_agent import OperationalHost
from ciw.operational_cli import default_context, prepare
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import invoke
from companion_policy_helpers import qualified_policies

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def native_stack():
    selected = os.environ.get('CIW_ACQUIRED_STREAM_STACK_ROOT')
    if not selected:
        pytest.skip('Requires exact TBRT/MCUR/STFE/GSIE/SET/FDIR/OIT checkouts')
    root = Path(selected).resolve()
    pins = {**window._pins(), **monitor.PINS}
    paths = {role: str(root / role) for role in pins}
    policies = qualified_policies(paths)
    identities = {}
    for role, pin in pins.items():
        adapter = PinnedSubprocessAdapter(root / role, pin['revision'], pin['module'],
                                          source_root=pin['source_root'], process_policy=policies[role])
        identities[role] = adapter.runtime_identity()
    return SimpleNamespace(paths=paths, identities=identities, policies=policies)


def document(operations):
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'native-metrology-qualification',
            'principals': [{'principal': principal, 'enabled': True,
                'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
                'scientific_operations': list(operations), 'resources': [],
                'reference_scopes': [], 'handoff_recipients': []}
                for principal in ('alice', 'bob')]}


def retained_bytes(facade, artifact, *, attempt=None, review_id=None):
    raw = bytearray()
    while True:
        args = {'job': 'measurement', 'artifact': artifact, 'offset': len(raw), 'limit': 4096}
        if attempt is not None:
            args['attempt'] = attempt
        if review_id is not None:
            args['review_id'] = review_id
        row = invoke('ncp_metrology_read', args, host=facade)
        raw.extend(base64.b64decode(row['bytes_b64'], validate=True))
        if row['next_offset'] is None:
            assert row['byte_count'] == len(raw)
            assert row['sha256'] == 'sha256:' + sha256(raw).hexdigest()
            assert row['authority'] == AUTHORITY
            return bytes(raw)
        assert row['next_offset'] == len(raw)


def configure(owner, directory, native_stack, *, engine, selected, request):
    clock = parse(selected)['device_clock']['clock_id'] if engine == 'calibrated-window' else 'fixture.clock'
    roles = window.ROLES if engine == 'calibrated-window' else monitor.ROLES
    profile = prepare(directory / 'native', investigation_id='native-metrology', sources={'capture': {
        'raw': selected, 'family': engine,
        'context': default_context(clock=clock, origin='synthetic'), 'configurations': []}},
        models={'assessment': {'source': 'capture', 'engine': engine, 'request': request,
            'policy': {'unit': 'm', 'coverage_factor': 2, 'max_abs_error': 1, 'max_standard_uncertainty': 1}}},
        providers={engine: {role: native_stack.paths[role] for role in roles}})
    native = owner.enter_context(OperationalHost(profile,
        process_policies={engine: {role: native_stack.policies[role] for role in roles}}))
    job = MetrologyJob.operational(native, 'assessment')
    value = document(job.operations)
    policy = PrincipalPolicy(lambda: value)
    host = MetrologyHost(policy=policy, principal='alice', tenant='synthetic-lab',
        jobs={'measurement': job}, journal=directory / 'workflow.sqlite', create=True)
    facade = policy.facade('alice', metrology=host)
    assert invoke('ncp_metrology_catalog', {}, host=facade)['jobs'][0]['currently_executable'] is True
    result = invoke('ncp_metrology_run', {'job': 'measurement', 'plan_ref': job.plan_ref,
                                         'attempt': 'native-first'}, host=facade)
    assert result['status'] == 'completed', result
    assert result['interpretation'] == 'INSUFFICIENT_EVIDENCE'
    assert result['authority'] == AUTHORITY
    source = retained_bytes(facade, 'source')
    assert source == request
    finding = parse(retained_bytes(facade, 'finding', attempt='native-first'))
    outcome = parse(retained_bytes(facade, 'outcome', attempt='native-first'))
    calibration = parse(retained_bytes(facade, 'calibration'))
    qualification = {'engine': engine, 'synthetic': True, 'source_sha256': sha256(source).hexdigest(),
        'plan_ref': job.plan_ref, 'result': result, 'outcome': outcome, 'finding': finding,
        'calibration': calibration, 'scientific_operations': list(job.operations)}
    (directory / 'qualification.json').write_bytes(encode(qualification))
    return SimpleNamespace(native=native, host=host, facade=facade, job=job, policy=policy,
        document=value, grant=value['principals'][0], result=result, source=source,
        finding=finding, outcome=outcome, calibration=calibration, directory=directory)


@pytest.fixture(scope='module')
def completed_window(native_stack, tmp_path_factory):
    raw = b'\n' + (ROOT / 'examples/calibrated-window/source.json').read_bytes() + b'\n '
    with ExitStack() as owner:
        yield configure(owner, tmp_path_factory.mktemp('metrology-window'), native_stack,
                        engine='calibrated-window', selected=raw, request=raw)


@pytest.fixture(scope='module')
def upstream_windows(native_stack):
    templates = runpy.run_path(str(ROOT / 'examples/acquired-stream/make_sequence.py'))['templates']()
    sessions = [window.create_session(window.canonical(item),
                {role: native_stack.paths[role] for role in window.ROLES},
                process_policies={role: native_stack.policies[role] for role in window.ROLES}) for item in templates]
    return {item['bundle_digest']: item for item in sessions}


def monitor_request(upstreams, *, held=False):
    configuration = deepcopy(monitor.DEFAULT_CONFIGURATION)
    if held:
        configuration['observability']['condition_limit'] = None
    raw = b'\n' + encode({'schema': monitor.SOURCE_SCHEMA,
        'experiment_id': 'native-metrology-residual', 'window_bundle_ids': list(upstreams),
        'configuration': configuration}) + b'\n '
    return raw, encode({'source': link_contract.packed(raw, 262144), 'upstreams': upstreams})


@pytest.fixture(scope='module')
def completed_monitor(native_stack, upstream_windows, tmp_path_factory):
    raw, request = monitor_request(upstream_windows)
    with ExitStack() as owner:
        yield configure(owner, tmp_path_factory.mktemp('metrology-monitor'), native_stack,
                        engine='residual-monitor', selected=raw, request=request)


def test_native_window_preserves_full_covariance_and_exact_source_context(completed_window, native_stack):
    c = completed_window
    summary = c.finding['data']['model']['native_results']
    steps = summary['steps']
    assert [row['operation_id'] for row in steps] == [op for _, op in window.OPERATIONS]
    clock, calibration, feature, state = [row['numerical_result'] for row in steps]
    assert clock['data']['temporal_covariance'] == [[.25, .25], [.25, .5]]
    assert [row['raw_value'] for row in calibration['data']['samples']] == [2, 4]
    assert [row['corrected_value'] for row in calibration['data']['samples']] == [5, 9]
    assert calibration['data']['temporal_covariance'] == [[5.5, 3.5], [3.5, 8.5]]
    assert calibration['data']['joint_time_value_covariance'][0][2:] == [.125, .125]
    assert feature['mean'] == 7 and feature['variance'] == 5.25
    assert feature['variance'] == 2**2 * .625 + 3**2 * .25 + .5  # Existing affine oracle.
    assert state['components'][0]['value'] == pytest.approx(1.12)
    assert state['covariance'][0][0] == pytest.approx(.84)
    assert c.calibration['declaration'] == parse(c.source)['calibration_profile']
    assert summary['verification']['outcome'] == 'passed'
    for role in window.ROLES:
        assert summary['runtimes'][role] == native_stack.identities[role]
    assert c.finding['data']['model']['held_out'] == 'not_established'
    assert c.result['authority']['physical_qualification'] == 'not_established'


def test_native_monitor_uses_retained_innovations_and_keeps_causal_and_alarm_holds(completed_monitor, upstream_windows, native_stack):
    c = completed_monitor
    request = parse(c.source)
    assert request['upstreams'] == upstream_windows
    assert sorted(c.calibration['upstream_refs']) == sorted(upstream_windows)
    summary = c.finding['data']['model']['native_results']
    data = summary['steps'][0]['numerical_result']['data']
    assert [row['detection']['raw_residual'] for row in data['rows']] == [[0.0], [0.5], [10.0]]
    assert [row['binding']['target_time'] for row in data['rows']] == [2, 4, 6]
    assert data['rows'][-1]['cusum']['status'] == 'statistical_anomaly'
    assert data['rows'][-1]['isolability']['status'] == 'ambiguous'
    assert all(row['isolability']['isolated_fault'] is None for row in data['rows'])
    assert all(row['interpretation']['alarm_authority'] == 'held_unknown_temporal_dependence' for row in data['rows'])
    assert all(pair['residual_cross_covariance'] == 'unknown' for pair in data['overlaps'])
    for row in data['rows']:
        original = upstream_windows[row['binding']['bundle_id']]['steps'][3]
        diagnostics = original['result']['result_artifact']['diagnostics']
        assert row['detection']['raw_residual'] == diagnostics['innovation']
        assert row['detection']['innovation_covariance'] == diagnostics['innovation_covariance']
        assert row['detection']['source_ids'] == [original['result_id']]
    assert summary['verification']['independent'] is False
    runtime = summary['runtimes']['fdir']
    assert runtime['companions']['oit'] == native_stack.identities['oit']
    assert {k: v for k, v in runtime.items() if k != 'companions'} == native_stack.identities['fdir']


def test_native_unresolved_observability_does_not_advance_monitor(native_stack, upstream_windows, tmp_path):
    raw, request = monitor_request(upstream_windows, held=True)
    with ExitStack() as owner:
        c = configure(owner, tmp_path, native_stack, engine='residual-monitor', selected=raw, request=request)
        data = c.finding['data']['model']['native_results']['steps'][0]['numerical_result']['data']
        assert data['final_state'] == {'positive': 0.0, 'negative': 0.0, 'sample_count': 0}
        for row in data['rows']:
            assert row['observability']['status'] == 'unresolved'
            assert row['cusum'] is None
            assert row['monitor_state_before'] == row['monitor_state_after'] == data['final_state']
            assert row['interpretation']['observability_gate'] == 'held'
            assert row['interpretation']['diagnostic_drift_candidate'] is False


@pytest.mark.parametrize('fixture', ['completed_window', 'completed_monitor'])
def test_each_nested_scientific_grant_is_rechecked_before_another_claim(fixture, request):
    c = request.getfixturevalue(fixture)
    before = deepcopy(c.native.agent._attempts)
    with Journal(c.host.journal) as db:
        state = db.state('metrology-attempts')
    original = list(c.grant['scientific_operations'])
    try:
        for operation in original:
            c.grant['scientific_operations'] = [value for value in original if value != operation]
            assert invoke('ncp_metrology_catalog', {}, host=c.facade)['jobs'][0]['currently_executable'] is False
            with pytest.raises(PrincipalRefusal):
                c.host.call('ncp_metrology_run', {'job': 'measurement', 'plan_ref': c.job.plan_ref, 'attempt': 'missing-grant'})
            assert c.native.agent._attempts == before
            with Journal(c.host.journal) as db:
                assert db.state('metrology-attempts') == state
    finally:
        c.grant['scientific_operations'] = original


@pytest.mark.parametrize('fixture', ['completed_window', 'completed_monitor'])
def test_retained_workflow_reopen_reuses_native_ids_and_review_does_not_grant(fixture, request, monkeypatch):
    c = request.getfixturevalue(fixture)
    second = MetrologyHost(policy=c.policy, principal='alice', tenant='synthetic-lab',
        jobs={'measurement': c.job}, journal=c.host.journal)
    facade = c.policy.facade('alice', metrology=second)
    monkeypatch.setattr(c.job, 'execute', lambda *_: pytest.fail('Retained attempt executed a native provider'))
    retry = invoke('ncp_metrology_run', {'job': 'measurement', 'plan_ref': c.job.plan_ref,
                                       'attempt': 'native-first'}, host=facade)
    assert retry['reused_response'] is True
    for field in ('execution_ids', 'result_ids', 'outcome_ref', 'intent_ref'):
        assert retry[field] == c.result[field]
    assert parse(retained_bytes(facade, 'outcome', attempt='native-first')) == c.outcome
    review = invoke('ncp_metrology_review', {'job': 'measurement', 'attempt': 'native-first',
        'outcome_ref': retry['outcome_ref'], 'review_id': 'independence-held',
        'disposition': 'requires_followup',
        'note': 'Synthetic declarations; independent references and physical qualification remain absent.'}, host=facade)
    assert review['authority'] == AUTHORITY
    assert review['authority']['review_grants_execution'] is False
    assert parse(retained_bytes(facade, 'outcome', attempt='native-first')) == c.outcome
    monkeypatch.setitem(c.grant, 'enabled', False)
    assert facade.call('ncp_metrology_read', {'job': 'measurement', 'artifact': 'source'})['status'] == 'refused'
    assert facade.call('ncp_metrology_status', {'job': 'measurement', 'attempt': 'native-first'})['status'] == 'refused'


def test_real_native_checkout_drift_refuses_before_import(native_stack, tmp_path):
    # Copy this tiny source checkout into a test-owned directory, preserving the
    # exact real Git objects through its existing alternates, then change a file.
    bound = tmp_path / 'tbrt'
    shutil.copytree(native_stack.paths['tbrt'], bound, ignore=shutil.ignore_patterns('__pycache__'))
    module = bound / 'src/tbrt/clock.py'
    module.write_bytes(module.read_bytes() + b'\n# deliberate tracked source drift\n')
    pin = window._pins()['tbrt']
    policies = qualified_policies({'tbrt': bound})
    with pytest.raises(AdapterRefusal) as raised:
        PinnedSubprocessAdapter(bound, pin['revision'], pin['module'], source_root=pin['source_root'],
                                process_policy=policies['tbrt'])
    assert raised.value.code == 'SOURCE_PIN_MISMATCH'


def test_trusted_policy_subclass_is_accepted_without_accepting_duck_types(tmp_path):
    """Constructor compatibility only; this does not qualify a Rust binary."""
    from ciw.link_study_cli import example

    class HostPolicy(PrincipalPolicy):
        pass

    request = example()
    profile = prepare(tmp_path / 'native', investigation_id='subclass-binding',
        sources={'capture': {'raw': encode(request['capture']), 'family': 'link-capture',
            'context': default_context(origin='synthetic',
                producer=request['capture']['manifest']['device_id'],
                clock=request['capture']['manifest']['clock']['id'],
                boot=request['capture']['boot_id']), 'configurations': []}},
        models={'assessment': {'source': 'capture', 'engine': 'scalar', 'request': encode(request),
            'policy': {'unit': 'N', 'coverage_factor': 2, 'max_abs_error': .1, 'max_standard_uncertainty': .1}}})
    with OperationalHost(profile) as native:
        job = MetrologyJob.operational(native, 'assessment')
        policy = HostPolicy(lambda: document(job.operations))
        host = MetrologyHost(policy=policy, principal='alice', tenant='synthetic-lab',
            jobs={'measurement': job}, journal=tmp_path / 'accepted.sqlite', create=True)
        assert host.catalog()['jobs'][0]['currently_executable'] is True
        assert native.agent._attempts == {}
        imitation = SimpleNamespace(check_principal=policy.check_principal,
            authorize=policy.authorize, context=policy.context, lock=policy.lock)
        with pytest.raises(TypeError, match='trusted_policy_and_fixed_jobs_required'):
            MetrologyHost(policy=imitation, principal='alice', tenant='synthetic-lab',
                jobs={'measurement': job}, journal=tmp_path / 'rejected.sqlite', create=True)
        assert not (tmp_path / 'rejected.sqlite').exists()
