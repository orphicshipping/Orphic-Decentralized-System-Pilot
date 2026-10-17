# Polymer, chemistry and physics benchmarks

## Additive scope

This increment extends the thermochemical benchmark in PR173, without changing
its four files, the existing isothermal Catalyst/Cantera profile, dependency pins,
operation registry, workspace schemas, evidence admission, hardware authority or
licensing. It adds two bounded polymer-related mathematical models and one
shared module-level run/inspect/reproduce interface. It does not assert a general
molecular-to-manufacturing simulator or an experimentally qualified polymer.

The models are independently selected by host-allowlisted data schemas. Sharing
an interface does **not** couple their physical equations: molar-mass moments do
not determine the viscoelastic moduli, and the thermochemical model does not
supply cure conversion or temperature evolution to the viscoelastic model.
Those couplings require new parameter, closure, unit and validation contracts.

## 1. Uniaxial generalized-Maxwell model

`uniaxial-generalized-maxwell.v1` consists of an equilibrium spring and up to 32
parallel Maxwell modes. Each mode is a spring and dashpot in series. The user
prescribes a piecewise-linear strain history. With branch stresses q_i, positive
moduli E_i, positive relaxation times tau_i and E_infinity >= 0:

```text
dq_i/dt + q_i/tau_i = E_i * d(strain)/dt
stress = E_infinity * strain + sum(q_i)
stored_energy_density = E_infinity * strain^2 / 2 + sum(q_i^2 / (2 E_i))
d(work_density)/dt = stress * d(strain)/dt
d(dissipation_density)/dt = sum(q_i^2 / (E_i tau_i)) >= 0
```

The reported energy identity is `delta_work = delta_stored_energy + delta_loss`.
Work and dissipated energy are integrated independently; loss is not reconstructed
from an energy residual. Work may be negative during unloading, but cumulative
loss cannot decrease. An invalid negative computed loss refuses execution; it
is not silently clipped.

The stress solution for each linear segment is evaluated analytically in
binary64. Cancellation-sensitive short segments use expm1, a power series and
an independent integral of the squared stress polynomial. Longer segments use
a mean/variance exponential representation for the dissipation integral.
The short-segment polynomial is a numerical approximation, not a certified
interval bound. High-precision tests cover the switching neighborhood as well
as h/tau from 1e-18 to 1e15; passing those samples is not a universal error proof.

The supported initial condition is explicitly `instantaneous-elastic-at-t0`:
q_i(0) = E_i * strain(0). The initial strain is assumed to have been imposed
instantaneously before the observed relaxation begins. Its stored energy is
reported; preparatory work before t=0 is outside the cumulative work record.
Zero initial strain supports ramp/hold/unload and cyclic prescribed loading.
Arbitrary residual stresses, stress-controlled creep, plasticity, damage,
fracture, finite-strain kinematics and spatial boundary-value problems are not
included.

Frequency-domain output uses the convention exp(+i*omega*t), omega = 2*pi*f:

```text
storage_modulus = E_infinity + sum(E_i * (omega*tau_i)^2 / (1+(omega*tau_i)^2))
loss_modulus = sum(E_i * omega*tau_i / (1+(omega*tau_i)^2))
```

Frequency input is in Hz, not rad/s. Stresses and moduli are Pa; time is s;
strain is dimensionless; energies are J/m^3. Returned quantities remain separate,
not combined into a mixed-unit physical error score.

### Declared domain is not a material qualification

The numerical envelope accepts 2-2048 strain-history samples, 0-32 modes,
0-256 increasing frequency samples, times to 1e6 s, spacing >= 1e-9 s,
relaxation times 1e-9 to 1e9 s, and total instantaneous modulus 1e-3 to 1e12 Pa.
Input strain is bounded to +/-0.02. **That 2% ceiling is an input-budget choice,
not evidence that a real polymer remains linearly viscoelastic at 2% strain.**
The admissible physical range must be measured for the particular material and
conditions; it may be much smaller.

`temperature_k` is a 200-600 K parameter-condition label. The equations are
isothermal, and supplied moduli and relaxation times are assumed appropriate
for that temperature. No time-temperature shift, WLF/Arrhenius rheological
shift, thermal expansion, dissipation-to-temperature feedback, glass transition
or cure-dependent constitutive law is implemented. The example parameters are
synthetic and are not attributed to a resin grade.

## 2. Finite molar-mass population moments

