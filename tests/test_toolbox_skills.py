"""Bounded skill execution through real principal-bound local service profiles."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ciw.context_catalog import for_principal_facade
from ciw.evidence_corpus import compile_evidence
from ciw.principal_policy import PrincipalPolicy
from ciw.services import open_reference
from ciw.toolbox_recipes import builtin_skills
from ciw.toolbox_skills import SkillRegistry, SkillRunner, digest
from logistics_fixture_helpers import Fixture, host as logistics_host
from reference_fixture_helpers import make_catalog


OPERATIONS = ['ncp_knowledge_resolve', 'ncp_evidence_search', 'ncp_evidence_span',
              'ncp_knowledge_overview', 'ncp_logistics_catalog', 'ncp_logistics_retained_read']


@pytest.fixture
def configured(tmp_path, monkeypatch):
    if shutil.which('node') is None:
        pytest.skip('local OLM canonical-digest fixture requires Node')
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    text = b'Synthetic calibration uses an independent reference standard.\n'
    corpus, record_id, source = make_catalog(tmp_path / 'input', text)
    selection = tmp_path / 'selection.json'
    selection.write_text(json.dumps({'schema': 'ciw.evidence-selection.v1',
        'disclosure_ref': 'fixture:toolbox-skill-local',
        'sources': [{'record_id': record_id, 'path': str(source)}]}))
    evidence = tmp_path / 'evidence'
    eref = compile_evidence(catalog=corpus, selection=selection, output=evidence)
    reference = open_reference(catalog_path=tmp_path / 'input/catalog-inputs/bundle',
        catalog_ref=corpus.ref, evidence_path=evidence, evidence_ref=eref)
    def grant(principal):
        return {'principal': principal, 'enabled': True,
            'operations': [{'name': n, 'constraints': {}} for n in OPERATIONS],
            'scientific_operations': [], 'resources': [],
            'reference_scopes': [corpus.ref, eref], 'handoff_recipients': []}
    value = {'schema': 'ciw.principal-policy.v1', 'revision': 'toolbox-test',
             'principals': [grant('alice'), grant('bob')]}
    policy = PrincipalPolicy(lambda: value)
    transport = Fixture()
    logistics = logistics_host(transport, principal='alice')
    facade = policy.facade('alice', reference=reference, logistics=logistics)
    admissions = []
    def admit(intent):
        admissions.append(deepcopy(intent))
        return True
    registry = builtin_skills()
    def runner(**kwargs):
        return SkillRunner(facade=kwargs.pop('facade', facade), registry=kwargs.pop('registry', registry),
            tenant=kwargs.pop('tenant', 'synthetic-tenant'), pins={'odsp': 'cef7d007'},
            replay_identity=kwargs.pop('replay_identity', 'test-replay'),
            admit_step=kwargs.pop('admit_step', admit), **kwargs)
    return SimpleNamespace(text=text, corpus=corpus, evidence_ref=eref, record_id=record_id,
        reference=reference, transport=transport, logistics=logistics, facade=facade,
        value=value, policy=policy, grant=value['principals'][0], registry=registry,
        admissions=admissions, runner=runner)


def citation(runner, *, attempt='citation', **kwargs):
    return runner.run('reference-citation', '1.0.0',
        {'source_id': 'gdrive:Original', 'query': 'calibration'}, attempt=attempt, **kwargs)


def test_reference_citation_real_services_and_retained_receipts(configured):
    c = configured
    active = c.runner()
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        result = citation(active)
    assert result['status'] == 'completed' and result['steps_completed'] == 3
    assert [call.args[0] for call in calls.call_args_list] == OPERATIONS[:3]
    assert [r['step_id'] for r in c.admissions] == ['resolve', 'search', 'passage']
    assert len({r['arguments_sha256'] for r in c.admissions}) == 3
    assert all(r['skill_sha256'] and r['schema_sha256'] and r['scope_sha256'] for r in c.admissions)
    final = active.receipt(result['artifact_id'])['receipt']
    assert final['status'] == 'completed' and final['authorizes_execution'] is False
    assert final['evidence_admission'] is False and final['freshness_or_truth_established'] is False
    assert final['receipt_sha256'] == digest({k: v for k, v in final.items() if k != 'receipt_sha256'})
    passage = active.receipt(final['steps'][-1]['artifact_id'])['receipt']
    assert passage['result']['span']['text'].encode() == c.text
    assert passage['result_sha256'] == digest(passage['result'])
    assert active.receipt(result['artifact_id'])['receipt'] == final


def test_cross_profile_provenance_uses_retained_upstream_locator(configured):
    c = configured
    active = c.runner()
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        result = active.run('cross-profile-provenance', '1.0.0', {}, attempt='audit')
    assert result['status'] == 'completed' and result['steps_completed'] == 3
    assert [r.args[0] for r in calls.call_args_list] == OPERATIONS[3:]
    final = active.receipt(result['artifact_id'])['receipt']
    catalog = active.receipt(final['steps'][1]['artifact_id'])['receipt']['result']
    assert calls.call_args_list[-1].args[1]['artifact_id'] == catalog['artifact_id']
    readback = active.receipt(final['steps'][2]['artifact_id'])['receipt']['result']
    assert readback['artifact_id'] == catalog['artifact_id']
    assert catalog['synthetic'] is True and catalog['authorizes_execution'] is False
    assert len(c.transport.calls) == 1  # retained readback makes no new upstream call


def test_per_step_admission_defaults_to_deny(configured):
    c = configured
    active = c.runner(admit_step=None)
    with patch.object(c.facade, 'call', side_effect=AssertionError('unadmitted dispatch')):
        result = citation(active)
    assert result['status'] == 'refused' and result['steps_completed'] == 0
    assert active.receipt(result['artifact_id'])['receipt']['steps'] == []


def test_fresh_admission_and_revocation_before_canonical_invoke(configured):
    c = configured
    intents = []
    def admit(intent):
        intents.append(deepcopy(intent))
        if intent['step_id'] == 'search':
            c.grant['operations'] = [r for r in c.grant['operations'] if r['name'] != 'ncp_evidence_search']
        return True
    active = c.runner(admit_step=admit)
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        result = citation(active)
    assert result['status'] == 'refused' and result['steps_completed'] == 1
    assert [r['step_id'] for r in intents] == ['resolve', 'search']
    assert [r.args[0] for r in calls.call_args_list] == ['ncp_knowledge_resolve']
    with pytest.raises(ValueError):
        active.receipt(result['artifact_id'])
    assert c.facade.call('ncp_evidence_search', {'query': 'calibration'})['status'] == 'refused'


def test_cancellation_during_admission_stops_before_next_step(configured):
    c = configured
    holder = {}
    cancellation = []
    def admit(intent):
        if intent['step_id'] == 'search':
            cancellation.append(holder['runner'].cancel(intent['attempt']))
        return True
    active = holder['runner'] = c.runner(admit_step=admit)
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        result = citation(active)
    assert result['status'] == 'cancelled' and result['steps_completed'] == 1
    assert [r.args[0] for r in calls.call_args_list] == ['ncp_knowledge_resolve']
    assert cancellation == [{'cancel_requested': True, 'termination_established': False, 'authorizes_execution': False}]
    assert active.receipt(result['artifact_id'])['receipt']['status'] == 'cancelled'
    with pytest.raises(ValueError):
        active.cancel('nonexistent')


def test_load_selects_client_context_without_service_or_discovery_mutation(configured):
    c = configured
    selector = for_principal_facade(c.facade)
    before = c.facade.descriptions()
    with patch.object(c.facade, 'call', side_effect=AssertionError('loading invoked service')):
        selected = c.registry.load('reference-citation', '1.0.0', selector)
    assert {r['name'] for r in selected['tools']} == set(OPERATIONS[:3])
    assert selected['authorizes_execution'] is False
    assert c.facade.descriptions() == before and c.transport.calls == []
    assert c.admissions == []


def test_duplicate_attempt_refuses_without_rerun(configured):
    c = configured
    active = c.runner()
    citation(active)
    admitted = len(c.admissions)
    with patch.object(c.facade, 'call', side_effect=AssertionError('duplicate attempt reran')):
        with pytest.raises(ValueError, match='attempt'):
            citation(active)
    assert len(c.admissions) == admitted


def test_receipt_owner_replay_and_tenant_isolation(configured):
    c = configured
    active = c.runner()
    artifact = citation(active)['artifact_id']
    bob_facade = c.policy.facade('bob', reference=c.reference,
        logistics=logistics_host(Fixture(), principal='bob'))
    bob = c.runner(facade=bob_facade)
    with pytest.raises(ValueError):
        bob.receipt(artifact)
    other_replay = c.runner(replay_identity='other-replay')
    with pytest.raises(ValueError):
        other_replay.receipt(artifact)
    with pytest.raises(ValueError, match='tenant'):
        c.runner(tenant='other-tenant')
    assert c.logistics.store.owner != bob_facade.hosts['logistics'].store.owner


def test_missing_source_and_no_evidence_span_fail_closed(configured):
    c = configured
    active = c.runner()
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        missing_source = active.run('reference-citation', '1.0.0',
            {'source_id': 'gdrive:Missing', 'query': 'calibration'}, attempt='missing-source')
    assert missing_source['status'] in {'refused', 'outcome_unknown'}
    assert missing_source['steps_completed'] == 0
    assert [r.args[0] for r in calls.call_args_list] == ['ncp_knowledge_resolve']
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        no_span = active.run('reference-citation', '1.0.0',
            {'source_id': 'gdrive:Original', 'query': 'unmatchedword'}, attempt='missing-span')
    assert no_span['status'] == 'refused' and no_span['steps_completed'] == 2
    assert [r.args[0] for r in calls.call_args_list] == ['ncp_knowledge_resolve', 'ncp_evidence_search']


def test_complete_summary_budget_checked_before_side_effects(configured):
    c = configured
    active = c.runner()
    with patch.object(c.facade, 'call', side_effect=AssertionError('bad budget dispatched')):
        for maximum in (True, 0, 127, 128, 262145):
            with pytest.raises(ValueError):
                citation(active, max_bytes=maximum)
    assert not active._attempts and c.admissions == []


def test_schema_drift_and_input_bounds_refuse_before_service(configured):
    c = configured
    active = c.runner()
    rows = deepcopy(c.reference.descriptions())
    for row in rows:
        row['inputSchema']['title'] = 'changed-host-schema'
    with patch.object(c.reference, 'descriptions', return_value=rows):
        with patch.object(c.facade, 'call', side_effect=AssertionError('schema drift dispatched')):
            result = citation(active)
    assert result['status'] == 'refused' and result['steps_completed'] == 0
    with patch.object(c.facade, 'call', side_effect=AssertionError('invalid input dispatched')):
        for inputs in ({'source_id': 'x'}, {'source_id': 'x', 'query': 'q', 'principal': 'bob'},
                       {'source_id': 'x', 'query': 'x' * 513}):
            with pytest.raises(ValueError):
                active.run('reference-citation', '1.0.0', inputs, attempt='invalid-input')
    assert c.admissions == []


def test_registry_is_versioned_detached_bounded_and_dependency_checked(configured):
    c = configured
    one = c.registry.list(limit=1)
    two = c.registry.list(offset=one['next_offset'], limit=1)
    assert one['items'][0]['skill_id'] != two['items'][0]['skill_id']
    assert two['next_offset'] is None
    row = c.registry.describe('reference-citation', '1.0.0')
    assert row['skill_sha256'] == digest(row['definition'])
    row['definition']['steps'][0]['tool'] = 'unregistered'
    assert c.registry.describe('reference-citation', '1.0.0')['definition']['steps'][0]['tool'] == 'ncp_knowledge_resolve'
    with pytest.raises(ValueError):
        c.registry.describe('reference-citation', '2.0.0')
    with pytest.raises(ValueError):
        c.registry.list(limit=17)
    with pytest.raises(ValueError):
        c.registry.describe('reference-citation', '1.0.0', max_bytes=128)
    clean = c.registry.describe('reference-citation', '1.0.0')['definition']
    for step in clean['steps']:
        step.pop('schema_sha256')
    variants = []
    wrong_version = deepcopy(clean); wrong_version['version'] = 'latest'; variants.append(wrong_version)
    unknown_tool = deepcopy(clean); unknown_tool['steps'][0]['tool'] = 'arbitrary_shell'; variants.append(unknown_tool)
    forward = deepcopy(clean); forward['steps'][0]['arguments'] = {
        'source_id': {'step': 'passage', 'path': ['span']}}; variants.append(forward)
    too_many = deepcopy(clean); too_many['steps'] = too_many['steps'] * 3; variants.append(too_many)
    for definition in variants:
        with pytest.raises(ValueError):
            SkillRegistry([definition])
    with pytest.raises(ValueError):
        SkillRegistry([clean, clean])


def test_admission_is_reviewable_detached_and_requires_exact_true(configured):
    c = configured
    intents = []
    def admit(intent):
        intents.append(deepcopy(intent))
        assert digest(intent['arguments']) == intent['arguments_sha256']
        # Callback edits must not become request arguments.
        intent['arguments'].clear()
        return True
    active = c.runner(admit_step=admit)
    assert citation(active)['status'] == 'completed'
    assert intents[0]['arguments'] == {'source_id': 'gdrive:Original'}
    strict = c.runner(admit_step=lambda _: 1, replay_identity='strict-replay')
    with patch.object(c.facade, 'call', side_effect=AssertionError('truthy admission dispatched')):
        result = citation(strict, attempt='strict')
    assert result['status'] == 'refused' and result['steps_completed'] == 0


def test_receipt_self_hash_rejects_tampered_returned_data(configured):
    c = configured
    active = c.runner()
    artifact = citation(active)['artifact_id']
    original = active._store.native(artifact)
    corrupted = deepcopy(original)
    corrupted['status'] = 'manufactured-success'
    with patch.object(active._store, 'native', return_value=corrupted):
        with pytest.raises(ValueError, match='digest'):
            active.receipt(artifact)
    assert active.receipt(artifact)['receipt'] == original


def test_disabled_principal_cannot_cancel_or_read_receipts(configured):
    c = configured
    active = c.runner()
    artifact = citation(active)['artifact_id']
    c.grant['enabled'] = False
    with patch.object(active._store, 'native', side_effect=AssertionError('revoked receipt read')):
        with pytest.raises(ValueError):
            active.receipt(artifact)
    with pytest.raises(ValueError):
        active.cancel('citation')


def test_tenant_binding_changed_during_admission_stops_before_next_service(configured):
    c = configured
    def admit(intent):
        if intent['step_id'] == 'search':
            c.logistics._bound_tenant = 'different-tenant'
        return True
    active = c.runner(admit_step=admit)
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        result = citation(active)
    assert result['status'] == 'refused' and result['steps_completed'] == 1
    assert [r.args[0] for r in calls.call_args_list] == ['ncp_knowledge_resolve']
    with pytest.raises(ValueError):
        active.receipt(result['artifact_id'])


@pytest.mark.parametrize('retained', [False, True])
def test_uncertain_outcome_halts_without_retry_and_preserves_upstream_locator(configured, retained):
    from ciw.principal_policy import PrincipalOutcomeUnknown
    from ciw.retention import RetainedFailure
    c = configured
    active = c.runner()
    upstream = 'failure-' + '1' * 32
    failure = RetainedFailure('outcome_unknown', upstream) if retained else PrincipalOutcomeUnknown()
    with patch('ciw.services.invoke', side_effect=failure) as calls:
        result = citation(active)
    assert calls.call_count == 1 and len(c.admissions) == 1
    assert result['status'] == 'outcome_unknown' and result['steps_completed'] == 0
    receipt = active.receipt(result['artifact_id'])['receipt']
    assert receipt['status'] == 'outcome_unknown'
    if retained:
        assert receipt['upstream_failure_artifact'] == upstream
    with pytest.raises(ValueError, match='attempt'):
        citation(active)


def test_recreated_runner_refuses_old_attempt_but_reopens_receipt(configured):
    c = configured
    original = c.runner()
    result = citation(original)
    expected = original.receipt(result['artifact_id'])['receipt']
    recreated = c.runner()
    with patch.object(c.facade, 'call', side_effect=AssertionError('recreated runner replayed attempt')):
        with pytest.raises(ValueError, match='attempt'):
            citation(recreated)
    assert recreated.receipt(result['artifact_id'])['receipt'] == expected
    fresh_replay = c.runner(replay_identity='intentional-replay')
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        repeated = citation(fresh_replay)
    assert repeated['status'] == 'completed' and calls.call_count == 3
    assert repeated['artifact_id'] != result['artifact_id']


def test_simultaneous_recreated_runners_dispatch_same_attempt_at_most_once(configured):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    c = configured
    runners = [c.runner(), c.runner()]
    barrier = Barrier(2)
    def launch(active):
        barrier.wait(timeout=5)
        try:
            return citation(active)
        except ValueError as exc:
            return {'status': 'rejected', 'reason': str(exc)}
    with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(launch, runners))
    assert sorted(r['status'] for r in results) == ['completed', 'rejected']
    assert 'attempt' in next(r['reason'] for r in results if r['status'] == 'rejected')
    assert calls.call_count == 3


@pytest.mark.parametrize('dangling', [False, True])
def test_attempt_marker_symlink_refuses_without_dispatch_or_overwrite(configured, tmp_path, dangling):
    from ciw.retention import _owner_dir
    c = configured
    active = c.runner()
    target = tmp_path / 'unrelated-target.json'
    if not dangling:
        target.write_text('unrelated protected bytes')
    marker = _owner_dir(active._store.owner) / ('toolbox-attempt-' + digest('citation') + '.json')
    marker.symlink_to(target)
    with patch.object(c.facade, 'call', side_effect=AssertionError('symlink marker dispatched')):
        with pytest.raises((ValueError, FileExistsError)):
            citation(active)
    assert marker.is_symlink()
    if dangling:
        assert not target.exists()
    else:
        assert target.read_text() == 'unrelated protected bytes'
    assert c.admissions == []


def test_attempt_capacity_applies_across_recreated_runners(configured):
    c = configured
    with patch.object(c.facade, 'call', side_effect=AssertionError('unadmitted call dispatched')):
        for index in range(4):
            active = c.runner(admit_step=None)
            assert citation(active, attempt='denied-' + str(index))['status'] == 'refused'
        with pytest.raises(ValueError, match='capacity'):
            citation(c.runner(admit_step=None), attempt='fifth')


def test_cancel_acknowledged_during_intent_retention_must_prevent_dispatch(configured):
    c = configured
    active = c.runner()
    original = active._keep
    observed = []
    def retain(record):
        artifact = original(record)
        if record['status'] == 'step_pending':
            observed.append(active.cancel(record['attempt']))
        return artifact
    with patch.object(active, '_keep', side_effect=retain):
        with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
            result = citation(active)
    assert observed and observed[0]['cancel_requested'] is True
    assert calls.call_count == 0, (result, calls.call_count, 'cancellation acknowledged before service dispatch')
    assert result['status'] == 'cancelled' and result['steps_completed'] == 0


def test_io_failure_after_service_result_is_unknown_and_attempt_never_replayed(configured):
    c = configured
    active = c.runner()
    original = active._keep
    def retain(record):
        if record['status'] == 'step_completed':
            raise OSError('simulated disk failure after completed canonical service')
        return original(record)
    with patch.object(active, '_keep', side_effect=retain):
        with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
            result = citation(active)
    assert calls.call_count == 1
    assert result['status'] == 'outcome_unknown' and result['steps_completed'] == 0
    assert active.receipt(result['artifact_id'])['receipt']['status'] == 'outcome_unknown'
    recreated = c.runner()
    with patch.object(c.facade, 'call', side_effect=AssertionError('replayed unknown outcome')):
        with pytest.raises(ValueError, match='attempt'):
            citation(recreated)


def test_final_receipt_io_failure_does_not_reclassify_as_completed(configured):
    c = configured
    active = c.runner()
    original = active._keep
    def retain(record):
        if record['status'] == 'completed':
            raise OSError('simulated terminal retention failure')
        return original(record)
    with patch.object(active, '_keep', side_effect=retain):
        with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
            result = citation(active)
    assert calls.call_count == 3
    assert result['status'] == 'outcome_unknown'
    retained = active.receipt(result['artifact_id'])['receipt']
    assert retained['status'] == 'step_pending'
    assert retained['admission']['step_id'] == 'passage'


def test_deadline_expired_during_intent_retention_must_prevent_dispatch(configured):
    c = configured
    clock = [0.0]
    active = c.runner(maximum_seconds=1)
    original = active._keep
    def retain(record):
        artifact = original(record)
        if record['status'] == 'step_pending':
            clock[0] = 2.0
        return artifact
    with patch('ciw.toolbox_skills.monotonic', side_effect=lambda: clock[0]):
        with patch.object(active, '_keep', side_effect=retain):
            with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
                result = citation(active)
    assert calls.call_count == 0, (result, calls.call_count, 'deadline expired before service dispatch')
    assert result['status'] == 'timed_out' and result['steps_completed'] == 0


def test_scope_changed_during_intent_retention_must_prevent_dispatch(configured):
    c = configured
    active = c.runner()
    original = active._keep
    def retain(record):
        artifact = original(record)
        if record['status'] == 'step_pending':
            c.value['revision'] = 'new-policy-during-retention'
        return artifact
    with patch.object(active, '_keep', side_effect=retain):
        with patch.object(c.facade, 'call', wraps=c.facade.call) as calls:
            result = citation(active)
    assert calls.call_count == 0, (result, calls.call_count, 'scope changed before service dispatch')
    assert result['status'] == 'refused' and result['steps_completed'] == 0
