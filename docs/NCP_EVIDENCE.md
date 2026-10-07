# Selected source evidence over MCP

The evidence profile adds exact source passages to the pinned Proto-Corpus catalog.
A host explicitly selects local UTF-8 files or named UTF-8 members of a ZIP archive.
NCP verifies the original stored bytes against the catalog, freezes only the
selected text, and exposes bounded passages with independent provenance.

This supports engineering review of source warnings, declared dependencies,
proposed vocabulary and historical reports. It does not turn those statements
into accepted engineering facts or installed-system measurements.

## Compile a selected evidence snapshot

First build the catalog using [NCP_SYSTEM.md](NCP_SYSTEM.md). Create a private
selection file with exact record IDs returned by catalog discovery:

~~~json
{
  "schema": "ciw.evidence-selection.v1",
  "disclosure_ref": "operator:verified-internal-analysis-decision",
  "sources": [
    {
      "record_id": "sha256:CATALOG_RECORD_DIGEST",
      "path": "/approved/source-repository.zip",
      "member": "source-repository/README.md"
    }
  ]
}
~~~

For a plain UTF-8 file, omit member. Selection paths are trusted operator inputs;
they are never accepted as model tool arguments. The disclosure reference records
a decision established by the host; the string itself is not a credential or
license adjudication.

~~~sh
ncp evidence build --catalog results/catalog --catalog-ref sha256:CATALOG_PIN \
  --selection results/private-selection.json --output results/evidence
ncp evidence status --catalog results/catalog --catalog-ref sha256:CATALOG_PIN \
  --bundle results/evidence --expected-ref sha256:EVIDENCE_PIN
ncp evidence search --catalog results/catalog --catalog-ref sha256:CATALOG_PIN \
  --bundle results/evidence --expected-ref sha256:EVIDENCE_PIN --query "not validated"
~~~

All digest placeholders stand for complete 64-character hexadecimal values.
Retain the returned evidence pin independently. Reopening checks the pinned
manifest, selected text digests, derivation descriptors and source linkage against
the exact catalog. It does not re-read the live originals.

## Representation and citation identities

| Identity | What it identifies |
| --- | --- |
| Catalog record ID | Normalized source metadata |
| Original source hash | Verified stored file or ZIP archive bytes |
| Representation ID | Source linkage, extraction recipe and selected text descriptor |
| Text hash | Entire selected UTF-8 file or archive member |
| Span ID | Representation plus exact byte locator |
| Span hash | Exact bytes of one returned passage |

No newline, Unicode or whitespace normalization changes the selected text.
Byte offsets apply to the selected UTF-8 text, not compressed ZIP offsets.
A span ID is not the hash of its text. Source URLs, source-reported limitations,
rights observations and unresolved catalog metadata remain attached.

Search uses indexed tokens with AND semantics and stable pagination. A paginated
representation inventory links exact catalog records to their selected text,
including empty files. The evidence pin also fixes the passage-window recipe. Results
include a bounded preview; use span to read the complete cited window and source
to inspect adjacent windows. Scores are lexical ranking, not confidence.

~~~sh
ncp evidence representations --catalog results/catalog --catalog-ref sha256:CATALOG_PIN \
  --bundle results/evidence --expected-ref sha256:EVIDENCE_PIN --record-id sha256:CATALOG_RECORD_DIGEST
ncp evidence source --catalog results/catalog --catalog-ref sha256:CATALOG_PIN \
  --bundle results/evidence --expected-ref sha256:EVIDENCE_PIN \
  --representation-id sha256:REPRESENTATION_DIGEST
ncp evidence span --catalog results/catalog --catalog-ref sha256:CATALOG_PIN \
  --bundle results/evidence --expected-ref sha256:EVIDENCE_PIN --span-id sha256:SPAN_DIGEST
~~~

## Compose with the knowledge and work profile

~~~sh
ncp knowledge config --bundle results/catalog --expected-ref sha256:CATALOG_PIN \
  --evidence-bundle results/evidence --evidence-ref sha256:EVIDENCE_PIN \
  --output results/knowledge-with-evidence.json
~~~

Existing database/principal/parent-ref arguments add the scoped requester or worker
tools. Both evidence arguments must appear together and the evidence catalog pin
must match the selected catalog. Metadata-only launchers continue to work.

The optional tools are ncp_evidence_overview, ncp_evidence_search,
ncp_evidence_representations, ncp_evidence_source and ncp_evidence_span. URI-addressable JSON resources use
ncp://evidence/EVIDENCE_PIN_HEX/overview and
ncp://evidence/EVIDENCE_PIN_HEX/span/SPAN_DIGEST_HEX.
Resources follow the official
[MCP resource protocol](https://modelcontextprotocol.io/specification/2026-07-28/server/resources).
The existing role, scope and revocation checks apply to every evidence tool,
resource and discovery call.

The host separately selects passages for a task:

~~~sh
ncp evidence bind-context --catalog results/catalog --catalog-ref sha256:CATALOG_PIN \
  --bundle results/evidence --expected-ref sha256:EVIDENCE_PIN --span-id sha256:SPAN_DIGEST \
  --database results/local-work.sqlite3 --parent-ref intent:engineering-review \
  --disclosure-ref operator:verified-task-context-decision
~~~

The resulting task context uses a representation identity and selected-text hash.
Its provenance retains the original source and archive-member identities.
Discovery does not bind a context; binding does not authorize a task; structural
citation checks do not accept a result. Authorization and review remain host-only.

## Bounds and unsupported representations

The initial profile limits selected text to 1 MiB per representation, 64
representations and 16 MiB total, with 1,800-byte UTF-8 passage windows.
Archives are capped at 128 MiB. A streaming preflight limits the central directory
to 8 MiB and 10,000 reconciled entries before the ZIP library allocates its index.
Expansion-size and selected-member compression-ratio limits also apply; ZIP64 and
multi-disk archives are unsupported. It refuses unsafe or ambiguous member names, links,
encrypted archives, unsupported compression, invalid UTF-8 and hash mismatches.

ZIP members are read directly using
[Python ZipFile.open](https://docs.python.org/3.12/library/zipfile.html#zipfile.ZipFile.open).
The compiler never extracts a repository to disk or executes archive content.
It does not traverse submodules or treat ZIP metadata as a verified Git checkout.

PDF/OCR, native Google document exports, binary data and unknown original hashes
remain unsupported by this text profile. Existing metadata discovery keeps these
sources visible. Future parsers need their own explicit recipes, locators,
budgets and verification tests.

Private selections, bundles, raw content, databases and local receipts stay in
ignored results directories. Tests use synthetic files and ZIP archives.
