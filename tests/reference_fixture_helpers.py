"""Synthetic upstream fixture builders. AGPL-3.0-or-later; see SOURCE_MANIFEST.json."""

import json

from hashlib import sha256

from pathlib import Path

from ciw.proto_corpus import ProtoCorpus, compile_bundle

def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")

def rows(path, values):
    path.write_text("\n".join(json.dumps(x, sort_keys=True) for x in values) + "\n", encoding="utf-8")

def make_inputs(tmp_path):
    """Return compile_bundle kwargs: registry/intake/selected/output Paths.

    Eight source files: four packaged (including one exported Sheet and one
    empty file), one oversize, two equal-byte intake PDFs and one repo archive.
    One selected annotation links existing original/staged files; no extra file.
    """
    registry, intake, selected = [tmp_path / name for name in ("registry", "intake", "selected")]
    for folder in (registry, intake, selected):
        folder.mkdir(parents=True)
    membership = []
    for file_id, path, digest, size in (("Original", "Sensor original.pdf", "a" * 64, 100),
                                       ("Staged", "Selected Sensor.pdf", "a" * 64, 100),
                                       ("Sheet", "Native register", "b" * 64, 80),
                                       ("Empty", "empty.py", sha256(b"").hexdigest(), 0)):
        export = file_id == "Sheet"
        membership.append({"source_file_id": file_id, "source_path": path,
            "source_path_at_initial_inventory": path, "source_url": "https://drive.google.com/file/d/" + file_id + "/view",
            "source_size_bytes": None if export else size, "size_bytes": size, "sha256": digest,
            "representation": "xlsx_export" if export else "original_bytes",
            "sha256_scope": "exported_xlsx_bytes" if export else "original_stored_file_bytes",
            "source_mime_type": "application/vnd.google-apps.spreadsheet" if export else "application/octet-stream",
            "category": "SENSORS", "classification_basis": "existing_category_folder", "set_id": "SENSORS/group",
            "archive_member": "sources/" + path + (".xlsx" if export else ""), "lot_id": "LOT-001"})
    rows(registry / "source_membership.jsonl", membership)
    dump(registry / "lot_registry.json", {"schema_version": "ncp-lot-registry.v1", "source_count": 5,
        "packaged_source_count": 4, "oversize_source_count": 1, "lot_count": 1,
        "lots": [{"lot_id": "LOT-001", "source_count": 4, "total_bytes": 280, "file_name": "LOT-001.zip",
                  "sidecar_manifest_file": "LOT-001.manifest.json", "sha256": "c" * 64,
                  "manifest_sha256": "d" * 64, "drive_file_id": "Archive", "archive_size_bytes": 200,
                  "drive_url": "https://drive.google.com/file/d/Archive/view", "checks": {"zip_crc_integrity": True}}]})
    dump(registry / "oversize_register.json", {"records": [{"source_file_id": "Oversize", "source_path": "Large.zip",
        "source_url": "https://drive.google.com/file/d/Oversize/view", "size_bytes": 400000000,
        "sha256": None, "category": "ARCHIVE-BUNDLES", "initial_source_path": "Large.zip"}]})
    pdfs = []
    for file_id in ("PaperOne", "PaperCopy"):
        pdfs.append({"source_id": "gdrive:" + file_id, "drive_file_id": file_id, "intake_id": "INTAKE-" + file_id,
            "sha256": "e" * 64, "size_bytes": 123, "bibliographic_title": "Polymer calibration " + file_id,
            "source_url": "https://drive.google.com/file/d/" + file_id + "/view", "category": "POLYMER",
            "destination_parent_id": "Folder", "destination_path": "Library/" + file_id + ".pdf",
            "original_path": "Queue/" + file_id + ".pdf", "authorizes_execution": False,
            "classification_basis": "opening pages", "doi_candidates_unverified": [], "page_count": 2,
            "duplicate_of": "gdrive:PaperOne" if file_id == "PaperCopy" else None, "findings": []})
    repos = [{"source_id": "gdrive:Repo", "drive_file_id": "Repo", "intake_id": "REPO-001", "sha256": "f" * 64,
              "size_bytes": 222, "repository_name": "polymer-simulation", "category": "SIMULATION",
              "destination_parent_id": "CodeFolder", "destination_path": "Code/polymer-simulation.zip",
              "original_path": "Queue/polymer-simulation.zip", "source_url": "https://drive.google.com/file/d/Repo/view",
              "authorizes_execution": False, "representation": "original_zip_archive", "code_executed": False,
              "archive_crc": "passed", "classification_basis": "README", "archive_declared_commit": "a" * 40,
              "commit_external_verification": False, "upstream_external_verification": False,
              "license_file_detection": "absent", "dependency_completeness": "not_verified", "build_test_status": "not_run",
              "description_and_review_notes": "README says simulated signals; not an installed instrument or validated solver.",
              "license_members_observed": [], "upstream_candidates_from_readme": ["https://github.com/example/declared"],
              "submodule_marker_observed": True, "unsafe_member_paths": [], "scientific_qualification": "not_performed",
              "installed_chain_evidence": False, "readme_members_reviewed": ["README.md"]}]
    for name, schema, values in (("intake_manifest.json", "notation.knowledge-kernel.intake.v1", pdfs),
                                 ("repository_manifest.json", "notation.knowledge-kernel.repository-intake.v1", repos)):
        dump(intake / name, {"schema": schema, "batch_id": "BATCH", "records": values,
                            "source_count": len(values), "unique_sha256_count": 1, "published_lots_modified": False})
    receipts = [{"file_id": x["drive_file_id"], "actual_parent_ids": [x["destination_parent_id"]],
                 "parent_ok": True, "name_ok": True, "size_ok": True, "modified_time_unchanged": True,
                 "move_success": True, "destination_path": x["destination_path"], "expected_sha256": x["sha256"],
                 "observed_sha256": x["sha256"], "hash_status": "matched", "observed_size_bytes": x["size_bytes"]}
                for x in pdfs + repos]
    dump(intake / "verification.json", {"schema": "notation.knowledge-kernel.intake-verification.v1", "batch_id": "BATCH",
         "source_files": 3, "pdf_files": 2, "repository_archives": 1, "hashes_matched": 3,
         "all_checks_passed": True, "published_lots_modified": False, "records": receipts})
    rows(selected / "source_manifest.jsonl", [{"schema": "notation.corpus-selection.source.v1", "selection_id": "NCP-CS-001",
         "source_id": "urn:notation:source:gdrive:Original", "authorizes_execution": False,
         "original": {"platform": "Google Drive", "file_id": "Original"},
         "snapshot": {"sha256": "a" * 64, "size_bytes": 100},
         "staged_copy": {"drive_file_id": "Staged", "readback_sha256": "a" * 64, "byte_match_verified": True},
         "curation": {"title": "Selected calibration method", "proposed_representation": "uncertainty graph",
                      "annotation_basis": "curator observation, not scientific adjudication", "source_role": "reference",
                      "limitations_to_preserve": ["Applicability to the installed sensor is unresolved."]},
         "rights": {"status": "Rights review required", "scope": "Archiving does not grant AI ingestion rights.",
                    "fulltext_ingestion_approved": False}, "pipeline": {"mcp_ingestion": "not_started"}}])
    return {"registry": registry, "intake": intake, "selected": selected, "output": tmp_path / "bundle"}

