"""Actual local cold cutover of synthetic PG, survey, journal and projection.

No hosted connections, equipment, actuation or automatic work replay.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import asyncio
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from atelier_mcp.observation_gateway import ObservationJournal, RecordedSensor
from atelier_mcp.observation_gateway.contracts import OutcomeUnknown, Refusal, encode
from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import DataInstrumentLedger
from ciw.distributed_nats import JetStreamBridge
from ciw.distributed_queue import PostgresQueue, QueueRefusal, install_schema
from ciw.internal_operator import InternalOperatorInspection, save_report
from ciw.offline_survey import OfflineSurveyFlow
from ciw.principal_policy import PrincipalPolicy
from ciw.sensor_survey_workbench import inspect_workbench

REPO = Path(__file__).resolve().parents[1]
OPERATION = 'fixture.held-projection.v1'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helpers = load('odsp_cross_store_fixture', REPO / 'tools/distributed-coordinator/cross_store_fixture.py')
local = load('odsp_cross_store_runtime', REPO / 'tools/distributed-coordinator/local_runtime.py')
example = load('odsp_cross_store_example', REPO / 'examples/sensor-survey-workbench/run.py')


def queue(runtime, authorize=lambda *args: True):
    return PostgresQueue(runtime.direct_dsn, owner='synthetic-cross-store', principal='survey-operator',
        generation='cross-store-v1', operations=[OPERATION], authorize=authorize, lease_seconds=.5)


async def publish(runtime, active, stream, *, fresh=False):
    import nats
    from nats.js.errors import NotFoundError
    client = await nats.connect(runtime.nats_url, token=runtime.token, allow_reconnect=False)
    try:
        js = client.jetstream()
        if fresh:
            with pytest.raises(NotFoundError):
                await js.stream_info(stream)
        bridge = JetStreamBridge(active, js, stream=stream, durable='worker', ack_wait=.3)
        await bridge.provision()
        count = await bridge.publish_pending()
        info = await js.stream_info(stream)
        assert info.state.messages == count
        return count
    finally:
        await client.close()


@pytest.fixture(scope='module')
def drill():
    import psycopg
    bins, nats = (os.environ.get(k) for k in ('ODSP_DISTRIBUTED_PG_BIN', 'ODSP_DISTRIBUTED_NATS_SERVER'))
    assert bins and nats, 'pinned owned local services required; never skip this qualification'
    pins = json.loads(os.environ['ODSP_CROSS_STORE_BINARY_PINS'])
    for name in ('initdb', 'postgres', 'pg_ctl', 'pg_dump', 'pg_restore'):
        assert sha256((Path(bins) / name).read_bytes()).hexdigest() == pins[name]
    assert sha256(Path(nats).read_bytes()).hexdigest() == pins['nats-server']
    root = Path(os.environ['ODSP_CROSS_STORE_SCRATCH']) / ('r-' + uuid4().hex[:8])
    root.mkdir(mode=0o700)
    evidence = Path(os.environ['ODSP_CROSS_STORE_EVIDENCE']) / root.name
    evidence.mkdir(mode=0o700)
    source = local.LocalRuntime(root / 'a', bins, nats)
    target = local.LocalRuntime(root / 'b', bins, nats)
    try:
        source.start()
        install_schema(source.direct_dsn)
        with psycopg.connect(source.direct_dsn) as db:
            db.execute('CREATE TABLE public.qualification_effects(job_id text PRIMARY KEY,value integer NOT NULL)')
        inputs = example.prepare(root / 'inputs')
        outcome = inputs.active.execute(inputs.root / 'workbench')
        # The projection predates a later lost sensor-read acknowledgement.
        # Its history remains intact while current binding readiness is held.
        fault = RecordedSensor(inputs.journal.device, (encode([example.declarations()[3]]),))
        plan = inputs.journal.prepare(fault, {'max_arrivals': 2, 'window_s': 2})
        with patch.object(fault, 'read_next', side_effect=OSError('synthetic lost read reply')) as fault_calls:
            with pytest.raises(OutcomeUnknown) as failure:
                inputs.journal.acquire(plan['plan_ref'], key='lost-sensor-reply', source=fault)
        assert fault_calls.call_count == 1
        unknown_sensor = failure.value.job_id
        assert inputs.journal.history(unknown_sensor)['outcome'] == 'outcome_unknown'
        assert inputs.journal.readiness(inputs.history['job_id'])['ready_for_held_projection'] is False
        q = queue(source)
        result = {'projection_manifest_ref': outcome['manifest_ref'],
                  'survey_capture_ref': inputs.model['survey']['capture_ref'],
                  'journal_job_id': inputs.history['job_id'], 'status': 'held'}
        done = q.enqueue(OPERATION, result, 'completed')
        def effect(operation, arguments, claim):
            assert operation == OPERATION and arguments == result
            with q.fenced_transaction(claim) as db:
                db.execute('INSERT INTO public.qualification_effects VALUES(%s,1)', (claim.fence.job_id,))
            return result
        assert q.execute_one('synthetic-worker', effect, job_id=done['job_id'])['status'] == 'completed'
        unknown = q.enqueue(OPERATION, result, 'lost-effect-reply')
        def uncertain(*args):
            effect(*args)
            raise OSError('synthetic lost committed SQL effect reply')
        with pytest.raises(OSError):
            q.execute_one('synthetic-worker', uncertain, job_id=unknown['job_id'])
        expired = q.enqueue(OPERATION, result, 'interrupted-dispatch')
        old_claim = q.claim_next('old-worker', job_id=expired['job_id'])
        q.begin_dispatch(old_claim)
        pending = q.enqueue(OPERATION, result, 'queued')
        stream = 'cross_store_' + uuid4().hex[:12]
        assert asyncio.run(publish(source, q, stream)) == 4
        owner = inputs.survey.store.owner
        retained = inputs.root / 'survey/retained' / sha256(owner.encode()).hexdigest()[:32]
        files = {str(p.relative_to(inputs.root)): p for p in (inputs.root / 'workbench').rglob('*') if p.is_file()}
        files.update({'survey/' + p.name: p for p in (inputs.root / 'survey').glob('*.json')})
        files.update({'retained/' + retained.name + '/' + p.name: p for p in retained.iterdir()
                      if p.name.endswith('.json') or p.name == 'sequence'})
        identities = {'tenant': inputs.ledger.tenant, 'principal': inputs.ledger.principal,
            'retention_owner': owner, 'projection_manifest_ref': outcome['manifest_ref'],
            'execution_id': outcome['execution_id'], 'result_id': outcome['result_id'],
            'journal_job_id': inputs.history['job_id'], 'journal_binding_ref': inputs.journal.binding_ref,
            'journal_unknown_job_id': unknown_sensor, 'journal_unknown_plan_ref': plan['plan_ref'],
            'survey_capture_ref': inputs.model['survey']['capture_ref'],
            'survey_result_ref': inputs.model['survey']['result_ref'], 'source_ref': inputs.survey.source_ref,
            'coordinator_queue_id': q.queue_id,
            'coordinator_jobs': {k: row['job_id'] for k, row in (
                ('done', done), ('unknown', unknown), ('expired', expired), ('queued', pending))}}
        participants = helpers.Participants(inputs.ledger, inputs.journal, retained, files, identities)
        bundle = evidence / 'set-v1'
        checkpoint, pin = helpers.capture_set(source, participants, bundle, quiesced=True)
        value = SimpleNamespace(root=root, evidence=evidence, source=source, target=target, inputs=inputs,
            outcome=outcome, q=q, result=result, participants=participants, bundle=bundle,
            checkpoint=checkpoint, pin=pin, old_claim=old_claim, stream=stream, pins=pins)
        helpers.write(evidence / 'runtime-provenance.json', canonical({'binaries': pins,
            'psycopg': psycopg.__version__, 'scratch': 'private_shared_memory_local_only',
            'power_loss_durability_tested': False}))
        yield value
    finally:
        # The execution sandbox may give each command a fresh shared-memory
        # mount. Keep the unique recovered data and process logs on disk before
        # that mount goes away; do not publish or retain broker credentials.
        try:
            if target.pg is not None and target.pg.poll() is None:
                helpers.write(evidence / 'final-coordinator.json', helpers._native().snapshot(target).encode())
            restored = root / 'restored'
            if restored.exists():
                final = evidence / 'restored-custody'
                final.mkdir(mode=0o700)
                for path in restored.rglob('*'):
                    if path.is_file() and not path.name.endswith(('.sqlite', '-wal', '-shm', '.lock')):
                        helpers.write(final / path.relative_to(restored), helpers.read(path))
                for kind in helpers.TABLES:
                    helpers.sqlite_backup(restored / (kind + '.sqlite'), final / (kind + '.sqlite'), kind)
        finally:
            target.close()
            source.close()
        for runtime, name in ((source, 'source'), (target, 'target')):
            for filename in ('initdb.log', 'postgres.log', 'nats.log', 'pg-restore.log', 'commands.json'):
                path = runtime.root / filename
                if path.exists():
                    helpers.write(evidence / (name + '-' + filename), helpers.read(path))
        helpers.write(evidence / 'owned-services-stopped.json', canonical({
            'source_stopped': source.pg is None and source.nats is None,
            'target_stopped': target.pg is None and target.nats is None,
            'existing_evidence_deleted': False,
            'unique_recovery_evidence_preserved_on_disk': True,
            'cluster_storage': 'temporary_private_shared_memory_not_power_loss_tested'}))


def validate(d, root=None, pin=None):
    return helpers.validate_set(root or d.bundle, expected_ref=pin or d.pin,
        expected_identities=d.participants.identities, expected_members=d.participants.files)


def cutover(d, *, participants=None, quiesced=True, staged=None):
    return helpers.cutover(d.source, d.target, participants or d.participants, d.checkpoint,
        d.bundle, staged or d.root / 'restored', expected_ref=d.pin, quiesced=quiesced)


@pytest.mark.parametrize('mutation', ['tamper', 'missing', 'extra', 'symlink', 'hardlink', 'unsafe-path',
                                     'wrong-journal', 'wrong-owner', 'wrong-external-pin'])
def test_incomplete_untrusted_or_mismatched_sets_hold_before_cutover(drill, mutation):
    d = drill
    root = d.evidence / ('bad-' + mutation)
    shutil.copytree(d.bundle, root)
    manifest_path = root / 'recovery-set.json'
    manifest = json.loads(manifest_path.read_bytes())
    pin = d.pin
    if mutation == 'tamper':
        (root / 'workbench/projection.json').rename(d.evidence / 'tamper-original-projection.json')
        helpers.write(root / 'workbench/projection.json', b'{}')
    elif mutation == 'missing':
        # Move the copied member into a preserved sibling, never discard evidence.
        (root / 'survey.sqlite').rename(d.evidence / 'missing-survey-preserved.sqlite')
    elif mutation == 'extra':
        helpers.write(root / 'unlisted.json', b'{}')
    elif mutation in ('symlink', 'hardlink'):
        name = root / 'workbench/model.json'
        original = d.evidence / (mutation + '-original-model.json')
        name.rename(original)
        if mutation == 'symlink':
            name.symlink_to(original)
        else:
            os.link(original, name)
    elif mutation == 'unsafe-path':
        manifest['members']['../escape.json'] = manifest['members'].pop('survey/source.json')
    elif mutation == 'wrong-owner':
        manifest['identities']['principal'] = 'other-principal'
    elif mutation == 'wrong-journal':
        # Native, well-formed alternate journal with the same binding and owner,
        # but missing the actual acquisition event history.
        (root / 'journal.sqlite').rename(d.evidence / 'swapped-journal-preserved.sqlite')
        spare_root = d.evidence / 'alternate-journal-source'
        spare_root.mkdir(mode=0o700)
        spare = ObservationJournal(spare_root / 'journal.sqlite', device=d.inputs.journal.device,
            tenant=d.inputs.ledger.tenant, principal=d.inputs.ledger.principal,
            authorize=d.inputs.journal._authorize, clock=d.inputs.journal._clock)
        replacement = root / 'journal.sqlite'
        helpers.sqlite_backup(spare.path, replacement, 'journal')
        manifest['members']['journal.sqlite'] = {'ref': helpers.ref(helpers.read(replacement)),
                                                 'bytes': replacement.stat().st_size}
        manifest['source_state']['sqlite']['journal'] = helpers.ref(helpers.canonical(
            helpers.sqlite_state(replacement, 'journal')))
    elif mutation == 'wrong-external-pin':
        pin = 'sha256:' + '0' * 64
    if mutation in ('unsafe-path', 'wrong-owner', 'wrong-journal'):
        raw = helpers.canonical(manifest)
        manifest_path.write_bytes(raw)
        pin = helpers.ref(raw)  # Even a newly pinned but incoherent set refuses.
    reasons = {'tamper': 'recovery_member_pin_mismatch', 'missing': 'unlisted_or_missing',
        'extra': 'unlisted_or_missing', 'symlink': 'recovery_links_refused',
        'hardlink': 'owned_bounded_regular', 'unsafe-path': 'recovery_member_set_changed',
        'wrong-journal': 'cross_store_journal_events_mismatch',
        'wrong-owner': 'contract_or_owner', 'wrong-external-pin': 'external_recovery_pin'}
    with pytest.raises(helpers.RecoveryHeld, match=reasons[mutation]):
        validate(d, root, pin)
    assert d.source.pg.poll() is None and d.target.pg is None and not d.target.data.exists()


@pytest.mark.parametrize('name', ['/survey.json', '../survey.json', 'survey/../../escape.json',
                                  'survey/./capture.json', 'survey\\capture.json', 'nats.conf'])
def test_fixed_member_paths_never_select_authority_or_escape(name):
    with pytest.raises(helpers.RecoveryHeld):
        helpers.safe_name(name)


def test_worker_quiescence_and_create_only_destination_are_required(drill):
    d = drill
    with pytest.raises(helpers.RecoveryHeld, match='quiescence'):
        cutover(d, quiesced=False)
    occupied = d.root / 'occupied'
    occupied.mkdir(mode=0o700)
    with pytest.raises(helpers.RecoveryHeld, match='new_separate'):
        cutover(d, staged=occupied)
    assert d.source.pg.poll() is None and d.target.pg is None


def test_advanced_sqlite_files_and_postgres_require_a_new_complete_set(drill):
    d = drill
    now = {'clock_id': 'synthetic-reference', 'epoch': 'synthetic-host-boot', 'seconds': 1001}
    d.inputs.journal._clock = lambda: now
    d.inputs.journal.readiness(d.inputs.history['job_id'])  # Actual durable antirollback advancement.
    with pytest.raises(helpers.RecoveryHeld, match='participant_advanced'):
        cutover(d)
    d.bundle = d.evidence / 'set-v2'
    d.checkpoint, d.pin = helpers.capture_set(d.source, d.participants, d.bundle, quiesced=True)
    changed = d.root / 'advanced-binding-evidence.json'
    helpers.write(changed, b'{"later_evidence":true}')
    files = {**d.participants.files, 'survey/binding.json': changed}
    with pytest.raises(helpers.RecoveryHeld, match='participant_advanced'):
        cutover(d, participants=replace(d.participants, files=files))
    probe = d.q.enqueue(OPERATION, d.result, 'later-queued-notice')
    with pytest.raises(helpers.RecoveryHeld, match='source_advanced'):
        cutover(d)
    assert d.source.pg.poll() is None and d.target.pg is None
    d.participants.identities['coordinator_jobs']['later_queued'] = probe['job_id']
    # Publish only the new queued wake-up, preserving all original source effects.
    async def extra():
        import nats
        client = await nats.connect(d.source.nats_url, token=d.source.token, allow_reconnect=False)
        try:
            bridge = JetStreamBridge(d.q, client.jetstream(), stream=d.stream, durable='worker', ack_wait=.3)
            await bridge.bind()
            assert await bridge.publish_pending() == 1
        finally:
            await client.close()
    asyncio.run(extra())
    d.bundle = d.evidence / 'set-v3'
    d.checkpoint, d.pin = helpers.capture_set(d.source, d.participants, d.bundle, quiesced=True)


def test_complete_cold_restore_fresh_authority_and_zero_resend(drill):
    import psycopg
    d = drill
    before = helpers.state(d.participants)
    history = d.inputs.journal.history(d.inputs.history['job_id'])
    coordinator_histories = {j: d.q.history(j) for j in d.participants.identities['coordinator_jobs'].values()}
    unknown_history = d.inputs.journal.history(d.participants.identities['journal_unknown_job_id'])
    source_export = d.inputs.journal.export(d.inputs.history['job_id'])
    report = cutover(d)
    restored_root = d.root / 'restored'
    assert report['all_participants_restored'] and report['custody_snapshot_equal']
    assert d.source.pg is None and d.source.nats is None
    assert helpers.state(d.participants) == before  # Source evidence retained unchanged.
    with pytest.raises(psycopg.OperationalError):
        d.q.complete(d.old_claim, {})
    q = queue(d.target)
    jobs = d.participants.identities['coordinator_jobs']
    for job_id in jobs.values():
        assert q.history(job_id) == coordinator_histories[job_id]
    assert q.inspect(jobs['done'])['result'] == d.result
    assert q.inspect(jobs['unknown'])['status'] == 'outcome_unknown'
    with pytest.raises(QueueRefusal):
        q.complete(d.old_claim, {})
    assert jobs['expired'] in [row['job_id'] for row in q.recover()]
    assert q.inspect(jobs['expired'])['status'] == 'outcome_unknown'
    assert q.inspect(jobs['expired'])['fence'] > d.old_claim.fence.number
    denied = queue(d.target, authorize=lambda *args: False)
    with pytest.raises(QueueRefusal):
        denied.claim_next('fresh-denied-worker')
    def forbidden(*args, **kwargs):
        pytest.fail('restoration must not redispatch, reacquire or recompute')
    for name in ('done', 'unknown', 'expired'):
        assert q.execute_one('no-resend', forbidden, job_id=jobs[name]) is None
    assert q.pending_notifications() == []  # Saved PubAcks do not restore broker state.
    assert q.reconcile_notifications() == 2
    assert asyncio.run(publish(d.target, q, d.stream, fresh=True)) == 2
    for name in ('queued', 'later_queued'):
        assert q.inspect(jobs[name])['status'] == 'queued'
    os.environ['NCP_RETAIN_DIR'] = str(restored_root / 'retained')  # Trusted launcher binding only.
    fresh_policy = {'schema': 'ciw.principal-policy.v1', 'revision': 'fresh-recovery-host', 'principals': [{
        'principal': 'survey-operator', 'enabled': True, 'operations': [], 'scientific_operations': [],
        'resources': [], 'reference_scopes': [], 'handoff_recipients': []}]}
    policy = PrincipalPolicy(lambda: fresh_policy)  # Never load the historical policy evidence file.
    calls = []
    def admission(action, source, context):
        calls.append(action)
        return action in {'open', 'read', 'assess', 'verify_chain'}
    ledger = DataInstrumentLedger(restored_root / 'survey.sqlite', tenant='synthetic-company',
        principal='survey-operator', policy=policy, admit=admission,
        clock=lambda: {'value': 1001, 'frame': example.survey_example().FRAME})
    survey = OfflineSurveyFlow(ledger=ledger, source_id=d.inputs.survey.source_id,
        source_ref=d.inputs.survey.source_ref, binding=parse(d.inputs.survey._binding_raw))
    authorizations = []
    permissions = {'journal': True}
    def authority(action, request):
        authorizations.append(action)
        if not permissions['journal']:
            return None
        return {'tenant': 'synthetic-company', 'principal': 'survey-operator',
            'binding_ref': d.participants.identities['journal_binding_ref'],
            'issuer': 'synthetic-recovery-host', 'epoch': 'new-recovery-policy',
            'policy_ref': helpers.ref(b'fresh recovery policy supplied by host'),
            'clock_epoch': 'synthetic-host-boot', 'expires_at_s': 1100,
            'actions': ['open', 'read', 'prepare', 'acquire', 'stop', 'recover']}
    recovery_clock = {'clock_id': 'synthetic-reference', 'epoch': 'synthetic-host-boot', 'seconds': 1001}
    journal = ObservationJournal(restored_root / 'journal.sqlite', device=d.inputs.journal.device,
        tenant=ledger.tenant, principal=ledger.principal, authorize=authority,
        clock=lambda: recovery_clock)
    with patch.object(RecordedSensor, 'read_next', side_effect=forbidden), \
         patch('ciw.numerical_workflow.execute', side_effect=forbidden), \
         patch('ciw.services.invoke', side_effect=forbidden):
        assert journal.history(d.inputs.history['job_id']) == history
        assert journal.history(d.participants.identities['journal_unknown_job_id']) == unknown_history
        assert journal.export(d.inputs.history['job_id']) == source_export
        retry = journal.acquire(d.participants.identities['journal_unknown_plan_ref'],
                                key='lost-sensor-reply', source=None)
        assert retry == unknown_history
        assert journal.recover()['source_reads'] == 0
        assert journal.readiness(d.inputs.history['job_id'])['ready_for_held_projection'] is False
        with pytest.raises(Refusal):
            journal.prepare(RecordedSensor(journal.device, (encode([example.declarations()[3]]),)),
                            {'max_arrivals': 2, 'window_s': 2})
        selected = {k: d.inputs.model['survey'][k] for k in ('artifact_id', 'capture_ref')}
        view = survey.read(**selected)
        assert view['retained_result']['result_ref'] == d.inputs.model['survey']['result_ref']
        assert ledger.snapshot(survey.source_id, selected['capture_ref']) == \
               (restored_root / 'workbench/survey-raw.json').read_bytes()
        ledger.verify_chain()
        projection = inspect_workbench(restored_root / 'workbench',
            expected_manifest_ref=d.outcome['manifest_ref'])['projection']
        assert projection['execution_id'] == d.outcome['execution_id']
        reader = InternalOperatorInspection(queue=q, job_ids=list(jobs.values()), survey=survey,
            survey_record=selected, journal=journal, journal_job_id=d.inputs.history['job_id'],
            workbench=restored_root / 'workbench', expected_manifest_ref=d.outcome['manifest_ref'],
            authorize=lambda *args: True)
        operator = reader.snapshot()
        assert operator['status'] == 'held'
        assert operator['components']['projection']['current_dependencies_held'] is True
        assert operator['components']['projection']['dependencies_match'] is True
        permissions['journal'] = False
        with pytest.raises(Refusal):
            journal.history(d.inputs.history['job_id'])
        permissions['journal'] = True
        recovery_clock['seconds'] = 1000
        with pytest.raises(Refusal):
            journal.readiness(d.inputs.history['job_id'])
        recovery_clock['seconds'] = 1001
        denied_policy = PrincipalPolicy(lambda: {**fresh_policy, 'principals': []})
        with pytest.raises(ValueError):
            DataInstrumentLedger(restored_root / 'survey.sqlite', tenant='synthetic-company',
                principal='survey-operator', policy=denied_policy, admit=admission,
                clock=lambda: {'value': 1001, 'frame': example.survey_example().FRAME})
    assert authorizations and calls and d.inputs.source.read_calls == 2
    with psycopg.connect(d.target.direct_dsn) as db:
        assert db.execute('SELECT job_id,value FROM public.qualification_effects ORDER BY job_id').fetchall() \
               == sorted((jobs[name], 1) for name in ('done', 'unknown'))
    operator_ref = save_report(d.evidence / 'restored-operator.json', operator)
    # A separate interpreter verifies custody and arithmetic without importing
    # candidate validation, Session, acquisition, queue or operator helpers.
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': '/tmp/odsp-distributed-runtime/venv/lib/python3.12/site-packages'}
    result = subprocess.run([sys.executable, str(REPO / 'tools/distributed-coordinator/verify_cross_store.py'),
        '--set', str(d.bundle), '--restored', str(restored_root), '--expected-ref', d.pin,
        '--dsn', d.target.direct_dsn], env=env, capture_output=True, timeout=20)
    helpers.write(d.evidence / 'independent.stdout.json', result.stdout)
    helpers.write(d.evidence / 'independent.stderr.log', result.stderr)
    assert result.returncode == 0, result.stderr.decode()
    independent = json.loads(result.stdout)
    assert independent['status'] == 'verified_held' and independent['effect_rows'] == 2
    report.update(identities=d.participants.identities, independent=independent,
        operator_ref=operator_ref, completed_journal_export_equal=True,
        unknown_journal_attempt_returned_without_source=True, later_binding_hold_preserved=True,
        terminal_coordinator_jobs_not_redispatched=True, queued_wakeups_only_republished=2,
        queued_jobs_still_queued=True, original_projection_execution_preserved=True,
        fresh_authority_supplied=True, revoked_authority_refused=True,
        journal_revocation_refused=True, survey_reopen_without_current_grant_refused=True,
        journal_clock_rollback_refused=True,
        old_worker_and_endpoint_refused=True, restored_effect_rows=2,
        physical_device_reads=0, resumed_work=0, recomputation=0,
        source_synthetic_reads_before_capture=2, fault_read_calls_before_capture=1)
    helpers.write(d.evidence / 'cross-store-result.json', canonical(report))
    helpers.write(d.evidence / 'latest.json', canonical({'set': str(d.bundle), 'set_ref': d.pin,
        'restored': str(restored_root), 'result': 'cross-store-result.json'}))
