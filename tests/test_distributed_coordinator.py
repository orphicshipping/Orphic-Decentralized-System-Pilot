"""Actual PostgreSQL and NATS qualification; no fake persistence or broker.

Set ODSP_DISTRIBUTED_PG_BIN and ODSP_DISTRIBUTED_NATS_SERVER to private pinned
runtime paths. ODSP_REQUIRE_DISTRIBUTED=1 makes missing prerequisites a failure.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest

from ciw.distributed_queue import PostgresQueue, QueueLimits, QueueRefusal, install_schema
from ciw.distributed_nats import JetStreamBridge


def _allow(principal, operation, arguments):
    return True


def _process_claim(dsn, identity, ready, result):
    queue = PostgresQueue(dsn, **identity, operations=["ncp_capabilities_list"],
                          authorize=_allow, lease_seconds=1)
    ready.wait(timeout=10)
    claim = queue.claim_next("child-" + str(os.getpid()))
    result.put(None if claim is None else claim.fence.number)


@pytest.fixture(scope="module")
def runtime():
    bins = (os.getenv("ODSP_DISTRIBUTED_PG_BIN"), os.getenv("ODSP_DISTRIBUTED_NATS_SERVER"))
    modules = all(importlib.util.find_spec(name) is not None for name in ("psycopg", "nats"))
    if not all(bins) or not modules:
        reason = "real distributed qualification requires pinned PostgreSQL/NATS runtimes and Python drivers"
        if os.getenv("ODSP_REQUIRE_DISTRIBUTED") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    helper = Path(__file__).resolve().parents[1] / "tools/distributed-coordinator/local_runtime.py"
    spec = importlib.util.spec_from_file_location("odsp_local_distributed_runtime", helper)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    root = Path(os.getenv("ODSP_DISTRIBUTED_EVIDENCE", "/tmp/odsp-distributed-runtime/evidence")) / ("run-" + uuid4().hex[:12])
    active = module.LocalRuntime(root, *bins)
    try:
        active.start()
        install_schema(active.dsn)
        with active_connection(active.dsn) as conn:
            conn.execute("CREATE TABLE qualification_effects(job_id text PRIMARY KEY, value integer NOT NULL)")
        pooler_binary = os.getenv("ODSP_DISTRIBUTED_PGBOUNCER_BIN")
        if pooler_binary:
            helper = helper.with_name("local_pooler.py")
            spec = importlib.util.spec_from_file_location("odsp_local_pooler", helper)
            pooler_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(pooler_module)
            active.pooler = pooler_module.LocalPooler(root / "pooler", pooler_binary,
                                                     active.socket, active.pg_port)
            active.pooler.start()
            active.dsn = active.pooler.dsn
        print("distributed_runtime_evidence=" + str(root))
        yield active
    finally:
        active.close()


def active_connection(dsn):
    import psycopg
    return psycopg.connect(dsn, connect_timeout=3)


@pytest.fixture
def make_queue(runtime):
    identity = {"owner": "qualification", "principal": "alice", "generation": "test-" + uuid4().hex}
    def make(**overrides):
        arguments = dict(identity, operations=["ncp_capabilities_list"], authorize=_allow, lease_seconds=1)
        arguments.update(overrides)
        return PostgresQueue(runtime.dsn, **arguments)
    return make


def test_atomic_attempt_and_transactional_outbox(make_queue, runtime):
    queues = [make_queue() for _ in range(4)]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda q: q.enqueue("ncp_capabilities_list", {}, "same-attempt"), queues))
    assert len({r["job_id"] for r in results}) == 1
    assert sum(not r["replayed"] for r in results) == 1
    queue = queues[0]
    assert len(queue.pending_notifications()) == 1
    assert [e["state"] for e in queue.history(results[0]["job_id"])] == ["queued"]
    with pytest.raises(QueueRefusal, match="attempt_conflict"):
        queue.enqueue("ncp_capabilities_list", {"changed": True}, "same-attempt")
    assert queue.inspect(results[0]["job_id"])["status"] == "queued"
    with active_connection(runtime.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM odsp_distributed_v1.jobs WHERE queue_id=%s", (queue.queue_id,)).fetchone()[0] == 1


def test_real_multiprocess_claims_and_permanent_expiry(make_queue, runtime):
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "claim-once")
    ctx = multiprocessing.get_context("spawn")
    ready, result = ctx.Barrier(4), ctx.Queue()
    children = [ctx.Process(target=_process_claim, args=(runtime.dsn, queue.identity, ready, result)) for _ in range(4)]
    for child in children:
        child.start()
    answers = [result.get(timeout=20) for _ in children]
    for child in children:
        child.join(timeout=10)
        assert child.exitcode == 0
    assert sum(value is not None for value in answers) == 1
    time.sleep(1.05)
    recovered = queue.recover()
    assert len(recovered) == 1 and recovered[0]["status"] == "outcome_unknown"
    assert recovered[0]["fence"] > max(value for value in answers if value)
    assert queue.claim_next("another-worker") is None
    assert queue.enqueue("ncp_capabilities_list", {}, "claim-once")["status"] == "outcome_unknown"
    assert queue.inspect(job["job_id"])["error"] == "worker_lost"
    assert queue.recover() == []


def test_renewal_and_fence_refuse_old_worker(make_queue):
    queue = make_queue(lease_seconds=0.35)
    first = queue.enqueue("ncp_capabilities_list", {}, "first")
    claim = queue.claim_next("worker")
    time.sleep(0.18); queue.renew(claim); time.sleep(0.18)
    assert queue.recover() == []
    queue.begin_dispatch(claim)
    assert queue.complete(claim, {"ok": True})["status"] == "completed"
    queue.enqueue("ncp_capabilities_list", {}, "second")
    later = queue.claim_next("worker")
    assert later.fence.number > claim.fence.number
    time.sleep(0.37)
    with pytest.raises(QueueRefusal, match="lease_expired"):
        queue.renew(later)
    queue.recover()
    with pytest.raises(QueueRefusal, match="stale_claim"):
        queue.complete(later, {"bad": True})
    assert queue.inspect(first["job_id"])["result"] == {"ok": True}


def test_real_principal_revocation_before_intent_has_zero_dispatch(make_queue):
    from ciw.principal_policy import PrincipalPolicy
    grant = {"principal": "alice", "enabled": True,
             "operations": [{"name": "ncp_capabilities_list", "constraints": {}}],
             "scientific_operations": [], "resources": [], "reference_scopes": [], "handoff_recipients": []}
    document = {"schema": "ciw.principal-policy.v1", "revision": "distributed-test", "principals": [grant]}
    policy = PrincipalPolicy(lambda: deepcopy(document))
    calls = []
    def authorize(principal, name, arguments):
        policy.authorize(principal, name, arguments)
        calls.append(name)
        if len(calls) == 2:  # Enqueue, then claim; revoke before final dispatch gate.
            grant["enabled"] = False
    queue = make_queue(authorize=authorize)
    job = queue.enqueue("ncp_capabilities_list", {}, "revoked")
    invoked = []
    with pytest.raises(ValueError):
        queue.execute_one("worker", lambda *args: invoked.append(args))
    assert invoked == []
    grant["enabled"] = True
    assert queue.inspect(job["job_id"])["status"] == "refused"
    assert [r["state"] for r in queue.history(job["job_id"])] == ["queued", "claimed", "refused"]


def test_same_database_effect_and_stale_fence_rollback(make_queue, runtime):
    queue = make_queue(lease_seconds=0.4)
    job = queue.enqueue("ncp_capabilities_list", {}, "effect")
    claim = queue.claim_next("worker")
    queue.begin_dispatch(claim)
    with pytest.raises(QueueRefusal, match="lease_expired"):
        with queue.fenced_transaction(claim) as conn:
            conn.execute("INSERT INTO public.qualification_effects VALUES(%s,1)", (job["job_id"],))
            conn.execute("SELECT pg_sleep(0.45)")
    with active_connection(runtime.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM public.qualification_effects WHERE job_id=%s", (job["job_id"],)).fetchone()[0] == 0
    queue.recover()
    with pytest.raises(QueueRefusal, match="stale_claim"):
        with queue.fenced_transaction(claim):
            pytest.fail("stale downstream fence admitted")


def test_durable_intent_unknown_after_effect_and_no_retry(make_queue, runtime):
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "uncertain")
    def dispatch(operation, arguments, claim):
        assert queue.inspect(job["job_id"])["status"] == "dispatching"
        with queue.fenced_transaction(claim) as conn:
            conn.execute("INSERT INTO public.qualification_effects VALUES(%s,1)", (job["job_id"],))
        raise RuntimeError("simulated acknowledgement loss after committed effect")
    with pytest.raises(RuntimeError):
        queue.execute_one("worker", dispatch)
    assert queue.inspect(job["job_id"])["status"] == "outcome_unknown"
    assert queue.execute_one("worker", lambda *args: pytest.fail("re-dispatched")) is None
    with active_connection(runtime.dsn) as conn:
        assert conn.execute("SELECT value FROM public.qualification_effects WHERE job_id=%s", (job["job_id"],)).fetchone() == (1,)
    assert [e["state"] for e in queue.history(job["job_id"])] == ["queued", "claimed", "dispatching", "outcome_unknown"]


def test_background_lease_renewal_keeps_actual_long_dispatch_owned(make_queue):
    queue = make_queue(lease_seconds=0.3)
    job = queue.enqueue("ncp_capabilities_list", {}, "heartbeat")
    def dispatch(operation, arguments, claim):
        time.sleep(0.7)
        assert queue.recover() == []
        return {"value": 17}
    assert queue.execute_one("worker", dispatch)["result"] == {"value": 17}
    assert queue.inspect(job["job_id"])["status"] == "completed"


def test_storage_restart_keeps_outbox_intent_and_terminal(make_queue, runtime):
    queue = make_queue(lease_seconds=0.4)
    queued = queue.enqueue("ncp_capabilities_list", {}, "restart-queued")
    queue.execute_one("worker", lambda *args: {"complete": True})
    interrupted = queue.enqueue("ncp_capabilities_list", {}, "restart-interrupted")
    claim = queue.claim_next("worker"); queue.begin_dispatch(claim)
    runtime.stop_process("pg", crash=True)
    time.sleep(0.45)
    runtime.start_postgres()
    reopened = make_queue(lease_seconds=0.4)
    assert reopened.inspect(queued["job_id"])["status"] == "completed"
    assert len(reopened.pending_notifications()) == 2
    assert reopened.recover()[0]["job_id"] == interrupted["job_id"]
    assert reopened.inspect(interrupted["job_id"])["status"] == "outcome_unknown"
    assert reopened.claim_next("after-restart") is None
    assert len(reopened.history(interrupted["job_id"])) == 4


def test_capacity_identity_and_corrupted_history_fail_closed(make_queue, runtime):
    limits = replace(QueueLimits(), max_jobs=2, max_pending=1)
    queue = make_queue(limits=limits)
    job = queue.enqueue("ncp_capabilities_list", {}, "capacity")
    with pytest.raises(QueueRefusal, match="capacity"):
        queue.enqueue("ncp_capabilities_list", {}, "overflow")
    with pytest.raises(QueueRefusal, match="binding_mismatch"):
        make_queue()
    other = make_queue(principal="bob", limits=limits)
    with pytest.raises(QueueRefusal, match="job_unknown"):
        other.inspect(job["job_id"])
    identity = queue.identity; identity["principal"] = "bob"
    assert queue.identity["principal"] == "alice"
    with active_connection(runtime.dsn) as conn:
        conn.execute("UPDATE odsp_distributed_v1.events SET event_sha256=%s WHERE queue_id=%s", ("0"*64, queue.queue_id))
    with pytest.raises(QueueRefusal, match="history_integrity"):
        queue.history(job["job_id"])


def test_oversized_and_invalid_results_are_unknown_after_dispatch(make_queue):
    queue = make_queue(limits=replace(QueueLimits(), max_result_bytes=256))
    for index, value in enumerate((None, {"huge": "x"*300}, {"nan": float("nan")})):
        job = queue.enqueue("ncp_capabilities_list", {}, "invalid-" + str(index))
        with pytest.raises(ValueError):
            queue.execute_one("worker", lambda *args: value)
        assert queue.inspect(job["job_id"])["status"] == "outcome_unknown"


async def _bridge(runtime, queue, *, suffix=None, ack_wait=0.3):
    import nats
    nc = await nats.connect(runtime.nats_url, token=runtime.token, connect_timeout=2,
                            max_reconnect_attempts=0, allow_reconnect=False)
    suffix = suffix or uuid4().hex[:16]
    bridge = JetStreamBridge(queue, nc.jetstream(), stream="ODSP_" + suffix, durable="worker", ack_wait=ack_wait)
    await bridge.provision()
    return nc, bridge


def test_real_jetstream_duplicate_delivery_dispatches_once(make_queue, runtime):
    async def scenario():
        queue = make_queue()
        job = queue.enqueue("ncp_capabilities_list", {}, "broker-duplicate")
        nc, bridge = await _bridge(runtime, queue)
        try:
            pending = queue.pending_notifications()[0]
            assert await bridge.publish_pending() == 1
            # Re-publish with a different transport ID to bypass time-window
            # dedup. Database custody must still prohibit a second execution.
            await bridge.js.publish(bridge.subject, pending["payload"], headers={"Nats-Msg-Id": "forced-duplicate"})
            effects = []
            def dispatch(operation, arguments, claim):
                with queue.fenced_transaction(claim) as conn:
                    conn.execute("INSERT INTO public.qualification_effects VALUES(%s,1)", (job["job_id"],))
                effects.append(claim.fence.number)
                return {"ok": True}
            for _ in range(2):
                assert (await bridge.process_one("worker", dispatch))["status"] == "completed"
            assert len(effects) == 1
            info = await bridge.js.consumer_info(bridge.stream, bridge.durable)
            assert info.num_ack_pending == 0 and info.num_pending == 0
            assert queue.pending_notifications() == []
        finally:
            await nc.close()
    asyncio.run(scenario())


def test_publish_ack_lost_retries_notification_not_effect(make_queue, runtime, monkeypatch):
    async def scenario():
        queue = make_queue()
        job = queue.enqueue("ncp_capabilities_list", {}, "lost-puback")
        nc, bridge = await _bridge(runtime, queue)
        original = queue.notification_published
        try:
            def lost(*args, **kwargs):
                raise OSError("simulated crash after broker ack before database mark")
            monkeypatch.setattr(queue, "notification_published", lost)
            with pytest.raises(OSError):
                await bridge.publish_pending()
            assert len(queue.pending_notifications()) == 1
            monkeypatch.setattr(queue, "notification_published", original)
            assert await bridge.publish_pending() == 1
            info = await bridge.js.stream_info(bridge.stream)
            assert info.state.messages == 1
            assert (await bridge.process_one("worker", lambda *args: {"result": 1}))["job_id"] == job["job_id"]
        finally:
            await nc.close()
    asyncio.run(scenario())


def test_broker_restart_unacked_message_reconciles_committed_job(make_queue, runtime):
    async def before():
        queue = make_queue()
        job = queue.enqueue("ncp_capabilities_list", {}, "broker-restart")
        nc, bridge = await _bridge(runtime, queue)
        await bridge.publish_pending()
        messages = await bridge.subscription.fetch(1, timeout=1)
        assert len(messages) == 1
        queue.execute_one("worker", lambda *args: {"durable": True})
        await nc.close()  # Deliberately no ack for the fetched notification.
        return queue, job, bridge.stream
    queue, job, stream = asyncio.run(before())
    runtime.stop_process("nats", crash=True)
    time.sleep(0.4)
    runtime.start_nats()
    async def after():
        import nats
        nc = await nats.connect(runtime.nats_url, token=runtime.token)
        try:
            bridge = JetStreamBridge(queue, nc.jetstream(), stream=stream, durable="worker", ack_wait=0.3)
            await bridge.bind()
            result = await bridge.process_one("worker", lambda *args: pytest.fail("replayed after broker restart"), timeout=3)
            assert result["status"] == "completed" and result["job_id"] == job["job_id"]
            assert (await bridge.js.consumer_info(stream, "worker")).num_ack_pending == 0
        finally:
            await nc.close()
    asyncio.run(after())


def test_unknown_delivery_is_acked_without_reexecution(make_queue, runtime):
    async def scenario():
        queue = make_queue()
        job = queue.enqueue("ncp_capabilities_list", {}, "broker-unknown")
        nc, bridge = await _bridge(runtime, queue)
        try:
            payload = queue.pending_notifications()[0]["payload"]
            await bridge.publish_pending()
            calls = []
            def fail(*args):
                calls.append(True)
                raise RuntimeError("possible effect")
            with pytest.raises(RuntimeError):
                await bridge.process_one("worker", fail)
            assert queue.inspect(job["job_id"])["status"] == "outcome_unknown"
            await bridge.js.publish(bridge.subject, payload, headers={"Nats-Msg-Id": "later-unknown"})
            assert (await bridge.process_one("worker", fail))["status"] == "outcome_unknown"
            assert calls == [True]
        finally:
            await nc.close()
    asyncio.run(scenario())


def test_wrong_broker_configuration_and_untrusted_notifications_refuse(make_queue, runtime):
    async def scenario():
        queue = make_queue()
        job = queue.enqueue("ncp_capabilities_list", {}, "wrong-notice")
        nc, bridge = await _bridge(runtime, queue)
        try:
            payload = queue.pending_notifications()[0]["payload"]
            notice = json.loads(payload); notice["request_sha256"] = "0"*64
            bad = json.dumps(notice, sort_keys=True, separators=(",", ":")).encode()
            with pytest.raises(QueueRefusal, match="event_conflict"):
                queue.notification_job(bad)
            with pytest.raises(QueueRefusal, match="event_invalid"):
                queue.notification_job(payload[:-1] + b',"arguments":{"injected":true}}')
            changed = JetStreamBridge(queue, bridge.js, stream=bridge.stream, durable="worker", ack_wait=1)
            with pytest.raises(QueueRefusal, match="consumer_config_mismatch"):
                await changed.provision()
            assert queue.inspect(job["job_id"])["status"] == "queued"
            assert (await bridge.js.consumer_info(bridge.stream, "worker")).config.ack_wait == 0.3
        finally:
            await nc.close()
    asyncio.run(scenario())


def test_queued_notification_recovers_after_complete_broker_stream_loss(make_queue, runtime):
    async def scenario():
        queue = make_queue()
        job = queue.enqueue("ncp_capabilities_list", {}, "broker-data-loss")
        nc, bridge = await _bridge(runtime, queue)
        try:
            await bridge.publish_pending()
            assert queue.pending_notifications() == []
            # Destruction is confined to this test's private disposable stream.
            await bridge.js.delete_stream(bridge.stream)
            await bridge.provision()
            result = await bridge.cycle("worker", lambda *args: {"restored_notice": True})
            assert result["job_id"] == job["job_id"] and result["status"] == "completed"
            assert queue.reconcile_notifications() == 0
            assert queue.inspect(job["job_id"])["result"] == {"restored_notice": True}
        finally:
            await nc.close()
    asyncio.run(scenario())


def test_completion_commit_ack_loss_preserves_completed_outcome(make_queue, monkeypatch):
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "completion-ack-loss")
    complete = queue.complete
    def uncertain_ack(claim, value):
        complete(claim, value)
        raise OSError("simulated client disconnect after durable commit")
    monkeypatch.setattr(queue, "complete", uncertain_ack)
    with pytest.raises(OSError):
        queue.execute_one("worker", lambda *args: {"durable": True})
    reopened = make_queue()
    assert reopened.inspect(job["job_id"])["status"] == "completed"
    assert reopened.recover() == []
    assert reopened.execute_one("new-worker", lambda *args: pytest.fail("duplicate dispatch")) is None
    assert reopened.history(job["job_id"])[-1]["state"] == "completed"


def test_postgres_unavailable_never_enters_callback(make_queue, runtime):
    import psycopg
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "database-down")
    runtime.stop_process("pg")
    try:
        with pytest.raises(psycopg.OperationalError):
            queue.execute_one("worker", lambda *args: pytest.fail("database-down dispatch"))
    finally:
        runtime.start_postgres()
    assert queue.inspect(job["job_id"])["status"] == "queued"
    assert len(queue.history(job["job_id"])) == 1


def test_cancelled_dispatch_custody_is_unknown_without_retry(make_queue):
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "interrupted")
    def interrupted(*args):
        raise KeyboardInterrupt("local interruption")
    with pytest.raises(KeyboardInterrupt):
        queue.execute_one("worker", interrupted)
    assert queue.inspect(job["job_id"])["status"] == "outcome_unknown"
    assert not queue._claims
    assert queue.execute_one("next-worker", lambda *args: pytest.fail("retry")) is None


def test_intent_commit_failure_never_enters_callback(make_queue, monkeypatch):
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "intent-failure")
    begin = queue.begin_dispatch
    def lost_ack(claim):
        begin(claim)
        raise OSError("simulated intent-commit acknowledgement loss")
    monkeypatch.setattr(queue, "begin_dispatch", lost_ack)
    with pytest.raises(OSError):
        queue.execute_one("worker", lambda *args: pytest.fail("ambiguous intent dispatch"))
    assert queue.inspect(job["job_id"])["status"] == "refused"
    assert not queue._claims
    assert [e["state"] for e in queue.history(job["job_id"])] == ["queued", "claimed", "dispatching", "refused"]


def test_existing_state_corruption_cannot_be_read_as_valid_history(make_queue, runtime):
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "result-corruption")
    queue.execute_one("worker", lambda *args: {"value": 1})
    with active_connection(runtime.dsn) as conn:
        conn.execute("UPDATE odsp_distributed_v1.jobs SET result=%s WHERE queue_id=%s AND job_id=%s",
                     (b'{"value":2}', queue.queue_id, job["job_id"]))
    with pytest.raises(QueueRefusal, match="history_outcome_mismatch"):
        queue.history(job["job_id"])
    with pytest.raises(QueueRefusal, match="history_outcome_mismatch"):
        queue.inspect(job["job_id"])


def test_fenced_sql_accidental_early_commit_refuses_and_rolls_back(make_queue, runtime):
    import psycopg
    queue = make_queue()
    job = queue.enqueue("ncp_capabilities_list", {}, "early-commit")
    claim = queue.claim_next("worker"); queue.begin_dispatch(claim)
    with pytest.raises(psycopg.ProgrammingError):
        with queue.fenced_transaction(claim) as conn:
            conn.execute("INSERT INTO public.qualification_effects VALUES(%s,1)", (job["job_id"],))
            conn.commit()
    with active_connection(runtime.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM public.qualification_effects WHERE job_id=%s", (job["job_id"],)).fetchone()[0] == 0
    queue.abandon(claim)


def test_runtime_role_requires_no_schema_or_delete_privilege(make_queue, runtime):
    import psycopg
    from psycopg import sql
    name = "runtime_" + uuid4().hex[:12]
    with active_connection(runtime.dsn) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA odsp_distributed_v1 TO {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("GRANT SELECT ON odsp_distributed_v1.format TO {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON odsp_distributed_v1.queues,odsp_distributed_v1.jobs,"
                             "odsp_distributed_v1.outbox TO {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("GRANT SELECT,INSERT ON odsp_distributed_v1.events TO {}").format(sql.Identifier(name)))
    from psycopg.conninfo import make_conninfo
    if runtime.pooler is not None:
        runtime.pooler.register_user(name)
    dsn = make_conninfo(runtime.dsn, user=name)
    queue = PostgresQueue(dsn, owner="role-test", principal="alice", generation=name,
                          operations=["ncp_capabilities_list"], authorize=_allow)
    job = queue.enqueue("ncp_capabilities_list", {}, "limited-role")
    assert queue.execute_one("worker", lambda *args: {"least_privilege": True})["status"] == "completed"
    assert len(queue.history(job["job_id"])) == 4
    for statement in ("DELETE FROM odsp_distributed_v1.events", "DROP TABLE odsp_distributed_v1.jobs"):
        with active_connection(dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_concurrent_distinct_jobs_respect_saved_running_capacity(make_queue):
    queue = make_queue(limits=replace(QueueLimits(), max_running=2), lease_seconds=2)
    for i in range(4):
        queue.enqueue("ncp_capabilities_list", {}, "parallel-" + str(i))
    queues = [make_queue(limits=replace(QueueLimits(), max_running=2), lease_seconds=2) for _ in range(4)]
    with ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda pair: pair[1].claim_next("worker-" + str(pair[0])), enumerate(queues)))
    live = [(q, claim) for q, claim in zip(queues, claims) if claim is not None]
    assert len(live) == 2 and len({claim.fence.number for _, claim in live}) == 2
    for owner, claim in live:
        owner.begin_dispatch(claim)
        owner.complete(claim, {"done": True})
    assert queue.claim_next("later-worker") is not None


def test_request_limits_exact_types_and_authorization_truthiness(make_queue):
    queue = make_queue(authorize=lambda *args: 1)
    with pytest.raises(QueueRefusal, match="authorization_refused"):
        queue.enqueue("ncp_capabilities_list", {}, "truthiness")
    queue = make_queue()
    for arguments in ([], {"x": float("inf")}, {"x": "x"*65536}, {"x": "\ud800"}):
        with pytest.raises((ValueError, UnicodeError)):
            queue.enqueue("ncp_capabilities_list", arguments, "invalid")
    assert queue.pending_notifications() == []
