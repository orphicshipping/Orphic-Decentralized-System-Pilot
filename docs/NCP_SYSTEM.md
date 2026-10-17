# NCP knowledge and work system

NCP combines a frozen source catalog, bounded task exchange, retained candidate
results and separate host review. MCP is the client interface to those components.
Existing NET investigation, provider and execution contracts retain their owners.
See the [system coherence guide](NCP_SYSTEM_COHERENCE.md) for the reconciled
source revisions and the distinct corpus, evidence and execution profiles.

## Components

| Component | Owner | Meaning |
| --- | --- | --- |
| Original documents, repositories and lots | Operator source archive | Artifacts unchanged by catalog compilation |
| Frozen Proto-Corpus | ciw.proto_corpus | Exact registry snapshots, normalized metadata and relationships |
| Knowledge interface | ciw.knowledge_mcp | Bounded metadata tools/resources using the official MCP SDK |
| Task store | ciw.external_work | Durable proposals, scoped contexts, worker leases and submissions |
| Authorization and review | Trusted local host | Exact-task permission and a separate scoped decision |
| Numerical execution | Existing instruments/providers | Existing explicit bindings and verification contracts |

Source-reported claims, metadata integrity, structural citation checks, scientific
reproduction and physical qualification remain distinct. The Kernel's portable
integrity grammar and Payload OS domain APIs retain their own ownership. This is
a specialized NCP catalog; native task references are not Kernel proof envelopes.

## Install and build

~~~sh
python -m pip install -e ".[dev,external-work]"
ncp knowledge --help
ncp work --help
~~~

Python 3.11 or newer is required. The net and ciw compatibility commands remain.
The external-work extra pins the official MCP SDK and gateway dependencies.
Metadata build/search and local task administration need no running model or network.

The compiler accepts these operator-selected inputs:

- Registry: lot_registry.json, source_membership.jsonl, oversize_register.json.
- Intake: intake_manifest.json, repository_manifest.json, verification.json.
- Optional selected collection: source_manifest.jsonl.

Create the output parent first; the bundle itself must not already exist.

~~~sh
ncp knowledge build --registry /approved/NCP_MCP_Lot_Registry \
  --intake /approved/Queue_Intake_20261007 \
  --selected /approved/NCP_MCP_Selected_Corpus --output results/proto-corpus-001
~~~

Retain the returned bundle_ref as the trusted pin. Every subsequent read requires
it explicitly. Opening verifies the manifest, exact inputs, derived catalog
digests and regeneration of the normalized projection. Conflicting identities,
hashes, counts or intake receipts are refused.

~~~sh
ncp knowledge status --bundle results/proto-corpus-001 --expected-ref sha256:PIN
ncp knowledge search --bundle results/proto-corpus-001 --expected-ref sha256:PIN \
  --query "polymer calibration" --limit 10
ncp knowledge source --bundle results/proto-corpus-001 --expected-ref sha256:PIN \
  --record-id sha256:RECORD
ncp knowledge relations --bundle results/proto-corpus-001 --expected-ref sha256:PIN \
  --record-id sha256:RECORD
ncp knowledge review-queue --bundle results/proto-corpus-001 --expected-ref sha256:PIN
~~~

PIN and RECORD stand for complete 64-character digests returned by build/search.
Search uses a derived inverted token index with AND semantics. Underscores and
CamelCase document names remain discoverable without substring false matches;
punctuation-only queries are refused. Ranking is lexical relevance, not confidence.
Use ncp knowledge resolve --source-id with an exact retained identity or declared
alias to retrieve its catalog record. Indexes do not change catalog or record pins. Pagination is bounded and returns
next_offset. Source kinds, categories and review gaps support targeted discovery.

## Source identity and representation

Source IDs, original-byte digests, exported-representation digests and metadata
digests remain separate. Native Google Sheet exports are explicitly exports.
Missing oversized-source hashes remain unknown; zero-byte source files remain valid.

Equal bytes do not merge source identities. Lot membership, duplicates and selected
aliases remain inspectable. Only genuinely staged selected copies receive copy
relationships. Moved intake originals are not described as newly created copies.

PDFs, repository ZIPs, code, notebooks and other assets are typed references.
No archive code runs and no full text is automatically extracted or embedded.
Readable links, license observations and integrity receipts do not grant source
use, redistribution, execution, scientific admission or release authority.
Unabridged metadata remains in pinned inputs behind the bounded projection.

## MCP connection

~~~sh
ncp knowledge config --bundle results/proto-corpus-001 --expected-ref sha256:PIN \
  --output results/ncp-knowledge.mcp.json
~~~

