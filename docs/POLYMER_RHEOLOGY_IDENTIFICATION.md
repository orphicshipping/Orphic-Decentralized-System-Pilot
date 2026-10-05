# Fixed-grid rheological spectrum identification

This extension of `net polymer rheology` converts a declared set of uniaxial
relaxation and/or dynamic-modulus observations into a **candidate** generalized
Maxwell parameter set. It uses the existing NET covariance artifact, content
seals, occurrence identities, JSON boundaries and create-only publication.
It does not replace the forward rheology tool, the separate loading-history
work in PR174, the chemistry benchmark, or the Session/State Ledger substrate.

## Scope and constitutive basis

At one declared temperature, the user fixes 1 to 8 increasing relaxation times
before fitting. Only `E_inf` and the corresponding nonnegative mode moduli are
inferred. The response basis is:

```text
E(t)  = E_inf + sum(E_i * exp(-t/tau_i))
E'(w) = E_inf + sum(E_i * (w*tau_i)^2 / (1+(w*tau_i)^2))
E''(w)=         sum(E_i * (w*tau_i)   / (1+(w*tau_i)^2))
```

All observations are uniaxial moduli in Pa. Relaxation coordinates are seconds;
dynamic coordinates are angular frequencies in rad/s. There is no automatic
Hz conversion, shear/Young conversion, temperature shift, free relaxation-time
optimization, parameter regularization, molecular interpretation, or hidden
resin-property database. A numerically identifiable fixed grid does not prove
that a material's continuous relaxation spectrum is uniquely known.

Rows must refer to the same declared material, frame and temperature. Those
shared declarations are assumptions, not independently authenticated sample
provenance. Input temperatures, coordinates and the model form are conditioned
on; their errors are not estimated or propagated.

## Fitting and covariance

The request retains 5 to 128 rows, their unique sample IDs and explicit `train`
or `holdout` partitions. At least parameters+1 training rows and two holdout
rows are required. The full modulus covariance uses the existing
`covariance-artifact.v1` with exact ordered row IDs, Pa units, reference values,
frame, observation basis and source commitments.

This profile deliberately requires a strictly positive-definite, exactly
symmetric covariance and variances in [1e-24, 1e24] Pa^2. The generic artifact's
PSD tolerance is not permission to invert a singular covariance. Unknown
covariance, zero variance, changed bindings, singularity and unsupported scales
are refused. No jitter, symmetrization, diagonal replacement or pseudoinverse
is applied. Nonzero off-diagonal covariance remains effective in the fit.

Only the training marginal covariance `R_train` enters the optimization:

```text
minimize 0.5 * (A_train E - y_train)^T R_train^-1 (A_train E - y_train)
subject to E >= 0.
```

The implementation scales covariance into correlation coordinates, whitens by
Cholesky, normalizes design columns and scales the right-hand side. It invokes
SciPy's dense BVLS least-squares algorithm with its LAPACK-backed linear
algebra, rather than adding a second custom optimizer. The normalized design
must have full column rank and condition number at most 1e8. Ill-conditioned
or unidentifiable requested grids are refused before optimization.

The optional engine is the repository's existing `numerical-checks` extra;
there is no new dependency pin. BVLS tolerance is 1e-12, with `max_iter=200`.
SciPy's BVLS initialization can add iterations outside that main-loop setting;
iteration metadata is reported under that meaning, not advertised as a total
CPU-work, memory or wall-time guarantee. Dimensions are explicitly bounded.
Fitted moduli must remain in [0, 1e12] Pa; invalid results are refused, not clipped.

## Independent verification

Before returning a candidate, the implementation checks frozen coefficients
against a separately written Decimal80 design/response and covariance
Cholesky/whitening calculation. It does **not** call the production basis,
prediction function or optimizer. On the declared convex fixed-grid problem,
it checks nonnegativity, scaled stationarity, inactive-bound dual feasibility
and normalized complementarity at a 2e-9 numerical policy tolerance. This is
finite-precision KKT consistency, not a formal proof or global parameter-error
certificate. A coherently forged set of coefficients, predictions, residuals
and export metadata must still fail this independent stationarity check.

