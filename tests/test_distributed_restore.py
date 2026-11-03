"""Actual native PostgreSQL dump/restore and fresh JetStream recovery, local only.

Every database, effect and token belongs to these synthetic fixtures. No hosted
DSN, device, existing database, operating credential or security setting is used.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import asyncio
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest

from ciw.distributed_nats import JetStreamBridge
from ciw.distributed_queue import PostgresQueue, QueueRefusal, install_schema


def load(name, filename):
    import sys
    path = Path(__file__).resolve().parents[1] / 'tools/distributed-coordinator' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helpers = load('odsp_restore_qualification_helper', 'restore_fixture.py')
local = load('odsp_restore_qualification_runtime', 'local_runtime.py')


@pytest.fixture(scope='module')
def runtimes():
    bins = [os.environ.get(k) for k in ('ODSP_DISTRIBUTED_PG_BIN', 'ODSP_DISTRIBUTED_NATS_SERVER')]
    if not all(bins):
        if os.environ.get('ODSP_REQUIRE_DISTRIBUTED') == '1':
            pytest.fail('pinned local PostgreSQL/NATS inputs required')
        pytest.skip('pinned local PostgreSQL/NATS inputs required')
    base = Path(os.environ.get('ODSP_DISTRIBUTED_EVIDENCE', '/tmp/odp-dr'))
    root = base / ('restore-' + uuid4().hex[:12])
    source = local.LocalRuntime(root / 'a', *bins)
    target = local.LocalRuntime(root / 'b', *bins)
    try:
        source.start()
        install_schema(source.direct_dsn)
        with db(source) as connection:
            connection.execute('CREATE TABLE public.qualification_effects(job_id text PRIMARY KEY, value integer NOT NULL)')
        yield source, target
    finally:
        target.close()
        source.close()


def db(runtime):
    import psycopg
    return psycopg.connect(runtime.direct_dsn, connect_timeout=2)


def queue(runtime, generation):
    return PostgresQueue(runtime.direct_dsn, owner='restore-fixture', principal='alice',
                         generation=generation, operations=['ncp_capabilities_list'],
                         authorize=lambda *args: True, lease_seconds=.5)


def cutover(source, target, checkpoint, **overrides):
    values = dict(expected_archive_sha256=checkpoint.archive_sha256,
                  expected_snapshot_sha256=checkpoint.snapshot_sha256)
    values.update(overrides)
    return helpers.restore_current(source, target, checkpoint, **values)


def test_corrupt_or_replaced_backup_refuses_before_cutover(runtimes):
    source, target = runtimes
    checkpoint = helpers.capture(source, source.root / 'corruption.dump')
    actual = checkpoint.archive.read_bytes()
    checkpoint.archive.write_bytes(b'PGDMP' + b'corrupted')
    with pytest.raises(helpers.RestoreHeld, match='backup_content_changed'):
        cutover(source, target, checkpoint)
    checkpoint.archive.write_bytes(actual)
    with pytest.raises(helpers.RestoreHeld, match='expected_identity_changed'):
        cutover(source, target, checkpoint, expected_archive_sha256='0' * 64)
    assert source.pg.poll() is None and target.pg is None and not target.data.exists()


def test_historical_checkpoint_and_wrong_source_refuse_before_cutover(runtimes):
    source, target = runtimes
    active = queue(source, 'stale-checkpoint')
    active.enqueue('ncp_capabilities_list', {}, 'before')
    checkpoint = helpers.capture(source, source.root / 'stale.dump')
    active.enqueue('ncp_capabilities_list', {}, 'after')
    with pytest.raises(helpers.RestoreHeld, match='source_advanced'):
        cutover(source, target, checkpoint)
    with pytest.raises(helpers.RestoreHeld, match='source_binding_changed'):
        cutover(source, target, replace(checkpoint, source_root=str(target.root)))
    assert source.pg.poll() is None and target.pg is None and not target.data.exists()


def test_native_current_checkpoint_restore_and_notification_recovery(runtimes, record_property):
    source, target = runtimes
    active = queue(source, 'current-checkpoint')
    done = active.enqueue('ncp_capabilities_list', {}, 'completed')
    def effect(operation, arguments, claim):
        with active.fenced_transaction(claim) as connection:
            connection.execute('INSERT INTO public.qualification_effects VALUES(%s,1)', (claim.fence.job_id,))
        return {'retained': 'synthetic-result'}
    assert active.execute_one('before-restore', effect)['status'] == 'completed'
    unknown = active.enqueue('ncp_capabilities_list', {}, 'unknown')
    def uncertain(*args):
        effect(*args)
        raise OSError('synthetic lost effect acknowledgement')
    with pytest.raises(OSError):
        active.execute_one('before-restore', uncertain)
    expired = active.enqueue('ncp_capabilities_list', {}, 'interrupted')
    claim = active.claim_next('old-worker')
    active.begin_dispatch(claim)
    pending = active.enqueue('ncp_capabilities_list', {}, 'queued')
    saved_histories = {j['job_id']: active.history(j['job_id']) for j in (done, unknown, expired, pending)}
    saved_next_fence = max(rows[-1]['fence'] for rows in saved_histories.values())
    stream = 'restore_' + uuid4().hex[:12]
    async def publish_before_loss():
        import nats
        client = await nats.connect(source.nats_url, token=source.token, allow_reconnect=False)
        try:
            bridge = JetStreamBridge(active, client.jetstream(), stream=stream, durable='worker', ack_wait=.3)
            await bridge.provision()
            assert await bridge.publish_pending() == 4
            assert active.pending_notifications() == []
        finally:
            await client.close()
    asyncio.run(publish_before_loss())
    checkpoint = helpers.capture(source, source.root / 'current.dump')
    source_socket = source.socket
    report = cutover(source, target, checkpoint)
    assert report['custody_snapshot_equal'] and report['source_services_stopped']
    assert source.pg is None and source.nats is None and not list(source_socket.glob('.s.PGSQL.*'))
    import psycopg
    with pytest.raises(psycopg.OperationalError):
        active.complete(claim, {'late_old_worker': True})
    restored = queue(target, 'current-checkpoint')  # Fresh host authorization; never loaded from the dump.
    for job in (done, unknown, expired, pending):
        assert restored.history(job['job_id']) == saved_histories[job['job_id']]
    with pytest.raises(QueueRefusal):
        restored.complete(claim, {'late': True})
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not restored.recover():
        time.sleep(.05)
    assert restored.inspect(expired['job_id'])['status'] == 'outcome_unknown'
    assert restored.inspect(expired['job_id'])['fence'] > saved_next_fence
    assert restored.inspect(done['job_id'])['result'] == {'retained': 'synthetic-result'}
    assert restored.inspect(unknown['job_id'])['status'] == 'outcome_unknown'
    assert restored.pending_notifications() == []  # Old PubAck metadata is not broker durability.
    denied = PostgresQueue(target.direct_dsn, owner='restore-fixture', principal='alice',
                           generation='current-checkpoint', operations=['ncp_capabilities_list'],
                           authorize=lambda *args: False, lease_seconds=.5)
    with pytest.raises(QueueRefusal, match='authoriz'):
        denied.claim_next('revoked-worker')
    assert restored.inspect(pending['job_id'])['status'] == 'queued'
    calls = []
    def restored_effect(operation, arguments, current):
        calls.append(current.fence)
        with restored.fenced_transaction(current) as connection:
            connection.execute('INSERT INTO public.qualification_effects VALUES(%s,1)', (current.fence.job_id,))
        return {'restored': True}
    async def reconcile_fresh_broker():
        import nats
        client = await nats.connect(target.nats_url, token=target.token, allow_reconnect=False)
        try:
            from nats.js.errors import NotFoundError
            with pytest.raises(NotFoundError):
                await client.jetstream().stream_info(stream)
            bridge = JetStreamBridge(restored, client.jetstream(), stream=stream, durable='worker', ack_wait=.3)
            await bridge.provision()  # Only this new local synthetic broker.
            result = await bridge.cycle('new-worker', restored_effect, timeout=1)
            assert result['job_id'] == pending['job_id'] and result['status'] == 'completed'
            assert await bridge.cycle('new-worker', restored_effect, timeout=.1) is None
            assert (await bridge.js.consumer_info(stream, 'worker')).num_ack_pending == 0
        finally:
            await client.close()
    asyncio.run(reconcile_fresh_broker())
    assert len(calls) == 1 and calls[0].number > restored.inspect(expired['job_id'])['fence']
    assert restored.enqueue('ncp_capabilities_list', {}, 'queued')['replayed'] is True
    with pytest.raises(QueueRefusal, match='attempt_conflict'):
        restored.enqueue('ncp_capabilities_list', {'different': True}, 'queued')
    assert restored.execute_one('late-worker', lambda *args: pytest.fail('terminal job redispatched')) is None
    with db(target) as connection:
        effects = connection.execute('SELECT job_id,value FROM public.qualification_effects ORDER BY job_id').fetchall()
    assert effects == sorted((j['job_id'], 1) for j in (done, unknown, pending))
    report.update(completed_jobs_preserved=True, unknown_jobs_not_replayed=True,
                  expired_claim_recovered_as_unknown=True, queued_job_dispatched_once=True,
                  restored_effect_rows=len(effects), duplicate_attempt_preserved=True,
                  fence_advanced=True, authorization_callback_supplied_anew=True,
                  revoked_authority_refused_before_dispatch=True,
                  old_source_endpoint_unavailable=True)
    (target.root.parent / 'restore-result.json').write_text(json.dumps(report, indent=2) + '\n')
    record_property('restore_archive_sha256', checkpoint.archive_sha256)
    record_property('restored_custody_sha256', checkpoint.snapshot_sha256)
    record_property('restore_seconds_observed', report['restore_seconds'])