This writes a concrete stdio launcher configuration. It does not edit client
settings, open a public listener or activate a remote connector.

The profile exposes ncp_knowledge_overview, ncp_knowledge_search,
ncp_knowledge_source, ncp_knowledge_relations, ncp_knowledge_review_queue,
ncp_knowledge_resolve, and ncp_system_status. The same metadata is addressable through a pinned overview
resource and a source resource template. Resource IDs never select arbitrary
files or URLs. Metadata is untrusted data, never instructions.

The new profile uses the official SDK for tools and
[URI-addressable resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources).
Existing legacy stdio profiles retain their own protocol behavior.

## Selected source passages

The optional [evidence profile](NCP_EVIDENCE.md) freezes explicitly selected UTF-8
files and named ZIP members after verifying their original source hashes. It adds
passage search, exact byte locators and cited task contexts to the same MCP profile.
The evidence bundle must reference the selected catalog pin, and all source
statements remain unreviewed.

## Shared work

Keep SQLite on a local disk, outside synchronized Drive folders.

~~~sh
ncp work init --database results/local-work.sqlite3
ncp work provision-client --database results/local-work.sqlite3 \
  --client-id local-requester --role requester --parent-ref intent:pilot
ncp work provision-client --database results/local-work.sqlite3 \
  --client-id local-worker --role worker --parent-ref intent:pilot
ncp knowledge bind-context --bundle results/proto-corpus-001 --expected-ref sha256:PIN \
  --database results/local-work.sqlite3 --record-id sha256:RECORD \
  --parent-ref intent:pilot --disclosure-ref operator:approved-metadata-use
~~~

Binding is trusted host administration. The host first establishes the referenced
disclosure decision. A reference string is an attestation pointer, not a credential.

The bound context is catalog metadata, with its own metadata source identity and
byte hash. Provenance links original source identity, represented digest and bundle
pin. It does not masquerade as PDF text or repository code. Existing exact-text
corpus binding remains available for separately selected approved text sources.

Generate one launcher per host-provisioned client:

~~~sh
ncp knowledge config --bundle results/proto-corpus-001 --expected-ref sha256:PIN \
  --database results/local-work.sqlite3 --principal local-requester \
  --parent-ref intent:pilot --output results/requester.mcp.json
ncp knowledge config --bundle results/proto-corpus-001 --expected-ref sha256:PIN \
  --database results/local-work.sqlite3 --principal local-worker \
  --parent-ref intent:pilot --output results/worker.mcp.json
~~~

Selecting the pinned catalog in a trusted launcher is the local metadata disclosure
boundary. Principal and investigation scope are fixed there and rechecked against
retained client state. They are not model arguments. Reading catalog metadata does
not authorize a task or original-file retrieval.

The requester can propose/read. The assigned worker can discover eligible work,
claim, read its approved context and submit a candidate. Only the host authorizes
the exact task hash:

~~~sh
ncp work authorize --database results/local-work.sqlite3 --task-id TASK \
  --expected-hash sha256:TASK_DIGEST --approval-ref operator:verified-decision \
  --worker-id local-worker --expires-at UTC_UNIX_TIMESTAMP
ncp work review --database results/local-work.sqlite3 --task-id TASK \
  --result-id RESULT --decision needs_review --reviewer-ref operator:reviewer \
  --scope metadata-provenance-review --findings "Applicability remains unresolved"
ncp work status --database results/local-work.sqlite3
~~~

Authorization and review are not MCP tools. A completed worker outcome or matching
quote does not accept the result. Idempotence, cancellation, expiry and atomic
claims follow the existing [external-work contracts](EXTERNAL_WORK.md).

## Remote boundary

The existing Cloudflare gateway remains a separate authenticated requester/worker
surface. It does not expose corpus-wide discovery. This change does not configure
login identities, OAuth callbacks, tunnel credentials, wake-ups or access policies.
See [deployment setup](EXTERNAL_WORK_CLOUDFLARE.md).

Polling remains the baseline. Optional routine wake-up signals retained work;
it does not grant authority or replace task state and review.

## Validation

~~~sh
python -m pytest -q tests/test_evidence_corpus.py tests/test_evidence_cli.py \
  tests/test_evidence_mcp.py tests/test_proto_corpus.py tests/test_knowledge_mcp.py \
  tests/test_knowledge_cli.py tests/test_external_work.py \
  tests/test_external_work_http.py tests/test_corpus_retrieval.py \
  tests/test_operator_catalog.py
~~~

Tests use synthetic metadata, text and archives. Live bundles, inventories, databases and client
configurations belong in ignored local results directories, not in this repository.
