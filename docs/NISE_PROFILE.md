# Bounded NISE schematic scoping profile

NISE is deterministic relevance and scoping computation over an explicitly
supplied catalog. It is not machine-learning inference, evidence admission,
physical validation, a provider scheduler or control authority. This profile
uses the unchanged implementation in
[`orphicsystems/Notations-Inference-Schematics-Engine` at
`81908ddbe817018af5d9887a6bdd9cce3e3eafc6`](https://github.com/orphicsystems/Notations-Inference-Schematics-Engine/tree/81908ddbe817018af5d9887a6bdd9cce3e3eafc6).
The existing 21 imported trees and unrelated runtime pins are preserved.

| Tool | Input | Meaning |
| --- | --- | --- |
| `ncp_nise_describe` | `{}` | Describe the host binding, contracts, bounds and limitations. |
| `ncp_nise_validate` | `{catalog, query}` | Validate declared data with the pinned NISE contracts. |
| `ncp_nise_compile` | `{request: {execution_id, catalog, query}}` | Compute candidate structure and return its complete bound receipt. |
| `ncp_nise_inspect` | `{schematic}` | Validate and summarize supplied candidate structure; no origin proof. |
| `ncp_nise_verify` | `{receipt}` | Validate the complete receipt and compare fresh pinned compilation. |

Only a trusted launcher selects the checkout, absolute interpreter and principal
for `ciw.nise_profile.NiseHost`. The host verifies the full pinned checkout,
interpreter identity and recorded dependency versions. Those version labels do
not attest dependency bytes. The upstream package is not imported in the ODSP
process, vendored into its wheel or installed into its interpreter environment.
Runtime paths and source pins cannot be chosen through request fields.

Use `ciw.services.invoke` with
`PrincipalPolicy.from_file(...).facade(principal, nise=host)` and serve that same
facade through `ciw.mcp_server.serve_sdk`. Every call requires its current tool
grant; validate, compile, inspect and verify also require
`inference.schematic.construct.v1` in `scientific_operations`. This permission
identifier does not register a default execution provider. Disabled, absent or
revoked grants refuse before a profile call. Discovery grants no execution.
Raw in-process host administration remains trusted and is not an authorization
sandbox. No NISE resources, retention, paging, handoff, annotations or cross-profile
mapping are exposed through this profile. The private HTTP candidate does not
automatically provision or expose it.

Receipts bind the caller's explicit execution identity, full catalog and query,
catalog digest, full schematic content, exact runtime, comparison policy and
non-authorizing claims. Verification validates the supplied structure and
recomputes through the same pinned child before comparing full canonical
content. A copied or recomputed ID cannot substitute for this comparison.
Replaying identical inputs preserves the declared occurrence; a new execution ID
is an explicit new caller assertion. Neither form authenticates the caller's
catalog, source labels or upstream evidence.

Natural-language `query.question` is inert retained context. The original
algorithm selects using explicit focus IDs, requested capabilities, hypothesis
policy, hop bound and node budget. Requested capability names are descriptive
selectors, not execution grants or eligibility checks. Regex-valid versioned
`analysis.*` capabilities remain legitimate; the examples' `signal.*` namespace
is not mandatory. The upstream limit 64 applies to requested capabilities, not
all operation nodes in a catalog.

The pinned algorithm can displace focus nodes under a small node budget, and a
frontier entry can also name a selected node. The profile preserves these results
and reports `focus_displaced` and `selected_frontier` diagnostics. It does not
silently change the algorithm or claim every frontier node was excluded.

The complete name/arguments request and a subsequent receipt-verification call
must each fit 65,536 serialized bytes. Complete tool-response envelopes fit 262,144
bytes, including duplicated text/structured content and a 1024-byte reservation
for JSON-RPC framing. A NISE-enabled `serve_sdk` session validates raw UTF-8 before
the official MCP SDK decodes it: duplicate keys, malformed Unicode, nonfinite or
oversized numbers and deep trees refuse before information is lost. Its wire
frame cap is 73,728 bytes; request IDs are at most 128 characters or exact safe
integers. Invalid or overlong frames close the session before any tool handler.
SDK 2.3 logs its own parser errors without answering malformed requests; the
strict input guard therefore closes explicitly instead of leaving a waiting client
or parsing a rejected frame's suffix. A client can reconnect for a valid request.
The strict wrapper retains the pinned SDK's standard-descriptor claim, stdin
diversion and restoration lifecycle. It depends on that SDK's custody helpers;
missing helpers refuse startup rather than serving unclaimed input.
Other existing `serve_sdk` sessions retain their previous framing behavior.

The profile also has bounded child wall/CPU/address/file/descriptor limits and
one active call per host. Live combined stdout/stderr is bounded by the existing
subprocess custody implementation. Timeout or cancellation never means rollback.
If process cleanup cannot be established, the runtime binding stays held with
typed uncertainty; a request cannot clear it or launch another child.
An additional lease cleanup error preserves that first uncertain response and
both local diagnostics. The declared limits are 5 seconds per child, 20 seconds
of total call work, 3 CPU seconds, 256 MiB address space, 1 MiB file size,
32 descriptors and 131,072 bytes of combined live provider stdout/stderr. Git
metadata commands have a separate 4 MiB combined output cap. No limit is a
promise of rollback, physical timing or deployed isolation.

This is a pinned local process boundary, **not a network or secret-scrubbing
sandbox**. Existing environment inheritance remains explicit. Windows, deployed
identity/containment, live upstream and physical qualification are separate gates.
Source and installed MCP/SDK tests use synthetic catalogs and do not close them.

The legacy [handoff CLI](NISE_HANDOFF.md) remains separate and non-authorizing. Its
stronger structural validation rejects the previously accepted empty trace; it
does not claim fresh recomputation or source authentication. It no longer copies
the question into an execution mandate. The new profile does not expose that CLI
as an MCP tool.

The supplied firm's ownership statement is recorded as user-asserted provenance,
not independently verified corporate assignment. Upstream MPL-2.0 license and
copyright notices remain unchanged. No counsel deadline or approval is inferred.

Qualification must run actual pinned child positive/refusal/forgery/cancellation
cases; current SDK/MCP identity and grant denials before launch; installed-wheel
operation outside the checkout; raw duplicate/Unicode/frame tests; full receipt
comparison; generator idempotence and exact source-tree verification. Missing
runtime, hosted credentials or deployment evidence remain explicit prerequisites.
The existing NISE workflow now selects the canonical pin, builds its provider
from an archive in a separate environment, and declares a Linux profile/MCP gate.
Its Windows job still covers the legacy handoff only. This configuration neither
adds private-repository credentials nor proves a hosted run occurred.
