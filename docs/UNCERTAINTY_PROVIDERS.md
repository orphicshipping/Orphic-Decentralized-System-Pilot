# Optional uncertainty comparison providers

`ciw.uncertainty_providers` adds two bounded numerical adapters around the existing
NCP covariance contract. It does not install libraries, register a default MCP
capability, issue instrument commands, grant permissions or create another
measurement store. The original Link study remains the primary scalar model.

| Adapter | Implemented scope | Required declarations |
| --- | --- | --- |
| `pydynamic_fir(request)` | PyDynamic 2.5.1 convolution/FIR, 2–64 samples, 2–16 dimensionless taps, `valid` or `full` boundary | Exact uniform **acquisition** ticks and timebase; zero initial/padded conditions; coefficient provenance; explicit exactness or full native joint covariance; statistical independence between the input vector and coefficient vector |
| `compare_link_affine(request)` | MetroloPy 1.1.1 comparison of the existing Link affine correction and arithmetic mean | Existing retained Link study request, explicit full joint covariance including shared input/gain/offset terms, declared unconstrained coordinate basis |

Both use native `covariance-artifact.v1` for input and output. Provider objects
are never serialized. The MetroloPy result includes the full corrected covariance
and a native mean covariance. The FIR result retains the coefficient reference,
sampling declaration, boundary mode and output sample index origin.

Every returned value and covariance entry must agree with independent exact
`Fraction` arithmetic within 128 binary64 reference ULPs. The tolerance has **no
absolute floor**, and exact zero requires zero. Nonzero reference underflow and
provider covariance loss are refusals. This is a conservative gate for the
bounded model, not a universal error bound for either library. No covariance is
repaired or replaced with the reference answer after a failed comparison.

## Uncertainty and sampling semantics

PyDynamic accepts a one-dimensional array as independent standard uncertainties,
a matrix as full covariance, and `None` as a certain vector. This adapter always
passes matrices. `unknown` refuses before importing the provider. A zero matrix
can be constructed only from an explicit `declared_exact` state with a retained
declaration reference; a supplied native covariance may also contain explicitly
zero rows. Missing uncertainty is never inferred from reported precision.

PyDynamic's convolution API has no input/coefficient cross-covariance argument.
The adapter therefore refuses any nonzero cross-block entry and additionally
requires the explicit `input_and_coefficients_independent` assumption. Within
each vector, all off-diagonal covariance is preserved. For uncertain independent
coefficients the reference includes the covariance-product term, not merely the
first-order Jacobian terms.

FIR eligibility requires sensor-acquisition timestamps, one declared boot/clock,
strictly increasing exactly uniform integer ticks, a positive tick frequency,
and `declared_exact` timing uncertainty. Report receipt ticks, missing timing,
nonuniform sampling, uncertain timing, implicit initial state and other boundary
modes refuse. Declared exact sampling is a conditional mathematical assumption;
the adapter does not authenticate that a physical instrument satisfied it.
Timing and model-discrepancy uncertainty are outside this slice.

Constraints must be exactly:

```json
{"kind": "unconstrained", "coordinate_basis": "declared_quantity_order"}
```

Constrained compositions, alternative covariance coordinates, arbitrary unit
conversion, Monte Carlo, FFT/deconvolution and library-wide functionality are
not exposed or qualified by these adapters. All physical qualification remains
`HELD`, including when numerical comparisons pass.

## Use

Provision a separate environment. The committed
`requirements/uncertainty-providers-windows-py312.lock` captures the exact 42-wheel
Windows CPython 3.12 environment used in the initial qualification, including
NumPy 2.4.3 and SciPy 1.17.1. It is deliberately separate from NCP's base install.
No dependency is added to the default MCP runtime. Install the selected NCP wheel
into that environment separately.

```powershell
uv venv C:/provider-env --python 3.12
uv pip install --python C:/provider-env/Scripts/python.exe --require-hashes --only-binary=:all: -r requirements/uncertainty-providers-windows-py312.lock
uv pip install --python C:/provider-env/Scripts/python.exe --no-deps C:/artifacts/computational_instrumentation_workbench-0.1.0-py3-none-any.whl

C:/provider-env/Scripts/python.exe -m ciw.uncertainty_providers pydynamic-fir C:/evidence/fir-request.json --output C:/evidence/fir-result.json
C:/provider-env/Scripts/python.exe -m ciw.uncertainty_providers metrolopy-link C:/evidence/comparison-request.json --output C:/evidence/comparison-result.json
```

