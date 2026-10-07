# Preparing a reviewed corpus refresh

`python -m ciw.corpus_refresh --selection SELECTION.json --output NEW_DIRECTORY`
prepares a local, create-once snapshot through the existing `proto_corpus` and
`evidence_corpus` compilers. It does not update Drive, a running MCP binding,
provider availability, or earlier catalog/evidence bundles.

The host supplies a `ciw.corpus-refresh-selection.v1` JSON object containing
`authorizes_execution: false`, a `disclosure_ref`, and 1–64 explicit sources. Each
source names its `drive_file_id`, local `source_path`, original `intake_manifest`
and `verification` JSON paths, `review_extent`, `limitations`, and selected ZIP
`members`. The builder supports the two retained 2026-10-07 intake receipt shapes;
it refuses missing, failed or conflicting identity/hash/receipt assertions.

```json
{
  "schema": "ciw.corpus-refresh-selection.v1",
  "authorizes_execution": false,
  "disclosure_ref": "operator:local-reviewed-selection",
  "sources": [{
    "drive_file_id": "THE_RETAINED_DRIVE_ID",
    "source_path": "/local/provider.zip",
    "intake_manifest": "/local/intake_manifest.json",
    "verification": "/local/verification.json",
    "review_extent": "README and selected API inspected; code not executed",
    "limitations": ["Upstream commit and redistribution rights unresolved"],
    "members": ["provider/README.md", "provider/src/uncertainty.py"]
  }]
}
```

The output retains all original selected source bytes and intake/verification
JSON bytes. It independently checks SHA-256 and size, performs bounded full ZIP
CRC reads, and compiles metadata with the existing catalog schema. Original
Drive identities remain unchanged. `selection_mode: retained_original` records
selection without manufacturing a staged Drive copy, duplicate annotation or
self-copy relation. Existing selections without that field retain their prior
normalization and record identities.

Normalized receipt parent/name/move fields are **historical intake assertions**,
with their original receipt locator and batch identity retained. They do not
claim a fresh cloud metadata read or a new move. Original-byte hashes are
independently checked during this build. A successful CRC/hash check does not
qualify the source or execute it.

ZIP members become exact UTF-8 evidence with archive hash, member name, member
hash and bounded passage locators. PDF entries remain metadata-only: the existing
evidence compiler deliberately excludes PDF parsing. Previously extracted PDF
text must not be passed off as original PDF bytes. Curatorial paper summaries
and page references belong in explicitly labelled limitations, separate from
verbatim evidence. Source rights remain unresolved and general fulltext ingestion
approval remains false; local reading does not establish redistribution rights.

Useful output files:

- `receipt.json`: exact catalog/evidence references, verified sources and review scope.
- `provenance/`: exact selection and original intake/receipt JSON bytes, hash addressed.
- `originals/`: exact selected PDFs and ZIPs, named using their unchanged Drive IDs.
- `catalog/`, `evidence/`: frozen native NCP bundles.
- `evidence-selection.json`: reproducible local member selection after publication.

For the reviewed metrology selection, run
`scripts/check_corpus_refresh_mcp.py --refresh NEW_DIRECTORY --output NEW_RESULT.json`
with the `mcp-control` extra. This checks all source hashes and retained member
passage locators, then starts two real stdio servers and exercises SDK legacy
initialization and modern discovery. It queries PyDynamic's `None`/certainty
semantics, timing/sampling limitations, TECKit's observational restrictions,
metadata-only PDF exclusion and unresolved rights. It tests refusal of arbitrary
source paths and authorization tools. The generated `*-mcp-config.json` is an
explicit launch configuration, not an activated host change. Its environment
currently selects this checkout's source; regenerate against the promoted runtime
when qualification and operator host binding are completed.

Unit coverage uses synthetic documents and unexecuted source code. Local test
artifacts can contain private corpus bytes and must not be committed or publicly
uploaded with the source changes.
