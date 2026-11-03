"""Bounded opt-in custody, policy and full-request binding tests."""
from copy import copy, deepcopy
from dataclasses import replace
import json
import math
import os
from pathlib import Path

import pytest

from ciw import store_queue
from ciw.store_queue import Claim, QueueLimits, QueueRefusal, StoreQueue


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="StoreQueue qualification requires POSIX no-follow kernel leases",
)


def queue(tmp_path, **overrides):
    options = dict(owner="host-a", principal="principal-a", generation="generation-a",
                   operations=["operation.one", "operation.two"], authorize=lambda *args: None)
    options.update(overrides)
    return StoreQueue(tmp_path / "queue", **options)


def test_same_attempt_replays_full_binding_after_reopen(tmp_path):
    first = queue(tmp_path)
    job = first.enqueue("operation.one", {"a": [1, "two"]}, "attempt-a")
    second = queue(tmp_path)
    replay = second.enqueue("operation.one", {"a": [1, "two"]}, "attempt-a")
    assert replay == {**job, "replayed": True}
    assert job["authorizes_execution"] is False
    assert "arguments" not in job
    for operation, arguments in (("operation.two", {"a": [1, "two"]}),
                                 ("operation.one", {"a": [1, "changed"]})):
        with pytest.raises(QueueRefusal, match="attempt_conflict"):
            second.enqueue(operation, arguments, "attempt-a")
    with second.claim_next("worker-a") as claim:
        result = second.complete(claim, {"synthetic": True})
    assert result["status"] == "completed"
    assert queue(tmp_path).execute_one("worker-b", lambda *args: pytest.fail("re-executed")) is None
    assert queue(tmp_path).enqueue("operation.one", {"a": [1, "two"]}, "attempt-a")["result"] == {"synthetic": True}


@pytest.mark.parametrize("field", ["owner", "principal", "generation"])
def test_host_principal_and_generation_cannot_reopen_someone_elses_queue(tmp_path, field):
    original = queue(tmp_path)
    original.enqueue("operation.one", {}, "attempt-a")
    with pytest.raises(QueueRefusal, match="binding_mismatch"):
        queue(tmp_path, **{field: "different"})
    assert original.claim_next("worker-a") is not None
    for claim in list(original._claims.values()):
        original.abandon(claim)


def test_fifo_concurrency_fences_and_context_abandonment(tmp_path):
    q = queue(tmp_path, limits=QueueLimits(max_running=2))
    jobs = [q.enqueue("operation.one", {"index": index}, f"attempt-{index}") for index in range(3)]
    a, b = q.claim_next("worker-a"), q.claim_next("worker-b")
    assert [a.job["job_id"], b.job["job_id"]] == [job["job_id"] for job in jobs[:2]]
    assert a.fence < b.fence
    assert q.claim_next("worker-c") is None
    assert q.recover()["jobs"] == []
    q.complete(a, {"value": 1})
    with q.claim_next("worker-c") as c:
        assert c.job["job_id"] == jobs[2]["job_id"]
        assert c.fence > b.fence
    q.complete(b, {"value": 2})
    assert q.inspect(jobs[2]["job_id"])["status"] == "outcome_unknown"
    assert q.execute_one("worker-d", lambda *args: pytest.fail("re-executed")) is None


def test_foreign_copied_released_claims_do_not_release_real_lease(tmp_path):
    q = queue(tmp_path)
    job = q.enqueue("operation.one", {}, "attempt")
    claim = q.claim_next("worker")
    wrong_queue = queue(tmp_path)
    for holder, candidate in ((wrong_queue, claim), (q, copy(claim)),
                              (q, Claim(q, claim.job, claim._lease))):
        with pytest.raises(QueueRefusal, match="not_owned"):
            holder.complete(candidate, {})
        assert q.recover()["jobs"] == []
    altered_copy = claim.job
    altered_copy["fence"] = 0
    assert claim.fence > 0
    q.complete(claim, {"value": 1})
    with pytest.raises(QueueRefusal, match="not_owned"):
        q.complete(claim, {"value": 2})
    assert q.inspect(job["job_id"])["result"] == {"value": 1}


