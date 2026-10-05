# Model–Reality Contract and thermal identification

This is the first implemented slice of the polymer microfab roadmap:
**retained measurements → identified model → conditional uncertainty → retained
numerical review**. It implements a small usable system-identification workload,
not the full manufacturing compiler, constraint solver, HIL runtime or MPC loop.

## Run it

From a NET installation containing this change:

```sh
net polymer identify example --output cooling-request.json
net polymer identify run cooling-request.json --output-dir cooling-run
net polymer identify inspect cooling-run
net polymer identify replay cooling-run --output-dir cooling-replay
```

Or run the deterministic synthetic fixture directly:

```sh
net polymer identify demo --output-dir cooling-demo
```

All output paths are create-only. The synthetic fixture has 24 samples, with 18
training samples followed by six held-out samples. Its boundary is 300 K, initial
excess is 80 K, and effective time constant is 12 s. These are **numerical fixture
values, not a manufacturing recipe or actual measurements**. Use a new directory
for each execution; `inspect` performs no fitting or provider dispatch.

`candidate` means the declared numerical gates passed. It does not mean a
machine, part, material, sensor or production process has been qualified. The
command exits with status 2 for a rejected/unavailable candidate or input refusal.

## Existing NET integration

The implementation uses `ciw.Session`, the existing `OperationRegistry`, retained
`run.v1` evidence, operation execution/result identities, trusted saved-payload
validation, content seals and the existing covariance artifact. It introduces
no controller, canonical store, device loader, machine endpoint or dependency.

The operation is `polymer.identify-thermal-response.v1`, with role
`state_estimator`. Its executable binding is opt-in through the thermal workflow
registry; merely opening an artifact does not grant execution. The existing
native SIDT/LTI identified-design workflows remain separate and unchanged.

Full nonuniform acquisition times and original samples live in the retained
request. The one-element selection channel is explicitly a declaration envelope,
not a fabricated physical sampling clock. Source/calibration hashes are retained
references, **not authenticity checks or proof of calibration traceability**.

A run retains `request.json`, `workspace.json`, the normal Session artifacts and,
for an accepted candidate, a derived `parameter-state.json`. A rejected fit keeps
its candidate and diagnostic reasons but produces no usable parameter-state
export. All operation results remain `not_verified`; state admission, hardware
actuation and production release are not performed.

## Six distinct state roles

`ciw.model_reality` provides a sealed, data-only view with these explicit slots:

| Slot | Meaning in this slice |
| --- | --- |
| specified | A requirement, not an observation |
| predicted | A model prediction, with a declared origin |
| commanded | A retained command record, not dispatch or authorization |
| observed | An explicitly declared retained observation, never a synthetic fixture |
| estimated | An inferred quantity, with source reference and uncertainty scope |
| verified | Reserved as `null`; promotion is deliberately unsupported |

Entity, quantity, exact declared unit, frame and clock belong to the contract.
Each non-null slot retains its own time, origin, value and source reference.
Unknown uncertainty is `null` with scope `unknown`, not zero. Numerical model
uncertainty is explicitly `conditional_model`; it cannot be relabeled as observed
measurement uncertainty. This generic data contract does not convert units or
prove a physical quantity-to-unit mapping.

Residuals require separate observed and predicted states at the exact same
declared instant. Missing cross-covariance means residual uncertainty remains
unknown. Supplying zero is an explicit independence assumption, not the default.
The calculation retains the cross term:

```text
Var(observed - predicted) = Var(observed) + Var(predicted) - 2 Cov(observed,predicted)
```

This arithmetic does not create a verification occurrence or acceptance decision.

## Bounded identification model

```text
T(t) = T_boundary + A exp(-t / tau)
```

Only `A` (initial excess temperature, K) and `tau` (effective time constant, s) are
fitted. Boundary temperature and declared timestamps are treated as exact inputs.
This does not identify thermal conductivity, interfacial heat transfer,
crystallization, viscosity, machine compliance or a spatial temperature field.

