"""Real compiled evidence CLI/profile integration with synthetic sources."""
import json

from ciw import evidence_cli, knowledge_cli, net
from ciw.evidence_corpus import EvidenceCorpus
from ciw.external_work import ExternalWorkStore
from test_evidence_corpus import make_catalog, zip_bytes


def prepared(tmp_path, capsys):
    raw = zip_bytes([("fixture/README.md", b"Calibration solver is not validated.\r\n")])
    catalog, record_id, path = make_catalog(tmp_path, raw, "fixture.zip")
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"schema": "ciw.evidence-selection.v1",
        "disclosure_ref": "fixture:explicit-reading",
        "sources": [{"record_id": record_id, "path": str(path), "member": "fixture/README.md"}]}))
    catalog_path = tmp_path / "catalog-inputs" / "bundle"
    output = tmp_path / "evidence"
    cat_args = ["--catalog", str(catalog_path), "--catalog-ref", catalog.ref]
    assert net.main(["evidence", "build", *cat_args, "--selection", str(selection),
                     "--output", str(output)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    ref = receipt["bundle_ref"]
    args = [*cat_args, "--bundle", str(output), "--expected-ref", ref]
    return catalog, catalog_path, record_id, output, ref, args


def test_cli_build_query_inventory_source_span_and_exact_resolver(tmp_path, capsys):
    catalog, catalog_path, record_id, output, ref, args = prepared(tmp_path, capsys)
    assert evidence_cli.main(["representations", *args, "--record-id", record_id]) == 0
    inventory = json.loads(capsys.readouterr().out)
    assert inventory["total"] == 1
    rid = inventory["items"][0]["representation_id"]
    assert evidence_cli.main(["source", *args, "--representation-id", rid]) == 0
    source = json.loads(capsys.readouterr().out)
    assert source["descriptor"]["archive_member"] == "fixture/README.md"
    assert evidence_cli.main(["search", *args, "--query", "not validated"]) == 0
    found = json.loads(capsys.readouterr().out)
    sid = found["items"][0]["span_id"]
    assert evidence_cli.main(["span", *args, "--span-id", sid]) == 0
    span = json.loads(capsys.readouterr().out)
    assert span["span"]["text"].encode() == b"Calibration solver is not validated.\r\n"
    assert not span["authorizes_execution"]
    assert knowledge_cli.main(["resolve", "--bundle", str(catalog_path),
        "--expected-ref", catalog.ref, "--source-id", "gdrive:Original"]) == 0
    assert json.loads(capsys.readouterr().out)["record"]["record_id"] == record_id
    assert evidence_cli.main(["search", *args, "--query", "..."]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "refused"


def test_configuration_retains_both_pins_and_checks_pairing(tmp_path, capsys):
    catalog, catalog_path, record_id, output, ref, args = prepared(tmp_path, capsys)
    base = ["--bundle", str(catalog_path), "--expected-ref", catalog.ref]
    target = tmp_path / "launcher.json"
    assert knowledge_cli.main(["config", *base, "--evidence-bundle", str(output),
        "--evidence-ref", ref, "--output", str(target)]) == 0
    assert not json.loads(capsys.readouterr().out)["activated"]
    command = json.loads(target.read_text())["mcpServers"]["ncp-knowledge"]["args"]
    assert command[command.index("--evidence-ref") + 1] == ref
    assert command[command.index("--expected-ref") + 1] == catalog.ref
    assert knowledge_cli.main(["status", *base, "--evidence-ref", ref]) == 2
    assert "supplied together" in json.loads(capsys.readouterr().err)["reason"]
    assert knowledge_cli.main(["status", *base, "--evidence-bundle", str(output),
                               "--evidence-ref", "sha256:" + "0" * 64]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "refused"


def test_cli_binding_retains_exact_member_bytes_without_authorizing_task(tmp_path, capsys):
    catalog, catalog_path, record_id, output, ref, args = prepared(tmp_path, capsys)
    corpus = EvidenceCorpus.open(output, ref, catalog)
    sid = corpus.search("calibration")["items"][0]["span_id"]
    store = ExternalWorkStore(tmp_path / "work.sqlite3")
    for role in ("requester", "worker"):
        store.provision_client(role, role=role, parent_ref="intent:fixture")
    assert evidence_cli.main(["bind-context", *args, "--span-id", sid,
        "--database", str(store.path), "--parent-ref", "intent:fixture",
        "--disclosure-ref", "fixture:task-context"]) == 0
    bound = json.loads(capsys.readouterr().out)
    task = store.propose("requester", request_key="fixture-task",
        task={"parent_ref": "intent:fixture", "instruction": "Review solver limitation",
              "expected_output": "Cited candidate", "context_ids": [bound["context_id"]]})
    context = store.context("requester", task_id=task["task_id"], context_id=bound["context_id"])
    assert context["text"].encode() == corpus.span_bytes(sid)
    assert context["source_hash"] == corpus.span(sid)["span"]["text_hash"]
    assert context["source_hash"] != "sha256:" + catalog.source(record_id)["record"]["source_sha256"]
    assert context["provenance"]["archive_member"] == "fixture/README.md"
    assert context["provenance"]["record_id"] == record_id
    assert task["authorization"] is None and store.discover("worker")["items"] == []
    assert not bound["authorizes_execution"]
