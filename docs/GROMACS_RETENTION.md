# GROMACS single-point runtime and native-artifact binding

**2026-10-05 implementation extension to draft PR #192**, starting at
`bfb254204e91d37e38cd6b6810333e73cc5ea921`. NET main inspected at
`0374076695475669188d61698e4656ced2263e01`.
**Native GROMACS execution remains NOT RUN.** Provider-free tests and a
subprocess transport double are not native numerical qualification.

This extends `run_gromacs_specification` in the existing
[runner](../instruments/execution/compute-runtime/execution/gromacs.py).
The [artifact helper](../instruments/execution/compute-runtime/execution/gromacs_artifacts.py)
is called inside that runner. It is not another molecular simulator, dispatcher,
registry, Workbench data bridge or evidence-admission route. `ExecutionResult`,
its existing byte encoding and the core commitment functions are unchanged.
Trajectory and minimization calls do not acquire this opt-in single-point mode.

## Explicit operator binding

A reviewed JSON manifest has the following structure. Placeholder hashes below
are intentionally invalid and must be replaced with independently reviewed
64-character lowercase SHA-256 values; this example cannot execute as written.

```json
{
  "schema": "ste.gromacs-runtime-binding.v1",
  "version_line": "GROMACS version: REPLACE_WITH_EXACT_REVIEWED_VERSION_LINE",
  "executable": {
    "path": "/absolute/approved/install/bin/gmx_d",
    "sha256": "REPLACE_WITH_REVIEWED_EXECUTABLE_SHA256"
  },
  "build_record": {
    "path": "/absolute/approved/build-record.json",
    "sha256": "REPLACE_WITH_REVIEWED_BUILD_RECORD_SHA256"
  },
  "dependencies": [
    {
      "path": "/absolute/approved/install/lib/libgromacs_d.so",
      "sha256": "REPLACE_WITH_REVIEWED_LIBRARY_SHA256"
    }
  ]
}
```

Use actual regular files, not symlinks; supply resolved paths after review.
Record source revision, compiler, CMake settings, precision/SIMD mode and library
inventory in the build record as applicable. The record's bytes are retained,
not interpreted as an authenticated build attestation. Up to 32 declared
runtime dependencies are supported. An empty list does not establish that a
binary is statically linked. This implementation checks **declared files**, not
completeness of the operating system's loader or dynamically loaded dependencies.

The executable, build record and declared dependency files are rehashed before
and after every stage, including the version probe. The canonical manifest hash
is also included as an explicit `runtime-sha256:` line in the single-point
program descriptor, so it participates in the existing program/specification
commitments. A bound descriptor cannot silently run in unbound mode.

Invoke the existing native gate:

```sh
python scripts/check_gromacs.py \
  --runtime-binding /absolute/reviewed-gromacs-runtime.json \
  --output /absolute/new/outside-checkout/gromacs-evidence
```

The manifest is **operator configuration**, not a binding loaded from a saved
receipt. No binary discovery, install, download, training or equipment operation
is performed. The output directory must be new and outside the checkout.
Conflicting `--gmx` or `--expected-sha256` arguments refuse.

The older binary-only gate invocation remains supported for compatibility, but
reports `runtime_artifact_binding: not_requested` and does **not** establish this
new milestone. Use `--runtime-binding` to require native artifacts.

## Retained execution contract

Bound mode requires CPU execution, one thread and `max_warnings=0`. It refuses
topology preprocessor directives, external file/include MDP options, duplicate
settings, nonzero steps and non-cutoff interaction methods. Its bounded MDP
vocabulary is intentionally narrower than general GROMACS. It invokes the same
`grompp -> mdrun -> energy` pipeline, requesting `effective.mdp` and
`processed.top` from `grompp` in addition to the original TPR.

Each `case-N/native` retains:

| Evidence | Files and bindings |
| --- | --- |
| Request | Exact descriptor, MDP, GRO, self-contained topology and hashed started-attempt record. |
| Runtime | Canonical runtime manifest, exact build-record bytes, full version stdout/stderr, executable/dependency digests. Executables and libraries are not copied. |
| Preprocessing | `run.tpr`, `effective.mdp`, `processed.top`, argv, stdin and raw stdout/stderr. |
| Computation | `run.edr`, `run.log`, argv, stdin and raw stdout/stderr. |
| Observable | `energy.xvg`, exact `Potential` selection bytes and raw stdout/stderr. |
| Result | Existing canonical `result.bin`, specification/program/input/output/computation identities and separate per-invocation execution identity. |