For each proposed log-time-constant, weighted least squares solves `A`
analytically. A 65-point logarithmic grid selects a local interval; 64 fixed
refinement steps follow. There are exactly **132 profile evaluations**. Bounds
are explicit and hard, with endpoint roundoff restoration only. This is a bounded
search, not a certificate of global optimality.

The request must declare the chronological split before fitting: at least six
training samples followed by at least three held-out samples. Holdout values are
never used to select the fitted parameters. Gates test thermal excitation,
Jacobian rank/conditioning, parameter bounds and training/holdout standardized
residuals. Non-cooling, rank-deficient, boundary-limited, ill-conditioned and
residual-failing candidates remain rejected evidence rather than disappearing.

The conditioning diagnostic uses dimensionless parameter coordinates: the
initial-excess scale is `max(abs(A), 1 K)` and the time-constant scale is `tau`.
The threshold and scaling convention are explicit. They are numerical diagnostic
choices, not universal physical adequacy criteria.

The request is limited to 2 MiB and 4,096 samples. It rejects implicit unit
conversion, unordered or duplicate samples, non-finite values, boolean numbers,
zero/unknown measurement uncertainty and missing assumptions. Noise is modeled
as independent Gaussian temperature error with known positive standard
uncertainties; correlated or unknown noise requires a future extension rather
than silent diagonalization or imputation.

## Conditional covariance and predictions

The two-parameter local covariance is calculated with a scaled-Jacobian SVD and
stored in NET's existing covariance schema, preserving the cross-covariance.
It is based on the declared known measurement uncertainties, not scaled to zero
because a perfect synthetic trace has zero residual. Mean-prediction uncertainty
uses the complete `J Sigma J^T` expression.

This is **local, conditional parameter uncertainty**. It excludes uncertainty in
boundary temperature, timestamps, calibration, sensor bias and model discrepancy.
It is not a complete metrological uncertainty budget or a future-measurement
prediction interval. Numerical prediction is forbidden outside the retained
trace's time interval and for rejected/unidentified candidates. No applicability
to a different machine, tool, resin lot or part is asserted.

Held-out samples come from the **same trace**, not an independent experiment.
Passing their gate does not establish physical generalization or causal validity.

## Review, replay and integrity

`inspect` checks seals, source/sidecar bindings, exact semantic fields, covariance
structure and numerical algebra at the retained parameters. It does not refit,
prove optimality or establish that the claimed search actually occurred. Hashes
are integrity identifiers, not signatures or proof of scientific truth.

`replay` explicitly performs a fresh Session execution using the same retained
evidence. Execution/result identities are new. It compares numerical outputs with
`rel_tol=5e-11, abs_tol=1e-11`; roles, statuses, sample/work counts, source binding,
authority and hard parameter bounds are not relaxed by that tolerance. Code
identity comparison is reported separately from numerical agreement.

This change does not modify the separate temporal-study validator. Its
cross-platform floating-point defect remains separate work unless fixed by
another change.

## Tests and follow-on work

```sh
python -m pytest -q tests/test_thermal_identification.py
```

Tests cover synthetic parameter recovery, an independent analytic covariance
check, noisy traces, held-out-data separation, excitation/conditioning and
boundary refusals, unknown uncertainty, cross terms, tampering, float roundoff,
Session persistence, no-fit inspection, fresh replay and the existing NET CLI.
The dedicated CI workflow adds the existing polymer workflow/metrology tests and
an installed-wheel CLI journey on Linux and Windows.

Next bounded increments are calibrated measured-trace ingress and a complete
uncertainty budget, independent-experiment model assessment, then an executable
process graph and finite configuration search that consume these model/evidence
bindings. Physical controller authority must remain separate from supervisory
optimization. HIL, active experiments, surrogate compilation and MPC are not
implemented by this change.

## Numerical references

These references support the numerical techniques, not the adequacy of the
single-exponential model for a specific polymer or machine:

- NIST, least-squares fitting: https://www.itl.nist.gov/div898/handbook/pmd/section4/pmd431.htm
- NIST, model validation: https://www.itl.nist.gov/div898/handbook/pmd/section4/pmd44.htm
- NumPy, singular value decomposition: https://numpy.org/doc/stable/reference/generated/numpy.linalg.svd.html
