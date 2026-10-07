# NCP selected-corpus read-only retrieval

This additive module reads the existing selected-corpus manifest, staging provenance
graph, collection map, extraction queue, and review findings. It reuses
`ciw.core.identities.content_identity`, existing strict JSON readers and
`ciw.agent_mcp.Server` / `serve`. It creates no second Session, capability registry,
provider loader, execution queue, scientific fact authority, or measurement schema.

The launcher is `python -m ciw.corpus_agent`. Existing `net`, `ncp`, numerical
providers, journals, operation IDs, dependency pins and registries are unchanged.
This is a local development retrieval profile, not a new NCP-wide wire version.

## Operator startup

Create a new, local bundle from the selected sidecars and captured internal files:

```sh
python -m ciw.corpus_agent build \
  --inputs ./selected-sidecars --snapshots ./source-snapshots \
  --output ./retrieval-bundle \
  --expose NCP-CS-001 NCP-CS-002 NCP-CS-003
```

The builder returns a `bundle_ref`. Retain it separately. The original selected
manifest and its pipeline/rights fields are not rewritten. The new catalog records
an explicit host choice to expose the three internal Markdown/JSON snapshots;
it does not upgrade any external source's ingestion permission. The initial profile
rejects PDF or other external full-text exposure even when requested.

```sh
python -m ciw.corpus_agent inspect --bundle ./retrieval-bundle --expected-ref sha256:ROOT_HASH
python -m ciw.corpus_agent search --bundle ./retrieval-bundle --expected-ref sha256:ROOT_HASH --query 'provider runtime identity'
python -m ciw.corpus_agent serve --bundle ./retrieval-bundle --expected-ref sha256:ROOT_HASH
python -m ciw.corpus_agent report --bundle ./retrieval-bundle --expected-ref sha256:ROOT_HASH --output ./inspector.html
```

Only the operator chooses local paths at startup. Tool arguments accept bounded
queries, existing selection IDs, span IDs and JSON pointers—not paths, URLs,
executable names, model APIs, permission changes or destination directories.
`serve` uses the original stdio framing/initialization/error implementation. It
starts no HTTP listener and makes no model, Drive, network or scientific-provider
calls. No client configuration is changed by this package.

## Six read-only queries

| Tool | Purpose |
|---|---|
| `ncp_corpus_overview` | Coverage and explicit processing limits |
| `ncp_corpus_search` | Lexical BM25 over curator metadata and exposed internal byte windows |
| `ncp_corpus_source` | Original source metadata, unresolved findings and paginated span locations |
| `ncp_corpus_span` | Exact UTF-8 source byte window, source hash and byte locator |
| `ncp_corpus_reported_field` | Native JSON value at a strict JSON pointer; retains numeric/boolean types |
| `ncp_corpus_relations` | Manifest-reconciled source/version/copy/collection subgraph |

Scores rank lexical matches; they are not probabilities, scientific confidence,
evidence weights, or a measure of applicability. This is not semantic embedding
search, learned graph reasoning, a trained model, or a deployed RAG answer generator.
It supplies source material from which a separately governed application can answer.

Source text and curation comments remain untrusted data. A paper or retained report
can contain imperative wording, statuses or tool names; none is dispatched. A
retained `PASS`/`VERIFIED` status is only source-reported historical data. The host
always returns non-authorizing boundaries and null new operation/execution/
verification IDs. A retrieval content hash is not a verification occurrence.
Tool annotations describe behavior but are not an operating-system sandbox.

## Integrity and context recovery

`catalog.json` pins each member's raw bytes. Startup requires its independently
provided raw SHA256 reference. The loader validates member hashes, original source
hashes, strict JSON, source identity uniqueness, review references and complete
extraction-task coverage. It reconstructs the expected v1 staging graph from the
retained manifest and collection definitions: rehashing a forged graph cannot make
it reconcile with unchanged source records.

