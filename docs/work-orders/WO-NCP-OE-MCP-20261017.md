# Extended work order: operational records to engineering investigations through MCP

**Work order:** WO-NCP-OE-MCP-20261017 · **Revision:** 1 · **Date:** 17 October 2026\
**Document status:** Complete implementation specification; implementation tasks below are proposed work.\
**Reference main revision:** `62577c96a280287c5df5d54783be00df1ab7c0de`\
**Delivery object:** An additive NCP operational-engineering investigation capability with personal MCP access.

## 1. Requested outcome

Connect the operational record to engineering, design, analysis and research work, and make that connection usable personally through MCP. Preserve the existing NCP architecture and its verified invariants.

The completed software must support four linked questions:

1. **Which configuration produced this temperature rise or other observed change?** Recover the actual recorded configuration and its applicability to the selected observations. If the association is unknown, report it as unknown.
2. **What changed?** Compare configurations, source conditions, calibration/model revisions and observed responses while preserving confounding differences.
3. **Does the measurement support the model?** Compare prediction and observation under declared semantics, applicability and uncertainty; distinguish agreement, disagreement and insufficient evidence.
4. **Which test should follow?** Produce an inspectable experiment proposal identifying the unresolved alternatives, required evidence, permitted methods and execution prerequisites.

All four answers must be accessible through MCP and traceable to retained records. A chat response alone does not satisfy the work order. Each journey must remain usable after the client disconnects and after an explicit supported restart.

Personal use is the intended operating context. This work order does not redefine the request as construction of a general-purpose SCADA product, a new operating system or a replacement for existing scientific engines. Acquisition continuity, historical queries and interruption handling are included where needed to deliver the requested operational-to-engineering workflows.

## 2. Architectural invariants

- Extend the existing Session, CapabilityRegistry, OperationRegistry, graph runner, measurement contracts, retained stores and agent/control boundaries. New adapters delegate to existing domain operations.
- Preserve distinct source/evidence, operation, execution, result, comparison, proposal and verification identities. An index or workflow receipt references these identities; it cannot replace them.
- Retain original bytes and interpretation context. Correction, filtering, aggregation and model evaluation create explicit derived records.
- Preserve unknown/missing values, timing uncertainty and correlated errors. Repetition, replay and duplicated delivery cannot be counted as new independent observations.
- Frozen scientific inputs remain frozen. Admitting a new recording creates a new bound source/investigation occurrence rather than silently refreshing an old profile.
- Separate device acquisition, numerical computation, retained inspection and proposed experiments in grants and tool descriptions. Inspection must not contact hardware or launch a provider.
- Local controller protections and existing operating gates remain independent of MCP, model output and UI availability. No proposal changes a grant or overrides a protection.
- Keep existing device/provider implementations and their applicability limits. Declared compatibility is not installed-chain qualification.

## 3. Inspected foundations and exact reuse obligations

The following files were inspected, and their presence was checked at the reference main revision. Except for `agent_mcp.py`, the listed source/doc blobs also match the inspected metrology working candidate. The main-revision MCP loader was inspected separately. These are source observations, not new execution or deployment results.