def test_revocation_reduced_operations_and_false_decision_refuse_without_dispatch(tmp_path):
    allowed, checks, calls = True, [], []
    def authorize(principal, operation, arguments):
        checks.append((principal, operation, deepcopy(arguments)))
        if not allowed:
            raise QueueRefusal("policy_revoked")
    q = queue(tmp_path, authorize=authorize)
    job = q.enqueue("operation.one", {"input": 1}, "attempt")
    allowed = False
    for action in (lambda: q.claim_next("worker"), lambda: q.inspect(job["job_id"]),
                   lambda: q.enqueue("operation.one", {"input": 1}, "attempt")):
        with pytest.raises(QueueRefusal, match="policy_revoked"):
            action()
    restricted = queue(tmp_path, operations=["operation.two"])
    with pytest.raises(QueueRefusal, match="operation_refused"):
        restricted.execute_one("worker", lambda *args: calls.append(args))
    false_policy = queue(tmp_path, authorize=lambda *args: False)
    with pytest.raises(QueueRefusal, match="authorization_refused"):
        false_policy.claim_next("worker")
    assert calls == []
    assert all(row[0] == "principal-a" for row in checks)
    allowed = True
    assert q.execute_one("worker", lambda *args: {"done": True})["status"] == "completed"


def test_reauthorization_immediately_before_dispatch(tmp_path):
    count, calls = 0, []
    def authorize(*args):
        nonlocal count
        count += 1
        # Enqueue, claim, then current authorization at execution boundary.
        if count == 3:
            raise QueueRefusal("revoked_at_dispatch")
    q = queue(tmp_path, authorize=authorize)
    job = q.enqueue("operation.one", {}, "attempt")
    with pytest.raises(QueueRefusal, match="revoked_at_dispatch"):
        q.execute_one("worker", lambda *args: calls.append(args))
    assert calls == [] and count == 3
    assert q.inspect(job["job_id"])["status"] == "refused"
    assert q.recover()["jobs"] == []
    assert q.execute_one("worker", lambda *args: pytest.fail("refusal retried")) is None


@pytest.mark.parametrize("error", [ValueError("dispatch failed"), KeyboardInterrupt("interrupted"), SystemExit(4)])
def test_dispatch_error_keeps_possible_effects_unknown_and_never_reexecutes(tmp_path, error):
    q, calls = queue(tmp_path), []
    job = q.enqueue("operation.one", {"a": 1}, "attempt")
    def dispatch(operation, arguments, fence):
        calls.append((operation, arguments, fence))
        raise error
    with pytest.raises(type(error)) as raised:
        q.execute_one("worker", dispatch)
    assert raised.value is error and len(calls) == 1
    result = q.inspect(job["job_id"])
    assert result["status"] == "outcome_unknown" and result["error"] == "dispatch_interrupted"
    assert q.recover()["jobs"] == []
    assert q.execute_one("worker", dispatch) is None


@pytest.mark.parametrize("result", [None, [], "not a record", {"a": "x" * 65536}])
def test_invalid_post_execution_result_releases_claim_and_recovers_unknown(tmp_path, result):
    q = queue(tmp_path)
    job = q.enqueue("operation.one", {}, "attempt")
    with pytest.raises(ValueError):
        q.execute_one("worker", lambda *args: result)
    assert q.inspect(job["job_id"])["status"] == "running"
    recovered = q.recover()["jobs"]
    assert len(recovered) == 1 and recovered[0]["status"] == "outcome_unknown"
    assert q.recover()["jobs"] == []


