"""Bounded first-order cooling identification; no machine control or qualification.

Fits T(t) = boundary_temperature_k + initial_excess_k * exp(-t/tau_s).
The boundary temperature and timestamps are explicitly treated as exact inputs.
Covariance is local/conditional, not a complete metrological uncertainty budget.
"""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np

from .control_contracts import content_ref, detached, keys, number, text
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import canonical_json, content_identity
from .operations.runner import check_seal, seal

REQUEST_SCHEMA = "ciw.thermal-identification-request.v1"
RESULT_SCHEMA = "ciw.thermal-identification-result.v1"
MAX_BYTES = 2 * 1024 * 1024
MAX_SAMPLES = 4096
GRID_POINTS, REFINEMENT_STEPS = 65, 64
EVALUATIONS = GRID_POINTS + 2 + REFINEMENT_STEPS + 1
ASSUMPTIONS = ["fixed_boundary_temperature", "exact_declared_timestamps",
               "independent_gaussian_temperature_errors_with_known_standard_uncertainties",
               "single_exponential_cooling", "held_out_samples_are_from_the_same_trace"]
LIMITATIONS = ["not_an_independent_experiment_validation",
              "boundary_clock_calibration_and_model_discrepancy_uncertainty_not_propagated",
              "no_material_property_or_heat_transfer_coefficient_identification",
              "no_spatial_temperature_field_or_crystallization_model",
              "no_global_optimality_certificate", "no_physical_qualification_or_actuation"]
AUTHORITY = {"physical_validation": "not_established", "calibration_traceability": "not_established",
             "state_admission": "not_performed", "hardware_actuation": "not_performed",
             "production_release": "not_performed"}
IDENTITY_FIELDS = {"facility_id", "machine_id", "tool_id", "material_lot_id", "cycle_id", "part_id"}


def validate_request(value: dict) -> dict:
    keys(value, {"schema", "identity", "source_kind", "source_ref", "calibration_ref",
                 "clock_id", "frame", "quantity", "unit", "boundary_temperature_k",
                 "tau_bounds_s", "gates", "assumptions", "samples"})
    detached(value)
    if len(canonical_json(value).encode()) > MAX_BYTES:
        raise ValueError("Thermal identification request exceeds byte budget")
    if value["schema"] != REQUEST_SCHEMA:
        raise ValueError("Unsupported thermal identification schema")
    if type(value["source_kind"]) is not str or value["source_kind"] not in {"synthetic", "retained_observation"}:
        raise ValueError("Declare synthetic or retained observation evidence")
    keys(value["identity"], IDENTITY_FIELDS)
    for item in value["identity"].values():
        text(item)
    for field in ("clock_id", "frame"):
        text(value[field])
    content_ref(value["source_ref"])
    if value["calibration_ref"] is not None:
        content_ref(value["calibration_ref"])
    if value["quantity"] != "part_temperature" or value["unit"] != "K":
        raise ValueError("Require part_temperature in explicit absolute kelvin")
    if value["assumptions"] != ASSUMPTIONS:
        raise ValueError("The conditional inference assumptions must be explicit and exact")
    if not 0 < number(value["boundary_temperature_k"]) <= 1e6:
        raise ValueError("Invalid absolute boundary temperature")
    bounds = value["tau_bounds_s"]
    if type(bounds) is not list or len(bounds) != 2:
        raise ValueError("Require two explicit time-constant bounds")
    low, high = map(number, bounds)
    if not 1e-6 <= low < high <= 1e6 or high / low < 1.0001:
        raise ValueError("Require finite separated time-constant bounds in seconds")
    gates = value["gates"]
    keys(gates, {"min_signal_to_noise", "max_condition_number", "max_training_rms_z", "max_holdout_rms_z"})
    for item in gates.values():
        if not 0 < number(item) <= 1e8:
            raise ValueError("Invalid diagnostic gate")
    if gates["max_condition_number"] < 1:
        raise ValueError("Condition-number gate must be at least one")
    samples = value["samples"]
    if type(samples) is not list or not 9 <= len(samples) <= MAX_SAMPLES:
        raise ValueError("Require 9..4096 thermal samples")
    previous, seen_holdout, ids = -1.0, False, set()
    train_count = holdout_count = 0
    for sample in samples:
        keys(sample, {"sample_id", "time_s", "temperature_k", "standard_uncertainty_k", "split"})
        sid = text(sample["sample_id"])
        if sid in ids:
            raise ValueError("Duplicate sample identity")
        ids.add(sid)
        stamp = number(sample["time_s"])
        if not previous < stamp <= 1e9:
            raise ValueError("Samples must have strictly increasing nonnegative times")
        previous = stamp
        if not 0 < number(sample["temperature_k"]) <= 1e6:
            raise ValueError("Invalid absolute temperature sample")
        if not 1e-9 <= number(sample["standard_uncertainty_k"]) <= 1e4:
            raise ValueError("Known positive measurement uncertainty is required; no zero imputation")
        if sample["split"] == "training" and not seen_holdout:
            train_count += 1
        elif sample["split"] == "holdout":
            seen_holdout = True
            holdout_count += 1
        else:
            raise ValueError("Declare a chronological training/holdout split before fitting")
    if samples[0]["time_s"] != 0 or train_count < 6 or holdout_count < 3:
        raise ValueError("Require t=0, at least six training and three held-out samples")
    return deepcopy(value)


