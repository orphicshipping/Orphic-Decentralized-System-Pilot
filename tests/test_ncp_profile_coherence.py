"""Real retained engineering output can be cited without promoting its authority.

All catalog identities and host decisions in this test are synthetic fixtures.
The production, evidence and task stores are their actual implementations.
"""
import hashlib
import json

import pytest

from ciw import engineering_thread as engineering
from ciw import engineering_thread_io as thread
from ciw import link_contract as contract
from ciw.external_work import ExternalWorkStore
from ciw.external_work_mcp import ExternalWorkHost
from ciw.knowledge_mcp import KnowledgeHost, KnowledgeRefusal
from ciw.link_study_cli import example
from ciw.session import Session
from test_evidence_corpus import build


def encode(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_cited_work_review_cannot_accept_or_reexecute_retained_engineering(tmp_path, monkeypatch):
    request = encode(example())
    intent = encode(engineering.example_intent("scalar", contract.bytes_ref(request)))
    compiled = thread.compile_packet(intent, request, tmp_path / "compiled", host_profile="scalar")
    retained = tmp_path / "retained"
    review = thread.run_packet(tmp_path / "compiled", retained, host_profile="scalar",
                              expected_compiled_ref=compiled["compiled_ref"], allow_compute=True)
    assert review["status"] == "EVIDENCE_REQUIRED"
    before = snapshot(retained)

    # Freeze the complete actual review bytes, then select its exact status passage.
    evidence_root = tmp_path / "cited"
    evidence_root.mkdir()
    corpus, catalog, _, original, _, _ = build(evidence_root, encode(review), "review.json")
    span_id = corpus.search("EVIDENCE_REQUIRED")["items"][0]["span_id"]
    span = corpus.span(span_id)["span"]
    assert "EVIDENCE_REQUIRED" in span["text"]
    store = ExternalWorkStore(tmp_path / "work.sqlite3", clock=lambda: 1000)
    parent = review["thread_ref"]
    for principal, role in (("requester", "requester"), ("worker", "worker")):
        store.provision_client(principal, role=role, parent_ref=parent)
    context = corpus.bind_context(store, span_id=span_id, parent_ref=parent,
                                  disclosure_ref="fixture:explicit-host-disclosure")
    assert context["authorizes_execution"] is False
    requester = KnowledgeHost(catalog, ExternalWorkHost(store, principal="requester"),
                              parent, evidence=corpus)
    worker = KnowledgeHost(catalog, ExternalWorkHost(store, principal="worker"), parent,
                           evidence=corpus)
    proposal = requester.call("ncp_work_propose", {"request_key": "review-status", "task": {
        "parent_ref": parent, "instruction": "Quote the retained status without promoting it.",
        "expected_output": "A cited candidate that preserves qualification limits.",
        "context_ids": [context["context_id"]],
    }})
    assert proposal["status"] == "ok"
    task = proposal["data"]
    claim = {"task_id": task["task_id"], "expected_hash": task["task_hash"],
             "attempt_id": "attempt-1", "bot_label": "synthetic-untrusted-attribution"}
    assert worker.call("ncp_work_claim", claim)["status"] == "refused"
    for name in ("ncp_work_authorize", "ncp_work_review", "net_execute", "ncp_evidence_bind_context"):
        assert worker.call(name, {})["reason"] == "unknown_tool"

    store.authorize(task["task_id"], expected_hash=task["task_hash"],
                    approval_ref="fixture:host-authorized-quotation-only", worker_id="worker",
                    expires_at=2000, lease_seconds=60)
    assert worker.call("ncp_work_claim", claim)["status"] == "ok"
    args = {"task_id": task["task_id"], "context_id": context["context_id"]}
    selected = worker.call("ncp_work_context", args)
    assert selected == requester.call("ncp_work_context", args)
    assert selected["data"]["text"] == span["text"]
    assert selected["data"]["excerpt_hash"] == contract.bytes_ref(span["text"].encode())

    def forbidden(*args, **kwargs):
        raise AssertionError("Evidence/task inspection dispatched scientific work")
    monkeypatch.setattr(Session, "handle", forbidden)
    submission = worker.call("ncp_work_submit", {key: claim[key] for key in
        ("task_id", "expected_hash", "attempt_id")} | {"result": {
            "outcome": "completed", "candidate": "The retained status is EVIDENCE_REQUIRED.",
            "citations": [{"context_id": context["context_id"], "quote": "EVIDENCE_REQUIRED"}],
            "limitations": ["Synthetic workflow; quotation is not physical qualification."],
        }})
    assert submission["status"] == "ok"
    assert submission["data"]["semantic_status"] == "unreviewed"
    assert submission["data"]["verification_id"] is None
    store.record_review(task["task_id"], result_id=submission["data"]["result_id"],
                        decision="accepted", reviewer_ref="fixture:host-reviewer",
                        scope="Quotation accuracy only", findings="Exact status substring checked.")
    reopened = thread.inspect_thread(retained, expected_thread_ref=parent, host_profile="scalar")
    assert reopened == review
    assert snapshot(retained) == before
    assert original.read_bytes() == encode(review)

    uri = worker.evidence_uri + "/span/" + span_id[7:]
    assert json.loads(worker.read_resource(uri))["span"]["text"] == span["text"]
    store.revoke_client("worker")
    with pytest.raises(KnowledgeRefusal, match="client_refused"):
        worker.read_resource(uri)
    assert worker.call("ncp_work_read", {"task_id": task["task_id"]})["reason"] == "client_refused"
    assert thread.inspect_thread(retained, expected_thread_ref=parent, host_profile="scalar") == review