`finite-molar-mass-population.v1` accepts 1-2048 declared bins, each containing
an identity, molar mass in kg/mol and nonnegative relative weight. The caller
must declare `weight_basis` as exactly `number` or `mass`. There is no silent
interpretation of mass fractions as number fractions, and no g/mol alias.
Molar masses are bounded to 1e-3 to 1e6 kg/mol; at least one weight must be positive.
Weights are relative, not automatically interpreted as experimentally measured
chain counts, sample masses or absolute number densities.

After explicit normalization, let n_i be number fractions and w_i mass fractions:

```text
M_n = sum(n_i * M_i)
w_i = n_i * M_i / M_n
M_w = sum(w_i * M_i)
M_z = sum(w_i * M_i^2) / M_w
molar_mass_dispersity = M_w / M_n
```

For mass-basis input, number fractions are recovered by normalizing w_i/M_i.
The result includes both fraction bases, all three molar-mass averages and the
molar-mass dispersity. No degree-of-polymerization dispersity is inferred: that
requires structural information about repeat units, copolymer composition and
end groups. Dispersity is not a diffusion coefficient.

For the equal-number two-bin example at 10 and 100 kg/mol, M_n = 55 kg/mol,
M_w approximately 91.81818 kg/mol, M_z approximately 99.10891 kg/mol and
molar-mass dispersity approximately 1.66942. Interpreting the same weights as
mass weights would instead give M_n approximately 18.18182 kg/mol and
M_w = 55 kg/mol. The distinction is tested explicitly.

Summation uses scaled weights and fsum. Any nonzero bin lost by underflow during
normalization or basis conversion causes refusal. Tests check moment ordering,
zero-weight bins, subdivision, permutation, scaling and basis round-trips.
This is neither a polymerization population-balance engine nor a conversion of
raw SEC/GPC signals to calibrated molar masses. Solvent interactions, branching,
tacticity, entanglements, topology, sequence and molecular simulation are not
inferred from these moments.

## 3. Shared benchmark execution and inspection

Use the existing PR173 model or either new model through the module entrypoint:

```powershell
$env:PYTHONPATH = "src"
python -m ciw.materials_bench run examples/polymer/viscoelastic-request.json --output new-viscoelastic.json
python -m ciw.materials_bench inspect new-viscoelastic.json --output new-inspection.json
python -m ciw.materials_bench reproduce new-viscoelastic.json --output new-reproduction.json
python -m ciw.materials_bench run examples/polymer/population-request.json --output new-population.json
python -m ciw.materials_bench run examples/thermochemical/request.json --output new-thermochemical.json
```

On POSIX use `PYTHONPATH=src` before each command. An installed package does not
need PYTHONPATH, but an installed-wheel qualification has not been performed for
this increment. All three runtime kernels use only the standard library; SciPy
is used only by optional independent tests.

`run` performs a fresh calculation. Legacy thermochemical report schema and
content/execution conventions are preserved. New polymer reports retain their
normalized request and digest, source-file closure digests, observed runtime
labels, results and a fresh execution UUID. Equal computational content may
have the same content digest and different execution occurrences.

`inspect` checks retained integrity, request binding, explicit types, source
identity consistency, dimensional shapes, authority restrictions, counters and
algebraic/energy balances. It creates a separate verification UUID and binds the
complete original report, including its execution occurrence, by digest.
It does not execute a time integrator or the thermochemical analytic trajectory
oracle. Population moments and elementary frequency-response algebra are
recomputed. A retained thermochemical analytic-pass claim is checked for shape
and applicability, **not re-established by running its oracle**.

An intentionally forged wrong-rate path can retain valid hashes and conservation
identities and therefore pass this limited inspection. `reproduce` performs a
fresh calculation and rejects a materially different trajectory. Its comparison
policies are retained per quantity: Pa for stress/modulus, J/m^3 for mechanical
energies, kg/mol for molar-mass moments and dimensionless fractions/dispersity.
The previous thermochemical dimensioned policy is preserved. Reproduction uses
the current kernel, not a downloaded historical implementation or an independent
solver, and is labeled accordingly. Source drift is exposed rather than hidden.

Content hashes establish byte/content consistency, not authorship or physical
truth. Runtime and execution labels are not authenticated. The CLI retains a
raw-input byte digest, but offline inspection cannot verify absent original
input bytes; `raw_request_bytes_verified` remains false. No signature, complete
environment attestation, evidence admission, uncertainty assessment or equipment
authority is created. No sensor evidence is fabricated to satisfy an operation
runner's source contract.

