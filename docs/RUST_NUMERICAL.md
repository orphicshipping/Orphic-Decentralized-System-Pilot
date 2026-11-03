# Optional native numerical execution

`RustNumericalBackend` computes the results of the seven existing numerical
operations in a separate Rust executable. A trusted host selects the binary,
three native linear algebra libraries and process policy. There is no Python
computation fallback. The default installation continues to use the existing
Python backend until the host supplies this binding.

This delivers a numerical kernel execution path. Python still owns principal
authorization, plan admission, contracts, covariance declarations, retained
records, sessions and the MCP server. This is not a complete Rust control-plane
port. Native output is authoritative for the selected operation only after the
existing request and result validators accept it. Neither discovery nor a native
commitment authorizes execution.

| Existing operation | Native computation |
| --- | --- |
| `uncertainty.propagate.v1` | Exponent-balanced products and expansion summation of all covariance terms |
| `uncertainty.budget.v1` | The same propagation, diagonal contributions and signed cross terms |
| `sensitivity.local.v1` | Supplied Jacobian scaling and bounded row norms |
| `calibration.fit.v1` | Known absolute covariance, normalized affine design, Cholesky whitening and SVD fit |
| `calibration.residuals.v1` | Held-out prediction with coefficient and observation covariance |
| `observability.rank.v1` | Scaled finite-horizon matrix and exact declared SVD rank cutoff |
| `model.discrepancy.v1` | Observed-minus-predicted residuals with the full joint covariance |

The implementation reuses the imported SCR `execution-core` input/output identity
types and its tagged byte commitment. Those transport commitments remain separate
from CIW canonical JSON identities. Imported source trees and their licenses are
unchanged; their integer execution kernel is not relabeled as a scientific solver.

## Native linear algebra and supported environment

The qualified environment is Linux x86-64, Rust 1.91.0, Python 3.12.14, NumPy
2.4.3, and the NumPy wheel's single-threaded scipy OpenBLAS ILP64 library. Rust
calls native LAPACKE/CBLAS directly. It does not start Python or call NumPy for
mathematical results. Python remains installed for shared scientific validation
and record construction.

The small `src/lapack.rs` FFI module is the only Rust module allowed to contain
unsafe code. It checks dimensions before entering LAPACK, owns all buffers, and
keeps the selected library loaded while function pointers exist. SVD, Cholesky,
linear solves, matrix multiplication, Gram matrices and dot products use that
library. Single-threaded BLAS reductions inspect native floating-point status so
overflow and inexact underflow are refused. LAPACK solve/decomposition status is
handled separately, consistent with the existing NumPy wrappers. Other native
targets are explicitly unsupported.

This choice follows executed differential evidence. An earlier nalgebra SVD
produced 87 different rank decisions in 400 matrices close to the declared
cutoff. It was replaced; the cutoff was not widened. The same 400 cases now have
identical singular values, thresholds and rank decisions in the pinned
environment. Four further cases exposed missing native BLAS underflow refusals;
they are now permanent regressions. The initial failures remain in the campaign
evidence. A pure Rust SVD without the pinned native library is not qualified.

The comparison follows the existing numerical tolerance contract, with exact
shape, metadata and rank/refusal decisions in the exercised cases. Floating
point last-bit differences can produce different covariance content identities;
both identities still commit their actual validated contents. The tests do not
prove equivalence over every possible binary64 input.

