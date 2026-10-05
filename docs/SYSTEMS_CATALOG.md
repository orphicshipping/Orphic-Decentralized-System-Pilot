# Systems catalog

This page is the CIW-facing map of the surrounding Notation Systems tools. It
is an integration index, not a replacement for an independent repository's
README, license or scientific contract. Each linked repository remains
responsible for its own implementation and should be read at the exact
revision named by the applicable CIW manifest.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| **Built-in kernel** | A bounded calculation is implemented in this repository and is callable with the base installation; its guide states numerical and retention limits. |
| **Optional pinned adapter** | CIW implements a call to an external provider and retains its result; the operator must provision and bind the specified checkout, runtime and dependencies. |
| **Client integration** | An interface exposes existing operations or views, without supplying their scientific implementations. |
| **Planning guide / contract only** | The boundary or planned profile is documented, but the stated executable path is not delivered. |
| **Pending gate** | A specific implementation, environment or validation requirement remains open; a partial implemented path can still exist. |

Implementation status is separate from validation status. For a bounded
integration, operation, execution and result identities must remain distinct;
units, frames, timing and uncertainty must be declared; provider pins must be
checked; and applicable save, reopen, replay and refusal paths must be tested.
Those tests establish only their declared scope. They do not establish general
scientific validity, physical calibration or successful execution on this host.

## CIW-owned pages and operations

| CIW page | Responsibility |
| --- | --- |
| [INSTRUMENTS.md](INSTRUMENTS.md) | User-facing commands, operation contracts, inputs, outputs and limits. |
| [INTEGRATION_COVERAGE.md](INTEGRATION_COVERAGE.md) | Selected implemented paths, source/test evidence, historical validation and remaining gates. |
| [WORKBENCH_ASSEMBLY.md](WORKBENCH_ASSEMBLY.md) | Multi-provider session assembly and deployment layout. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Admission, persistence, identity separation, replay and read-only boundaries. |
| [CONTRACT_FOUNDATIONS.md](CONTRACT_FOUNDATIONS.md) | Typed exchange and machine-manifest foundations. |
| [PROVIDER_AVAILABILITY.md](PROVIDER_AVAILABILITY.md) | Exact local checkout provisioning and access limitations. |

Built-in kernels include the synthetic oscillator and its statistics and
periodogram, the Python thermal reference, and encoder-position calculation.
CIW also owns the orchestration and records for telemetry, calibrated process,
identified-design, covariance and geometry workflows, whose computations require
optional external providers. Energy log analysis is local; NVML/CUDA capture
requires supported hardware and drivers. Detailed contracts and setup are in the
[instrument catalogue](INSTRUMENTS.md).

The [local MCP stdio adapter](NET_AGENT_PROTOCOL.md), terminal/WebSocket session
and optional Godot views are client integrations. The MCP adapter implements a
bounded JSON-RPC tools profile with source and official-SDK test gates; it does
not imply every MCP client or feature is certified. The
[device gateway](DEVICE_GATEWAY.md), its [acceptance plan](DEVICE_GATEWAY_ACCEPTANCE.md)
and [Julia/SP1 F2 topology](JULIA_SP1.md) are planning guides for their stated
undelivered paths, not additional callable kernels.

## Surrounding systems

The links below go to each independent repository's README. The CIW column
describes the current boundary. All provider computations in this table are
optional and require their documented binding; upstream repositories are not
made available merely by installing CIW.