def test_capacity_reserves_results_and_keeps_terminal_attempts(tmp_path):
    limits = QueueLimits(max_jobs=2, max_pending=1, max_result_bytes=128, max_state_bytes=10000)
    q = queue(tmp_path, limits=limits)
    one = q.enqueue("operation.one", {}, "one")
    with pytest.raises(QueueRefusal, match="capacity_exhausted"):
        q.enqueue("operation.one", {}, "two")
    q.complete(q.claim_next("worker"), {"body": "x" * 100})
    two = q.enqueue("operation.one", {}, "two")
    q.complete(q.claim_next("worker"), {})
    with pytest.raises(QueueRefusal, match="capacity_exhausted"):
        q.enqueue("operation.one", {}, "three")
    assert q.inspect(one["job_id"])["status"] == q.inspect(two["job_id"])["status"] == "completed"
    assert len(list(q.directory.glob("*.lease"))) == 2
    state = json.loads((q.directory / "queue.json").read_bytes())
    assert sum(j["reserved_bytes"] for j in state["jobs"]) + 2048 <= limits.max_state_bytes


def test_reservation_refuses_before_publishing_even_small_requests(tmp_path):
    q = queue(tmp_path, limits=QueueLimits(max_result_bytes=128, max_state_bytes=4096))
    before = (q.directory / "queue.json").read_bytes()
    with pytest.raises(QueueRefusal, match="capacity_exhausted"):
        q.enqueue("operation.one", {}, "attempt")
    assert (q.directory / "queue.json").read_bytes() == before


@pytest.mark.parametrize("arguments", [[], {"bad": math.inf}, {"bad": math.nan},
                                       {"bad": "\ud800"}, {"large": 10**200},
                                       {"large": "snowman \u2603" * 9000}])
def test_argument_refusals_happen_before_authorization_and_publication(tmp_path, arguments):
    checks = []
    q = queue(tmp_path, authorize=lambda *args: checks.append(args))
    before = (q.directory / "queue.json").read_bytes()
    with pytest.raises(ValueError):
        q.enqueue("operation.one", arguments, "attempt")
    assert checks == [] and (q.directory / "queue.json").read_bytes() == before


def test_cyclic_and_deep_input_are_bounded(tmp_path):
    q = queue(tmp_path)
    cyclic = {}; cyclic["self"] = cyclic
    deep = {}; cursor = deep
    for _ in range(30):
        cursor["next"] = {}; cursor = cursor["next"]
    for arguments in (cyclic, deep):
        with pytest.raises(QueueRefusal, match="complexity"):
            q.enqueue("operation.one", arguments, "attempt")


@pytest.mark.parametrize("override", [{"max_running": True}, {"max_pending": 0},
                                     {"lock_timeout": math.inf}, {"max_jobs": 1025}])
def test_invalid_limits_refuse(tmp_path, override):
    with pytest.raises(QueueRefusal, match="limit_invalid"):
        queue(tmp_path, limits=replace(QueueLimits(), **override))
    with pytest.raises(QueueRefusal, match="limit_invalid"):
        queue(tmp_path, limits=False)


def test_limits_cannot_change_under_existing_history(tmp_path):
    queue(tmp_path)
    with pytest.raises(QueueRefusal, match="binding_mismatch"):
        queue(tmp_path, limits=QueueLimits(max_running=2))


def test_path_record_and_history_safety(tmp_path):
    q = queue(tmp_path)
    job = q.enqueue("operation.one", {}, "attempt")
    q.abandon(q.claim_next("worker"))
    record = q.directory / "queue.json"
    original = record.read_bytes()
    target = tmp_path / "target.json"
    target.write_bytes(original)
    record.unlink(); record.symlink_to(target)
    with pytest.raises(ValueError):
        queue(tmp_path)
    assert target.read_bytes() == original
    record.unlink()
    with pytest.raises(QueueRefusal, match="history_missing"):
        queue(tmp_path)
    assert not record.exists()
    record.write_bytes(original)
    link = tmp_path / "link"
    link.symlink_to(q.directory, target_is_directory=True)
    with pytest.raises(QueueRefusal, match="path_invalid"):
        StoreQueue(link, **dict(owner="host-a", principal="principal-a", generation="generation-a",
                               operations=["operation.one"], authorize=lambda *args: None))
    assert q.inspect(job["job_id"])["status"] == "outcome_unknown"


