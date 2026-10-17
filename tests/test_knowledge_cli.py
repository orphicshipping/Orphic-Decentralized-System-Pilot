"""Operator CLI and metadata-context boundaries; no production archive fixture."""
from hashlib import sha256
import json
from pathlib import Path
import time

import pytest

from ciw import external_work_cli, knowledge_cli
from ciw.external_work import ExternalWorkStore
from ciw.external_work_mcp import ExternalWorkHost


class Catalog:
    ref = "sha256:" + "a" * 64

    def __init__(self, source_hash="b" * 64):
        self.payload = {
            "schema": "ciw.proto-corpus-source.v1", "source_id": "gdrive:fixture-source",
            "title": "Fixture metadata", "source_sha256": source_hash,
            "representation_sha256": source_hash, "sha256_scope": "original_stored_file_bytes",
            "representation": "original_bytes", "category": "TEST", "kind": "document",
            "source_url": "https://example.invalid/source", "metadata_only": True,
            "authorizes_execution": False, "scientific_adjudication": "not_performed",
        }
        self.raw = json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()
        self.record_id = "sha256:" + sha256(self.raw).hexdigest()

    def source(self, record_id):
        if record_id != self.record_id:
            raise ValueError("missing record")
        return {"record": {**self.payload, "record_id": self.record_id}}

    def record_bytes(self, record_id):
        self.source(record_id)
        return self.raw

    def overview(self):
        return {"metadata_only": True, "counts": {"source_records": 1}}


def provision(tmp_path):
    store = ExternalWorkStore(tmp_path / "work.sqlite3")
    for role in ("requester", "worker"):
        store.provision_client(role, role=role, parent_ref="intent:test")
    return store


def test_metadata_binding_keeps_three_identity_roles_distinct(tmp_path):
    corpus = Catalog()
    store = provision(tmp_path)
    bound = knowledge_cli.bind_metadata(store, corpus, record_id=corpus.record_id,
        parent_ref="intent:test", disclosure_ref="fixture:metadata-only")
    assert bound["metadata_hash"] == corpus.record_id
    task = store.propose("requester", request_key="one", task={"parent_ref": "intent:test",
        "instruction": "Review metadata", "expected_output": "Candidate",
        "context_ids": [bound["context_id"]]})
    context = store.context("requester", task_id=task["task_id"], context_id=bound["context_id"])
    assert context["source_id"] != corpus.payload["source_id"]
    assert context["source_id"].startswith("urn:notation:proto-corpus:metadata:")
    assert context["snapshot_ref"] == corpus.ref
    assert context["source_hash"] == corpus.record_id
    assert context["source_hash"] != "sha256:" + corpus.payload["source_sha256"]
    assert context["text"].encode() == corpus.raw
    assert context["provenance"]["original_source_id"] == corpus.payload["source_id"]
    assert context["provenance"]["original_representation_sha256"] == corpus.payload["source_sha256"]
    assert context["provenance"]["metadata_only"] is True
    assert task["authorization"] is None and task["review"] is None
    assert store.discover("worker")["items"] == []


def test_metadata_binding_retains_unknown_original_digest(tmp_path):
    corpus = Catalog(None)
    store = provision(tmp_path)
    bound = knowledge_cli.bind_metadata(store, corpus, record_id=corpus.record_id,
        parent_ref="intent:test", disclosure_ref="fixture:metadata-only")
    assert bound["source_sha256"] is None
    assert bound["metadata_hash"] == corpus.record_id


def test_oversized_metadata_never_creates_context(tmp_path):
    corpus = Catalog()
    corpus.raw = b"x" * 8193
    store = provision(tmp_path)
    with pytest.raises(ValueError, match="budget"):
        knowledge_cli.bind_metadata(store, corpus, record_id=corpus.record_id,
                                   parent_ref="intent:test", disclosure_ref="fixture")
    with store._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM contexts").fetchone()[0] == 0


