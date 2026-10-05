# NVIDIA ALCHEMI Toolkit — external provider target

Added to the NET external-tool inventory: **2026-10-05**.

**Status: isolated CPU Lennard-Jones qualification binding implemented; broader
model and physical qualification remain pending.** The inventory does not install
dependencies, bind a runtime, register a default Workbench operation, start a
training job, or grant equipment authority. The explicit local binding and gate
below exercise one classical single-point profile. Existing ecosystem inventories
and research-test commentary do not establish any broader callable integration.

## Upstream identity and inspected scope

| Item | Record |
| --- | --- |
| Name | NVIDIA ALCHEMI Toolkit |
| Package / import | `nvalchemi-toolkit` / `nvalchemi` |
| Official documentation | <https://nvidia.github.io/nvalchemi-toolkit/> |
| Upstream repository | <https://github.com/NVIDIA/nvalchemi-toolkit> |
| Inspected upstream source | `bf605f9191b2978fc865f4286d11e4481b679999`; documentation reference, **not** a qualified NET execution pin |
| Published documentation observed | Version label `0.2.0`; live documentation may differ from a source checkout |
| Upstream license | Apache-2.0; retain upstream notices. Model weights, datasets and optional dependencies require their own license review. |
| NET role | Optional atomistic simulation, interatomic-potential evaluation and future model-training provider |
| NET runtime qualification | Explicit CPU LJ gate below; no GPU, learned-model, dynamics or physical qualification |

