"""Synthetic refresh acceptance: bytes, authority and create-only publication."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import zipfile

import pytest

from ciw.corpus_refresh import build_refresh
from ciw.evidence_corpus import EvidenceCorpus
from ciw.knowledge_mcp import KnowledgeHost
from ciw.proto_corpus import ProtoCorpus


def dump(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def selection(tmp_path):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("provider/README.md", b"None is certain; unknown uncertainty must be refused.\r\n")
        archive.writestr("provider/unsafe.py", b"raise RuntimeError('THIS_MUST_NOT_EXECUTE')\n")
    records, checks, choices = [], [], []
    for file_id, filename, raw in (("Archive", "provider.zip", archive_bytes.getvalue()),
                                   ("Paper", "paper.pdf", b"%PDF-synthetic retained bytes")):
        path = tmp_path / filename
        path.write_bytes(raw)
        record = {"source_id": "gdrive:" + file_id, "drive_file_id": file_id,
                  "intake_id": "intake-" + file_id, "source_url": "https://drive.google.com/file/d/" + file_id + "/view",
                  "authorizes_execution": False, "original_filename": filename,
                  "bibliographic_title": "Synthetic " + file_id, "size_bytes": len(raw),
                  "sha256": sha256(raw).hexdigest(), "destination_parent_id": "Folder",
                  "destination_path": "Knowledge/" + filename, "original_path": "Queue/" + filename,
                  "category": "test", "classification_basis": "synthetic fixture",
                  "rights_review": "unresolved", "page_count": 1}
        records.append(record)
        checks.append({"drive_file_id": file_id, "destination_parent_id": "Folder", "sha256": record["sha256"],
                       "bytes_match": True, "metadata_match": True, "parents_match": True, "permissions_match": True})
        choices.append({"drive_file_id": file_id, "source_path": str(path),
                        "intake_manifest": str(tmp_path / "intake.json"), "verification": str(tmp_path / "verification.json"),
                        "review_extent": "Synthetic bounded review only; unqualified",
                        "limitations": ["Acquisition timing and sampling are unknown; receipt ticks are not equally spaced acquisition.",
                                        "Unresolved source restrictions; no authorization or qualification."],
                        "members": ["provider/README.md", "provider/unsafe.py"] if file_id == "Archive" else []})
    dump(tmp_path / "intake.json", {"schema": "notation.knowledge-kernel.intake.v1", "batch_id": "test-batch", "records": records})
    dump(tmp_path / "verification.json", {"batch_id": "test-batch", "all_sources_removed_from_queue": True, "checks": checks})
    path = tmp_path / "selection.json"
    dump(path, {"schema": "ciw.corpus-refresh-selection.v1", "authorizes_execution": False,
                "disclosure_ref": "fixture:local-only", "sources": choices})
    return path


def test_refresh_is_reproducible_and_preserves_originals_and_locator_bytes(selection, tmp_path):
    input_before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    receipt = build_refresh(selection=selection, output=tmp_path / "first")
    again = build_refresh(selection=selection, output=tmp_path / "second")
    assert receipt == again
    catalog = ProtoCorpus.open(tmp_path / "first/catalog", receipt["catalog_ref"])
    evidence = EvidenceCorpus.open(tmp_path / "first/evidence", receipt["evidence_ref"], catalog)
    host = KnowledgeHost(catalog, evidence=evidence)
    assert receipt["source_count"] == 2 and receipt["archive_count"] == receipt["pdf_metadata_count"] == 1
    assert not receipt["activated"] and not receipt["authorizes_execution"]
    assert host.call("ncp_knowledge_search", {"query": "timing sampling"})["data"]["total"] == 2
    span_id = host.call("ncp_evidence_search", {"query": "None certain"})["data"]["items"][0]["span_id"]
    span = host.call("ncp_evidence_span", {"span_id": span_id})["data"]["span"]
    raw = b"None is certain; unknown uncertainty must be refused.\r\n"
    assert span["text"].encode() == raw
    assert span["locator"]["span_sha256"] == "sha256:" + sha256(raw).hexdigest()
    assert evidence.search("THIS_MUST_NOT_EXECUTE")["total"] == 1
    for source in receipt["sources"]:
        assert sha256((tmp_path / "first" / source["original_bytes"]).read_bytes()).hexdigest() == source["sha256"]
        record = catalog.resolve(source["source_id"])["record"]
        assert len(record["selected_annotations"]) == 1
        assert record["selected_annotations"][0]["selection_mode"] == "retained_original"
        assert record["selected_annotations"][0]["fulltext_ingestion_approved"] is False
        assert record["rights_review"] == "unresolved"
        assert not catalog.relations(record["record_id"])["items"]
    for path, raw in input_before.items():
        assert path.read_bytes() == raw
    assert host.call("ncp_work_authorize", {})["status"] == "refused"
    assert host.call("ncp_evidence_search", {"query": "certain", "path": str(selection)})["status"] == "refused"
    with pytest.raises(ValueError, match="must be new"):
        build_refresh(selection=selection, output=tmp_path / "first")


@pytest.mark.parametrize("mutation", ["source_hash", "receipt", "authority", "duplicate", "pdf_text", "missing_member"])
def test_refresh_rejects_changed_or_unauthorized_inputs_without_partial_publication(selection, tmp_path, mutation):
    request = json.loads(selection.read_text())
    if mutation == "source_hash":
        (tmp_path / "paper.pdf").write_bytes(b"changed")
    elif mutation == "receipt":
        data = json.loads((tmp_path / "verification.json").read_text())
        data["checks"][0]["parents_match"] = False
        dump(tmp_path / "verification.json", data)
    elif mutation == "authority":
        request["authorizes_execution"] = True
    elif mutation == "duplicate":
        request["sources"].append(deepcopy(request["sources"][0]))
    elif mutation == "pdf_text":
        request["sources"][1]["members"] = ["derived.txt"]
    else:
        request["sources"][0]["members"] = ["provider/absent.md"]
    dump(selection, request)
    with pytest.raises(ValueError):
        build_refresh(selection=selection, output=tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".corpus-refresh-*"))


def test_legacy_metrology_receipt_normalization_keeps_historical_assertions(selection, tmp_path):
    records = json.loads((tmp_path / "intake.json").read_text())["records"]
    checks = [{"source_id": record["source_id"], "sha256": record["sha256"], "size_bytes": record["size_bytes"],
               "destination_path": record["destination_path"], "destination_parent_id": "Folder",
               **{key: True for key in ("bytes_unchanged", "name_unchanged", "file_id_unchanged",
                                       "modified_time_unchanged", "permissions_unchanged")}} for record in records]
    dump(tmp_path / "verification.json", {"status": "PASS", "queue_count_after": 0, "checks": checks})
    result = build_refresh(selection=selection, output=tmp_path / "legacy")
    normalized = json.loads((tmp_path / "legacy/intake/verification.json").read_text())
    assert result["source_count"] == 2
    assert all(x["assertion_scope"] == "historical_intake_receipt_plus_local_byte_recheck" for x in normalized["records"])
    assert all(x["original_batch_id"] == "test-batch" for x in normalized["records"])


def test_retained_original_mode_refuses_a_different_staged_identity(tmp_path):
    from ciw.proto_corpus import compile_bundle
    from test_proto_corpus import make_inputs
    inputs = make_inputs(tmp_path)
    path = inputs["selected"] / "source_manifest.jsonl"
    row = json.loads(path.read_text())
    row["selection_mode"] = "retained_original"
    dump(path, row)
    with pytest.raises(ValueError, match="retained original identity mismatch"):
        compile_bundle(**inputs)


def test_explicit_copy_mode_preserves_legacy_metadata_identity(tmp_path):
    from ciw.proto_corpus import compile_bundle
    from test_proto_corpus import make_inputs
    inputs = make_inputs(tmp_path)
    first = ProtoCorpus.open(inputs["output"], compile_bundle(**inputs))
    path = inputs["selected"] / "source_manifest.jsonl"
    row = json.loads(path.read_text())
    row["selection_mode"] = "staged_copy"
    dump(path, row)
    inputs["output"] = tmp_path / "explicit-copy"
    second = ProtoCorpus.open(inputs["output"], compile_bundle(**inputs))
    assert first.resolve("gdrive:Original")["record"] == second.resolve("gdrive:Original")["record"]
    assert first.resolve("gdrive:Staged")["record"] == second.resolve("gdrive:Staged")["record"]
