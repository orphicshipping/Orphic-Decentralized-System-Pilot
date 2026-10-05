"""Bounded, non-stiff A -> B / heat-exchange benchmark; not a material model.

All four state components are integrated, including signed heat to the bath.
No clipping, state admission, mechanism import, or native-provider qualification.
See docs/THERMOCHEMICAL_BENCHMARK.md for equations and verification boundaries.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import sys
import tempfile
from typing import Any
import uuid

MODEL = "closed-thermochemical-a-to-b.v1"
REQUEST_SCHEMA = "ciw.thermochemical-benchmark-request.v1"
RESULT_SCHEMA = "ciw.thermochemical-benchmark-result.v1"
R_J_MOL_K = 8.31446261815324
MAX_REQUEST_BYTES = 65536
TEMPERATURE_RANGE_K = (200.0, 1000.0)
AUTHORITY = {
    "scope": "abstract-model-numerical-benchmark",
    "physical_validation": False,
    "native_provider_qualified": False,
    "state_admission": False,
    "hardware_actuation": False,
    "measurement_uncertainty": "not_declared",
}
FIELDS = {
    "schema", "model", "species_order", "initial_concentration_mol_m3",
    "initial_temperature_k", "bath_temperature_k", "reference_temperature_k",
    "reference_rate_s_inv", "activation_energy_j_mol",
    "reaction_internal_energy_j_mol", "heat_capacity_j_m3_k",
    "heat_transfer_w_m3_k", "volume_m3", "time_s", "solver",
}
SOLVER_FIELDS = {
    "rtol", "atol_concentration_mol_m3", "atol_temperature_k", "atol_heat_j",
    "max_step_s", "max_steps",
}
TRAJECTORY_FIELDS = {
    "species_order", "time_s", "concentration_mol_m3", "temperature_k",
    "heat_to_bath_j", "temperature_change_k",
}


class Refusal(ValueError):
    """A request or numerical execution is outside the declared contract."""


class _TrialRejected(Exception):
    """A trial stage is inadmissible; retry with a smaller step."""


def _keys(value: Any, expected: set[str], name: str) -> None:
    if type(value) is not dict or set(value) != expected:
        raise Refusal(f"{name}: missing or unexpected fields")


def _number(value: Any, lower: float, upper: float, name: str) -> float:
    if type(value) not in (int, float):
        raise Refusal(f"{name}: expected a finite number, not a boolean or string")
    try:
        x = float(value)
    except (OverflowError, ValueError) as exc:
        raise Refusal(f"{name}: number is not representable") from exc
    if not math.isfinite(x) or not lower <= x <= upper:
        raise Refusal(f"{name}: outside [{lower}, {upper}]")
    return x


def _rate(p: dict, temperature: float) -> float:
    # Reference-temperature form of Arrhenius; energy and R both use mol, not kmol.
    k0 = p["reference_rate_s_inv"]
    if k0 == 0:
        return 0.0
    exponent = p["activation_energy_j_mol"] / R_J_MOL_K * (
        1.0 / p["reference_temperature_k"] - 1.0 / temperature
    )
    return k0 * math.exp(exponent)


def validate_request(request: Any) -> dict:
    """Return a detached, binary64-normalized, SI-only data request."""
    _keys(request, FIELDS, "request")
    p = deepcopy(request)
    if p["schema"] != REQUEST_SCHEMA or p["model"] != MODEL:
        raise Refusal("Unsupported thermochemical schema or model")
    if p["species_order"] not in (["A", "B"], ["B", "A"]):
        raise Refusal("species_order must be [A, B] or [B, A]")
    initial = p["initial_concentration_mol_m3"]
    if type(initial) is not list or len(initial) != 2:
        raise Refusal("Expected exactly two initial concentrations")
    p["initial_concentration_mol_m3"] = [
        _number(x, 0, 1000, "initial concentration") for x in initial
    ]
    _number(math.fsum(p["initial_concentration_mol_m3"]), 1e-6, 1000, "total concentration")
    ranges = {
        "initial_temperature_k": (250, 500), "bath_temperature_k": (250, 500),
        "reference_temperature_k": (250, 500), "reference_rate_s_inv": (0, 10),
        "activation_energy_j_mol": (0, 150000),
        "reaction_internal_energy_j_mol": (-1e6, 1e6),
        "heat_capacity_j_m3_k": (1, 1e9), "heat_transfer_w_m3_k": (0, 1e9),
        "volume_m3": (1e-6, 1),
    }
    for key, (lo, hi) in ranges.items():
        p[key] = _number(p[key], lo, hi, key)
    times = p["time_s"]
    if type(times) is not list or not 2 <= len(times) <= 128:
        raise Refusal("time_s requires 2 to 128 samples")
    p["time_s"] = [_number(t, 0, 100, "time_s") for t in times]
    if times[0] != 0 or any(b - a < 1e-12 for a, b in zip(p["time_s"], p["time_s"][1:])):
        raise Refusal("time_s must increase from zero with spacing >= 1e-12 s")
    _keys(p["solver"], SOLVER_FIELDS, "solver")
    sranges = {
        "rtol": (1e-12, 1e-3), "atol_concentration_mol_m3": (1e-14, 1e-5),
        "atol_temperature_k": (1e-12, 1e-2), "atol_heat_j": (1e-14, 1e-2),
        "max_step_s": (1e-9, 1),
    }
    for key, (lo, hi) in sranges.items():
        p["solver"][key] = _number(p["solver"][key], lo, hi, key)
    count = p["solver"]["max_steps"]
    if type(count) is not int or not 1 <= count <= 100000:
        raise Refusal("max_steps must be an integer in [1, 100000]")
    a0 = p["initial_concentration_mol_m3"][p["species_order"].index("A")]
    rise = -p["reaction_internal_energy_j_mol"] * a0 / p["heat_capacity_j_m3_k"]
    lo = min(p["initial_temperature_k"], p["bath_temperature_k"]) + min(0.0, rise)
    hi = max(p["initial_temperature_k"], p["bath_temperature_k"]) + max(0.0, rise)
    if lo < TEMPERATURE_RANGE_K[0] or hi > TEMPERATURE_RANGE_K[1]:
        raise Refusal("Conservative reachable-temperature envelope leaves 200-1000 K")
    # This cap limits the declared rate domain, not a guarantee of non-stiffness.
    if _rate(p, hi) > 1000 or p["heat_transfer_w_m3_k"] / p["heat_capacity_j_m3_k"] > 1000:
        raise Refusal("Reaction or thermal rate exceeds 1000 s^-1")
    return p


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_request(raw: bytes) -> dict:
    if type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES:
        raise Refusal("Request must be UTF-8 bytes of at most 64 KiB")
    def invalid_constant(value: str) -> None:
        raise Refusal(f"Non-finite JSON constant: {value}")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=invalid_constant)
    except Refusal:
        raise
    except (ValueError, RecursionError) as exc:
        raise Refusal("Malformed or excessively nested JSON request") from exc
    return validate_request(data)


def _initial(p: dict) -> tuple[float, float, float, float]:
    values = dict(zip(p["species_order"], p["initial_concentration_mol_m3"]))
    return values["A"], values["B"], 0.0, 0.0


def _admissible(p: dict, y: tuple) -> None:
    if (not all(math.isfinite(x) for x in y) or y[0] < 0 or y[1] < 0
            or not TEMPERATURE_RANGE_K[0] <= p["initial_temperature_k"] + y[2] <= TEMPERATURE_RANGE_K[1]):
        raise _TrialRejected("Non-finite, negative concentration, or invalid temperature")


def _rhs(p: dict, y: tuple) -> tuple:
    _admissible(p, y)
    temperature = p["initial_temperature_k"] + y[2]
    rate = _rate(p, temperature) * y[0]
    heat_flux = p["heat_transfer_w_m3_k"] * ((p["initial_temperature_k"] - p["bath_temperature_k"]) + y[2])
    return (-rate, rate,
            (-p["reaction_internal_energy_j_mol"] * rate - heat_flux) / p["heat_capacity_j_m3_k"],
            heat_flux * p["volume_m3"])


def _rk4(p: dict, y: tuple, h: float) -> tuple:
    def advance(slope: tuple, factor: float) -> tuple:
        return tuple(a + h * factor * b for a, b in zip(y, slope))
    k1 = _rhs(p, y)
    k2 = _rhs(p, advance(k1, 0.5))
    k3 = _rhs(p, advance(k2, 0.5))
    k4 = _rhs(p, advance(k3, 1.0))
    result = tuple(a + h * math.fsum((b, 2*c, 2*d, e)) / 6
                   for a, b, c, d, e in zip(y, k1, k2, k3, k4))
    _admissible(p, result)
    return result


def _trajectory(p: dict, states: list[tuple]) -> dict:
    indices = [0 if name == "A" else 1 for name in p["species_order"]]
    return {"species_order": list(p["species_order"]), "time_s": list(p["time_s"]),
            "concentration_mol_m3": [[y[i] for i in indices] for y in states],
            "temperature_k": [p["initial_temperature_k"] + y[2] for y in states],
            "temperature_change_k": [y[2] for y in states],
            "heat_to_bath_j": [y[3] for y in states]}


def simulate(request: dict) -> dict:
    """Adaptive RK4 step doubling, with a global attempted-step budget.

    Accept two half steps without Richardson extrapolation. The local estimate
    is abs(fine-coarse)/15; it is not a certified bound on global solution error.
    """
    p = validate_request(request)
    solver = p["solver"]
    atols = (solver["atol_concentration_mol_m3"],) * 2 + (
        solver["atol_temperature_k"], solver["atol_heat_j"])
    y, t = _initial(p), 0.0
    states = [y]
    h = min(solver["max_step_s"], p["time_s"][1])
    attempted = accepted = rejected = 0
    for target in p["time_s"][1:]:
        while t < target:
            if attempted >= solver["max_steps"]:
                raise Refusal("Attempted-step budget exhausted; no successful result")
            remaining = target - t
            h = min(h, solver["max_step_s"], remaining)
            if t + h == t or t + h / 2 == t:
                raise Refusal("Step is below binary64 time resolution")
            attempted += 1
            try:
                coarse = _rk4(p, y, h)
                half = _rk4(p, y, h / 2)
                fine = _rk4(p, half, h / 2)
                error = max(abs(b - a) / (15 * (atol + solver["rtol"] * max(abs(old), abs(b))))
                            for old, a, b, atol in zip(y, coarse, fine, atols))
            except _TrialRejected:
                rejected += 1
                h *= 0.5
                continue
            if error > 1:
                rejected += 1
                h *= max(0.2, 0.9 * error ** -0.2)
                continue
            y = fine
            t = target if h == remaining else t + h
            accepted += 1
            h *= 4.0 if error == 0 else min(4.0, max(0.2, 0.9 * error ** -0.2))
        states.append(y)
    trajectory = _trajectory(p, states)
    metrics = check_balances(p, trajectory)
    return {"trajectory": trajectory, "balance_metrics": metrics,
            "solver": {"algorithm": "python-rk4-step-doubling.v1",
                       "attempted_steps": attempted, "accepted_steps": accepted,
                       "rejected_steps": rejected, "settings": deepcopy(solver)}}


def _validate_trajectory(p: dict, trajectory: dict) -> list[tuple]:
    _keys(trajectory, TRAJECTORY_FIELDS, "trajectory")
    if trajectory["species_order"] != p["species_order"] or trajectory["time_s"] != p["time_s"]:
        raise Refusal("Trajectory species order or sampling grid differs")
    # Equality alone would allow bools to impersonate 0/1 in a retained grid.
    if type(trajectory["time_s"]) is not list:
        raise Refusal("Trajectory grid must be a list")
    for t in trajectory["time_s"]:
        _number(t, 0, 100, "trajectory time")
    count = len(p["time_s"])
    for key in ("concentration_mol_m3", "temperature_k", "temperature_change_k", "heat_to_bath_j"):
        if type(trajectory[key]) is not list or len(trajectory[key]) != count:
            raise Refusal("Trajectory sample count differs")
    states = []
    ai = p["species_order"].index("A")
    for row, temp, change, heat in zip(trajectory["concentration_mol_m3"], trajectory["temperature_k"],
                                       trajectory["temperature_change_k"], trajectory["heat_to_bath_j"]):
        if type(row) is not list or len(row) != 2:
            raise Refusal("Trajectory concentration row must contain two numbers")
        values = [_number(x, 0, 1001, "trajectory concentration") for x in row]
        temp = _number(temp, 200, 1000, "trajectory temperature")
        change = _number(change, -800, 800, "trajectory temperature change")
        if abs(temp - (p["initial_temperature_k"] + change)) > 4 * math.ulp(temp):
            raise Refusal("Absolute temperature and temperature-change representation differ")
        states.append((values[ai], values[1-ai], change, _number(heat, -1e15, 1e15, "trajectory heat")))
    if states[0] != _initial(p):
        raise Refusal("Trajectory initial state differs")
    return states


def check_balances(request: dict, trajectory: dict) -> dict:
    """Check sampled conservation only: a wrong-rate path may still pass."""
    p = validate_request(request)
    states = _validate_trajectory(p, trajectory)
    a0, b0, _, _ = _initial(p)
    total = math.fsum((a0, b0))
    cv = p["heat_capacity_j_m3_k"] * p["volume_m3"]
    duv = p["reaction_internal_energy_j_mol"] * p["volume_m3"]
    mass_error = max(abs(math.fsum((a, b)) - total) for a, b, _, _ in states)
    terms = [(cv * change, duv * (b - b0), heat) for _, b, change, heat in states]
    energy_error = max(abs(math.fsum(row)) for row in terms)
    energy_scale = max(1.0, *(abs(x) for row in terms for x in row))
    if mass_error > 1e-12 + 1e-10 * total or energy_error > 1e-9 + 1e-10 * energy_scale:
        raise Refusal("Concentration or energy conservation failed")
    return {"concentration_balance_max_abs_mol_m3": mass_error,
            "energy_balance_max_abs_j": energy_error,
            "minimum_concentration_mol_m3": min(min(y[:2]) for y in states),
            "minimum_temperature_k": min(trajectory["temperature_k"]),
            "maximum_temperature_k": max(trajectory["temperature_k"]),
            "scope": "sampled-conservation-not-trajectory-accuracy"}


def constant_rate_reference(request: dict) -> dict:
    """Independent closed-form oracle when Ea=0 or k_ref=0, including k=lambda."""
    p = validate_request(request)
    if p["activation_energy_j_mol"] != 0 and p["reference_rate_s_inv"] != 0:
        raise Refusal("Constant-rate analytic reference is not applicable")
    a0, b0, _, _ = _initial(p)
    temp0 = p["initial_temperature_k"]
    k, cv = p["reference_rate_s_inv"], p["heat_capacity_j_m3_k"]
    lam = p["heat_transfer_w_m3_k"] / cv
    delta_u, bath, volume = p["reaction_internal_energy_j_mol"], p["bath_temperature_k"], p["volume_m3"]
    states = []
    for t in p["time_s"]:
        a = a0 * math.exp(-k * t)
        progress = -a0 * math.expm1(-k * t)
        d = abs(lam - k)
        convolution = t * math.exp(-min(k, lam) * t) if d == 0 else (
            math.exp(-min(k, lam) * t) * (-math.expm1(-d * t)) / d)
        change = (bath - temp0) * (-math.expm1(-lam * t)) - delta_u / cv * k * a0 * convolution
        heat = -volume * (cv * change + delta_u * progress)
        states.append((a, b0 + progress, change, heat))
    states[0] = _initial(p)
    return _trajectory(p, states)


def compare_trajectories(request: dict, candidate: dict, reference: dict) -> dict:
    """Dimensioned discrepancies under fixed numerical comparison tolerances."""
    p = validate_request(request)
    a, b = _validate_trajectory(p, candidate), _validate_trajectory(p, reference)
    labels = ("concentration_max_abs_mol_m3", "temperature_change_max_abs_k", "heat_max_abs_j")
    errors = [0.0, 0.0, 0.0]
    atols = (2e-8, 2e-8, 2e-6, 2e-5)
    within = True
    for actual, expected in zip(a, b):
        for i, (x, y) in enumerate(zip(actual, expected)):
            error = abs(x - y)
            group = max(0, i - 1)
            errors[group] = max(errors[group], error)
            within = within and error <= atols[i] + 2e-8 * abs(y)
    return {"within_policy": within, **dict(zip(labels, errors)),
            "policy": {"rtol": 2e-8, "atol_concentration_mol_m3": 2e-8,
                       "atol_temperature_k": 2e-6, "atol_heat_j": 2e-5}}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def make_report(request: dict) -> dict:
    p = validate_request(request)
    result = simulate(p)
    analytic = {"status": "not_applicable", "reason": "temperature-dependent-rate"}
    if p["activation_energy_j_mol"] == 0 or p["reference_rate_s_inv"] == 0:
        comparison = compare_trajectories(p, result["trajectory"], constant_rate_reference(p))
        if not comparison["within_policy"]:
            raise Refusal("Constant-rate analytic comparison failed")
        analytic = {"status": "passed", **comparison}
    body = {"schema": RESULT_SCHEMA, "request": p,
            "runtime": {"implementation": platform.python_implementation(),
                        "python_version": platform.python_version(), "platform": sys.platform},
            "request_sha256": hashlib.sha256(_canonical(p)).hexdigest(),
            "kernel_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            **result, "analytic_reference": analytic, "authority": deepcopy(AUTHORITY)}
    return {**body, "content_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
            "execution_id": str(uuid.uuid4())}


def _write_new_report(path: Path, text: str) -> None:
    """Publish only complete files, without clobbering an existing path.

    Hard-link publication is atomic and exclusive. Filesystems that cannot
    provide it refuse publication instead of falling back to unsafe overwrite.
    """
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".thermochemical-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path, help="New JSON report; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        with args.request.open("rb") as stream:
            raw = stream.read(MAX_REQUEST_BYTES + 1)
        report = make_report(load_request(raw))
        report["request_bytes_sha256"] = hashlib.sha256(raw).hexdigest()
        text = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            _write_new_report(args.output, text)
        else:
            sys.stdout.write(text)
        return 0
    except (ValueError, OSError) as exc:
        print(f"Thermochemical benchmark refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
