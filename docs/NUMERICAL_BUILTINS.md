# Numerical built-ins: supplied-model calculations

Seven bounded operations extend `statistics.v1` and `spectrum.periodogram.v1`
in the built-in NET/NCP registry. They use the existing registered-operation,
Session, retained-result, CLI and MCP execution paths. No external numerical
provider or new MCP tool is required. NumPy is already a core dependency.

These calculations assess **declared models**, not physical instruments.
Source identity binds a request to a retained recording; it does not prove that
the supplied arrays, derivatives, covariance or independence declarations were
derived from that recording. No operation acquires hardware data, establishes
traceability, certifies calibration, grants control authority or creates a
physical acceptance threshold.

## Operations and mathematics

| Operation | Computation | Important limits |
| --- | --- | --- |
| `uncertainty.propagate.v1` | First-order full covariance `C_out = J C_in Jᵀ`; marginal standard uncertainties | Supplied Jacobian and nominal output; no function evaluation or nonlinear remainder estimate |
| `uncertainty.budget.v1` | Same transport, plus each diagonal `J_ai² C_ii` and signed pair `J_ai J_ak (C_ik + C_ki)` | Correlated contributions can be negative; not independent percentage allocations |
| `calibration.fit.v1` | Affine generalized least squares `y = offset + slope*x`, with full known absolute observation covariance | Exact x only; at least three samples; positive-definite exactly symmetric covariance; no errors-in-variables model |
| `calibration.residuals.v1` | Held-out `r = y - Hβ`, `C_r = C_y + H C_β Hᵀ`, bias, RMSE and covariance-aware diagnostics | Held-out observations must be declared independent of coefficients; training/evaluation sample IDs must be disjoint |
| `sensitivity.local.v1` | Supplied `J`, scaled `D_out⁻¹ J D_in`, and scaled row norms | Does not estimate derivatives, evaluate arbitrary code or infer scales |
| `observability.rank.v1` | SVD of scaled discrete finite-horizon `[C; CA; …; CA^(h-1)]` | Supplied linear transition and observation; rank depends on declared scales, horizon and `rcond`; not nonlinear/global observability |
| `model.discrepancy.v1` | `r = observed - predicted`, `C_r = C_oo + C_pp - C_op - C_po`, bias, RMSE and diagnostics | Requires aligned identities and full joint covariance; not independent validation of the prediction model |

GLS uses centered/scaled x, covariance whitening and SVD, not normal-equation
inversion. Coefficient covariance is based on the supplied absolute noise model;
it is **not multiplied by a fitted residual variance**. A perfect fit therefore
does not imply zero coefficient uncertainty. Its reported condition number is
for the whitened centered/scaled design, not the raw physical-coordinate design.

Residual/discrepancy chi-square is `rᵀ C_r⁻¹ r` when Cholesky succeeds. Singular
or nonpositive covariance produces `chi_square: null` with an explicit status,
not a pass. A zero marginal uncertainty produces a null standardized residual.
No p-value, coverage factor, confidence interval or acceptance decision is
inferred. Gaussian assumptions would be needed to interpret chi-square as a
reference distribution.

## Request contract

Every operation accepts exactly `parameters: {"request": {...}}`. Unknown keys
are refused. Every request includes:

- `source_evidence_id`: exactly the retained run's evidence ID.
- `assumptions`: 1–32 nonempty strings, each at most 256 characters.

An **axis** has exactly `quantity_ids`, `units`, `frame`, `reference_values`.
Quantity IDs are unique and ordered; units and nominal values follow that order.
Frames and units are explicit strings, not automatic conversion instructions.
Jacobian entries have output-unit/input-unit semantics. Dimensionally consistent
model construction remains the caller's responsibility; units are not parsed
as symbolic expressions. A discrete transition describes the supplied
`sample_period_s`; this operation does not discretize a continuous-time model.

Covariance inputs use the existing content-addressed
[`covariance-artifact.v1`](COVARIANCE.md) contract. Construct them with
`ciw.core.covariance.create_covariance_artifact`, not by inventing hashes.
Order, unit strings, frame and reference values must match their declared axis.
Observation/input/joint covariance provenance must include the request evidence
ID. Coefficient covariance can retain a different training evidence ID; derived
provenance retains both. Shared covariance checks operate in correlation scale,
preserve the supplied matrix and never clip, symmetrize or repair it. GLS further
requires exact symmetry and positive definiteness.

| Operation(s) | Required fields beyond the common fields |
| --- | --- |
| Propagation, budget | `input` axis, `output` axis, `jacobian` (output × input), `covariance` matching input |
| Sensitivity | `input`, `output`, `jacobian`, positive `input_scales`, positive `output_scales` |
| Affine fit | `model: "affine_exact_x"`, `sample_ids`, `x`, `y`, `x_unit`, `y_unit`, `frame`, `observation_covariance`, `rcond` |
| Held-out residuals | Same sample/model fields as fit, but replace `rcond` with `coefficients`, `training_sample_ids`, `independence: "held_out_observations_independent_of_coefficients"` |
| Observability | `states` axis, `outputs` axis, `transition` (state × state), `observation` (output × state), positive `state_scales`, positive `output_scales`, `horizon`, positive `sample_period_s`, `rcond` |
| Model discrepancy | `sample_ids`, `observed`, `predicted`, `unit`, `frame`, `joint_covariance` |