def _arrays(request: dict, split: str):
    samples = [s for s in request["samples"] if s["split"] == split]
    return tuple(np.asarray([s[key] for s in samples], dtype=float)
                 for key in ("time_s", "temperature_k", "standard_uncertainty_k"))


def _profile(log_tau: float, t, y, sigma, boundary: float):
    tau = math.exp(log_tau)
    e = np.exp(-t / tau)
    ew = e / sigma
    amplitude = float(np.dot(ew, (y - boundary) / sigma) / np.dot(ew, ew))
    z = (y - boundary - amplitude * e) / sigma
    return float(np.dot(z, z)), amplitude, tau


def _summarize(request: dict, amplitude: float, tau: float) -> dict:
    t, y, sigma = _arrays(request, "training")
    e = np.exp(-t / tau)
    # Dimensionless parameter coordinates make the condition test independent
    # of the raw Kelvin/second scale disparity. The covariance retains SI units.
    scales = np.array([max(abs(amplitude), 1.0), tau])
    jacobian = np.column_stack((e, amplitude * e * (t / tau) / tau))
    jw = jacobian * scales / sigma[:, None]
    _, singular, vt = np.linalg.svd(jw, full_matrices=False)
    rank = int(np.count_nonzero(singular > singular[0] * 1e-12))
    condition = float(singular[0] / singular[-1]) if rank == 2 else None
    snr = float((np.max(y) - np.min(y)) / np.max(sigma))
    reasons = []
    low, high = request["tau_bounds_s"]
    if amplitude <= 0:
        reasons.append("non_cooling_candidate")
    if tau <= low * (1 + 1e-6) or tau >= high * (1 - 1e-6):
        reasons.append("bound_limited_candidate")
    if snr < request["gates"]["min_signal_to_noise"]:
        reasons.append("insufficient_thermal_excitation")
    if rank < 2:
        reasons.append("rank_deficient_parameter_jacobian")
    elif condition > request["gates"]["max_condition_number"]:
        reasons.append("ill_conditioned_parameter_jacobian")
    evaluations = {}
    rows = []
    for split in ("training", "holdout"):
        ts, ys, us = _arrays(request, split)
        predictions = request["boundary_temperature_k"] + amplitude * np.exp(-ts / tau)
        errors = ys - predictions
        z = errors / us
        evaluations[split] = {"sample_count": len(ts), "rmse_k": float(np.sqrt(np.mean(errors ** 2))),
                              "rms_z": float(np.sqrt(np.mean(z ** 2)))}
        if evaluations[split]["rms_z"] > request["gates"]["max_" + split + "_rms_z"]:
            reasons.append(split + "_residual_gate_failed")
        samples = [s for s in request["samples"] if s["split"] == split]
        rows.extend({"sample_id": s["sample_id"], "split": split, "time_s": s["time_s"],
                     "predicted_k": float(p), "residual_k": float(r), "standardized_residual": float(zz)}
                    for s, p, r, zz in zip(samples, predictions, errors, z))
    model = {"kind": "fixed_boundary_single_exponential.v1", "initial_excess_k": amplitude,
             "tau_s": tau, "boundary_temperature_k": request["boundary_temperature_k"]}
    model_ref = content_identity({"request_ref": content_identity(request), "model": model})
    covariance = None
    if rank == 2 and condition <= request["gates"]["max_condition_number"]:
        inverse = vt.T / singular
        matrix = (inverse @ inverse.T) * scales[:, None] * scales[None, :]
        covariance = create_covariance_artifact(matrix=matrix.tolist(),
            quantity_ids=["initial_excess_temperature", "thermal_time_constant"], units=["K", "s"],
            frame="thermal.parameter.v1", reference_values=[amplitude, tau],
            method="local_linearized_known_noise_weighted_least_squares_svd",
            basis={"kind": "parameter", "id": model_ref},
            provenance={"provider": "ciw.thermal_identification.v1",
                        "source_evidence_ids": [content_identity(request)], "source_covariance_ids": []},
            assumptions=deepcopy(ASSUMPTIONS))
    return seal({"schema": RESULT_SCHEMA, "request_ref": content_identity(request), "model_ref": model_ref,
        "identity": deepcopy(request["identity"]), "source_kind": request["source_kind"],
        "status": "rejected" if reasons else "candidate", "reasons": reasons, "model": model,
        "parameter_covariance": covariance, "uncertainty_scope": "local_conditional_parameter_covariance",
        "diagnostics": {"parameter_rank": rank, "condition_number": condition,
                        "condition_coordinates": "initial_excess_scale_and_tau_scale",
                        "signal_to_noise": snr, "degrees_of_freedom": len(t) - 2, **evaluations},
        "residuals": rows, "applicability": {"time_interval_s": [0.0, request["samples"][-1]["time_s"]],
                        "identity": deepcopy(request["identity"]), "boundary_temperature_k": request["boundary_temperature_k"]},
        "work": {"profile_evaluations": EVALUATIONS, "grid_points": GRID_POINTS,
                 "refinement_steps": REFINEMENT_STEPS},
        "limitations": deepcopy(LIMITATIONS), "authority": deepcopy(AUTHORITY)})


