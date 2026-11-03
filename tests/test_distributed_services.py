"""Real PostgreSQL/NATS composition with canonical audited principal services."""
import asyncio
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import pytest

from ciw.audited_host import AuditedPrincipalHost
from ciw.campaign_contracts import canonical
from ciw.distributed_services import DistributedServices
from ciw.principal_policy import PrincipalOutcomeUnknown, PrincipalPolicy, PrincipalRefusal
from ciw.services import invoke
from test_campaign_audited_host import bound
from test_distributed_coordinator import runtime, active_connection

OPERATION = "ncp_domain_read_record"
ARGUMENTS = {"domain": "finance", "record_id": "record:../inert/path"}


@pytest.fixture
def composed(runtime, tmp_path):
    host, raw, policy, anchor = bound(tmp_path)
    service = DistributedServices(runtime.dsn, host=host, generation="test-" + uuid4().hex,
                                  operations=[OPERATION], lease_seconds=3)
    return service, host, raw, policy, anchor


def stored(runtime, service, job):
    with active_connection(runtime.dsn) as db:
        return db.execute("SELECT status,dispatch_intent FROM odsp_distributed_v1.jobs "
                          "WHERE queue_id=%s AND job_id=%s", (service.queue.queue_id, job["job_id"])).fetchone()


def test_real_jetstream_canonical_call_and_retained_audit(composed, runtime, monkeypatch):
    service, host, raw, _, anchor = composed
    expected = invoke(OPERATION, ARGUMENTS, host=raw)
    calls = []
    original = raw.call
    monkeypatch.setattr(raw, "call", lambda *args: (calls.append(args), original(*args))[1])
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="canonical-read")
    async def exercise():
        import nats
        connection = await nats.connect(runtime.nats_url, token=runtime.token, connect_timeout=3)
        try:
            bridge = service.bridge(connection.jetstream(), stream="canonical_" + uuid4().hex[:12], durable="reader")
            await bridge.provision()
            result = await service.cycle(bridge, "reader", timeout=1)
            assert result["status"] == "completed" and result["result"] == expected
            assert await service.cycle(bridge, "reader", timeout=0.1) is None
            assert (await bridge.js.consumer_info(bridge.stream, bridge.durable)).num_ack_pending == 0
        finally:
            await connection.close()
    asyncio.run(exercise())
    assert calls == [(OPERATION, ARGUMENTS)]
    assert service.enqueue(OPERATION, ARGUMENTS, attempt="canonical-read")["replayed"] is True
    assert service.execute_one("another-worker") is None
    assert [row["state"] for row in service.history(job["job_id"])] == ["queued", "claimed", "dispatching", "completed"]
    events = host._audit.chain.verify(anchor.read())
    assert [row["event"]["phase"] for row in events] == ["admitted", "succeeded"]
    request_ref = "sha256:" + sha256(canonical({"name": OPERATION, "arguments": ARGUMENTS})).hexdigest()
    assert all(row["event"]["request_ref"] == request_ref for row in events)
    assert "record:../inert/path" not in host._audit.chain.path.read_text()


def test_current_policy_refuses_enqueue_and_claim_without_audit(composed, runtime, monkeypatch):
    service, host, raw, policy, anchor = composed
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="policy-claim")
    policy["principals"][0]["operations"] = []
    calls = []
    monkeypatch.setattr(raw, "call", lambda *args: calls.append(args))
    with pytest.raises(PrincipalRefusal):
        service.enqueue(OPERATION, ARGUMENTS, attempt="policy-enqueue")
    with pytest.raises(PrincipalRefusal):
        service.execute_one("worker")
    assert calls == [] and host._audit.chain.verify(anchor.read()) == []
    assert stored(runtime, service, job) == ("queued", None)


