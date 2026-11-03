# Orphic Decentralized Systems Protocol

Developed by **Orphic Systems**

ODSP provides shared services for models, agents and deterministic software: retained investigations, bounded computation and controlled access to evidence. Clients use MCP over stdio or the Python service API. **ODRP** is the read-only reference profile of the same substrate.

[Architecture and boundaries](#architecture-and-boundaries) · [Profiles](#implemented-profiles) · [Setup and clients](#setup-and-clients) · [Qualification and CI](#qualification-and-ci) · [Source and licensing](#source-and-licensing)

## Architecture and boundaries

A trusted launcher selects the installed release, principal, current policy, profile, corpus references and approved runtime pins. Client arguments cannot replace those bindings. The intended software boundary is a `PrincipalPolicy` facade: MCP and `ciw.services.invoke` use the same current tool, scientific-operation, resource and disclosure checks. Direct calls to raw in-process hosts remain trusted administration, not an authentication sandbox.

Evidence access, computation and authority remain distinct:

- **Evidence:** ODRP reads approved immutable catalog/evidence snapshots. Retrieved text is untrusted data. A source hash, capture or receipt does not independently authenticate its assertions.
- **Computation:** host-bound operations preserve their inputs, runtime identities, outcomes and verification records. Full-content replay checks do not establish physical validity or evidence admission.
- **Execution:** tool discovery and a successful check confer no grant. Plans require explicit admission and execution permissions; domain, workbench and nested coordinator calls also check the underlying scientific-operation grants. Missing bindings, revoked access and unresolved outcomes remain refusals or holds.

The existing private ingress candidate wraps HTTPS requests around the installed stdio service. It is **not native MCP Streamable HTTP or OAuth**. Cloudflare ingress and isolated single-writer execution are a proposed private deployment profile. Ubuntu/PlanetScale/Cloudflare topology choices are not evidence of provisioned or qualified infrastructure; no database, Kubernetes cluster or hardware platform is mandated here.

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

NSR, SRA, ClockSync and NISE use fixed, host-selected subprocess bindings to their existing implementations. Verification validates complete supplied receipts before each profile's replay comparison; caller-supplied digest IDs alone are insufficient. Each profile preserves its own occurrence semantics. These process boundaries are not network or secret-scrubbing sandboxes.

The [tool-programme acceptance contract](docs/TOOL_PROGRAMME_ACCEPTANCE.md) tracks the optional strict data boundaries, locked release inventory, audited principal host, fenced local queue and client work. Explicit process limits and a minimal environment can be selected by trusted launchers; strict namespace isolation must refuse when unavailable. These additions do not activate a deployment or expand existing grants.

Ledger reads do not execute the capability they describe: the `proved_heat` row is not an SP1 proof. Unbound providers and existing qualification holds remain explicit.

## Setup and clients

The package declares Python 3.11 or newer. The private qualification environment selects Python 3.12.14 and Node 24.19.0; its exact dependencies and runtime bindings are part of the release evidence. From a checkout, an isolated local MCP installation starts with:

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

For an additional bound profile, deterministic software uses the same facade. This NISE example requires the approved provider checkout and its **separate** interpreter to exist already:

```python
from ciw.nise_profile import open_nise
from ciw.mcp_server import serve_sdk
from ciw.principal_policy import PrincipalPolicy
from ciw.services import invoke

policy = PrincipalPolicy.from_file('/absolute/approved-policy.json')
with open_nise(checkout='/absolute/approved-nise',
               python_executable='/absolute/nise-env/bin/python',
               principal='operator') as host:
    client = policy.facade('operator', nise=host)
    description = invoke('ncp_nise_describe', {}, host=client)
```

Inside that same host lifetime, a launcher can call `serve_sdk(client, name='odsp-nise')` to serve the facade over stdio. Every NISE operation except describe also requires `inference.schematic.construct.v1` in the principal's `scientific_operations`. Other profiles have their own host factories and grants in the linked guides.

## Bounds and recovery

Control arguments are limited to 65,536 serialized bytes. The bounded NSR/SRA/ClockSync/NISE profiles limit complete calls to 65,536 bytes and complete duplicated MCP envelopes to 262,144 bytes; individual profiles impose additional schema, process and framing limits. OLM has its own 16 KiB input bound. Limits apply to serialized bytes, including escaped Unicode.

Owner retention has a 4,000,000-byte budget. Private principal-bound stores disable eviction and refuse capacity exhaustion; legacy/demo stores can evict within their configured bounds. OLM conservatively refuses bundles above 3,800,000 bytes; its 6 MiB network-read allowance does not promise equivalent durable capacity. See the profile guides for exact paging and scope rules.

For control plans and coordinator jobs, reusing an attempt/key returns the same record; conflicting requests refuse. Timeout or cancellation is not proof of rollback. Uncertain subprocess cleanup holds the affected runtime binding against further launches; uncertain durable transactions retain their recovery fence. Current-checkpoint rehoming preserves owner/history; lost-history disaster recovery remains unresolved. Follow the [operations runbook](docs/PRIVATE_HOST_OPERATIONS.md#recovery-and-owned-backup); do not clear a hold or silently re-execute work.

## Qualification and CI

Implementation, local fixtures, installed-artifact tests, native-provider checks, hosted integration and physical qualification are separate claims. The NISE/CI integration requires fresh qualification against its complete source revision and installed wheel. Earlier receipts apply only to their recorded revisions; this README declares no final combined pass or production readiness.

The [Woodpecker preparation](docs/WOODPECKER_PRIVATE_CI.md) provides a manual local workflow in [.woodpecker/private-qualification.yaml](.woodpecker/private-qualification.yaml) and an [exact-source driver](scripts/run_private_woodpecker.py). It uses the official CLI with preselected tools, offline dependencies and fresh evidence. `ODSP_CI_NISE_MODE=native` plus explicit `CIW_NISE_CHECKOUT` and `CIW_NISE_PYTHON` bindings is required for combined NISE acceptance. Default `contracts` mode records NISE qualification as `NOT_REQUESTED`.

No Woodpecker server, agent, OAuth application, persistent host or deployment has been created by this integration. Local execution is not a sandbox or hosted qualification. The existing GitHub release-evidence requirements remain intact; local Woodpecker receipts do not replace them.

Use the [five-gate release contract](docs/PRIVATE_RELEASE_CONTRACT.md), [executable gap inventory](release/private-host-gaps.json), [development checks](docs/DEVELOPMENT.md) and [capability verification report](mcp/verification-report.json) to assess a specific release. Remaining gates include approved live identity/upstream bindings, actual hosted storage/restore, larger OLM retention, applicable native/platform/physical evidence and operational acceptance. Missing required prerequisites and skipped checks do not become passes. Production SLO/RPO/RTO values require explicit decisions; local fixture targets are not commitments.

## Source and licensing

The package retains its `ciw` namespace and `net`/`ncp` entry points. Start with [services.py](src/ciw/services.py), the [plug-in contract](mcp/plug-in.json), [capability map](mcp/capability-map.json) and [tool contracts](mcp/model-catalog.json).

The [folder index](mcp/folder-index.json) and [project guide](docs/MCP_PROJECT.md) point to each folder's `MCP.md` or [external contract](mcp/source-contracts.md). External navigation preserves imported sources and exact-input capsules, including all 21 import pins. A folder is not a human console and does not opt for the easiest task in a set. Source paths and catalog rows confer no tool grant.

First-party code is declared `AGPL-3.0-or-later`, subject to explicit component exceptions: see [LICENSE](LICENSE) and the [licensing policy](docs/LICENSING.md). Imported components and separately bound providers retain their own licenses, copyright and provenance notices. NISE is an external MPL-2.0 provider, not an additional vendored import tree. Adapter integration expands no rights or counsel clearance.

For changes, read [CONTRIBUTING.md](CONTRIBUTING.md). Historical operator material remains in [OPERATOR_REFERENCE.md](docs/OPERATOR_REFERENCE.md), and the [implementation audit](docs/IMPLEMENTATION_AUDIT_20261102.md) records the earlier repair's scope and remaining limits.
