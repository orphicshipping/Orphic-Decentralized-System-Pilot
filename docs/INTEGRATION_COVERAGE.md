# Integration coverage and evidence boundaries

Source audit refreshed: **2026-10-04**, inspected baseline
`554bb1540ef1b621af9e08f2d632095ca958150d`. This is a selected inventory of
implemented source surfaces and their tests, not an exhaustive operation list
or a new execution report for every provider. The refresh checks the MCP
implementation, protocol guide, tests and workflow, and clarifies inventory
categories. The [2026-10-03 concerns audit](CONCERNS_AUDIT_2026-10-03.md) is a
historical record at its own stated baseline, not the current inventory.

**Implemented** means a callable implementation and associated contracts are
present. **Bound provider** additionally requires the operator's exact checkout,
executable and dependency closure. A named operation, capability record or
source file establishes neither qualification nor physical validity. An
unavailable provider must refuse rather than manufacture a result.

Read the inventory in four categories: built-in kernels execute repository-owned
calculations; optional bound providers require separately provisioned runtimes;
client integrations expose existing operations or views; planning guides describe
work that has not supplied the stated executable path. These are implementation
categories, not scientific or physical qualification levels. CI configuration
is a runnable test definition, not evidence that its latest run passed.

## Current source addendum — 2026-10-04

Inspected source: `554bb1540ef1b621af9e08f2d632095ca958150d`, matching the
refreshed inventory above. These selected paths extend the historical
2026-10-03 audit; their source presence does not report a new execution of all
providers or qualify the full research release. The
[model validity and evidence guide](MODEL_VALIDITY_AND_EVIDENCE.md) explains how
to read declared limits, numerical checks and physical evidence separately.

