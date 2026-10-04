# Model validity and evidence

NET is a research workbench with explicit mathematical contracts and retained
checks. A model's equations, a successful numerical test, a replay and a proof
answer different questions. This guide records the scope of current source,
extending the domain inventory reviewed at
`554bb1540ef1b621af9e08f2d632095ca958150d` on 2026-10-04 with the separate
temperature numerical checker. Qualification evidence must identify the actual
immutable candidate and runtime. This guide does not report new experimental
results or qualify a release candidate.

## Three domains to record

| Domain | What must be specified | What a passing check establishes |
| --- | --- | --- |
| Computational | Operation and version, input types/units, finite bounds, arithmetic, tolerances, dependencies, solver and resource limits | The stated procedure completed or its stated checks passed for the retained inputs |
| Physical applicability | Measurand, geometry/material, installation, operating conditions, time/location meaning, assumptions and excluded effects | Input declarations fit the model's stated envelope; their physical truth still needs evidence |
| Validation coverage | Actual tested conditions, reference chain and uncertainty, independent cases, metrics, frozen acceptance criteria and retained results | Reviewed performance on those conditions, with an explicit argument for any claimed interpolation or transfer |

These domains can differ. An accepted input can be experimentally untested; a
conservation check can pass for an inadequate physical model. Finite tests do
not prove accuracy everywhere inside an allowed interval. Keep the sampled
conditions and the claimed envelope separately inspectable. Never widen a
physical validity claim merely by increasing a parser bound or test tolerance.

## Current scoped paths

The operating guides and source validators own the detailed limits. This table
is a review index, not another runtime policy or a replacement for those checks.

| Path | Current model and domain | Evidence boundary and next gate |
| --- | --- | --- |
| [Temperature processing](TEMPERATURE_QUALIFICATION.md) | One sensor/readout; 1–64 samples; affine conversion; full joint raw/gain/offset covariance; first-order propagation; optional declared installed correction | A separate rational first-order checker compares computed values/covariance and sample decisions. Applicability and uncertainty inputs are declarations. Sample acceptance does not bound temperatures between samples. Establish calibration and installed dynamic response, assess higher-order/rounding effects, and complete the uncertainty budget before claiming physical performance. |
| [Measured thermal](MEASURED_THERMAL.md) | Python two-capacity model; 1–128 equally spaced rows; already-calibrated shell, power and ambient streams; separately held-out core reference | The kernel retains input/model/clock uncertainty but does not propagate it. Its conditional covariance and comparison PASS do not establish measurement uncertainty or physical qualification. Validate the installed chain and model discrepancy on independent heating cycles. |
| [Dynamic fluids](FLUID_DYNAMICS.md) | Linear incompressible reservoir exchange with a lumped compliant boundary; separate periodic, small-amplitude shallow-water wave model | In-repository references, [SciPy comparisons](EXTERNAL_NUMERICAL_COMPARISONS.md), refinement and conservation checks assess specified numerical cases; this is not independent external replication. General molecular/CFD coupling, nonlinear polymer constitutive behaviour and experimental fluid validation remain unestablished. Each extension needs a separate model and coupling qualification. |
| [Hypergraph rewriting](HYPERGRAPH_REWRITE.md) | Fixed typed rules; at most 64 nodes, 128 edges and 32 rules; bounded exploration | Preserving declared heat capacity is a structural invariant, not a heat-transfer experiment. A valid truncated history does not establish exhaustive exploration, general confluence, global solution accuracy or a theory of fundamental physics. |
| [SP1 heat](PROVED_HEAT.md) | Registered integer Jacobi guest; 3–32 cells; 0–64 steps; integer magnitude at most `2^40`; dimensionless values | A fresh proof check binds the expected guest and input/output commitments. It does not prove physical heat, sensor origin, calibration or arbitrary model training. Temperature processing needs its own guest, arithmetic contract and qualification. |

