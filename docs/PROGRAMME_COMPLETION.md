# ODSP programme completion

This campaign starts at `235ee45a11f744e8c801364965c07e9e3e13854f`, whose tree is
identical to the earlier `9c89157c` campaign. That earlier source is preserved.
This document records implementation obligations; entries below are not claims
of completed qualification or permission to activate services.

The user-supplied audit findings and independent corrections are reconciled in
[Audit reconciliation and release holds](AUDIT_RECONCILIATION.md). Credential
and companion containment take priority while the remaining programme proceeds.
Effort estimates are planning inputs, not delivery evidence or a waiting period.

## Immediate internal-use sequence

The current priority is reliable work for the company's own physical,
geographic and molecular investigations. The first deliverable is a readable
source checkpoint for other contributors; it is not a qualified deployment.
Keep the broader obligations below in the backlog. External-client-only token
lifecycle and rate-limit expansion are deferred for this internal pilot; existing
controls for any exposed system remain required.

Use the following sequence. Owners are responsibility boundaries to assign in
the development team, not statements that a named person has accepted work.
No source, instrument, cloud account or reviewer is selected by this document.

| Priority and owner boundary | Work to deliver | Dependencies and measurable acceptance |
| --- | --- | --- |
| 1. Durable coordinator, retention and recovery — persistence owner | Reconcile PostgreSQL claims, database-clock leases, fencing, NATS outbox wake-ups and the separately owned retained artifacts into one internal custody contract. Select direct PostgreSQL or a compatible managed PostgreSQL endpoint; PlanetScale is a candidate. Define backup ownership, RPO/RTO and an operator recovery procedure. | Start from [coordinator contracts](DISTRIBUTED_COORDINATOR.md), [retention](../src/ciw/retention.py) and [owned recovery](PRIVATE_HOST_OPERATIONS.md#recovery-and-owned-backup). Competing workers must dispatch at most once per admitted attempt; stale fences, duplicate wake-ups and reconnects must not repeat effects. A restart and a separately restored backup must preserve owner, generation, pending intent, outcome uncertainty and audit identities. Record observed restore time and data-loss interval against explicitly chosen targets. Local PgBouncer tests do not qualify a hosted endpoint or an object-store restore. |
| 2. One or two actual offline-to-governed intake flows — intake owner | Select real company-owned exports, beginning with a recorded sensor/OPC UA or MQTT export and, if needed, one geographic or molecular observation dataset. Bind approved source, usage, exact raw bytes, clock/frame/unit/calibration metadata and an explicit intake attempt. Retain a separate unadmitted observation. | Reuse [offline intake](OFFLINE_INTAKE.md), [data-instrument contracts](DOMAIN_H_DATA_SYSTEMS.md), and [metrology](METROLOGY_OPERATIONS.md). Reopening must recover the same raw hash and source identity; exact repeated attempts are idempotent and conflicting attempts refuse. Test malformed rows, duplicate samples, stale data, schema/control drift, revoked access and full stores. A sticky drift hold must block downstream trust until the exact impact review is accepted. Identify the actual TBRT calculation and NSL admission records where supplied; otherwise mark time authentication and admission unestablished. |
| 3. Recorded journal, simulator, one read-only instrument — instrument owner | Implement the first bounded journal using the existing [device-gateway contract](DEVICE_GATEWAY.md), then verify a simulator wrapper and one separately selected physical instrument read. Carry device and acquisition identities, original timestamps, clock mappings, frames, units, calibration applicability, uncertainty, ordering and gaps. | Gate each stage independently against [device acceptance](DEVICE_GATEWAY_ACCEPTANCE.md) and [companion policy configuration](COMPANION_POLICY_CONFIGURATION.md). The simulator must demonstrate bounded stop, partial-read/failure records, duplicate/dropout/late-data handling and no resend after uncertainty. Before the physical read, obtain the exact instrument, endpoint/transport, credentials if any, allowed read and evidence/retention scope. A suitable host must pass real isolation checks. No actuation, excitation, motion, controller write or Operate mode is included. Simulator and protocol-server tests do not establish physical validity. |
| 4. One held fusion/projection — scientific and representation owners | Choose one physical-plus-geographic case or a molecular case using exact retained observations. Bind the model and provider versions, clock/frame transformations, sample support and the uncertainty model. Retain computation and a read-only held projection for human inspection. | Reuse [covariance](COVARIANCE.md), [visual evidence projection](VISUAL_EVIDENCE_PROJECTION.md), applicable [GIS](GIS_RS_EXECUTION.md) or [molecular](FLUID_MOLECULAR_PROVIDER.md) contracts. Use an independent reference with declared tolerances. Test shared error/cross-covariance, duplicate observations, missing/late inputs and unavailable mappings; unsupported combinations must hold. Every displayed derived value must resolve to exact inputs, execution and uncertainty. Projection never admits evidence, becomes canonical state or authorizes an action. Human review is an explicit retained decision. |
| 5. Private operator and recovery tooling — operator owner | Package current principal policy, private panel, capability holds, retained inspection and backup/recovery runbooks for the selected internal deployment. Surface pending, held, refused, completed and unknown outcomes distinctly. | Start from [operator panel](PRIVATE_OPERATOR_PANEL.md), [operations](PRIVATE_HOST_OPERATIONS.md), [release gates](PRIVATE_RELEASE_CONTRACT.md) and current audit records. An operator must inspect a source-to-result chain, revoke access, diagnose a failed run, reopen after restart and restore owned data without automatic replay. Record retention latency, job outcome/completion, capacity and audit-chain checks under bounded failure conditions. Specify thresholds before calling them SLOs. An internal interface still requires appropriate identity, ingress, credential and isolation controls for its actual exposure. |

The extension sits above existing logistics, historian, PLC/SCADA, robotics,
geographic and laboratory systems. Their source and controller authorities remain
separate. The existing OLM adapter only inspects retained upstream artifacts; it
does not submit logistics jobs or independently authenticate upstream receipt
claims. The NSL adapter only replays approved captured responses and does not
perform admission. Establishing a live knowledge graph, source-authenticated
time or an NSL writer requires an independently reviewed integration.

Keep these distinctions in every handoff: source implementation, executable
local verification, installed artifact, hosted operation, physical measurement
and human acceptance. A source branch or local fixture cannot close a stronger
gate. Current native execution is held where the real Bubblewrap namespace probe
refuses; the trusted hardware collector also requires an eligible root-owned
`lsblk`. Do not relax either prerequisite to obtain a green run.

The supplied assessments are design context, not ODSP qualification reports.
The first explicitly examined ORPHIC-LOGISTICS-ENGINE after failing to access
ODSP; its architecture and market statements are not evidence about this source.
The other supplied inventories can describe an earlier published revision.
Relevant input references are Library files
`libfile_187a6298f72c8191972da91bb1feb980`,
`libfile_097327d980c881919c4c334dd2a566fc`,
`libfile_e170f6359a948191a0dc2518f015eda8` and
`libfile_cf57fa29ada481919a9c6bc939787b37`.
No market statistics, customer readiness or equipment-control certification are
adopted from those assessments.

Before contributors take a slice, assign disjoint files and agree any shared
contract change with the integration owner. Bind tests to the actual candidate
source and retain commands, dependencies, outcomes and holds. Preserve all six
Domain H profiles and the remaining Rust/proof/research obligations below;
deferring them from this pilot does not complete them. In particular the
representation/morphism and systemic-control-plane directions, curriculum,
general OpenUSD interchange, broader control/design kernels and equipment
authority modes remain governed by [unimplemented directions](UNIMPLEMENTED_DIRECTIONS.md).

## Architecture and dependency order

The current principal facade, scientific operation contracts, source/evidence
identities and durable uncertainty rules remain the common authority. New
transports and orchestration must reach that same boundary. Imported domain
engines retain their source identities and licensing holds.

1. Extend execution behind host-selected runtime bindings: native Rust computes
   the seven existing numerical operations, with Python-oracle comparisons and
   committed native runtime identity. Policy remains independent of the kernel.
2. Add PostgreSQL as the authoritative coordinator store. Its transactional
   claims, database-clock leases, fencing and durable intent precede execution.
   A transactional outbox sends NATS JetStream wake-ups; delivery never grants
   authority and cannot restart an uncertain attempt.
3. Add a stable MCP toolbox surface. Host-approved components are activated by
   opaque handles; each inner operation reaches current principal authorization
   and a separate required audit chain. Persisted signed workflows are verified
   against current trusted signer state. Shared cache entries never cache grants
   or replace fresh execution/verification occurrences.
4. Expose native official-SDK Streamable HTTP with OAuth protected-resource
   metadata and externally issued bearer validation. Bind the issuer, resource,
   subject and scopes in host configuration; refresh current grants per request.
5. Connect read-only industrial middleware through host-bound ROS2, OPC UA and
   MQTT adapters. Preserve acquisition/receipt time, quality and original bytes.
   Software simulator checks do not qualify physical devices or measurements.
6. Connect operational metrology to the existing native scientific workflows,
   retaining plan, source, calibration applicability, execution and review
   separately. Proof work uses the existing SCR/SP1 boundary and explicit Kani
   harnesses; neither receipts nor signatures are substitutes for native proofs.
7. Add Domain H data systems after reconciling PPDA/SCOUT, SRA and NSL against
   their actual retained source. Treat each registered data source as an
   instrument: known-good sample calibration, freshness and schema checks,
   immutable dataset provenance, and a persistent drift hold pending an impact
   review. The six profiles are collection (`ncp_collect_*`), pipelines
   (`ncp_pipeline_*`), analysis (`ncp_analysis_*`), database construction
   (`ncp_db_*`), database operations (`ncp_dbops_*`) and mining
   (`ncp_mining_*`). Their implementation and evidence ledger is
   [Domain H data systems](DOMAIN_H_DATA_SYSTEMS.md).

Domain H collection is limited to places, assets, infrastructure and imagery.
Person resolution, stealth collection and spoofed browser identity are excluded.
Registration must retain licence, terms, robots status, honest client identity
and rate limits; a storage hash does not establish permission to collect.
TBRT timestamp and NSL admission references must resolve to the actual verified
records. A local clock or newly minted receipt cannot stand in for either.
Pipelines bind exact input, code and output versions and reject breaking schema
changes. Derived values retain uncertainty and dataset lineage. Database write
surfaces produce reviewable migration plans; applying them is a separate human
approval. Destructive maintenance and mining findings cannot auto-act.

Shared-file integration is sequential. Independent implementations own disjoint
new files until the integration owner reconciles their contracts and tests the
combined result. No new endpoint, grant, secret, device action or paid allocation
is activated by installing these modules.

## Acceptance obligations

| Area | Required executable evidence | External qualification |
| --- | --- | --- |
| Rust kernels | Actual native execution, all seven operations, independent numerical comparisons and refusal boundaries, runtime drift rejection | Target architecture/runtime qualification |
| PostgreSQL/NATS | Real local servers, competing workers, restart, duplicate deliveries, stale fence and unknown outcome tests | Selected hosted store/broker and recovery drill |
| Toolbox/workflows/cache | Actual MCP calls, signature and revocation checks, restart/concurrent processes, current-policy disclosure and dispatch | Production signer custody and storage policy |
| HTTP/OAuth | Official SDK client over TCP, tokens/metadata/audience/scopes/revocation/host checks, restart | Approved identity provider, TLS and private ingress |
| Industrial middleware | Actual protocol clients against local servers or ROS2 middleware, bounded stop and negative cases | Named approved devices, credentials, timing/calibration evidence |
| Metrology | Existing scientific engines, exact source/calibration binding, durable restart and review, negative cases | Independent physical measurements and operator acceptance |
| Native proofs | Installed SP1 proof/verify and Kani results with actual toolchain/statement identity | Required registered guest and trusted-key custody |
| Domain H data systems | Verified component reconciliation, calibrated sample/schema/freshness checks, sticky drift and impact review, exact dataset receipts, reproducible pipelines/analysis, migration dry runs and evidence-backed mining | Approved sources, R2 storage, live database/broker identities, actual TBRT/NSL admission and separately approved migrations or destructive actions |
| Release | Complete baseline, current installed package/container, current hosted CI and deployment receipt | Protected registry/access/storage/spending approvals |

Missing prerequisites remain explicit blockers. Skips, source inspection, local
unit tests and earlier-image receipts cannot close a stronger evidence gate.
