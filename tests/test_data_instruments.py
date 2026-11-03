"""Durable Domain H contracts and actual principal-routed TBRT integration.

Fixtures are synthetic source/rights reviews, never live rights or NSL claims.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from ciw.campaign_contracts import ContractRefusal, canonical
from ciw.data_instruments import (DataInstrumentLedger, InstrumentOutcomeUnknown, InstrumentRefusal, KINDS,
                                  content_ref, validate_table, version_ref)
from ciw.persistent_cache import PersistenceRefusal
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal


FRAME = {'clock_id': 'fixture:reference', 'time_scale': 'fixture-monotonic', 'unit': 's'}
DEVICE = {'clock_id': 'fixture:device', 'time_scale': 'device-monotonic', 'unit': 's'}
BASELINE = b'[{"id":"control-place","value":10,"label":"synthetic control","enabled":true}]'
GOOD = b'[{"id":"control-place","value":10.5,"label":"synthetic control","enabled":true}, {"id":"place-2","value":17,"label":"synthetic place","enabled":false}]'
BAD = b'[{"id":"control-place","value":11,"label":"synthetic control","enabled":true}]'


def evidence(label):
    return content_ref(label.encode())


def policy_document():
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'fixture-1', 'principals': [{
        'principal': 'fixture-owner', 'enabled': True,
        'operations': [{'name': n, 'constraints': {}} for n in
                       ('ncp_clocksync_reconcile', 'ncp_clocksync_replay')],
        'scientific_operations': ['tbrt.affine-clock-reconcile.v1'],
        'resources': [], 'reference_scopes': [], 'handoff_recipients': []}]}


def source_document():
    usage = {'status': 'permitted', 'document_ref': evidence('synthetic usage document'),
             'review_ref': evidence('synthetic host review')}
    return {'source_id': 'places-fixture', 'version': 1, 'kind': 'places',
            'origin': 'https://example.invalid/places.json',
            'license': {**usage, 'expression': 'CC0-1.0'}, 'terms': deepcopy(usage), 'robots': deepcopy(usage),
            'scope_review_ref': evidence('synthetic places-only content review'),
            'identification': {'product': 'ODSP', 'version': '1.0', 'contact': 'https://example.invalid/contact'},
            'minimum_interval_seconds': 10,
            'table': {'key': 'id', 'columns': {'id': 'string', 'value': 'number', 'label': 'string', 'enabled': 'boolean'}, 'nullable': []},
            'calibration': {'known_good_ref': content_ref(BASELINE), 'evidence_ref': evidence('control-sample-review'),
                            'absolute_tolerances': {'value': .5}},
            'freshness': {'frame': FRAME, 'max_age_seconds': 100, 'future_tolerance_seconds': 0}}


@pytest.fixture
def configured(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    state = {'now': 1000, 'policy': policy_document(), 'allowed': True, 'review': None}
    policy = PrincipalPolicy(lambda: state['policy'])
    def admit(action, source, context):
        return state['allowed']
    def review(intent, context):
        return canonical(intent) == state['review']
    def make(**kwargs):
        return DataInstrumentLedger(root / 'instrument.sqlite', tenant='fixture-tenant', principal='fixture-owner',
                                    policy=policy, admit=admit, clock=lambda: {'value': state['now'], 'frame': FRAME},
                                    verify_impact_review=review, **kwargs)
    ledger = make()
    document = source_document()
    ref = ledger.register_source(document, BASELINE)
    return ledger, ref, document, state, make


def capture(ledger, ref, raw=GOOD, attempt='capture-1', observed=None):
    return ledger.capture('places-fixture', ref, raw, attempt=attempt,
                          observed=observed or {'value': 990, 'frame': FRAME})


def reviewed_release(ledger, state, hold, recovery):
    review = {'approval_ref': evidence('human approval fixture'), 'reviewer': 'fixture-reviewer',
              'decision': 'release_local_drift_hold', 'impact': [{
                  'artifact_ref': evidence('affected derived artifact'), 'disposition': 'invalidate',
                  'evidence_ref': evidence('explicit impact assessment')}]}
    state['review'] = canonical({'source_id': 'places-fixture', 'hold_ref': hold, 'capture_ref': recovery, 'review': review})
    return ledger.review_hold('places-fixture', expected_hold_ref=hold, capture_ref=recovery, review=review)


def test_exact_dataset_versions_raw_bytes_idempotency_and_restart(configured):
    ledger, ref, _, _, reopen = configured
    first = capture(ledger, ref)
    assert capture(ledger, ref) == first
    assert ledger.snapshot('places-fixture', first) == GOOD
    receipt = ledger.receipt('places-fixture', first)
    data = receipt['body']
    assert data['dataset']['content_ref'] == content_ref(GOOD)
    assert data['dataset_ref'] == version_ref(data['dataset'])
    assert data['checks'] == {'schema': 'within_contract', 'calibration': 'within_tolerance', 'freshness': 'within_nominal_window'}
    assert data['nsl_admission'] == 'not_verified'
    assert receipt['authorizes_execution'] is receipt['authorizes_state_admission'] is False
    equivalent = json.dumps(json.loads(GOOD), indent=2).encode()
    second = capture(ledger, ref, equivalent, 'capture-2')
    assert ledger.receipt('places-fixture', second)['body']['dataset_ref'] != data['dataset_ref']
    occurrence = capture(ledger, ref, attempt='capture-3')
    assert occurrence != first
    assert ledger.receipt('places-fixture', occurrence)['body']['dataset_ref'] == data['dataset_ref']
    with pytest.raises(InstrumentRefusal, match='attempt_conflict'):
        capture(ledger, ref, BAD)
    assert reopen().receipt('places-fixture', first) == receipt
    assert reopen().verify_chain()['records'] == 4


@pytest.mark.parametrize('kind', sorted(KINDS))
def test_only_permitted_source_kinds_register(configured, kind):
    ledger, _, doc, _, _ = configured
    doc.update(source_id='fixture-' + kind, kind=kind)
    assert ledger.register_source(doc, BASELINE).startswith('sha256:')


@pytest.mark.parametrize('change', [
    {'kind': 'person'}, {'kind': 'people'}, {'kind': 'resolvePerson'}, {'stealthFetch': True},
    {'origin': 'http://example.invalid/data'}, {'origin': 'https://secret@example.invalid/data'},
    {'origin': 'https://example.invalid/data?token=secret'}, {'origin': 'https://example.invalid/data#secret'},
    {'identification': {'product': 'Mozilla', 'version': '1.0', 'contact': 'https://example.invalid/contact'}},
    {'headers': {'User-Agent': 'browser'}},
])
def test_person_stealth_spoofing_and_undeclared_fields_refused(configured, change):
    ledger, _, doc, _, _ = configured
    doc.update(source_id='refused-fixture', **change)
    before = ledger.verify_chain()
    with pytest.raises((InstrumentRefusal, ContractRefusal)):
        ledger.register_source(doc, BASELINE)
    assert ledger.verify_chain() == before


@pytest.mark.parametrize('field', ['license', 'terms', 'robots'])
@pytest.mark.parametrize('status', ['held', 'unknown'])
def test_usage_holds_allow_registration_but_no_intake_or_fetch_reservation(configured, field, status):
    ledger, _, doc, _, _ = configured
    doc.update(source_id='held-fixture')
    doc[field]['status'] = status
    ref = ledger.register_source(doc, BASELINE)
    with pytest.raises(InstrumentRefusal, match='source_usage_held'):
        ledger.reserve_collection('held-fixture', ref)
    with pytest.raises(InstrumentRefusal, match='source_usage_held'):
        ledger.capture('held-fixture', ref, GOOD, attempt='held', observed={'value': 990, 'frame': FRAME})


@pytest.mark.parametrize('raw', [b'{', b'[]', b'NaN', b'[{"id":"x","id":"y"}]',
    b'[{"id":"control-place","value":true,"label":"synthetic control","enabled":true}]',
    b'[{"id":"control-place","value":10,"label":"synthetic control","enabled":true,"extra":1}]',
    b'[{"id":"control-place","value":"10","label":"synthetic control","enabled":true}]',
    b'[{"id":"control-place","value":10,"label":"synthetic control","enabled":1}]',
    b'[{"id":"control-place","value":10,"label":"synthetic control","enabled":true},'
    b'{"id":"control-place","value":10,"label":"synthetic control","enabled":true}]', BAD])
def test_drifted_snapshots_remain_exact_and_create_sticky_hold(configured, raw):
    ledger, ref, _, _, reopen = configured
    bad = capture(ledger, ref, raw, 'bad')
    before = ledger.receipt('places-fixture', bad)
    assert ledger.snapshot('places-fixture', bad) == raw
    assessment = ledger.assess('places-fixture', bad)
    assert 'out_of_tolerance' in assessment['checks'].values()
    held = assessment['hold_ref']
    good = capture(reopen(), ref, attempt='recovery')
    result = reopen().assess('places-fixture', good)
    assert result['checks'] == {'schema': 'within_contract', 'calibration': 'within_tolerance', 'freshness': 'within_nominal_window'}
    assert result['hold_ref'] == held
    assert result['trusted_for_downstream'] is False
    assert ledger.receipt('places-fixture', bad) == before


def test_sample_tolerance_exact_boundary_and_missing_sample(configured):
    ledger, ref, _, _, _ = configured
    inside = capture(ledger, ref)
    assert ledger.assess('places-fixture', inside)['checks']['calibration'] == 'within_tolerance'
    missing = capture(ledger, ref, b'[{"id":"place-2","value":10,"label":"synthetic control","enabled":true}]', 'missing')
    assert ledger.assess('places-fixture', missing)['checks']['calibration'] == 'out_of_tolerance'


def test_atomic_capture_inspection_is_detached_current_and_does_not_append(configured):
    ledger, ref, _, state, _ = configured
    record = capture(ledger, ref)
    before = ledger.verify_chain()
    inspected = ledger.inspect_capture('places-fixture', record)
    assert inspected['raw'] == GOOD
    assert inspected['receipt'] == ledger.receipt('places-fixture', record)
    assert inspected['assessment']['capture_ref'] == record
    assert inspected['assessment']['trusted_for_downstream'] is False
    inspected['receipt']['body'].clear()
    state['now'] = 1100
    again = ledger.inspect_capture('places-fixture', record)
    assert again['receipt']['body']['dataset']['content_ref'] == content_ref(GOOD)
    assert again['assessment']['checks']['freshness'] == 'out_of_tolerance'
    assert ledger.verify_chain() == before
    held = ledger.assess('places-fixture', record)
    assert held['hold_ref'] is not None


def test_capture_inspection_refuses_superseded_source_and_revoked_principal(configured):
    ledger, ref, document, state, _ = configured
    record = capture(ledger, ref)
    state['allowed'] = False
    with pytest.raises(InstrumentRefusal, match='host_admission_required'):
        ledger.inspect_capture('places-fixture', record)
    state['allowed'] = True
    document['version'] = 2
    ledger.register_source(document, BASELINE)
    with pytest.raises(InstrumentRefusal, match='source_version_superseded'):
        ledger.inspect_capture('places-fixture', record)


def test_nullable_typed_key_and_noncoercion():
    table = {'key': 'id', 'columns': {'id': 'integer', 'value': 'number', 'active': 'boolean'}, 'nullable': ['value']}
    assert validate_table(b'[{"id":1,"value":null,"active":false}]', table)
    for bad in (b'[{"id":true,"value":null,"active":false}]',
                b'[{"id":1.0,"value":null,"active":false}]',
                b'[{"id":1,"value":null,"active":0}]'):
        with pytest.raises(InstrumentRefusal):
            validate_table(bad, table)


def test_source_versions_are_immutable_and_contract_change_cannot_clear_hold(configured):
    ledger, ref, doc, _, _ = configured
    old = capture(ledger, ref)
    doc['version'] = 2
    doc['calibration']['absolute_tolerances']['value'] = .75
    changed = ledger.register_source(doc, BASELINE)
    with pytest.raises(InstrumentRefusal, match='superseded'):
        capture(ledger, ref, attempt='old-version')
    with pytest.raises(InstrumentRefusal, match='source_version_sequence'):
        doc['version'] = 4
        ledger.register_source(doc, BASELINE)
    current = capture(ledger, changed, attempt='new-version')
    result = ledger.assess('places-fixture', current)
    assert ledger.receipt('places-fixture', result['hold_ref'])['body']['reasons'] == ['source_contract_changed']
    assert ledger.snapshot('places-fixture', old) == GOOD


def test_review_requires_exact_host_approval_and_cannot_grant_downstream_trust(configured):
    ledger, ref, _, state, reopen = configured
    bad = capture(ledger, ref, BAD, 'bad')
    hold = ledger.assess('places-fixture', bad)['hold_ref']
    good = capture(ledger, ref, attempt='good')
    review = {'approval_ref': evidence('invented approval'), 'reviewer': 'fixture-reviewer',
              'decision': 'release_local_drift_hold', 'impact': []}
    with pytest.raises(InstrumentRefusal, match='verified_host_impact_review_required'):
        ledger.review_hold('places-fixture', expected_hold_ref=hold, capture_ref=good, review=review)
    event = reviewed_release(ledger, state, hold, good)
    assert ledger.receipt('places-fixture', event)['body']['recovery']['hold_ref'] == hold
    result = reopen().assess('places-fixture', good)
    assert result['hold_ref'] is None
    assert result['trusted_for_downstream'] is False and result['nsl_admission'] == 'not_verified'
    assert result['source_authenticated'] is False
    with pytest.raises(InstrumentRefusal, match='active_hold_changed'):
        reviewed_release(ledger, state, hold, good)


def test_stale_review_does_not_clear_newer_hold_or_stale_recovery(configured):
    ledger, ref, _, state, _ = configured
    bad = capture(ledger, ref, BAD, 'bad')
    old = ledger.assess('places-fixture', bad)['hold_ref']
    new = ledger.assess('places-fixture', bad)['hold_ref']
    good = capture(ledger, ref, attempt='good')
    with pytest.raises(InstrumentRefusal, match='active_hold_changed'):
        reviewed_release(ledger, state, old, good)
    state['now'] = 1200
    with pytest.raises(InstrumentRefusal, match='recovery_capture_out_of_tolerance'):
        reviewed_release(ledger, state, new, good)


def test_review_wait_cannot_release_a_capture_that_became_stale(configured):
    ledger, ref, _, state, _ = configured
    bad = capture(ledger, ref, BAD, 'bad')
    held = ledger.assess('places-fixture', bad)['hold_ref']
    good = capture(ledger, ref, attempt='good')
    original = ledger._verify_review
    calls = []
    def delayed(intent, context):
        calls.append(intent)
        if len(calls) == 2:
            state['now'] = 1200
        return original(intent, context)
    ledger._verify_review = delayed
    with pytest.raises(InstrumentRefusal, match='recovery_capture_out_of_tolerance'):
        reviewed_release(ledger, state, held, good)
    state['now'] = 1000
    assert ledger.assess('places-fixture', good)['hold_ref'] == held


def test_current_freshness_frames_future_and_old_data_hold(configured):
    ledger, ref, _, state, _ = configured
    exact = capture(ledger, ref, observed={'value': 900, 'frame': FRAME})
    assert ledger.assess('places-fixture', exact)['checks']['freshness'] == 'within_nominal_window'
    state['now'] = 1001
    assert ledger.assess('places-fixture', exact)['checks']['freshness'] == 'out_of_tolerance'
    future = capture(ledger, ref, attempt='future', observed={'value': 1002, 'frame': FRAME})
    assert ledger.assess('places-fixture', future)['checks']['freshness'] == 'out_of_tolerance'
    unmapped = capture(ledger, ref, attempt='unmapped', observed={'value': 103, 'frame': DEVICE})
    result = ledger.assess('places-fixture', unmapped)
    assert result['checks']['freshness'] == 'mapping_required'
    assert result['timestamp_status'] == 'host_observation_only'
    assert result['nominal_age_seconds'] is None
    ledger._clock = lambda: {'value': 1001, 'frame': DEVICE}
    with pytest.raises(InstrumentRefusal, match='reference_clock_mismatch'):
        ledger.assess('places-fixture', exact)


def test_rate_reservations_are_atomic_persistent_and_never_network_grants(configured, monkeypatch):
    ledger, ref, _, state, reopen = configured
    import socket
    def forbidden(*a, **k):
        pytest.fail('collection reservation attempted network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    first = ledger.reserve_collection('places-fixture', ref)
    body = ledger.receipt('places-fixture', first)['body']
    assert body['network_request_performed'] is body['authorizes_network'] is False
    with pytest.raises(InstrumentRefusal, match='rate_limited'):
        reopen().reserve_collection('places-fixture', ref)
    state['now'] = 999
    with pytest.raises(InstrumentRefusal, match='clock_regressed'):
        ledger.reserve_collection('places-fixture', ref)
    state['now'] = 1010
    def run(_):
        try:
            return reopen().reserve_collection('places-fixture', ref)
        except InstrumentRefusal:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(x is not None for x in results) == 1


def _process_capture(path, ref, output):
    policy = PrincipalPolicy(policy_document)
    ledger = DataInstrumentLedger(path, tenant='fixture-tenant', principal='fixture-owner', policy=policy,
                                  admit=lambda *a: True, clock=lambda: {'value': 1000, 'frame': FRAME})
    output.put(capture(ledger, ref))


def test_independent_processes_commit_identical_attempt_only_once(configured):
    ledger, ref, _, _, _ = configured
    context = multiprocessing.get_context('spawn')
    output = context.Queue()
    processes = [context.Process(target=_process_capture, args=(ledger.db.path, ref, output)) for _ in range(2)]
    for process in processes:
        process.start()
    try:
        results = [output.get(timeout=20) for _ in processes]
        assert len(set(results)) == 1
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill(); process.join()
        output.close()
    assert ledger.verify_chain()['records'] == 2


def test_revocation_before_dispatch_and_during_io_rolls_back_and_withholds(configured, monkeypatch):
    ledger, ref, _, state, _ = configured
    first = capture(ledger, ref)
    state['policy']['principals'][0]['enabled'] = False
    with pytest.raises(PrincipalRefusal):
        ledger.snapshot('places-fixture', first)
    state['policy']['principals'][0]['enabled'] = True
    original = ledger._event
    def revoke(db, *args):
        event = original(db, *args)
        state['policy']['revision'] = 'changed-during-transaction'
        return event
    before = ledger.verify_chain()
    monkeypatch.setattr(ledger, '_event', revoke)
    with pytest.raises(InstrumentRefusal, match='principal_context_changed'):
        capture(ledger, ref, attempt='revoked')
    assert ledger.verify_chain() == before
    state['allowed'] = False
    with pytest.raises(InstrumentRefusal, match='host_admission_required'):
        ledger.receipt('places-fixture', first)


def test_revocation_after_commit_is_disclosed_as_committed_without_retry(configured, monkeypatch):
    ledger, ref, _, state, _ = configured
    original = ledger._current
    calls = []
    def revoke(identity, action, source_id):
        calls.append(action)
        if len(calls) == 3:
            state['policy']['principals'][0]['enabled'] = False
        return original(identity, action, source_id)
    monkeypatch.setattr(ledger, '_current', revoke)
    with pytest.raises(InstrumentOutcomeUnknown, match='local_commit_completed_disclosure_withheld'):
        capture(ledger, ref)
    with sqlite3.connect(ledger.db.path) as db:
        rows = db.execute("SELECT ref FROM instrument_events WHERE kind='capture'").fetchall()
    assert len(rows) == 1
    state['policy']['principals'][0]['enabled'] = True
    monkeypatch.setattr(ledger, '_current', original)
    assert capture(ledger, ref) == rows[0][0]
    assert ledger.verify_chain()['records'] == 2


def test_namespace_limits_links_and_capacity_refuse_without_losing_evidence(configured, tmp_path):
    ledger, ref, _, _, _ = configured
    first = capture(ledger, ref)
    kwargs = dict(tenant='other-tenant', principal='fixture-owner', policy=ledger.policy,
                  admit=lambda *a: True, clock=lambda: {'value': 1000, 'frame': FRAME})
    with pytest.raises(InstrumentRefusal, match='store_binding_changed'):
        DataInstrumentLedger(ledger.db.path, **kwargs)
    kwargs['tenant'] = 'fixture-tenant'
    with pytest.raises(InstrumentRefusal, match='store_binding_changed'):
        DataInstrumentLedger(ledger.db.path, max_records=16, **kwargs)
    linked = ledger.db.path.parent / 'linked.sqlite'
    linked.symlink_to(ledger.db.path)
    with pytest.raises(PersistenceRefusal):
        DataInstrumentLedger(linked, **kwargs)
    bounded = ledger.db.path.parent / 'bounded.sqlite'
    small = DataInstrumentLedger(bounded, max_records=16, **kwargs)
    selected = small.register_source(source_document(), BASELINE)
    for index in range(15):
        capture(small, selected, attempt='capture-' + str(index))
    with pytest.raises(InstrumentRefusal, match='store_budget_reached'):
        capture(small, selected, attempt='overflow')
    assert small.verify_chain()['records'] == 16
    assert ledger.snapshot('places-fixture', first) == GOOD


def test_corrupted_blob_and_receipt_fail_closed(configured):
    ledger, ref, _, _, _ = configured
    receipt = capture(ledger, ref)
    with sqlite3.connect(ledger.db.path) as db:
        db.execute('UPDATE instrument_blobs SET value=? WHERE ref=?', (BAD, content_ref(GOOD)))
    with pytest.raises(InstrumentRefusal, match='snapshot_missing_or_corrupt'):
        ledger.snapshot('places-fixture', receipt)
    with sqlite3.connect(ledger.db.path) as db:
        db.execute('UPDATE instrument_events SET value=? WHERE ref=?', (b'{}', receipt))
    with pytest.raises(InstrumentRefusal, match='receipt_missing_or_wrong_scope'):
        ledger.receipt('places-fixture', receipt)
    with pytest.raises(InstrumentRefusal, match='receipt_chain_corrupt'):
        ledger.verify_chain()


@pytest.fixture
def real_clocksync(configured):
    from ciw.clocksync_profile import ClockSyncHost, PIN
    checkout = os.environ.get('CIW_CLOCKSYNC_CHECKOUT')
    if not checkout:
        pytest.skip('actual pinned TBRT checkout required; no synthetic verification fallback')
    assert subprocess.check_output(['git', '-C', checkout, 'rev-parse', 'HEAD'], text=True).strip() == PIN
    ledger = configured[0]
    with ClockSyncHost(checkout=Path(checkout).resolve(), python_executable=sys.executable,
                       principal='fixture-owner') as host:
        yield ledger.policy.facade('fixture-owner', clocksync=host)


def clock_request(raw=GOOD):
    return {'execution_id': 'fixture:data-clock-1',
            'observation': {'device_time': 103, 'frame': DEVICE, 'evidence_id': content_ref(raw),
                            'received_at': None, 'known_at': None},
            'model': {'model_id': 'fixture:clock-map', 'source_frame': DEVICE, 'reference_frame': FRAME,
                      'device_origin': 100, 'reference_origin': 990, 'skew': 2, 'offset': .5,
                      'valid_device_interval': [100, 110], 'synchronization_evidence_ids': ['fixture:asserted-sync']},
            'expected_reference': FRAME,
            'joint_covariance': {'order': ['device_time', 'skew', 'offset'],
                                 'matrix': [[1, .25, .5], [.25, 1, .125], [.5, .125, 4]],
                                 'cross_covariance_policy': 'declared', 'evidence_ids': ['fixture:asserted-covariance']}}


def test_actual_pinned_tbrt_link_replay_exact_dataset_and_truthful_trust_gates(configured, real_clocksync):
    from ciw.services import invoke
    ledger, ref, _, _, reopen = configured
    captured = capture(ledger, ref, observed={'value': 103, 'frame': DEVICE})
    assert ledger.assess('places-fixture', captured)['checks']['freshness'] == 'mapping_required'
    receipt = invoke('ncp_clocksync_reconcile', {'request': clock_request()}, host=real_clocksync)
    assert receipt['numerical_result']['event_time_delta'] == 6.5
    assert receipt['numerical_result']['variance'] == 22.75
    link = ledger.link_clocksync('places-fixture', captured, receipt, host=real_clocksync)
    stored = ledger.receipt('places-fixture', link)['body']
    assert stored['replay']['outcome'] == 'verified'
    assert stored['receipt'] == receipt
    assert stored['receipt']['authority']['source_authenticated'] is False
    result = reopen().assess('places-fixture', captured)
    assert result['nominal_age_seconds'] == 3.5
    assert result['clock_link_ref'] == link
    assert result['time_uncertainty']['variance'] == 22.75
    assert result['source_authenticated'] is result['trusted_for_downstream'] is False
    assert result['nsl_admission'] == 'not_verified'
    with pytest.raises(InstrumentRefusal, match='already_recorded'):
        ledger.link_clocksync('places-fixture', captured, receipt, host=real_clocksync)


def test_actual_tbrt_wrong_dataset_tampered_receipt_or_revoked_grant_never_link(configured, real_clocksync):
    from ciw.services import invoke
    ledger, ref, _, state, _ = configured
    captured = capture(ledger, ref, observed={'value': 103, 'frame': DEVICE})
    request = clock_request()
    receipt = invoke('ncp_clocksync_reconcile', {'request': request}, host=real_clocksync)
    wrong = deepcopy(receipt)
    wrong['request']['observation']['evidence_id'] = content_ref(BAD)
    with pytest.raises(InstrumentRefusal, match='timestamp_dataset_binding_mismatch'):
        ledger.link_clocksync('places-fixture', captured, wrong, host=real_clocksync)
    altered = deepcopy(receipt)
    altered['numerical_result']['event_time_delta'] = 1
    with pytest.raises(InstrumentRefusal, match='replay_not_verified'):
        ledger.link_clocksync('places-fixture', captured, altered, host=real_clocksync)
    state['policy']['principals'][0]['scientific_operations'] = []
    with pytest.raises(PrincipalRefusal):
        ledger.link_clocksync('places-fixture', captured, receipt, host=real_clocksync)
    assert ledger.assess('places-fixture', captured)['clock_link_ref'] is None
    with pytest.raises(InstrumentRefusal, match='principal_clocksync_binding_required'):
        ledger.link_clocksync('places-fixture', captured, receipt, host=real_clocksync.hosts['clocksync'])
