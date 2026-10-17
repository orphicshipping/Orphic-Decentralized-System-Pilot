# CAREX reference matrices through retained native rank diagnostics

`data.rank-carex-matrices.v1` connects an explicitly retained NPZ reference to
`oit.rank_diagnostics`, the existing Observability Testbed endpoint. It adds no
numerical solver. The operator explicitly binds the existing OIT source package;
saved files cannot select, import or execute a provider.

## Scope and preserved data

The bounded NPZ loader accepts exactly A, B, Q and R as real, finite, nonempty
numeric two-dimensional arrays, with the CAREX matrix dimension relationships.
It checks compressed and expanded budgets, array dimensions and exact NPY payload
sizes before allocating an array. Object/pickle, structured, complex, boolean,
text, ambiguous/duplicate/path-like members, unsupported compression, nonfinite
values and integer values not exactly representable by OIT's float64 conversion
are refused. NumPy always loads with `allow_pickle=False`. No archive member is
extracted or executed.

The existing artifact/data-record envelope retains every original NPZ byte.
Its checked manifest preserves archive member order, each original NPY member
hash, dtype (including endian), shape and C/Fortran storage order. OIT's existing
internal float64 conversion is explicit; source bytes and metadata remain intact.
Rows and columns are not reordered, balanced, whitened or assigned physical units.
Q and R are continuous-time Riccati cost weights, not measurement covariance.
Unknown physical units and experimental uncertainty stay unknown.

This is matrix rank, not observability or identifiability. It supplies no C matrix,
observation horizon, measurement covariance, parameter sensitivities, sensor
placement, Riccati solution, discrete-time conversion or Lyapunov certificate.
In particular, a rank result for CAREX 19/20 does not repair the difficult Riccati
residuals reported by their source. Content hashes establish integrity, not
publisher authentication or physical qualification.

## Explicit numerical policy and existing lifecycle

The first bounded request profile requires all three tolerances explicitly:
rank_rtol=1e-12, rank_atol=0.0, weak_rtol=1e-6. A direction is retained precisely
when s > max(rank_atol, rank_rtol*s_max). The source matrix is analyzed in raw
coordinates. The native routine's internal max-absolute scaling is only its
existing numerical SVD technique. Absolute tolerance and singular values have the
matrix's unspecified physical units; relative tolerances are dimensionless.
These are numerical acceptance rules, not measurement uncertainty.

Programmatic integration into an existing Session:

```python
from ciw.data_rank_bridge import RankRuntime, retain_carex, source_parameters

session.operations.register(RankRuntime(oit_source_root).operation())
source = retain_carex(session.workbench, exact_npz_bytes,
    dataset_id="carex-6", recorded_at=recorded_at,
    provenance={"source_url": reviewed_url, "acquisition": "exact retained bytes"})
response = session.handle({"protocol_version": 1, "request_id": "rank-carex-6",
    "type": "operation.execute", "payload": {
        "operation_id": "data.rank-carex-matrices.v1",
        "parameters": source_parameters(session.workbench, source["source_id"])}})
```

The backend consumes only that unchanged Workbench source. It records complete
native rank diagnostics, source content references, mapped quantities, explicit
tolerances, source/runtime hashes and an execution occurrence through the normal
Session lifecycle. Infinite condition numbers have the explicit JSON value
`positive_infinity`; they are not converted to finite numbers or absent values.
The rank operation is discoverable in the read-only data inventory and is not
automatically executable in the default registry.

Workspace reopening checks retained correspondence, spectrum/classification and
output shapes without importing OIT or performing an SVD. These offline integrity
checks do not independently establish numerical truth. Fresh replay requires
explicit rebinding, runs a fresh source-only process and creates a distinct
execution identity. Accepted source corrections invalidate consuming executions
and results through the existing dependency graph while preserving old evidence.
No parallel store, registry owner or authority path is introduced.

## Published-source qualification

```sh
OPENBLAS_NUM_THREADS=1 python scripts/qualify_carex_rank_consumer.py \
  --carex-source-dir /path/to/acquired/sources/riccati \
  --oit-source-root instruments/mathematics/observability/src \
  --output-dir /path/to/new-private-evidence
```

The runner checks the five acquired SciPy 1.17.0 CAREX 6/15/18/19/20 NPZs and the
retained [SciPy source definition](https://github.com/scipy/scipy/blob/v1.17.0/scipy/linalg/tests/test_solvers.py)
against exact SHA256 pins before native execution. It retains the source
definition as data and never executes corpus Python files. The complete 20 arrays
pass through 10 Session execution occurrences (first execution and explicit
replay per archive), totaling 40 existing OIT endpoint calls. Each workspace
reopens without a bound provider, then replays identically when explicitly rebound.

The Session interface requires a recording: the runner uses NET's existing,
clearly synthetic demonstration recording solely as a lifecycle harness. Its
samples, channels and times are never passed to the rank endpoint and never
substitute for a CAREX array. This is not source recording/time-series coverage.

An independent existing SciPy SVD with LAPACK `gesvd` (QR iteration) checks the
native NumPy `gesdd` singular values and rank at the declared tolerance. Normalized
spectrum acceptance is atol=2e-13, rtol=2e-12; retained condition comparison is
rtol=2e-5. Nullspace residuals and orthonormality are separately checked. These
implementations share floating-point/BLAS infrastructure; they are not independent
physical evidence. Synthetic diagonal perturbations provide an additional exact
analytic oracle for threshold equality, above/below threshold, zero, duplicate
columns, 1e-100/1e100 scale changes and absolute cutoffs. Synthetic fixture results
are never reported as acquired-source results.

Install the existing `numerical-checks` extra for that qualification-only SciPy
oracle. Base operation execution requires only the existing NumPy dependency.
Set `NET_CAREX_SOURCE_DIR` to run the opt-in acquired-source test in
`tests/test_data_rank_bridge.py`. Missing source bytes cause an explicit skip;
public tests contain only small synthetic fixtures. Generated evidence and corpus
files stay in operator-owned directories and are not published with this code.

## Shared native interface for other bounded formats

`RankRuntime.evaluate(matrices, tolerances)` accepts an insertion-ordered mapping
of 1–16 explicit names to real numeric NumPy matrices. Each axis is 1–512,
combined array storage is at most 8 MiB, and conversion of integers to native
float64 must be exact. The separate `validate_native(matrices, native, tolerances)`
checks saved-output structure without an SVD. Neither helper assigns CAREX roles
to another format. A format-specific adapter must preserve its own source bytes,
array/quantity semantics, provenance and retained dependency contract; three-
dimensional arrays cannot be flattened into this interface implicitly.