The interface extends the existing module-level benchmark path. It does **not**
add a `net` subcommand, native provider, operation registry, retained-workspace
format, or second evidence ledger. Approved operation/workspace integration is a
separate gate, requiring the real host's contracts and full regression checks.

### Boundary hardening

The shared interface accepts bounded UTF-8 JSON, with an 8 MiB byte limit,
depth <= 16, <=250000 nodes, object keys <=128 characters and strings <=1024
characters. Duplicate keys, non-finite values, unsupported schemas, numeric
booleans and unexpected fields are refused. The JSON-depth negative test
initially exposed interpreter-dependent acceptance; the explicit tree budgets
now make that contract independent of Python's recursion setting. Programmatic
cyclic and over-budget trees also refuse.

Output reuses PR173's exclusive hard-link publication after a complete temporary
write and flush. Existing files are never replaced; unsupported filesystems
refuse instead of falling back to unsafe overwrite. This is not a crash-resume
or directory-metadata durability guarantee. Stdout is a stream, not an atomic
retained-file transaction.

## 4. Observed verification and remaining gates

The focused source test command is:

```powershell
python -m pytest -q tests/test_thermochemical_benchmark.py tests/test_polymer_viscoelastic.py tests/test_polymer_population.py tests/test_materials_bench.py
```

On the local Linux/CPython 3.13.5 runtime, the final focused suite passed
**428 tests, zero failures, zero skips**:

| Component | Tests |
| --- | ---: |
| Unchanged thermochemical regression | 158 |
| Viscoelastic response and energy | 123 |
| Molar-mass population | 79 |
| Shared interface and retention checks | 68 |

This includes 40 independent SciPy comparisons: 24 thermochemical and 16
viscoelastic, using DOP853 and Radau with separately written right-hand sides.
The viscoelastic comparisons include stress, signed work and dissipated energy,
not just endpoints or conserved sums. Also included are 36 100-digit Decimal
segment comparisons, 40 60-digit Decimal population comparisons, and a maximum
32-mode/2048-sample execution with 65504 mode updates.

The first combined run had one failing new depth-budget test. Its explicit
input-budget fix passed the next run; three subsequent tests cover retained
quantity-specific reproduction policies. No existing thermochemical test,
physics equation or qualification threshold was relaxed.

Three isolated, compile-checked mutation experiments were also detected by the
existing tests: removing the Hz-to-angular-frequency factor, doubling the
relaxation clock, and treating mass fractions as number fractions. Each produced
one assertion failure, no collection/import error, and no skipped test. These
are three sensitivity checks, not exhaustive mutation coverage. The production
source was not mutated. All three models also completed run/inspect/reproduce
using `python -S`, with site-packages loading disabled.

Observed dependencies were pytest 9.0.2, NumPy 2.3.5 and SciPy 1.17.0.
NumPy and SciPy differ from the repository pins; this is **not** a pinned runtime
qualification. Without SciPy, independent solver tests skip and must not be
counted as passed. No claim is made for full-repository regression, installed
wheel behavior, Windows, Julia/Catalyst/Cantera provider execution for these
models, GPU execution, experimental validation or manufacturing readiness.
The temporal-roundoff work in PR172 was inspected as prior evidence, not rerun
or incorporated in this branch.

Next gates are native operation/workspace integration under approved source
contracts, pinned cross-platform source/wheel checks, and calibrated material
benchmarks with retained measurement uncertainty. DMA/relaxation data and
independently calibrated molar-mass distributions would address different
parameters; neither can substitute for the other's evidence.

## Scientific references

These are references for constitutive conventions and terminology, not claims
that external providers executed this code. No external implementation is copied.

- A. F. Bower, Applied Mechanics of Solids, section 3.6, linear viscoelasticity:
  https://solidmechanics.org/text/Chapter3_6/Chapter3_6.htm
- MOOSE, GeneralizedMaxwellModel, parallel Maxwell modes and constitutive context:
  https://mooseframework.inl.gov/source/materials/GeneralizedMaxwellModel.html
- IUPAC Gold Book, molar-mass dispersity, term 12224; mass-average molar mass,
  term 12217; definitions based on IUPAC Recommendations 2014:
  https://goldbook.iupac.org/terms/view/12224
  https://goldbook.iupac.org/terms/view/12217
- SciPy solve_ivp, DOP853/Radau methods and componentwise error tolerances:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html
- Existing thermochemical equations, bounds and scientific references remain in
  THERMOCHEMICAL_BENCHMARK.md.