| Current path | Source and operating guide | Evidence boundary |
| --- | --- | --- |
| Local MCP agent interface | [Implementation](../src/ciw/agent_mcp.py), [protocol and qualification](NET_AGENT_PROTOCOL.md) | Bounded local tools use existing execution, observation, comparison and replay contracts. A served profile or successful transport check does not qualify its scientific model or authorize equipment. |
| Affine temperature processing | `metrology.temperature.process.v1`: [contract](../src/ciw/temperature_contract.py), [processor](../src/ciw/temperature_processing.py), [MCP profile](NET_AGENT_PROTOCOL.md#temperature-measurement-processing-profile), [tests](../tests/test_temperature_processing.py) | 1–64 observations, full joint raw/calibration covariance, declared applicability, optional installed correction and conditional sample-instant acceptance. Applicability is checked against submitted declarations; traceability, authenticated acquisition and physical qualification remain unestablished. |
| Thermal estimation from recordings | `ciw.measured-thermal.v1`: [guide](MEASURED_THERMAL.md), [contract](../src/ciw/measured_thermal.py), [tests](../tests/test_measured_thermal_review.py) | Already-calibrated shell, power and ambient streams feed the Python estimator; a held-out core stream supports comparison. Input uncertainty is retained but is not propagated by this kernel. Fixtures are simulated, and numerical comparison does not establish installed-chain validity. |
| Coupled thermal/thermoelastic references | [System composition](SYSTEM_COMPOSITION.md), [workflow](../src/ciw/system_workflow.py), [tests](../tests/test_system_workflow.py) | A thermal rod and quasi-static axial mechanics have bounded balance, analytic and refinement checks. Coupling is one-way; constitutive extensions and experimental material validation require separate evidence. |
| Polymer temperature pilot | [Qualification protocol](TEMPERATURE_QUALIFICATION.md), [setup record](../examples/temperature/pilot-setup.json), [conformity guide](TEMPERATURE_PRODUCT_CONFORMITY.md) | A programme and unfilled records for one installed measurement chain. Actual hardware, installation, operating envelope, required uncertainty and experimental results remain required. PINN/gPINN comparison and a temperature-specific SP1 guest remain planned. |

The affine temperature and recorded-thermal workflows have separate contracts and
no automatic handoff. Preserve observation/estimate labels, units, time/location
semantics, uncertainty and lineage when preparing a study. Neither a calibration
record hash nor a computation proof establishes the physical calibration chain.

## Shared host and provider-free foundations

| Surface | Merged implementation and tests | Scope and remaining gate |
| --- | --- | --- |
| Terminal, Python API and local WebSocket session | [session](../src/ciw/session.py), [server](../src/ciw/server.py); [protocol tests](../tests/test_protocol.py), [integration tests](../tests/test_integration.py) | Implemented local host. Remote authentication, multi-user deployment and hard real-time control are not supplied. |
| Statistics and periodogram | `statistics.v1`, `spectrum.periodogram.v1`: [registry](../src/ciw/operations/registry.py), [instruments](../src/ciw/instruments.py); [instrument tests](../tests/test_instruments.py), [runner tests](../tests/test_operation_runner.py) | Implemented bounded analysis. Legacy result envelopes remain `not_verified`; explicit reference checks are separate. |
| Retained source, result, failure, save/reopen and replay | [workbench](../src/ciw/workbench.py), [attempts](../src/ciw/workflow_attempts.py); [session tests](../tests/test_workbench_session.py), [failure-history tests](../tests/test_workflow_failure_history.py) | Implemented catalog and explicit fresh replay. Reopen performs no provider execution. Retained terminal failures are not crash recovery for in-flight work. |
| Encoder/gearbox/leadscrew configuration | `ciw.encoder-position.v1`: [manifest](../src/ciw/machine_manifest.py), [workflow](../src/ciw/machine_workflow.py); [manifest tests](../tests/test_machine_manifest.py), [lifecycle tests](../tests/test_machine_workflow.py) | Implemented evidence-backed, read-only position profile and first-order joint covariance. No document authenticity check, physical commissioning, firmware loading or actuation. |
| Typed project declaration and dependency status | `ciw.project-graph.v1`: [model](../src/ciw/project_model.py), [workflow](../src/ciw/project_workflow.py); [model tests](../tests/test_project_model.py), [lifecycle tests](../tests/test_project_workflow.py) | Implemented append-only typed declarations, separate physical/computation/evidence edges and `needs_reevaluation` for changed pinned inputs. Does not execute declared graph computations or bind every workbench result automatically. |
| Local correction journal and retained-artifact claim graph | [journal](../src/ciw/correction_journal.py), [dependency projection](../src/ciw/dependency_graph.py), [runnable correction gate](../scripts/check_correction_loop.py) | Implemented append-only predicted/estimated claims, source correction proposals and final local reviews. Accepted reviews mark withdrawn source aliases and transitive descendants stale; prior bytes remain unchanged. Reviewer names are declarations, not authenticated principals. Claims stay `not_verified`, unadmitted and unauthorized. Unknown native input references remain explicit. |
| Data-only scalar reference import | [reference contract](../src/ciw/reference_evidence.py), [session tests](../tests/test_correction_session.py) | Retains exact reference source bytes and declared quantity, origin, unit, frame, timestamp and uncertainty. Reference-dependent claims can become stale independently of model outputs. No executable operation or physical acquisition is supplied. |
| Synthetic thermal observer and sensor choice | `ciw.thermal-observer.v1`: [contract](../src/ciw/thermal_contract.py), [reference](../src/ciw/thermal_reference.py), [workflow](../src/ciw/thermal_workflow.py); [contract tests](../tests/test_thermal_contract.py), [lifecycle tests](../tests/test_thermal_workflow.py) | Implemented Python two-capacity model, dropout-aware Joseph update and bounded sensor selection. Julia thermal worker and held-out physical measurements remain separate gates. |
| Retained measured thermal ingress and held-out core comparison | `ciw.measured-thermal.v1`: [contract](../src/ciw/measured_thermal.py), [workflow](../src/ciw/measured_thermal_workflow.py), [tests](../tests/test_measured_thermal.py), [review regressions](../tests/test_measured_thermal_review.py), [operator guide](MEASURED_THERMAL.md) | Four exact-byte observation streams; existing Python thermal kernel; shell-only conditioning; retained calibration declarations and clock/frame/uncertainty; numerical comparison and fresh replay. Test fixture is simulated. Calibration application/authenticity and physical validation remain unestablished. |
| Capability listing and preflight | [capabilities](../src/ciw/capabilities.py), [doctor](../src/ciw/doctor.py); [capability tests](../tests/test_capabilities.py), [doctor tests](../tests/test_doctor.py) | Implemented data-only listing and local preflight. No universal profile discovery or composition registry; `authorizes_execution: false`, qualification `not_performed`. |
| Legibility compilation, signatures and installed operator path | [guide](LEGIBILITY.md), [operator guide](RUN_NET.md), [compiler](../src/ciw/legibility.py), [installed check](../scripts/check_operator_installed.py); [preflight tests](../tests/test_doctor_legibility.py) | Synchronized human/reasoning/vision records, explicit signing/trust and read-only impact import. The Legibility doctor checks installed dependency metadata without executing cryptography. Numerical verification, integrity, issuer trust, currentness and physical validation remain distinct; no canonical admission or actuation. |
| Retained educational/reference calculations | [learning](../src/ciw/learning.py), [linear response](../src/ciw/linear_response.py), [exact response](../src/ciw/exact_response.py); [learning tests](../tests/test_learning.py), [exact-response tests](../tests/test_exact_response.py) | Implemented narrow teaching/checking surfaces. No universal curriculum, theorem prover or equipment authority. |

## Local agent and client integration

The [MCP adapter](../src/ciw/agent_mcp.py) is implemented over the existing
[agent API](../src/ciw/agent_api.py). It is a bounded, sequential JSON-RPC 2.0
stdio server with one UTF-8 JSON message per line, a 1 MiB input-line limit and
a 4,096 request-ID budget per connection. It negotiates MCP `2025-11-25` or
`2025-06-18` and implements `initialize`, `notifications/initialized`, `ping`,
`tools/list` and `tools/call`. It exposes operator-bound inputs and allowed
operations; it adds no scientific solver or mandatory MCP package dependency.
Remote HTTP, batch requests, resources, prompts, sampling, tasks, subscriptions
and general shell execution are outside this profile. See the
[agent protocol](NET_AGENT_PROTOCOL.md) for setup, retries and retained records.

[Agent tests](../tests/test_agent_protocol.py) cover discovery, actual built-in
dispatch, malformed requests, bounded stdio frames, refusal, retry/replay and
retention, including a real CLI subprocess. The
[dedicated workflow](../.github/workflows/net-agent-mcp.yml) configures source
and installed-wheel tests on Ubuntu and Windows plus an
[official Python MCP SDK client campaign](../scripts/check_agent_mcp.py), with
`mcp==1.26.0` as a test-only dependency. That campaign exercises the built-in
profile; its reference-self comparison checks transport and records, not an
independent numerical result. These checks do not certify all MCP clients or
protocol features, an actual model session, native providers, remote hosting,
or physical equipment. Workflow presence alone is not a passing execution
receipt for this checkout.

The terminal, WebSocket and optional Godot views are also client surfaces.
Their existence does not expand the numerical or physical scope of the
operations they display.

## Scientific provider operations

These operations use the shared lifecycle. Provider pins and numerical policy
remain owned by each operating guide and packaged runtime manifest. Synthetic
transport doubles establish contract/retention behavior; required native gates
need actual providers and cannot close with skips.

| Registered surface | Guide and source/tests | Scientific scope and limits |
| --- | --- | --- |
| `ciw.calibrated-observable.v1` | [guide](CALIBRATED_OBSERVABLE.md), [source](../src/ciw/calibrated_observable.py), [tests](../tests/test_calibrated_observable.py) | Bound calibrated-observation and estimation chain; no general physical acquisition or automatic correction loop. |
| `ciw.identified-design.v1` | [guide](IDENTIFIED_DESIGN.md), [source](../src/ciw/identified_design.py), [tests](../tests/test_identified_design.py) | Bound model, prediction and constrained next-observation design. Uncertainty limits remain explicit; selecting a measurement does not perform or authorize it. |
| `ciw.telemetry.v1` | [guide](TELEMETRY.md), [source](../src/ciw/telemetry.py), [tests](../tests/test_telemetry.py) | Bound telemetry processing and retained records; no deadline guarantee from the host. |
| `ciw.calibrated-window.v1` | [guide](CALIBRATED_WINDOW.md), [source](../src/ciw/calibrated_window.py), [tests](../tests/test_calibrated_window.py) | Bound finite calibrated-window workflow; no continuous hardware polling. |
| `ciw.schematic-assessment.v1`, `ciw.numerical-heat.v1` | [guide](DECLARED_WORKLOADS.md), [source](../src/ciw/declared_workload.py), [tests](../tests/test_declared_workloads.py) | Bound schematic assessment and numerical heat computation. Numerical completion is distinct from proof. |
| `ciw.proved-heat.v1` | [guide](PROVED_HEAT.md), [source](../src/ciw/proved_heat.py), [tests](../tests/test_proved_heat.py), [required gate](../scripts/check_proved_heat.py) | Registered integer Jacobi guest and fresh SP1 verifier path. Missing toolchain/ELF/prover refuses. Saved proof outcome is historical, not freshly verified on inspect. Does not verify arbitrary Julia or physical heat. |
| `ciw.schematic-companions.v1` | [guide](INTEGRATED_MODULES.md), [source](../src/ciw/schematic_companions.py), [tests](../tests/test_schematic_companions.py) | Bound selected schematic/JSPT/PLSR companions with retained upstream bindings; continuous linear surrogate scope only. |
| `ciw.bim-quantity.v1` | [guide](INTEGRATED_MODULES.md), [source](../src/ciw/bim_quantity.py), [tests](../tests/test_bim_quantity.py) | Bound IFC quantity conditioning and ledger replay; no surveyed geometry/clearance qualification. |
| `ciw.acquired-dataset.v1`, `ciw.acquired-calibrated-window.v1` | [dataset guide](ACQUIRED_DATASET.md), [stream guide](ACQUIRED_STREAM.md), [dataset source](../src/ciw/acquired_dataset.py), [window source](../src/ciw/acquired_window.py); [dataset tests](../tests/test_acquired_dataset.py), [window tests](../tests/test_acquired_window.py) | Bound finite offline snapshot acquisition/import and calibrated processing. Raw lineage is retained; no general live sensor gateway or fresh physical sample from replay. |
| `ciw.residual-monitor.v1` | [source](../src/ciw/residual_monitor.py), [tests](../tests/test_residual_monitor.py) | Bound CUSUM residual monitor. An alarm is an estimated anomaly, not evidence of a uniquely identified cause. |
| `ciw.measurement-chain.v1` | [guide](../examples/measurement-chain/README.md), [source](../src/ciw/measurement_chain.py), [tests](../tests/test_measurement_chain.py) | Bound RCI → FSRT → JSPT chain with joint covariance; no unsupported independence or automatic physical admission. |
| `ciw.geometric-circle.v1` | [guide](GTE.md), [source](../src/ciw/geometric_circle.py), [tests](../tests/test_geometric_circle.py) | Bound circle eligibility, local geometry and covariance. No general multi-frame geometric-algebra dynamics. |
| `ciw.identified-stability.v1` | [guide](PLSR.md), [source](../src/ciw/identified_stability.py), [tests](../tests/test_identified_stability.py) | Bound discrete identified-model Lyapunov assessment; no continuous-time conversion or universal nonlinear/hybrid safety case. |
| `ciw.flat-torus-reference.v1`, `ciw.curved-path-transfer.v1` | [guide](GEODESIC_REFERENCES.md), [source](../src/ciw/geodesic_reference.py), [tests](../tests/test_geodesic_reference.py) | Bound geodesic reference profiles; no physical navigation qualification. |
| `ciw.covariance-geometry.v1`, `ciw.mesh-path.v1`, `ciw.translation-flow.v1` | [guide](GEOMETRY_RESEARCH.md), [source](../src/ciw/geometry_research.py), [tests](../tests/test_geometry_research.py) | Bound geometry research profiles; representation and numerical scope remain profile-specific. |
| `ciw.variational-free-energy.v1` | [guide](VARIATIONAL_FREE_ENERGY.md), [source](../src/ciw/free_energy_workflow.py), [tests](../tests/test_free_energy_workflow.py) | Bound linear Gaussian/free-energy example, not a general discovery system. |
| `ciw.energy-accuracy.v1` | [guide](ENERGY_ACCURACY.md), [source](../src/ciw/energy_workflow.py), [tests](../tests/test_energy_workflow.py) | Bounded CUDA/NVML bench when host capability is available. Synthetic/retained paths do not establish measured whole-machine energy or calibrated laboratory uncertainty. |
| `ciw.instrument-exchange.v1` | [guide](EXCHANGE.md), [source](../src/ciw/exchange_adapter.py), [tests](../tests/test_exchange_adapter.py) | Bound typed exchange adaptation. Profile semantics/provenance do not supply a universal model compiler. |
| `ciw.julia-oscillator.v1` | [guide](JULIA_OSCILLATOR.md), [source](../src/ciw/julia_oscillator.py), [tests](../tests/test_julia_oscillator.py) | Bound Tsit5 worker with independent Python analytic oracle and fresh replay; no physical validation, sensor-model identification or unrestricted Julia execution. |
| `ciw.native-interop.v1` | [guide](NATIVE_INTEROP.md), [source](../src/ciw/native_interop.py), [contracts](../src/ciw/native_interop_contract.py), [tests](../tests/test_native_interop.py) | Bound SCR profiles for affine arithmetic, oscillator force/energy, Tsit5, ControlSystemsBase, JuMP/QP, Catalyst/Cantera and a scalar interval requirement. Exact runtime closure and independent checks apply. No general ModelingToolkit plant compiler or source-to-binary attestation. |
| `esm.inspect-candidate.v1`, `esm.capture-candidate.v1` | [guide](STATE_DIAGNOSTICS_EVIDENCE.md), [source](../src/ciw/candidate_evidence.py), [tests](../tests/test_workbench_candidates.py) | Explicitly bound ESM candidate inspection/capture. Capture remains unadmitted; no canonical-state mutation, release activation or blanket verification authority. |

The standalone [JuMP adjustment study](DESIGN_ADJUSTMENT.md) is a real worker
with an exact Python checker but no registered operation/execution/result
identity of its own. It differs from the registered `design-qp.v1` profile
inside `ciw.native-interop.v1`.

## Platform claims that remain partial or planned

| Claim | Current evidence | Remaining demonstration |
| --- | --- | --- |
| Incident replay / counterfactual investigation | Retained sources/replay, fresh identities, project status and a local source/bundle/execution/result/claim dependency projection exist. | Concrete incident data and complete workload-specific dependency coverage. Projection uses retained explicit references; it does not infer all scientific dependencies. |
| Adaptive self-calibration | Calibrated windows, residual monitoring and estimation exist. The correction-loop validation record reports a synthetic encoder-offset proposal/review/corrected execution with transitive staleness and offline reopen. | Continuous correction policy, actual instrument/reference evidence and held-out physical improvement. Local review acceptance is not physical calibration or ESM admission. |
| Executable claim graph / safety case | Scoped provider checks, proof profiles and project declarations exist. Predicted/estimated claims depend on retained artifact identities and can become stale. | Checker/premise/operational-policy graph, workload-specific safety proof and hybrid-system proof where needed. These claims remain `not_verified`; no regulator acceptance is claimed. |
| Language-neutral engineering model | Bounded native contracts and genuine historical cross-language cases exist. | Shared thermal plant/sensor model, observer comparison, covariance transform and constrained measurement choice across Python/Julia. |
| AI researcher through MCP | Implemented bounded local stdio adapter, operator-bound tools, deterministic refusals, retained candidates and source/SDK test gates; see the client integration section above. | Broader client/model interoperability and native-provider qualification require separate execution evidence. The adapter grants no equipment permission. |
| Device authorization and operation | Scientific paths refuse equipment authority; [gateway contract](DEVICE_GATEWAY.md) and [acceptance plan](DEVICE_GATEWAY_ACCEPTANCE.md) are specified. | Trusted issuer/policy, durable dispatch/recovery, simulator qualification and separately commissioned hardware. Acceptance cases are not results. |
| Broad mathematical platform | Local covariance, linear stability, geometry, free-energy, bounded QP and scalar interval profiles exist. | General nonlinear UQ, multi-frame GA, streaming TDA, hybrid CPS proofs and general symbolic/numeric compilation remain provider research. |

## Interpreting validation

Historical genuine execution is recorded in
[native validation](NATIVE_INTEROP_VALIDATION.md),
[reaction validation](REACTION_BENCHMARK_VALIDATION.md) and
[interval requirement](INTERVAL_REQUIREMENT.md). Those records name platforms,
pins, completed cases and open gates. They do not qualify this checkout anew or
the reader's environment.

Source inspection, passing transport doubles, same-runtime replay, independent
numerical agreement, cryptographic proof and physical reference comparisons
support different claims. Successful computation never supplies equipment
authorization. [Execution responsibilities](EXECUTION_RESPONSIBILITIES.md) and
[protocol](PROTOCOL.md) retain these boundaries.

The [correction-loop validation record](CORRECTION_LOOP_VALIDATION.md) reports
execution of the [correction gate](../scripts/check_correction_loop.py) using
the provider-free `ciw.encoder-position.v1` operation. One withheld synthetic
point gives original position `1.001 m` against reference `0.998 m`: residual `0.003 m` exceeds the declared `0.0005 m`
diagnostic bound. A corrected offset produces `0.998 m` with residual zero.
The gate checks inert proposals, transitive staleness after local acceptance,
fresh execution/result identities, unchanged original artifacts, late historical
reexecution remaining stale and exact offline reopen. This demonstrates one
workflow with synthetic fixture evidence, not a calibrated physical instrument,
an independent physical validation or general self-healing behavior.

Journal actions checkpoint their events and complete retained dependencies
together before publication. [Validation results](CORRECTION_LOOP_VALIDATION.md)
record that correction increment's negative tests, source/installed-wheel gates
and optional provider skips separately.
