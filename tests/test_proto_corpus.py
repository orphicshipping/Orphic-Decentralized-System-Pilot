"""Metadata integrity and authority-boundary tests, with no source-content IO."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from ciw.proto_corpus import ProtoCorpus, ProtoCorpusError, compile_bundle


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


@pytest.fixture
def inputs(tmp_path):
    return make_inputs(tmp_path)


def open_inputs(inputs):
    pin = compile_bundle(**inputs)
    return ProtoCorpus.open(inputs["output"], pin)


def source(corpus, source_id):
    return next(x for x in corpus.search("", limit=100)["items"] if x["source_id"] == source_id)


def edit(path, transform):
    value = json.loads(path.read_text(encoding="utf-8"))
    transform(value)
    dump(path, value)


def test_all_registries_preserve_identities_hash_scopes_and_boundaries(inputs):
    corpus = open_inputs(inputs)
    assert corpus.overview()["counts"] == {"source_records": 8, "lot_count": 1,
        "packaged_source_records": 4, "oversize_source_records": 1, "intake_pdf_records": 2,
        "intake_repository_records": 1, "selected_annotations": 1, "represented_hashes": 5,
        "unhashed_source_records": 1, "duplicate_byte_groups": 2, "duplicate_byte_extra_records": 2}
    native = corpus.source(source(corpus, "gdrive:Sheet")["record_id"])["record"]
    assert native["source_sha256"] is None and native["representation_sha256"] == "b" * 64
    assert native["representation"] == "xlsx_export" and native["source_size_bytes"] is None
    assert native["representation_size_bytes"] == 80
    huge = corpus.source(source(corpus, "gdrive:Oversize")["record_id"])["record"]
    assert huge["source_sha256"] is None and huge["representation"] == "reference_only"
    for item in corpus.search("", limit=100)["items"]:
        record = corpus.source(item["record_id"])["record"]
        assert record["metadata_only"] and record["review_status"] == "unreviewed"
        assert not record["authorizes_execution"] and not record["authorizes_acceptance"]
        assert not record["authorizes_source_disclosure"] and not record["raw_sources_rehashed"]
        assert "sha256:" + sha256(corpus.record_bytes(item["record_id"])).hexdigest() == item["record_id"]
        assert "record_id" not in json.loads(corpus.record_bytes(item["record_id"]))


def test_selection_and_duplicates_are_relations_not_additional_or_merged_records(inputs):
    corpus = open_inputs(inputs)
    original = corpus.source(source(corpus, "gdrive:Original")["record_id"])["record"]
    assert original["source_aliases"] == ["urn:notation:source:gdrive:Original"]
    annotation = original["selected_annotations"][0]
    assert annotation["limitations"] == ["Applicability to the installed sensor is unresolved."]
    assert annotation["pipeline_observation"]["mcp_ingestion"] == "not_started"
    assert annotation["fulltext_ingestion_approved"] is False
    related = corpus.relations(source(corpus, "gdrive:Staged")["record_id"])["items"]
    assert {x["kind"] for x in related} == {"curated_copy_of", "equal_representation_bytes", "packaged_in"}
    packaged = next(x for x in related if x["kind"] == "packaged_in")
    assert packaged["lot_sha256"] == "c" * 64 and packaged["assertion_scope"] == "packaging_not_scientific_acceptance"
    assert corpus.review_queue(kind="selected_limitation")["total"] == 1
    assert corpus.review_queue(kind="missing_digest")["total"] == 1


def test_library_original_is_external_provenance_not_fabricated_file(inputs):
    path = inputs["selected"] / "source_manifest.jsonl"
    selection = json.loads(path.read_text())
    selection["source_id"] = "urn:notation:source:library:file_original"
    selection["original"] = {"platform": "ChatGPT Library", "file_id": "file_original"}
    rows(path, [selection])
    corpus = open_inputs(inputs)
    assert corpus.overview()["counts"]["source_records"] == 8
    edge = next(x for x in corpus.relations(source(corpus, "gdrive:Staged")["record_id"])["items"] if x["kind"] == "curated_copy_of")
    assert edge["target_source_id"] == "library:file_original" and edge["target_record_id"] is None


def test_deterministic_frozen_inputs_and_results_are_defensive(inputs, tmp_path):
    original = {p.name: p.read_bytes() for folder in (inputs["registry"], inputs["intake"]) for p in folder.iterdir()}
    first = open_inputs(inputs)
    pin2 = compile_bundle(**(inputs | {"output": tmp_path / "second"}))
    assert first.ref == pin2
    for name, raw in original.items():
        assert (inputs["output"] / "inputs" / name).read_bytes() == raw
    result = first.source(source(first, "gdrive:Original")["record_id"])
    result["record"]["title"] = "mutated"
    assert first.source(result["record"]["record_id"])["record"]["title"] != "mutated"
    inputs["registry"].joinpath("source_membership.jsonl").write_text("changed")
    assert first.search("sensor")["total"] == 4
    assert ProtoCorpus.open(inputs["output"], first.ref).ref == first.ref


def test_search_and_review_pagination_deterministic_and_bounded(inputs):
    corpus = open_inputs(inputs)
    whole = corpus.search("polymer", limit=100)
    one = corpus.search("polymer", limit=1)
    assert one["next_offset"] == 1
    assert one["items"] + corpus.search("polymer", offset=1, limit=100)["items"] == whole["items"]
    assert corpus.search("polymer", kind="repository_archive")["total"] == 1
    assert corpus.search("", category="does-not-exist")["items"] == []
    assert corpus.search("selected calibration")["total"] == 2
    for kwargs in ({"limit": True}, {"limit": 101}, {"offset": -1}, {"offset": True}):
        with pytest.raises(ProtoCorpusError):
            corpus.search("", **kwargs)
    with pytest.raises(ProtoCorpusError):
        corpus.search("x" * 513)
    with pytest.raises(ProtoCorpusError):
        corpus.source("../../source")


@pytest.mark.parametrize("file,mutation", [
    ("lot_registry.json", lambda x: x.update(source_count=99)),
    ("lot_registry.json", lambda x: x["lots"][0].update(source_count=3)),
    ("lot_registry.json", lambda x: x["lots"][0].update(sha256="bad")),
    ("intake_manifest.json", lambda x: x.update(source_count=True)),
    ("intake_manifest.json", lambda x: x["records"][0].update(authorizes_execution=True)),
    ("intake_manifest.json", lambda x: x["records"][0].update(destination_path="../escape")),
    ("verification.json", lambda x: x["records"][0].update(observed_sha256="0" * 64)),
    ("verification.json", lambda x: x["records"][0].update(parent_ok=False)),
    ("verification.json", lambda x: x["records"][0].update(observed_size_bytes=True)),
    ("verification.json", lambda x: x.update(hashes_matched=2)),
    ("verification.json", lambda x: x["records"].append(deepcopy(x["records"][0]))),
    ("repository_manifest.json", lambda x: x["records"][0].update(code_executed=True)),
])
def test_mismatched_registries_refused(inputs, file, mutation):
    folder = inputs["registry"] if file == "lot_registry.json" else inputs["intake"]
    edit(folder / file, mutation)
    with pytest.raises(ProtoCorpusError):
        compile_bundle(**inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("mutation", [
    lambda values: values.append(deepcopy(values[0])),
    lambda values: values[0].update(sha256="not-a-hash"),
    lambda values: values[0].update(archive_member="../escape"),
    lambda values: values[0].update(lot_id="MISSING"),
    lambda values: values[0].update(source_size_bytes=True),
    lambda values: values[2].update(sha256_scope="original_stored_file_bytes"),
])
def test_membership_identity_and_representation_errors_refused(inputs, mutation):
    path = inputs["registry"] / "source_membership.jsonl"
    values = [json.loads(line) for line in path.read_text().splitlines()]
    mutation(values)
    rows(path, values)
    with pytest.raises(ProtoCorpusError):
        compile_bundle(**inputs)


def test_duplicate_json_keys_and_nonfinite_metadata_refused(inputs):
    path = inputs["registry"] / "lot_registry.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"lot_count": 1', b'"lot_count": 1, "lot_count": 1'))
    with pytest.raises(ProtoCorpusError, match="duplicate JSON key"):
        compile_bundle(**inputs)
    path.write_bytes(original[:-1] + b',"extra": NaN}')
    with pytest.raises(ProtoCorpusError, match="nonfinite JSON"):
        compile_bundle(**inputs)


@pytest.mark.parametrize("member", ["catalog.json", "inputs/source_membership.jsonl"])
def test_bundle_tampering_and_wrong_pin_refused(inputs, member):
    corpus = open_inputs(inputs)
    with pytest.raises(ProtoCorpusError, match="pin mismatch"):
        ProtoCorpus.open(inputs["output"], "sha256:" + "0" * 64)
    path = inputs["output"] / member
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ProtoCorpusError, match="checksum mismatch"):
        ProtoCorpus.open(inputs["output"], corpus.ref)


def test_forged_derived_catalog_refused_even_with_new_self_consistent_pin(inputs):
    open_inputs(inputs)
    path = inputs["output"] / "catalog.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["records"][0]["authorizes_execution"] = True
    dump(path, value)
    raw = path.read_bytes()
    manifest_path = inputs["output"] / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["catalog"].update(size_bytes=len(raw), sha256="sha256:" + sha256(raw).hexdigest())
    dump(manifest_path, manifest)
    with pytest.raises(ProtoCorpusError, match="derivation mismatch"):
        ProtoCorpus.open(inputs["output"], "sha256:" + sha256(manifest_path.read_bytes()).hexdigest())


def test_symlink_input_refused(inputs, tmp_path):
    path = inputs["registry"] / "lot_registry.json"
    target = tmp_path / "outside.json"
    path.rename(target)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit test symlinks")
    with pytest.raises(ProtoCorpusError, match="symlink"):
        compile_bundle(**inputs)


def test_existing_output_refused_without_overwrite(inputs):
    corpus = open_inputs(inputs)
    before = (inputs["output"] / "manifest.json").read_bytes()
    with pytest.raises(ProtoCorpusError, match="already exists"):
        compile_bundle(**inputs)
    assert (inputs["output"] / "manifest.json").read_bytes() == before
    assert ProtoCorpus.open(inputs["output"], corpus.ref).ref == corpus.ref


def test_optional_selection_and_bad_nested_schema(inputs):
    corpus = open_inputs(inputs | {"selected": None})
    assert corpus.overview()["counts"]["selected_annotations"] == 0
    edit(inputs["registry"] / "lot_registry.json", lambda x: x.update(lots="invalid"))
    with pytest.raises(ProtoCorpusError):
        compile_bundle(**(inputs | {"output": inputs["output"].parent / "bad"}))


def test_repository_source_specific_limitations_and_observations_survive(inputs):
    corpus = open_inputs(inputs)
    record = corpus.source(source(corpus, "gdrive:Repo")["record_id"])["record"]
    notes = record["repository"]
    assert "not an installed instrument" in notes["description_and_review_notes"]
    assert notes["upstream_candidates_from_readme"] == ["https://github.com/example/declared"]
    assert notes["upstream_external_verification"] is False and notes["license_members_observed"] == []
    assert notes["submodule_marker_observed"] is True and notes["installed_chain_evidence"] is False
    assert corpus.search("simulated signals")["total"] == 1
    assert "simulated signals" in corpus.record_bytes(record["record_id"]).decode()


def test_large_license_lists_have_explicit_bounded_preview_and_pinned_original(inputs):
    members = [f"lib/{n:03}/LICENSE" for n in range(40)]
    edit(inputs["intake"] / "repository_manifest.json", lambda x: x["records"][0].update(license_members_observed=members))
    corpus = open_inputs(inputs)
    row = corpus.source(source(corpus, "gdrive:Repo")["record_id"])["record"]["repository"]
    assert row["license_members_observed"] == members[:12]
    assert row["license_member_count"] == 40 and row["license_members_truncated"] is True
    assert row["complete_observations_locator"] == {"input": "repository_manifest.json", "intake_id": "REPO-001"}
    retained = json.loads((inputs["output"] / "inputs" / "repository_manifest.json").read_bytes())
    assert retained["records"][0]["license_members_observed"] == members