Covariance propagation depends on an adequate measurement model and defensible
input covariance. It does not establish those premises. JCGM GUM-6 addresses
measurement models; GUM and its supplements address uncertainty, including
nonlinearity and propagation of distributions. Use them to assess the pilot's
approximation rather than assuming first-order propagation is universally
adequate. [JCGM measurement and uncertainty guides](https://www.bipm.org/en/web/guest/publications/guides).

## Keep evidence claims separate

| Evidence | Claim it can support | Additional evidence still needed |
| --- | --- | --- |
| Retained bytes, hashes and replay | Content identity and reproducible processing under the recorded procedure | Authenticated acquisition, correct installation and physical representativeness |
| Structural checks and numerical references | Declared invariants, reference agreement and observed refinement behaviour | Coverage beyond exercised cases and adequacy of the physical model |
| A fresh computation proof | Execution of the expected compiled program with the bound public outputs, under the proof system's assumptions | The program's intended semantics and the truth of physical inputs/assumptions |
| Calibration and comparison experiments | A reviewed measurement result and performance in specified conditions | Uncertainty adequacy, transfer to other installations and ongoing stability |
| Research-release checks | Selected software, installation and operator requirements on an immutable candidate | Physical qualification, product-specific conformity and commissioned operation |

SP1 verification must use the intended program identity and relevant public
values; a different guest proves a different computation. Retaining a previous
verification report is distinct from verifying a proof now.
[SP1 development guide](https://docs.succinct.xyz/docs/sp1/getting-started/quickstart).

Metrological traceability concerns a documented calibration chain whose links
contribute uncertainty. A stored certificate or its hash does not establish that
chain or fitness for the intended use.
[NIST traceability guidance](https://www.nist.gov/metrology/metrological-traceability).

No single project-wide `qualified` flag combines these decisions. The
[capability ledger](../release/capability-ledger.json) is a manually reviewed
inventory, not an execution/admission gate. The
[release collector](QUALIFIED_RELEASE.md#collect-and-replay-ci-evidence) evaluates
its separate CI policy; it does not promote the ledger, assess experimental
adequacy or issue product certification. Current temperature reports explicitly
retain `physical_qualification: not_established`, even when a declared numerical
acceptance rule gives `conditional_pass`.

## Evidence required to extend a domain

For each proposed extension, retain a review record alongside the instrument's
existing specification and qualification evidence:

1. Identify the old and proposed operation/model versions, exact source/runtime
   identities, changed ranges and assumptions, new observables, and information
   discarded at each interface. Explain the physical basis for the extension.
2. Freeze analytical or independently implemented reference cases and numerical
   tolerances. Exercise interior points, boundaries and coupled extremes, then
   just-outside cases, missingness, saturation and exhausted resource budgets.
   Check conservation, refinement and interface work where the model requires
   them. Record which invalid conditions refuse and which return unavailable or
   indeterminate values; verify that they cannot silently become accepted data.
3. Assess uncertainty and correlation, discretization/arithmetic effects and
   model discrepancy. Document evidence supporting any independence assumptions;
   do not count the same contribution in both calibration and model residuals.
4. Define held-out physical comparisons before inspecting their outcomes. Retain
   instruments, installation, calibration chain, conditions, raw observations,
   exclusions and failures. Report performance by condition, not just a pooled
   average. Any broader envelope needs a supported interpolation/transfer claim.
5. Review the evidence for the exact configuration and claimed use. Record the
   reviewer, unresolved limitations, maintenance checks, recalibration triggers
   and affected results on version change or withdrawal. Preserve earlier
   results and their original scope; a new version does not retroactively
   qualify them.

This is a review protocol. Existing profile validators enforce their own
computational bounds; NET does not yet execute this entire review automatically.
General molecular/CFD coupling requires new constitutive and interface models,
conservative transfer and independent numerical/physical comparisons. Renaming a
lumped state or removing a refusal cannot supply that evidence.

## First application: polymer temperature during a heating cycle

Use the existing [pilot setup](../examples/temperature/pilot-setup.json) and
[qualification protocol](TEMPERATURE_QUALIFICATION.md). The sensor/readout,
polymer geometry and installation, operating/ramp ranges and required uncertainty
remain open. Until they are selected and experiments completed, the software
supports conditional processing and pilot preparation.

| Work item | Reviewable deliverable | Completion criterion |
| --- | --- | --- |
| Conventional measurement baseline | Frozen measurand, installed-chain model, reference comparisons, full uncertainty budget and untouched validation cycles | The agreed uncertainty and comparison requirements hold over the claimed envelope, with missing/unsupported conditions reported |
| PINN/gPINN comparison | Same observations and frozen splits as the conventional baseline; model/weights identity; separate observation and estimate records; model-discrepancy and out-of-distribution tests | Held-out performance and failure behaviour meet pre-agreed criteria without reference leakage; inference outside qualified conditions stays explicitly unsupported |
| Optional verifiable processing report | Separate temperature guest, numeric contract, expected program identity/public outputs, agreement fixtures and proof-time/resource measurements | Independent verification binds inputs/procedure/report; arithmetic changes are evaluated against the conventional processor; no physical-validity claim is inferred |
| Operational pilot | Reviewed configuration, check standards, recalibration/withdrawal procedures and a product-specific conformity assessment where applicable | Named reviewers close the agreed evidence requirements; research-release CI alone cannot close this item |

Compare within-envelope held-out conditions separately from conditions outside
training or physical applicability. An acceptable average PINN error does not
establish reliable extrapolation. A bounded checker proves only its encoded
checks; neither it nor a small inference proof proves the entire training
process or global solution accuracy.
