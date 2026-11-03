# Orphic Decentralized Systems Protocol

Developed by **Orphic Systems**

ODSP provides shared services for models, agents and deterministic software: retained investigations, bounded computation and controlled access to evidence. Clients use MCP over stdio or the Python service API. **ODRP** is the read-only reference profile of the same substrate.

[Current status](#current-status) · [Architecture](#architecture-and-boundaries) · [Profiles](#implemented-profiles) · [Toolbox and clients](#toolbox-and-clients) · [Setup](#setup) · [Outstanding programme](#outstanding-programme) · [Qualification and CI](#qualification-and-ci) · [Licensing](#source-and-licensing)

## Current status

**ODSP is operator-run research and pilot software. It is not release-qualified, and the updated project has not been deployed.** This README describes the implementation merged in [PR #266](https://github.com/orphicsystems/Orphic-Decentralized-System-Protocol/pull/266), at source revision `235ee45a11f744e8c801364965c07e9e3e13854f`. This documentation update does not publish the subsequent implementation campaign.

The merged programme includes strict Python contracts, principal-bound service calls, retained audit records, a local fenced queue, read-only intake/domain profiles, proof-receipt verification, a Rust request observer, a typed client/viewer and Python client toolbox libraries. The broader Rust core, distributed coordinator, server toolbox, persistent cache, live integrations and Domain H work remain subject to the [outstanding programme](#outstanding-programme) and release gates below.

## Architecture and boundaries

A trusted launcher selects the installed release, principal, current policy, profile, corpus references and approved runtime pins. Client arguments cannot replace those bindings. The intended software boundary is a `PrincipalPolicy` facade: MCP and `ciw.services.invoke` use the same current tool, scientific-operation, resource and disclosure checks. Direct calls to raw in-process hosts remain trusted administration, not an authentication sandbox.

Evidence access, computation and authority remain distinct:

- **Evidence:** ODRP reads approved immutable catalog/evidence snapshots. Retrieved text is untrusted data. A source hash, capture or receipt does not independently authenticate its assertions.
- **Computation:** host-bound operations preserve their inputs, runtime identities, outcomes and verification records. Full-content replay checks do not establish physical validity or evidence admission.
- **Execution:** tool discovery and a successful check confer no grant. Plans require explicit admission and execution permissions; domain, workbench and nested coordinator calls also check the underlying scientific-operation grants. Missing bindings, revoked access and unresolved outcomes remain refusals or holds.

The published private ingress candidate wraps HTTPS requests around the installed stdio service. It does not provide a native MCP Streamable HTTP/OAuth control plane. The operator panel validates Cloudflare Access assertions at its server proxy and maps the authenticated subject to a host-selected backend principal; the browser receives no backend credential.

The cloud direction is private authenticated ingress, separately operated compute and durable coordinator state. **PlanetScale-managed PostgreSQL is a candidate for that state; NATS messaging and compute remain separate services.** Portable PostgreSQL transactions and transaction-pool compatibility are being verified locally. This is not evidence of a provisioned PlanetScale database, a running NATS service or hosted failover/restore qualification. The existing Cloudflare Worker/Durable Object/storage implementation remains a separate candidate release path; no migration or deployment is implied by this README.

See the [private release contract](docs/PRIVATE_RELEASE_CONTRACT.md), [operating runbook](docs/PRIVATE_HOST_OPERATIONS.md) and [underlying architecture](docs/ARCHITECTURE.md).

## Implemented profiles

These are separately bound surfaces. A discovery entry neither installs a provider nor extends another profile's grants. Actual available tools come from the selected host's `tools/list` response.

| Surface | Implemented scope | Contract and limits |
| --- | --- | --- |
| ODSP control | Discover capabilities; create/admit plans; submit, inspect and compare runs; named domain/workbench operations | [Control model](docs/MCP_MODEL.md). Runtime bindings and capability holds still apply. |
| ODRP | Read catalog, knowledge and evidence snapshots with exact locators | [Reference profile](docs/ODRP.md). Disclosure grants remain separate from execution. |
| NSL | Replay an exact host-approved offline state-ledger response | [Read profiles](docs/READ_ONLY_ADAPTERS.md). No fresh query; capture provenance remains host-declared and unverified. |
| OLM | Read existing governed `inspect_retained` results, inspect catalog definitions and page owned retained data | [Read profiles](docs/READ_ONLY_ADAPTERS.md). Upstream identity/authorization remains required; no job submission. |
| NSR | Describe, reconcile affine state and verify complete receipts using the existing pinned kernel | [Bounded native profiles](docs/BOUNDED_NATIVE_PROFILES.md#nsr-affine-reconciliation). Accepted, held, refused and invalid-contract outcomes stay distinct. |
| SRA | Describe, validate, retrieve declared graph neighborhoods, assess and verify complete receipts | [Bounded native profiles](docs/BOUNDED_NATIVE_PROFILES.md#sra-schematic-assessment). No companion execution or rendering/projection surface. |
| ClockSync | Describe, reconcile a declared affine event mapping and replay its complete receipt | [Bounded native profiles](docs/BOUNDED_NATIVE_PROFILES.md#clocksync-affine-event-mapping). No hardware-clock access or synchronization. |
| NISE | Describe, validate, compile, inspect and verify deterministic schematic scoping over a supplied catalog | [NISE profile](docs/NISE_PROFILE.md), host pin `81908ddbe817018af5d9887a6bdd9cce3e3eafc6`. Question text is inert context; no ML inference, provider scheduling or control authority. |
| Offline intake | Inspect and fully verify host-approved ROS, OPC UA and MQTT observation exports | [Offline intake](docs/OFFLINE_INTAKE.md). No broker, wire-protocol, device or control connection. |
| Domain records | Inspect captured metrology/GIS contracts and finance/trade observations | [Domain records](docs/CAMPAIGN_DOMAIN_READ.md). No new engine, live feed, advice or order submission. |
| Proof receipts | Verify complete attributed receipts against a current host key registry | [Proof verification](docs/PROOF_VERIFICATION_PROFILE.md). Signature attribution is separate from computation proof; native SP1 remains held. |

NSR, SRA, ClockSync and NISE use fixed, host-selected subprocess bindings to their existing implementations. Verification validates complete supplied receipts before each profile's replay comparison; caller-supplied digest IDs alone are insufficient. Each profile preserves its own occurrence semantics. These process boundaries alone do not isolate network access, files or inherited secrets.

The [tool-programme acceptance contract](docs/TOOL_PROGRAMME_ACCEPTANCE.md) tracks the optional strict data boundaries, locked release inventory, audited principal host, fenced local queue and client work. Explicit process limits and a minimal environment can be selected by trusted launchers; strict namespace isolation must refuse when unavailable. These additions do not activate a deployment or expand existing grants.

An optional [Rust request observer](docs/RUST_REQUEST_BOUNDARY.md) compares bounded protocol decisions while Python remains authoritative. Trusted launchers select its exact executable; default operation and grants are unchanged. Native comparison, installed-client and cleanup qualification are separate from formal proof or deployed containment.

Ledger reads do not execute the capability they describe: the `proved_heat` row is not an SP1 proof. Unbound providers and existing qualification holds remain explicit.

## Toolbox and clients

The merged [context selector](docs/CLIENT_CONTEXT_SELECTOR.md) lists, searches, describes and selects exact schemas from the current authorized host view. Changes to policy, identity, schemas or bound captures invalidate stale selections. Selection changes a client's model context; it does not grant permission or invoke a service.

The merged [client skills and cache](docs/TOOLBOX_SKILLS.md) provide:

- Versioned `reference-citation` and `cross-profile-provenance` recipes over existing canonical services.
- A bounded synchronous `SkillRunner`, with fresh host admission for every step, current principal checks, retained intent/results and durable attempt markers that prevent silent replay.
- A separate in-memory cache for disclosed schemas and explicitly approved immutable reads. It caches no authorization decisions and is not used to execute skill steps.

These libraries add no MCP tools and provide no server session load/unload API. The server toolbox, signed workflow registry and shared persistent cache belong to the subsequent completion campaign. The [typed client and read-only viewer](tools/campaign-client/README.md) are additional consumers of the same contracts; neither supplies authority.

## Setup

The package declares Python 3.11 or newer. Use the exact Python, Node, provider and dependency versions recorded for the release being qualified; an earlier environment receipt does not qualify a newer checkout. From a checkout, an isolated local MCP installation starts with:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install '.[mcp-control]'
```

This installs ODSP and the optional official MCP SDK. It does not provision native providers, corpora, credentials or a private deployment. Release qualification uses the [offline build and installed-wheel procedure](docs/PRIVATE_HOST_OPERATIONS.md#reproducible-local-release).

For a synthetic control profile, prepare a **new** absolute output directory:

```sh
.venv/bin/python -m ciw.net control demo-config \
  --instrument builtin --output-dir /absolute/new-demo
```

Configure an MCP client to launch the current-policy stdio server with an existing approved policy and its selected principal:

```sh
.venv/bin/python -I -B -m ciw.principal_mcp \
  --profile /absolute/new-demo/profile.json \
  --policy /absolute/approved-policy.json \
  --principal operator --instrument builtin
```

The absolute paths and `operator` are placeholders for trusted launcher configuration. The [policy schema](src/ciw/principal_policy.py) requires explicit grants; preparing a demo does not create them. Use the installed interpreter's absolute path in the client's configuration. This launcher supports control and optional ODRP catalog/evidence bindings. The existing `ciw.net control serve` and `ciw.net reference serve` commands remain local trusted launchers; they do not authenticate separate remote users.

The [private operator panel](docs/PRIVATE_OPERATOR_PANEL.md) uses the same installed MCP service through the existing private gateway. Its separate server proxy verifies an Access assertion, maps the authenticated subject to a host-selected principal, and preserves transaction receipts and uncertain outcomes. The browser receives no backend credential. Building or testing this candidate does not provision an Access application, change the paused historical portal, or qualify a hosted deployment.

Additional profiles require their documented host factories, current grants, approved provider checkouts and runtime pins. See [bounded native profiles](docs/BOUNDED_NATIVE_PROFILES.md) and [NISE](docs/NISE_PROFILE.md). A provider checkout, installed SDK or selected tool schema is not an execution grant. Read the security defaults below before launching companion code.

## Security defaults and execution limits

At the published revision, principal policy and scope checks govern tool access, and private retention stores refuse capacity exhaustion. Raw in-process administration remains trusted code. Do not expose a raw host as an authenticated multi-user service.

**Companion containment is an unresolved release gate.** Published companion launch paths do not uniformly require a scrubbed environment or namespace isolation. Optional Linux resource limits do not block network or filesystem access. The approved local remediation clears inherited environment variables and requires Bubblewrap for pinned companion execution, refusing when isolation is unavailable; that implementation is not included in this README-only update. The current test host refuses the namespace probe, so a successful strict-isolation run still needs a suitable host.

The published gateway supports principal disabling/revocation, but its per-principal backend token format does not enforce expiry or rotation. A replacement credential lifecycle is in local qualification. In published main, nine raw-host profile routes return through profile-specific validators without running `services.admit`. Principal-bound facade calls still apply their own current policy and request checks; the local campaign is reconciling the shared admission path.

## Bounds and recovery

Control arguments are limited to 65,536 serialized bytes. The bounded NSR/SRA/ClockSync/NISE profiles limit complete calls to 65,536 bytes and complete duplicated MCP envelopes to 262,144 bytes; individual profiles impose additional schema, process and framing limits. OLM has its own 16 KiB input bound. Limits apply to serialized bytes, including escaped Unicode.

Owner retention has a 4,000,000-byte budget. Private principal-bound stores disable eviction and refuse capacity exhaustion; legacy/demo stores can evict within their configured bounds. OLM conservatively refuses bundles above 3,800,000 bytes; its 6 MiB network-read allowance does not promise equivalent durable capacity. See the profile guides for exact paging and scope rules.

For control plans and coordinator jobs, reusing an attempt/key returns the same record; conflicting requests refuse. Timeout or cancellation is not proof of rollback. Uncertain subprocess cleanup holds the affected runtime binding against further launches; uncertain durable transactions retain their recovery fence. Current-checkpoint rehoming preserves owner/history; lost-history disaster recovery remains unresolved. Follow the [operations runbook](docs/PRIVATE_HOST_OPERATIONS.md#recovery-and-owned-backup); do not clear a hold or silently re-execute work.

## Outstanding programme

The following work is part of the active completion campaign. Local source, a passing slice or a planned interface does not mean it is published on main, qualified as an integrated release or deployed.

| Workstream | Published main | Remaining completion and qualification |
| --- | --- | --- |
| Rust core and kernels | Opt-in request observer; Python remains authoritative | Native policy/numerical implementations and oracle comparisons are being integrated. The complete core/kernel port and final source-bound acceptance remain open. |
| Distributed coordinator | Local atomic claims, leases and fencing | PostgreSQL/NATS custody and numerical execution have local integration evidence. Final combined checks, hosted PlanetScale compatibility, failover and restore remain open. |
| MCP toolbox and skills | Client-side selection and synchronous recipes | Server tool loading and an MCP skill runner are being integrated with canonical admission, ownership and unresolved-outcome rules. |
| Persistent cache and workflow registry | Isolated in-memory content cache and host-supplied recipes | Shared persistent storage and signed registry implementations need final combined acceptance, current trust/key policy and deployment qualification. |
| Live ROS2 and industrial intake | Saved ROS/OPC UA/MQTT exports | Live adapter work remains separate from actual ROS runtime, broker/device and authorized network qualification. No equipment-control readiness is claimed. |
| Operational metrology | Captured contracts plus existing native scientific workflows | New operational composition has local native evidence; final integration, traceable standards, uncertainty and physical acceptance remain separate gates. |
| Native proofs | Attributed receipt verification | Local Kani harness evidence covers specific properties only. SP1 build/proof inputs, capacity and genuine proof acceptance remain unresolved. |
| Remote control plane | Private HTTPS-to-stdio gateway and Access-validated panel candidate | Native Streamable HTTP/OAuth integration, bounded credentials, rate limiting and metrics need final integration and hosted identity/storage checks. |

### Domain H: data systems

Domain H treats each data source as an instrument: register its allowed use, calibrate against known samples, check freshness and schema drift, and hold affected downstream work until an impact review resolves drift. Dataset receipts must retain exact source/input/output identities. TBRT computation does not authenticate wall-clock time, and a receipt is not NSL admission.

The six profiles below are **active work, not published tools in this revision**. Their intended contracts are:

| Profile | Contract and current status |
| --- | --- |
| `ncp_collect_*` | Licensed, terms/robots-reviewed, honestly identified, rate-limited collection of places, assets, infrastructure and imagery only. Local source/dataset receipts and collection/R2 quarantine adapters exist; hosted collection and storage are unqualified. No person kind, stealth fetch or spoofed browser identity is admitted. |
| `ncp_pipeline_*` | Declared transformations with exact inputs, code version, outputs, lineage, idempotent attempts and breaking-schema refusal. The pipeline integration remains in progress; arbitrary SQL execution is not implied. |
| `ncp_analysis_*` | Reproducible queries, statistics and charts over exact dataset versions, with declared uncertainty. Implementation and qualification remain in progress. |
| `ncp_db_*` | Vocabulary-bound schema/index/migration plans and isolated dry runs. The local database slice has PostgreSQL/transaction-pool evidence; integration remains open. Applying a migration requires separate human approval. |
| `ncp_dbops_*` | Read-only health, drift, query/index advice and backup/restore assessment. Local checks do not establish a successful hosted restore. Destructive actions require explicit approval. |
| `ncp_mining_*` | Evidence-linked anomaly, clustering, geodata/imagery change and ledger-pattern findings with confidence. Implementation and qualification remain open; findings never auto-actuate. |

PPDA/SCOUT, SRA and NSL must be identified and verified at their actual source revisions before reuse. SRA's current profile is schematic assessment, not a verified live knowledge-graph connection. The new data layer does not establish authenticated time or actual NSL admission; its downstream trust remains held. OSINT exclusions in the new profile do not resolve the outstanding review of imported SCOUT/FrameMapper sources. Connector names are not evidence of configured access.

## Qualification and CI

Implementation, local fixtures, installed-artifact tests, native-provider checks, hosted integration and physical qualification are separate claims. Earlier receipts apply only to their recorded source, package/image, runtime and configuration. The earlier frozen frontend container does not qualify the current completion campaign.

The recorded [GitHub Actions run for main `235ee45a`](https://github.com/orphicsystems/Orphic-Decentralized-System-Protocol/actions/runs/41682002605) failed at startup with zero jobs. The platform cause remains unverified; workflow syntax checks or local tests do not resolve that failure. The full scientific baseline is also not a clean pass: its recorded attempt retains a trusted `lsblk` prerequisite refusal and skipped checks. Required collector ownership/trust checks remain enforced.

Final integrated source/installed-wheel tests, a matching container, hosted identity/storage and restore checks, applicable native/physical evidence and operator acceptance remain open. No final combined pass or production readiness is declared.

The [Woodpecker preparation](docs/WOODPECKER_PRIVATE_CI.md) provides a manual local workflow in [.woodpecker/private-qualification.yaml](.woodpecker/private-qualification.yaml) and an [exact-source driver](scripts/run_private_woodpecker.py). It uses the official CLI with preselected tools, offline dependencies and fresh evidence. `ODSP_CI_NISE_MODE=native` plus explicit `CIW_NISE_CHECKOUT` and `CIW_NISE_PYTHON` bindings is required for combined NISE acceptance. Default `contracts` mode records NISE qualification as `NOT_REQUESTED`.

No Woodpecker server, agent, OAuth application, persistent host or deployment has been created by this integration. Local execution is not a sandbox or hosted qualification. The existing GitHub release-evidence requirements remain intact; local Woodpecker receipts do not replace them.

Use the [five-gate release contract](docs/PRIVATE_RELEASE_CONTRACT.md), [executable gap inventory](release/private-host-gaps.json), [development checks](docs/DEVELOPMENT.md) and [capability verification report](mcp/verification-report.json) to assess a specific release. Remaining gates include approved live identity/upstream bindings, actual hosted storage/restore, larger OLM retention, applicable native/platform/physical evidence and operational acceptance. Missing required prerequisites and skipped checks do not become passes. Production SLO/RPO/RTO values require explicit decisions; local fixture targets are not commitments.

## Source and licensing

The package retains its `ciw` namespace and `net`/`ncp` entry points. Start with [services.py](src/ciw/services.py), the [plug-in contract](mcp/plug-in.json), [capability map](mcp/capability-map.json) and [tool contracts](mcp/model-catalog.json).

The [folder index](mcp/folder-index.json) and [project guide](docs/MCP_PROJECT.md) point to each folder's `MCP.md` or [external contract](mcp/source-contracts.md). External navigation preserves imported sources and exact-input capsules, including all 21 import pins. A folder is not a human console and does not opt for the easiest task in a set. Source paths and catalog rows confer no tool grant.

First-party code is declared `AGPL-3.0-or-later`, subject to explicit component exceptions: see [LICENSE](LICENSE) and the [licensing policy](docs/LICENSING.md). Imported components and separately bound providers retain their own licenses, copyright and provenance notices. NISE is an external MPL-2.0 provider, not an additional vendored import tree. Adapter integration expands no rights or counsel clearance. [THIRD_PARTY.md](THIRD_PARTY.md) identifies imported licenses, including FrameMapper GPLv3; the presence of that notice does not establish complete upstream-notice or distribution compliance.

Companion qualification and licensing/removal review remain open. The exact six-copy removal scope, including whether history or retained artifacts are affected, has not been established. This documentation change removes no companion files, rewrites no history and clears no source or licensing hold.

For changes, read [CONTRIBUTING.md](CONTRIBUTING.md). Historical operator material remains in [OPERATOR_REFERENCE.md](docs/OPERATOR_REFERENCE.md), and the [implementation audit](docs/IMPLEMENTATION_AUDIT_20261102.md) records the earlier repair's scope and remaining limits.