Retained response components are independently checked at relative tolerance
2e-12 plus an eight-ULP underflow allowance; exact reference zeros stay zero.
The condition-number audit uses independently constructed Decimal design and
whitening inputs, but the SVD remains NumPy/LAPACK; it is not a second linear
algebra implementation. Seeded tests also compare fits with a separate NNLS
solver and an independently constructed complex-valued harmonic basis.

The internal numerical gate creates no verification occurrence. `verify`
creates a fresh, sealed verification receipt linked to evidence, execution,
result and result digest. Inspection checks shape, covariance admissibility,
identities, retained residual arithmetic and export consistency; it never
fits or executes a constitutive response. Historical source hashes remain
provenance, not a trigger to load old code or rerun the original execution.

## Held-out prediction and limits

Held-out values and the held-out/cross-partition covariance blocks never alter
the fitted coefficients. Predictions are evaluated afterward and compared with
the request's declared maximum absolute error in Pa. The result retains both
partitions' RMSE, maximum error and counts. Maximum-error decisions are hard
comparisons; a tiny RMSE comparison allowance cannot relax the holdout limit.

A held-out failure is retained and the `run` CLI exits 2. An independent audit
can still PASS because it confirms that the fit and failed prediction report
are internally correct. Neither outcome releases a part or validates a resin.

A partition label is not evidence that experiments were independently acquired.
No holdout probability, confidence interval or coverage is claimed, especially
when training/holdout measurements are correlated. Parameter covariance is not
reported: active constraints, fixed-grid bias and model discrepancy require
separate treatment. No experimental uncertainty has been certified.

## Existing forward-model export

A numerically admissible candidate is represented as an existing v1 rheology
request, not a new model format. Its parameter-source hash binds the normalized
identification request and inferred coefficients. Exactly inactive zero modes
are omitted from this forward representation; every coefficient remains in the
identification record. A positive mode below the existing 1e-6 Pa forward floor
is **not** silently dropped: export is unavailable with an explicit reason.
An all-zero result likewise does not fabricate a positive stiffness.

The default exported diagnostic load is strain 0.001, a two-point time window
and a few diagnostic angular frequencies. These are authored diagnostic
settings, not measured loading or a manufacturing prescription. Export alone
checks retained structure and emits a candidate declaration; it is not fresh
verification or admission. Run `verify` and the forward audit explicitly.

## Commands

With this candidate installed, including the `numerical-checks` extra:

```sh
net polymer rheology identify example --output request.json
net polymer rheology identify run request.json --output fit.json
net polymer rheology identify inspect fit.json
net polymer rheology identify verify fit.json --output fit-audit.json
net polymer rheology identify export fit.json --output forward-request.json
net polymer rheology analyze forward-request.json --output response.json
net polymer rheology verify response.json --output response-audit.json
```

The provided example is explicitly synthetic. No physical DMA/rheometer data
or instrument calibration certificate is supplied. JSON input rejects duplicate
keys and unknown fields; existing output paths and symlinks are never replaced.
Numerical backend failures become explicit refusals. A filesystem unable to
provide the existing atomic hard-link publication contract refuses publication.

## Integration and qualification

The four-lane scientific workflow now includes identification and the full
installed command chain. The original thermochemical oversized-JSON regression
keeps its complete 65,537-byte payload but has a short explicit test ID. This
fixes the observed Windows `PYTEST_CURRENT_TEST` environment limit failure;
no failing assertion or case was removed. A regression checks the ID policy.

Generic polymer jobs without the optional SciPy engine skip the identification
test module; those skips are not numerical qualification. The scientific
matrix installs the extra and must execute all cases. See the new validation
record for exact local counts, versions, artifact identities and what was not
run. Source-based tests, reconstructed-source wheel tests, hosted checks and
physical qualification are separate evidence scopes.

No existing runtime kernel, provider grant, dependency pin, licensing rule,
state admission, device interface or hardware authority is changed. This
increment is a bounded inverse-model adapter, not a private physics foundation
model or a manufacturing controller.

## Primary references

- Generalized Maxwell and Prony-series constitutive context, COMSOL:
  https://doc.comsol.com/6.3/doc/com.comsol.help.sme/sme_ug_theory.06.029.html
- SciPy bounded linear least squares and BVLS termination semantics:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.lsq_linear.html
- SciPy independent NNLS comparison engine:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.nnls.html
- Pytest current-test environment semantics:
  https://docs.pytest.org/en/stable/example/simple.html#pytest-current-test-environment-variable
