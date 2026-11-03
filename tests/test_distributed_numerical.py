"""Real native computation held under PostgreSQL custody through publication."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ciw.agent_mcp import from_profile
from ciw.audit_chain import AuditChain
from ciw.audited_host import AuditedPrincipalHost
from ciw.control_gateway import ControlHost
from ciw.distributed_numerical import DistributedNumerical
from ciw.distributed_queue import QueueRefusal
from ciw.principal_policy import PrincipalOutcomeUnknown, PrincipalPolicy, PrincipalRefusal, owner_for
from test_builtin_numerical import NAMES
from test_builtin_numerical_mcp import profile_files
from test_campaign_audited_host import Anchor
from test_distributed_coordinator import runtime, active_connection
from test_rust_numerical import backend, binary, lapack_libraries

OPERATIONS = ["ncp_run_submit", "ncp_run_get", "ncp_artifact_read"]


@pytest.fixture
def computation(runtime, backend, tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retained"))
    profile = profile_files(tmp_path / "profile")
    document = {"schema": "ciw.principal-policy.v1", "revision": "distributed-native-test", "principals": [{
        "principal": "alice", "enabled": True,
        "operations": [{"name": name, "constraints": {}} for name in OPERATIONS],
        "scientific_operations": list(NAMES),
        "resources": ["ncp://plans/", "ncp://runs/", "ncp://artifacts/"],
        "reference_scopes": [], "handoff_recipients": []}]}
    policy = PrincipalPolicy(lambda: document)
    chain = AuditChain(tmp_path / "audit.jsonl", chain_id="audited-profile", allowed_operations=OPERATIONS)
    anchor = Anchor(tmp_path / "anchor.sqlite")
    stack = ExitStack()
    owner = stack.enter_context(from_profile(profile, numerical_backend=backend))
    control = stack.enter_context(ControlHost(owner, retained_owner=owner_for("alice")))
    template = control.template("baseline")
    plan = control.create("source", "baseline", template["graph"])
    plan = control.admit(plan["plan_id"], plan["digest"])
    facade = policy.facade("alice", control=control)
    audited = AuditedPrincipalHost(facade, chain=chain, read_anchor=anchor.read, compare_and_set_anchor=anchor.cas)
    service = DistributedNumerical(runtime.dsn, host=audited, backend=backend,
                                  generation="native-" + uuid4().hex, lease_seconds=3)
    original = {path.name: sha256(path.read_bytes()).hexdigest() for path in profile.parent.glob("*.json")}
    try:
        yield SimpleNamespace(service=service, owner=owner, control=control, document=document,
            grant=document["principals"][0], policy=policy, anchor=anchor, audited=audited,
            backend=backend, plan=plan, profile=profile, source_hashes=original, stack=stack)
    finally:
        stack.close()


def enqueue(c, attempt="all-native"):
    return c.service.enqueue(c.plan["plan_id"], c.plan["digest"], attempt=attempt)


def db_job(runtime, c, job):
    with active_connection(runtime.dsn) as db:
        return db.execute("SELECT status,fence,result,lease_until>clock_timestamp() "
                          "FROM odsp_distributed_v1.jobs WHERE queue_id=%s AND job_id=%s",
                          (c.service.queue.queue_id, job["job_id"])).fetchone()


def expire(runtime, c, job):
    with active_connection(runtime.dsn) as db:
        db.execute("UPDATE odsp_distributed_v1.jobs SET lease_until=clock_timestamp()-interval '1 second' "
                   "WHERE queue_id=%s AND job_id=%s", (c.service.queue.queue_id, job["job_id"]))


def assert_source_private(c):
    assert c.owner._attempts == {} and c.control._jobs == {}
    assert not c.owner._generated
    assert {path.name: sha256(path.read_bytes()).hexdigest() for path in c.profile.parent.glob("*.json")} == c.source_hashes


def count_native(c, monkeypatch):
    calls = []
    calculate = c.backend.calculate
    def observed(*args, **kwargs):
        calls.append(args[0])
        return calculate(*args, **kwargs)
    monkeypatch.setattr(c.backend, "calculate", observed)
    return calls


def test_real_jetstream_all_native_kernels_publish_terminal_and_audited(computation, runtime, monkeypatch):
    c = computation
    calls = count_native(c, monkeypatch)
    monkeypatch.setattr("ciw.numerical_kernels.calculate", lambda *a, **k: pytest.fail("Python numerical fallback"))
    job = enqueue(c)
    async def exercise():
        import nats
        connection = await nats.connect(runtime.nats_url, token=runtime.token, connect_timeout=3)
        try:
            bridge = c.service.bridge(connection.jetstream(), stream="native_" + uuid4().hex[:12], durable="native")
            await bridge.provision()
            result = await c.service.cycle(bridge, "native-worker", timeout=1)
            assert result["status"] == "completed", result
            assert result["result"]["run"]["state"] == "completed"
            assert len(result["result"]["results"]) == 7
            assert {row["operation_id"] for row in result["result"]["results"]} == set(NAMES)
            for row in result["result"]["results"]:
                assert row["runtime"]["provider"] == "ciw.numerical.rust"
                assert row["runtime"]["binary_sha256"] == c.backend.expected_sha256
            assert result["result"]["fence"]["number"] == result["fence"]
            assert await c.service.cycle(bridge, "native-worker", timeout=0.1) is None
            assert (await bridge.js.consumer_info(bridge.stream, bridge.durable)).num_ack_pending == 0
        finally:
            await connection.close()
    asyncio.run(exercise())
    assert set(calls) == set(NAMES) and len(calls) == 7
    assert enqueue(c)["replayed"] is True
    assert c.service.execute_one("other-worker") is None
    assert [row["state"] for row in c.service.history(job["job_id"])] == ["queued", "claimed", "dispatching", "completed"]
    events = c.audited._audit.chain.verify(c.anchor.read())
    assert [row["event"]["phase"] for row in events] == ["admitted", "succeeded"] * 3
    assert_source_private(c)


def test_compute_heartbeat_retains_claim_past_original_lease(computation, runtime, monkeypatch):
    c = computation
    entered, release = Event(), Event()
    original = c.backend.calculate
    calls = []
    def slow(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            entered.set()
            assert release.wait(15)
        return original(*args, **kwargs)
    monkeypatch.setattr(c.backend, "calculate", slow)
    job = enqueue(c, "renewed-computation")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(c.service.execute_one, "native-worker")
        try:
            assert entered.wait(15)
            time.sleep(c.service.queue.lease_seconds + 0.4)
            assert db_job(runtime, c, job)[::2] == ("dispatching", None)
            assert db_job(runtime, c, job)[3] is True
            assert c.service.queue.recover() == []
        finally:
            release.set()
        assert future.result(timeout=30)["status"] == "completed"
    assert len(calls) == 7
    assert_source_private(c)


def test_revocation_at_enqueue_claim_and_dispatch_has_no_native_execution(computation, runtime, monkeypatch):
    c = computation
    calls = count_native(c, monkeypatch)
    job = enqueue(c, "revoke-before-claim")
    scientific = c.grant["scientific_operations"]
    c.grant["scientific_operations"] = []
    with pytest.raises(PrincipalRefusal):
        enqueue(c, "revoke-before-enqueue")
    with pytest.raises(PrincipalRefusal):
        c.service.execute_one("worker")
    assert db_job(runtime, c, job)[0] == "queued" and not calls
    c.grant["scientific_operations"] = scientific
    original = c.service.queue.begin_dispatch
    def revoke(claim):
        c.grant["enabled"] = False
        return original(claim)
    monkeypatch.setattr(c.service.queue, "begin_dispatch", revoke)
    with pytest.raises(PrincipalRefusal):
        c.service.execute_one("worker")
    assert db_job(runtime, c, job)[0] == "refused" and not calls
    assert c.audited._audit.chain.verify(c.anchor.read()) == []
    assert_source_private(c)


def test_canonical_queued_worker_rechecks_scientific_grant(computation, runtime, monkeypatch):
    c = computation
    calls = count_native(c, monkeypatch)
    job = enqueue(c, "queued-revocation")
    original = ControlHost._work
    def revoke(control, run_id):
        c.grant["scientific_operations"] = []
        return original(control, run_id)
    monkeypatch.setattr(ControlHost, "_work", revoke)
    with pytest.raises((PrincipalRefusal, PrincipalOutcomeUnknown)):
        c.service.execute_one("worker")
    assert not calls
    assert db_job(runtime, c, job)[0] == "outcome_unknown"
    c.grant["scientific_operations"] = list(NAMES)
    assert enqueue(c, "queued-revocation")["status"] == "outcome_unknown"
    assert c.service.execute_one("other-worker") is None
    assert_source_private(c)


def test_revocation_after_native_return_withholds_result_and_remaining_kernels(computation, runtime, monkeypatch):
    c = computation
    calls = []
    original = c.backend.calculate
    def revoke(*args, **kwargs):
        calls.append(args[0])
        value = original(*args, **kwargs)
        c.grant["scientific_operations"] = []
        return value
    monkeypatch.setattr(c.backend, "calculate", revoke)
    job = enqueue(c, "returned-revocation")
    with pytest.raises((PrincipalRefusal, PrincipalOutcomeUnknown)):
        c.service.execute_one("worker")
    assert len(calls) == 1
    if db_job(runtime, c, job)[0] == "dispatching":
        # Revocation stops renewal. Finishing private evidence may outlast the
        # lease; recovery, not a stale worker, then records permanent unknown.
        expire(runtime, c, job)
        c.service.queue.recover()
    assert db_job(runtime, c, job)[0] == "outcome_unknown"
    assert db_job(runtime, c, job)[2] is None
    c.grant["scientific_operations"] = list(NAMES)
    assert enqueue(c, "returned-revocation")["status"] == "outcome_unknown"
    assert c.service.execute_one("other-worker") is None
    assert_source_private(c)


def test_expired_native_claim_cannot_publish_late_result_or_replay(computation, runtime, monkeypatch):
    c = computation
    calculated, release = Event(), Event()
    calls = []
    original = c.backend.calculate
    def late(*args, **kwargs):
        calls.append(args[0])
        value = original(*args, **kwargs)
        calculated.set()
        assert release.wait(15)
        return value
    monkeypatch.setattr(c.backend, "calculate", late)
    job = enqueue(c, "late-result")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(c.service.execute_one, "old-worker")
        try:
            assert calculated.wait(15)
            old_fence = db_job(runtime, c, job)[1]
            expire(runtime, c, job)
            recovered = c.service.queue.recover()
            assert recovered[0]["status"] == "outcome_unknown"
            assert recovered[0]["fence"] > old_fence
        finally:
            release.set()
        with pytest.raises((QueueRefusal, PrincipalRefusal, PrincipalOutcomeUnknown)):
            future.result(timeout=30)
    assert len(calls) == 1 and db_job(runtime, c, job)[2] is None
    assert enqueue(c, "late-result")["status"] == "outcome_unknown"
    assert c.service.execute_one("replacement-worker") is None
    assert_source_private(c)


def revoke_disclosure(grant, mode):
    if mode == "run_constraint":
        for operation in grant["operations"]:
            if operation["name"] == "ncp_run_get":
                operation["constraints"] = {"run_id": ["another-run"]}
    else:
        name = "ncp_run_get" if mode == "run_operation" else "ncp_artifact_read"
        grant["operations"] = [row for row in grant["operations"] if row["name"] != name]


@pytest.mark.parametrize("mode", ["artifact_operation", "run_operation", "run_constraint"])
def test_result_grant_revocation_at_pg_commit_refuses_publication(computation, runtime, monkeypatch, mode):
    c = computation
    calls = count_native(c, monkeypatch)
    job = enqueue(c, "publication-revocation")
    original = c.service.queue.complete
    def revoke(claim, result):
        revoke_disclosure(c.grant, mode)
        return original(claim, result)
    monkeypatch.setattr(c.service.queue, "complete", revoke)
    with pytest.raises(PrincipalRefusal):
        c.service.execute_one("worker")
    assert len(calls) == 7 and db_job(runtime, c, job)[2] is None
    # A settlement exception never overwrites possibly committed bytes. Here
    # readback proves no commit; permanent unknown follows lease recovery.
    assert db_job(runtime, c, job)[0] == "dispatching"
    expire(runtime, c, job)
    c.service.queue.recover()
    assert c.service.inspect(job["job_id"])["status"] == "outcome_unknown"
    assert c.service.execute_one("replacement-worker") is None
    assert_source_private(c)


@pytest.mark.parametrize("mode", ["artifact_operation", "run_operation", "run_constraint"])
def test_completed_results_recheck_output_grants_on_every_disclosure(computation, monkeypatch, mode):
    c = computation
    calls = count_native(c, monkeypatch)
    job = enqueue(c, "disclosure-revocation")
    completed = c.service.execute_one("worker")
    assert completed["status"] == "completed"
    saved = deepcopy(c.grant["operations"])
    revoke_disclosure(c.grant, mode)
    with pytest.raises(PrincipalRefusal):
        c.service.inspect(job["job_id"])
    with pytest.raises(PrincipalRefusal):
        enqueue(c, "disclosure-revocation")
    c.grant["operations"] = saved
    assert c.service.inspect(job["job_id"])["result"] == completed["result"]
    assert len(calls) == 7


def test_terminal_audit_failure_keeps_unknown_and_original_audit_hold(computation, runtime, monkeypatch):
    c = computation
    calls = count_native(c, monkeypatch)
    original = c.audited._audit.compare_and_set_anchor
    monkeypatch.setattr(c.audited._audit, "compare_and_set_anchor",
        lambda before, after: False if after["sequence"] == 6 else original(before, after))
    job = enqueue(c, "terminal-audit")
    with pytest.raises(PrincipalOutcomeUnknown):
        c.service.execute_one("worker")
    assert len(calls) == 7 and c.anchor.read()["sequence"] == 5
    assert db_job(runtime, c, job)[0] == "outcome_unknown" and db_job(runtime, c, job)[2] is None
    next_job = enqueue(c, "same-unresolved-audit")
    with pytest.raises(PrincipalOutcomeUnknown):
        c.service.execute_one("worker")
    assert len(calls) == 7 and db_job(runtime, c, next_job)[0] == "outcome_unknown"
    assert_source_private(c)


def test_original_source_execute_submit_and_replay_remain_sealed(computation):
    c = computation
    with pytest.raises(ValueError, match="guard"):
        c.owner.execute("source", "baseline", "bypass")
    with pytest.raises(ValueError, match="guard"):
        c.control.submit(c.plan["plan_id"], c.plan["digest"], "bypass")
    # A checkpoint cannot erase the persistent marker. Reopening without the
    # live host binding must refuse even before an operation is dispatched.
    c.stack.close()
    with from_profile(c.profile, resume=True, numerical_backend=c.backend) as reopened:
        with pytest.raises(ValueError, match="guard"):
            reopened.execute("source", "baseline", "bypass-after-restart")
        with ControlHost(reopened, resume=True, retained_owner=owner_for("alice")) as control:
            with pytest.raises(ValueError, match="guard"):
                control.submit(c.plan["plan_id"], c.plan["digest"], "bypass-after-restart")


def test_durable_aggregate_attempt_budget_survives_host_restart(computation, runtime):
    c = computation
    with ThreadPoolExecutor(4) as pool:
        jobs = list(pool.map(lambda index: enqueue(c, "budget-" + str(index)), range(4)))
    assert len({job["job_id"] for job in jobs}) == 4
    with pytest.raises(QueueRefusal, match="capacity"):
        enqueue(c, "fifth-before-restart")
    generation = c.service.queue.identity["generation"]
    c.stack.close()
    with from_profile(c.profile, resume=True, numerical_backend=c.backend) as owner:
        with ControlHost(owner, resume=True, retained_owner=owner_for("alice")) as control:
            facade = c.policy.facade("alice", control=control)
            audited = AuditedPrincipalHost(facade, chain=c.audited._audit.chain,
                read_anchor=c.anchor.read, compare_and_set_anchor=c.anchor.cas)
            reopened = DistributedNumerical(runtime.dsn, host=audited, backend=c.backend,
                                            generation=generation, lease_seconds=1)
            with pytest.raises(QueueRefusal, match="capacity"):
                reopened.enqueue(c.plan["plan_id"], c.plan["digest"], attempt="fifth-after-restart")
            assert reopened.enqueue(c.plan["plan_id"], c.plan["digest"], attempt="budget-0")["replayed"] is True
    with from_profile(c.profile, resume=True, numerical_backend=c.backend) as owner:
        with ControlHost(owner, resume=True, retained_owner=owner_for("alice")) as control:
            facade = c.policy.facade("alice", control=control)
            audited = AuditedPrincipalHost(facade, chain=c.audited._audit.chain,
                read_anchor=c.anchor.read, compare_and_set_anchor=c.anchor.cas)
            with pytest.raises(ValueError, match="binding changed"):
                DistributedNumerical(runtime.dsn, host=audited, backend=c.backend,
                                     generation="fresh-budget-not-authorized", lease_seconds=1)


def test_separate_worker_paths_share_one_durable_budget_and_execute_once(computation, runtime, tmp_path, monkeypatch):
    c = computation
    calls = count_native(c, monkeypatch)
    second = tmp_path / "other-worker"
    second.mkdir()
    for source in c.profile.parent.glob("*.json"):
        (second / source.name).write_bytes(source.read_bytes())
    with from_profile(second / c.profile.name, numerical_backend=c.backend) as owner:
        with ControlHost(owner, retained_owner=owner_for("alice")) as control:
            plan = control.create("source", "baseline", control.template("baseline")["graph"])
            control.admit(plan["plan_id"], plan["digest"])
            facade = c.policy.facade("alice", control=control)
            audited = AuditedPrincipalHost(facade, chain=c.audited._audit.chain,
                read_anchor=c.anchor.read, compare_and_set_anchor=c.anchor.cas)
            audited._audit = c.audited._audit
            remote = DistributedNumerical(runtime.dsn, host=audited, backend=c.backend,
                generation=c.service.queue.identity["generation"], lease_seconds=3)
            assert remote.queue.queue_id == c.service.queue.queue_id
            assert remote._root != c.service._root
            with ThreadPoolExecutor(2) as pool:
                jobs = list(pool.map(lambda service: service.enqueue(plan["plan_id"], plan["digest"],
                                     attempt="shared-worker-attempt"), [c.service, remote]))
            assert jobs[0]["job_id"] == jobs[1]["job_id"]
            assert sum(job["replayed"] for job in jobs) == 1
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda item: item[0].execute_one(item[1]),
                                        [(c.service, "worker-a"), (remote, "worker-b")]))
            assert sum(result is not None for result in results) == 1
            assert next(result for result in results if result is not None)["status"] == "completed"
            assert len(calls) == 7
            assert remote.inspect(jobs[0]["job_id"])["status"] == "completed"
            for index in range(3):
                remote.enqueue(plan["plan_id"], plan["digest"], attempt="remaining-" + str(index))
            for service in (c.service, remote):
                with pytest.raises(QueueRefusal, match="capacity"):
                    service.enqueue(plan["plan_id"], plan["digest"], attempt="fresh-path-cannot-reset-budget")
            assert not owner._attempts and not owner._generated and not control._jobs
    assert_source_private(c)


def test_guard_loss_blocks_reused_execute_and_replay_and_restored_execution(backend, tmp_path, monkeypatch):
    profile = profile_files(tmp_path / "guarded-profile")
    allowed = [True]
    calls, original = [], backend.calculate
    def observed(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(backend, "calculate", observed)
    with from_profile(profile, numerical_backend=backend) as owner:
        owner.bind_execution_guard(lambda: allowed[0], binding="b" * 64)
        assert owner.execute("source", "baseline", "first")["status"] == "completed"
        assert len(calls) == 7
        allowed[0] = False
        with pytest.raises(ValueError, match="guard"):
            owner.execute("source", "baseline", "first")
        with pytest.raises(ValueError, match="guard"):
            owner.replay("first", "second")
        assert len(calls) == 7
    with from_profile(profile, resume=True, numerical_backend=backend) as owner:
        with pytest.raises(ValueError, match="guard"):
            owner.execute("source", "baseline", "first")
        with pytest.raises(ValueError, match="guard"):
            owner.replay("first", "second")
        with pytest.raises(ValueError, match="owner already used"):
            owner.bind_execution_guard(lambda: True, binding="b" * 64)
    assert len(calls) == 7


@pytest.mark.parametrize("value", [None, False, 1, "yes"])
def test_host_execution_guard_requires_exact_true_and_is_not_resettable(backend, tmp_path, value):
    profile = profile_files(tmp_path / "profile")
    with from_profile(profile, numerical_backend=backend) as owner:
        owner.bind_execution_guard(lambda: value, binding="a" * 64)
        with pytest.raises(ValueError, match="guard"):
            owner.execute("source", "baseline", "no-truthy-authority")
        with pytest.raises(ValueError, match="already bound"):
            owner.bind_execution_guard(lambda: True, binding="a" * 64)
        assert owner._attempts == {} and not owner._generated


def test_observed_source_rebinding_permanently_refuses(computation, runtime, monkeypatch):
    c = computation
    job = enqueue(c, "rebind")
    original = c.owner._maximum
    c.owner._maximum += 1
    with pytest.raises(PrincipalRefusal):
        c.service.execute_one("worker")
    c.owner._maximum = original
    with pytest.raises(PrincipalRefusal):
        c.service.execute_one("worker")
    assert db_job(runtime, c, job)[0] == "queued"
