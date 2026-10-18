# Notations Control Protocol

Scientific computing, measurement context, and reproducible engineering investigations.

Developed by **Notation Systems Inc.**

[System design](#system-design) · [Local evaluation](#local-evaluation) · [Technical assessment](#technical-assessment) · [Integration](#integration) · [Documentation](#documentation-and-development)

<a id="from-a-calculation-to-an-engineering-record"></a>

## Purpose and scope

**Notations Control Protocol (NCP)** is a software framework and engineering workbench for composing supported computational operations, retaining their inputs and results, and examining the evidence behind an engineering conclusion. It continues the project previously named **Notations Engineering Terminal (NET)**.

The unit of work is a retained investigation: a question, source records, declared context, selected methods, execution occurrences, results, and separately scoped checks. Specialist instruments retain responsibility for their numerical methods and applicability. NCP provides the common execution and review environment; it is not a universal physical model, a certification service, or an unrestricted equipment controller.

The application focus includes polymer processing, component development, instrumentation, and discrete manufacturing. The same substrate supports bounded thermal, mechanical, geometric, atmospheric, and interactive-system investigations. A component specification, simulated response, indicated measurement, and approved manufacturing decision remain different objects.

## Implementation status and naming

This branch consolidates the open development work into one review candidate. The
installed aliases are `ncp`, `net`, and `ciw`; package and retained record identities
are unchanged. Review the [selected baseline ledger](docs/RESEARCH_BASELINE_20261017.md)
for the exact source PRs, integration decisions, executed checks, and blocked
qualification. This candidate is not a release or a claim of universal provider
qualification.

The distribution remains `computational-instrumentation-workbench`; the Python package remains `ciw`. Existing guides and commands may therefore use NET terminology. A project-name change does not migrate stored records, change a wire protocol, or grant additional authority.

Read the [integration inventory](docs/INTEGRATION_COVERAGE.md) by its stated revision and evidence category. Source implementation, a configured test workflow, an executed test, a qualified installation, and a released product are not interchangeable status claims.

<a id="how-the-system-fits-together"></a>
<a id="why-use-net"></a>

## System design

```text
Source records + engineering context
                 |
                 v
Typed contracts + explicit runtime bindings
                 |
                 v
Session / registered operations
        |                    |
        v                    v
Built-in instruments   Optional specialist providers
        |                    |
        +---------+----------+
                  v
Retained inputs / results / refusals / dependencies
                  |
                  v
Inspection / comparison / scoped checks / reports
                  |
                  +--> Explicit replay: a new execution occurrence
```

| Responsibility | Design boundary |
| --- | --- |
| **Evidence and context** | Retain source material and declared quantity, unit, frame, clock, calibration, uncertainty, and procedure information where supported by the selected contract. Preserve missing information rather than filling it with assumed precision. |
| **Composition and execution** | Check declared interfaces and dependencies; dispatch registered operations through operator-supplied runtime bindings. Saved workspace content does not supply execution authority. |
| **Scientific instruments** | Own algorithms, parameters, validity domains, uncertainty treatment, and reference comparisons. An interface match does not establish physical compatibility or model validity. |
| **Retention and review** | Preserve original inputs, outcomes, failed candidates, and lineage. Reopening reads retained history; explicit replay creates new execution and result occurrences. |
| **Clients and views** | CLI, Python, local service, and supported MCP profiles expose existing operations or retained state. A report, visualization, or agent explanation is a view of the record, not its replacement. |

Evidence, operation, execution, result, and verification identities remain distinct. Numerical verification, source integrity, empirical validation, and permission to act have separate meanings. A content hash can identify retained bytes; it does not establish the physical origin or accuracy of a measurement.

<a id="mathematical-foundations-and-optional-proofs"></a>

The mathematical structure includes typed serial/parallel composition, covariance transport in supported profiles, declared invariant checks, and exact finite representation-preservation tests. These mechanisms have bounded domains; they do not generate constitutive laws or establish predictive accuracy by themselves. See [research foundations](docs/RESEARCH_FOUNDATIONS.md) and [model validity and evidence](docs/MODEL_VALIDITY_AND_EVIDENCE.md).

<a id="other-supported-starting-points"></a>

## Scientific and industrial workflows

| Work area | Implemented scope and entry point |
| --- | --- |
| Recording analysis | Statistics, spectra, explicit selections, and retained results: [recording workflow](docs/quickstart.md#analyze-a-retained-recording). |
| Supplied-model numerical library | Seven built-ins for covariance propagation/budgets, affine calibration/residuals, local sensitivity, observability and model discrepancy through existing CLI/MCP plans: [contracts and example](docs/NUMERICAL_BUILTINS.md). |
| Measurement processing | Declared calibration and full joint covariance in selected profiles: [covariance](docs/COVARIANCE.md) and [temperature-processing profile](docs/NET_AGENT_PROTOCOL.md#temperature-measurement-processing-profile). |
| Thermal and mechanical studies | Bounded state estimation, held-out reference comparisons, and selected mechanical, fluid, thermal, and atmospheric examples: [recorded thermal workflow](docs/MEASURED_THERMAL.md) and [domain examples](docs/DOMAIN_EXAMPLES.md). |
| Polymer processing | Injection-molding and extrusion-blow-molding profiles with retained features, part tolerances, bounded reference calculations, and separate arithmetic review: [polymer guide](docs/POLYMER_PROCESSING.md). Microfabrication-specific models and installed-machine interfaces require separate implementation and qualification. |
| Workflow composition | Typed dependencies and bounded acceptance/repair stages: [workflow algebra](docs/WORKFLOW_ALGEBRA.md) and [work orders](docs/NET_PRODUCTION.md). |
| Representation and geometry | Exact finite probability/intervention checks, retained counterexamples, and selected geometry/BIM workflows: [finite preservation](docs/FINITE_REPRESENTATION_PRESERVATION.md) and [instrument catalogue](docs/INSTRUMENTS.md). |
| Equipment and interactive systems | Refurbishment records, explicit release review, and selected engine-specific views: [refurbishment operations](docs/REFURBISHMENT_OPERATIONS.md) and [interactive simulation](docs/INTERACTIVE_SIMULATION.md). |

Optional providers require the exact runtimes and dependencies specified by their guides. The core installation does not qualify every provider or supply every device interface. For example, the recorded-thermal estimator retains input uncertainty but does not propagate it through that kernel; uncertainty handling must be checked at the operation level.

An optional [registered heat profile](docs/PROVED_HEAT.md) connects to the Scientific Computation Runtime and SP1 for a bounded integer calculation. Its proof concerns the registered computation and commitments, not physical calibration or the validity of an arbitrary heat model. Reading a saved proof report is not a fresh verifier execution. The retained profile makes no privacy claim.

## Local evaluation

[Start with one installed investigation](docs/BASELINE_START_HERE.md) · [Related repositories and provider boundaries](docs/LATENT_PROVIDER_SURFACE_20261018.md) · [Risks and owners](docs/BASELINE_RISKS_20261018.md)

<a id="quickstart"></a>

### Install and exercise the core

Use Python 3.11 or newer from the repository root. On Linux or macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .

net doctor --profile core
net catalog
net start --output-dir results/first-use-001
```

Windows instructions are in the [platform quickstart](docs/quickstart.md#install).

`doctor` checks installed package/dependency metadata. `catalog` lists command surfaces and provider requirements without executing them. `start` executes bounded **synthetic** oscillator, thermal-observer, and atmospheric workflows, including retained inspection and a thermal replay.

Inspect `results/first-use-001/readiness.json` for the actual checks and output locations. Use a new output directory for each run. A successful first-use receipt covers that installation and those checks; it is not external-provider or physical qualification. Retain installation failures and dependency mismatches rather than relaxing pins and reporting the result as an unchanged qualified environment.

<a id="keep-a-local-investigation-open"></a>

### Persistent workbench

```sh
net workbench --output-dir results/workbench
```

In a second terminal with the same environment activated:

```sh
ciw health
ciw send session.get
ciw send analysis.stats
ciw send result.list
ciw send workspace.save
```

`analysis.stats` creates a new result. Save before stopping the service with Ctrl+C. Restarting reopens saved state without rerunning its numerical operations. The default service binds to loopback; this example does not establish a remote or multi-user deployment.

<a id="explore-a-manufacturing-workflow"></a>

### Polymer investigation

```sh
net polymer demo --process injection_molding --output-dir results/polymer-demo-001
net polymer summary results/polymer-demo-001
net polymer verify results/polymer-demo-001 --output results/polymer-audit-001.json
net polymer replay results/polymer-demo-001 --output-dir results/polymer-replay-001 --summary
```

Review `results/polymer-demo-001/report/report.html`. This synthetic example deliberately includes a nonconforming dimension. A completed workflow and passing arithmetic audit can therefore coexist with a failed nominal tolerance comparison. Neither the example nor its replay constitutes a physical experiment, process qualification, or part-release authorization.

<a id="status-and-operating-boundaries"></a>

## Technical assessment

This repository provides implementation and investigation records for technical review. It does not assert product certification, accredited calibration, standards conformity, or fitness for an unspecified industrial application.

For an institutional evaluation or proposed industrial integration, define the assessment around an exact configuration and engineering question:

| Assessment object | Information to establish |
| --- | --- |
| **Intended use** | User, environment, measurand or engineering objective, decision to be supported, and excluded uses. |
| **Configuration** | Repository revision, installed artifact, dependency closure, selected providers, inputs, and any connected hardware/firmware/interface revisions. |
| **Measurement chain** | Instrument and reference identities, calibration evidence, timing/frame semantics, procedure, operating conditions, and an appropriate uncertainty budget. |
| **Computational method** | Model version, assumptions, applicability, tolerances, independent reference checks, and handling of missing or out-of-domain inputs. |
| **Acceptance and authority** | Predeclared decision rules, responsible reviewer, required evidence, and separate authorization for execution or release. |
| **Reproducibility and custody** | Original records, retention/replay behavior, access controls, permitted disclosure, and the evidence needed to detect or investigate substitutions. |

A numerical check answers its specified computational question. Metrological traceability and physical validity require evidence about the measurement chain. Product safety and EMC assessment require their own supplied-product definition and test evidence. Internal readiness predicates or uploaded documents are not an exhaustive normative assessment and do not automatically discharge those obligations.

Use the [model-validity guide](docs/MODEL_VALIDITY_AND_EVIDENCE.md), [operator-readiness guide](docs/OPERATOR_READINESS.md), [capability ledger](release/capability-ledger.json), and [release-qualification guide](docs/QUALIFIED_RELEASE.md) to identify the relevant records and unresolved gates. Check every receipt against its recorded revision, environment, inputs, and observation method.

## Integration

### Instruments and GaugeLink

[Notations GaugeLink](https://github.com/atomtrapping/Notations-GaugeLink) provides instrument-specific acquisition and product workflows over the shared Link development path. NCP owns the downstream engineering environment and registered scientific operations. Review the GaugeLink and NCP dependency revisions together; a successful test in either repository does not establish the combined installation.

Adapters must preserve original source records and their declared semantics. Unit/frame changes, correction, filtering, and inference belong to explicit operations with their own inputs and results. Transport identity, measurement identity, and computation occurrences must not be conflated. Use the existing contracts instead of introducing a parallel measurement store or execution engine.

### Agent and application access

[Provider discovery and native console bindings](docs/PROVIDER_INTEGRATION.md)
connect the source/license inventory to existing workflow pins and expose the
bounded BIM clearance profile through the common admitted-plan interface.

MCP is the **Model Context Protocol** client interface; it is distinct from NCP's project name and from the physical instrument protocol. The candidate includes bounded MCP profiles over the existing agent API, with operator-bound inputs and permitted operations. Read-only inspection and authorized computation are different capabilities; the whole MCP surface should not be described as read-only. The [agent protocol](docs/NET_AGENT_PROTOCOL.md) identifies supported profiles, transport limits, grants, and verification procedures.

An agent request does not grant permission to execute a provider, contact a device, change a calibration, approve a part, or disclose evidence. Tool descriptions and model responses do not replace enforcement at the owning runtime boundary. Test the actual client, transport, and installed implementation used by the integration; a handler test does not establish compatibility with every host.

### Data handling and deployment

Retained workspaces and exports may contain original inputs and outputs. Review content, access permissions, provider boundaries, and disclosure rights before sharing. Retention and computation-proof features do not provide confidentiality by themselves. Only process third-party or standards material under permissions appropriate to the intended use; ordinary access must not be assumed to permit AI ingestion or redistribution.

Remote authentication, multi-user operation, hard real-time guarantees, and physical equipment control are outside the default local workflow and require separate implementation and qualification. The [architecture](docs/SYSTEM_ARCHITECTURE.md), [execution responsibilities](docs/EXECUTION_RESPONSIBILITIES.md), and [stack map](docs/STACK.md) describe the broader boundaries.

## Documentation and development

The [operator manual](docs/OPERATOR_MANUAL.md) covers end-to-end use. The [instrument catalogue](docs/INSTRUMENTS.md) and [integration inventory](docs/INTEGRATION_COVERAGE.md) organize workflows and their limits. The [development guide](docs/DEVELOPMENT.md) covers testing and provider qualification; the [monorepo guide](docs/MONOREPO.md) and [module manifest](instruments/manifest.json) identify imported packages and provenance. [Technical reference](TECHNICAL_REFERENCE.md) preserves earlier material; operation-specific guides own subsequent additions.

An implementation contribution should identify its engineering question, inputs, owned interfaces, independent checks, and save/reopen/replay behavior. Preserve existing numerical engines and identity boundaries. Document what changed, what was executed, and which claims remain unverified. Report reproducible defects and scoped proposals through the [issue tracker](https://github.com/atomtrapping/Notations-Control-Protocol/issues).

## Organization and licence

NCP is developed by **[Notation Systems Inc.](https://notation.systems)** as shared infrastructure for scientific computing, systems engineering, manufacturing and interactive environments.

**© 2026 Giason Pooni, for original contributions.** The root code follows the existing [LICENSE](LICENSE) and [platform licensing policy](docs/LICENSING.md). Imported modules retain their own licences and upstream notices. Project-owned original assets follow the [asset permission policy](docs/licensing/ORIGINAL_ASSETS.md).

Repository access or publication does not change those terms or confer evidence, signing or execution authority. An imported-component inventory does not establish clearance for a combined distribution; see the [distribution and network-source policy](docs/LICENSING.md#distribution-and-network-source).