The [inspected upstream README](https://github.com/NVIDIA/nvalchemi-toolkit/blob/bf605f9191b2978fc865f4286d11e4481b679999/README.md)
describes batched molecular dynamics and geometry relaxation, machine-learned
interatomic potential (MLIP) evaluation, training/fine-tuning, GPU-resident atomic
data, Zarr-backed data handling and multi-GPU workflows. These are upstream
capabilities, not claims of NET implementation or polymer validation.

The [model documentation](https://nvidia.github.io/nvalchemi-toolkit/models/index.html)
identifies wrappers for MACE, AIMNet2 and UMA, and classical/correction models
including Lennard-Jones, Ewald/PME and DFT-D3. Their supported inputs, outputs,
periodicity and applicability differ. A wrapper's existence is not evidence
that a particular checkpoint covers a polymer, charge state or process regime.

## Architectural placement

```text
NET evidence, operation, execution and verification contracts
  -> explicitly bound, isolated ALCHEMI worker (bounded CPU LJ slice)
  -> selected model and nvalchemi-toolkit / toolkit-ops dependencies
  -> retained atomic results and trajectories
  -> independent NET review and downstream scientific computation
```

ALCHEMI is Python-facing scientific software using GPU-oriented computational
components. Scientific computing and Python are not mutually exclusive.
Keep the provider's native environment behind a versioned process/artifact
boundary; do not rewrite it in Julia merely for language uniformity, or make it
the owner of NET state. Existing Julia, Rust/C++, analytical and numerical
providers remain independent and unchanged.

Its natural scope is atomic positions, elements, neighbor relationships and
periodic cells. It is not a replacement for the proposed general discrete-domain
runtime, a lattice-Boltzmann solver, continuum polymer-flow computation, or a
qualified injection-molding model. Atomistic-to-continuum property/closure
transfer requires its own assumptions, scale mapping and validation evidence.

## Bounded implementation sequence (not registered operations)

| Stage | Proposed scope | Gate before promotion |
| --- | --- | --- |
| 1 | Single-configuration energy and forces; stress only where supported | Pin one model/environment; validate input/output semantics; retain independent reference and refusal checks. |
| 2 | Bounded geometry relaxation | Retain convergence criteria, all failures, final residual forces and model applicability; convergence alone does not prove physical correctness. |
| 3 | Short, specified-ensemble molecular-dynamics trajectory | Verify timestep sensitivity and ensemble-appropriate diagnostics; test checkpoint/replay and preserve stochastic state. |
| 4 | Private training or fine-tuning | Authorized datasets and weights, frozen dataset manifests, leakage-resistant splits, held-out reference evaluation and separate checkpoint promotion. |

The bounded worker accepts `nvalchemi.lj-single-point.v1` through the existing
`ciw.adapter-request.v1` / `ciw.adapter-response.v1` transport. It is not added to
the default operation registry. Workbench promotion must reuse existing operation
registration and retained-workflow mechanisms, not introduce a parallel registry.

## Reproducible CPU single-point gate

`ciw.adapters.nvalchemi.NvalchemiLJBinding` reuses the existing pinned subprocess
boundary. It requires an explicit clean NET source commit and a separately
provisioned interpreter. The lock in `runtimes/nvalchemi-lj/environment.json`
records Python/platform, all installed distribution versions and the downloaded
NVIDIA/PyTorch/Warp wheel hashes. At binding and invocation the adapter checks
source bytes, interpreter, versions and installed files of the toolkit, toolkit-ops,
PyTorch, Warp and NumPy. These checks detect drift; they do not authenticate an
operator's initial environment or constitute an OS sandbox.

The profile uses `nvalchemi-toolkit==0.2.0`, `nvalchemi-toolkit-ops==0.4.1`,
`torch==2.10.0+cpu`, `warp-lang==1.18.0` and `numpy==2.3.5`, with the complete
distribution lock in `runtimes/nvalchemi-lj/requirements-cpu.txt`. Keep this
environment separate: NET's base NumPy pin is not the provider's NumPy pin.
The lock currently targets Python 3.12 on Linux x86-64; match its exact recorded
patch version when binding. NVIDIA documents CPU support through Warp in its
[hardware FAQ](https://nvidia.github.io/nvalchemi-toolkit/userguide/about/faq.html).
This profile requires neither a GPU nor a learned checkpoint or account.

```sh
python3.12 -m venv ../nvalchemi-cpu
../nvalchemi-cpu/bin/python -m pip install 'torch==2.10.0+cpu' --index-url https://download.pytorch.org/whl/cpu
../nvalchemi-cpu/bin/python -m pip install -r runtimes/nvalchemi-lj/requirements-cpu.txt
../nvalchemi-cpu/bin/python -m pip check
PYTHONPATH=src .venv/bin/python scripts/check_nvalchemi.py \
  --python ../nvalchemi-cpu/bin/python --revision "$(git rev-parse HEAD)" \
  --output results/nvalchemi-cpu-qualification.json
```

Only neutral, nonperiodic Z=18 fixtures with 2–32 ordered atoms are accepted.
The declared Cartesian coordinates are in angstrom, energies in eV, and forces
in eV/angstrom. The fixed unshifted Lennard-Jones parameters are epsilon 0.0104 eV,
sigma 3.4 angstrom, cutoff 8.5 angstrom and zero switch width. The complete directed
neighbor matrix is constructed locally and passed with `half_list=False` to the
actual NVIDIA `LennardJonesModelWrapper`; energies and forces come from its Warp
kernel, not NET's independent reference. No model weights are fetched.

The gate compares repulsive/attractive pairs, the zero crossing, equilibrium,
cutoff exclusion, and three-atom, translated, rotated and permuted configurations
against an independent analytic pair sum. It also checks force/energy finite
differences, fresh-process replay, JSON reopening and refusal cases. Every worker
uses a fresh temporary Warp compilation cache. Reports retain raw configurations,
outputs, separate execution/result identities, the runtime identity, numerical
errors and explicit tolerances. Offline unit tests use synthetic reference values
and cannot establish that the vendor was run. Run them with
`python -m pytest -q tests/test_nvalchemi.py`; the real gate needs the explicit
binding above (or `CIW_NVALCHEMI_PYTHON` and `CIW_NVALCHEMI_REVISION` for its
opt-in integration test).

Successful comparison qualifies only this bounded CPU software/numerical path.
The synthetic Z=18 parameters are not a calibrated argon material model. Periodic
cells, stress, charge/spin, different chemistry, other models, molecular dynamics,
training, GPU execution and continuum/property transfer remain outside this gate.

## Required adapter and evidence contract

Retain separate evidence, operation, execution, prediction/result and verification
identities. Bind toolkit and toolkit-ops versions/commits, interpreter and
dependency lock, model architecture and checkpoint hashes, dataset provenance,
GPU/driver/runtime details, precision and random seeds. Model downloads must be
explicit and must not occur silently during replay. No remote data or checkpoint
upload is authorized by this inventory entry.

Inputs must declare atom identities, element numbers, positions, coordinate
frame, units, periodic cell and boundary flags, and model-required charge/spin
or per-atom attributes. Reject unsupported chemistry, malformed/non-finite
inputs, singular periodic cells and missing required outputs. Preserve atom
ordering and any explicit reorder map through batching and unbatching.

Outputs must distinguish model-predicted energies, forces and optional stresses
from measurements. Record energy reference conventions, stress/virial sign and
normalization, units and shape. Uncertainty that a model does not supply remains
**unavailable**, not zero. Retain raw trajectories, model failures and diagnostics;
clamping or other interventions must be explicit and cannot turn an invalid run
into a qualified physical result.

The [upstream installation guide](https://nvidia.github.io/nvalchemi-toolkit/userguide/about/install.html)
describes mutually exclusive CUDA extras and a separate environment for the UMA
extra. Select and lock a compatible stack; do not add all extras to the base NET
installation. Compatibility, numerical tolerance, capacity and performance on
the intended host remain untested.

## Qualification remains open

Independent checks should cover an analytic pair-potential fixture, force versus
energy finite differences where applicable, atom permutation, frame transforms
and periodic-boundary handling, with declared tolerances. Transform the complete
physical problem, including cell or external fields, when testing symmetry.

For dynamics, use energy-drift checks for an appropriate isolated NVE case;
thermostatted or barostatted trajectories require their own ensemble diagnostics.
Test timestep refinement, non-finite outputs, missing checkpoints, unsupported
model capabilities, interrupted runs, save/reopen and fresh replay. GPU replay
requires a measured numerical policy, not an assumed bitwise guarantee.

A same-model finite-difference or replay check establishes internal consistency,
not accuracy against nature. Qualified chemistry needs held-out independent
reference calculations and/or experiments. Surrogate-generated trajectories
retain synthetic/model provenance and must not become independent ground truth
for the model that generated them. Polymer property, manufacturing release and
machine-control qualification are separate gates.

See [systems catalog](SYSTEMS_CATALOG.md), [integration coverage](INTEGRATION_COVERAGE.md)
and [model validity and evidence](MODEL_VALIDITY_AND_EVIDENCE.md) for NET's existing
status and evidence boundaries.