def fit(value: dict) -> dict:
    """Run a fixed-budget profile search; retain rejected candidates with reasons."""
    request = validate_request(value)
    t, y, sigma = _arrays(request, "training")
    low, high = map(math.log, request["tau_bounds_s"])
    grid = np.linspace(low, high, GRID_POINTS)
    evaluate = lambda x: _profile(x, t, y, sigma, request["boundary_temperature_k"])
    results = [evaluate(float(x)) for x in grid]
    best = min(range(GRID_POINTS), key=lambda i: results[i][0])
    left, right = float(grid[max(0, best - 1)]), float(grid[min(GRID_POINTS - 1, best + 1)])
    ratio = (math.sqrt(5) - 1) / 2
    a, b = right - ratio * (right - left), left + ratio * (right - left)
    fa, fb = evaluate(a), evaluate(b)
    for _ in range(REFINEMENT_STEPS):
        if fa[0] <= fb[0]:
            right, b, fb = b, a, fa
            a = right - ratio * (right - left)
            fa = evaluate(a)
        else:
            left, a, fa = a, b, fb
            b = left + ratio * (right - left)
            fb = evaluate(b)
    final = evaluate((left + right) / 2)
    _, amplitude, tau = min((results[best], fa, fb, final), key=lambda item: item[0])
    # exp(log(bound)) can round outside an endpoint; only the selected endpoint
    # is restored to its originally declared value. No fitted interior is clipped.
    if tau < request["tau_bounds_s"][0]:
        tau = request["tau_bounds_s"][0]
    elif tau > request["tau_bounds_s"][1]:
        tau = request["tau_bounds_s"][1]
    return _summarize(request, amplitude, tau)


def _numeric_equal(actual, expected, path="report") -> None:
    """Float roundoff tolerance never changes identities, roles or gate decisions."""
    if type(expected) is float:
        if type(actual) not in (int, float) or not math.isclose(number(actual), expected, rel_tol=5e-11, abs_tol=1e-11):
            raise ValueError("Thermal numerical consistency differs at " + path)
    elif type(expected) is dict:
        keys(actual, set(expected))
        for key in expected:
            _numeric_equal(actual[key], expected[key], path + "." + key)
    elif type(expected) is list:
        if type(actual) is not list or len(actual) != len(expected):
            raise ValueError("Thermal sequence differs at " + path)
        for index, (a, b) in enumerate(zip(actual, expected)):
            _numeric_equal(a, b, path + "." + str(index))
    elif type(actual) is not type(expected) or actual != expected:
        raise ValueError("Thermal exact semantics differ at " + path)


