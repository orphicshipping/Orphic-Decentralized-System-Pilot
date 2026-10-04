# Comparisons using third-party numerical code

`scripts/check_external_numerics.py` checks the reservoir and polymer cooling
implementations against SciPy and a classical conduction solution. It imports
the production solvers and input contracts, but does not import CIW's reference
or verification implementations to calculate expected results.

This is a maintainer-authored comparison **inside this repository**. SciPy is
external numerical code; this report is not an independently authored external
publication. It supplies no physical experiment, material validation, sensor
calibration or manufacturing-process qualification. The review's request for a
comparison published and reproduced outside this repository remains open.

## Run and inspect

From the repository root, in a Python 3.11+ environment:

```sh
python -m pip install ".[numerical-checks,dev]"
python -I scripts/check_external_numerics.py --output comparison.json
python -m pytest tests/test_external_numerics.py -q
```

The comparison itself takes seconds on an ordinary CPU after installation. The
isolated command imports the installed package, so reinstall after changing the
models. CI runs the same checker against a built wheel and retains its JSON
report. The script returns zero only on `PASS`; numerical failures and execution
exceptions return nonzero, with a failure report when the output can be written.
The optional test module skips when SciPy is absent; the dedicated CI job installs
`numerical-checks` and requires the script to pass.

The JSON contains Python, NumPy, SciPy and package versions, the comparison
script's SHA256, hashes of the imported numerical source files, all measured
errors and tolerances, and the checkout SHA when CI supplies `GITHUB_SHA`.
The checkout SHA is environmental metadata; the source hashes identify the two
imported model files. A report never makes an uncommitted change part of that SHA.

## Reservoir equations and checks

Four synthetic cases cover coupled damped motion, coupled conservative motion,
uncoupled conservative motion, and mixed nonzero initial coordinates/velocities
with changed reservoir areas, pipe resistance, mass, stiffness and damping.
Every case passes the production input-domain checks before simulation.

The comparison calculates pressures directly from displaced liquid volumes:

```text
p1 = -rho g r / A1                    p2 = rho g (r - a x) / A2
dr/dt = Q                            dx/dt = v
dQ/dt = (p1 - p2 - R Q) / I           dv/dt = (a p2 - k x - c v) / m
I = rho pipe_length / pipe_area
```

It integrates these force balances with SciPy's adaptive eighth-order
[`DOP853` method](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.DOP853.html),
using `rtol=2e-12`, `atol=1e-15`. Three additional integrated states accumulate
`R Q^2`, `c v^2`, and `a p2 v`. This changes both the integration algorithm and
the work quadrature from the production implicit-midpoint implementation.

At every retained time, independently evaluated kinetic, spring and hydrostatic
energy are compared with the production traces. Fluid, structure and total
balances are checked separately, including the sign of interface work. State
errors use each reference coordinate's maximum absolute amplitude as the scale
(floor `1e-12`); energy/work errors use initial total energy. A relative error
at a zero crossing is therefore not used.

The finest trace must stay within `1e-3` of each state amplitude and `3e-5` of
initial energy for each energy, loss and interface-work quantity. Discrete and
continuous reference balance residuals must be at most `2e-9` of initial energy.
Halving the step must reduce the maximum normalized state error by a factor
between `3.7` and `4.3`, consistent with second-order convergence. These are
regression budgets for the four declared fixtures, not global bounds over the
entire accepted parameter space. A trajectory with a deliberately
wrong stiffness still balances energy; a regression test confirms that the
independent trajectory comparison rejects it.

## Cooling arithmetic and spatial discrepancy

The first comparison numerically integrates the homogeneous energy balance
`rho V cp dT/dt = -h A (T - T_ambient)` with SciPy `solve_ivp`, rather than
evaluating the production exponential. A temperature-crossing event supplies
the target time independently of the production logarithm. The temperature
error must be at most `2e-8 K`, and remaining-time error at most `2e-7 s`.
One fixture includes sample age, so target time and forecast time cannot be
silently treated as the same clock.

The second comparison deliberately permits internal temperature gradients:
an infinite plane wall, initially uniform, with equal constant convection at
both faces, constant properties and no heat source or phase change. With
`L = V/A` (half-thickness), `alpha = k/(rho cp)`, `Bi = h L/k`, and
`Fo = alpha t/L^2`, the heat equation has the separated solution

```text
theta(X,Fo) = sum C_n cos(mu_n X) exp(-mu_n^2 Fo),  X = x/L
mu_n tan(mu_n) = Bi
C_n = 4 sin(mu_n) / (2 mu_n + sin(2 mu_n))
```

This is the convective-slab solution treated in Lienhard and Lienhard,
[*A Heat Transfer Textbook*, chapter 5](https://ahtt.mit.edu/).
Spatial integration from `X=0` to `1` gives the volume-average coefficient
`C_n sin(mu_n)/mu_n`. The checker computes each eigenvalue using SciPy
[`brentq`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.brentq.html)
and sums 64 terms; it also checks agreement with 32 terms within `1e-12`.
Another bracketed solve locates the average-temperature target crossing.
The initial value is imposed exactly at time zero. Positive-time checks have
`Fo >= 3.125`, well beyond the slowly converging initial boundary transient.

The nine cases use `Bi = 0.001, 0.02, 0.08` and elapsed times `0.25, 1, 3`
times the lumped time constant, with varied density, heat capacity, wall size,
conductivity and boundary temperature. They compare the declared uniform
temperature to the slab **volume average**, not an arbitrary sensor location.
Inputs have zero supplied uncertainty to isolate arithmetic and spatial-model
effects, and all are inside the declared temperature, time and Biot domains.

The slab comparison allows a temperature difference of `0.01` times the initial
part-to-boundary temperature difference and a relative remaining-target-time
difference of `0.03`. These looser tolerances describe the finite-gradient
discrepancy in these idealized fixtures; they are not numerical-integration
tolerances or an accuracy promise for real polymers. In particular, a passing
low-Biot guard does not remove spatial-model discrepancy or add it to the
production uncertainty budget.

Regression tests corrupt energy, dissipation, interface work, cooling
temperature and target-time numbers, and check rejection. They also show that
the slab discrepancy tends toward zero as Biot number decreases. Calibration,
uncertainty propagation, latent heat, temperature-dependent material properties,
geometry-specific sensor response and experimental agreement remain untested
by this comparison.