| System and independent README | CIW role | Status / integration page |
| --- | --- | --- |
| [Provenance-Preserving-Data-Acquisition](https://github.com/atomtrapping/Notations-Data-Intake#readme) | Retain source observations and acquisition lineage. | Pinned telemetry and calibrated paths; [TELEMETRY.md](TELEMETRY.md) |
| [Streaming-Telemetry-Feature-Extraction](https://github.com/atomtrapping/Notations-Signal-Processing-RunTime#readme) | Bounded causal window features. | Pinned telemetry path; [TELEMETRY.md](TELEMETRY.md) |
| [Geometric-State-Inference-Engine](https://github.com/atomtrapping/Notations-State-Inference-Engine#readme) | State prediction/update and covariance diagnostics. | Pinned telemetry and calibrated paths; [INTEGRATION_COVERAGE.md](INTEGRATION_COVERAGE.md) |
| [State-Estimation-Evaluation-Testbed](https://github.com/atomtrapping/Notations-Estimator-Bench#readme) | Scoped exchange conformance and replay checks. | Read-only inspection; [EXCHANGE.md](EXCHANGE.md) |
| [Constraint-Based-State-Reconciliation](https://github.com/atomtrapping/Notations-State-Recompiler#readme) | Held/accepted conservation candidates. | Pinned calibrated path; [CALIBRATED_OBSERVABLE.md](CALIBRATED_OBSERVABLE.md) |
| [Time-Base-Reconciliation-Runtime](https://github.com/atomtrapping/Notations-ClockSync#readme) | Clock mapping and time uncertainty. | Pinned calibrated path; [CALIBRATED_OBSERVABLE.md](CALIBRATED_OBSERVABLE.md) |
| [Metrological-Calibration-Uncertainty-Runtime](https://github.com/atomtrapping/Notations-Calibration-Runtime#readme) | Time-applicable calibration and correlated uncertainty. | Pinned calibrated path; [CALIBRATED_OBSERVABLE.md](CALIBRATED_OBSERVABLE.md) |
| [Observability-Identifiability-Testbed](https://github.com/atomtrapping/Notations-Observability-Testbed#readme) | Observability and local identifiability gates. | Pinned calibrated/design paths; [IDENTIFIED_DESIGN.md](IDENTIFIED_DESIGN.md) |
| [Fault-Detection-Isolation-Runtime](https://github.com/atomtrapping/Notations-FaultSense-RunTime#readme) | Innovation and residual diagnostics. | Pinned calibrated path; [CALIBRATED_OBSERVABLE.md](CALIBRATED_OBSERVABLE.md) |
| [System-Identification-Dynamics-Testbed](https://github.com/atomtrapping/Notations-Linear-Dynamics-Testbed#readme) | Bounded point-model identification. | Pinned identified-design path; [IDENTIFIED_DESIGN.md](IDENTIFIED_DESIGN.md) |
| [Experiment-Design-Sensor-Placement-Testbed](https://github.com/atomtrapping/Notations-SensorDesign-RunTime#readme) | Candidate information and cost ranking. | Pinned advisory path; [IDENTIFIED_DESIGN.md](IDENTIFIED_DESIGN.md) |
| [Yield-Weighted-Inference-Runtime](https://github.com/atomtrapping/Notations-Yield-Weighted-Runtime#readme) | Separate advisory computation-token decision. | Pinned identified-design path; [IDENTIFIED_DESIGN.md](IDENTIFIED_DESIGN.md) |
| [Jacobian-Sensitivity-Propagation-Testbed](https://github.com/atomtrapping/Notations-Sensitivity-Testbed#readme) | Explicit covariance transport through a declared map. | Pinned covariance path; [COVARIANCE.md](COVARIANCE.md) |
| [Parameterized-Lyapunov-Stability-Runtime](https://github.com/atomtrapping/Parameterized-Lyapunov-Stability-Runtime#readme) | Supported certificate evaluation. | Pinned standalone operation; [PLSR.md](PLSR.md) |
| [Scientific-Computation-Runtime](https://github.com/atomtrapping/Notations-Compute-Runtime#readme) | Registered integer execution and optional proof backend. | Optional pinned native profiles and proof operation; [NATIVE_INTEROP.md](NATIVE_INTEROP.md), [PROVED_HEAT.md](PROVED_HEAT.md) |
| [Evidence-and-State-Management](https://github.com/atomtrapping/Notations-State-Ledger#readme) | Candidate evidence review and retention. | Optional pinned candidate inspection/capture; no state admission. [STATE_DIAGNOSTICS_EVIDENCE.md](STATE_DIAGNOSTICS_EVIDENCE.md) |
| [Geospatial-State-Visualization](https://github.com/atomtrapping/Notations-Real-Time-Globe#readme) | Read-only geographic and temporal projection. | Client integration: pinned read-only geographic projection; [INTEGRATED_MODULES.md](INTEGRATED_MODULES.md) |
| [Schematics-Retrieval-Agent](https://github.com/atomtrapping/Notations-Retrieval-Agent#readme) | Evidence-backed asset and signal-binding proposals. | Optional pinned schematic assessment and selected companions; [DECLARED_WORKLOADS.md](DECLARED_WORKLOADS.md), [INTEGRATED_MODULES.md](INTEGRATED_MODULES.md) |
| [Retrofitted-Computational-Instrumentation](https://github.com/atomtrapping/Notations-Metrology-Adapter#readme) | External sensing and retrofit observation modules. | Optional pinned calibration/measurement-chain adapter; [ADAPTERS.md](ADAPTERS.md), [measurement-chain example](../examples/measurement-chain/README.md) |
| [Construction-State-Estimator-for-BIM](https://github.com/atomtrapping/Notations-Estimator-for-BIM#readme) | Built-asset state estimation and model context. | Implemented quantity-only pinned adapter; surveyed-frame geometry remains pending. See [INTEGRATED_MODULES.md](INTEGRATED_MODULES.md). |
| [Covariance-Geometry-and-Geodesic-Testbed](https://github.com/atomtrapping/Covariance-Geometry-and-Geodesic-Testbed#readme) | Affine-invariant covariance geometry. | Optional pinned external provider; [GEOMETRY_RESEARCH.md](GEOMETRY_RESEARCH.md) |
| [Intrinsic-Surface-Geodesics-Testbed](https://github.com/atomtrapping/Intrinsic-Surface-Geodesics-Testbed#readme) | Bounded mesh-edge geodesic baseline. | Optional pinned external provider; [GEOMETRY_RESEARCH.md](GEOMETRY_RESEARCH.md) |
| [Translation-Surface-Dynamics-Explorer](https://github.com/atomtrapping/Polygon-Trajectory-Experiments#readme) | Exact rational square-tiled trajectory prefixes. | Optional pinned external provider; [GEOMETRY_RESEARCH.md](GEOMETRY_RESEARCH.md) |
| [Flat-Torus-Geodesic-Reference](https://github.com/atomtrapping/Notations-Periodic-Space#readme) | Exact area-one lattice/winding reference. | Pinned reference provider; [GEODESIC_REFERENCES.md](GEODESIC_REFERENCES.md) |
| [Curved-Surface-Geodesic-Sensitivity-Runtime](https://github.com/atomtrapping/Notations-Surface-RunTime#readme) | Constant-curvature Jacobi transfer reference. | Pinned reference provider; [GEODESIC_REFERENCES.md](GEODESIC_REFERENCES.md) |
| [Instrument-Conformance-and-Replay-Harness](https://github.com/atomtrapping/Conformance-and-Replay-Retainer#readme) | Versioned external profile conformance. | Pinned validator; [EXCHANGE.md](EXCHANGE.md) |

The [Julia Tsit5 oscillator](JULIA_OSCILLATOR.md) has a registered provider
contract and retained replay path. Bounded ControlSystemsBase and JuMP/HiGHS
profiles are also implemented through [native interoperability](NATIVE_INTEROP.md);
this does not integrate the full JuliaControl or JuMP ecosystem. ModelingToolkit,
RxInfer, OpenFOAM, FreeCAD, TensorFlow Lattice, an FPGA toolchain and the broader
persistent Julia scientific runtime remain candidate providers. Every existing
profile still requires its exact environment and separate validation evidence.

## External tool boundary inventory

ALCHEMI inventory addition: **2026-10-05**. Existing rows retain their selected
source-audit boundaries; adding this table does not rerun or requalify them.
Consult [integration coverage](INTEGRATION_COVERAGE.md) and each operating guide
for source, retained validation records and open gates.

| External tool | Inspected boundary / inventory status |
| --- | --- |
| Godot | Inspection-client source present; general simulation integration remains a separate qualification target. |
| Bevy | Retained-render projector source present; general ECS simulation integration remains separate. |
| Blender | Optional authoring target; qualified authoring-job integration remains pending in the cited integration audit. |
| SP1 | Selected registered computation-proof path, not blanket verification of every instrument. |
| Catalyst / Cantera | Bounded reaction-provider families. |
| ControlSystemsBase | Bounded control-oscillator profile. |
| OrdinaryDiffEqTsit5 | Bounded oscillator integration. |
| JuMP / HiGHS | Bounded quadratic-design optimization. |
| JuliaIntervals family | Bounded interval-enclosure profile. |
| NVIDIA ALCHEMI Toolkit (`nvalchemi-toolkit`) | Added external atomistic-simulation and ML interatomic-potential provider target. Upstream documents batched molecular dynamics, relaxation and model training/fine-tuning. **Planning guide / contract only: NET adapter, environment binding, independent numerical validation and polymer/physical qualification remain pending.** See [NVALCHEMI.md](NVALCHEMI.md). |
| OpenTelemetry / OTLP | Selected standard operational telemetry contract, separate from scientific evidence, computation, verification and control identities. **Planning guide / contract only:** optional instrumentation and failure-isolation gates are not yet implemented by this addition. See [OPERATIONAL_OBSERVABILITY.md](OPERATIONAL_OBSERVABILITY.md). |
| OpenTelemetry Collector | Selected backend-independent receiver/processor/exporter boundary for operational signals. **Planning guide / contract only:** deployment, privacy, queue/loss and backend-substitution qualification remain pending. See [OPERATIONAL_OBSERVABILITY.md](OPERATIONAL_OBSERVABILITY.md). |
| OpenObserve | First self-hostable operational-observability backend target, not the NET State Ledger or a scientific authority. **Planning guide / contract only:** exact-version end-to-end ingestion and deployment qualification remain pending; profiles stay opt-in/development-status. See [OPERATIONAL_OBSERVABILITY.md](OPERATIONAL_OBSERVABILITY.md). |

ALCHEMI extends the molecular/materials layer; it does not replace existing
Julia/native providers, continuum solvers, NET evidence ownership or machine
controller authority. Its Python-facing API remains inside an optional provider
boundary. This catalog entry does not install the toolkit, register an operation
or authorize training, model downloads, remote data transfer or execution.

Operational-observability inventory addition: **2026-10-05**. OTel/OTLP and the
Collector describe the operational interface; OpenObserve is a replaceable
backend target. The existing scientific `ciw.telemetry.v1` workflow and OIT
observability/identifiability are unchanged. This selection does not install an
SDK, enable network export, create an operation or evidence schema, modify
scientific retention, or grant control authority. See the
[operational-observability contract](OPERATIONAL_OBSERVABILITY.md) for correlation,
offline/failure isolation, security, signal maturity and acceptance gates.

## The collapse rule for loose tooling

The project is collapsing *interfaces and evidence paths*, not erasing
independent source repositories:

1. Keep upstream source, README, license and scientific tests in the provider
   repository.
2. Add one CIW adapter or exchange producer with a versioned operation contract.
3. Pin the provider commit, source tree, interpreter and relevant dependencies
   in a CIW manifest.
4. Retain typed inputs, units, frames, clock declarations, uncertainty status,
   provenance and refusal behavior in the CIW execution/result records.
5. Add save, reopen, replay and independent validation before labeling the
   provider integrated.

Provider code is not copied into CIW merely to make a table entry look
complete. A README link is discoverability; a pinned adapter plus exercised
records is integration.

## Pin and validation ledger

The authoritative pins are the JSON manifests under src/ciw/ and the
provider-specific operating guides under docs/. The current native reference
pins include:

| Provider | Revision |
| --- | --- |
| Flat Torus Geodesic Reference | dc918562cd9e351a65475d29f46963c9f2fd7db8 |
| Curved Surface Geodesic Sensitivity Runtime | bbc535af29c30997e56fd120320c570830676462 |
| Instrument Conformance and Replay Harness | dc4d826ecd1f28c1d55b724618380ce44e58bedd |

Julia worker source, environment manifests and bounded qualification records
exist; [native validation](NATIVE_INTEROP_VALIDATION.md) identifies historical
platforms, executed cases and remaining gates. Such records do not qualify the
reader's checkout anew. Physical calibration, FPGA programming, client-specific
MCP interoperability and profile-specific independent ICRH validation require
separate evidence; no client or provider status grants equipment authority.