The implementation follows the pinned NumPy algorithms in its
[linear algebra wrappers](https://github.com/numpy/numpy/blob/v2.4.3/numpy/linalg/_linalg.py)
and [matrix multiplication dispatch](https://github.com/numpy/numpy/blob/v2.4.3/numpy/_core/src/umath/matmul.c.src).

## Exact host binding

Construct `RustNumericalBackend(binary, expected_sha256, *, process_policy,
lapack_libraries)` in trusted launcher code. `lapack_libraries` maps exactly three
absolute paths to independently selected SHA-256 values: one
`libscipy_openblas64_*`, one `libgfortran-*` and one `libquadmath-*`. Each invocation
rechecks the executable and these libraries, copies their exact bytes into a
private directory, and executes that snapshot under the selected `ProcessPolicy`.
Symlink files, set-ID files, changed bytes and unknown loader/environment variables
are refused. Paths, pins and policy are never MCP tool arguments.

Use `from_profile(profile, numerical_backend=backend)` for the normal control/MCP
path, or `numerical_workflow.execute(..., backend=backend)` for trusted in-process
composition. Existing principal and scientific grants are still required.
The backend cannot be attached to a non-builtin or read-only profile.

`native/odsp-core/mcp_launcher.py --profile PROFILE --binding BINDING` starts the
existing SDK stdio server with an explicit JSON binding. `--resume` uses existing
retention/resume semantics. The binding is a closed object:

```json
{
  "schema": "odsp.rust-numerical-host-binding.v1",
  "binary": "/approved/path/odsp-numerical-core",
  "binary_sha256": "<approved 64-character digest>",
  "lapack_libraries": {
    "/approved/path/libscipy_openblas64_<wheel>.so": "<approved digest>",
    "/approved/path/libgfortran-<wheel>.so": "<approved digest>",
    "/approved/path/libquadmath-<wheel>.so": "<approved digest>"
  },
  "process_policy": {
    "mode": "limits",
    "expected_prlimit_sha256": "<approved /usr/bin/prlimit digest>",
    "wall_seconds": 10,
    "cpu_seconds": 10,
    "memory_bytes": 268435456,
    "max_input_bytes": 524288,
    "max_output_bytes": 2097152,
    "file_bytes": 0
  }
}
```

This is a template, not a ready-to-run approval. Obtain pins from the reviewed
build/dependency manifest; do not automatically trust a replacement file by
rehashing it. The default explicit environment includes `OPENBLAS_NUM_THREADS=1`
and `OMP_NUM_THREADS=1`; only `PATH`, `LANG`, `LC_ALL` and those two thread settings
are accepted by this backend.

The runtime record commits the native binary, native source/build inputs,
Python boundary source files, library contents, ILP64 ABI, actual OpenBLAS
configuration/core/thread count and process-policy identity. Old records validate
offline without rerunning an old binary. This is a host declaration and content
binding, not remote attestation. System libc, libm, libgcc, the ELF loader, CPU and
kernel remain host dependencies; the three pinned libraries are not a complete
operating-system attestation. Qualify the complete deployment image separately.

Limits mode enforces wall/CPU/address-space/file/output bounds and owned process
group cleanup. It does not provide network or filesystem namespace isolation,
aggregate descendant memory accounting or a per-job cgroup. If namespace
isolation is required, explicitly select and qualify the existing Bubblewrap
policy; unavailable namespaces must remain held. Local qualification used limits
mode. Neither mode is proof of hardware or measurement traceability.

## Reproduce local qualification

Run from the repository root, with reviewed Rust 1.91.0 and the locked Python
environment. Put Cargo caches and targets on a filesystem with sufficient free
space. The Linux qualification used about 320 MiB of isolated Cargo inputs/build
outputs and about 30 MiB of temporary library snapshots per concurrent call.

```sh
export CARGO_HOME=/absolute/scratch/odsp-cargo-home
export CARGO_TARGET_DIR=/absolute/scratch/odsp-cargo-target
cargo build --release --locked --manifest-path native/odsp-core/Cargo.toml
cargo test --release --locked --offline --manifest-path native/odsp-core/Cargo.toml
export ODSP_RUST_NUMERICAL_BINARY="$CARGO_TARGET_DIR/release/odsp-numerical-core"
export ODSP_RUST_LAPACK_DIR=/absolute/pinned-environment/lib/python3.12/site-packages/numpy.libs
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:tests
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
python -m pytest -q tests/test_rust_numerical.py tests/test_rust_numerical_mcp.py --junitxml=/absolute/evidence/native.xml
python native/odsp-core/qualify_existing.py --binary "$ODSP_RUST_NUMERICAL_BINARY" --lapack-directory "$ODSP_RUST_LAPACK_DIR" --junit /absolute/evidence/existing-native.xml --receipt /absolute/evidence/existing-native.json
```

The environment variables select inputs for tests only; the production backend
does not discover or enable itself from them. Without explicit test inputs, native
tests skip and must not be reported as native qualification. The qualification
helper records measured local input hashes and disables Python computation while
running the existing suite; it does not generate a trusted deployment binding.

The current local checks passed 4 Rust unit tests, 74 native/MCP tests and all 67
existing numerical tests with Python computation disabled. They cover Decimal
covariance oracles, independent GLS equations, full covariance, exact SVD cutoff
decisions, maximum dimensions, malformed requests/results, byte commitments,
binary/library/source replacement, loader injection, output/time bounds,
retained/offline identity and real MCP discovery/admission/execution/resume for
all seven operations. Local synthetic fixtures establish supplied-model numerical
behavior only. No hosted, container, physical, SP1 or Kani qualification is claimed.

Independent boundary review additionally exercised 400 extreme/subnormal inputs
with no compute/refuse disagreement. It identified a mixed boolean/numeric child
covariance that could be coerced before validation, and missing required source
entries in offline runtime validation. Both defects were fixed and independently
rechecked; the exact mixed-matrix case is retained as a regression. The review did
not run sanitizers or independently rerun the MCP lifecycle.

## Dependency and license inventory

`native/odsp-core/Cargo.lock` pins registry checksums.
`native/odsp-core/dependencies.lock.json` inventories all 56 locked packages,
including 38 selected normal/build packages for Linux x86-64, their declared
licenses and registry archive digests. Optional locked packages are labeled as
unselected. Review with:

```sh
cargo metadata --locked --offline --manifest-path native/odsp-core/Cargo.toml --format-version 1
cargo tree --locked --offline --manifest-path native/odsp-core/Cargo.toml --target x86_64-unknown-linux-gnu --edges normal,build
```

New ODSP code is AGPL-3.0-or-later. Imported execution crates retain MIT notices;
registry packages retain their declared licenses. NumPy's native wheel libraries
have separate bundled notices, including OpenBLAS and GCC runtime terms. They are
host-supplied runtime inputs, not redistributed in this source patch. The inventory
is not legal clearance, and existing source/license holds remain in force.