def make_catalog(tmp_path, raw, filename="note.md", source_id="gdrive:Original"):
    """Real pinned ProtoCorpus fixture, with one selected original backed by bytes."""
    assert source_id == "gdrive:Original"
    inputs = make_inputs(tmp_path / "catalog-inputs")
    membership_path = inputs["registry"] / "source_membership.jsonl"
    members = [json.loads(line) for line in membership_path.read_text().splitlines()]
    for member in members[:2]:
        member.update(sha256=sha256(raw).hexdigest(), size_bytes=len(raw), source_size_bytes=len(raw))
    members[0]["source_path"] = filename
    members[0]["source_path_at_initial_inventory"] = filename
    membership_path.write_text("\n".join(json.dumps(row) for row in members) + "\n")
    path = inputs["registry"] / "lot_registry.json"
    registry = json.loads(path.read_text())
    registry["lots"][0]["total_bytes"] = 2 * len(raw) + 80
    path.write_text(json.dumps(registry))
    path = inputs["selected"] / "source_manifest.jsonl"
    selection = json.loads(path.read_text())
    selection["snapshot"].update(sha256=sha256(raw).hexdigest(), size_bytes=len(raw))
    selection["staged_copy"]["readback_sha256"] = sha256(raw).hexdigest()
    path.write_text(json.dumps(selection) + "\n")
    pin = compile_bundle(**inputs)
    catalog = ProtoCorpus.open(inputs["output"], pin)
    record_id = catalog.resolve(source_id)["record"]["record_id"]
    source_path = tmp_path / filename
    source_path.write_bytes(raw)
    return catalog, record_id, source_path
