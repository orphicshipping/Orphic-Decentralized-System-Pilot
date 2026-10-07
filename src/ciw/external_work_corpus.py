"""Host-only binding from the retained corpus to bounded task context.

Preserve native source identities, exact byte windows and unresolved findings.
No caller-controlled path or corpus-wide disclosure tool is exposed through MCP.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from pathlib import Path

from .control_contracts import bytes_ref
from .corpus_retrieval import Corpus
from .external_work import require, text


def bind_span(store, *, bundle: Path, expected_ref: str, span_id: str,
              parent_ref: str, disclosure_ref: str):
    text(disclosure_ref)
    corpus = Corpus.open(bundle, expected_ref=expected_ref)
    retrieved = corpus.span(span_id)
    span = retrieved["span"]
    selection_id = span["selection_id"]
    source = corpus.source(selection_id)
    record = source["source"]
    pieces = []
    offset = 0
    while True:
        page = corpus.source(selection_id, offset, 50)
        pieces.extend(corpus.span(item["span_id"])["span"]["text"].encode("utf-8")
                      for item in page["spans"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    raw = b"".join(pieces)
    expected_source = "sha256:" + record["snapshot"]["sha256"]
    require(bytes_ref(raw) == expected_source, "source_reconstruction_failed")
    locator = span["locator"]
    require(raw[locator["byte_start"]:locator["byte_end_exclusive"]] == span["text"].encode(),
            "span_reconstruction_failed")
    return store.retain_context(
        parent_ref=parent_ref, snapshot_ref=expected_ref, source_id=record["source_id"],
        version_ref=expected_source, source_bytes=raw, expected_source_hash=expected_source,
        start=locator["byte_start"], end=locator["byte_end_exclusive"],
        provenance={"selection_id": selection_id, "span_id": span_id,
                    "source_url": retrieved["source_url"], "disclosure_ref": disclosure_ref,
                    "findings": retrieved["findings"],
                    "limitations": record["curation"].get("limitations_to_preserve", []),
                    "semantic_support_checked": False, "scientific_adjudication": "not_performed"})
