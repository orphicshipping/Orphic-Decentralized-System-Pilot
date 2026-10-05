# GROMACS — existing native provider and bounded qualification gate

Inspected and extended **2026-10-05**, from NET main
`0374076695475669188d61698e4656ced2263e01`.
**Native GROMACS execution for this increment: NOT RUN.**

## Retained single-point extension

The follow-on [runtime/artifact binding guide](GROMACS_RETENTION.md) documents
the opt-in `--runtime-binding` gate, exact native TPR/EDR/log retention,
per-stage/runtime bindings and provider-free offline receipt checks. It extends
the original PR #192 increment below. Native GROMACS remains **NOT RUN**;
source-slice tests do not close native or installed-package qualification.

## Source and existing implementation

The selected repository is
[notationsystems/GROMACS-Molecular-Simulation](https://github.com/notationsystems/GROMACS-Molecular-Simulation),
not the pasted URL with `this` appended. The selected repository is public and
archived; it remains unchanged. Inspected source:
`abc2dc98468993fdad6c670a75616c25c9b3e28b`, tree
`381700bb2f0518d5ab0af13df6367b27fa835afa`.
This identifies inspected source, **not the source of an executed binary**.
The upstream [README](https://github.com/notationsystems/GROMACS-Molecular-Simulation/blob/abc2dc98468993fdad6c670a75616c25c9b3e28b/README)
describes the CMake build and LGPL-2.1 distribution; preserve upstream licensing
and citation metadata. No upstream source is vendored by this increment.

NET already contains these providers in
[`execution/gromacs.py`](../instruments/execution/compute-runtime/execution/gromacs.py):

| Existing descriptor | Result scope |
| --- | --- |
| `ste.gromacs.single-point-energy.v1` | One Potential value in kJ/mol. |
| `ste.gromacs.trajectory-energy.v1` | Per-frame Potential energy series, not the complete molecular trajectory. |
| `ste.gromacs.energy-minimization.v1` | Energy series and final GRO structure; completion is not proof of convergence. |

The existing path is `ExecutionSpecification` -> `grompp` -> `mdrun` ->
`energy` -> `ExecutionResult` -> `SpecificationDispatcher`. The
[structure bridge](../instruments/execution/compute-runtime/structures/gromacs_bridge.py)
already maps structures to GRO inputs. Neither another dispatcher nor a second
scientific evidence store is needed. Numerical molecular simulation remains
inside the native GROMACS executable; Python handles existing orchestration and
checks. This change does not add a top-level `ciw.*` operation or a Workbench
source family.

## Increment delivered

The existing runner now checks exact workload headers and one well-formed version
line; rejects ambiguous descriptors, unsuccessful probes and runtime version
mismatches; fingerprints executable bytes before/after the version probe and
each computational stage; and accepts an optional operator-reviewed executable
SHA-256 pin. Checks apply to all three existing runner functions.

Energy results must have exactly two finite numeric columns, nonnegative,
strictly increasing times and the expected row count. Missing, non-ASCII,
non-finite, overflowing/underflowing or malformed output cannot mint a completed
computation. Accepted result bytes retain their existing encoding and therefore
the same output and computation commitment rules. A parse failure returns the
existing `FAULT_PARSE` halt with no output or computation identity.

Final minimized structures receive bounded shape/finite-coordinate checks for
standard fixed-width GRO output (three 8-character coordinate fields). This is
not a general variable-precision GRO parser, topology validation, convergence
check or material qualification. Parsed artifacts are limited to 4 MiB each.

Existing positional APIs and their historical default of `max_warnings=2` are
preserved. The new gate explicitly sets **`max_warnings=0`**, `cpu_only=True` and
one thread; it does not tolerate warnings or silently select a GPU. Applications
using the old defaults have not automatically acquired this stricter policy.

```python
result = run_gromacs_specification(
    specification,
    approved_gmx_path,
    expected_binary_sha256=reviewed_executable_sha256,
    max_warnings=0,
    cpu_only=True,
)
```

Runtime mismatches and process startup/time-out failures raise the existing
`EngineProtocolError`; the unchanged dispatcher can record a failed operation.
Nonzero computational stage exits remain halted results, not synthetic energies.
Executable drift detection is not a race-free attestation, dependency lock,
security sandbox or proof of scientific correctness. Shared libraries, drivers,
force-field includes and source-to-binary correspondence remain separately
unestablished. General subprocess output and temporary disk use are not an OS
resource sandbox. The narrowly defined gate uses no topology includes.

## Explicit native gate

[`scripts/check_gromacs.py`](../scripts/check_gromacs.py) invokes the existing
single-point runner; it is not a substitute simulator. With a provisioned,
reviewed executable, run from the NET checkout:

```sh
python scripts/check_gromacs.py \
  --gmx /absolute/path/to/approved/gmx_d \
  --expected-sha256 REPLACE_WITH_REVIEWED_64_CHARACTER_SHA256 \
  --output /absolute/new/outside-checkout/gromacs-evidence
```

The output directory must be new and outside the checkout. The expected digest
must come from an operator-reviewed build or artifact; copying the hash of an
unknown binary does not establish trust. No PATH search, installation, download,
checkpoint loading or force-field selection occurs automatically.

The gate evaluates three separations and repeats one case for the same-host
identity check. It declares a self-contained synthetic argon-like pair model:
`sigma=0.3401 nm`, `epsilon=0.978638 kJ/mol`, hard cutoff `1.0 nm`, no potential
shift, no dispersion correction, no charges, and a 3 nm periodic cubic box whose
images are outside the interaction range. The finite scalar reference evaluates
`4*epsilon*((sigma/r)^12-(sigma/r)^6)` below the cutoff. It is an independent
implementation of the same declared equations, **not experimental evidence**.

The fixed provisional comparison policy is absolute tolerance `2e-5 kJ/mol` and
relative tolerance `5e-6`. This is not propagated uncertainty or a calibrated
error bound. The [GROMACS parameter documentation](https://manual.gromacs.org/current/user-guide/mdp-options.html)
and [unit definitions](https://manual.gromacs.org/current/reference-manual/definitions.html)
are references for the explicit conventions, not evidence of a successful run.

The gate retains input/descriptor bytes, successful canonical result bytes,
existing specification/program/input/output/computation identities, separate
attempt/case/comparison IDs, the binary fingerprint/version and numerical
residuals. The binary-only invocation above does **not** retain native TPR/EDR
files or full process logs. Use the explicit [retained mode](GROMACS_RETENTION.md)
for that evidence; a genuine native run remains an open qualification gate.
A failed comparison retains the computed result and failure rather than replacing
it with the scalar reference. A repeated computation may have the same content
identity while its case and comparison IDs remain distinct.

| Gate exit | Meaning |
| --- | --- |
| 0 / `PASS` | The specified single-point cases and same-host repeat met the declared numerical policy. |
| 1 / `FAIL` | A bound attempt failed its pin, execution, parser, numerical or repeat check. |
| 2 / `NOT_RUN` | No complete explicit binding, or the specified executable is unavailable. |

Even a native pass does not qualify the full NET Session/Workbench path, GPU
execution, MD ensembles, force calculations, energy minimization, polymers,
MLIP checkpoints, training datasets, physical calibration or equipment control.

## Initial PR #192 execution evidence and remaining gates

The retained extension
[records its additional tests separately](GROMACS_RETENTION.md#executed-evidence-and-remaining-gates).

The provider-free boundary suite executed **107 tests, all passing**, using
Python 3.13.5 / pytest 9.0.2 on Linux. These exercised a reconstructed source
slice whose pristine runner, engine, specification and commitment files were
checked against their Git blob identities before editing. The slice did not
contain the eager package `__init__`/dispatcher import closure. These are
isolated-module tests with explicitly labelled process doubles, **not** a full
checkout, installed-package, actual Session/dispatcher or native GROMACS run.
Synthetic gate tests cover receipt retention and mismatch handling only.

Focused tests against the original runner reproduced acceptance of malformed
descriptors and non-finite energy output. The new
[CI definition](../.github/workflows/net-gromacs-boundary.yml) runs the boundary
suite with the actual repository package on Ubuntu and Windows and exercises
the no-binding gate. It is an executable test definition, not a passing receipt.
The native tests already in the repository still require their real binary;
the new provider-free CI job does not represent missing native tests as success.

Local native execution was blocked: no `gmx`/`gmx_d` executable was installed,
and a local GitHub clone attempt failed with DNS resolution unavailable. The
connector allowed source inspection but supplied no native runtime. A reviewed
executable and its dependency/build record are required to close that gate.

ALCHEMI PR #181 and retained-data PR #185 remain separate and unchanged. This
increment does not copy either branch or duplicate the parent's data bridges.
The ALCHEMI LJ fixture uses different sigma/epsilon and units; cross-provider
agreement requires explicitly aligned units, parameters, cutoff/shift rules,
periodicity and coordinate precision. No cross-provider agreement is claimed.
All values remain computations about a synthetic model. Physical qualification,
proof, uncertainty and equipment authority are not created by this integration.
