# Polymer reference rheology and independent scientific audit

This additive, data-only capability is available through `net polymer rheology`.
It uses NET's existing content encoder, seals, create-only publication and
separate evidence, execution, result and verification identities. It does not
create another ledger or grant hardware, provider, or state-admission authority.
The existing polymer-cycle workflow, leakage workflow and thermochemical
benchmark remain separate and unchanged in scientific scope.

## Constitutive model

The explicitly one-dimensional, isothermal generalized Maxwell model is:

```
E(t) = E_infinity + sum_i E_i exp(-t/tau_i)
sigma(t) = epsilon_0 E(t)
E_storage(omega) = E_infinity + sum_i E_i (omega tau_i)^2 / [1 + (omega tau_i)^2]
E_loss(omega) = sum_i E_i (omega tau_i) / [1 + (omega tau_i)^2]
```

The time experiment is an ideal imposed strain step at t=0, held thereafter.
The zero sample is the right-hand limit after that step. Harmonic responses are
steady-state responses of the same linear model, not a simulated startup sine
experiment. Frequency is **angular frequency in rad/s**, never implicit Hz.
These are tensile/uniaxial moduli, not a conversion from shear moduli or a
three-dimensional constitutive law.

For the step response, energy quantities per unit volume are:

```
Psi(t) = epsilon_0^2 / 2 * [E_infinity + sum_i E_i exp(-2t/tau_i)]
D(t) = epsilon_0^2 / 2 * sum_i E_i [1 - exp(-2t/tau_i)]
D_dot(t) = epsilon_0^2 * sum_i E_i / tau_i exp(-2t/tau_i)
Psi(t) + D(t) = Psi(0+)
```

D is dissipation after loading, not the loading impulse work. The passive
parameterization requires positive branch moduli and relaxation times, with
nonnegative equilibrium stiffness. Energy density has units J/m^3 and
dissipation rate W/m^3. The kernel does not integrate a temperature rise from
this dissipation and does not couple it to the thermochemical benchmark.

## Boundary and numerical implementation

Requests require exact fields and a declared parameter-source content reference.
That reference is a binding declaration, not authentication, calibration or
proof that a material exists. `synthetic` and `caller_declared` are the only
source kinds. The supplied temperature labels the fixed parameter set; there
is no temperature interpolation, Arrhenius/WLF shifting or inferred temperature
dependence. Missing uncertainty is explicitly **not propagated**, not assigned
zero experimental variance.

The supported numerical envelope is 0-32 modes; 2-256 increasing time samples
starting at zero; 1-256 increasing angular-frequency samples; at most 16,384
mode-sample evaluations. Positive times/frequencies are at least 1e-12, time is
at most 1e9 s, and angular frequency at most 1e12 rad/s. Branch stiffness is
1e-6 to 1e12 Pa and relaxation time 1e-9 to 1e9 s. Equilibrium stiffness is
0-1e12 Pa. An empty branch list requires positive equilibrium stiffness.
Strain is zero or magnitude 1e-12 to 0.02; temperature is 200-600 K. These are
computational bounds, not experimentally validated material domains.

The production kernel uses binary64 arithmetic, fsum, expm1 and reciprocal
frequency expressions. At large decay exponents it splits the exponential
before applying the stiffness/rate factor. This prevents an intermediate exp
from underflowing to zero while the correctly scaled response remains
representable. No negative output is clipped into a passive result.

## Verification, inspection and provenance

`analyze` creates a sealed response with a fresh execution and result identity,
normalized request evidence identity, exact work accounting and runtime source
hashes. It remains `not_verified` even after a separate audit is performed.

`inspect` verifies structure, field types, source binding, work counts and seals
without executing a numerical model. It does **not** establish numerical truth.
`verify` creates a fresh verification receipt using a separately implemented
80-digit Decimal oracle. The oracle does not call the production kernel, its
exp/expm1 implementation, or its reciprocal frequency code. It compares every
retained response component, not merely conservation or summary metrics.

Comparison uses relative tolerance 2e-12 with an eight-ULP binary64 allowance,
including the underflow fringe. Analytically exact zero responses (zero loading,
initial dissipation, zero-frequency loss and purely elastic dissipation) remain
exact. There is no engineering-scale absolute tolerance floor. This is numerical
cross-checking, not interval certification, uncertainty coverage or physical
validation. Both implementations share the declared constitutive assumptions;
independent arithmetic cannot establish their appropriateness for a real resin.

## CLI

With the containing package installed:

```sh
net polymer rheology example --output request.json
net polymer rheology analyze request.json --output result.json
net polymer rheology inspect result.json
net polymer rheology verify result.json --output verification.json
```

All output paths are create-only, through the existing atomic `save_new` helper.
Existing files and symlinks are not overwritten. Inputs use NET's bounded strict
JSON loader, including duplicate-key refusal. These commands are an explicit
analytical CLI; this increment does not register a Session operation or grant an
AgentHost/native-provider capability. Runtime hashes are provenance observations,
not signatures or environment attestation.

## Cooling precision repair

The existing lumped cooling model used `(1 - exp(-elapsed/tau))*u_ambient` for
its boundary-temperature sensitivity. For valid synthetic declarations with
tau=1e17 s and elapsed=1 s, this rounded to zero instead of about 2e-18 K for
u_ambient=0.2 K. The production calculation and separately written cooling
oracle now use `-expm1(-elapsed/tau)` and `-expm1(-rate*elapsed)` respectively.
The model, Biot gate, authority flags and uncertainty profile are unchanged.
Eight regression cases compare this contribution against 70-digit Decimal
arithmetic; seven positive-time cases failed before the repair. This is a small
numerical-representation defect, not evidence of material-scale temperature error.

## Test and qualification scope

The new candidate source-overlay run passed 182 focused tests: 96 rheology,
8 cooling-precision and 78 added thermochemical audits, with no skips. The
separately retained PR172 overlay passed its 22 portability tests; these are
a distinct configuration, not part of this candidate. The chemistry tests add
32 independent DOP853/Radau comparisons using an
absolute-temperature RHS, 18 heat-capacity/volume/reaction-sign combinations,
equal-rate limits, invalid declarations, budget exhaustion and an intentionally
wrong-rate trajectory that conserves quantities but fails accuracy comparison.
They require the `numerical-checks` extra; without SciPy that module is skipped
and must not be reported as qualified.

The existing 18-check polymer operator campaign is run before and after the
repair. It covers numerical audits, retained replay, fresh identities, typed
agent execution, idempotent retry and separation from part conformity. It does
not establish instrument calibration or factory qualification.

The new GitHub workflow defines Windows/Linux and Python 3.11/3.13 checks,
installs the repository's pinned numerical extras, and retains JUnit and CLI
artifacts. A workflow definition is not evidence that those jobs passed. Local
runs used Linux/Python 3.13.5, NumPy 2.3.5 and SciPy 1.17.0 rather than the
repository pins (NumPy 2.4.3, SciPy 1.17.1). Full-repository, pinned-environment,
installed-wheel and physical qualification remain separate gates.

## Primary technical references

- COMSOL, Linear Viscoelasticity (generalized Maxwell, frequency response and dissipation):
  https://doc.comsol.com/6.3/doc/com.comsol.help.sme/sme_ug_theory.06.029.html
- Python, expm1 and floating-point mathematical functions:
  https://docs.python.org/3/library/math.html#math.expm1
- SciPy, independent solve_ivp methods and tolerances:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html

This implementation is original scalar reference code; these references do not
constitute endorsement, a validated resin database or experimental evidence.
