"""Frozen, metadata-only catalog of the raw NCP Proto-Corpus.

Source-file identity, represented byte identity, packaging and scientific trust
are separate. Input registries are retained byte for byte; none of the APIs
opens original sources, extracts archives, performs embeddings or grants access.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from time import sleep as _publication_pause

SCHEMA = "ciw.proto-corpus-catalog.v1"
MAX_INPUT = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_CATALOG = 24 * 1024 * 1024
MAX_RECORD = 8192
MAX_RECORDS = 20000
HEX = re.compile(r"[0-9a-f]{64}")
REF = re.compile(r"sha256:[0-9a-f]{64}")
FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,180}")
INPUT_NAMES = ("source_membership.jsonl", "lot_registry.json", "oversize_register.json",
               "intake_manifest.json", "repository_manifest.json", "verification.json")
SELECTED = "selected_source_manifest.jsonl"
BOUNDARIES = {
    "metadata_only": True, "review_status": "unreviewed",
    "scientific_adjudication": "not_performed", "authorizes_execution": False,
    "authorizes_source_disclosure": False, "authorizes_acceptance": False,
    "source_authenticity_checked": False, "raw_sources_rehashed": False,
    "embeddings_created": False, "snapshot_semantics": "frozen_registry_metadata_not_live_Drive",
    "data_role": "untrusted_retained_source_and_curatorial_metadata",
}
LIMITATIONS = [
    "Registry hashes describe retained representations, not scientific validity or source authenticity.",
    "Packaging verification and intake receipts are retained assertions, reconciled here without re-reading source bytes.",
    "Source-use rights and scientific acceptance remain outside this catalog and either model's judgment.",
    "Lexical search matches metadata only; source content, code and nested archives are not inspected or executed.",
    "Equal bytes do not merge source identities, file locations, versions or authority.",
    "Source paths and URLs are observations from the frozen registries, not live availability checks.",
]


class ProtoCorpusError(ValueError):
    """A catalog integrity, schema or bounded-request refusal."""


def _require(condition, message):
    if not condition:
        raise ProtoCorpusError(message)


def _encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ProtoCorpusError("invalid canonical metadata") from exc


def _ref(raw):
    return "sha256:" + sha256(raw).hexdigest()


def _search_tokens(text, *, document=False):
    """Unicode words, split at underscores; no substring or stemming matches.

    Documents retain whole words and add Latin CamelCase components so both
    ``OpenFOAM`` and ``open foam`` find the same named artifact. Query tokens
    are case-insensitive whole words and all must occur in the metadata.
    """
    words = re.findall(r"[^\W_]+", text)
    result = {word.casefold() for word in words}
    if document:
        for word in words:
            result.update(part.casefold() for part in re.split(
                r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", word))
    return result


def _object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate JSON key")
        value[key] = item
    return value


def _parse(raw):
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ProtoCorpusError("nonfinite JSON")))
        work = [(value, 0)]
        visited = 0
        while work:
            item, depth = work.pop()
            visited += 1
            _require(depth <= 32 and visited <= 2000000, "JSON tree outside bounds")
            if type(item) is dict:
                work.extend((v, depth + 1) for v in item.values())
            elif type(item) is list:
                work.extend((v, depth + 1) for v in item)
            elif type(item) is str:
                _require(len(item) <= 32768, "metadata string outside bounds")
                item.encode("utf-8")
            elif type(item) is float:
                _require(math.isfinite(item), "nonfinite JSON")
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtoCorpusError("invalid registry JSON") from exc


def _rows(raw):
    lines = [line for line in raw.splitlines() if line.strip()]
    _require(len(lines) <= MAX_RECORDS, "registry row count outside bounds")
    values = [_parse(line) for line in lines]
    _require(all(type(x) is dict for x in values), "registry rows must be objects")
    return values


def _text(value, label, maximum=2048):
    _require(type(value) is str and 0 < len(value) <= maximum and "\x00" not in value,
             "invalid " + label)
    return value


def _integer(value, label):
    _require(type(value) is int and 0 <= value <= 2 ** 53 - 1, "invalid " + label)
    return value


def _hash(value, nullable=False):
    if value is None and nullable:
        return None
    _require(type(value) is str and HEX.fullmatch(value) is not None, "invalid SHA256")
    return value


def _id(value):
    _require(type(value) is str and FILE_ID.fullmatch(value) is not None, "invalid Drive file ID")
    return value


def _relative(value):
    _text(value, "relative path", 4096)
    _require("\\" not in value and ":" not in value and not value.startswith("/") and
             all(p not in ("", ".", "..") for p in value.split("/")), "unsafe relative path")
    return value


def _no_links(path):
    path = Path(path).absolute()
    _require(not any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                     for p in (path, *path.parents)), "symlink or junction refused")
    return path


_WINDOWS_PUBLICATION = os.name == "nt"
_PUBLICATION_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64)


def _publish_directory(temporary, output, *, require=_require):
    """Publish checked staging bytes once; briefly tolerate Windows sharing locks.

    Retrying is limited to the final create-only rename. Validation, reads and
    writes are not repeated. Every attempt rechecks link and destination guards;
    Windows Path.rename also refuses an existing destination at the OS boundary.
    A persistent denial retains its original error and the caller owns cleanup.
    """
    for attempt in range(len(_PUBLICATION_DELAYS) + 1):
        staged = _no_links(temporary)
        target = _no_links(output)
        require(not target.exists(), "output appeared during publication")
        try:
            staged.rename(target)
            return
        except OSError as exc:
            if (not _WINDOWS_PUBLICATION or getattr(exc, "winerror", None) not in {5, 32, 33}
                    or attempt == len(_PUBLICATION_DELAYS)):
                raise
        _publication_pause(_PUBLICATION_DELAYS[attempt])


def _read(path, maximum=MAX_INPUT):
    path = _no_links(path)
    try:
        before = path.stat()
        _require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum, "input outside byte bounds")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            _require(stat.S_ISREG(opened.st_mode) and (before.st_dev, before.st_ino) ==
                     (opened.st_dev, opened.st_ino), "input changed before read")
            raw = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        _require((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) ==
                 (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and len(raw) == after.st_size
                 and 0 < len(raw) <= maximum, "input changed during read")
        return raw
    except OSError as exc:
        raise ProtoCorpusError("cannot read bounded catalog input") from exc


def _list(value, label):
    _require(type(value) is list and len(value) <= MAX_RECORDS, "invalid " + label)
    _require(all(type(x) is dict for x in value), "invalid " + label + " rows")
    return value


def _unique(rows, field):
    result = {}
    for row in rows:
        key = _text(row.get(field), field)
        _require(key not in result, "duplicate " + field)
        result[key] = row
    return result


def _kind(path, mime, *, repository=False):
    if repository:
        return "repository_archive"
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in (".exe", ".msi", ".dmg"):
        return "installer"
    if suffix in (".zip", ".tar", ".gz", ".7z"):
        return "archive"
    if suffix == ".ipynb":
        return "notebook"
    if suffix in (".pdf", ".docx", ".md", ".txt", ".html", ".tm"):
        return "document"
    if suffix in (".py", ".js", ".ts", ".h", ".c", ".cpp", ".sh", ".css", ".pyc"):
        return "code"
    if suffix in (".png", ".jpg", ".jpeg", ".svg", ".ico"):
        return "image"
    if suffix in (".json", ".jsonl", ".csv", ".xlsx", ".xml") or mime == "application/vnd.google-apps.spreadsheet":
        return "data"
    return "other"


def _normalize(inputs):
    try:
        return _normalize_records(inputs)
    except ProtoCorpusError:
        raise
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise ProtoCorpusError("invalid registry schema") from exc


def _normalize_records(inputs):
    """Derive deterministic metadata; never consult original source paths/URLs."""
    _require(set(inputs) in (set(INPUT_NAMES), set(INPUT_NAMES) | {SELECTED}), "unexpected catalog inputs")
    membership = _rows(inputs["source_membership.jsonl"])
    registry = _parse(inputs["lot_registry.json"])
    oversize = _parse(inputs["oversize_register.json"])
    pdf = _parse(inputs["intake_manifest.json"])
    repo = _parse(inputs["repository_manifest.json"])
    verification = _parse(inputs["verification.json"])
    _require(all(type(x) is dict for x in (registry, oversize, pdf, repo, verification)), "registry must be object")
    _require(registry.get("schema_version") == "ncp-lot-registry.v1", "unsupported lot registry schema")
    _require(pdf.get("schema") == "notation.knowledge-kernel.intake.v1" and
             repo.get("schema") == "notation.knowledge-kernel.repository-intake.v1" and
             verification.get("schema") == "notation.knowledge-kernel.intake-verification.v1", "unsupported intake schema")
    lots = _list(registry.get("lots"), "lots")
    lots_by_id = _unique(lots, "lot_id")
    large = _list(oversize.get("records"), "oversize")
    pdfs, repos = _list(pdf.get("records"), "PDFs"), _list(repo.get("records"), "repositories")
    _require(_integer(registry.get("lot_count"), "lot count") == len(lots) and
             _integer(registry.get("packaged_source_count"), "packaged count") == len(membership) and
             _integer(registry.get("oversize_source_count"), "oversize count") == len(large) and
             _integer(registry.get("source_count"), "source count") == len(membership) + len(large), "lot counts disagree")
    for manifest, rows in ((pdf, pdfs), (repo, repos)):
        _require(_integer(manifest.get("source_count"), "intake count") == len(rows) and
                 _integer(manifest.get("unique_sha256_count"), "unique digest count") == len({x.get("sha256") for x in rows})
                 and manifest.get("published_lots_modified") is False, "intake counts or preservation disagree")
    _require(_text(pdf.get("batch_id"), "batch") == repo.get("batch_id") == verification.get("batch_id"), "intake batch mismatch")
    receipts = _unique(_list(verification.get("records"), "verification"), "file_id")
    intake_ids = [x.get("drive_file_id") for x in pdfs + repos]
    _require(len(set(intake_ids)) == len(intake_ids) and set(receipts) == set(intake_ids), "intake receipt membership mismatch")
    _require(verification.get("all_checks_passed") is True and verification.get("published_lots_modified") is False and
             _integer(verification.get("source_files"), "verified source count") == len(intake_ids) and
             _integer(verification.get("hashes_matched"), "verified hash count") == len(intake_ids) and
             _integer(verification.get("pdf_files"), "verified PDF count") == len(pdfs) and
             _integer(verification.get("repository_archives"), "verified repository count") == len(repos), "intake verification mismatch")
    records, members_seen, lot_counts, lot_bytes = {}, set(), Counter(), Counter()
    edges, findings = [], []

    def add(file_id, path, digest, size, category, representation, scope, *, mime=None,
            source_size=None, title=None, provenance, original_path=None, repository=False):
        file_id = _id(file_id)
        source_id = "gdrive:" + file_id
        _require(source_id not in records, "duplicate source identity across registries")
        _relative(path)
        digest = _hash(digest, nullable=True)
        _integer(size, "representation size")
        if source_size is not None:
            _integer(source_size, "source size")
        record = {"schema": "ciw.proto-corpus-source.v1", "source_id": source_id,
                  "drive_file_id": file_id, "title": _text(title or PurePosixPath(path).name, "title"),
                  "category": _text(category, "category", 120), "kind": _kind(path, mime, repository=repository),
                  "representation": representation, "source_sha256": digest if scope == "original_stored_file_bytes" else None,
                  "representation_sha256": digest, "sha256_scope": scope,
                  "source_size_bytes": source_size, "representation_size_bytes": size,
                  "source_path": path, "original_source_path": original_path, "source_mime_type": mime,
                  "source_url": "https://drive.google.com/file/d/" + file_id + "/view",
                  "provenance": [provenance], "lot_memberships": [], "selected_annotations": [],
                  "source_aliases": [], "rights_review": "unresolved", **BOUNDARIES}
        records[source_id] = record
        findings.append({"source_id": source_id, "kind": "rights_review", "detail": "Source-use rights have not been adjudicated by this catalog."})
        if digest is None:
            findings.append({"source_id": source_id, "kind": "missing_digest", "detail": "Registry has no source-byte digest; retained as a reference only."})
        if scope == "exported_xlsx_bytes":
            findings.append({"source_id": source_id, "kind": "derived_representation", "detail": "The retained SHA256 identifies an XLSX export, not native Google Sheet bytes."})
        return record

    for row in membership:
        lot_id = _text(row.get("lot_id"), "lot ID")
        _require(lot_id in lots_by_id, "unknown lot membership")
        member = _relative(row.get("archive_member"))
        _require((lot_id, member) not in members_seen, "duplicate archive member")
        members_seen.add((lot_id, member))
        rep, scope = row.get("representation"), row.get("sha256_scope")
        _require((rep, scope) in (("original_bytes", "original_stored_file_bytes"),
                                 ("xlsx_export", "exported_xlsx_bytes")), "unsupported representation scope")
        if rep == "original_bytes":
            _require(type(row.get("source_size_bytes")) is int and row["source_size_bytes"] == row.get("size_bytes"), "original byte size mismatch")
        else:
            _require(row.get("source_size_bytes") is None and row.get("source_mime_type") == "application/vnd.google-apps.spreadsheet", "invalid native export")
        record = add(row.get("source_file_id"), row.get("source_path"), _hash(row.get("sha256")),
                     row.get("size_bytes"), row.get("category"), rep, scope, mime=row.get("source_mime_type"),
                     source_size=row.get("source_size_bytes"), original_path=row.get("source_path_at_initial_inventory"),
                     provenance={"input": "source_membership.jsonl", "source_file_id": row.get("source_file_id")})
        record["source_url"] = _text(row.get("source_url"), "source URL")
        record["classification_basis"] = _text(row.get("classification_basis"), "classification basis")
        record["set_id"] = _text(row.get("set_id"), "set ID")
        record["lot_memberships"] = [{"lot_id": lot_id, "archive_member": member}]
        lot_counts[lot_id] += 1
        lot_bytes[lot_id] += row["size_bytes"]
    for lot_id, lot in lots_by_id.items():
        _require(_integer(lot.get("source_count"), "lot sources") == lot_counts[lot_id] and
                 _integer(lot.get("total_bytes"), "lot bytes") == lot_bytes[lot_id], "lot membership totals mismatch")
        _hash(lot.get("sha256")); _hash(lot.get("manifest_sha256")); _id(lot.get("drive_file_id"))
        _relative(lot.get("file_name")); _relative(lot.get("sidecar_manifest_file"))
        _integer(lot.get("archive_size_bytes"), "archive size")
    for row in large:
        record = add(row.get("source_file_id"), row.get("source_path"), row.get("sha256"), row.get("size_bytes"),
                     row.get("category"), "reference_only", "not_available", source_size=row.get("size_bytes"),
                     original_path=row.get("initial_source_path"),
                     provenance={"input": "oversize_register.json", "source_file_id": row.get("source_file_id")})
        _require(row.get("sha256") is None, "oversize representation contract changed")
        record["source_url"] = _text(row.get("source_url"), "source URL")
    for is_repo, rows, name in ((False, pdfs, "intake_manifest.json"), (True, repos, "repository_manifest.json")):
        _unique(rows, "intake_id")
        for row in rows:
            file_id = _id(row.get("drive_file_id"))
            _require(row.get("source_id") == "gdrive:" + file_id and row.get("authorizes_execution") is False,
                     "intake identity or authority mismatch")
            receipt = receipts[file_id]
            _require(all(receipt.get(k) is True for k in ("parent_ok", "name_ok", "size_ok", "modified_time_unchanged", "move_success"))
                     and receipt.get("hash_status") == "matched" and receipt.get("observed_sha256") == row.get("sha256") == receipt.get("expected_sha256")
                     and type(receipt.get("observed_size_bytes")) is int and receipt["observed_size_bytes"] == row.get("size_bytes")
                     and receipt.get("destination_path") == row.get("destination_path") and
                     type(receipt.get("actual_parent_ids")) is list and row.get("destination_parent_id") in receipt["actual_parent_ids"],
                     "intake verification record mismatch")
            record = add(file_id, row.get("destination_path"), _hash(row.get("sha256")), row.get("size_bytes"), row.get("category"),
                         "original_zip_archive" if is_repo else "original_bytes", "original_stored_file_bytes",
                         source_size=row.get("size_bytes"), mime="application/zip" if is_repo else "application/pdf",
                         title=row.get("repository_name") if is_repo else row.get("bibliographic_title"), repository=is_repo,
                         original_path=row.get("original_path"), provenance={"input": name, "intake_id": row.get("intake_id")})
            record["source_url"] = _text(row.get("source_url"), "source URL")
            record["classification_basis"] = _text(row.get("classification_basis"), "classification basis")
            record["intake_id"] = row["intake_id"]
            if row.get("duplicate_of"):
                edges.append({"source_id": record["source_id"], "target_source_id": _text(row["duplicate_of"], "duplicate target"), "kind": "declared_duplicate_of"})
            if is_repo:
                _require(row.get("representation") == "original_zip_archive" and row.get("code_executed") is False and
                         row.get("archive_crc") == "passed", "repository intake state mismatch")
                record["repository"] = {k: row.get(k) for k in ("archive_declared_commit", "commit_external_verification",
                    "upstream_external_verification", "license_file_detection", "dependency_completeness", "build_test_status", "archive_crc",
                    "description_and_review_notes", "license_members_observed", "upstream_candidates_from_readme",
                    "submodule_marker_observed", "unsafe_member_paths", "scientific_qualification", "installed_chain_evidence",
                    "readme_members_reviewed")}
                licenses = row.get("license_members_observed", [])
                _require(type(licenses) is list and len(licenses) <= 1000 and
                         all(type(x) is str and 0 < len(x) <= 2048 for x in licenses), "invalid license observations")
                record["repository"]["license_members_observed"] = licenses[:12]
                record["repository"]["license_member_count"] = len(licenses)
                record["repository"]["license_members_truncated"] = len(licenses) > 12
                record["repository"]["complete_observations_locator"] = {"input": name, "intake_id": row["intake_id"]}
                findings.append({"source_id": record["source_id"], "kind": "repository_review", "detail": "Archive integrity is recorded; origin, license, dependencies and operation require separate review."})
            else:
                record["doi_candidates_unverified"] = row.get("doi_candidates_unverified", [])
                record["page_count"] = row.get("page_count")
    for edge in edges:
        _require(edge["target_source_id"] in records and records[edge["source_id"]]["representation_sha256"] ==
                 records[edge["target_source_id"]]["representation_sha256"], "duplicate declaration mismatch")
    selections = _rows(inputs[SELECTED]) if SELECTED in inputs else []
    _unique(selections, "selection_id"); _unique(selections, "source_id")
    for selected in selections:
        _require(selected.get("schema") == "notation.corpus-selection.source.v1" and selected.get("authorizes_execution") is False,
                 "unsupported selected source contract")
        stage, original, snapshot = selected.get("staged_copy"), selected.get("original"), selected.get("snapshot")
        _require(all(type(x) is dict for x in (stage, original, snapshot, selected.get("curation"), selected.get("rights"))), "invalid selected metadata")
        staged_id = "gdrive:" + _id(stage.get("drive_file_id"))
        _require(staged_id in records and stage.get("byte_match_verified") is True and
                 _hash(snapshot.get("sha256")) == stage.get("readback_sha256") == records[staged_id]["source_sha256"] and
                 _integer(snapshot.get("size_bytes"), "selected size") == records[staged_id]["source_size_bytes"], "selected snapshot mismatch")
        platform = original.get("platform")
        _require(platform in ("Google Drive", "ChatGPT Library"), "unsupported selected platform")
        native_id = _text(original.get("file_id"), "original file ID")
        native_source = ("gdrive:" if platform == "Google Drive" else "library:") + native_id
        _require(selected.get("source_id") == "urn:notation:source:" + native_source, "selected original identity mismatch")
        annotation = {"selection_id": _text(selected.get("selection_id"), "selection ID"),
                      "original_source_id": selected["source_id"], "staged_source_id": staged_id,
                      "title": _text(selected["curation"].get("title"), "selected title"),
                      "proposed_representation": _text(selected["curation"].get("proposed_representation"), "proposed representation"),
                      "rights_status": _text(selected["rights"].get("status"), "rights status"),
                      "fulltext_ingestion_approved": selected["rights"].get("fulltext_ingestion_approved")}
        _require(type(annotation["fulltext_ingestion_approved"]) is bool, "invalid fulltext approval state")
        annotation["annotation_basis"] = _text(selected["curation"].get("annotation_basis"), "annotation basis")
        annotation["source_role"] = _text(selected["curation"].get("source_role"), "selected source role")
        limitations = selected["curation"].get("limitations_to_preserve")
        _require(type(limitations) is list and len(limitations) <= 32, "invalid selected limitations")
        annotation["limitations"] = [_text(x, "selected limitation") for x in limitations]
        annotation["rights_scope"] = _text(selected["rights"].get("scope"), "rights scope")
        annotation["pipeline_observation"] = selected.get("pipeline")
        _require(type(annotation["pipeline_observation"]) is dict, "invalid pipeline observation")
        records[staged_id]["selected_annotations"].append(annotation)
        records[staged_id]["provenance"].append({"input": SELECTED, "selection_id": annotation["selection_id"]})
        if native_source in records:
            _require(records[native_source]["source_sha256"] == snapshot["sha256"], "selected original digest mismatch")
            records[native_source]["selected_annotations"].append(annotation)
            records[native_source]["source_aliases"].append(selected["source_id"])
            records[native_source]["provenance"].append({"input": SELECTED, "selection_id": annotation["selection_id"]})
        else:
            _require(platform == "ChatGPT Library", "selected original Drive source missing")
        for limitation in annotation["limitations"]:
            findings.append({"source_id": staged_id, "kind": "selected_limitation",
                             "selection_id": annotation["selection_id"], "detail": limitation})
        edges.append({"source_id": staged_id, "target_source_id": native_source, "kind": "curated_copy_of",
                      "original_source_id": selected["source_id"], "selection_id": annotation["selection_id"]})
    for finding in _list(pdf.get("review_findings", []), "review findings"):
        key = _text(finding.get("id"), "finding ID")
        for row in pdfs:
            if key in row.get("findings", []):
                findings.append({"source_id": row["source_id"], "kind": "intake_finding", "finding_id": key,
                                 "detail": _text(finding.get("finding"), "finding", 4096)})
    by_hash = defaultdict(list)
    for record in records.values():
        if record["representation_sha256"]:
            by_hash[(record["sha256_scope"], record["representation_sha256"])].append(record["source_id"])
    duplicate_groups = [sorted(ids) for ids in by_hash.values() if len(ids) > 1]
    for ids in duplicate_groups:
        for source_id in ids:
            findings.append({"source_id": source_id, "kind": "duplicate_bytes", "detail": "Equal represented bytes have multiple retained source-file identities."})
    _require(len(records) <= MAX_RECORDS, "catalog source count outside bounds")
    final = []
    for source_id in sorted(records):
        record = records[source_id]
        record["record_id"] = _ref(_encode(record))
        _require(len(_encode(record)) <= MAX_RECORD, "source metadata record outside byte bound")
        final.append(record)
    counts = {"source_records": len(final), "lot_count": len(lots), "packaged_source_records": len(membership),
              "oversize_source_records": len(large), "intake_pdf_records": len(pdfs), "intake_repository_records": len(repos),
              "selected_annotations": len(selections), "represented_hashes": len(by_hash),
              "unhashed_source_records": sum(x["representation_sha256"] is None for x in final),
              "duplicate_byte_groups": len(duplicate_groups), "duplicate_byte_extra_records": sum(len(x) - 1 for x in duplicate_groups)}
    return {"schema": SCHEMA, "counts": counts, "records": final,
            "lots": sorted(lots, key=lambda x: x["lot_id"]),
            "relations": sorted(edges, key=lambda x: (x["source_id"], x["kind"], x["target_source_id"])),
            "duplicate_groups": sorted(duplicate_groups),
            "review_queue": sorted(findings, key=lambda x: (x["kind"], x["source_id"], x.get("finding_id", ""))),
            "boundaries": BOUNDARIES, "limitations": LIMITATIONS}


def compile_bundle(*, registry: Path, intake: Path, selected: Path | None, output: Path) -> str:
    """Freeze the metadata registries to a new directory; never mutate inputs."""
    paths = {name: Path(registry if name in INPUT_NAMES[:3] else intake) / name for name in INPUT_NAMES}
    if selected is not None:
        paths[SELECTED] = Path(selected) / "source_manifest.jsonl"
    inputs, total = {}, 0
    for name, path in paths.items():
        raw = _read(path)
        total += len(raw)
        _require(total <= MAX_TOTAL, "input bundle outside byte bounds")
        inputs[name] = raw
    catalog = _encode(_normalize(inputs))
    _require(len(catalog) <= MAX_CATALOG, "catalog outside byte bounds")
    manifest = _encode({"schema": "ciw.proto-corpus-bundle.v1", "catalog_schema": SCHEMA,
                       "inputs": [{"name": name, "size_bytes": len(raw), "sha256": _ref(raw)} for name, raw in sorted(inputs.items())],
                       "catalog": {"name": "catalog.json", "size_bytes": len(catalog), "sha256": _ref(catalog)}})
    output = _no_links(output)
    _require(not output.exists(), "output already exists")
    _require(output.parent.is_dir(), "output parent must exist")
    temporary = Path(tempfile.mkdtemp(prefix=".proto-corpus-", dir=output.parent))
    try:
        (temporary / "inputs").mkdir()
        for name, raw in inputs.items():
            (temporary / "inputs" / name).write_bytes(raw)
        (temporary / "catalog.json").write_bytes(catalog)
        (temporary / "manifest.json").write_bytes(manifest)
        _require(not output.exists(), "output appeared during compilation")
        _publish_directory(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return _ref(manifest)


class ProtoCorpus:
    """Verified frozen in-memory reader. Methods accept identifiers, never paths."""

    @classmethod
    def open(cls, bundle: Path, expected_ref: str):
        _require(type(expected_ref) is str and REF.fullmatch(expected_ref) is not None, "expected bundle pin required")
        bundle = _no_links(bundle)
        manifest_raw = _read(bundle / "manifest.json")
        _require(_ref(manifest_raw) == expected_ref, "bundle pin mismatch")
        manifest = _parse(manifest_raw)
        _require(type(manifest) is dict and set(manifest) == {"schema", "catalog_schema", "inputs", "catalog"} and
                 manifest["schema"] == "ciw.proto-corpus-bundle.v1" and manifest["catalog_schema"] == SCHEMA, "unsupported bundle manifest")
        descriptors = _unique(_list(manifest["inputs"], "input descriptors"), "name")
        _require(set(descriptors) in (set(INPUT_NAMES), set(INPUT_NAMES) | {SELECTED}), "unexpected bundle input names")
        inputs, total = {}, 0
        for name, descriptor in descriptors.items():
            raw = _read(bundle / "inputs" / name)
            total += len(raw)
            _require(total <= MAX_TOTAL, "input bundle outside byte bounds")
            _require(type(descriptor.get("size_bytes")) is int and descriptor["size_bytes"] == len(raw) and descriptor.get("sha256") == _ref(raw), "input checksum mismatch")
            inputs[name] = raw
        descriptor = manifest["catalog"]
        _require(type(descriptor) is dict and descriptor.get("name") == "catalog.json", "invalid catalog descriptor")
        raw = _read(bundle / "catalog.json", MAX_CATALOG)
        _require(type(descriptor.get("size_bytes")) is int and descriptor["size_bytes"] == len(raw) and descriptor.get("sha256") == _ref(raw), "catalog checksum mismatch")
        catalog = _normalize(inputs)
        _require(raw == _encode(catalog), "catalog derivation mismatch")
        self = cls()
        self.ref = expected_ref
        self._catalog = catalog
        self._records = {x["record_id"]: x for x in catalog["records"]}
        self._sources = {x["source_id"]: x for x in catalog["records"]}
        self._inputs = manifest["inputs"]
        self._by_duplicate = {}
        for group in catalog["duplicate_groups"]:
            for source_id in group:
                self._by_duplicate[source_id] = group
        self._index_metadata()
        return self

    def _index_metadata(self):
        """Derived runtime indexes never alter the pinned catalog or records."""
        postings, titles = defaultdict(set), {}
        facets = {"category": defaultdict(set), "kind": defaultdict(set)}
        identities = defaultdict(set)
        for record_id, record in self._records.items():
            titles[record_id] = frozenset(_search_tokens(record["title"], document=True))
            fields = [record["title"], record["source_path"], record["category"], record["kind"],
                      *[x["title"] for x in record["selected_annotations"]],
                      *[text for x in record["selected_annotations"] for text in x["limitations"]],
                      record.get("repository", {}).get("description_and_review_notes") or ""]
            for term in set().union(*(_search_tokens(text, document=True) for text in fields)):
                postings[term].add(record_id)
            for field in facets:
                facets[field][record[field]].add(record_id)
            for source_id in (record["source_id"], *record["source_aliases"]):
                identities[source_id].add(record_id)
        self._postings = {term: frozenset(ids) for term, ids in postings.items()}
        self._title_terms = titles
        self._facets = {field: {value: frozenset(ids) for value, ids in index.items()}
                        for field, index in facets.items()}
        self._identities = {source_id: frozenset(ids) for source_id, ids in identities.items()}
        self._overview = {"schema": SCHEMA, "counts": self._catalog["counts"],
                          "categories": dict(sorted(Counter(x["category"] for x in self._records.values()).items())),
                          "kinds": dict(sorted(Counter(x["kind"] for x in self._records.values()).items())),
                          "inputs": self._inputs, "limitations": LIMITATIONS}
        adjacency = defaultdict(list)
        for relation in self._catalog["relations"]:
            for source_id in {relation["source_id"], relation["target_source_id"]}:
                adjacency[source_id].append(relation)
        self._relation_adjacency = dict(adjacency)
        self._lots = {lot["lot_id"]: lot for lot in self._catalog["lots"]}
        self._reviews = [{**item, "record_id": self._sources[item["source_id"]]["record_id"]}
                         for item in self._catalog["review_queue"]]
        by_kind = defaultdict(list)
        for item in self._reviews:
            by_kind[item["kind"]].append(item)
        self._reviews_by_kind = dict(by_kind)

    def _result(self, **values):
        return deepcopy({"catalog_ref": self.ref, **values, **BOUNDARIES})

    def _record(self, record_id):
        _require(type(record_id) is str and REF.fullmatch(record_id) is not None, "metadata record ID required")
        _require(record_id in self._records, "source record not found")
        return self._records[record_id]

    @staticmethod
    def _page(items, limit, offset, maximum=100):
        _require(type(limit) is int and 1 <= limit <= maximum and type(offset) is int and 0 <= offset <= MAX_RECORDS * 10,
                 "pagination outside bounds")
        page = items[offset:offset + limit]
        return {"items": page, "total": len(items), "next_offset": offset + limit if offset + limit < len(items) else None}

    def overview(self):
        return self._result(**self._overview)

    def search(self, query, limit=10, category=None, kind=None, offset=0):
        _require(type(query) is str and 0 < len(query) <= 512, "query outside bounds")
        for value in (category, kind):
            _require(value is None or type(value) is str and 0 < len(value) <= 120, "invalid search filter")
        terms = sorted(_search_tokens(query))
        _require(1 <= len(terms) <= 32, "query term count outside bounds")
        candidates = [self._postings.get(term, frozenset()) for term in terms]
        for field, value in (("category", category), ("kind", kind)):
            if value is not None:
                candidates.append(self._facets[field].get(value, frozenset()))
        candidates.sort(key=len)
        matches = set(candidates[0]).intersection(*candidates[1:])
        items = []
        for record_id in matches:
            record = self._records[record_id]
            score = sum(3 if term in self._title_terms[record_id] else 1 for term in terms)
            items.append({k: record[k] for k in ("record_id", "source_id", "title", "kind", "category", "source_url")} | {"score": score})
        items.sort(key=lambda x: (-x["score"], x["source_id"]))
        return self._result(**self._page(items, limit, offset), search_basis="indexed_metadata_tokens",
                            query_terms=terms, query_match="all_terms")

    def resolve(self, source_id):
        """Resolve only an exact retained source identity or explicit alias."""
        _require(type(source_id) is str and 0 < len(source_id) <= 512, "source identity required")
        records = self._identities.get(source_id, frozenset())
        _require(len(records) == 1, "source identity unavailable or ambiguous")
        return self._result(record=self._records[next(iter(records))])

    def source(self, record_id):
        return self._result(record=self._record(record_id))

    def record_bytes(self, record_id) -> bytes:
        return _encode({k: v for k, v in self._record(record_id).items() if k != "record_id"})

    def relations(self, record_id, limit=50, offset=0):
        record = self._record(record_id)
        source_id = record["source_id"]
        edges = [dict(relation) for relation in self._relation_adjacency.get(source_id, ())]
        for peer in self._by_duplicate.get(source_id, []):
            if peer != source_id:
                edges.append({"source_id": source_id, "target_source_id": peer, "kind": "equal_representation_bytes"})
        for membership in record["lot_memberships"]:
            lot = self._lots[membership["lot_id"]]
            edges.append({"source_id": source_id, "kind": "packaged_in", **membership,
                          "lot_sha256": lot["sha256"], "lot_url": lot["drive_url"],
                          "assertion_scope": "packaging_not_scientific_acceptance"})
        for edge in edges:
            target = self._sources.get(edge.get("target_source_id"))
            edge["target_record_id"] = target["record_id"] if target else None
        edges.sort(key=lambda x: (x["kind"], x.get("source_id", ""), x.get("target_source_id", ""), x.get("lot_id", "")))
        return self._result(**self._page(edges, limit, offset))

    def review_queue(self, limit=20, offset=0, kind=None):
        _require(kind is None or type(kind) is str and 0 < len(kind) <= 120, "invalid review filter")
        items = self._reviews if kind is None else self._reviews_by_kind.get(kind, [])
        return self._result(**self._page(items, limit, offset))