Internal snapshots are frozen in memory after checked reads. Each source text
window stores 0-based UTF-8 byte start and exclusive end offsets, plus the raw span
hash and source hash. Source pagination followed by span expansion recovers every
byte of each exposed internal document. Paragraph boundaries are preferred, but a
window is not asserted to contain an entire scientific claim or all its conditions.
Follow the original source to obtain complete context.

JSON pointers select parsed native data while the untouched raw JSON is also
available through byte windows. Integers, floats, booleans and signed zero retain
their representations under the existing CIW content encoder. No unit conversion
or provider equivalence is inferred.

The file reader rejects symlinks and special files and checks before/after file
metadata. The pinned bundle is a frozen local snapshot, not live Drive sync,
atomic filesystem snapshotting, physical-origin authentication, hostile-host
isolation, PKI, an independent scientific review, or release authorization.
Reopening rejects changed bytes. Existing open objects intentionally remain frozen.

## Limits

This bounded profile permits at most 512 selection records, 8 MiB per member,
32 MiB aggregate input, 10,000 text windows, 1,800 bytes per window, 512 query
characters, 24 distinct query terms, 20 search hits and 128 KiB response bytes.
Source span pagination returns at most 50 descriptors per page. Unknown tools and
arguments refuse. Limits apply only to this optional retrieval profile, not the
broader CIW/NCP substrate.

No real source bundle or source register is distributed with this module. The
operator supplies retained sidecars and selects any permitted internal text
snapshots. External literature remains metadata-only in this profile; attribution
notices do not become blanket ingestion approvals. Original archive copies remain
at their retained locations, and a source register is never silently synchronized.

## Tests and inspection view

Run the new focused suite using the existing project test runner:

```sh
PYTHONPATH=src python -m pytest -q tests/test_corpus_retrieval.py
PYTHONPATH=src python scripts/check_corpus_retrieval.py --bundle ./retrieval-bundle --expected-ref sha256:ROOT_HASH
```

The focused tests exercise real indexing, original MCP Server framing, subprocess
stdio, native JSON values, exact byte reconstruction, graph reconciliation, tamper
refusals, resource limits, no-provider invocation, detached responses and HTML
escaping. Synthetic fixtures are labeled synthetic. The separate real-bundle
checker reports observed metadata retrieval and exact reconstruction; it does not
claim answer correctness or scientific validation.

`corpus_view.render_html` is a frozen, script-free inspection export, not a new NCP
workbench. It provides direct source navigation, native keyboard-accessible
`details` disclosures, text relations, explicit coverage labels and source hashes.
External links are restricted to HTTPS Drive/Docs URLs and are only opened by the
reader. It fetches no assets or fonts. Browser checks cover desktop/mobile overflow,
keyboard disclosure and absence of external requests. They are not full WCAG
certification or physical-device testing.

## Applied specialist guidance

- **UML/software-architecture visualization:** source, snapshot, copy and collection
  roles stay distinct; graph edges are checked against their source model instead
  of treating a visually plausible graph as evidence.
- **Accessibility/inclusive visualization:** all relationships and limitations have
  a text path; native disclosures and direct labels replace hover-only information.
- **Testing data visualizations:** test model correctness and interaction separately;
  real records determine the display while adversarial synthetic fixtures exercise
  unsafe names and state transitions.

## Integration boundary

The reader reuses the existing MCP transport, strict JSON helpers and identity
encoder. It does not replace provider catalogs, execution hosts or graph
interchange. The selected-source sidecar format includes staged copy identities;
a moved original or new metadata record must not be represented as a separately
verified staged copy merely to satisfy that format.

References used in the implementation review:
- Official MCP tool-annotation boundary: https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/
- Existing implementation: `src/ciw/agent_mcp.py`, `control_contracts.py`, `core/identities.py`.
- Corpus input basis: unchanged `source_manifest.jsonl`, `selection_graph.json`,
  `review_findings.jsonl`, `extraction_queue.jsonl` and `collections.json`.
