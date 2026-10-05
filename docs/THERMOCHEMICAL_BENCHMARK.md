# Coupled thermochemical benchmark

This additive NET benchmark joins first-order reaction kinetics and lumped heat
transfer in one explicitly bounded model. It does **not** change or qualify the
existing `reaction-a-to-b.v1` Catalyst/Cantera profile. That profile remains
isothermal and equal-enthalpy. This new profile is a Python numerical benchmark,
not a native-provider binding, material database, chemical mechanism importer,
polymer cure model, measurement, or equipment controller.

## Model and units

`closed-thermochemical-a-to-b.v1` describes a homogeneous, closed, fixed-volume
control volume containing abstract species A and B. Their identical composition
and charge are model assumptions, not imported chemical information. The only
reaction is A -> B. Total concentration is constant. The closure has constant
volumetric heat capacity and a constant species internal-energy difference.
There are no flow, pressure-work, phase-change, spatial-gradient, reverse-reaction,
mixture-property, or bath-temperature dynamics.

With r = k(T)c_A and theta = T - T_initial, the equations are:

```text
k(T) = k_reference * exp[(Ea/R) * (1/T_reference - 1/T)]
dc_A/dt = -r
dc_B/dt =  r
C_v * dtheta/dt = -Delta_u * r - H * (T_initial + theta - T_bath)
dQ_bath/dt = V * H * (T_initial + theta - T_bath)
```

C_v is volumetric constant-volume heat capacity in J/(m^3 K), not a mass-based
heat capacity or constant-pressure c_p. Delta_u = u_B - u_A is in J/mol:
negative is exothermic, positive is endothermic. It is an **internal-energy**
difference, not a generic reaction enthalpy. R = 8.31446261815324 J/(mol K).
H is a nonnegative volumetric heat-transfer coefficient in W/(m^3 K); its
relation to geometry or an actual surface conductance must be supplied separately.
Q_bath is signed heat in joules: positive leaves the control volume, negative
enters it. Concentrations use mol/m^3, volume m^3, time seconds and temperature K.
There is no implicit Celsius, kmol, or heat-capacity basis conversion.

The checked energy identity is:

```text
C_v * V * theta + Delta_u * V * (c_B - c_B_initial) + Q_bath = 0.
```

All four state components (c_A, c_B, theta, Q_bath) are independently integrated.
The energy balance is not imposed by reconstructing Q_bath from the other states.
Tracking theta directly avoids repeated subtraction of nearly equal absolute
temperatures in high-heat-capacity cases. Reports retain both theta and absolute
T; the checker requires their representations to agree within four binary64 ulps.
Volume-scaling tests keep C_v and H fixed: concentrations and temperature are
intensive, while total transferred heat scales with V. This is not a claim about
geometric scaling of a real vessel's surface conductance.

## Data-only request boundary

The full example is `examples/thermochemical/request.json`. Exact field sets are
required, including the schema, model, species order and solver settings. Unknown
fields, duplicate JSON keys, booleans masquerading as numbers, numeric strings,
non-finite numbers and invalid species orders are refused. JSON input is limited
to 64 KiB; no equations, executable paths, provider names or code are accepted.

The domain is deliberately bounded:

- Two nonnegative concentrations with total 1e-6 to 1000 mol/m^3, and species
  order either [A, B] or [B, A].
- Initial, bath and reference temperatures 250-500 K. A conservative envelope
  using the complete reaction energy and bath temperature must remain in
  200-1000 K. Ea is 0-150000 J/mol and k_reference is 0-10 s^-1.
- Delta_u is between -1e6 and 1e6 J/mol; C_v is 1-1e9 J/(m^3 K); volume is
  1e-6 to 1 m^3. Reaction rate over the conservative envelope and H/C_v may not
  exceed 1000 s^-1. These caps do not establish stiffness qualification.
- 2-128 strictly increasing samples beginning at zero, final time at most 100 s,
  spacing at least 1e-12 s, and 1-100000 globally counted attempted steps.

A structurally admissible request is not guaranteed to finish within its budget.
An execution failure returns a refusal, not a partially successful trajectory.

## Numerical method and checks

The standard-library kernel uses adaptive classical RK4 with step doubling.
It accepts the two half steps without Richardson extrapolation. Its estimate
abs(fine-coarse)/15 is normalized with a separate absolute tolerance for each
quantity and a relative tolerance. The temperature tolerance applies to theta,
not the absolute-temperature offset. This is a local numerical error estimate,
not a certified global error bound. Sampling targets are reached by integration,
not by unqualified dense interpolation.