def covariance_consistent(actual: dict | None, expected: dict | None) -> None:
    """Compare covariance in dimensionless coordinates, never absolute SI floors.

    A variance of 1e-20 must not be replaceable by zero merely because it is
    smaller than a generic 1e-11 numerical tolerance. Hashes remain byte-exact;
    this comparison is separate from each artifact's integrity validation.
    """
    if actual is None or expected is None:
        if actual is not expected:
            raise ValueError("Covariance availability differs")
        return
    for artifact in (actual, expected):
        validate_covariance_artifact(artifact)
    if (actual["quantity_ids"] != expected["quantity_ids"]
            or actual["units"] != expected["units"] or actual["frame"] != expected["frame"]):
        raise ValueError("Covariance axes, units or frame differ")
    a, b = (np.asarray(item["matrix"], dtype=float) for item in (actual, expected))
    diagonal = np.diag(b)
    for index in np.flatnonzero(diagonal == 0):
        if np.any(a[index] != 0) or np.any(a[:, index] != 0):
            raise ValueError("An exact zero-variance coordinate differs")
    active = np.flatnonzero(diagonal > 0)
    if len(active):
        scale = np.sqrt(diagonal[active])
        an = a[np.ix_(active, active)] / scale[:, None] / scale[None, :]
        bn = b[np.ix_(active, active)] / scale[:, None] / scale[None, :]
        if not np.allclose(an, bn, rtol=5e-11, atol=1e-12):
            raise ValueError("Covariance differs in dimensionless coordinates")


def validate_result(request: dict, result: dict) -> dict:
    """Check retained bindings and algebra, WITHOUT running the fit/search again.

A valid content seal is not proof of origin or physical truth. This review does
not establish search optimality or reproducibility; explicit replay does a fit.
"""
    request = validate_request(request)
    detached(result)
    check_seal(result)
    model = result.get("model")
    keys(model, {"kind", "initial_excess_k", "tau_s", "boundary_temperature_k"})
    amplitude, tau = number(model["initial_excess_k"]), number(model["tau_s"])
    if not request["tau_bounds_s"][0] <= tau <= request["tau_bounds_s"][1] or abs(amplitude) > 1e7:
        raise ValueError("Model parameters violate hard bounds")
    expected = _summarize(request, amplitude, tau)
    actual = deepcopy(result)
    covariance_consistent(actual["parameter_covariance"], expected["parameter_covariance"])
    for item in (actual, expected):
        item.pop("record_digest", None)
        cov = item["parameter_covariance"]
        if cov is not None:
            validate_covariance_artifact(cov, expected_quantity_ids=["initial_excess_temperature", "thermal_time_constant"],
                                        expected_units=["K", "s"], expected_frame="thermal.parameter.v1")
            cov.pop("covariance_id")
            cov.pop("matrix")  # Already checked at its own physical scale above.
    _numeric_equal(actual, expected)
    return deepcopy(result)


def predict(request: dict, result: dict, time_s: float) -> dict:
    """A bounded conditional mean prediction, not a measurement prediction interval."""
    request = validate_request(request)
    result = validate_result(request, result)
    if result["status"] != "candidate" or result["parameter_covariance"] is None:
        raise ValueError("Rejected or unidentified candidates cannot predict")
    time_s = number(time_s)
    if not 0 <= time_s <= result["applicability"]["time_interval_s"][1]:
        raise ValueError("Prediction extrapolation beyond the retained trace is forbidden")
    a, tau = result["model"]["initial_excess_k"], result["model"]["tau_s"]
    e = math.exp(-time_s / tau)
    j = np.array([e, a * e * (time_s / tau) / tau])
    matrix = np.array(result["parameter_covariance"]["matrix"])
    variance = float(j @ matrix @ j)
    if variance < 0 or not math.isfinite(variance):
        raise ValueError("Invalid propagated conditional variance")
    return {"time_s": time_s, "temperature_k": request["boundary_temperature_k"] + a * e,
            "standard_uncertainty_k": math.sqrt(variance),
            "uncertainty_scope": "conditional_mean_parameter_only", "model_ref": result["model_ref"],
            "source_kind": request["source_kind"], "authority": deepcopy(AUTHORITY)}


def example_request() -> dict:
    """Deterministic synthetic numerical fixture; not a processing recipe."""
    samples = [{"sample_id": f"synthetic.sample.{i}", "time_s": float(i * 2),
                "temperature_k": 300.0 + 80.0 * math.exp(-i * 2 / 12.0),
                "standard_uncertainty_k": 0.2, "split": "training" if i < 18 else "holdout"}
               for i in range(24)]
    return {"schema": REQUEST_SCHEMA, "identity": {key: "synthetic." + key for key in sorted(IDENTITY_FIELDS)},
            "source_kind": "synthetic", "source_ref": content_identity(samples), "calibration_ref": None,
            "clock_id": "synthetic.elapsed_seconds", "frame": "synthetic.part.v1",
            "quantity": "part_temperature", "unit": "K", "boundary_temperature_k": 300.0,
            "tau_bounds_s": [1.0, 100.0], "gates": {"min_signal_to_noise": 5.0,
                "max_condition_number": 1e6, "max_training_rms_z": 3.0, "max_holdout_rms_z": 3.0},
            "assumptions": deepcopy(ASSUMPTIONS), "samples": samples}