Output paths must be new. The CLI rejects duplicate JSON fields, bounds input
bytes, retains the exact request-byte reference, returns structured refusal on
stderr, and leaves the request unchanged. `tests/test_uncertainty_providers.py`
contains synthetic `fir()` and `comparison()` request constructors. The latter
wraps the existing `link_study_cli.example()` request:

```python
from ciw.link_study_cli import example
from ciw.uncertainty_providers import compare_link_affine

result = compare_link_affine({
    "schema": "ciw.metrolopy-link-comparison-request.v1",
    "study": example(),
    "uncertainty_state": "covariance",
    "constraints": {"kind": "unconstrained", "coordinate_basis": "declared_quantity_order"},
})
```

`scripts/check_uncertainty_providers.py --output-dir <new-directory>` runs the
provider conformance tests and six real CLI journeys, retaining requests,
results, byte identities, per-quantity tolerances, XML and a JSON qualification
receipt. It requires the pinned providers and treats every skip as failure.
Use `--installed` with a selected NCP wheel and an empty `PYTHONPATH` for an
installed-package check. Source and installed runs cover the same cases and
must not be added together. `.github/workflows/uncertainty-providers.yml` provisions
the committed wheel hash lock on Windows Python 3.12, installs the candidate NCP
wheel, runs this installed qualifier outside the checkout, rejects every skip,
and uploads the wheel, requests, results and qualification receipt.

`scripts/retain_uncertainty_provider_environment.py --pins <exact-pins.txt>
--output-dir <new-directory>` is a separately invoked release-host tool. It
downloads the pinned public PyPI wheels, the two provider source archives and
release metadata, verifies hashes and sizes, and compares installed Python and
native-library code with the selected wheels. It does not install or execute
archives. Its output includes a wheelhouse, source archives, metadata,
platform-specific hash lock and `environment.json`. A later offline install can
use `--no-index --find-links <retained-directory>/wheelhouse --require-hashes`.

## Qualification findings and limits

The initial Windows Python 3.12.14 source gate passed 44 tests with zero skips,
plus six retained CLI calls. Cases include known analytical FIR outputs,
uncertain coefficients, full off-diagonal covariance, explicit exactness,
shared calibration error, covariance around `1e-280`, cancellation, altered
provider results and contract refusals. This is synthetic software/numerical
qualification; no installed instrument chain was exercised.

Two extreme MetroloPy cases intentionally **refuse**:

- A tiny surviving covariance between a cancelled output and much larger outputs
  can be returned as zero by the provider. The rational comparison catches it.
- Degenerate shared correlations can produce nonfinite intermediate uncertainty
  and an upstream exception. It is converted to a bounded provider refusal.

The supported single-row cancellation case preserves the independent `1e-280`
offset variance. Uniformly small multi-row covariance and the ordinary shared
calibration example pass. These findings are not a claim that all covariance
scales or arbitrary MetroloPy computations are qualified.

PyDynamic 2.5.1 announces that it was archived in May 2024 and is no longer
maintained for security or other patches. Keep this adapter optional, locally
bounded and identified. The upstream release uses an LGPLv3+ classifier and
contains a separate GPLv3 notice for `impinvar`; its source archive includes
`licence.txt` even though the release wheel omits that top-level file. MetroloPy
1.1.1 identifies `GPL-3.0-only` and includes its license in the wheel. Source
archives and license-file hashes are retained for release review. This records
package metadata and inspected notices; it does not approve a redistribution
bundle or claim a legal compatibility determination.

These archives are identified PyPI release bytes, **not** the branch-named Drive
snapshots studied earlier. The earlier corpus archive hashes do not establish
the identity of the provisioned release.

## Primary API sources

- [PyDynamic convolution contract](https://pydynamic.readthedocs.io/en/latest/PyDynamic.uncertainty.html#PyDynamic.uncertainty.propagate_convolution.convolve_unc).
- [PyDynamic 2.5.1 release metadata](https://pypi.org/pypi/PyDynamic/2.5.1/json) and its retained source archive/API implementation.
- [MetroloPy correlated quantities and covariance methods](https://nrc-cnrc.github.io/MetroloPy/_build/html/hand_made_doc.html#basic-gummy-methods).
- [MetroloPy 1.1.1 release metadata](https://pypi.org/pypi/metrolopy/1.1.1/json) and its retained source archive/API implementation.
