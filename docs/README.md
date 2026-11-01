# Notations Systems Terminal documentation

The root [README](../README.md) is the macro entrypoint. This index points to
the page that owns each kind of detail so status and contracts do not drift
across several copies.

The technical name is **Computational Instrumentation Workbench (CIW)**. The
Python distribution is `computational-instrumentation-workbench`; Python code
imports `ciw`. The command-line entry points are `net` and `ciw`.

## Start here

| Need | Page |
| --- | --- |
| Model entry: folders, call order, mandates, and what is not a grant | [MCP project map](MCP_PROJECT.md) |
| Tool arguments, profile grants, and refusals a model must repeat | [MCP contract](MCP_MODEL.md) |
| Every tracked folder, loose/latent candidates and explicit integration gates | [Complete repository folder audit, 2026-10-18](REPOSITORY_FOLDER_AUDIT_20261018.md) |
| DC electronics repair, refurbishment and retrofit cases | [Electronics repair](ELECTRONICS_REPAIR.md) |
| Install and check the public Terminal/Legibility workflows together | [Run NET](RUN_NET.md) |
| Product scope, operating model and scientific workspace | [Workbench overview](WORKBENCH_OVERVIEW.md) |
| Current provider map and loose-tool collapse rule | [Systems catalog](SYSTEMS_CATALOG.md) |
| Shared profiles, typed composition and evidence boundaries | [Consolidation roadmap](CONSOLIDATION.md) |
| Executable implementation architecture | [Architecture](ARCHITECTURE.md) |
| Python, Julia, native execution and proof responsibilities | [Execution responsibilities](EXECUTION_RESPONSIBILITIES.md) |
| Bounded Rust/C++, JuliaControl and JuMP execution | [Native interoperability](NATIVE_INTEROP.md) |
| Fixed-model chemical kinetics and cross-engine references | [Reaction benchmark](REACTION_BENCHMARK.md) |
| Dry hydrostatic atmospheric state, independent quadrature and typed provider handoffs | [Atmospheric engine](ATMOSPHERIC_ENGINE.md) (research branch increment) |
| Unsaturated moist-air state, continuous humidity guard and frozen-composition handoffs | [Moist-air extension](ATMOSPHERIC_MOIST_AIR.md) (research branch increment) |
| Declared reference comparisons, independent arithmetic and four-point NIST agreement | [Atmospheric reference validation](ATMOSPHERIC_REFERENCE_VALIDATION.md) (research branch increment) |
| Bounded SI measurement CSV, retained input bindings and fresh mapping checks | [Atmospheric measurement preparation](ATMOSPHERIC_MEASUREMENT_INGRESS.md) (research branch increment) |
| Elastic contact, independent impulse/energy checks and typed preservation | [Impact contact benchmark](IMPACT_CONTACT_BENCHMARK.md) (research branch increment) |
| Irreversible crush state, plastic work and independent energy accounting | [Impact crush benchmark](IMPACT_CRUSH_BENCHMARK.md) (research branch increment) |
| Finite parameter cases and separate numerical/design qualification | [Impact scenario envelope](IMPACT_SCENARIO_ENVELOPE.md) (research branch increment) |
| Simply supported elastic plate, finite contact patch and temporal/modal refinement | [Impact plate benchmark](IMPACT_PLATE_BENCHMARK.md) (research branch increment) |
| Bounded scalar linearization error with exact reference | [Interval requirement check](INTERVAL_REQUIREMENT.md) |
| Optional providers and the next acceptance experiments | [Provider development sequence](PROVIDER_DEVELOPMENT.md) |
| Multi-provider assembly and local deployment | [Workbench assembly](WORKBENCH_ASSEMBLY.md) |
| Current executable paths and remaining gates | [Integration coverage](INTEGRATION_COVERAGE.md) |
| Uncertainty-aware reference comparison, local timing and remaining qualification work | [Qualification progress](QUALIFICATION_PROGRESS.md) |
| Sourced polymer-temperature design choices, assumptions and evidence gaps | [Temperature pilot candidate](TEMPERATURE_PILOT_CANDIDATE.md) |
| Capability claims checked against merged source | [Concerns audit, 2026-10-03](CONCERNS_AUDIT_2026-10-03.md) |
| Source correction, dependent-claim staleness and retained history | [Retained correction loop](CORRECTION_LOOP.md) |
| User-facing instruments and exact commands | [Instrument catalogue](INSTRUMENTS.md) |
| Synchronized specimen views, signatures and separate trust/qualification checks | [Legibility Instrument](LEGIBILITY.md) |
| Typed configurations, coupled polymer models and retained numerical checks | [Scientific system composition](SYSTEM_COMPOSITION.md) |
| Continuous composition work, qualification and next priorities | [Composition development checkpoint](SYSTEM_DEVELOPMENT.md) |
| Oscillator demo, inspection and reopen commands | [Oscillator operator card](OSCILLATOR_OPERATOR.md) |
| One worked RMS calculation, retained selection and explicit replay | [Retained RMS lesson](LEARNING.md) |
| Local dependencies, exact expected identities and unperformed checks | [Read-only profile diagnostics](DOCTOR.md) |
| Provider-free index of existing surfaces; not discovery or qualification | [Capability index](CAPABILITIES.md) |
| Offline tooling map and declared capability inspector | [Tooling visualizer](TOOLING_VISUALIZER.md) |
| Retained native heading candidates and matrix contributions | [Curved-path study](CURVED_PATH_STUDY.md) |
| Equations, assumptions and bounded what-if previews | [Mathematical model exploration](MODEL_EXPLORATION.md) |
| Named but unimplemented research directions | [Unimplemented directions](UNIMPLEMENTED_DIRECTIONS.md) |