Each stage records hashes of what it consumes and produces. Request and
intermediate bytes are checked against retained bytes before and after use;
missing, changed, empty or oversized required outputs cannot become completion.
Partial logs and available artifacts are retained on ordinary stage failures.
A final `receipt.json` is written last, with an integrity hash over metadata and
artifact hashes. A started directory without a final receipt is **incomplete**;
there is no crash-recovery or resume claim. Existing directories/files are not
overwritten. Writes are not claimed to be fsync-backed durable transactions.

Requests are limited to 1 MiB per component; retained artifacts to 16 MiB each
and 64 MiB total; build-record bytes to 1 MiB. Existing energy-text parsing also
retains its stricter 4 MiB limit. These bound retained data, not process memory,
temporary disk consumption or runtime-file hashing cost. Subprocess execution
is trusted and time-bounded, **not an OS sandbox**. The minimalist environment
removes inherited loader overrides and GROMACS configuration overrides; it is
not a complete dependency lock or a race-free execution attestation.

## Matched reference and offline validation

The gate keeps the existing three synthetic pair separations, plus a repeat.
Its scalar reference now binds to the **actual submitted GRO coordinates and
exact topology/MDP bytes**, rather than only a nominal loop distance. Changed
geometry, units/box convention, force-field parameters or settings refuse the
reference comparison. The model remains sigma `0.3401 nm`, epsilon
`0.978638 kJ/mol`, unshifted cutoff `1.0 nm`, zero charges, no dispersion
correction and a `3 nm` periodic box with no interacting images for these cases.
This is not yet the different ALCHEMI #181 fixture.

A comparison has its own identity and binds specification, input digest and
computed output/computation identities. A failed comparison keeps the computed
value. No reference value substitutes for a failed provider. The fixed
`2e-5 kJ/mol` absolute and `5e-6` relative tolerances remain provisional numeric
policy, not propagated covariance or physical accuracy bounds.

```python
from pathlib import Path
from execution.gromacs_artifacts import validate_receipt

receipt = validate_receipt(Path("/retained/case-0/native"))
```

This checks hashes, exact manifest/descriptor and stage bindings, fixed argv and
policy, raw output correspondence, time-zero single-point semantics and the
existing core execution commitments. It does **not** open executable/library
paths in the manifest or run GROMACS. Receipts can be moved and inspected after
the runtime files are unavailable. A resealed semantically invalid receipt is
rejected; a wholly forged but self-consistent record is not authenticated by
hashes. This is not a cryptographic computation proof.

## Executed evidence and remaining gates

On Linux / Python 3.13.5, the original 107 provider-free boundary tests and 76
new binding/artifact tests passed together: **183 passed, zero failed/skipped**.
The added coverage includes four real subprocess launches running an explicitly
labelled script double, not GROMACS. Other tests use explicit process doubles.
The real subprocess fixture is POSIX-only; Windows reports that fixture skipped,
not native qualification. An offline time-zero tamper defect was reproduced and
fixed before the final passing run.

Local execution used a reconstructed source slice. Original PR #192 runner,
engine, specification, commitments, old tests, gate and CI file were checked
against Git blob identities. The eager package `__init__`/dispatcher dependency
closure and a full checkout were not present; these tests do not qualify the
installed package or NET Session. The updated
[workflow](../.github/workflows/net-gromacs-boundary.yml) requests both suites on
the actual repository package, but its presence does not report a passing run.

Fresh native attempts found no `gmx` or `gmx_d`; local GitHub cloning failed
with `Could not resolve host: github.com`. No native binary was downloaded or
executed. **A reviewed runtime manifest and a real native gate PASS are still
required**. Full-package/dispatcher/installed checks and host-specific CPU
qualification remain open; GPU, trajectory, force/stress, minimization and
polymer/physical validation are not established by this change.

ALCHEMI #181 and parent data integration #185 are untouched. In particular,
`src/ciw/data_compute_bridge.py` and the shared Workbench family contracts remain
with #185. This receipt is a lower-runtime artifact; no automatic new Workbench
family is claimed. No private model, data corpus, checkpoint, machine authority,
physical admission, merge or deployment is part of this increment.
