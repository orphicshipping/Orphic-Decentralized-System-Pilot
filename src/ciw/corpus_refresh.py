"""Prepare a new local corpus selection using the existing catalog/evidence compilers.

No source code is imported, installed or executed. PDFs remain metadata-only.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

from . import evidence_corpus as evidence
from .proto_corpus import (ProtoCorpus, ProtoCorpusError, _encode, _id, _no_links,
                           _parse, _publish_directory, _read, _ref, compile_bundle)

MAX_SOURCES = 64
MAX_SOURCE_TOTAL = 256 * 1024 * 1024
MAX_EXPANDED_CRC = 128 * 1024 * 1024


def _require(value, message):
    if not value:
        raise ProtoCorpusError(message)


def _write(path, value):
    path.write_bytes(_encode(value))


def _crc_check(raw):
    """Bound allocation and decompression before checking every member CRC."""
    stream = io.BytesIO(raw)
    evidence._zip_preflight(stream)
    with zipfile.ZipFile(stream) as archive:
        index = evidence._zip_index(archive)
        _require(sum(x.file_size for x in index.values()) <= MAX_EXPANDED_CRC,
                 "archive CRC expansion budget exceeded")
        for info in index.values():
            _require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                     "unsupported archive compression")
            _require(info.file_size <= max(1, info.compress_size) * evidence.MAX_RATIO,
                     "archive CRC expansion ratio exceeded")
            if info.is_dir():
                continue
            total = 0
            with archive.open(info) as member:
                while chunk := member.read(65536):
                    total += len(chunk)
                    _require(total <= info.file_size, "archive member grew")
            _require(total == info.file_size, "archive member size mismatch")


def build_refresh(*, selection: Path, output: Path):
    """Host-only create-once refresh; original intake bytes and assertions survive.

    Current intake receipts are normalized into the legacy compiler vocabulary.
    Parent/name/move assertions describe that retained receipt, never a new Drive
    operation or a live cloud read. Source hashes are independently checked now.
    """
    raw_selection = _read(selection)
    request = _parse(raw_selection)
    _require(type(request) is dict and request.get("schema") == "ciw.corpus-refresh-selection.v1",
             "unsupported refresh selection")
    _require(request.get("authorizes_execution") is False, "selection cannot authorize execution")
    disclosure = request.get("disclosure_ref")
    _require(type(disclosure) is str and 0 < len(disclosure) <= 512, "disclosure reference required")
    rows = request.get("sources")
    _require(type(rows) is list and 1 <= len(rows) <= MAX_SOURCES, "selection source count outside bounds")
    output = _no_links(output)
    _require(not output.exists() and output.parent.is_dir(), "output must be new with existing parent")
    temporary = Path(tempfile.mkdtemp(prefix=".corpus-refresh-", dir=output.parent))
    try:
        result = _build(rows, request, raw_selection, temporary, output)
        _publish_directory(temporary, output)
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _build(rows, request, raw_selection, root, destination):
    for name in ("registry", "intake", "selected", "provenance", "originals"):
        (root / name).mkdir()
    (root / "provenance" / "selection.json").write_bytes(raw_selection)
    pdfs, repos, receipts, annotations, selected_text = [], [], [], [], []
    observed, ids, metadata_inputs, source_total = [], set(), {}, 0
    for choice in rows:
        _require(type(choice) is dict, "selection row must be object")
        file_id = _id(choice.get("drive_file_id"))
        _require(file_id not in ids, "duplicate selected source")
        ids.add(file_id)
        documents = []
        provenance = {}
        for label in ("intake_manifest", "verification"):
            path = Path(choice[label])
            raw = _read(path)
            ref = _ref(raw)
            name = ref[7:] + ".json"
            (root / "provenance" / name).write_bytes(raw)
            metadata_inputs[name] = {"sha256": ref, "size_bytes": len(raw)}
            documents.append(_parse(raw))
            provenance[label] = {"path": "provenance/" + name, "sha256": ref,
                                 "drive_file_id": file_id}
        manifest, verification = documents
        _require(manifest.get("schema") == "notation.knowledge-kernel.intake.v1", "unsupported intake schema")
        if "batch_id" in verification:
            _require(manifest.get("batch_id") == verification["batch_id"], "intake batch mismatch")
        matches = [r for r in manifest.get("records", []) if r.get("drive_file_id") == file_id]
        checks = [r for r in verification.get("checks", [])
                  if r.get("drive_file_id") == file_id or r.get("source_id") == "gdrive:" + file_id]
        _require(len(matches) == len(checks) == 1, "selected source or receipt missing or duplicated")
        record, check = deepcopy(matches[0]), checks[0]
        _require(record.get("source_id") == "gdrive:" + file_id and record.get("authorizes_execution") is False,
                 "intake identity or authority mismatch")
        if "batch_id" in verification:
            verified = (all(check.get(k) is True for k in ("bytes_match", "metadata_match", "parents_match", "permissions_match"))
                        and verification.get("all_sources_removed_from_queue") is True)
        else:
            verified = (verification.get("status") == "PASS" and verification.get("queue_count_after") == 0
                        and all(check.get(k) is True for k in ("bytes_unchanged", "name_unchanged",
                                "file_id_unchanged", "modified_time_unchanged", "permissions_unchanged"))
                        and check.get("destination_path") == record.get("destination_path")
                        and check.get("size_bytes") == record.get("size_bytes"))
        _require(verified and check.get("destination_parent_id") == record.get("destination_parent_id")
                 and check.get("sha256") == record.get("sha256"), "retained intake verification failed")
        raw = _read(Path(choice["source_path"]), evidence.MAX_ARCHIVE)
        source_total += len(raw)
        _require(source_total <= MAX_SOURCE_TOTAL, "refresh source byte budget exceeded")
        _require(len(raw) == record.get("size_bytes") and sha256(raw).hexdigest() == record.get("sha256"),
                 "selected source bytes disagree with intake")
        filename = record.get("original_filename", "")
        suffix = Path(filename).suffix.lower()
        _require(suffix in (".pdf", ".zip"), "refresh supports PDF metadata and ZIP text")
        frozen = root / "originals" / (file_id + suffix)
        frozen.write_bytes(raw)
        limitations = choice.get("limitations")
        _require(type(limitations) is list and limitations and
                 all(type(x) is str and 0 < len(x) <= 2048 for x in limitations), "limitations required")
        extent = choice.get("review_extent")
        _require(type(extent) is str and 0 < len(extent) <= 2048, "review extent required")
        members = choice.get("members", [])
        _require(type(members) is list and len(members) <= evidence.MAX_DOCUMENTS, "invalid selected members")
        _require(len(members) == len(set(members)), "duplicate selected member")
        original_batch = manifest["batch_id"]
        record["refresh_provenance"] = provenance
        if suffix == ".zip":
            _crc_check(raw)
            record.update(repository_name=record["bibliographic_title"], representation="original_zip_archive",
                          code_executed=False, archive_crc="passed", archive_declared_commit=None,
                          commit_external_verification=False, upstream_external_verification=False,
                          license_file_detection="observed_not_adjudicated", dependency_completeness="not_verified",
                          build_test_status="not_run", description_and_review_notes=extent,
                          license_members_observed=record.get("license_file_paths", []),
                          upstream_candidates_from_readme=[], submodule_marker_observed=None,
                          unsafe_member_paths=record.get("archive_path_findings", []),
                          scientific_qualification="not_performed", installed_chain_evidence=False,
                          readme_members_reviewed=[x for x in members if "readme" in x.casefold()])
            repos.append(record)
            selected_text.extend({"source_id": record["source_id"], "path": str(frozen), "member": member}
                                 for member in members)
        else:
            _require(not members, "PDF derived text is not an original-byte representation")
            pdfs.append(record)
        receipts.append({"file_id": file_id, "actual_parent_ids": [record["destination_parent_id"]],
                         "parent_ok": True, "name_ok": True, "size_ok": True, "modified_time_unchanged": True,
                         "move_success": True, "destination_path": record["destination_path"],
                         "expected_sha256": record["sha256"], "observed_sha256": record["sha256"],
                         "observed_size_bytes": len(raw), "hash_status": "matched",
                         "assertion_scope": "historical_intake_receipt_plus_local_byte_recheck",
                         "original_batch_id": original_batch, "original_receipt": provenance["verification"]})
        annotations.append({"schema": "notation.corpus-selection.source.v1", "selection_mode": "retained_original",
                            "selection_id": "refresh:" + file_id, "source_id": "urn:notation:source:" + record["source_id"],
                            "authorizes_execution": False, "original": {"platform": "Google Drive", "file_id": file_id},
                            "snapshot": {"sha256": record["sha256"], "size_bytes": len(raw)},
                            "staged_copy": {"drive_file_id": file_id, "readback_sha256": record["sha256"],
                                            "byte_match_verified": True},
                            "curation": {"title": record["bibliographic_title"], "proposed_representation":
                                         "metadata_only" if suffix == ".pdf" else "bounded_exact_archive_members",
                                         "annotation_basis": extent, "source_role": "research_candidate",
                                         "limitations_to_preserve": limitations},
                            "rights": {"status": record.get("rights_review", "unresolved"),
                                       "scope": "Local selected reading only; redistribution and general fulltext ingestion unresolved.",
                                       "fulltext_ingestion_approved": False},
                            "pipeline": {"mcp_ingestion": "prepared_frozen_local_selection", "activated": False}})
        observed.append({"source_id": record["source_id"], "sha256": record["sha256"], "size_bytes": len(raw),
                         "original_bytes": "originals/" + frozen.name, "provenance": provenance,
                         "archive_crc": "passed" if suffix == ".zip" else "not_applicable",
                         "review_extent": extent, "members": members, "rights_review": record.get("rights_review", "unresolved"),
                         "qualified": False})
    _require(selected_text and len(selected_text) <= evidence.MAX_DOCUMENTS, "select between one and 64 text members")
    # A whitespace-only JSONL is an empty registry, accepted by the existing bounded reader.
    (root / "registry" / "source_membership.jsonl").write_bytes(b"\n")
    _write(root / "registry" / "lot_registry.json", {"schema_version": "ncp-lot-registry.v1", "source_count": 0,
           "packaged_source_count": 0, "oversize_source_count": 0, "lot_count": 0, "lots": []})
    _write(root / "registry" / "oversize_register.json", {"records": []})
    batch = "local-selected-refresh:" + _ref(raw_selection)[7:]
    for name, schema, records in (("intake_manifest.json", "notation.knowledge-kernel.intake.v1", pdfs),
                                  ("repository_manifest.json", "notation.knowledge-kernel.repository-intake.v1", repos)):
        _write(root / "intake" / name, {"schema": schema, "batch_id": batch, "records": records,
               "source_count": len(records), "unique_sha256_count": len({x["sha256"] for x in records}),
               "published_lots_modified": False})
    _write(root / "intake" / "verification.json", {"schema": "notation.knowledge-kernel.intake-verification.v1",
           "batch_id": batch, "source_files": len(rows), "pdf_files": len(pdfs), "repository_archives": len(repos),
           "hashes_matched": len(rows), "all_checks_passed": True, "published_lots_modified": False, "records": receipts})
    (root / "selected" / "source_manifest.jsonl").write_bytes(b"\n".join(map(_encode, annotations)) + b"\n")
    catalog_ref = compile_bundle(registry=root / "registry", intake=root / "intake", selected=root / "selected",
                                 output=root / "catalog")
    catalog = ProtoCorpus.open(root / "catalog", catalog_ref)
    for row in selected_text:
        row["record_id"] = catalog.resolve(row.pop("source_id"))["record"]["record_id"]
    _write(root / "evidence-selection.json", {"schema": "ciw.evidence-selection.v1",
           "disclosure_ref": request["disclosure_ref"], "sources": selected_text})
    evidence_ref = evidence.compile_evidence(catalog=catalog, selection=root / "evidence-selection.json",
                                              output=root / "evidence")
    # Keep reproducible selection paths after staging is renamed.
    for row in selected_text:
        row["path"] = str(destination / "originals" / Path(row["path"]).name)
    _write(root / "evidence-selection.json", {"schema": "ciw.evidence-selection.v1",
           "disclosure_ref": request["disclosure_ref"], "sources": selected_text})
    receipt = {"schema": "ciw.corpus-refresh-receipt.v1", "selection_sha256": _ref(raw_selection),
               "catalog_ref": catalog_ref, "evidence_ref": evidence_ref, "source_count": len(rows),
               "pdf_metadata_count": len(pdfs), "archive_count": len(repos), "text_representations": len(selected_text),
               "original_source_bytes": source_total, "sources": observed, "retained_metadata_inputs": metadata_inputs,
               "authorizes_execution": False, "activated": False, "physical_qualification": False,
               "disclosure_ref": request["disclosure_ref"]}
    _write(root / "receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_refresh(selection=args.selection, output=args.output)
        print(json.dumps({k: v for k, v in result.items() if k not in ("sources", "retained_metadata_inputs")}, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)[:400], "authorizes_execution": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