@pytest.mark.parametrize("mutation", ["duplicate", "status", "request", "fence", "extra"])
def test_corrupt_persisted_records_fail_closed(tmp_path, mutation):
    q = queue(tmp_path)
    q.enqueue("operation.one", {}, "attempt")
    path = q.directory / "queue.json"
    raw = path.read_bytes(); state = json.loads(raw)
    if mutation == "duplicate":
        raw = raw.replace(b'"next_fence":0', b'"next_fence":0,"next_fence":0')
    else:
        if mutation == "status": state["jobs"][0]["status"] = []
        if mutation == "request": state["jobs"][0]["arguments"] = {"forged": True}
        if mutation == "fence": state["jobs"][0]["fence"] = True
        if mutation == "extra": state["jobs"][0]["grant"] = True
        raw = store_queue._wire(state, q.limits.max_state_bytes)
    path.write_bytes(raw)
    with pytest.raises(QueueRefusal):
        q.claim_next("worker")
    assert path.read_bytes() == raw


def test_current_host_dispatch_and_argument_copies(tmp_path):
    checks, dispatched = [], []
    def authorize(principal, operation, arguments):
        checks.append((principal, operation, arguments.copy()))
        arguments["untrusted_callback_mutation"] = True
    q = queue(tmp_path, authorize=authorize)
    original = {"path_like_data": "../../inert-input"}
    q.enqueue("operation.one", original, "attempt")
    def dispatch(operation, arguments, fence):
        dispatched.append((operation, arguments.copy(), fence))
        arguments["changed"] = True
        return {"synthetic": True, "fence": fence}
    result = q.execute_one("worker", dispatch)
    assert dispatched == [("operation.one", original, 1)]
    assert all(row[2] == original for row in checks)
    assert result["result"] == {"synthetic": True, "fence": 1}
    assert result["authorizes_execution"] is False


def test_cancellation_at_final_authorization_releases_admitted_lease_without_execution(tmp_path):
    calls, checks = [], []
    def authorize(*args):
        checks.append(args)
        if len(checks) == 3:
            raise KeyboardInterrupt("cancelled at current policy check")
    q = queue(tmp_path, authorize=authorize)
    job = q.enqueue("operation.one", {}, "attempt")
    with pytest.raises(KeyboardInterrupt):
        q.execute_one("worker", lambda *args: calls.append(args))
    assert calls == [] and q._claims == {}
    assert q.recover()["jobs"][0]["status"] == "outcome_unknown"
    assert q.inspect(job["job_id"])["status"] == "outcome_unknown"


def test_host_dispatch_can_reuse_existing_coordinator_without_changing_its_format(tmp_path, monkeypatch):
    from ciw import coordinator, retention, services
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "legacy-retained"))
    store = retention.Store("existing-owner")
    executed, permissions = [], []
    def synthetic_domain(**arguments):
        executed.append(arguments)
        return {"example": True, "value": 7}
    monkeypatch.setattr(services, "domain_run", synthetic_domain)
    def authorize(principal, operation, arguments):
        permissions.append((principal, operation, arguments))
        if operation != "ncp_domain_run" or arguments != {"domain": "sequence", "example": True}:
            raise QueueRefusal("synthetic_scope_refused")
    q = queue(tmp_path, operations=["ncp_domain_run"], authorize=authorize)
    job = q.enqueue("ncp_domain_run", {"domain": "sequence", "example": True}, "attempt")
    def host_dispatch(operation, arguments, fence):
        # Fixed host-owned occurrence binding; fence is evidence, never a grant.
        return coordinator.submit(store, operation, arguments, "queue-attempt")
    result = q.execute_one("worker", host_dispatch)
    assert result["status"] == "completed" and result["result"]["status"] == "completed"
    assert len(executed) == 1 and len(permissions) == 3
    old = coordinator.submit(store, "ncp_domain_run", {"domain": "sequence", "example": True}, "queue-attempt")
    assert old["replayed"] is True and len(executed) == 1
    assert old["schema"] == "ncp.coordinator-job.v1"
    assert q.inspect(job["job_id"])["result"]["schema"] == old["schema"]
    assert q.execute_one("worker", host_dispatch) is None


