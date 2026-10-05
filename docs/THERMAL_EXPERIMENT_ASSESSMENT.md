# Frozen-model assessment of a separate thermal experiment

This increment extends the existing thermal identification workload. It does not
replace NET's Session, operation registry, covariance schema or evidence store.
It adds `polymer.assess-thermal-experiment.v1` as an opt-in `backend` operation.
All results remain `not_verified`; no state admission, controller command or
production release is performed.

## Run the synthetic end-to-end example

```sh
net polymer identify demo --output-dir thermal-training
net polymer identify experiment example thermal-training --output experiment.json
net polymer identify experiment run experiment.json --output-dir thermal-assessment
net polymer identify experiment inspect thermal-assessment
net polymer identify experiment replay thermal-assessment --output-dir thermal-assessment-replay
```

The example is a separately declared **synthetic** trace of the canonical 300 K
boundary, 80 K initial excess and 12 s time constant. It is not a measurement or
manufacturing recipe. The generator refuses physical/retained-observation
training records rather than fabricating a physical experiment. It does not
silently generate a new trace from an arbitrary fitted model to guarantee a pass.
Every run destination must be new; existing evidence is not overwritten.

## Contract and occurrence binding

An assessment request contains the exact training request, its completed NET
execution record and its sealed operation-result record. The normal NET saved
result and execution validators check those bindings. The assessment uses the
retained model and covariance: **it never refits A or tau on assessment data**.

The new experiment must declare a different cycle, part, source reference and
disjoint sample identities. Facility, machine, tool, material lot, coordinate
frame, quantity, kelvin unit and boundary temperature remain bound to training.
Samples use explicitly declared elapsed time since each experiment started,
within the original trace's time interval. No clock synchronization, absolute
time correspondence or initial-condition measurement is inferred.

This is a **declared separate experiment**, not proof of statistical independence,
authentic source acquisition or an independent calibration. Renaming arbitrary
files cannot establish those facts. Original source/calibration references remain
unattested; no bytes are fetched and no instrument is acquired by this operation.
Actual measurements require a correctly prepared request with its own provenance.
Raw/calibrated instrument ingress is not added by this increment.

The input budget is 4 MiB and 3–128 assessment samples. Dense covariance cost is
bounded by that sample cap. Missing observation uncertainty is refused; there is
no automatic diagonalization, zero imputation, interpolation or unit conversion.

## Joint uncertainty, not just per-point error bars

Let the frozen parameters be theta = [A, tau], their local covariance be P,
the new temperature vector be y with declared covariance R, and J be the
prediction Jacobian at the new elapsed times. C = Cov(theta, y) has two rows
ordered exactly like the retained parameter covariance and one column per new
sample, in declared sample order. Entries have units K*K and s*K respectively.

```text
r = y - f(theta)
Cov(r) = R + J P J^T - J C - C^T J^T
```

The observation covariance uses NET's existing covariance artifact and binds to
sample order, temperatures, units, frame and source. Model uncertainty correlates
predictions at different times even when observation errors are independent.
Those off-diagonal terms are retained.

Parameter/observation dependence has three explicit modes:

| Mode | Interpretation |
| --- | --- |
| `unknown` | The joint uncertainty is not known. Residual covariance and normalized decision remain unavailable. |
| `declared_independent` | Zero parameter/observation cross covariance is a declared assumption with a retained basis reference. |
| `explicit_cross_covariance` | Retain the supplied two-by-N cross matrix and validate the full joint covariance. |

A valid joint covariance is required before propagation. The implementation does
not clip eigenvalues, discard cross terms, add diagonal jitter or use a
pseudoinverse to turn singular evidence into a passing decision. Residual
conditioning is evaluated in dimensionless correlation coordinates; both stored
triangles are inspected. Cholesky whitening is attempted only for a usable
positive-definite residual covariance.

The residual report preserves the full covariance and each observed/predicted
value. It reports physical-unit RMSE and bias as well as a correlation-aware
whitened RMS score. These are explicitly declared numerical gates, not p-values,
coverage guarantees, physical qualification or causal diagnosis.