def test_revocation_after_claim_refuses_before_dispatch_intent(composed, runtime, monkeypatch):
    service, host, raw, policy, anchor = composed
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="policy-intent")
    original = service.queue.begin_dispatch
    def revoke(claim):
        policy["principals"][0]["enabled"] = False
        return original(claim)
    monkeypatch.setattr(service.queue, "begin_dispatch", revoke)
    calls = []
    monkeypatch.setattr(raw, "call", lambda *args: calls.append(args))
    with pytest.raises(PrincipalRefusal):
        service.execute_one("worker")
    assert calls == [] and host._audit.chain.verify(anchor.read()) == []
    assert stored(runtime, service, job) == ("refused", None)


@pytest.mark.parametrize("at_terminal", [False, True])
def test_policy_revocation_during_audit_io_preserves_unknown_and_never_replays(composed, runtime, monkeypatch, at_terminal):
    service, host, raw, policy, anchor = composed
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="policy-audit")
    original_call, original_cas = raw.call, host._audit.compare_and_set_anchor
    calls = []
    monkeypatch.setattr(raw, "call", lambda *args: (calls.append(args), original_call(*args))[1])
    def revoke(before, after):
        result = original_cas(before, after)
        if after["sequence"] == (2 if at_terminal else 1):
            policy["principals"][0]["enabled"] = False
        return result
    monkeypatch.setattr(host._audit, "compare_and_set_anchor", revoke)
    with pytest.raises((PrincipalRefusal, PrincipalOutcomeUnknown)):
        service.execute_one("worker")
    assert len(calls) == int(at_terminal)
    assert stored(runtime, service, job)[0] == "outcome_unknown"
    records = host._audit.chain.verify(anchor.read())
    assert [row["event"]["phase"] for row in records] == ["admitted", "succeeded" if at_terminal else "refused"]
    policy["principals"][0]["enabled"] = True
    assert service.enqueue(OPERATION, ARGUMENTS, attempt="policy-audit")["status"] == "outcome_unknown"
    assert service.execute_one("later-worker") is None
    assert len(calls) == int(at_terminal)


def test_terminal_anchor_failure_retains_audit_hold_and_queue_unknown(composed, monkeypatch):
    service, host, raw, _, anchor = composed
    original_cas = host._audit.compare_and_set_anchor
    monkeypatch.setattr(host._audit, "compare_and_set_anchor",
                        lambda before, after: False if after["sequence"] == 2 else original_cas(before, after))
    calls, original = [], raw.call
    monkeypatch.setattr(raw, "call", lambda *args: (calls.append(args), original(*args))[1])
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="audit-unknown")
    with pytest.raises(PrincipalOutcomeUnknown):
        service.execute_one("worker")
    assert len(calls) == 1 and service.inspect(job["job_id"])["status"] == "outcome_unknown"
    assert anchor.read()["sequence"] == 1
    assert service.execute_one("another-worker") is None
    next_job = service.enqueue(OPERATION, ARGUMENTS, attempt="new-occurrence-held")
    with pytest.raises(PrincipalOutcomeUnknown):
        service.execute_one("another-worker")
    assert service.inspect(next_job["job_id"])["status"] == "outcome_unknown"
    assert len(calls) == 1  # Existing audit custody hold is not reset by a new queue occurrence.


def test_jetstream_redelivery_rechecks_revoked_policy_before_claim(composed, runtime, monkeypatch):
    service, host, raw, policy, anchor = composed
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="broker-revoked")
    calls = []
    original = raw.call
    monkeypatch.setattr(raw, "call", lambda *args: (calls.append(args), original(*args))[1])
    async def exercise():
        import nats
        connection = await nats.connect(runtime.nats_url, token=runtime.token, connect_timeout=3)
        try:
            bridge = service.bridge(connection.jetstream(), stream="revoked_" + uuid4().hex[:12], durable="reader", ack_wait=0.2)
            await bridge.provision()
            assert await bridge.publish_pending() == 1
            policy["principals"][0]["enabled"] = False
            with pytest.raises(PrincipalRefusal):
                await bridge.process_one("worker", service._dispatch, timeout=1)
            assert stored(runtime, service, job) == ("queued", None)
            assert calls == [] and host._audit.chain.verify(anchor.read()) == []
            policy["principals"][0]["enabled"] = True
            # This queued occurrence never reached a claim or dispatch intent;
            # fresh authorization after notification redelivery can admit it.
            result = await bridge.process_one("worker", service._dispatch, timeout=1)
            assert result["status"] == "completed"
            assert len(calls) == 1
        finally:
            await connection.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["principal", "policy", "provider", "audited_facade", "queue"])