| Foundation                            | Existing surface                                                                                                                                              | Required use in this order                                                                                                                                                                                                |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agent tools and host profiles         | [agent_tools.py]\(../../src/ciw/agent_tools.py), [agent_mcp.py]\(../../src/ciw/agent_mcp.py)                                                                  | Reuse capability discovery, opaque retained inspection, observation paging, authorized execution and fixed comparisons. Extend the owning host boundary only when necessary.                                              |
| Frozen measurement MCP                | [link_measurement_agent.py]\(../../src/ciw/link_measurement_agent.py)                                                                                         | Preserve `devices_list`, `device_inspect`, `measurements_query`, `measurement_inspect` and `evidence_trace` semantics. These tools inspect a frozen bundle and do not discover devices or infer globally latest readings. |
| Acquisition and retained wire records | [link_gateway.py]\(../../src/ciw/link_gateway.py), [link_store.py]\(../../src/ciw/link_store.py), [link_measurements.py]\(../../src/ciw/link_measurements.py) | Retain original frames, configuration/manifest dependencies and receipt times. Extend bounded segment orchestration and queries around these contracts.                                                                   |
| History inspection                    | [evidence_history.py]\(../../src/ciw/evidence_history.py)                                                                                                     | Reuse retained sequence, boot/reset, overflow and consistency checks. Add engineering projections without introducing a competing event store or claiming authenticated history.                                          |
| Acquisition-to-estimation chain       | [ACQUIRED_STREAM.md]\(../../docs/ACQUIRED_STREAM.md), [acquired_window\.py]\(../../src/ciw/acquired_window\.py)                                               | Reuse existing acquisition, time reconciliation, calibration, features, estimates and lineage where the selected profile applies.                                                                                         |
| Residual and observability analysis   | [residual_monitor.py]\(../../src/ciw/residual_monitor.py)                                                                                                     | Reuse retained innovations, covariance, OIT gating and FDIR/CUSUM. Do not label an ambiguous scalar residual as an identified physical fault.                                                                             |
| Reference comparisons                 | [reference_comparison.py]\(../../src/ciw/reference_comparison.py)                                                                                             | Reuse declared alignment, full joint covariance and guarded outcomes. Extend semantics through explicit adapters.                                                                                                         |
| Design/uncertainty investigations     | [GAUGELINK_MCP_REFIT.md]\(../../docs/GAUGELINK_MCP_REFIT.md)                                                                                                  | Reuse existing Board preview, validation, baseline, recompute, compare and candidate flows where their lifecycle fits. Hypothetical improvement remains separate from measured improvement.                               |
| Read-only GPU telemetry               | [energy_nvml.py]\(../../src/ciw/energy_nvml.py), [energy_gateway.py]\(../../src/ciw/energy_gateway.py)                                                        | Reuse actual telemetry readers, host-call brackets and unavailable/refused outcomes. Presence of NVML is not a general laboratory gateway.                                                                                |
| Host recovery and presentation        | [agent_recovery.py]\(../../src/ciw/agent_recovery.py), [operator_workbench.py]\(../../src/ciw/operator_workbench.py)                                          | Preserve attempt identity, exclusive ownership and unknown interrupted outcomes; project the same retained results into operator views.                                                                                   |

GaugeLink's [documented development implementation](https://github.com/notationsystems/Notations-GaugeLink/blob/main/README.md) supplies finite receive-only acquisition and retained context. Its reviewed development branch, NCP dependency closure and physical compatibility must be identified together; the overview-only default branch is not an acquisition installation.