Every trial stage and proposed state is checked for finite numbers, nonnegative
concentrations and the temperature domain. An inadmissible trial is retried with
a smaller step; nothing is silently clipped. Rejected trials count against the
same global budget as accepted trials. Exhaustion or loss of time resolution
refuses the run. Sampled concentration and energy balances are checked separately
and reported in their own units, not combined into a physical accuracy score.

For Ea=0 or k_reference=0, a separate closed-form solution checks the complete
sampled trajectory. Its heat-exchange convolution uses expm1 and handles equal
and nearly equal reaction and cooling rates. For nonlinear Arrhenius cases the
runtime report explicitly says the constant-rate oracle is not applicable.
Nonlinear verification currently lives in the tests: 12 seeded cases against
each of SciPy DOP853 and Radau, using an independently written right-hand side.
These test calls do not register either method as a NET provider.

A deliberately wrong-rate trajectory preserves both balances yet fails the
reference comparison. Conservation is necessary, not sufficient for accuracy.
The tests also check fourth-order step refinement, adiabatic exothermic and
endothermic signs, pure heat exchange, incoming bath heat, species permutation,
volume scaling, high-heat-capacity cancellation, malformed requests, corrupted
trajectories, step exhaustion, content identity and command-line failure paths.

## Invocation and report identity

From a source checkout on a POSIX shell:

```sh
PYTHONPATH=src python -m ciw.thermochemical_benchmark \
  examples/thermochemical/request.json --output new-thermochemical-result.json
PYTHONPATH=src python -m pytest -q tests/test_thermochemical_benchmark.py
```

With the containing CIW package installed, omit PYTHONPATH. On PowerShell set
`$env:PYTHONPATH = 'src'` for a source checkout. This is a module-level entrypoint;
this increment does not add a `net` subcommand, native-operation dispatch,
workspace replay, or a second evidence ledger. SciPy is not needed to run the
kernel. The 24 independent solver tests require the existing `numerical-checks`
extra; without SciPy those tests skip and must not be reported as passing.

Reports include the normalized request, source and request digests, per-run
execution identity, runtime version information, trajectory, solver counts,
dimensioned balance metrics and the applicable analytic comparison. The CLI also
records the exact input-byte digest. `content_sha256` covers the report body
before the fresh execution identity and raw-input transport digest are appended.
Runtime strings and hashes are observations, not environment attestation or a
signature. Matching hashes do not establish physical truth.

File output uses exclusive, atomic hard-link publication after a temporary file
is fully written and flushed. Existing outputs are never overwritten. A
filesystem without the required hard-link support refuses publication rather
than falling back to replacement. Stdout output is an ordinary stream, not an
atomic retained artifact. No crash-durability guarantee for directory metadata
is asserted.

## Validation record and remaining gates

The focused local run passed **158 tests**, including all **24 independent
SciPy comparisons** and **18 heat-capacity/volume/activation combinations**.
Environment: CPython 3.13.5, pytest 9.0.2, NumPy 2.3.5 and SciPy 1.17.0 on Linux.
The numerical kernel itself has no NumPy or SciPy dependency. This is a source
module test pass, not a full-repository, installed-wheel, Windows, pinned-provider,
GPU, or physical validation pass. The repository's pinned optional SciPy version
was not installed in this runtime. The full checkout could not be cloned from
the execution environment; source context was inspected through GitHub.

No existing runtime, provider allowlist, package pin, native reaction contract,
state-admission policy, hardware authority or license is changed. Publication as
a draft PR is not a merge or a hosted CI pass. Remaining integration work is to
qualify this distinct model through approved provider and retained-workspace
contracts, then compare independent implementations under pinned environments.
Physical materials, uncertainty, parameter calibration, stiff mechanisms,
spatial transport and experimental validation each require their own evidence.

## Scientific references

The Arrhenius convention and distinction between reaction-rate and energy
balances follow standard formulations documented by the projects below; the
specific bounded constant-property model and implementation here are new code.

- Cantera, rate constant parameterizations:
  https://www.cantera.org/stable/reference/kinetics/rate-constants.html
- Cantera, ideal-gas control-volume reactor energy equation:
  https://www.cantera.org/stable/reference/reactors/ideal-gas-reactor.html
- SciPy, solve_ivp method and tolerance documentation:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html