def test_changed_host_identity_permanently_refuses_before_intent(composed, runtime, monkeypatch, change):
    service, host, raw, policy, anchor = composed
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="rebound")
    facade = host.facade
    original_policy = facade.policy
    original_queue = service.queue
    if change == "principal":
        facade.principal = "bob"
    elif change == "policy":
        facade.policy = PrincipalPolicy(lambda: policy)
    elif change == "provider":
        facade.hosts["domain_read"] = object()
    elif change == "audited_facade":
        host.facade = original_policy.facade("alice", domain_read=raw)
    else:
        service.queue = object()
    calls = []
    monkeypatch.setattr(raw, "call", lambda *args: calls.append(args))
    with pytest.raises(PrincipalRefusal):
        service.execute_one("worker")
    host.facade, facade.principal, facade.policy = facade, "alice", original_policy
    facade.hosts["domain_read"], service.queue = raw, original_queue
    with pytest.raises(PrincipalRefusal):
        service.execute_one("worker")
    assert calls == [] and host._audit.chain.verify(anchor.read()) == []
    assert stored(runtime, service, job) == ("queued", None)


def test_lease_expiry_during_audit_admission_prevents_canonical_call(composed, runtime, monkeypatch):
    service, host, raw, _, anchor = composed
    job = service.enqueue(OPERATION, ARGUMENTS, attempt="lost-after-audit")
    original_cas = host._audit.compare_and_set_anchor
    def expire(before, after):
        result = original_cas(before, after)
        if after["sequence"] == 1:
            with active_connection(runtime.dsn) as db:
                db.execute("UPDATE odsp_distributed_v1.jobs SET lease_until=clock_timestamp()-interval '1 second' "
                           "WHERE queue_id=%s AND job_id=%s", (service.queue.queue_id, job["job_id"]))
        return result
    monkeypatch.setattr(host._audit, "compare_and_set_anchor", expire)
    calls = []
    monkeypatch.setattr(raw, "call", lambda *args: calls.append(args))
    with pytest.raises(PrincipalRefusal):
        service.execute_one("worker")
    assert calls == []
    service.queue.recover()
    assert service.inspect(job["job_id"])["status"] == "outcome_unknown"
    assert service.execute_one("worker") is None
    assert [row["event"]["phase"] for row in host._audit.chain.verify(anchor.read())] == ["admitted", "refused"]


def test_canonical_mutations_need_qualified_downstream_adapter_and_refuse_before_queue(composed, runtime):
    service, host, _, _, _ = composed
    with pytest.raises(PrincipalRefusal):
        DistributedServices(runtime.dsn, host=host, generation="unqualified-mutations",
                            operations=["ncp_run_submit"])
    with pytest.raises(PrincipalRefusal):
        service.enqueue("ncp_run_submit", {}, attempt="not-fenced")
    with active_connection(runtime.dsn) as db:
        assert db.execute("SELECT count(*) FROM odsp_distributed_v1.jobs WHERE queue_id=%s",
                          (service.queue.queue_id,)).fetchone()[0] == 0
    with pytest.raises(TypeError):
        service.enqueue(OPERATION, ARGUMENTS, attempt="caller-identity", principal="bob")


def test_unaudited_facade_cannot_be_used_as_distributed_dispatch_host(composed, runtime):
    service, host, _, _, _ = composed
    assert type(host) is AuditedPrincipalHost
    with pytest.raises(PrincipalRefusal):
        DistributedServices(runtime.dsn, host=host.facade, generation="without-audit", operations=[OPERATION])
