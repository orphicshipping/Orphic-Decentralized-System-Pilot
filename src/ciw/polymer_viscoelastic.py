"""Bounded uniaxial generalized-Maxwell benchmark, not a calibrated polymer.

Piecewise-linear prescribed strain; isothermal, small strain, fixed moduli.
Exact segment stress updates and independently integrated work/dissipation.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

MODEL = "uniaxial-generalized-maxwell.v1"
SCHEMA = "ciw.polymer-viscoelastic-request.v1"
FIELDS = {"schema", "model", "equilibrium_modulus_pa", "modes", "temperature_k",
          "initial_condition", "time_s", "strain", "frequency_hz"}
MODE_FIELDS = {"id", "modulus_pa", "relaxation_time_s"}
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")
AUTHORITY = {"scope": "declared-constitutive-model-numerical-benchmark",
             "physical_validation": False, "parameter_calibration": False,
             "state_admission": False, "hardware_actuation": False,
             "measurement_uncertainty": "not_declared"}


class Refusal(ValueError):
    """Data or calculation is outside this declared benchmark contract."""


def _keys(value, expected, name):
    if type(value) is not dict or set(value) != expected:
        raise Refusal(f"{name}: missing or unexpected fields")


def _number(value, lo, hi, name):
    if type(value) not in (int, float):
        raise Refusal(f"{name}: expected finite number, not boolean/string")
    try:
        v = float(value)
    except (ValueError, OverflowError) as exc:
        raise Refusal(f"{name}: number not representable") from exc
    if not math.isfinite(v) or not lo <= v <= hi:
        raise Refusal(f"{name}: outside [{lo}, {hi}]")
    return v


def validate_request(request):
    _keys(request, FIELDS, "request")
    p = deepcopy(request)
    if p["schema"] != SCHEMA or p["model"] != MODEL:
        raise Refusal("Unsupported viscoelastic schema/model")
    if p["initial_condition"] != "instantaneous-elastic-at-t0":
        raise Refusal("Only declared instantaneous elastic initial strain is supported")
    p["equilibrium_modulus_pa"] = _number(p["equilibrium_modulus_pa"], 0, 1e12, "equilibrium modulus")
    p["temperature_k"] = _number(p["temperature_k"], 200, 600, "temperature")
    if type(p["modes"]) is not list or len(p["modes"]) > 32:
        raise Refusal("Expected zero to 32 Maxwell modes")
    ids = set()
    for mode in p["modes"]:
        _keys(mode, MODE_FIELDS, "mode")
        if type(mode["id"]) is not str or not _ID.fullmatch(mode["id"]) or mode["id"] in ids:
            raise Refusal("Mode identities must be unique bounded identifiers")
        ids.add(mode["id"])
        mode["modulus_pa"] = _number(mode["modulus_pa"], 1e-3, 1e12, "mode modulus")
        mode["relaxation_time_s"] = _number(mode["relaxation_time_s"], 1e-9, 1e9, "relaxation time")
    instantaneous = math.fsum([p["equilibrium_modulus_pa"]] + [m["modulus_pa"] for m in p["modes"]])
    if not 1e-3 <= instantaneous <= 1e12:
        raise Refusal("Total instantaneous modulus outside [1e-3, 1e12] Pa")
    if type(p["time_s"]) is not list or not 2 <= len(p["time_s"]) <= 2048:
        raise Refusal("Expected 2 to 2048 strain-history samples")
    p["time_s"] = [_number(t, 0, 1e6, "time") for t in p["time_s"]]
    if p["time_s"][0] != 0 or any(b-a < 1e-9 for a, b in zip(p["time_s"], p["time_s"][1:])):
        raise Refusal("Time must increase from zero with spacing >= 1e-9 s")
    if type(p["strain"]) is not list or len(p["strain"]) != len(p["time_s"]):
        raise Refusal("Strain and time sample counts differ")
    p["strain"] = [_number(e, -0.02, 0.02, "small strain") for e in p["strain"]]
    if type(p["frequency_hz"]) is not list or len(p["frequency_hz"]) > 256:
        raise Refusal("Expected zero to 256 frequency samples")
    p["frequency_hz"] = [_number(f, 0, 1e9, "frequency") for f in p["frequency_hz"]]
    if any(b <= a for a, b in zip(p["frequency_hz"], p["frequency_hz"][1:])):
        raise Refusal("Frequencies must strictly increase")
    return p


def _phi2(x):
    """(x + expm1(-x))/x^2, cancellation-safe for a short segment."""
    if x < 0.05:
        term, terms = 0.5, [0.5]
        for n in range(1, 12):
            term *= -x / (n + 2)
            terms.append(term)
        return math.fsum(terms)
    return (x + math.expm1(-x)) / x**2


def _segment(q0, modulus, tau, h, delta_strain):
    """Return final stress, signed work density, nonnegative loss density.

    For short segments integrate the power-series representation of q(s)^2;
    for longer segments use an orthogonal mean/variance exponential basis.
    Neither energy channel is reconstructed from the energy-balance residual.
    """
    x = h / tau
    a = math.exp(-x)
    phi1 = -math.expm1(-x) / x
    delta = modulus * delta_strain
    q1 = math.fsum((q0*a, delta*phi1))
    mean = math.fsum((q0*phi1, delta*_phi2(x)))
    work = delta_strain * mean
    if x < 0.05:
        # q(h*u)=q0 + (delta-x*q0)u + ...; u in [0,1].
        coeff = [q0, delta-x*q0]
        for n in range(2, 14):
            coeff.append(-x*coeff[-1]/n)
        square_integral = math.fsum(ci*cj/(i+j+1) for i, ci in enumerate(coeff)
                                    for j, cj in enumerate(coeff))
        dissipation = x * square_integral / modulus
    else:
        steady = delta / x
        transient = q0 - steady
        # Var(exp(-s/tau)), normalized to [0,h]. Positive outside series region.
        variance = -math.expm1(-2*x)/(2*x) - phi1**2
        dissipation = x / modulus * math.fsum((mean**2, transient**2 * variance))
    if not all(math.isfinite(v) for v in (q1, work, dissipation)) or dissipation < 0:
        raise Refusal("Invalid segment: no negative-dissipation clipping is permitted")
    return q1, work, dissipation


def dynamic_moduli(request):
    """E*(omega) for exp(i omega t); E'' >= 0, frequencies explicitly in Hz."""
    p = validate_request(request)
    storage, loss = [], []
    for f in p["frequency_hz"]:
        elastic, viscous = [p["equilibrium_modulus_pa"]], []
        for mode in p["modes"]:
            x = 2*math.pi*f*mode["relaxation_time_s"]
            # Avoid x^2 overflow in any future extension of the bounded domain.
            if x <= 1:
                real, imag = x*x/(1+x*x), x/(1+x*x)
            else:
                inv = 1/x
                real, imag = 1/(1+inv*inv), inv/(1+inv*inv)
            elastic.append(mode["modulus_pa"]*real)
            viscous.append(mode["modulus_pa"]*imag)
        storage.append(math.fsum(elastic)); loss.append(math.fsum(viscous))
    return {"frequency_hz": p["frequency_hz"], "storage_modulus_pa": storage,
            "loss_modulus_pa": loss, "convention": "exp(+i*omega*t);omega=2*pi*f"}


def _energy(p, strain, stresses):
    return math.fsum([0.5*p["equilibrium_modulus_pa"]*strain**2] +
                     [q*q/(2*m["modulus_pa"]) for q, m in zip(stresses, p["modes"])])


def simulate(request):
    p = validate_request(request)
    strains, times, modes, eq = p["strain"], p["time_s"], p["modes"], p["equilibrium_modulus_pa"]
    q = [m["modulus_pa"]*strains[0] for m in modes]
    stresses = [math.fsum([eq*strains[0]]+q)]
    branches = [q.copy()]
    energies = [_energy(p, strains[0], q)]
    works, losses, step_work, step_loss = [0.0], [0.0], [], []
    for i, (t0,t1) in enumerate(zip(times, times[1:])):
        h, de = t1-t0, strains[i+1]-strains[i]
        w = [0.5*eq*de*(strains[i]+strains[i+1])]
        d, new = [], []
        for qi, m in zip(q, modes):
            qnew, wi, di = _segment(qi, m["modulus_pa"], m["relaxation_time_s"], h, de)
            new.append(qnew); w.append(wi); d.append(di)
        q = new
        step_work.append(math.fsum(w)); step_loss.append(math.fsum(d))
        works.append(math.fsum(step_work)); losses.append(math.fsum(step_loss))
        branches.append(q.copy())
        stresses.append(math.fsum([eq*strains[i+1]]+q))
        energies.append(_energy(p, strains[i+1], q))
    trajectory = {"time_s": times, "strain": strains, "mode_order": [m["id"] for m in modes],
                  "mode_stress_pa": branches, "stress_pa": stresses,
                  "stored_energy_j_m3": energies, "work_j_m3": works,
                  "dissipated_energy_j_m3": losses}
    metrics = check_balances(p, trajectory)
    return {"trajectory": trajectory, "dynamic_moduli": dynamic_moduli(p),
            "balance_metrics": metrics,
            "work_accounting": {"segments": len(times)-1, "mode_updates": (len(times)-1)*len(modes),
                                "frequency_mode_evaluations": len(p["frequency_hz"])*len(modes)},
            "authority": deepcopy(AUTHORITY)}


def check_balances(request, trajectory):
    """Algebraic and energy checks only; not a relaxation-rate oracle."""
    p = validate_request(request)
    keys = {"time_s", "strain", "mode_order", "mode_stress_pa", "stress_pa",
            "stored_energy_j_m3", "work_j_m3", "dissipated_energy_j_m3"}
    _keys(trajectory, keys, "trajectory")
    for key, expected in (("time_s",p["time_s"]), ("strain",p["strain"]),
                          ("mode_order",[m["id"] for m in p["modes"]])):
        if type(trajectory[key]) is not list or trajectory[key] != expected:
            raise Refusal(f"Trajectory {key} binding differs")
    for key in ("time_s", "strain"):
        for v in trajectory[key]: _number(v, -1e6, 1e6, key)
    n = len(p["time_s"])
    for key in keys-{"mode_order"}:
        if type(trajectory[key]) is not list or len(trajectory[key]) != n:
            raise Refusal("Trajectory length differs")
    stress_residuals, energy_residuals, balance_residuals = [], [], []
    for i in range(n):
        q = trajectory["mode_stress_pa"][i]
        if type(q) is not list or len(q) != len(p["modes"]):
            raise Refusal("Mode stress shape differs")
        for v in q: _number(v, -1e12, 1e12, "mode stress")
        stress = _number(trajectory["stress_pa"][i], -1e12, 1e12, "stress")
        energy = _number(trajectory["stored_energy_j_m3"][i], 0, 1e15, "energy")
        work = _number(trajectory["work_j_m3"][i], -1e15, 1e15, "work")
        loss = _number(trajectory["dissipated_energy_j_m3"][i], 0, 1e15, "loss")
        expected_stress = math.fsum([p["equilibrium_modulus_pa"]*p["strain"][i]]+q)
        expected_energy = _energy(p,p["strain"][i],q)
        stress_residuals.append(abs(stress-expected_stress))
        energy_residuals.append(abs(energy-expected_energy))
        if not math.isclose(stress,expected_stress,rel_tol=2e-11,abs_tol=1e-7):
            raise Refusal("Stress decomposition differs")
        if not math.isclose(energy,expected_energy,rel_tol=2e-11,abs_tol=1e-9):
            raise Refusal("Stored-energy decomposition differs")
        if i == 0:
            expected_q = [m["modulus_pa"]*p["strain"][0] for m in p["modes"]]
            if q != expected_q or work != 0 or loss != 0:
                raise Refusal("Initial elastic state differs")
        else:
            if loss < trajectory["dissipated_energy_j_m3"][i-1]:
                raise Refusal("Dissipation is not monotonic")
            dw = work-trajectory["work_j_m3"][i-1]
            du = energy-trajectory["stored_energy_j_m3"][i-1]
            dd = loss-trajectory["dissipated_energy_j_m3"][i-1]
            residual = abs(math.fsum((dw,-du,-dd)))
            scale = max(energy,trajectory["stored_energy_j_m3"][i-1],abs(work),loss,1.0)
            if residual > 1e-8 + 2e-10*scale:
                raise Refusal("Mechanical work/energy/dissipation balance failed")
            balance_residuals.append(residual)
    return {"stress_decomposition_max_abs_pa": max(stress_residuals),
            "stored_energy_decomposition_max_abs_j_m3": max(energy_residuals),
            "energy_balance_max_abs_j_m3": max(balance_residuals,default=0),
            "scope": "sampled-algebraic-and-energy-consistency-not-rate-accuracy"}
