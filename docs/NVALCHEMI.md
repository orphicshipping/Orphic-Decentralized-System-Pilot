# NVIDIA ALCHEMI Toolkit — external provider target

Added to the NET external-tool inventory: **2026-10-05**.

**Status: optional bounded LJ energy/force adapter source implemented; native
ALCHEMI CPU/CUDA qualification remains pending.** See the [pilot operator and
validation guide](NVALCHEMI_PILOT.md). This extends the original documentation-only
inventory with an explicitly bound worker and provider-free contract tests.
No dependency is installed or provider bound by default. MLIPs, relaxation,
dynamics and training remain future profiles; no equipment authority is granted.

## Upstream identity and inspected scope

| Item | Record |
| --- | --- |
| Name | NVIDIA ALCHEMI Toolkit |
| Package / import | `nvalchemi-toolkit` / `nvalchemi` |
| Official documentation | <https://nvidia.github.io/nvalchemi-toolkit/> |
| Upstream repository | <https://github.com/NVIDIA/nvalchemi-toolkit> |
| Inspected upstream source | `bf605f9191b2978fc865f4286d11e4481b679999`; required pilot source revision; **not** a native qualification receipt |
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
  -> explicitly bound, isolated ALCHEMI worker (bounded LJ pilot; other profiles pending)
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

## Bounded implementation sequence

| Stage | Proposed scope | Gate before promotion |
| --- | --- | --- |
| 1 | `atomic.lj-energy-forces.v1`: synthetic non-periodic LJ energy/force pilot | Adapter, scalar checks and offline schemas implemented; operator binding and native qualification required. Stress and MLIP checkpoints remain unsupported. |
| 2 | Bounded geometry relaxation | Retain convergence criteria, all failures, final residual forces and model applicability; convergence alone does not prove physical correctness. |
| 3 | Short, specified-ensemble molecular-dynamics trajectory | Verify timestep sensitivity and ensemble-appropriate diagnostics; test checkpoint/replay and preserve stochastic state. |
| 4 | Private training or fine-tuning | Authorized datasets and weights, frozen dataset manifests, leakage-resistant splits, held-out reference evaluation and separate checkpoint promotion. |

The pilot reuses `PinnedSubprocessAdapter`, `OperationRegistry`, the existing
execution/result envelopes and Session save/reopen. Its backend operation and
offline schema are registered only by trusted setup, never by saved files.
No parallel execution registry or default provider binding is introduced.

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
