# LAMMPS — bounded external tooling

Implemented candidate: 2026-10-05. Native CPU qualification is **NOT_RUN in the
local preparation environment**. Source/installed/hosted results must be reported
from their actual receipts, never inferred from this guide or workflow existence.

## Scope and source identities

Upstream is [lammps/lammps](https://github.com/lammps/lammps), the native C++
molecular-dynamics engine. The inspected stable source and native CI build target
is `8de817dd79bfe4525d5d39246a212d833e6dee07`. This reference is not an attestation
that a separately supplied binary was built from that source. Upstream licensing
and notices remain upstream; no LAMMPS code or executable is vendored in NET.

NET baseline: `0374076695475669188d61698e4656ced2263e01`. The existing provider
roadmap previously listed LAMMPS as planned; no matching implementation/PR was
found in the bounded reconciliation. GROMACS #192, ALCHEMI #181 and parent data
integration #185 remain separate and are not edited or required by this provider.

`execution.lammps.run_lammps_specification` implements one workload descriptor:

```
ste.lammps.lj-energy-forces.v1
```

It returns the **existing** `ExecutionResult` over an `ExecutionSpecification`.
The executable SHA-256 and exact reported version participate in PROGRAM identity.
Configuration and input bytes participate in the existing specification commitment.
No second dispatcher, operation registry, evidence pool or admission path is added.
This is a Compute Runtime provider, **not a newly registered top-level `net data`
family or Session operation**. The dedicated CLI is available from a NET checkout;
the root CIW wheel does not package the consolidated Compute Runtime source tree.

## Supported scientific envelope

The native input is generated from a strict data-only JSON configuration. It
accepts 2–32 synthetic argon-labelled atoms with unique IDs, explicit configuration
and frame identities, and finite Cartesian positions in angstrom within +/-100.
All atoms use the same fixed LJ parameters: epsilon = 0.0104 eV, sigma = 3.4 A,
cutoff = 8.5 A. Minimum separation is 0.75 sigma. A 0.001 A exclusion band around
the hard cutoff avoids an ambiguous force comparison at its discontinuity.

The generated script selects `units metal`, non-periodic fixed boundaries,
a +/-128 A box, `pair_style lj/cut`, no energy shift or tail correction, total
(not per-atom) energy, one process, one OpenMP thread and `run 0`. An NVE fix is
present but no integration step occurs. Output is one total energy and the
atom-ordered Cartesian force vectors. The native dump must retain the same IDs,
atom type, positions, box and step. Units are eV and eV/A.

This is **not an experimentally calibrated argon model or a polymer potential**.
Periodic cells, bonded/coarse-grained chains, arbitrary LAMMPS scripts, force-field
files, external plugins, GPU/MPI acceleration, dynamics, minimization, stress,
reactive potentials, ML checkpoints and training are outside this profile.
The input schema rejects extra keys instead of guessing their meanings.

Official semantics: [LJ pair style](https://docs.lammps.org/pair_lj.html),
[units](https://docs.lammps.org/units.html), [run](https://docs.lammps.org/run.html),
[write_dump](https://docs.lammps.org/write_dump.html),
[command options](https://docs.lammps.org/Run_options.html).

## Use from the existing NET checkout

Provision a reviewed LAMMPS executable separately. For Linux, upstream describes
[distribution and static binaries](https://docs.lammps.org/Install_linux.html).
Resolve any executable symlink first. Retain the source/build/dependency record
and review its SHA-256. A version banner alone is not a runtime binding.
NET does not download, install, discover, upgrade or replace a binary at run time.

```bash
python scripts/check_lammps.py example --output /tmp/lj-input.json

python scripts/check_lammps.py run \
  --input /tmp/lj-input.json \
  --lmp /absolute/path/to/lmp \
  --expected-sha256 REVIEWED_64_HEX_DIGEST \
  --output /tmp/net-lammps-run

python scripts/check_lammps.py qualify \
  --lmp /absolute/path/to/lmp \
  --expected-sha256 REVIEWED_64_HEX_DIGEST \
  --output /tmp/net-lammps-qualification
```

Output directories must be new and outside the checkout. `gate.json` contains
per-case capture locations, external manifest hashes, computation identities and
independent check records. The `qualify` command evaluates four pair separations
(repulsive, attractive, near-cutoff and outside-cutoff), one repeat and a three-atom
cluster. It checks energy and all force components, not just an energy sign.

```bash
python scripts/check_lammps.py verify \
  --capture /tmp/net-lammps-run/case-0 \
  --manifest-sha256 HASH_FROM_THE_ORIGINAL_GATE_RECEIPT

python scripts/check_lammps.py replay \
  --capture /tmp/net-lammps-run/case-0 \
  --manifest-sha256 HASH_FROM_THE_ORIGINAL_GATE_RECEIPT \
  --lmp /absolute/path/to/lmp \
  --expected-sha256 ORIGINAL_REVIEWED_BINARY_DIGEST \
  --output /tmp/net-lammps-replay
```

`verify` performs no process execution. It rechecks raw artifacts, policy and
identities, reconstructs the numerical result and recomputes the scalar comparison.
`replay` first verifies the original capture, then requires an explicit matching
binary binding and makes a fresh capture; original files remain untouched.
A changed engine is a new `run`, not a silent replay substitution.

Exit codes: 0 = the declared check passed, 1 = failure, 2 = no explicit available
native binding / NOT_RUN. Process-double test reports are not native qualification.
Do not replace the reviewed digest or relax tolerances merely to obtain exit 0.

## Existing dispatcher composition

The operator can supply this runner through the existing callback seam:

```python
from pathlib import Path
from uuid import uuid4
from execution.dispatcher import SpecificationDispatcher
from execution.lammps import run_lammps_specification

# spec_for and interpret are the existing workload owner's functions. interpret
# must preserve simulation provenance, units and the bounded model applicability.
dispatcher = SpecificationDispatcher(
    spec_for=spec_for,
    interpret=interpret,
    extracted_at=declared_extraction_time,
    runner=lambda spec: run_lammps_specification(
        spec, Path(bound_lmp_executable),
        artifact_directory=artifact_root / uuid4().hex,
    ),
)
```

This example does not invent a physical measurement. Admission remains owned by
the existing experiment/operation lifecycle. Parent #185 should retain exact
program/configuration/input and native artifact bytes, the external manifest
anchor, units/frame/atom IDs, and distinct capture, computation and verification
identities when adding an explicit typed-family route. No parent bridge is copied.

## Retention and trust boundaries

A capture keeps exact descriptor/configuration/input bytes, generated native
script, full help stdout/stderr, run stdout/stderr, LAMMPS log, energy text,
atom/coordinate/force dump and canonical numerical result. Before/after executable
hashes detect binary drift. Input files are compared before and after execution.
Artifacts must be regular non-symlink/non-hard-linked files, individually <=4 MiB
and collectively <=16 MiB. Native calls have a bounded deadline. These are adapter
limits, not a hostile-process sandbox or hard real-time guarantee.

The separate capture manifest binds all retained bytes and result identities.
Its SHA-256 must be anchored outside the capture before later verification; a
mutable file next to the artifacts is not an independent trust anchor. Hashes
establish integrity relative to that anchor, not authenticated origin or physical
truth. Same-host repeat equality is checked but cross-platform bitwise identity
is not promised. Shared-library/driver closure and source-to-binary attestation
remain explicitly unestablished. The native CI retains build logs and a dynamic
library listing; that listing is not a cryptographic dependency closure.

Malformed output or a process failure cannot become a completed computation.
Warnings/errors require review. A well-formed but numerically wrong native result
is **retained with a failed comparison**, never replaced by the scalar reference.
The scalar checker uses independent all-pairs equations, not LAMMPS output as its
own target. This establishes implementation agreement for the same model only.
Policy: energy/force absolute tolerances 1e-8 in their declared units, relative
1e-6. They are provisional numerical tolerances, not calibrated uncertainty.

Capture and verification IDs are fresh; equal scientific computations may retain
equal output/computation commitments. `physical_validation=not_performed`,
`uncertainty=unavailable`, and `equipment_authority=false` remain explicit.
No comparison is promoted to a computation proof or production qualification.

## Tests and remaining gates

`tests/test_lammps_boundary.py` contains provider-free input/reference, tamper,
process-failure, bounded actual subprocess-transport and CLI lifecycle tests.
Synthetic process doubles are labelled. `test_lammps_dispatcher.py` requires the
actual package and checks the existing dispatcher consumes the result type and
refuses halted computations; it does not exercise physical admission.

`.github/workflows/net-lammps.yml` defines full-package Linux/Windows contracts
and a separate real native CPU job built from the exact upstream commit above.
A missing native toolchain or failed numerical check must fail that native job;
there is no substitute implementation or passing skip. Neither job's definition
nor this source delivery is evidence of a successful run. Local preparation used
a verified three-module source slice because direct GitHub downloads failed;
full-package, installed-boundary and native results require their own receipts.
