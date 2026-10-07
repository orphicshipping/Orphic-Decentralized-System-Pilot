# NCP selected-corpus read-only retrieval

This additive module reads the existing selected-corpus manifest, staging provenance
graph, collection map, extraction queue, and review findings. It reuses
`ciw.core.identities.content_identity`, existing strict JSON readers and
`ciw.agent_mcp.Server` / `serve`. It creates no second Session, capability registry,
provider loader, execution queue, scientific fact authority, or measurement schema.

The installed launchers are `ncp corpus` and the historical alias `net corpus`.
`python -m ciw.corpus_agent` remains supported. Numerical providers, journals,
operation IDs, dependency pins and registries retain their existing contracts.
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

The initial real bundle has 15 metadata records and 3 internal text snapshots.
External literature remains metadata-only. Seven sources still require the prior
rights review and five have recorded attribution notices, not blanket approvals.
No PDF is distributed in this retrieval bundle. Original archive copies remain in
Drive, and the previous selected-corpus register is not silently synchronized.

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

This increment is based on the recovered package closure at upstream PR219 commit
`26ffb9c06dec36c2357300b581fff7f415a1d1a0`, whose `src/ciw` tree was verified as
`5af7336ca3cf1507eb0a7ea92a9f29cd25e10586`. It is not a complete current-main
monorepo checkout. Apply the additive files only after reconciling newer branches;
no adjacent provider-catalog or graph-interchange work is replaced.

References used in the implementation review:
- Official MCP tool-annotation boundary: https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/
- Existing implementation: `src/ciw/agent_mcp.py`, `control_contracts.py`, `core/identities.py`.
- Corpus input basis: unchanged `source_manifest.jsonl`, `selection_graph.json`,
  `review_findings.jsonl`, `extraction_queue.jsonl` and `collections.json`.


## One MCP connection for investigations and the corpus

`ncp agent` exposes the existing MCP launcher through the normal command catalog.
Add the corpus to an explicitly read-only builtin profile:

```sh
ncp agent serve --profile ./inspection-profile.json \
  --corpus-bundle ./retrieval-bundle --corpus-ref sha256:ROOT_HASH
```

The two corpus options are required together. The corpus is verified and frozen
before native input loading. The host profile is read once; the exact loaded
profile must have `output_dir: null`, `allow_operations: []`, and empty
`candidate_domains`, `comparisons` and `check_suites`. Combined inspection rejects
native instrument selectors, provider flags and resume before provider startup.
The example at `examples/agents/corpus-inspection/profile.json` starts with no
native inputs. Bind retained validated NCP records under its `inputs` object to
inspect them alongside corpus records; paths are relative to the profile.

This mode advertises eleven tools: the six corpus queries above plus
`net_capabilities`, `net_inspect`, `net_source_context`, `net_check_edit` and
`net_observe`. A fixed dispatch allowlist enforces the restriction, including
direct calls to unadvertised tools. Corpus IDs never become native artifact
handles. Inspection does not retain new scientific results, approve evidence,
restore provider code or execute a retrieved instruction. Ordinary explicitly
granted NCP execution remains available through the existing agent launcher
without the corpus options; scientific outputs keep their native provenance.

A source's appearance in a search result does not connect it to an investigation.
The operator/application must review relevance, units, applicability and permissions
before creating any separately governed native record. No implicit admission,
source-to-operation conversion or model-generated scientific assertion is added.

The combined profile uses the existing tools-only MCP JSONL stdio transport and
supports the existing 2025-11-25 and 2025-06-18 negotiations. The MCP reference
pages for [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
and [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
were checked on 2026-10-07. Results retain both JSON text and structured content;
tool failures set `isError`. The transport starts no network listener.


The combined builtin reader also registers the installed, fixed Link scalar
payload validators so a fresh process can reopen GaugeLink investigations.
These validators check retained arithmetic and relationships; they do not restore
operation bindings, dispatch `Session.handle`, mint execution IDs, or establish
physical qualification. Unknown saved operation contracts still refuse.
