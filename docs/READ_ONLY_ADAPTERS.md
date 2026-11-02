# NSL and OLM read-only profiles

These profiles are separate host bindings reached through `ciw.services.invoke`.
They do not compose grants with ODRP's `ReferenceHost`, the control host or one
another. `services.capability_map()['reference_profiles']` describes the three
profiles; discovery does not authorize access or execution. No new CLI launcher,
query engine, scheduler, credential or upstream implementation is included.

## NSL: exact offline captured responses

`ciw.state_read_profile.open_state_reference` accepts approved capture **bytes**,
their expected SHA256, the fixed source-contract commit
`4fef5f488694d0dd526b2a2166f5e5c90f1dd7ec`, principal, origin, projection and an
explicit ruling-ID allowlist. The trusted launcher performs file I/O and selects
these bindings; request payloads cannot replace them. Serve the bound host with
the existing `ciw.knowledge_mcp.serve_stdio` transport.

| ODSP operation | Captured NSL operation |
|---|---|
| `ncp_state_releases` | `list_releases` |
| `ncp_state_release` | `get_release` |
| `ncp_state_release_manifest` | `get_release_manifest` |
| `ncp_state_records` | `list_records` |
| `ncp_state_as_of` | `query_as_of` |
| `ncp_state_retractions` | `list_retractions` |
| `ncp_state_ruling` | `get_ruling` |
| `ncp_state_ruling_manifest` | `get_ruling_manifest` |

Only an exact captured operation/argument tuple is replayed. There is no fresh
read, pagination synthesis, temporal query execution or inferred answer.
`COUNTERPARTY_SHARED` and `PUBLIC_RULING` are the supported host projections;
`query_as_of` requires the former. `FIRM_INTERNAL` is not accepted. Dates are
validated, ruling reads require the host's allowlist, and uncaptured queries
refuse. A refusal nested inside an upstream `result` remains intact and produces
`outcome='refused'`, even when the outer captured tool call succeeded.

Capture origin is host-declared and unverified. The profile does not authenticate
an upstream principal, attest a source revision or verify a governed receipt.
Its provenance explicitly reports those limits. The response digest identifies
the adapter's canonical JSON representation; it is not an upstream attestation.
Source statements remain untrusted data. Capture and argument bounds are checked,
and both the SDK response and complete MCP envelope must fit 262,144 bytes.

## OLM: governed retained reads

`ciw.logistics_profile.open_logistics` binds an HTTPS endpoint ending in
`/api/pilot/mcp`, bearer token, tenant, principal, declared roles, allowed source
kinds and absolute Node executable. These are trusted launcher inputs, never
model arguments. The source-contract pin is
`217a09016116123a5be95544b870e990c895e142`; it does not prove the revision of a
running server. The upstream remains responsible for actual authentication and
resource-specific authorization. Serve with `ciw.logistics_mcp.serve(host)`.

| ODSP operation | Read behavior |
|---|---|
| `ncp_logistics_catalog` | Existing `inspect_retained` catalog selector |
| `ncp_logistics_describe` | Catalog read, then all matching capability versions or the exact requested version and their implementations |
| `ncp_logistics_job` | Existing job status/result selector; never submission |
| `ncp_logistics_inspect` | One closed retained selector permitted by this host |
| `ncp_logistics_retained_read` | Byte page of a retained bundle owned by this tenant/principal, after checking its profile, contract pin and current host scope |

The closed selectors cover workflow plan/run, acquisition, compute status/result,
observation, catalog, temporal state, twin and package manifest. The existing
`pilot:agent-read` requirement and kind-specific read-role mappings are enforced
locally. A reconnected host with revoked roles or a narrower kind allowlist cannot
use a retained artifact to recover the removed access. Tenant/principal ownership
also remains enforced by the existing retention store.

Requests use protocol `2025-06-18`, with a 16 KiB input bound and a bounded 6 MiB
upstream read. Redirects, retries and credential fallback are not supported.
The independently authored fixed Node serializer checks the canonical digest of
the **whole JSON-RPC response**, including ID, using ECMAScript numeric semantics
and UTF-16 key order. HTTP/RPC/tool errors, incorrect IDs or protocol versions,
duplicate JSON keys, absent receipt hashes and digest mismatches refuse. SDK and
MCP refusals do not disclose transport exception details.

The retained bundle preserves the complete parsed RPC object, receipts and any
derived catalog projection. It does not retain the original wire formatting or
claim byte-identical network requests. Source/read/result receipt hashes remain
upstream claims; only the complete-RPC canonical digest is verified locally.
Small bundles are returned inline; larger accepted bundles expose an artifact ID
and byte paging. Paging does not execute the upstream operation again.

The bundle threshold is **3,800,000 serialized bytes**. The existing retention
store independently enforces **4,000,000 bytes for the whole owner**, including
its envelope and other artifacts, with its existing eviction policy. Responses
that exceed those bounds refuse; the 6 MiB network allowance is not a promise of
6 MiB durable storage. No global cap is raised and no fields are truncated.

## Verification and remaining gates

Repository tests use explicitly synthetic captures/transports and real separate
official MCP client/server processes. They check all mappings, scope and identity
refusals, malformed responses, canonical numeric/key-order cases, paging,
reconnection, output bounds and coexistence with the original ODRP profile.
The installed-wheel gate runs outside the checkout; the ODRP candidate also
checks installed capability discovery without increasing execution usage.

Separately reviewed upstream evidence was reported by the parent integration
review: NSL's unchanged governed server produced nine fixture responses across
eight reads; OLM's unchanged governed handler used local PostgreSQL/S3/JWKS
fixtures and supplied 18 cases for independent response/canonical-digest checks.
Those upstream source trees and private captures are not vendored here. The local
repository tests do not replace that evidence or claim a live deployment.

Outstanding qualification includes approved live endpoints and identities,
upstream revision evidence and resource-specific roles, OLM positive resource/job
cases beyond the captured evidence, and an approved durable-retention design for
4–6 MiB results. Native-platform and hosted CI qualification remain separate.
The existing repository-wide baseline failures also remain project gates;
matching main is evidence of inheritance, not acceptance. This integration does
not weaken immutable-source checks or repair unrelated engines.
