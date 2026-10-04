"""Compare bounded CIW models with SciPy; no physical-validation claim.

Run against an installed wheel with ``python -I scripts/check_external_numerics.py
--output comparison.json``. No CIW reference or verification implementation is
used to produce the comparison values. See docs/EXTERNAL_NUMERICAL_COMPARISONS.md.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform

import numpy as np


SCOPE = "in_repository_comparison_using_third_party_numerics_not_physical_validation"
STATE_FIELDS = ("transfer_m3", "piston_displacement_m", "flow_m3_per_s", "piston_velocity_m_per_s")
ENERGY_FIELDS = ("fluid_energy_j", "structure_energy_j", "total_energy_j",
                 "fluid_dissipation_j", "structure_dissipation_j",
                 "structure_interface_work_j", "fluid_interface_work_j")
TOLERANCES = {
    "reservoir_finer_state_normalized": 1e-3,
    "reservoir_finer_energy_normalized": 3e-5,
    "reservoir_balance_normalized": 2e-9,
    "reservoir_reference_balance_normalized": 2e-9,
    "reservoir_refinement_ratio_minimum": 3.7,
    "reservoir_refinement_ratio_maximum": 4.3,
    "cooling_ode_temperature_k": 2e-8,
    "cooling_ode_target_time_s": 2e-7,
    "cooling_slab_temperature_initial_gap_fraction": 0.01,
    "cooling_slab_target_time_relative": 0.03,
    "cooling_series_32_64_term_difference": 1e-12,
}


def _bounded_check(value, maximum):
    return math.isfinite(value) and value <= maximum


def reservoir_cases():
    from ciw.fluid_reservoir_contract import example_request

    default = example_request()
    cases = {"coupled_damped": default}
    for label in ("coupled_conservative", "uncoupled_conservative", "mixed_initial_pulse"):
        request = deepcopy(default)
        if label.endswith("conservative"):
            request["connector"]["resistance_pa_s_per_m3"] = 0.0
            request["structure"]["damping_n_s_per_m"] = 0.0
        if label == "uncoupled_conservative":
            request["structure"]["enabled"] = False
        if label == "mixed_initial_pulse":
            request["reservoirs"].update(area1_m2=0.14, area2_m2=0.08)
            request["connector"].update(length_m=1.4, resistance_pa_s_per_m3=50000.0)
            request["structure"].update(mass_kg=8.0, stiffness_n_per_m=150.0, damping_n_s_per_m=1.5)
            request["initial"].update(transfer_m3=0.0004, flow_m3_per_s=-0.0001,
                                      piston_displacement_m=0.003, piston_velocity_m_per_s=-0.005)
        cases[label] = request
    return cases


def reservoir_reference(request, times):
    """Integrate pressure/force equations and work rates with adaptive RK8.

    The state matrix, midpoint step, CIW reference and CIW verifier are not used.
    Energy is evaluated directly from the computed heights and velocities.
    """
    from scipy.integrate import solve_ivp

    fluid, tanks, pipe, piston = (request[k] for k in ("fluid", "reservoirs", "connector", "structure"))
    density, gravity = fluid["density_kg_per_m3"], fluid["gravity_m_per_s2"]
    a1, a2 = tanks["area1_m2"], tanks["area2_m2"]
    area = piston["piston_area_m2"] if piston["enabled"] else 0.0
    inertia = density * pipe["length_m"] / pipe["area_m2"]
    resistance = pipe["resistance_pa_s_per_m3"]
    mass, spring = piston["mass_kg"], piston["stiffness_n_per_m"]
    damping = piston["damping_n_s_per_m"] if piston["enabled"] else 0.0

    def rates(_time, y):
        transfer, position, flow, speed = y[:4]
        p1 = density * gravity * (-transfer / a1)
        p2 = density * gravity * ((transfer - area * position) / a2)
        force = area * p2
        acceleration = (force - spring * position - damping * speed) / mass if piston["enabled"] else 0.0
        return [flow, speed, (p1 - p2 - resistance * flow) / inertia,
                acceleration, resistance * flow**2, damping * speed**2, force * speed]

    initial = [request["initial"][field] for field in STATE_FIELDS] + [0.0, 0.0, 0.0]
    solution = solve_ivp(rates, (0.0, request["clock"]["duration_s"]), initial,
                         method="DOP853", t_eval=times, rtol=2e-12, atol=1e-15)
    if not solution.success:
        raise RuntimeError("SciPy reservoir integration failed: " + solution.message)
    transfer, position, flow, speed, fluid_loss, structure_loss, work = solution.y
    fluid_energy = (inertia * flow**2 + density * gravity *
                    (transfer**2 / a1 + (transfer - area * position)**2 / a2)) / 2.0
    structure_energy = (mass * speed**2 + spring * position**2) / 2.0 if piston["enabled"] else np.zeros_like(times)
    total = fluid_energy + structure_energy
    values = dict(zip(STATE_FIELDS, solution.y[:4]))
    values.update(zip(ENERGY_FIELDS, (fluid_energy, structure_energy, total, fluid_loss,
                                     structure_loss, work, -work)))
    residual = np.max(np.abs(total + fluid_loss + structure_loss - total[0])) / total[0]
    return values, float(residual)


def compare_reservoir(request, result):
    """Compare raw numbers, so even a corrupted/resealed result can fail."""
    rows = {}
    for label in ("coarse", "fine", "finer"):
        trace = result["resolutions"][label]["trace"]
        reference, reference_balance = reservoir_reference(request, trace["time_s"])
        energy_scale = float(reference["total_energy_j"][0])
        state_errors = {}
        for field in STATE_FIELDS:
            # Maximum amplitude avoids singular pointwise relative error at zero.
            scale = max(float(np.max(np.abs(reference[field]))), 1e-12)
            state_errors[field] = float(np.max(np.abs(np.asarray(trace[field]) - reference[field])) / scale)
        energy_errors = {field: float(np.max(np.abs(np.asarray(trace[field]) - reference[field])) / energy_scale)
                         for field in ENERGY_FIELDS}
        fluid_energy, structure_energy = (np.asarray(trace[field]) for field in ENERGY_FIELDS[:2])
        balances = {
            "total": np.asarray(trace["total_energy_j"]) + trace["fluid_dissipation_j"] +
                     np.asarray(trace["structure_dissipation_j"]) - energy_scale,
            "fluid": fluid_energy + trace["fluid_dissipation_j"] -
                     np.asarray(trace["fluid_interface_work_j"]) - reference["fluid_energy_j"][0],
            "structure": structure_energy + trace["structure_dissipation_j"] -
                         np.asarray(trace["structure_interface_work_j"]) - reference["structure_energy_j"][0],
        }
        rows[label] = {"state_errors_normalized": state_errors,
                       "energy_errors_over_initial_energy": energy_errors,
                       "balance_errors_over_initial_energy": {key: float(np.max(np.abs(value)) / energy_scale)
                                                              for key, value in balances.items()},
                       "reference_balance_error_over_initial_energy": reference_balance}
    state_maxima = [max(rows[label]["state_errors_normalized"].values()) for label in rows]
    ratios = [before / after if after > 0 else 0.0 for before, after in zip(state_maxima, state_maxima[1:])]
    fine = rows["finer"]
    checks = {
        "finer_state": all(_bounded_check(v, TOLERANCES["reservoir_finer_state_normalized"])
                           for v in fine["state_errors_normalized"].values()),
        "finer_energy_and_work": all(_bounded_check(v, TOLERANCES["reservoir_finer_energy_normalized"])
                                     for v in fine["energy_errors_over_initial_energy"].values()),
        "discrete_balances": all(_bounded_check(v, TOLERANCES["reservoir_balance_normalized"])
                                 for row in rows.values() for v in row["balance_errors_over_initial_energy"].values()),
        "reference_balance": all(_bounded_check(row["reference_balance_error_over_initial_energy"],
                                                 TOLERANCES["reservoir_reference_balance_normalized"])
                                  for row in rows.values()),
        "second_order_refinement": all(TOLERANCES["reservoir_refinement_ratio_minimum"] <= ratio <=
                                        TOLERANCES["reservoir_refinement_ratio_maximum"] for ratio in ratios),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "resolution_errors": rows, "state_error_refinement_ratios": ratios}


def slab_average_ratio(biot, fourier, terms=64):
    """Volume-average plane-wall series; L=half-thickness=V/exposed area."""
    from scipy.optimize import brentq

    if biot <= 0 or fourier < 0:
        raise ValueError("Require positive Biot number and nonnegative Fourier time")
    if fourier == 0:
        return 1.0  # Uniform initial condition, avoiding slow t=0 truncation.
    roots = np.asarray([brentq(lambda mu: mu * math.tan(mu) - biot,
                              n * math.pi + 1e-12, (n + 0.5) * math.pi - 1e-12,
                              xtol=1e-14) for n in range(terms)])
    weights = 4.0 * np.sin(roots)**2 / (roots * (2.0 * roots + np.sin(2.0 * roots)))
    return float(np.sum(weights * np.exp(-roots**2 * fourier)))


def cooling_cases():
    from ciw.polymer_contract import example_request, validate_request

    cases = {}
    for biot, length, conductivity, density, capacity, face_area, initial, ambient, age in (
        (0.001, 1e-4, 0.15, 900.0, 1800.0, 0.001, 370.0, 295.0, 0.0),
        (0.02, 4e-4, 0.22, 1050.0, 2000.0, 0.002, 350.0, 300.0, 0.0),
        (0.08, 6e-4, 0.28, 1150.0, 2400.0, 0.004, 410.0, 310.0, 0.5),
    ):
        h = biot * conductivity / length
        tau = density * capacity * length / h
        for elapsed_over_tau in (0.25, 1.0, 3.0):
            request = example_request()
            request["clock"]["max_skew_s"] = 0.0
            cooling = request["model"]["cooling"]
            values = {"density_kg_m3": density, "heat_capacity_j_kg_k": capacity,
                      "volume_m3": 2.0 * length * face_area, "area_m2": 2.0 * face_area,
                      "heat_transfer_w_m2_k": h, "conductivity_w_m_k": conductivity,
                      "target_temperature_k": ambient + 0.6 * (initial - ambient)}
            for name, value in values.items():
                cooling[name].update(value=value, standard_uncertainty=0.0,
                                     source_ref="sha256:" + sha256(json.dumps({name: value}).encode()).hexdigest())
            cooling["forecast_horizon_s"] = elapsed_over_tau * tau - age
            request["model"]["validity"]["elapsed_s"] = [0.0, 3600.0]
            for sensor in request["sensors"]:
                if sensor["sensor_id"] in ("part-temperature", "cooling-water"):
                    for sample in sensor["samples"]:
                        sample.update(value=initial if sensor["sensor_id"] == "part-temperature" else ambient,
                                      standard_uncertainty=0.0)
                    if sensor["sensor_id"] == "part-temperature":
                        sensor["samples"][-1]["time_s"] -= age
                    sensor["source_ref"] = "sha256:" + sha256(json.dumps(sensor["samples"], sort_keys=True).encode()).hexdigest()
            cases[f"biot_{biot:g}_elapsed_{elapsed_over_tau:g}_tau"] = validate_request(request)
    return cases


def compare_cooling(request, estimate):
    from scipy.integrate import solve_ivp
    from scipy.optimize import brentq

    if estimate["cooling"]["status"] != "ESTIMATED" or estimate["cooling"].get("target", {}).get("status") != "ESTIMATED":
        return {"status": "FAIL", "reason": "valid_fixture_did_not_produce_cooling_and_target_estimates"}
    cooling = request["model"]["cooling"]
    values = {key: row["value"] for key, row in cooling.items() if isinstance(row, dict)}
    sensors = {row["sensor_id"]: row for row in request["sensors"]}
    part = sensors[cooling["temperature_sensor_id"]]["samples"][-1]
    initial = part["value"]
    ambient = sensors[cooling["ambient_sensor_id"]]["samples"][-1]["value"]
    age = request["clock"]["end_s"] - part["time_s"]
    elapsed = age + cooling["forecast_horizon_s"]
    heat_capacity = values["density_kg_m3"] * values["volume_m3"] * values["heat_capacity_j_kg_k"]
    conductance = values["heat_transfer_w_m2_k"] * values["area_m2"]

    def rates(_time, temperature):
        return [-conductance * (temperature[0] - ambient) / heat_capacity]

    def target_event(_time, temperature):
        return temperature[0] - values["target_temperature_k"]

    solution = solve_ivp(rates, (0.0, request["model"]["validity"]["elapsed_s"][1]), [initial],
                         method="DOP853", dense_output=True, events=target_event, rtol=2e-13, atol=1e-12)
    if not solution.success or len(solution.t_events[0]) != 1:
        raise RuntimeError("SciPy cooling integration or target crossing failed")
    ode_temperature = float(solution.sol(elapsed)[0])
    ode_remaining_time = max(0.0, float(solution.t_events[0][0]) - age)
    length = values["volume_m3"] / values["area_m2"]
    diffusivity = values["conductivity_w_m_k"] / (values["density_kg_m3"] * values["heat_capacity_j_kg_k"])
    biot = values["heat_transfer_w_m2_k"] * length / values["conductivity_w_m_k"]
    fourier = diffusivity * elapsed / length**2
    slab_ratio = slab_average_ratio(biot, fourier)
    series_difference = abs(slab_ratio - slab_average_ratio(biot, fourier, terms=32))
    slab_temperature = ambient + (initial - ambient) * slab_ratio
    target_ratio = (values["target_temperature_k"] - ambient) / (initial - ambient)
    slab_target_fourier = brentq(lambda time: slab_average_ratio(biot, time) - target_ratio,
                                0.0, 10.0 / biot, xtol=1e-12)
    slab_target_time = slab_target_fourier * length**2 / diffusivity
    slab_remaining_time = max(0.0, slab_target_time - age)
    actual = estimate["cooling"]
    errors = {
        "ode_temperature_k": abs(actual["temperature_k"] - ode_temperature),
        "ode_target_time_s": abs(actual["target"]["remaining_time_s"] - ode_remaining_time),
        "slab_temperature_initial_gap_fraction": abs(actual["temperature_k"] - slab_temperature) / (initial - ambient),
        "slab_target_time_relative": abs(actual["target"]["remaining_time_s"] - slab_remaining_time) / slab_remaining_time,
        "series_32_64_term_difference": series_difference,
    }
    checks = {key: _bounded_check(value, TOLERANCES["cooling_" + key]) for key, value in errors.items()}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "biot_number": biot, "fourier_number": fourier, "errors": errors,
            "temperature_k": {"ciw_lumped": actual["temperature_k"], "scipy_lumped_ode": ode_temperature,
                              "convective_slab_volume_average": slab_temperature},
            "remaining_target_time_s": {"ciw_lumped": actual["target"]["remaining_time_s"],
                                        "scipy_lumped_ode": ode_remaining_time,
                                        "convective_slab_volume_average": slab_remaining_time}}


def run_comparisons():
    import scipy
    from ciw import fluid_reservoir_solver, polymer_models

    reservoirs = {name: compare_reservoir(request, fluid_reservoir_solver.simulate(request))
                  for name, request in reservoir_cases().items()}
    cooling = {name: compare_cooling(request, polymer_models.engineering_estimate(request))
               for name, request in cooling_cases().items()}
    return {
        "schema": "ciw.external-numerical-comparison.v1", "scope": SCOPE,
        "status": "PASS" if all(row["status"] == "PASS" for row in [*reservoirs.values(), *cooling.values()]) else "FAIL",
        "versions": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
                     "ciw": metadata.version("computational-instrumentation-workbench")},
        "checkout_sha_from_environment": os.environ.get("GITHUB_SHA"),
        "source_sha256": {name: sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for name, module in (("fluid_reservoir_solver", fluid_reservoir_solver), ("polymer_models", polymer_models))},
        "comparison_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "tolerances": TOLERANCES, "reservoir": reservoirs, "polymer_cooling": cooling,
        "limitations": ["Maintainer-authored comparison retained inside this repository; no independent external publication",
                        "Synthetic declared inputs; no sensor, material, installation or process validation",
                        "Plane-wall comparison measures spatial model discrepancy only for the listed idealized cases",
                        "Uncertainty propagation, calibration authenticity and physical applicability are not tested"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the full JSON report here; otherwise print it")
    args = parser.parse_args(argv)
    try:
        report = run_comparisons()
        # JSON must remain machine-readable even if a regression produced NaN.
        serialized = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except Exception as error:
        report = {"schema": "ciw.external-numerical-comparison.v1", "scope": SCOPE,
                  "status": "FAIL", "error": f"{type(error).__name__}: {error}"}
        serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"External numerical comparisons: {report['status']}; report: {args.output}")
    else:
        print(serialized, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