## Decision semantics

| Status | Meaning |
| --- | --- |
| `numerically_consistent` | The frozen model satisfies all declared numerical gates under the supplied joint second moments and conditional assumptions. |
| `rejected` | At least one declared residual magnitude or whitened-residual gate fails. |
| `indeterminate` | No magnitude gate failed, but dependence is unknown or residual covariance cannot support the normalized assessment. |

Unknown dependence does not hide an obvious failure of the RMSE/bias gates.
CLI exit status is 2 for rejected, indeterminate, unavailable or malformed input.
A completed execution can legitimately contain a rejected assessment. Both that
result and its evidence remain retained. Neither a numerically consistent nor a
rejected assessment changes the original fit, the Model–Reality verified slot,
or controller authority.

## Audit fixes to the original identification slice

1. A rehashed covariance could previously be replaced by zero when its entries
   were below the generic absolute floating-point tolerance. Comparison now uses
   dimensionless coordinates scaled by the expected marginal uncertainties.
   Tiny but nonzero variances cannot disappear below an absolute SI-unit floor.
2. A candidate's `parameter-state.json` is now required during bundle inspection.
   A missing state export cannot pass unnoticed.
3. The state export must preserve its retained parameter value exactly. Its
   derived standard uncertainty uses a relative comparison with no absolute
   floor, so a tiny positive uncertainty cannot be replaced by zero either.

Artifact hashes stay exact. Their numerical comparison is separate from their
content-integrity checks. Existing genuinely valid Phase 1 bundles remain
readable; the 69 original tests are unchanged and pass.

## Review and replay

Inspection checks the retained occurrence bindings and recomputes residual
algebra without running the identification search or executing a provider. It
leaves the inspected bundle's bytes unchanged. Replay deliberately creates a new
Session execution/result occurrence using the same retained assessment evidence.
It compares exact semantics and scale-aware covariance separately from permitted
floating roundoff and reports runtime code identity separately from agreement.

## Test evidence and limits

The initial local verification of this increment passed 155 focused tests in
both source and installed-package contexts. The installed package was rebuilt by
repacking the retrieved base wheel with the exact changed Python source, then
installing it into an isolated target and running tests outside the checkout.
This is not a clean full-repository wheel build.

Local environment: Python 3.13.5, NumPy 2.3.5, pytest 9.0.2. The project's pinned
NumPy is different; the dedicated Linux/Windows CI matrix must verify the pinned
environment before claiming that platform qualification. The full repository
regression suite and cross-platform replay were not run locally.

A retained synthetic changed-response case (tau changes from 12 to 16 seconds)
produced RMSE 6.6540 K and was rejected without altering the original model.
Unknown dependence produced `indeterminate`. A 64-trial seeded simulation sanity
check with independent generated training/assessment noise produced mean squared
whitened RMS 1.0348. This is a numerical sanity check, not real measurement data or
a statistical coverage certificate.

The existing injection- and extrusion-blow-molding smoke workflows also passed
their software/numerical audits; their intentionally nonconforming parts remained
`NONCONFORMING`.

Boundary temperature, timestamps, calibration bias and model discrepancy are not
included in the uncertainty budget. Independent physical acquisition and
calibration traceability remain necessary before any physical qualification.
No MPC, HIL, constraint solver or manufacturing process graph is introduced here.

## Numerical references

NIST describes residual analysis as central to assessing model adequacy and warns
that a high fit score alone is insufficient. This increment implements bounded
numerical residual checks, not NIST's full model-validation methodology:
https://www.itl.nist.gov/div898/handbook/pmd/section4/pmd44.htm

NumPy documents that Cholesky requires a positive-definite matrix and does not
itself check the full symmetric matrix. The implementation therefore validates
covariance and checks both triangles before whitening:
https://numpy.org/doc/2.3/reference/generated/numpy.linalg.cholesky.html
