# Notations Engineering Terminal

**A local workbench for retained investigations, bounded numerical models and optional instrument integrations.**

[Notation Systems](https://notation.systems) · [Quickstart](#quickstart) ·
[Instruments](docs/INSTRUMENTS.md) · [Integration coverage](docs/INTEGRATION_COVERAGE.md) ·
[Temperature processing and pilot plan](#temperature-measurement-and-qualification) ·
[Research release](docs/QUALIFIED_RELEASE.md) · [Documentation](docs)

**NET** combines small numerical instruments with records of their inputs,
assumptions, execution and checks. The built-in examples are synthetic and the
physical models are deliberately narrow. Optional providers and client adapters
extend particular workflows; the catalogue does not establish a validated
metrology system, general CFD solver or game-production platform.

The canonical repository is
[atomtrapping/Notations-Engineering-Terminal](https://github.com/atomtrapping/Notations-Engineering-Terminal).
The former `atomtrapping/Notations-Systems-Terminal` and
`giasonpooni/Notations-Engineering-Terminal` URLs redirect here (checked
2026-10-04). The distribution is `computational-instrumentation-workbench`, the
Python import is `ciw`, and `net` and `ciw` are its command entry points. These
compatibility names identify this same package; imported modules retain their
own package identities.

The working loop is **author → run → observe → compare → modify → check**.
Inputs, assumptions, results, failed attempts and explicit checks remain
inspectable. Reopening an investigation reads its retained state; an explicit
replay creates a new execution.

## What runs today

| Surface | Implemented calculation or behavior | Evidence boundary |
| --- | --- | --- |
| Investigation host | Local sessions, retained successes and failures, save/reopen and explicit replay | Reopen reads records without executing providers. |
| Impact | Fixed-step velocity Verlet for unilateral spring contact; other bounded benchmark profiles | Synthetic low-dimensional fixtures, not material qualification. |
| Liquid exchange and waves | Four-state linear reservoir/piston ODE with implicit midpoint; separate periodic linear surface wave | Numerical mass, energy and interface-work checks for the declared equations; no experimental validation or general CFD. |
| Thermal estimation | Python two-capacity model, Joseph covariance update and held-out core comparison | Built-in recordings are simulated; calibration authenticity and physical accuracy remain unestablished. |
| Temperature processing | Affine conversion and first-order joint covariance for declared samples | Conditional software calculation; the installed measurement pilot and product assessment are plans. |
| Polymer | Cycle statistics, lumped cooling and pressure-arrival estimates, bounded control examples | Model assumptions and synthetic numerical checks, not qualified polymer process engineering. |
| Workflow declarations | Typed composition and bounded hypergraph transformations | Structural checks do not calculate physical behavior. |
| Optional integrations | Explicit provider bindings, SP1 integer-heat proof path, BIM and Blender/Godot/Bevy clients | Installation and profile-specific execution evidence required; absent providers refuse. A saved proof is historical on inspection. |

[Runnable domain examples](docs/DOMAIN_EXAMPLES.md) exercise the built-in paths
from an installed wheel. [Numerical comparisons](docs/EXTERNAL_NUMERICAL_COMPARISONS.md)
compare selected equations with third-party numerical code and a heat-equation
solution. They are maintained in this repository and are not independent
external replication or physical validation.
The [institutional data campaign](docs/INSTITUTIONAL_TESTING.md) runs retained
NIST, Oxford, CWRU and EPA originals against an installed wheel. It checks native
numerical kernels, observed/reference records, missing evidence and retained
results with source hashes and independent arithmetic. Its direct numerical
helpers preserve external source identity; full measured-series Workbench/MCP
integration and physical qualification remain separate requirements.

The [instrument catalogue](docs/INSTRUMENTS.md) and
[coverage matrix](docs/INTEGRATION_COVERAGE.md) distinguish built-in calculations,
provider requirements, client adapters and planning records. A callable feature,
a passing numerical check, a guide and an experimentally validated instrument
are different forms of evidence.

## Polymer workflows

Run `net polymer demo --output-dir polymer-demo` for a saved cycle report and audit,
or `net polymer qualify --output-dir polymer-qualification` to exercise the
synthetic qualification campaign for both process profiles and their agent workflow.
Here `qualify` names software checks, not physical process qualification.
Retained native calibration import and the finite qualification evidence are described in the
[operator guide](docs/POLYMER_PROCESSING.md).

`net polymer leakage` adds retained coolant-volume and material-mass accounting
through the existing pinned FlowState kernels, with full covariance, replay and
independent numerical audits. The [leakage guide](docs/leakage.md) includes provider
setup and runnable examples; an unaccounted deficit does not establish its cause.

## Temperature measurement and qualification

The proposed first metrology pilot targets **polymer temperature during a
heating cycle**. It has not been performed: no installed sensor/readout,
polymer/range-specific reference comparison or complete experimental uncertainty
budget is supplied. Two processing paths are implemented; the pilot and
conformity records below are planning templates.

| Status | Path | Scope |
| --- | --- | --- |
| Implemented: observation conversion and uncertainty | [Temperature MCP profile](docs/NET_AGENT_PROTOCOL.md#temperature-measurement-processing-profile) | Affine calibration for 1–64 samples, full joint raw/calibration covariance, optional declared installed correction, and conditional acceptance. |
| Implemented: retained-recording core estimate | [Measured thermal workflow](docs/MEASURED_THERMAL.md) | Already-calibrated shell, power and ambient streams feed a conventional estimator; a held-out core reference supports a separate comparison. |
| Planned: installed measurement qualification | [Pilot protocol](docs/TEMPERATURE_QUALIFICATION.md) and [setup record](examples/temperature/pilot-setup.json) | Reference comparisons, repeatability, environmental effects, uncertainty, held-out validation and maintenance. |
| Unassessed: product conformity | [Product-conformity guide](docs/TEMPERATURE_PRODUCT_CONFORMITY.md) and [assessment record](examples/temperature/conformity-assessment.json) | Product/configuration scope, applicable requirements, evidence, laboratory credentials, review and reassessment. |

Prepare and serve the synthetic MCP processing example:

```sh
python -m ciw.agent_mcp temperature-config --output-dir temperature-agent
python -m ciw.agent_mcp serve --instrument temperature --profile temperature-agent/profile.json
```

The example deliberately has an incomplete uncertainty budget, so acceptance
remains indeterminate. To supply an operator-prepared processing request, add
`--request /absolute/path/request.json` to the configuration command above and
use a fresh output directory. The pilot and conformity planning records are not
executable requests.

Raw observations, calibrated sensing-element temperatures and polymer estimates
retain distinct labels. The affine and thermal-recording paths have separate
contracts and no automatic handoff. Their software fixtures establish processing
behaviour; physical qualification still requires the actual sensor/readout,
installation, polymer, operating range, uncertainty requirement and experiments.

PINN/gPINN comparison and a temperature-specific SP1 guest remain programme work.
The existing [SP1 heat workload](docs/PROVED_HEAT.md) has its own bounded
computational claim. Numerical acceptance, computation proofs, metrological
traceability and product conformity each require their own evidence.

## One repository, modular instruments

The [engineering monorepo](docs/MONOREPO.md) co-locates 21 imported public modules
under [instruments/](instruments), alongside the root Terminal package. Modules
retain their mathematics, package identities, original source history, tests and
licences. Shared contracts connect them through declared interfaces.

| Boundary | Responsibility |
| --- | --- |
| Terminal and composition | Investigation state, configuration, operation selection and dispatch. |
| Measurement and inference | Observations, timing, uncertainty, estimation and diagnostics. |
| Domain engines | Physical models, numerical implementations and their validity domains. |
| Evidence and verification | Provenance, reference checks, correction dependencies and explicit admission boundaries. |
| Representations and clients | Human, reasoning, spatial and visual projections of retained state. |

The distribution and entry-point names are listed above. Evidence, operation,
execution and verification identities remain separate.
NET retains investigation records; canonical evidence/state authority stays at
its declared evidence and admission interfaces.

Blender remains an authoring application. Godot and Bevy retain their own live
worlds. Scientific engines retain their numerical responsibilities. Game state
and industrial evidence keep separate authority even when they share tooling.

The repository consolidates source, but some provider bindings still require
explicit runtime checkouts, executables or credentials. Consolidation alone does
not establish that every workflow can run after an upstream repository is removed.
The migration and qualification guides record the current dependency closure.

## Bounded dynamic fluid models

`net fluid` retains a time-dependent two-reservoir exchange model with a
bidirectionally coupled compliant boundary, and a separate periodic linear
surface-wave profile. In-repository reference implementations,
mass/energy/interface-work checks, and temporal/spatial refinement test the
declared equations and configurations. The [fluid guide](docs/FLUID_DYNAMICS.md)
includes runnable commands, model equations, a numerically checked scalar-state handoff, and explicit
expansion routes. The existing FlowState snapshot estimator and runtime pin remain
unchanged; experimental validation and general molecular/CFD coupling remain
unestablished.

## Evolving workflow structures

`net rewrite` adds an optional, bounded hypergraph computation model for ensemble
construction and workflow auditing. Fixed rules replicate nodes, vary declared
parameters, add typed couplings and split a thermal zone while preserving its
declared heat capacity. Retained histories distinguish content identities from
rewrite execution, result and verification occurrences. Bounded exploration
keeps branch identities; explicit schedule checks compare exact terminal graphs.

```sh
net rewrite example --profile thermal --output results/thermal-request.json
net rewrite run results/thermal-request.json --output results/thermal-rewrite.json
net rewrite verify results/thermal-rewrite.json --output results/thermal-verification.json
```

The [rewrite guide](docs/HYPERGRAPH_REWRITE.md) covers types, limits, replay and
qualification. These transformations describe structure; numerical models still
calculate behavior, and existing operation contracts govern execution. Preserved
declarations do not establish predictive accuracy or validate a proposed mapping
from hypergraph evolution to fundamental physics.

## Quickstart

Follow the [operator quickstart](docs/quickstart.md) to install, check and use the
instrument. `net catalog` shows its command surfaces and scientific provider
requirements; `net start --output-dir results/first-use-001` runs the bounded
synthetic first-use check and retains its completion receipt. Start or resume
one local investigation with `net workbench --output-dir results/workbench`.

The [operator readiness guide](docs/OPERATOR_READINESS.md) explains exact public
provider provisioning and specialist qualification. Scientific provider and
engine dependencies remain optional and explicitly bound. Reading retained
results must not silently launch a runtime or rerun an experiment.

The [installed public-tool guide](docs/RUN_NET.md) adds checked paths through
impact, signed Legibility and atmospheric handoffs. Python 3.11 or newer is
required.


```sh
python -m pip install .
net doctor --profile core
net catalog
net start --output-dir results/first-use-001
net workbench --output-dir results/workbench
```

For the signed legibility demonstration:

```sh
python -m pip install -e ".[legibility]"
ciw legibility demo --output-dir results/legibility-demo
ciw legibility verify results/legibility-demo \
  --trust results/legibility-demo/demo-trust.json --expected-version 1
```

Open `results/legibility-demo/review.html` to inspect the synthetic specimen.
The demo trust anchor exercises key matching; it is not an organizational
identity certificate. See [LEGIBILITY.md](docs/LEGIBILITY.md) for operator keys,
artifact commitments and verification boundaries.

Inspect and audit the imported module registry:

```sh
python scripts/superrepo.py list
python scripts/superrepo.py audit
```

Use a full-history checkout for the source-preservation audit. The
[technical reference](TECHNICAL_REFERENCE.md#quickstart) and module guides provide
the remaining installation, analysis, provider-binding and replay commands.
Graphical clients and scientific providers are optional, explicitly configured
dependencies.

## Development and qualification

NET is in active development. The [research-release plan](docs/QUALIFIED_RELEASE.md),
[capability ledger](release/capability-ledger.json) and
[qualification policy](release/qualification-policy.json) distinguish implemented
features, installed/operator evidence and the remaining release gates. A merged
feature is not a declaration that the full research release or a physical
measurement system has been qualified.

Use the instrument's operating guide for its supported model, configuration,
provider requirements and retained checks. Production metrology needs calibrated
observations and experimental validation; machinery control needs separate
commissioning and authorization. Cryptographic integrity does not establish
physical truth or current source dependencies.

[Integration coverage](docs/INTEGRATION_COVERAGE.md), the
[superrepo audit](docs/SUPERREPO_AUDIT_2026-10-04.md), and each instrument's guide
provide the detailed status. [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md)
preserves the earlier reference and historical terminology; current module
guides take precedence for later additions.

## Attribution and licence

Project materials use **Notation Systems Inc.**, **Notations Gaming**,
**Notation Manufacturing** and **Notations Laboratories** as declared
organizational names. The repository is not evidence of corporate registration,
laboratory accreditation or product certification.

**© 2026 Giason Pooni, for original contributions.** The root code is governed by
the existing [AGPL-3.0-or-later licence](LICENSE). Imported modules retain their
own licences and upstream notices, recorded in [the manifest](instruments/manifest.json).
A manifest is an inventory, not clearance for a combined distribution. Review
[the distribution and network-source policy](docs/LICENSING.md#distribution-and-network-source)
for the actual components and deployment. Project-owned original assets use the
[asset permission policy](docs/licensing/ORIGINAL_ASSETS.md). Repository publication
does not change those terms or confer evidence, signing or execution authority.
