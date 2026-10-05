# Bounded external HOOMD particle studies

`particles.hoomd-md.v1` is an explicitly bound external HOOMD-blue CPU MD
provider. It uses the existing Session operation lifecycle and Workbench
`data-record` sources. It adds no solver implementation, state store, dynamic
plugin loader, or machine-control path. Listing the provider never runs it.

## Executable first profile

- Generic, **unqualified model specimens** represented as contiguous linear
  chains: 2–256 beads total, 1–16 chains, at most 64 beads per chain.
- Three-dimensional orthorhombic periodic boxes (sides at most 64 sigma), unit-mass beads, shifted WCA
  nonbonded interactions excluding directly bonded neighbors, harmonic bonds.
- NVT Langevin dynamics with explicit reduced `kT`, drag, timestep, a 16-bit
  seed, and explicitly seeded momentum initialization. Pressure is observed;
  no target pressure is accepted in this NVT profile.
- At most 100,000 steps per stage and 65 frames per stage, with complete sample
  periods. Timestep is at most 0.005 reduced time units. These are software
  bounds, not stability, convergence, or equilibration guarantees.
- Initial GSD, equilibration GSD, and production GSD remain authoritative native
  bytes with SHA-256 identities. Bounded base64 chunks are transport only;
  trajectories are never replaced by flattened JSON coordinates.
- Frame identities bind artifact hash, frame index, native timestep and topology.
  Validation checks finite data, native counts, exact topology, invariant box,
  initial-to-equilibration continuity, and equilibration-to-production continuity.
- Derived descriptors: potential and kinetic energy, pressure, average-chain
  radius of gyration, single-origin unwrapped particle MSD, and MSD/(6t).
  The last is explicitly **finite-lag diffusivity**, not an asymptotic fitted
  diffusion coefficient. It is null at the zero-lag frame. No drift removal,
  confidence interval, convergence test, or uncertainty estimate is invented.

Reduced units remain native. The study declares length, energy and mass SI
scales and their uncalibrated status. Observation projection explicitly applies
these scales, including tau = length × sqrt(mass/energy), through the existing
observation-stream and comparison contracts. Changing specimen representation,
potential, topology, or SI scales changes the projected specimen identity.

## Install and run

Use the official CPU conda-forge package recommended by
[HOOMD installation documentation](https://hoomd-blue.readthedocs.io/en/v7.2.0/installation.html).
The first qualified binary profile is Linux x86-64 / Python 3.12:

```sh
micromamba create -n hoomd-cpu -c conda-forge \
  python=3.12.14 hoomd=7.2.0=cpu_py312_h3353ea4_0 gsd=5.0.1 numpy=2.4.3 pip
python -m venv .venv-net
.venv-net/bin/python -m pip install '.[dev,particles]'
.venv-net/bin/net hoomd example --output study.json
.venv-net/bin/net hoomd run study.json \
  --python /absolute/path/to/hoomd-cpu/bin/python --output-dir results/chains
.venv-net/bin/net hoomd inspect results/chains/native-result.json
```

`run` is create-only and writes exact `.gsd` files, the native result, a retained
workspace, scalar observation datasets, a separate static reference dataset,
and four independent mathematical comparisons. The reference calculates
pair/bond energies, kinetic energy, virial pressure and the pair-distance
radius-of-gyration identity from GSD snapshots without calling HOOMD. The
absolute tolerances are 1e-10 reduced units converted to SI; relative tolerance
is 1e-10. Double-precision GSD preserves the coordinates for these comparisons.

A successful MD execution and a PASS of those static checks are distinct.
Neither demonstrates equilibration, predictive material accuracy, viscosity,
macroscopic rheology, manufacturing behavior, or safe control action.
The example's 100 equilibration and 200 production steps are a short software
qualification case, not a scientifically converged material study.

To compare with an independently supplied experiment or another model, retain
an observation stream with explicit matching quantity, units, clock and frame,
then use `net data compare`. No interpolation or alignment is silently inferred.
The built-in static reference is not independent experimental evidence.

## Runtime and source provenance

HOOMD [v7.2.0](https://github.com/glotzerlab/hoomd-blue/releases/tag/v7.2.0)
annotated tag `57e870be43a29d21ee0a09ca7a55e63c4e1b216b` resolves to source
commit `8578236c7ce99151f9ec4b707b2095227494674d`. The qualified conda archive is
`hoomd-7.2.0-cpu_py312_h3353ea4_0.conda`, SHA-256
`367bfd99fb28733b525df490fbe8d5f4263195efd8d72fff9c4a8aa714b0a301`.
Its recipe's official release source archive hash is
`c88f2652ee53b41fcb9253eb70b29c3329d6bc044f581761e29aaf60b72bc5c4`;
the packaging patch is `fix-python-site-dir.patch`.

The binary honestly reports `hoomd.version.git_sha1 == "unknown"`. NET retains
that value and the exact conda distribution metadata rather than replacing it
with the upstream source commit. Unknown-source binaries outside this exact
qualified distribution are refused. Source builds must report the pinned
source commit and need their own execution qualification; matching metadata
alone is not a test pass.

GSD is pinned to [5.0.1](https://github.com/glotzerlab/gsd/releases/tag/v5.0.1),
which fixes the topology group data-type defect encountered in 4.2.0. Older
files are not silently rewritten. HOOMD is BSD-3-Clause and GSD BSD-2-Clause;
both remain separately installed dependencies. No upstream implementation is
vendored, and NET's existing AGPL licence is unchanged.

Binding records actual Python/NumPy/GSD/HOOMD versions, CPU architecture,
compiler/build flags, installed Python/bytecode/native-module hashes, interpreter
hash, the fixed worker hash, NET bridge code hash, and the separate analysis environment. Changes refuse execution. These are provenance
and drift checks, not a sandbox or authentication of a compromised installation.
System shared libraries are not comprehensively hashed. The runtime binding is
process-local; saved files cannot supply a Python path, module or command.

## Retention, reopen and qualification

The optional `particles` extra installs only the GSD data reader. Saved records
can be reopened and checked without HOOMD installed or running. Replaying MD
requires explicitly binding `HoomdRuntime(python).operation()` to the Session
again. Replays have distinct execution identities; the gate compares descriptors
with atol=rtol=1e-10 on the same qualified CPU runtime. Cross-device bitwise
reproducibility is not claimed.

```sh
NET_HOOMD_PYTHON=/absolute/path/to/hoomd-cpu/bin/python \
  .venv-net/bin/python -m pytest -q tests/test_hoomd.py
.venv-net/bin/python scripts/check_hoomd.py \
  --python /absolute/path/to/hoomd-cpu/bin/python --output-dir results/hoomd-gate
```

The latter requires a clean installed wheel and fails on skipped native tests.
It produces a reopenable workspace, all GSD artifacts, JUnit results, independent
comparison records, and a scoped qualification receipt. The CI workflow
`HOOMD native CPU` runs that installed-wheel gate.

GPU execution, sweeps, rheological identification, zero-copy paths, HPMC, MPCD,
adaptive studies, branched topologies and arbitrary potentials are roadmap
work. Their upstream availability does not imply a NET-qualified route.