@pytest.mark.parametrize("mutation", ["refused_worker_lost", "unknown_authorization", "boolean_limit"])
def test_terminal_reason_and_persisted_integer_types_are_not_interchangeable(tmp_path, mutation):
    q = queue(tmp_path)
    job = q.enqueue("operation.one", {}, "attempt")
    q.abandon(q.claim_next("worker"))
    path = q.directory / "queue.json"
    state = json.loads(path.read_bytes())
    if mutation == "refused_worker_lost":
        state["jobs"][0].update(status="refused", error="worker_lost")
    elif mutation == "unknown_authorization":
        state["jobs"][0]["error"] = "authorization_refused"
    else:
        state["limits"]["max_running"] = True
    path.write_bytes(store_queue._wire(state, q.limits.max_state_bytes))
    with pytest.raises(QueueRefusal, match="record_invalid"):
        q.inspect(job["job_id"])


def test_all_admitted_structurally_large_results_fit_reserved_queue_capacity(tmp_path):
    q = queue(tmp_path)
    result = {"samples": [0] * 10000}
    jobs = [q.enqueue("operation.one", {"index": i}, f"attempt-{i}") for i in range(4)]
    calls = []
    def dispatch(operation, arguments, fence):
        calls.append(arguments["index"])
        return result
    for i in range(4):
        assert q.execute_one("worker", dispatch)["status"] == "completed"
        assert calls == list(range(i + 1))
    reopened = queue(tmp_path)
    for job in jobs:
        assert reopened.inspect(job["job_id"])["result"] == result
    assert reopened.execute_one("worker", dispatch) is None
    assert calls == [0, 1, 2, 3]
    assert len((q.directory / "queue.json").read_bytes()) < q.limits.max_state_bytes


def _nested(depth):
    value = 0
    for _ in range(depth):
        value = {"nested": value}
    return value


def test_request_result_and_queue_response_depth_account_for_owned_envelopes(tmp_path):
    q = queue(tmp_path)
    # The full bound request has one outer level; the result has none.
    arguments, result = _nested(23), _nested(24)
    store_queue._wire(result, q.limits.max_result_bytes)
    job = q.enqueue("operation.one", arguments, "attempt")
    received = []
    def dispatch(operation, args, fence):
        received.append(args)
        return result
    completed = q.execute_one("worker", dispatch)
    assert completed["status"] == "completed" and completed["result"] == result
    assert received == [arguments]
    assert queue(tmp_path).inspect(job["job_id"])["result"] == result
    with pytest.raises(QueueRefusal, match="complexity"):
        q.enqueue("operation.one", _nested(24), "too-deep-request")
    with pytest.raises(QueueRefusal, match="complexity"):
        store_queue._wire(_nested(25), q.limits.max_result_bytes)


def test_result_reservation_exhaustion_refuses_before_any_dispatch(tmp_path):
    # Four maximum results cannot fit, so only the first three are admitted.
    q = queue(tmp_path, limits=QueueLimits(max_state_bytes=220000))
    for index in range(3):
        q.enqueue("operation.one", {"index": index}, f"attempt-{index}")
    calls = []
    with pytest.raises(QueueRefusal, match="capacity_exhausted"):
        q.enqueue("operation.one", {"index": 3}, "attempt-3")
    assert calls == []
    for _ in range(3):
        assert q.execute_one("worker", lambda operation, args, fence:
                             (calls.append(args["index"]) or {"samples": [0] * 10000}))["status"] == "completed"
    assert calls == [0, 1, 2]