def test_cli_config_binds_exact_principal_pin_and_parent(tmp_path, monkeypatch, capsys):
    corpus = Catalog()
    monkeypatch.setattr(knowledge_cli.ProtoCorpus, "open", lambda *a, **k: corpus)
    store = provision(tmp_path)
    target = tmp_path / "mcp.json"
    args = ["config", "--bundle", str(tmp_path / "bundle"), "--expected-ref", corpus.ref,
            "--database", str(store.path), "--principal", "requester", "--parent-ref",
            "intent:test", "--output", str(target)]
    assert knowledge_cli.main(args) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["activated"] is False and status["authorizes_execution"] is False
    config = json.loads(target.read_text())["mcpServers"]["ncp-knowledge"]
    assert corpus.ref in config["args"] and "requester" in config["args"]
    assert "intent:test" in config["args"] and config["command"]
    raw = target.read_bytes()
    assert knowledge_cli.main(args) == 2
    assert target.read_bytes() == raw
    capsys.readouterr()
    args[-1] = str(tmp_path / "bad-scope.json")
    args[args.index("--parent-ref") + 1] = "intent:other"
    assert knowledge_cli.main(args) == 2
    assert not (tmp_path / "bad-scope.json").exists()


def test_cli_config_refuses_partial_work_binding(tmp_path, monkeypatch, capsys):
    corpus = Catalog()
    monkeypatch.setattr(knowledge_cli.ProtoCorpus, "open", lambda *a, **k: corpus)
    target = tmp_path / "mcp.json"
    assert knowledge_cli.main(["config", "--bundle", str(tmp_path), "--expected-ref", corpus.ref,
                              "--principal", "requester", "--output", str(target)]) == 2
    assert not target.exists()


def test_init_is_exclusive_and_unknown_revocation_refuses(tmp_path, capsys):
    path = tmp_path / "host.sqlite3"
    assert external_work_cli.main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    original = path.read_bytes()
    assert external_work_cli.main(["init", "--database", str(path)]) == 2
    assert path.read_bytes() == original
    capsys.readouterr()
    for command, flag, value in [
        ("revoke-client", "--client-id", "never-provisioned"),
        ("revoke-context", "--context-id", "sha256:" + "f" * 64),
    ]:
        assert external_work_cli.main([command, "--database", str(path), flag, value]) == 2
        assert json.loads(capsys.readouterr().err)["status"] == "refused"


def test_known_revocation_is_idempotent_and_blocks_scope(tmp_path, capsys):
    store = provision(tmp_path)
    args = ["revoke-client", "--database", str(store.path), "--client-id", "worker"]
    for _ in range(2):
        assert external_work_cli.main(args) == 0
        assert json.loads(capsys.readouterr().out)["data"]["revocation_recorded"] is True
    response = ExternalWorkHost(store, principal="worker").call("ncp_work_discover", {})
    assert response["reason"] == "client_refused"


def test_host_authorization_and_review_remain_separate(tmp_path, capsys):
    corpus = Catalog()
    store = provision(tmp_path)
    bound = knowledge_cli.bind_metadata(store, corpus, record_id=corpus.record_id,
        parent_ref="intent:test", disclosure_ref="fixture")
    task = store.propose("requester", request_key="one", task={"parent_ref": "intent:test",
        "instruction": "Inspect fixture metadata", "expected_output": "Candidate",
        "context_ids": [bound["context_id"]]})
    args = ["authorize", "--database", str(store.path), "--task-id", task["task_id"],
            "--expected-hash", "sha256:" + "f" * 64, "--approval-ref", "fixture:grant",
            "--worker-id", "worker", "--expires-at", str(time.time() + 300)]
    assert external_work_cli.main(args) == 2
    capsys.readouterr()
    args[args.index("--expected-hash") + 1] = task["task_hash"]
    assert external_work_cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["data"]["execution_status"] == "authorized"
    store.claim("worker", task_id=task["task_id"], expected_hash=task["task_hash"],
                attempt_id="fixture-attempt", bot_label="unverified fixture label")
    submitted = store.submit("worker", task_id=task["task_id"], expected_hash=task["task_hash"],
        attempt_id="fixture-attempt", result={"outcome": "completed", "candidate": "Metadata only.",
        "citations": [{"context_id": bound["context_id"], "quote": "Fixture metadata"}],
        "limitations": ["Fixture; no scientific acceptance."]})
    assert submitted["semantic_status"] == "unreviewed"
    assert store.read("requester", task_id=task["task_id"])["review"] is None
    assert external_work_cli.main(["review", "--database", str(store.path),
        "--task-id", task["task_id"], "--result-id", submitted["result_id"],
        "--decision", "needs_review", "--reviewer-ref", "fixture:reviewer",
        "--scope", "metadata", "--findings", "Unresolved applicability"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["decision"] == "needs_review"
    assert submitted["verification_id"] is None
