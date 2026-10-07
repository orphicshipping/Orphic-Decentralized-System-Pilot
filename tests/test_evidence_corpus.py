"""Exact selected-text and ZIP evidence tests; all sources are synthetic."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import stat
import struct
import warnings
import zipfile

import pytest

from ciw import evidence_corpus as evidence
from ciw.external_work import ExternalWorkStore
from ciw.proto_corpus import ProtoCorpus, compile_bundle
from test_proto_corpus import make_inputs


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


def zip_bytes(entries, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            for name, raw in entries:
                archive.writestr(name, raw)
    return output.getvalue()


def build(tmp_path, raw=b"Calibration requires a measured reference.\r\n", filename="note.md", members=None):
    catalog, record_id, path = make_catalog(tmp_path, raw, filename)
    entries = [{"record_id": record_id, "path": str(path)}]
    if members is not None:
        entries = [{"record_id": record_id, "path": str(path), "member": member} for member in members]
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"schema": "ciw.evidence-selection.v1", "disclosure_ref": "fixture:approved-local-reading", "sources": entries}))
    output = tmp_path / "evidence"
    pin = evidence.compile_evidence(catalog=catalog, selection=selection, output=output)
    return evidence.EvidenceCorpus.open(output, pin, catalog), catalog, record_id, path, selection, output


def first_span(corpus):
    return corpus.search("calibration")["items"][0]["span_id"]


def test_exact_plain_text_utf8_windows_reconstruct_every_byte(tmp_path):
    raw = b"\xef\xbb\xbf" + ("Calibration \u03bb \U0001f9ea\r\n" * 240).encode()
    corpus, catalog, record_id, path, selection, output = build(tmp_path, raw)
    assert corpus.catalog_ref == catalog.ref
    assert corpus.overview()["representations"] == 1
    assert corpus.overview()["spans"] > 1
    span = corpus.span(first_span(corpus))["span"]
    rid = span["representation_id"]
    pieces, offset = [], 0
    while True:
        page = corpus.source(rid, offset=offset, limit=1)
        for item in page["items"]:
            value = corpus.span(item["span_id"])["span"]
            piece = corpus.span_bytes(item["span_id"])
            assert piece == value["text"].encode()
            assert evidence._ref(piece) == value["locator"]["span_sha256"]
            assert len(piece) <= evidence.MAX_SPAN_BYTES
            pieces.append(piece)
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert b"".join(pieces) == raw
    assert path.read_bytes() == raw
    assert str(path).encode() not in (output / "manifest.json").read_bytes()
    assert span["record_id"] == record_id
    assert span["text_hash"] == evidence._ref(raw)


def test_zip_members_retain_archive_and_member_identities_and_one_source_read(tmp_path, monkeypatch):
    calls = []
    original = evidence._verified_source
    @contextmanager
    def checked(path, record, maximum):
        if Path(path).suffix == ".zip":
            calls.append(path)
        with original(path, record, maximum) as stream:
            yield stream
    monkeypatch.setattr(evidence, "_verified_source", checked)
    text = b"Calibration uses explicitly unqualified synthetic observations.\n"
    archive = zip_bytes([("repo/README.md", text), ("repo/model.py", b"raise RuntimeError('NEVER_EXECUTE')\n")])
    corpus, catalog, record_id, path, _, output = build(tmp_path, archive, "source.zip", ["repo/README.md", "repo/model.py"])
    assert len(calls) == 1
    assert corpus.overview()["representations"] == 2
    hit = corpus.search("calibration")["items"][0]
    value = corpus.source(hit["representation_id"])["descriptor"]
    assert value["source_sha256"] == sha256(archive).hexdigest()
    assert value["text_hash"] == evidence._ref(text)
    assert value["text_hash"] != "sha256:" + value["source_sha256"]
    assert value["archive_member"] == "repo/README.md"
    assert value["source_metadata"]["selected_annotations"][0]["rights_status"] == "Rights review required"
    assert corpus.search("NEVER_EXECUTE")["total"] == 1
    assert not (tmp_path / "repo").exists()
    assert path.read_bytes() == archive


def test_search_uses_exact_tokens_and_preserves_quotes_and_defensive_results(tmp_path):
    corpus, *_ = build(tmp_path, b"Calibration senses polyethylene, not PE. Optical PP is source reported.\n")
    assert corpus.search("polyethylene")["total"] == 1
    assert corpus.search("polyeth")["total"] == 0
    assert corpus.search("source reported")["total"] == 1
    assert corpus.search("absent")["total"] == 0
    assert corpus.search("calibration", record_id="sha256:" + "f" * 64)["total"] == 0
    value = corpus.span(first_span(corpus))
    value["span"]["text"] = "tamper"
    value["source_metadata"]["title"] = "tamper"
    assert corpus.span(first_span(corpus))["span"]["text"] != "tamper"
    assert corpus.source(value["span"]["representation_id"])["source_metadata"]["title"] != "tamper"


def test_pagination_and_empty_text_are_explicit(tmp_path):
    corpus, *_ = build(tmp_path, b"Calibration\n" * 500)
    all_hits = corpus.search("calibration", limit=50)
    first = corpus.search("calibration", limit=1)
    assert first["items"] + corpus.search("calibration", offset=1, limit=50)["items"] == all_hits["items"]
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    empty, *_ = build(empty_dir, b"")
    assert empty.overview()["text_bytes"] == empty.overview()["spans"] == 0
    assert empty.overview()["representations"] == 1
    assert empty.search("calibration")["items"] == []


@pytest.mark.parametrize("arguments", [{"query": ""}, {"query": "!!!"}, {"query": "x" * 513},
    {"query": "calibration", "limit": True}, {"query": "calibration", "limit": 51},
    {"query": "calibration", "offset": -1}, {"query": "calibration", "offset": True},
    {"query": "calibration", "record_id": "../../source"}])
def test_invalid_search_refuses(tmp_path, arguments):
    corpus, *_ = build(tmp_path)
    with pytest.raises(ValueError):
        corpus.search(**arguments)


def test_pin_catalog_and_text_tampering_refuse(tmp_path):
    corpus, catalog, _, path, _, output = build(tmp_path)
    with pytest.raises(ValueError, match="pin"):
        evidence.EvidenceCorpus.open(output, "sha256:" + "f" * 64, catalog)
    other = deepcopy(catalog)
    other.ref = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="binding"):
        evidence.EvidenceCorpus.open(output, corpus.ref, other)
    path.write_bytes(b"mutated original")
    assert evidence.EvidenceCorpus.open(output, corpus.ref, catalog).span(first_span(corpus))["span"]["text"].startswith("Calibration")
    text_file = next((output / "texts").iterdir())
    text_file.write_bytes(text_file.read_bytes().replace(b"Calibration", b"CALIBRATION"))
    with pytest.raises(ValueError, match="hash"):
        evidence.EvidenceCorpus.open(output, corpus.ref, catalog)


def test_forged_descriptor_refuses_even_with_new_bundle_pin(tmp_path):
    corpus, catalog, _, _, _, output = build(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["documents"][0]["descriptor"]["source_metadata"]["rights_review"] = "approved"
    descriptor = manifest["documents"][0]["descriptor"]
    old = output / manifest["documents"][0]["text_file"]
    rid = evidence._ref(evidence._encode(descriptor))
    manifest["documents"][0].update(representation_id=rid, text_file="texts/" + rid[7:] + ".txt")
    (output / manifest["documents"][0]["text_file"]).write_bytes(old.read_bytes())
    raw = evidence._encode(manifest)
    manifest_path.write_bytes(raw)
    with pytest.raises(ValueError, match="reconciliation"):
        evidence.EvidenceCorpus.open(output, evidence._ref(raw), catalog)


@pytest.mark.parametrize("mutate", [lambda p: p.write_bytes(b"X" * p.stat().st_size), lambda p: p.write_bytes(b"too short")])
def test_changed_original_refuses_before_output(tmp_path, mutate):
    corpus, catalog, _, path, selection, _ = build(tmp_path)
    mutate(path)
    output = tmp_path / "new-evidence"
    with pytest.raises(ValueError):
        evidence.compile_evidence(catalog=catalog, selection=selection, output=output)
    assert not output.exists()


@pytest.mark.parametrize("raw,filename", [(b"\xff\xfe", "note.md"), (b"hello\0", "note.md"),
    (b"%PDF-1.7 ASCII-only fixture", "paper.pdf"), (b"x" * (evidence.MAX_TEXT + 1), "note.md")], ids=["invalid-utf8", "nul", "pdf", "oversized"])
def test_unsupported_or_oversized_plain_text_refuses(tmp_path, raw, filename):
    with pytest.raises(ValueError):
        build(tmp_path, raw, filename)


@pytest.mark.parametrize("bad", ["../outside.txt", "/absolute.txt", "C:/drive.txt", "dir\\escape.txt", "dir/./note.txt"])
def test_unsafe_zip_neighbors_refuse_without_extraction(tmp_path, bad):
    # ZipInfo normalizes Windows separators at creation; mutate both headers so
    # the test archive really contains the unsafe name on every platform.
    portable_name = bad.replace("\\", "/")
    raw = zip_bytes([("README.md", b"Calibration"), (portable_name, b"unselected")])
    raw = raw.replace(portable_name.encode(), bad.encode())
    with pytest.raises(ValueError, match="unsafe"):
        build(tmp_path, raw, "source.zip", ["README.md"])
    assert not (tmp_path / "evidence").exists()


def test_duplicate_encrypted_and_symlink_zip_entries_refuse(tmp_path):
    duplicate = zip_bytes([("README.md", b"first"), ("README.md", b"second")])
    with pytest.raises(ValueError, match="duplicate"):
        build(tmp_path, duplicate, "source.zip", ["README.md"])
    encrypted = bytearray(zip_bytes([("README.md", b"Calibration")]))
    encrypted[6] |= 1
    central = encrypted.index(b"PK\x01\x02")
    encrypted[central + 8] |= 1
    second = tmp_path / "encrypted"
    second.mkdir()
    with pytest.raises(ValueError, match="encrypted"):
        build(second, bytes(encrypted), "source.zip", ["README.md"])
    info = zipfile.ZipInfo("link.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    third = tmp_path / "link"
    third.mkdir()
    with pytest.raises(ValueError, match="links"):
        build(third, zip_bytes([("README.md", b"Calibration"), (info, b"target")]), "source.zip", ["README.md"])


def test_zip_member_size_ratio_and_total_expansion_bounds(tmp_path, monkeypatch):
    raw = zip_bytes([("README.md", b"a" * 50000)], zipfile.ZIP_DEFLATED)
    with pytest.raises(ValueError, match="ratio"):
        build(tmp_path, raw, "source.zip", ["README.md"])
    second = tmp_path / "member"
    second.mkdir()
    with pytest.raises(ValueError, match="byte budget"):
        build(second, zip_bytes([("README.md", b"x" * (evidence.MAX_TEXT + 1))]), "source.zip", ["README.md"])
    third = tmp_path / "expanded"
    third.mkdir()
    monkeypatch.setattr(evidence, "MAX_EXPANDED", 10)
    with pytest.raises(ValueError, match="expansion budget"):
        build(third, zip_bytes([("README.md", b"Calibration")]), "source.zip", ["README.md"])


def test_selection_does_not_permit_extra_fields_duplicates_or_native_exports(tmp_path):
    corpus, catalog, record_id, _, selection, _ = build(tmp_path)
    selected = json.loads(selection.read_text())
    for sources in [selected["sources"] * 2, selected["sources"] * 65,
                    [{**selected["sources"][0], "execute": True}]]:
        selection.write_text(json.dumps({**selected, "sources": sources}))
        with pytest.raises(ValueError):
            evidence.compile_evidence(catalog=catalog, selection=selection, output=tmp_path / "new")
    exported = catalog.resolve("gdrive:Sheet")["record"]["record_id"]
    selection.write_text(json.dumps({**selected, "sources": [{**selected["sources"][0], "record_id": exported}]}))
    with pytest.raises(ValueError, match="original-byte"):
        evidence.compile_evidence(catalog=catalog, selection=selection, output=tmp_path / "new")


def test_source_symlink_guard_and_existing_output_refuse(tmp_path, monkeypatch):
    corpus, catalog, _, path, selection, output = build(tmp_path)
    before = (output / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="new"):
        evidence.compile_evidence(catalog=catalog, selection=selection, output=output)
    assert before == (output / "manifest.json").read_bytes()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == path or original(p))
    with pytest.raises(ValueError, match="symlink"):
        evidence.compile_evidence(catalog=catalog, selection=selection, output=tmp_path / "new")


def test_binding_preserves_full_member_hash_and_separate_task_authorization(tmp_path):
    raw = b"Calibration is source reported.\n" * 150
    archive = zip_bytes([("README.md", raw)])
    corpus, catalog, record_id, *_ = build(tmp_path, archive, "source.zip", ["README.md"])
    store = ExternalWorkStore(tmp_path / "tasks.sqlite3")
    for role in ("requester", "worker"):
        store.provision_client(role, role=role, parent_ref="intent:fixture")
    span_id = first_span(corpus)
    receipt = corpus.bind_context(store, span_id=span_id, parent_ref="intent:fixture", disclosure_ref="fixture:task-disclosure")
    task = store.propose("requester", request_key="evidence", task={"parent_ref": "intent:fixture", "instruction": "Review retained source claim", "expected_output": "Candidate with limitations", "context_ids": [receipt["context_id"]]})
    retained = store.context("requester", task_id=task["task_id"], context_id=receipt["context_id"])
    assert retained["source_hash"] == evidence._ref(raw)
    assert retained["source_hash"] != "sha256:" + sha256(archive).hexdigest()
    assert retained["source_id"].startswith("urn:notation:evidence-representation:")
    assert retained["provenance"]["original_source_id"] == "gdrive:Original"
    assert retained["provenance"]["original_source_sha256"] == sha256(archive).hexdigest()
    assert retained["provenance"]["source_metadata"]["rights_review"] == "unresolved"
    assert retained["text"].encode() == corpus.span_bytes(span_id)
    assert retained["snapshot_ref"] == corpus.ref
    assert task["authorization"] is None and store.discover("worker")["items"] == []


def test_span_boundaries_preserve_normal_lexical_tokens(tmp_path):
    raw = b"A" * 1795 + b" thermocouple reading. " + ("\u03bb" * 895 + " thermometer output.").encode()
    corpus, *_ = build(tmp_path, raw)
    assert corpus.search("thermocouple")["total"] == 1
    assert corpus.search("thermometer")["total"] == 1
    rid = corpus.representations()["items"][0]["representation_id"]
    spans = corpus.source(rid, limit=50)["items"]
    assert b"".join(corpus.span_bytes(item["span_id"]) for item in spans) == raw
    assert all(len(corpus.span_bytes(item["span_id"])) <= evidence.MAX_SPAN_BYTES for item in spans)


def test_representations_discover_empty_text_and_filter_catalog_record(tmp_path):
    corpus, _, record_id, *_ = build(tmp_path, b"")
    page = corpus.representations(record_id=record_id, limit=1)
    assert page["total"] == 1 and page["next_offset"] is None
    item = page["items"][0]
    assert item["span_count"] == item["text_size_bytes"] == 0
    assert item["record_id"] == record_id
    assert corpus.source(item["representation_id"])["items"] == []
    assert corpus.representations(record_id="sha256:" + "f" * 64)["total"] == 0
    assert corpus.representations(offset=1)["items"] == []
    for arguments in ({"limit": True}, {"offset": -1}, {"record_id": "invalid"}):
        with pytest.raises(ValueError):
            corpus.representations(**arguments)


def test_span_recipe_is_pinned_and_validated_at_open(tmp_path):
    corpus, catalog, _, _, _, output = build(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["span_recipe"] == evidence.SPAN_RECIPE
    assert corpus.overview()["span_recipe"] == evidence.SPAN_RECIPE
    manifest["span_recipe"]["method"] = "unversioned-different-windows"
    raw = evidence._encode(manifest)
    manifest_path.write_bytes(raw)
    with pytest.raises(ValueError, match="binding"):
        evidence.EvidenceCorpus.open(output, evidence._ref(raw), catalog)


@pytest.mark.parametrize("case", ["member-count", "directory-bytes", "count-underreported", "count-overreported",
    "multidisk", "member-multidisk", "zip64-count", "zip64-locator", "zip64-member", "zip64-extra",
    "truncated-end", "comment-length", "directory-offset", "entry-length", "invalid-header", "long-name" ])
def test_zip_directory_preflight_refuses_before_zipfile_allocation(tmp_path, monkeypatch, case):
    info = zipfile.ZipInfo("README.md")
    if case == "zip64-extra":
        info.extra = struct.pack("<HHQ", 1, 8, 11)
    raw = bytearray(zip_bytes([(info, b"Calibration"), ("notes.md", b"Unselected")]))
    end = raw.rfind(b"PK\x05\x06")
    central = raw.find(b"PK\x01\x02")
    if case == "member-count":
        monkeypatch.setattr(evidence, "MAX_MEMBERS", 1)
    elif case == "directory-bytes":
        monkeypatch.setattr(evidence, "MAX_CENTRAL_DIRECTORY", 1)
    elif case in {"count-underreported", "count-overreported", "zip64-count"}:
        count = {"count-underreported": 1, "count-overreported": 3, "zip64-count": 0xFFFF}[case]
        struct.pack_into("<HH", raw, end + 8, count, count)
    elif case == "multidisk":
        struct.pack_into("<H", raw, end + 4, 1)
    elif case == "member-multidisk":
        struct.pack_into("<H", raw, central + 34, 1)
    elif case == "zip64-locator":
        raw[end:end] = b"PK\x06\x07" + bytes(16)
    elif case == "zip64-member":
        struct.pack_into("<L", raw, central + 42, 0xFFFFFFFF)
    elif case == "truncated-end":
        del raw[-3:]
    elif case == "comment-length":
        struct.pack_into("<H", raw, end + 20, 1)
    elif case == "directory-offset":
        struct.pack_into("<L", raw, end + 16, central + 1)
    elif case == "entry-length":
        struct.pack_into("<H", raw, central + 32, 0xFFFF)
    elif case == "invalid-header":
        raw[central:central + 4] = b"BAD!"
    elif case == "long-name":
        struct.pack_into("<H", raw, central + 28, 8193)
    def forbidden_allocation(*args, **kwargs):
        raise AssertionError("ZipFile allocated before bounded preflight refusal")
    monkeypatch.setattr(evidence.zipfile, "ZipFile", forbidden_allocation)
    with pytest.raises(evidence.EvidenceError):
        build(tmp_path, bytes(raw), "source.zip", ["README.md"])
    assert not (tmp_path / "evidence").exists()


def test_zip_directory_preflight_accepts_bounded_comments_and_extra_fields(tmp_path):
    info = zipfile.ZipInfo("README.md")
    info.extra = struct.pack("<HH", 0xCAFE, 3) + b"abc"
    info.comment = b"untrusted member comment"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(info, b"Calibration uses a reference.")
        archive.comment = b"comment " * 8191
    corpus, *_ = build(tmp_path, stream.getvalue(), "source.zip", ["README.md"])
    assert corpus.search("calibration")["total"] == 1


@pytest.mark.parametrize('scope,path,value', [
    ('manifest', ('boundaries', 'authorizes_execution'), 0),
    ('manifest', ('span_recipe', 'paragraph_preference'), 1),
    ('manifest', ('span_recipe', 'max_bytes'), 1800.0),
    ('descriptor', ('recipe', 'original_bytes_verified_at_compile'), 1),
    ('descriptor', ('source_metadata', 'metadata_only'), 1),
    ('descriptor', ('source_metadata', 'authorizes_execution'), 0),
])
def test_resealed_numeric_aliases_cannot_change_typed_evidence_claims(tmp_path, scope, path, value):
    _, catalog, _, _, _, output = build(tmp_path)
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    document = manifest['documents'][0]
    target = manifest if scope == 'manifest' else document['descriptor']
    target[path[0]][path[1]] = value
    if scope == 'descriptor':
        original = (output / document['text_file']).read_bytes()
        rid = evidence._ref(evidence._encode(document['descriptor']))
        document.update(representation_id=rid, text_file='texts/' + rid[7:] + '.txt')
        (output / document['text_file']).write_bytes(original)
    raw = evidence._encode(manifest)
    manifest_path.write_bytes(raw)
    # A caller-supplied new root pin does not make differently typed claims
    # identical to the original catalog and the fixed extraction recipe.
    with pytest.raises(ValueError, match='binding|reconciliation'):
        evidence.EvidenceCorpus.open(output, evidence._ref(raw), catalog)