Metrology drafts [#236](https://github.com/notationsystems/Notations-Control-Protocol/pull/236), [#237](https://github.com/notationsystems/Notations-Control-Protocol/pull/237), [#238](https://github.com/notationsystems/Notations-Control-Protocol/pull/238) and [#239](https://github.com/notationsystems/Notations-Control-Protocol/pull/239) are separate review candidates. Their retained assessments, stale-bound proposals and HTTP transport may supply reusable patterns after review. The HTTP draft's existing sampled-metrology grants do not authorize general telemetry, collection or remote equipment access.

## 4. Required end-to-end journeys

| Journey                                    | Required behavior                                                                                                                                                                                             | Evidence retained                                                                                                                    |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| J1 — Inspect the operational record        | Through MCP, select an authorized investigation, inspect available channels/configurations and query an explicit recording interval. Report freshness, source-time semantics, receipt time, quality and gaps. | Selected snapshot/catalog generation, original recording references, query interval and projection lineage.                          |
| J2 — Explain a configuration-linked change | Select baseline and changed intervals, obtain a structured configuration diff and compare declared response quantities. Expose simultaneous changes and uncertain change boundaries.                          | Both configurations, their applicability, selected observations, transformation operations and comparison outcomes.                  |
| J3 — Examine model support                 | Bind the selected records to an existing approved model/provider, execute prediction and comparison, and retain residuals, uncertainty and applicability checks.                                              | Exact source/model/provider/policy revisions, prediction and measurement identities, numerical results and separately scoped checks. |
| J4 — Prepare the next investigation        | Link competing explanations to evidence gaps and produce a versioned experiment proposal with candidate settings, observable quantities, decision targets, resource limits and required grants.               | Finding dependencies, proposal revision, expected discriminatory evidence, prerequisites and invalidation conditions.                |
| J5 — Resume and review                     | Reconnect through MCP, recover the same retained investigation, inspect earlier answers and retry identical completed requests without new scientific execution. Keep interrupted unknown outcomes explicit.  | Original attempts/results, recovery receipt, unchanged source bindings and explicit fresh replay identities when requested.          |

These journeys apply across compatible retained recordings, Link/GaugeLink measurements, calibrated-window/thermal investigations and existing GPU telemetry records. The adapter inventory determines which combinations have applicable models. A missing instrument or provider must produce a specific unsupported/unavailable result while leaving the rest of the investigation inspectable.

## 5. Record and interface contract

Define only the connective records absent from existing contracts. Each candidate schema requires a compatibility review before registration.

| Object                      | Required information                                                                                                                                                                                                                              |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source/recording binding    | Exact bytes/content references, origin category, producer/device/boot and channel identity, source configuration, units, frame where applicable, calibration applicability, timing basis, capture quality, gaps and explicit completeness limits. |
| Configuration applicability | Configuration identity/revision and exact retained representation; effective event/sequence/time basis; uncertainty in that basis; hardware/firmware/provider/model revisions where known; fields actually observed versus operator-declared.     |
| Operational query           | Authorized source/snapshot generation, interval and clock domain, channel/quantity selection, pagination and declared transformations. Return range coverage and actual query limits.                                                             |
| Engineering binding         | Selected evidence and response quantity, model/parameter/provider/runtime identities, assumptions, validity domain, alignment, joint uncertainty or explicit unknowns, and fixed comparison/decision policy.                                      |
| Finding                     | Question, result references, numerical/empirical status, evidence supporting or contradicting it, unresolved alternatives, scope and invalidation dependencies.                                                                                   |
| Proposed experiment         | Finding and source/configuration/model/policy dependencies; hypothesis alternatives; intended observation/intervention; predicted distinguishing outcomes; procedure and applicability; execution requirements; current approval status.          |

Freshness, sample validity, transport connectivity and capture completeness are separate fields. A connected device can provide stale or invalid data. A closed capture can contain gaps. Last receipt time does not establish instrument sample time. An unknown uncertainty budget must not become zero uncertainty.

Configuration comparisons must report explicit unchanged, changed, absent and incomparable fields. Units or frame changes require explicit transformations. An order in a journal is not automatically an authenticated wall-clock ordering.

## 6. Implementation work packages

### WP01 — Freeze the actual integration inventory

**Work:** Record exact NCP/GaugeLink/provider revisions, installed dependencies, supported source families and present MCP operations. Identify reusable contracts, runtime gates and UI surfaces. Update the reference inventory against the chosen implementation base rather than assuming all drafts are landed.

**Deliverables:** A source-family/capability matrix, dependency manifest, executable integration plan and recorded baseline test results.

**Acceptance:** Every required journey maps to existing operations or an explicitly identified addition; each source family has available, unavailable and unsupported cases; no existing history, acquisition or monitor implementation is misreported as absent.

**Dependencies:** None. **Candidate surfaces:** existing integration documentation, runtime manifests and the work-order acceptance matrix.

### WP02 — Bind operational records to effective configurations

**Work:** Add the missing connective contract and adapters around existing manifests, recordings and configuration events. Bind units, frames, clocks, calibration and model identities without replacing existing schemas. Handle captures spanning configuration changes and uncertain effective times.

**Deliverables:** Strict schema/adapter, migration-free compatibility reader, reference fixtures and focused tests.

**Acceptance:** Missing or contradictory bindings cannot produce a definitive configuration attribution. Exact original identities survive round-trip. A configuration change during acquisition splits interpretation or holds the affected interval explicitly. Recorded metadata and physical observations remain distinguishable.

**Dependencies:** WP01. **Candidate surfaces:** existing Link contracts/semantics and one additive operational-context module if needed.

### WP03 — Retain acquisition continuity and admit new source snapshots

**Work:** Extend existing capture/worker infrastructure to maintain ongoing personal recording sessions as bounded retained segments. Specify start/stop ownership, cadence, rotation, disk quota, backpressure, disconnect behavior and explicit restart. Host-controlled admission creates new immutable investigation inputs for closed segments.

**Deliverables:** Collector lifecycle, segment/admission receipts, state inspection and failure fixtures; actual limits recorded in the host profile and result.

**Acceptance:** Collection does not depend on an open chat. Disconnects, overflow and incomplete segments remain visible. A full store applies the declared stop/hold policy without silent overwrite. Reopening does not reconnect to hardware. New arrivals do not mutate an old scientific source or its grant. Uncertain in-flight capture outcomes are not silently repeated.

**Dependencies:** WP02. **Candidate surfaces:** existing Link gateway/serial/runtime/store, approved telemetry adapter and existing worker/control lifecycle.

### WP04 — Add historical and configuration-difference projections

**Work:** Query retained segments by authorized identity, quantity and explicit interval; compute a detached configuration diff and quality/coverage projection. Reuse existing journals and history validators. Indexes must be reconstructible from their retained sources.

**Deliverables:** Shared Python/CLI query functions, paged outputs, lineage-bearing diffs and interval edge-case tests.

**Acceptance:** Multi-boot/time-domain ambiguity is explicit. Gaps, late data, duplicate deliveries, equal-valued distinct observations and out-of-order receipt are handled distinctly. Aggregate or resampled values are derived results, not new observations. Snapshot queries are repeatable; latest-within-snapshot is not advertised as a globally current measurement.

**Dependencies:** WP02–WP03. **Candidate surfaces:** `evidence_history`, Link measurement readers and an additive query projection.

### WP05 — Join the operational record to engineering calculations

**Work:** Bind queried evidence to the existing relevant temperature/thermal, calibrated-window, residual-monitor, scalar-study or supported design operations. Add explicit mapping adapters where semantics differ. Preserve original pre-correction and pre-update quantities.

**Deliverables:** Registered domain adapters, fixed experiment graphs and independently checkable response/comparison fixtures.

**Acceptance:** Prediction, observed response, residual and comparison have distinct lineage. Missing calibration, unresolved clock/frame alignment or unsupported covariance holds the interpretation. Repeated/shared evidence cannot improve uncertainty by being counted twice. Synthetic/model-only outputs cannot acquire physical-observation status. Unsupported source/model pairings refuse explicitly.

**Dependencies:** WP04. **Candidate surfaces:** existing Session/runner, acquired/calibrated windows, measured thermal, reference comparator and residual monitor.

### WP06 — Produce evidence-linked findings and competing explanations

**Work:** Assemble structured answers to the first three engineering questions from actual results. Name confounding changes, unsupported assumptions and competing explanations. Bind every material claim to retained evidence or label it as an untested hypothesis.

**Deliverables:** Finding contract, deterministic structured report and operator/MCP-readable explanation fields.

**Acceptance:** Association alone does not become causation. An ambiguous scalar innovation does not identify a sensor/process/calibration fault uniquely. Explanations remain reviewable without an LLM service. If an assistant supplies prose, that prose references the structured record and cannot overwrite its status or measurements.

**Dependencies:** WP05. **Candidate surfaces:** existing report/inspection layer and one additive finding projection.

### WP07 — Prepare and invalidate proposed next experiments

**Work:** Link each unresolved explanation to candidate discriminating observations or supported tests. Use existing operation capabilities and declared parameter domains. Retain procedure, expected distinguishing outcomes, uncertainty/decision targets, resource limits and execution prerequisites.

**Deliverables:** Proposal contract, capability-based preparation adapter, stale-dependency checks and inspection/report output.

**Acceptance:** A proposal identifies what evidence would distinguish its alternatives; where the existing model cannot predict that, the limitation is explicit. Changed source, configuration, model, policy or finding invalidates the relevant proposal binding. Preparation performs no device command, workload launch, calibration edit or approval. Test selection does not silently change the acceptance rule after seeing its outcome.

**Dependencies:** WP06. **Candidate surfaces:** existing experiment/parameter contracts and reviewed proposal patterns; preserve prior identities rather than creating a second experiment engine.

### WP08 — Expose the complete journeys through personal MCP

**Work:** Add an operator-prepared operational-engineering MCP profile. Reuse existing measurement inspection and `net_capabilities`, `net_inspect`, `net_observe`, `net_execute`, `net_compare` and Board tools wherever applicable. Add tool names only for genuinely missing domain operations; document each new tool as a proposal until implemented.

**Deliverables:** Shared domain methods, strict input/output schemas, generated client configuration, stdio server integration and real SDK tests. Reads, acquisition requests, authorized numerical execution and proposal preparation must have distinct grants.

**Acceptance:** A real client completes J1–J5. Arbitrary source paths, URLs, imports, devices, providers, grant edits and policy selectors refuse. Same-attempt retry preserves outcomes, parameter changes require new identities, and unknown interrupted work is not automatically dispatched. Existing frozen MeasurementHost semantics remain intact. Python, CLI and MCP return the same underlying records and decisions.

**Dependencies:** WP04–WP07; implementation may proceed alongside domain work once interfaces are fixed. **Candidate surfaces:** existing agent/control hosts, `link_measurement_agent`, `agent_tools`, `mcp_server` and profile/CLI wiring.

### WP09 — Preserve access across laptop and worker boundaries

**Work:** Supply local stdio personal access and define/test the authenticated transport binding needed when the selected collector/worker resides on another host. Use the existing worker and approved MCP transport seams. Explicitly bind caller, investigation, source-admission policy, capabilities and worker identity; restrict remote methods to those implemented by this order.

**Deliverables:** Local profile/configuration, remote-compatible adapter and isolation/revocation/restart tests; installation instructions for selected deployment paths.

**Acceptance:** Another caller/investigation cannot query or execute against the selected record. Revocation denies subsequent calls without claiming to undo already dispatched work. Remote arguments cannot nominate a shell or filesystem/URL target. Each returned measurement retains the acquisition host/clock identity. A dropped client cannot disable collection accounting or local protections.

**Dependencies:** WP03 and WP08. **Candidate surfaces:** existing external-work/control authentication and worker boundaries; review HTTP metrology patterns separately before reuse. Public hosting, credentials and actual external deployment require their own selected environment; the local journey must work independently.

### WP10 — Present the same investigation in the existing operator view

**Work:** Connect operational interval selection, configuration diff, model comparison, findings and next-test proposal to existing operator/Board presentation. Expose source/result lineage and freshness without duplicating scientific state.

**Deliverables:** Operator panels or existing-view extensions, navigable evidence links and browser tests for the selected interface.

**Acceptance:** MCP and UI inspect the same result identities. Historical selections are visibly historical; missing/stale data cannot appear as a current healthy measurement. A design scenario remains hypothetical until supported by separate measured evidence. User annotations remain append-only review context, not device alarm resets or equipment acknowledgements.

**Dependencies:** WP06–WP08. **Candidate surfaces:** existing operator workbench, Board view and MicroGauge presentation seam where available; no redesign of unrelated website work.

### WP11 — Verify complete workflows, failures and compatibility

**Work:** Exercise every journey through actual providers where available, the real MCP SDK, restartable processes and an installed wheel. Build, run, inspect, correct and audit throughout implementation; the final acceptance run covers the complete connected journey.

**Deliverables:** Reproducible test driver, source and installed artifacts, test results, provider identities, independent references and an acceptance report tied to the exact candidate.

**Acceptance:** All applicable scenarios in Section 8 pass; required but unrun scenarios remain open. Existing AgentHost, measurement, Link, Board and selected numerical regressions pass. No source fallback during installed tests. Fresh replay creates fresh execution/result occurrences; retained inspection creates none. Browser checks run against actual selected UI assets.

**Dependencies:** WP01–WP10. **Candidate surfaces:** existing pytest configuration, focused tests, provider qualification scripts and selected existing CI workflows.

### WP12 — Deliver the usable installation and evidence package

**Work:** Package documented installation/start/reconnect/stop/inspect/export procedures; include actual MCP client configuration and selected profile setup. Record what is implemented, what was executed and what remains physically/deployment-unqualified.

**Deliverables:** Installable wheel, resolved configuration/dependency manifest, complete J1–J5 operator guide, representative retained investigation, accepted test report and remaining prerequisites by deployment/source family.

**Acceptance:** An operator can follow the guide through the complete workflow without inventing missing steps or treating a demonstration as a measurement. Reports distinguish numerical completion, model support, physical validation and permission to act. Code review receives concrete changes and exact validation results; no undocumented gate relaxation or automatic merge/release is part of this order.

**Dependencies:** WP11.

## 7. Execution and review organization

WP01–WP02 fix the integration contracts. Acquisition continuity and historical projections can then develop alongside domain integration; findings, proposals, MCP and operator views share those same records. This is delivery sequencing within one extended work order. The completion target is all required connected journeys.

Keep implementation PRs reviewable by owned interface: contract/admission; acquisition/history; engineering bindings/findings; experiment proposals; MCP/transport; presentation; integration evidence. Split further if inspection exposes a separately owned change. Every PR includes its relevant tests and compatibility results; the final integration report covers the combined candidate. Do not replace the requested capability with a demonstration-only closeout.

Use existing pytest settings. Run focused new tests and affected regressions when changes warrant them. Build a wheel at domain/MCP integration checkpoints and verify the installed artifact. Preserve passed evidence; rerun a check when changed code, environment, inputs or an unresolved failure makes its earlier result insufficient.

## 8. End-to-end acceptance matrix

All entries start **NOT RUN for this work order**. Existing earlier test results provide baseline evidence only. Each result must name the candidate revision, inputs, environment, observation method and its actual outcome.

| ID  | Scenario                                                                    | Required outcome                                                                                                                                         |
| --- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01 | Normal operational record with a recorded configuration change              | MCP recovers exact bindings, computes the intended diff and traces each finding to retained records.                                                     |
| A02 | Change occurs inside a capture; effective time is uncertain                 | Affected observations are split or held; no fabricated exact attribution.                                                                                |
| A03 | Data missing, stale, malformed, outside range or absent                     | Distinct quality states; no zero-filled measurement or current/healthy status.                                                                           |
| A04 | Clock drift/reset, new boot, late or reordered receipts                     | Clock/boot identities and ambiguity retained; no fabricated common timeline.                                                                             |
| A05 | Duplicate delivery versus separate equal-valued measurements                | Delivery duplication is accounted for; actual distinct observations remain distinct; no false information gain.                                          |
| A06 | Shared calibration/model error and correlated windows                       | Declared dependencies retained; unsupported covariance remains unknown; guarded comparison remains honest.                                               |
| A07 | Model supported, contradicted and insufficient-evidence cases               | Separate reproducible outcomes under a policy fixed before comparison; no promotion to physical truth.                                                   |
| A08 | Multiple configuration changes or observational confounding                 | Alternatives remain visible; no unjustified single-cause diagnosis.                                                                                      |
| A09 | Candidate proposal, changed source/model/policy and changed finding         | Exact dependencies bind the proposal; changed dependencies invalidate it; no execution during drafting.                                                  |
| A10 | Invalid MCP arguments and capability escalation attempts                    | Refusal before provider/device dispatch; no grant or selector mutation.                                                                                  |
| A11 | Completed attempt retry and explicit fresh replay                           | Retry returns the original outcome; fresh replay has new occurrences and remains distinguishable from new acquisition.                                   |
| A12 | Client disconnect, collector interruption, worker/provider kill and restart | Retained work remains inspectable; incomplete/unknown outcomes remain explicit; no automatic duplicate device action.                                    |
| A13 | Segment/store quota and backpressure                                        | Declared stop/hold behavior, retained overflow/gap evidence and bounded memory/disk use; no silent overwrite.                                            |
| A14 | Retained inspection with device/provider calls disabled                     | Query, trace, report and reopen succeed without collection or numerical execution.                                                                       |
| A15 | Revocation, wrong investigation/caller and remote binding substitution      | New unauthorized calls deny; no cross-investigation data access or arbitrary host access.                                                                |
| A16 | Same investigation through CLI, real SDK MCP and selected operator view     | Same record identities, quantities, decisions and qualification status.                                                                                  |
| A17 | Source versus installed wheel                                               | Package import and asset identities match the wheel; complete MCP journey works outside the source checkout.                                             |
| A18 | Actual physical acquisition unavailable                                     | Synthetic/replayed/recorded status remains explicit; software tests finish and physical acceptance stays separately unestablished.                       |
| A19 | GPU profile: temperature at/above 80°C or invalid/unavailable prerequisite  | Existing local policy blocks any separately gated diagnostic workload; inspection/read-only telemetry does not silently launch one. Equality is blocked. |
| A20 | Earlier verified profiles and records                                       | Relevant regression suites pass; original schemas, identities, grants and replay meanings remain compatible.                                             |

## 9. Scientific comparison and next-test requirements

A structured answer to “does the measurement support the model?” must name the response quantity, model validity domain, selected independent/held-out reference where applicable, parameter-fitting data, comparison data, uncertainty treatment and policy. Model fitting and held-out validation must be distinguishable. Numerical agreement under a declared model is the specific supported claim.

The next-test proposal must state the alternatives being tested, what outcome would discriminate among them, controlled and observed variables, expected confounders, instrument/model prerequisites and predeclared decision criteria. It must identify whether the proposed operation is a retained-data analysis, simulation, new acquisition or physical intervention. Missing predictive capability is an evidence gap, not a reason to invent a test's information gain.

For polymer/materials work, preserve material/lot/formulation and geometry/process revisions where supplied; connect processing conditions to measured properties and design requirements through explicitly supported models. For GPU investigations, preserve device/driver/configuration and acquisition timing context. Neither domain inherits physical calibration or causal identification from its integration into NCP.

## 10. Evidence and deployment prerequisites

| Prerequisite                                                        | Work that can finish independently                                                                                                    | Acceptance requiring the prerequisite                                                 |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Physical device, supported connection and acquisition permission    | Contracts, segmentation, retained imports, lifecycle/failure tests, numerical integration and SDK journeys using explicit test inputs | Actual instrument compatibility and physical acquisition fidelity.                    |
| Calibration, independent references and representative observations | Uncertainty plumbing, conditional comparisons, held/unknown behavior and reproducible software checks                                 | Physical accuracy, installed-chain uncertainty and empirical model-validation claims. |
| Exact optional provider/runtime access                              | Adapter contracts, refusal/recovery tests and other available provider integrations                                                   | Genuine native execution and qualification of that provider path.                     |
| Selected laptop/worker environment and credentials                  | Local stdio installation and authenticated transport/isolation tests                                                                  | An actual remote installation and network/credential qualification.                   |

Absent physical or deployment evidence must not be replaced by relabelling fixtures. It also must not leave implementable contracts, software journeys, tests or packaging unfinished. No stress test, driver/power change, device calibration, actuator command, public deployment or approval grant is authorized merely by creating this work order.

## 11. Closeout record

Closeout must include the exact code revision and wheel digest; source-family capability matrix; environment/provider identities; all J1–J5 receipts; A01–A20 outcomes; affected regression results; actual MCP client configuration; representative reopening/replay evidence; documentation checks; and separate physical/deployment prerequisites.

The work order is complete as a specification. Its implementation is complete only when the required connected software journeys and applicable acceptance scenarios have executed successfully on the delivered artifact. Physical or remote deployment acceptance is reported separately against its supplied evidence. Existing metrology plans and earlier qualification records remain unchanged.