For calibration, observation covariance IDs are the sample IDs, units repeat
`y_unit`, and reference values equal `y`. Coefficients have ordered IDs
`["offset", "slope"]`, units `[y_unit, y_unit + "/" + x_unit]`, matching frame,
and reference values equal the coefficients. Coefficient provenance metadata
must contain the exact `training_sample_ids` used by a held-out request. Fit
results create this metadata automatically. The held-out example supplies
separate synthetic coefficients; it does not reuse the fit's training samples.

Discrepancy joint covariance order is all `observed:<sample_id>` quantities,
then all `predicted:<sample_id>` quantities, with reference values in that same
order. Independent models still require explicitly supplied zero cross-blocks.

Bounds: 32 quantities per model axis; 256 calibration samples; 128 discrepancy
pairs; integer horizon 1–32; `rcond` in `[1e-15, 1e-2]`; canonical request at most
512 KiB. Only finite JSON numbers are accepted, not booleans or numeric strings.
Rank is the count of singular values strictly above `rcond * largest_value`.
Unrepresentable arithmetic is refused rather than silently reported as a valid
result. These are binary64 computations, not interval-certified numerics.

## Run the complete example

From a checkout, in a Python virtual environment:

```sh
python -m pip install '.[mcp-control]'
python examples/numerical_operations/prepare.py --output-dir work/numerical-demo-001
net capabilities --json
net run work/numerical-demo-001/experiment.json \
  --run work/numerical-demo-001/source.json \
  --output-dir work/numerical-result-001
```

Preparation creates a synthetic source, seven independent graph nodes and an
explicit profile; it executes no kernels and refuses an existing destination.
Inspect `graph-run.json` in the result directory. Expected reference results:
propagated variance 21; budget diagonal terms 16 and 9, cross term −4; fitted
offset 1 and slope 2; scaled sensitivity `[4/7, -3/7]`; observability rank 2;
discrepancy bias 1.5, RMSE `sqrt(2.5)`, chi-square 5. The held-out residual
covariance is `[[1.25,.30,.35],[.30,1.39,.48],[.35,.48,1.61]]`.

## Operate through MCP

Configure a local MCP client to launch the installed interpreter with:

```sh
python -m ciw.net control serve --profile /absolute/path/work/numerical-demo-001/profile.json
```

The existing MCP tools remain the interface:

```text
ncp_capabilities_list → ncp_plan_template → ncp_plan_create
    → ncp_plan_admit → ncp_run_submit → ncp_run_get → ncp_artifact_read
```

Use the returned plan ID and digest for admission/submission, and a stable
`attempt` for idempotent retry. The example profile grants these seven operation
IDs, a maximum of eight nodes and four executions, with no candidate parameter
domains. Changing input arrays requires a deliberately prepared new baseline
and profile. Discovery does not grant execution permission; the older statistics
demo profile remains statistics-only even though it discovers nine operations.

For retained control state, restart the identical profile/runtime with `--resume`.
Source/catalogue changes may invalidate the frozen resume identity. Do not edit
old policy/history to force admission: retain the old environment for inspection
or resume, and create a new profile/output directory for changed deployments.
The local operator console is a client of this same boundary; these operations
do not add a second unrestricted execution endpoint.

## Retention and verification

Results carry `ciw.numerical-result.v1`, operation ID, request digest, evidence ID,
and `scope: "declared_model_only"`. The surrounding existing execution/result
records retain full parameters, execution identity and runtime identity. Runtime
metadata records Python/NumPy versions and SHA-256 commitments for the numerical
contract, kernel, offline validator, workflow, runtime and shared covariance
sources. These hashes identify code bytes, not signed scientific qualification.

Reopening validates contracts, identities, provenance and selected algebraic
relationships **without executing numerical kernels**. It is not independent
recomputation of every covariance, fit or SVD. Explicit replay creates a fresh
execution/result occurrence. Recorded verification remains separate from
calculation success, repeatability and physical validity.

Regression evidence lives in `tests/test_builtin_numerical.py` and
`tests/test_builtin_numerical_mcp.py`: analytical/Decimal references, independent
GLS normal-equation reference, cancellation and scale cases, refusal tests,
save/reopen/replay, runtime tampering, CLI dispatch, real MCP discovery/admission/
execution, artifact schema validation, retry and legacy-session resume.

```sh
python -m pip install '.[dev,mcp-control,bench-models]'
python -m pytest -q tests/test_builtin_numerical.py tests/test_builtin_numerical_mcp.py
```

For an installed-wheel check, run those tests with the new wheel installed in
a separate environment, `python -I -m pytest -o pythonpath= ...`, and confirm
`ciw.__file__` resolves into that environment's `site-packages`. Clearing the
repository pytest `pythonpath` override is necessary to test the installed code.
The MCP control workflow includes a dedicated Linux/Windows numerical job that
runs both source and installed-wheel checks and rejects skipped cases. A
configured job is not evidence that a particular hosted run has passed.

Further work is explicit: uncertain-x/nonlinear calibration, automatic model
derivatives, Monte Carlo/interval propagation, distribution/coverage assumptions,
domain-specific acceptance policies, and physical reference experiments are not
implemented by these v1 contracts.