## Contracts and operations

- [Finite representation preservation and Board morphism binding](FINITE_REPRESENTATION_PRESERVATION.md) (research branch increment)
- [Evidence-bound representation expansion](EVIDENCE_BOUND_EXPANSION.md) (research branch increment)
- [Representation realization adapters](REPRESENTATION_REALIZATIONS.md) (research branch increment)
- [Spatial and mesh realization adapters](SPATIAL_MESH_REALIZATIONS.md) (research branch increment)

- [Protocol and record identities](PROTOCOL.md)
- [Contract foundations and typed exchange](CONTRACT_FOUNDATIONS.md)
- [State-space transformation contract](STATE_TRANSFORMATIONS.md)
- [Workbench research context](RESEARCH_CONTEXT.md)
- [Unimplemented directions](UNIMPLEMENTED_DIRECTIONS.md)
- [Generic adapters](ADAPTERS.md)
- [Device and Instrument Gateway proposal](DEVICE_GATEWAY.md) and [acceptance plan](DEVICE_GATEWAY_ACCEPTANCE.md)
- [Covariance provenance and replay](COVARIANCE.md)
- [Retained telemetry](TELEMETRY.md) and [shared telemetry](SHARED_TELEMETRY.md)
- [Calibrated observable process](CALIBRATED_OBSERVABLE.md)
- [Identified and budgeted observation](IDENTIFIED_DESIGN.md)
- [Machine manifest workflow](CONTRACT_FOUNDATIONS.md#machine-manifest-operation)
- [Energy-to-accuracy bench](ENERGY_ACCURACY.md)
- [Variational free-energy sensor fusion](VARIATIONAL_FREE_ENERGY.md)
- [Geodesic references](GEODESIC_REFERENCES.md) and [geometry research](GEOMETRY_RESEARCH.md)
- [PLSR](PLSR.md), [registered heat proof](PROVED_HEAT.md), [Julia oscillator](JULIA_OSCILLATOR.md), and [Julia/SP1 direction](JULIA_SP1.md)
- [Exchange inspection](EXCHANGE.md)

## Bounded NET workflows

These guides describe explicit operations and retained records. Optional native
providers require their own pinned bindings and qualification; importing a guide
or inspecting a record does not establish provider availability.

| Need | Page |
| --- | --- |
| NET command plane and typed composition | [Control plane](NET_CONTROL_PLANE.md) · [Workflow algebra](WORKFLOW_ALGEBRA.md) |
| Semantic capabilities, container composition and selective recomputation | [Semantic capabilities](SEMANTIC_CAPABILITIES.md) · [Containers](CONTAINER_CALCULUS.md) · [Needle](NEEDLE.md) |
| Parameterized Boards, offline editing and live evidence views | [System Board](SYSTEM_BOARD.md) · [Offline editor](SYSTEM_BOARD_VISUAL.md) · [Evidence viewer](VISUAL_SYSTEM_BOARD.md) |
| Published physical measurements, uncertainty plots and source inspection | [Measurement evidence visualizer](MEASUREMENT_VISUALIZER.md) |
| Preservation contracts, finite morphisms and evidence-bound expansion | [Preservation](PRESERVATION_CONTRACTS.md) · [Finite morphisms](FINITE_REPRESENTATION_PRESERVATION.md) · [Expansion](EVIDENCE_BOUND_EXPANSION.md) |
| Visual gates and identity-preserving evidence projections | [Representation gates](VISUAL_REPRESENTATION_GATES.md) · [Evidence projection](VISUAL_EVIDENCE_PROJECTION.md) |
| Dry hydrostatic atmospheric state, independent quadrature and typed provider handoffs | [Atmospheric engine](ATMOSPHERIC_ENGINE.md) |
| Convection, heat-exchanger design, pipe-flow references, radiation and two-phase balances | [Thermofluids tools](THERMOFLUIDS.md) |
| Retained molding-cycle metrology, reference estimates, numerical audits and explicit agent profiles | [Polymer processing](POLYMER_PROCESSING.md) · [Agent protocol](NET_AGENT_PROTOCOL.md#polymer-cycle-profile) |
| Bounded impact models, independent verification and finite scenarios | [Elastic contact](IMPACT_CONTACT_BENCHMARK.md) · [Crush](IMPACT_CRUSH_BENCHMARK.md) · [Plate](IMPACT_PLATE_BENCHMARK.md) · [Scenario envelope](IMPACT_SCENARIO_ENVELOPE.md) |
| Cross-system transitions, interoperability and artifact provenance | [Transitions](INDUSTRIAL_SEMANTIC_TRANSITIONS.md) · [Interoperability](EXECUTABLE_INTEROPERABILITY.md) · [Provenance](ARTIFACT_PROVENANCE.md) |
| Explicit workcells and agent transport boundaries | [Workcells](EXECUTABLE_WORKCELLS.md) · [Agent protocol](NET_AGENT_PROTOCOL.md) |
| Separate native simulation owners and retained campaigns | [Installed adapters](INTERACTIVE_INSTALLED.md) · [Motion-study Godot owner](GODOT_STATEFUL_OWNER.md) · [Point-provider campaigns](GODOT_STATEFUL_CAMPAIGNS.md) |
| Julia-authored kernels and native consumers | [Kernel export](OSCILLATOR_KERNEL_EXPORT.md) · [Consumers](OSCILLATOR_NATIVE_CONSUMERS.md) |
| Computational objects and bounded annotation histories | [Objects](COMPUTATIONAL_OBJECTS.md) · [Annotations](ANNOTATIONS.md) · [Perspectives](HISTORICAL_PERSPECTIVE.md) |
| Original research context and mathematical preservation limits | [Research foundations](RESEARCH_FOUNDATIONS.md) · [Representation problem](REPRESENTATION_PROBLEM.md) |

## Development and availability

- [Qualified research release: candidate, PR scope and evidence gate](QUALIFIED_RELEASE.md)
- [Engineering superrepo: module map, exact source histories and qualification commands](MONOREPO.md)
- [Superrepo debugging audit, 2026-10-04](SUPERREPO_AUDIT_2026-10-04.md)
- [Combined integration candidate and acceptance gates](INTEGRATION_CANDIDATE.md)
- [Development guide](DEVELOPMENT.md)
- [Development-window gap audit](DEVELOPMENT_GAPS.md)
- [Provider availability and exact checkout provisioning](PROVIDER_AVAILABILITY.md)
- [Stack map](STACK.md) and [stack role](STACK_ROLE.md)
- [Diagram atlas](DIAGRAMS.md)
- [Quickstart](quickstart.md)
- [Deployment](../deploy/README.md)

Historical audits remain linked from the root for context. They do not override
the current operation catalogue, integration matrix or provider manifests.
