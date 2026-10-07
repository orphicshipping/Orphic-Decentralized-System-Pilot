"""Host-selected exact UTF-8 evidence, linked to a pinned Proto-Corpus.

Read selected ZIP members with ZipFile.open(ZipInfo), never extract/extractall:
https://docs.python.org/3.12/library/zipfile.html#zipfile.ZipFile.open
Original archive hashes, selected text hashes and evidence record IDs differ.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import tempfile
import zipfile

from .proto_corpus import _encode, _parse, _read, _no_links, _ref

MAX_TEXT = 1024 * 1024
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_DOCUMENTS = 64
MAX_SPAN_BYTES = 1800
SPAN_RECIPE = {"method": "utf8-token-aware-windows.v1", "max_bytes": MAX_SPAN_BYTES,
               "paragraph_preference": True, "paragraph_minimum_bytes": 256,
               "tokenization": "unicode-alphanumeric-excluding-underscore-casefold.v1"}
MAX_MEMBERS = 10000
MAX_CENTRAL_DIRECTORY = 8 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_RATIO = 200
MAX_MANIFEST = 1024 * 1024
REF = re.compile(r"sha256:[0-9a-f]{64}")
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".ttl", ".rdf",
                   ".owl", ".n3", ".nt", ".nq", ".trig", ".yaml", ".yml", ".toml", ".ini",
                   ".cfg", ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".htm",
                   ".sh", ".ps1", ".c", ".h", ".cpp", ".hpp", ".rs", ".jl", ".gd", ".wgsl",
                   ".sql", ".tex", ".rst", ".r", ".m", ".f", ".f90", ".cmake"}
BOUNDARIES = {"data_role": "untrusted_retained_source_text", "metadata_only": False,
              "authorizes_execution": False, "authorizes_acceptance": False,
              "authorizes_source_disclosure": False, "scientific_adjudication": "not_performed",
              "source_authenticity_checked": False, "redistribution_license_granted": False,
              "code_executed": False, "embeddings_created": False,
              "snapshot_semantics": "frozen_selected_text_not_live_source",
              "original_sources_rehashed_at_open": False}
LIMITATIONS = ["Exact retained text is source-reported material, not accepted scientific evidence.",
               "Host-selected reading does not establish a redistribution license or source authenticity.",
               "Byte windows may omit surrounding conditions; inspect the complete selected representation.",
               "ZIP-member evidence preserves member bytes; its text hash is not the original archive hash."]


class EvidenceError(ValueError):
    """Bounded evidence selection, integrity or query refusal."""


def _require(condition, message):
    if not condition:
        raise EvidenceError(message)


def _pin(value):
    _require(type(value) is str and REF.fullmatch(value) is not None, "invalid evidence reference")
    return value


def _text(value, label, maximum=2048):
    _require(type(value) is str and 0 < len(value) <= maximum and "\x00" not in value,
             "invalid " + label)
    return value


def _utf8(raw):
    _require(type(raw) is bytes and len(raw) <= MAX_TEXT and b"\x00" not in raw,
             "selected text outside UTF-8 byte bounds")
    try:
        raw.decode("utf-8")  # Preserve BOMs, line endings and all code points verbatim.
    except UnicodeError as exc:
        raise EvidenceError("selected text is not UTF-8") from exc
    return raw


def _text_name(name):
    name = PurePosixPath(name).name
    _require(PurePosixPath(name).suffix.lower() in TEXT_EXTENSIONS or
             name.casefold() in {"readme", "license", "licence", "copying", "notice", "authors", "makefile", "dockerfile"},
             "unsupported text representation; PDF and binary parsing are not enabled")


def _member(name, *, directory=False):
    _text(name, "archive member")
    clean = name[:-1] if directory and name.endswith("/") else name
    _require(clean and "\\" not in clean and ":" not in clean and not clean.startswith("/")
             and all(p not in ("", ".", "..") for p in clean.split("/"))
             and all(ord(c) >= 32 for c in clean), "unsafe archive member")
    return name


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def _verified_source(path, record, maximum):
    """Hash and freeze one bounded ordinary source without retaining its path."""
    path = _no_links(path)
    expected_size = record["representation_size_bytes"]
    _require(type(expected_size) is int and 0 <= expected_size <= maximum, "source outside byte budget")
    before = path.stat()
    _require(stat.S_ISREG(before.st_mode) and before.st_size == expected_size, "source size mismatch")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as source, tempfile.SpooledTemporaryFile(max_size=MAX_TEXT) as frozen:
        opened = os.fstat(source.fileno())
        _require(stat.S_ISREG(opened.st_mode) and opened.st_size == expected_size and
                 (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                 "source changed before read")
        digest, size = sha256(), 0
        while True:
            chunk = source.read(128 * 1024)
            if not chunk:
                break
            size += len(chunk)
            _require(size <= maximum and size <= expected_size, "source grew outside byte budget")
            digest.update(chunk)
            frozen.write(chunk)
        _require(_identity(opened) == _identity(os.fstat(source.fileno())) and size == expected_size,
                 "source changed during read")
        _require(digest.hexdigest() == record["representation_sha256"], "source hash mismatch")
        frozen.seek(0)
        yield frozen



def _zip_preflight(source):
    """Bound directory allocation before ZipFile reads it or creates ZipInfos.

    Strict single-disk, non-ZIP64 subset of PKWARE APPNOTE 6.3.10, sections
    4.3.12, 4.3.15-16 and 4.4.1 (little-endian unsigned fields):
    https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
    CPython ZipFile reads size_cd and constructs its entire directory eagerly:
    https://github.com/python/cpython/blob/3.12/Lib/zipfile/__init__.py
    No decompression or member object construction happens in this preflight.
    """
    source.seek(0, os.SEEK_END)
    size = source.tell()
    tail_size = min(size, 22 + 65535)
    source.seek(size - tail_size)
    tail = source.read(tail_size)
    position = tail.rfind(b"PK\x05\x06")
    _require(position >= 0 and len(tail) - position >= 22, "invalid ZIP end record")
    _, disk, cd_disk, disk_count, count, cd_size, cd_offset, comment = struct.unpack_from("<4s4H2LH", tail, position)
    end_record = size - tail_size + position
    _require(position + 22 + comment == len(tail), "invalid ZIP end record length")
    _require(disk == cd_disk == 0 and disk_count == count, "multidisk ZIP refused")
    _require(count != 0xFFFF and cd_size != 0xFFFFFFFF and cd_offset != 0xFFFFFFFF, "ZIP64 refused")
    if end_record >= 20:
        source.seek(end_record - 20)
        _require(source.read(4) != b"PK\x06\x07", "ZIP64 refused")
    _require(count <= MAX_MEMBERS, "archive member count exceeded before allocation")
    _require(cd_size <= MAX_CENTRAL_DIRECTORY, "archive central directory byte budget exceeded")
    # Refuse inconsistent directory offsets and extension records here.
    _require(cd_offset + cd_size == end_record, "invalid standard ZIP directory bounds")
    cursor, observed = cd_offset, 0
    while cursor < end_record:
        _require(observed < count and end_record - cursor >= 46, "ZIP directory entry count mismatch")
        source.seek(cursor)
        header = source.read(46)
        _require(len(header) == 46 and header[:4] == b"PK\x01\x02", "invalid ZIP directory header")
        name_len, extra_len, comment_len, member_disk = struct.unpack_from("<4H", header, 28)
        compressed, expanded = struct.unpack_from("<2L", header, 20)
        local_offset = struct.unpack_from("<L", header, 42)[0]
        _require(member_disk == 0, "multidisk ZIP member refused")
        _require(0xFFFFFFFF not in (compressed, expanded, local_offset), "ZIP64 member refused")
        _require(0 < name_len <= 8192, "archive member name byte budget exceeded")
        next_cursor = cursor + 46 + name_len + extra_len + comment_len
        _require(next_cursor <= end_record, "invalid ZIP directory entry bounds")
        source.seek(cursor + 46 + name_len)
        extra = source.read(extra_len)
        offset = 0
        while offset < len(extra):
            _require(len(extra) - offset >= 4, "invalid ZIP extra field")
            field, length = struct.unpack_from("<HH", extra, offset)
            _require(field != 1, "ZIP64 extra field refused")
            offset += 4 + length
            _require(offset <= len(extra), "invalid ZIP extra field length")
        cursor, observed = next_cursor, observed + 1
    _require(observed == count and cursor == end_record, "ZIP directory entry count mismatch")
    source.seek(0)


def _zip_index(archive):
    infos = archive.infolist()
    _require(len(infos) <= MAX_MEMBERS, "archive member count exceeded")
    result, expanded = {}, 0
    for info in infos:
        _require(info.orig_filename == info.filename, "unsafe normalized or truncated archive member name")
        name = _member(info.filename, directory=info.is_dir())
        _require(name not in result, "duplicate archive member")
        mode = (info.external_attr >> 16) & 0xFFFF
        _require(not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                 "archive links or special files refused")
        _require(not info.flag_bits & 1, "encrypted archive refused")
        _require(info.file_size >= 0 and info.compress_size >= 0, "invalid archive sizes")
        expanded += info.file_size
        _require(expanded <= MAX_EXPANDED, "archive declared expansion budget exceeded")
        result[name] = info
    return result


def _zip_text(archive, index, member):
    _member(member)
    _text_name(member)
    _require(member in index, "selected archive member missing")
    info = index[member]
    _require(not info.is_dir() and info.file_size <= MAX_TEXT, "selected member outside byte budget")
    _require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "unsupported ZIP compression")
    _require(info.file_size <= max(info.compress_size, 1) * MAX_RATIO, "archive expansion ratio exceeded")
    with archive.open(info, "r") as source:
        raw = source.read(MAX_TEXT + 1)
    _require(len(raw) == info.file_size, "archive member size mismatch")
    return _utf8(raw)


def _record(catalog, record_id):
    _pin(record_id)
    record = catalog.source(record_id)["record"]
    _require(record.get("record_id") == record_id and _ref(catalog.record_bytes(record_id)) == record_id,
             "catalog record hash mismatch")
    _require(record.get("sha256_scope") == "original_stored_file_bytes" and
             record.get("representation") in ("original_bytes", "original_zip_archive") and
             record.get("source_sha256") is not None and
             record["source_sha256"] == record.get("representation_sha256"),
             "source has no supported original-byte binding")
    return record


def _descriptor(record, raw, member):
    kind = "utf8_file" if member is None else "zip_member"
    if member is None:
        _text_name(record["source_path"])
        _require(record["representation_size_bytes"] == len(raw) and
                 record["source_sha256"] == sha256(raw).hexdigest(), "plain text source binding mismatch")
    else:
        _member(member)
        _text_name(member)
    return {"record_id": record["record_id"], "source_id": record["source_id"],
            "source_sha256": record["source_sha256"], "source_size_bytes": record["representation_size_bytes"],
            "kind": kind, "archive_member": member, "text_hash": _ref(raw), "text_size_bytes": len(raw),
            "source_metadata": deepcopy(record), "limitations": list(LIMITATIONS),
            "recipe": {"method": "utf8-verbatim.v1" if member is None else "zip-member-utf8-verbatim.v1",
                       "normalization": "none", "decoder": "utf-8-strict",
                       "original_bytes_verified_at_compile": True,
                       "archive_crc_scope": "selected_member_only" if member is not None else "not_applicable"}}


def _windows(raw):
    start = 0
    while start < len(raw):
        end = min(start + MAX_SPAN_BYTES, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        if end < len(raw):
            boundary = raw.rfind(b"\n\n", start, end)
            if boundary >= start + 256:
                end = boundary + 2
            else:
                chunk = raw[start:end].decode("utf-8")
                following = raw[end:end + 4].decode("utf-8", errors="ignore")
                # Do not split a normal lexical token between searchable windows.
                if chunk and following and _tokens(chunk[-1]) and _tokens(following[0]):
                    separators = list(re.finditer(r"[\W_]+", chunk))
                    if separators:
                        end = start + len(chunk[:separators[-1].end()].encode("utf-8"))
        yield start, end, raw[start:end]
        start = end


def _tokens(value):
    return re.findall(r"[^\W_]+", value.casefold())


def compile_evidence(*, catalog, selection: Path, output: Path) -> str:
    """Compile explicit local source/member selections; original files stay intact."""
    _pin(catalog.ref)
    selected = _parse(_read(selection, MAX_MANIFEST))
    _require(type(selected) is dict and set(selected) == {"schema", "disclosure_ref", "sources"}
             and selected["schema"] == "ciw.evidence-selection.v1", "invalid evidence selection")
    disclosure = _text(selected["disclosure_ref"], "disclosure reference", 512)
    choices = selected["sources"]
    _require(type(choices) is list and 1 <= len(choices) <= MAX_DOCUMENTS, "selection count outside bounds")
    groups, seen = {}, set()
    for item in choices:
        _require(type(item) is dict and set(item) in ({"record_id", "path"}, {"record_id", "path", "member"}),
                 "invalid source selection fields")
        record = _record(catalog, item["record_id"])
        path = _no_links(Path(_text(item["path"], "source path", 4096)))
        member = item.get("member")
        if member is not None:
            _member(member)
        key = (item["record_id"], member)
        _require(key not in seen, "duplicate evidence selection")
        seen.add(key)
        group = groups.setdefault((item["record_id"], str(path)), {"record": record, "members": []})
        group["members"].append(member)
    documents, texts, total = [], {}, 0
    try:
        for (_, path), group in groups.items():
            record, members = group["record"], group["members"]
            _require(not (None in members and len(members) > 1), "mixed plain/archive selection")
            zipped = members[0] is not None
            if not zipped:
                _text_name(record["source_path"])
            with _verified_source(path, record, MAX_ARCHIVE if zipped else MAX_TEXT) as frozen:
                if zipped:
                    _zip_preflight(frozen)
                    with zipfile.ZipFile(frozen, "r") as archive:
                        index = _zip_index(archive)
                        _require(all(member in index for member in members), "selected archive member missing")
                        _require(sum(index[member].file_size for member in members) <= MAX_TOTAL - total, "total evidence byte budget exceeded")
                        loaded = [(member, _zip_text(archive, index, member)) for member in members]
                else:
                    loaded = [(None, _utf8(frozen.read(MAX_TEXT + 1)))]
            for member, raw in loaded:
                total += len(raw)
                _require(total <= MAX_TOTAL, "total evidence byte budget exceeded")
                descriptor = _descriptor(record, raw, member)
                representation_id = _ref(_encode(descriptor))
                text_file = "texts/" + representation_id[7:] + ".txt"
                documents.append({"representation_id": representation_id, "descriptor": descriptor, "text_file": text_file})
                texts[text_file] = raw
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise EvidenceError("source or selected archive member unavailable") from exc
    manifest = {"schema": "ciw.evidence-bundle.v1", "catalog_ref": catalog.ref,
                "disclosure_ref": disclosure, "documents": sorted(documents, key=lambda x: x["representation_id"]),
                "boundaries": BOUNDARIES, "span_recipe": SPAN_RECIPE}
    raw_manifest = _encode(manifest)
    _require(len(raw_manifest) <= MAX_MANIFEST, "evidence manifest outside byte bounds")
    output = _no_links(output)
    _require(not output.exists() and output.parent.is_dir(), "output must be new with existing parent")
    temporary = Path(tempfile.mkdtemp(prefix=".evidence-build-", dir=output.parent))
    try:
        (temporary / "texts").mkdir()
        for name, raw in texts.items():
            (temporary / name).write_bytes(raw)
        (temporary / "manifest.json").write_bytes(raw_manifest)
        pin = _ref(raw_manifest)
        EvidenceCorpus.open(temporary, pin, catalog)
        _require(not output.exists(), "output appeared during evidence compilation")
        temporary.rename(output)
        return pin
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


class EvidenceCorpus:
    """Frozen explicit representations; read APIs cannot choose paths or URLs."""
    @classmethod
    def open(cls, bundle, expected_ref, catalog):
        _pin(expected_ref)
        bundle = _no_links(bundle)
        manifest_raw = _read(bundle / "manifest.json", MAX_MANIFEST)
        _require(_ref(manifest_raw) == expected_ref, "evidence bundle pin mismatch")
        manifest = _parse(manifest_raw)
        _require(type(manifest) is dict and set(manifest) == {"schema", "catalog_ref", "disclosure_ref", "documents", "boundaries", "span_recipe"}
                 and manifest["schema"] == "ciw.evidence-bundle.v1" and manifest["catalog_ref"] == catalog.ref
                 and manifest["boundaries"] == BOUNDARIES and manifest["span_recipe"] == SPAN_RECIPE,
                 "evidence/catalog binding mismatch")
        _text(manifest["disclosure_ref"], "disclosure reference", 512)
        documents = manifest["documents"]
        _require(type(documents) is list and 1 <= len(documents) <= MAX_DOCUMENTS, "evidence document count outside bounds")
        self = cls()
        self.ref, self.catalog_ref = expected_ref, catalog.ref
        self._disclosure_ref = manifest["disclosure_ref"]
        self._docs, self._raw, self._spans, self._by_rep = {}, {}, {}, defaultdict(list)
        seen_sources, total = set(), 0
        for document in documents:
            _require(type(document) is dict and set(document) == {"representation_id", "descriptor", "text_file"}, "invalid evidence document")
            rid, descriptor = document["representation_id"], document["descriptor"]
            _pin(rid)
            _require(type(descriptor) is dict and rid == _ref(_encode(descriptor)) and rid not in self._docs, "representation identity mismatch")
            _require(document["text_file"] == "texts/" + rid[7:] + ".txt", "invalid evidence text member")
            record = _record(catalog, descriptor.get("record_id"))
            path = _no_links(bundle / document["text_file"])
            size = path.stat().st_size
            _require(type(descriptor.get("text_size_bytes")) is int and 0 <= size <= MAX_TEXT and
                     descriptor["text_size_bytes"] == size, "evidence text size mismatch")
            # _verified_source also accepts empty ordinary files, unlike metadata readers.
            text_record = {"representation_size_bytes": size, "representation_sha256": _pin(descriptor.get("text_hash"))[7:]}
            with _verified_source(path, text_record, MAX_TEXT) as source:
                raw = _utf8(source.read(MAX_TEXT + 1))
            expected = _descriptor(record, raw, descriptor.get("archive_member"))
            _require(descriptor == expected, "evidence descriptor/catalog reconciliation failed")
            key = (record["record_id"], descriptor["archive_member"])
            _require(key not in seen_sources, "duplicate evidence source/member")
            seen_sources.add(key)
            total += len(raw)
            _require(total <= MAX_TOTAL, "total evidence byte budget exceeded")
            self._docs[rid], self._raw[rid] = deepcopy(descriptor), raw
            for start, end, window in _windows(raw):
                locator = {"byte_start": start, "byte_end_exclusive": end, "span_sha256": _ref(window)}
                span_id = _ref(_encode({"representation_id": rid, "locator": locator}))
                span = {"span_id": span_id, "representation_id": rid, "record_id": record["record_id"],
                        "source_id": record["source_id"], "source_url": record["source_url"],
                        "text": window.decode("utf-8"), "text_hash": descriptor["text_hash"],
                        "archive_member": descriptor["archive_member"], "locator": locator}
                self._spans[span_id] = span
                self._by_rep[rid].append(span_id)
        _require(list(self._docs) == sorted(self._docs), "evidence manifest document order mismatch")
        self._terms, self._postings = {}, defaultdict(set)
        for sid, span in self._spans.items():
            terms = Counter(_tokens(span["text"]))
            self._terms[sid] = terms
            for term in terms:
                self._postings[term].add(sid)
        return self

    def _result(self, **values):
        return deepcopy({"evidence_ref": self.ref, "catalog_ref": self.catalog_ref, **values, **BOUNDARIES})

    @staticmethod
    def _page(items, limit, offset):
        _require(type(limit) is int and 1 <= limit <= 50 and type(offset) is int and 0 <= offset <= 200000, "invalid evidence pagination")
        return {"items": items[offset:offset + limit], "total": len(items),
                "next_offset": offset + limit if offset + limit < len(items) else None}

    def overview(self):
        return self._result(representations=len(self._docs), source_records=len({d["record_id"] for d in self._docs.values()}),
                            text_bytes=sum(map(len, self._raw.values())), spans=len(self._spans),
                            disclosure_ref=self._disclosure_ref, limitations=LIMITATIONS,
                            search_method="indexed_exact_tokens_AND_not_embeddings", span_recipe=SPAN_RECIPE)

    def representations(self, record_id=None, limit=20, offset=0):
        if record_id is not None:
            _pin(record_id)
        items = []
        for rid, descriptor in self._docs.items():
            if record_id is not None and descriptor["record_id"] != record_id:
                continue
            record = descriptor["source_metadata"]
            items.append({"representation_id": rid, "record_id": descriptor["record_id"],
                          "source_id": descriptor["source_id"], "source_url": record["source_url"],
                          "archive_member": descriptor["archive_member"], "text_hash": descriptor["text_hash"],
                          "text_size_bytes": descriptor["text_size_bytes"],
                          "span_count": len(self._by_rep[rid]), "title": record["title"]})
        return self._result(**self._page(items, limit, offset))

    def search(self, query, limit=10, offset=0, record_id=None):
        _require(type(query) is str and 0 < len(query) <= 512, "invalid evidence query")
        terms = set(_tokens(query))
        _require(0 < len(terms) <= 32, "query requires bounded lexical tokens")
        if record_id is not None:
            _pin(record_id)
        matches = set.intersection(*(self._postings.get(term, set()) for term in terms))
        hits = []
        for sid in matches:
            span = self._spans[sid]
            if record_id is not None and span["record_id"] != record_id:
                continue
            hit = {k: span[k] for k in ("span_id", "representation_id", "record_id", "source_id", "source_url", "text_hash", "archive_member", "locator")}
            hit.update(score=sum(self._terms[sid][term] for term in terms), excerpt=span["text"][:500],
                       excerpt_is_truncated=len(span["text"]) > 500)
            hits.append(hit)
        hits.sort(key=lambda x: (-x["score"], x["span_id"]))
        return self._result(**self._page(hits, limit, offset), search_method="indexed_exact_tokens_AND_not_embeddings",
                            score_is_confidence=False)

    def source(self, representation_id, offset=0, limit=20):
        _pin(representation_id)
        _require(representation_id in self._docs, "evidence representation unavailable")
        spans = [{k: self._spans[sid][k] for k in ("span_id", "representation_id", "record_id", "locator")}
                 for sid in self._by_rep[representation_id]]
        return self._result(representation_id=representation_id, descriptor=self._docs[representation_id],
                            source_metadata=self._docs[representation_id]["source_metadata"],
                            **self._page(spans, limit, offset))

    def span(self, span_id):
        _pin(span_id)
        _require(span_id in self._spans, "evidence span unavailable")
        span = self._spans[span_id]
        return self._result(span=span, source_metadata=self._docs[span["representation_id"]]["source_metadata"], limitations=LIMITATIONS)

    def span_bytes(self, span_id):
        _pin(span_id)
        _require(span_id in self._spans, "evidence span unavailable")
        span = self._spans[span_id]
        location = span["locator"]
        return self._raw[span["representation_id"]][location["byte_start"]:location["byte_end_exclusive"]]

    def bind_context(self, store, *, span_id, parent_ref, disclosure_ref):
        from .external_work import text
        text(parent_ref)
        text(disclosure_ref)
        span = self.span(span_id)["span"]
        rid = span["representation_id"]
        descriptor, raw = self._docs[rid], self._raw[rid]
        context_id = store.retain_context(parent_ref=parent_ref, snapshot_ref=self.ref,
            source_id="urn:notation:evidence-representation:" + rid[7:], version_ref=descriptor["text_hash"],
            source_bytes=raw, expected_source_hash=descriptor["text_hash"],
            start=span["locator"]["byte_start"], end=span["locator"]["byte_end_exclusive"],
            provenance={"representation_id": rid, "span_id": span_id, "catalog_ref": self.catalog_ref,
                "record_id": descriptor["record_id"], "original_source_id": descriptor["source_id"],
                "original_source_sha256": descriptor["source_sha256"], "text_hash": descriptor["text_hash"],
                "archive_member": descriptor["archive_member"], "recipe": descriptor["recipe"],
                "span_recipe": SPAN_RECIPE,
                "source_metadata": descriptor["source_metadata"], "limitations": descriptor["limitations"],
                "disclosure_ref": disclosure_ref, "compile_disclosure_ref": self._disclosure_ref,
                "locator_semantics": "UTF-8 byte offsets in selected text, not original ZIP offsets",
                "scientific_adjudication": "not_performed", "semantic_support_checked": False})
        return {"context_id": context_id, "representation_id": rid, "span_id": span_id,
                "text_hash": descriptor["text_hash"], "original_source_sha256": descriptor["source_sha256"],
                "authorizes_execution": False, "scientific_adjudication": "not_performed"}
