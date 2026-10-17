"""Fixed-grid, nonnegative Maxwell-spectrum candidates from declared modulus data.

Reuses NET evidence, covariance and occurrence contracts. Times/temperature are
conditioned on, not fitted. No calibration, parameter covariance or admission.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import platform
import sys

import numpy as np

from .control_contracts import content_ref, detached, keys, number, text
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import content_identity, new_identity, validate_identity
from .operations.runner import check_seal, seal
from . import polymer_rheology as forward

REQUEST_SCHEMA = "ciw.polymer-rheology-identification-request.v1"
RESULT_SCHEMA = "ciw.polymer-rheology-identification-result.v1"
OPERATION = "polymer.rheology.identify-fixed-grid.v1"
MAX_MODES, MAX_SAMPLES = 8, 128
CONDITION_LIMIT, SOLVER_TOL, KKT_TOL = 1e8, 1e-12, 2e-9
MAX_ITER = 200
QUANTITIES = {"relaxation_modulus_pa": "s", "storage_modulus_pa": "rad/s", "loss_modulus_pa": "rad/s"}
AUTHORITY = {"physical_validation": False, "parameter_uncertainty": "not_assessed",
             "state_admission": False, "hardware_actuation": False,
             "calibration_traceability": False}
ASSUMPTIONS = [
    "All rows describe the same uniaxial linear isothermal parameter set; no shear/Young conversion.",
    "Relaxation times are fixed by the request before fitting; only nonnegative moduli are inferred.",
    "Coordinates, temperature and model form are conditioned on; errors in these quantities are not propagated.",
    "Covariance is caller-declared modulus covariance; authenticity and calibration are not established.",
    "Only the training marginal covariance enters fitting; holdout data never choose coefficients.",
    "Holdout is a declared data partition, not proof of independent acquisition or statistical independence.",
    "Active constraints, fixed-grid approximation and model discrepancy preclude a parameter-coverage claim.",
]
SOURCE_MODULES = ("ciw.polymer_rheology_identification", "ciw.polymer_rheology_identification_reference",
                  "ciw.polymer_rheology", "ciw.control_contracts", "ciw.core.covariance",
                  "ciw.core.identities", "ciw.operations.runner")


def _bound(value, lo, hi, label):
    x = number(value)
    if not lo <= x <= hi:
        raise ValueError(f"{label} outside [{lo}, {hi}]")
    return x


def validate_request(request):
    keys(request, {"schema", "model", "material_id", "source_kind", "source_ref", "frame", "temperature_k",
                   "relaxation_times_s", "samples", "covariance", "holdout_max_abs_error_pa"})
    p = detached(request)
    if p["schema"] != REQUEST_SCHEMA or p["model"] != forward.MODEL:
        raise ValueError("Unsupported spectrum-identification schema or model")
    text(p["material_id"])
    text(p["frame"])
    content_ref(p["source_ref"])
    if p["source_kind"] not in ("synthetic", "caller_declared"):
        raise ValueError("Source declarations do not confer measurement qualification")
    p["temperature_k"] = _bound(p["temperature_k"], 200, 600, "temperature")
    p["holdout_max_abs_error_pa"] = _bound(p["holdout_max_abs_error_pa"], 0, 1e12, "holdout limit")
    taus = p["relaxation_times_s"]
    if type(taus) is not list or not 1 <= len(taus) <= MAX_MODES:
        raise ValueError("Require 1 to 8 fixed relaxation times")
    p["relaxation_times_s"] = [_bound(x, 1e-9, 1e9, "relaxation time") for x in taus]
    if any(b <= a for a, b in zip(taus, taus[1:])):
        raise ValueError("Relaxation grid must strictly increase")
    rows = p["samples"]
    if type(rows) is not list or not 5 <= len(rows) <= MAX_SAMPLES:
        raise ValueError("Require 5 to 128 declared samples")
    seen, train, holdout = set(), 0, 0
    for row in rows:
        keys(row, {"sample_id", "partition", "quantity", "coordinate", "coordinate_unit", "value_pa"})
        identity = text(row["sample_id"])
        if identity in seen:
            raise ValueError("Sample identities must be unique across both partitions")
        seen.add(identity)
        if row["partition"] == "train":
            train += 1
        elif row["partition"] == "holdout":
            holdout += 1
        else:
            raise ValueError("Declare train or holdout for every sample")
        if type(row["quantity"]) is not str or row["quantity"] not in QUANTITIES:
            raise ValueError("Only uniaxial relaxation, storage and loss moduli are supported")
        if row["coordinate_unit"] != QUANTITIES[row["quantity"]]:
            raise ValueError("Coordinate unit must be s or rad/s as required; no implicit Hz conversion")
        row["coordinate"] = _bound(row["coordinate"], 0, 1e9 if row["coordinate_unit"] == "s" else 1e12,
                                   "sample coordinate")
        if 0 < row["coordinate"] < 1e-12:
            raise ValueError("Positive coordinates must be at least 1e-12")
        row["value_pa"] = _bound(row["value_pa"], -1e13, 1e13, "observed modulus")
    if train < len(taus) + 2 or holdout < 2:
        raise ValueError("Require at least parameters+1 training rows and two holdout rows")
    cov = p["covariance"]
    validate_covariance_artifact(cov, expected_quantity_ids=[r["sample_id"] for r in rows],
                                 expected_units=["Pa"] * len(rows), expected_frame=p["frame"])
    if (cov["reference_values"] != [r["value_pa"] for r in rows]
            or cov["basis"] != {"kind": "observation", "id": p["source_ref"]}
            or p["source_ref"] not in cov["provenance"]["source_evidence_ids"]):
        raise ValueError("Covariance reference values or source binding differs")
    # This GLS profile is stricter than the generic PSD artifact: no arbitrary
    # triangle selection, jitter, zero-variance regularization or pseudoinverse.
    matrix = cov["matrix"]
    for i, row in enumerate(matrix):
        _bound(row[i], 1e-24, 1e24, "modulus variance")
        if any(row[j] != matrix[j][i] for j in range(len(rows))):
            raise ValueError("Identification covariance must be exactly symmetric")
    correlation, _ = _correlation(matrix)
    try:
        np.linalg.cholesky(correlation)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Identification requires positive-definite covariance; no repair") from exc
    return p


def _correlation(matrix):
    c = np.asarray(matrix, dtype=float)
    sigma = np.sqrt(np.diag(c))
    correlation = (c / sigma[:, None]) / sigma[None, :]
    return correlation, sigma


def _basis(p):
    """Dimensionless fixed-grid design; no experimental unit conversion."""
    rows = []
    for sample in p["samples"]:
        x, q = sample["coordinate"], sample["quantity"]
        values = [0.0 if q == "loss_modulus_pa" else 1.0]
        for tau in p["relaxation_times_s"]:
            if q == "relaxation_modulus_pa":
                values.append(math.exp(-x / tau))
            else:
                z = x * tau
                if z <= 1:
                    values.append(z * z / (1 + z*z) if q == "storage_modulus_pa" else z / (1 + z*z))
                else:
                    u = 1 / z
                    values.append(1 / (1 + u*u) if q == "storage_modulus_pa" else u / (1 + u*u))
        rows.append(values)
    return np.asarray(rows)


def _system(p):
    train = [i for i, row in enumerate(p["samples"]) if row["partition"] == "train"]
    matrix = np.asarray(p["covariance"]["matrix"])[np.ix_(train, train)]
    corr, sigma = _correlation(matrix)
    lower = np.linalg.cholesky(corr)
    a = np.linalg.solve(lower, _basis(p)[train] / sigma[:, None])
    b = np.linalg.solve(lower, np.array([p["samples"][i]["value_pa"] for i in train]) / sigma)
    column = np.linalg.norm(a, axis=0)
    if not np.all(np.isfinite(column)) or np.any(column == 0):
        raise ValueError("The training design cannot identify every requested coefficient")
    design = a / column
    singular = np.linalg.svd(design, compute_uv=False)
    if singular[-1] <= 0 or singular[0] / singular[-1] > CONDITION_LIMIT:
        raise ValueError("Training design is rank-deficient or ill-conditioned on this fixed grid")
    scale = max(abs(b)) or 1.0  # Coordinate normalization only, not a tolerance floor.
    return design, b / scale, column, scale, float(singular[0] / singular[-1])


def _predict(p, coefficients):
    # Use the existing production model, including its weighted-underflow path.
    result = []
    for row in p["samples"]:
        x, q = row["coordinate"], row["quantity"]
        if q == "relaxation_modulus_pa":
            terms = [coefficients[0], *(forward._weighted_decay(e, x / tau)
                     for e, tau in zip(coefficients[1:], p["relaxation_times_s"]))]
        else:
            terms = [0.0 if q == "loss_modulus_pa" else coefficients[0]]
            for e, tau in zip(coefficients[1:], p["relaxation_times_s"]):
                z = x * tau
                if z <= 1:
                    terms.append(e * z*z / (1 + z*z) if q == "storage_modulus_pa" else e * z / (1 + z*z))
                else:
                    u = 1 / z
                    terms.append(e / (1 + u*u) if q == "storage_modulus_pa" else e * u / (1 + u*u))
        result.append(math.fsum(terms))
    return result


def _metrics(p, predictions):
    metrics = {}
    for partition in ("train", "holdout"):
        errors = [actual - row["value_pa"] for row, actual in zip(p["samples"], predictions)
                  if row["partition"] == partition]
        metrics[partition] = {"count": len(errors), "rmse_pa": math.sqrt(math.fsum(x*x for x in errors) / len(errors)),
                              "max_abs_error_pa": max(map(abs, errors))}
    return metrics


def _candidate(p, coefficients):
    if any(0 < x < 1e-6 for x in coefficients[1:]):
        return None, "positive-mode-below-existing-forward-envelope"
    if not any(coefficients):
        return None, "no-positive-stiffness"
    taus = p["relaxation_times_s"]
    return forward.validate_request({"schema": forward.REQUEST_SCHEMA, "model": forward.MODEL,
        "material_id": p["material_id"], "source_kind": p["source_kind"],
        "parameter_source_ref": content_identity({"identification_request": p, "coefficients_pa": coefficients}),
        "temperature_k": p["temperature_k"], "equilibrium_modulus_pa": coefficients[0],
        "modes": [{"mode_id": f"fixed-{i}", "modulus_pa": e, "relaxation_time_s": tau}
                  for i, (e, tau) in enumerate(zip(coefficients[1:], taus)) if e > 0],
        "step_strain": 0.001, "time_s": [0.0, min(1e9, 10 * taus[-1])],
        "angular_frequency_rad_s": sorted({0.0, 1 / taus[-1], 1 / taus[0]})}), None


def runtime_identity():
    from . import polymer_rheology_identification_reference  # Ensure inventory is loaded.
    return {"python": platform.python_version(), "numpy": np.__version__,
            "source_normalization": "utf8_lf", "source_refs": {
                name: "sha256:" + sha256(Path(sys.modules[name].__file__).read_text(encoding="utf-8")
                                        .replace("\r\n", "\n").encode()).hexdigest()
                for name in SOURCE_MODULES}}


def identify(request):
    p = validate_request(request)
    try:
        import scipy
        from scipy.optimize import lsq_linear
    except ImportError as exc:
        raise ValueError("Identification requires the existing numerical-checks extra (SciPy)") from exc
    try:
        design, rhs, column, scale, condition = _system(p)
        fit = lsq_linear(design, rhs, bounds=(0.0, np.inf), method="bvls", tol=SOLVER_TOL, max_iter=MAX_ITER,
                         lsq_solver="exact")
    except (np.linalg.LinAlgError, RuntimeError) as exc:
        raise ValueError("Identification linear algebra did not converge") from exc
    if not fit.success or not np.all(np.isfinite(fit.x)) or np.any(fit.x < 0):
        raise ValueError("Bounded least-squares solver did not return an admissible solution")
    coefficients = (fit.x * scale / column).tolist()
    for e in coefficients:
        _bound(e, 0, 1e12, "fitted modulus")
    predictions = _predict(p, coefficients)
    metrics = _metrics(p, predictions)
    candidate, refusal = _candidate(p, coefficients)
    result = seal({"schema": RESULT_SCHEMA, "operation_id": OPERATION, "request": p,
        "evidence_id": content_identity(p), "execution_id": new_identity("execution"),
        "result_id": new_identity("result"), "runtime": runtime_identity(),
        "coefficients_pa": coefficients, "predicted_modulus_pa": predictions, "metrics": metrics,
        "holdout_status": "WITHIN_DECLARED_LIMIT" if metrics["holdout"]["max_abs_error_pa"] <= p["holdout_max_abs_error_pa"]
                          else "OUTSIDE_DECLARED_LIMIT",
        "solver": {"algorithm": "scipy-lsq-linear-bvls", "scipy": scipy.__version__, "iterations": int(fit.nit),
                   "termination_status": int(fit.status), "max_iter": MAX_ITER, "tol": SOLVER_TOL,
                   "scaled_design_condition": condition},
        "candidate_response_request": candidate, "candidate_export_refusal": refusal,
        "verification_status": "not_verified", "assumptions": list(ASSUMPTIONS), "authority": deepcopy(AUTHORITY)})
    validate_result(result)
    # A solver status alone cannot issue a usable candidate. Independently check
    # the frozen coefficients, response and convex KKT conditions without refit.
    _, failures = _audit(result)
    if failures:
        raise ValueError("Independent fixed-grid solution audit failed: " + ", ".join(failures))
    return result


def validate_result(result):
    """Retained shape/binding/algebraic inspection only; no fit or response execution."""
    detached(result)
    check_seal(result)
    keys(result, {"schema", "operation_id", "request", "evidence_id", "execution_id", "result_id", "runtime",
                  "coefficients_pa", "predicted_modulus_pa", "metrics", "holdout_status", "solver",
                  "candidate_response_request", "candidate_export_refusal", "verification_status", "assumptions",
                  "authority", "record_digest"})
    p = validate_request(result["request"])
    if (result["schema"] != RESULT_SCHEMA or result["operation_id"] != OPERATION
            or result["evidence_id"] != content_identity(p) or result["verification_status"] != "not_verified"
            or content_identity(result["authority"]) != content_identity(AUTHORITY) or result["assumptions"] != ASSUMPTIONS):
        raise ValueError("Identification schema, evidence or authority binding differs")
    for kind in ("execution", "result"):
        validate_identity(result[kind + "_id"], kind)
    coefficients = result["coefficients_pa"]
    if type(coefficients) is not list or len(coefficients) != len(p["relaxation_times_s"]) + 1:
        raise ValueError("Coefficient dimensions differ")
    for e in coefficients:
        _bound(e, 0, 1e12, "fitted modulus")
    predictions = result["predicted_modulus_pa"]
    if type(predictions) is not list or len(predictions) != len(p["samples"]):
        raise ValueError("Prediction dimensions differ")
    for value in predictions:
        _bound(value, 0, 1e13, "prediction")
    expected_metrics = _metrics(p, predictions)
    # The same retained arithmetic defines these descriptive metrics. No model
    # is evaluated and no uncertain physical decision is inferred here.
    keys(result["metrics"], {"train", "holdout"})
    for partition, expected in expected_metrics.items():
        retained = result["metrics"][partition]
        keys(retained, {"count", "rmse_pa", "max_abs_error_pa"})
        if (type(retained["count"]) is not int or retained["count"] != expected["count"]
                or _bound(retained["max_abs_error_pa"], 0, 2e13, "maximum residual") != expected["max_abs_error_pa"]
                or not math.isclose(_bound(retained["rmse_pa"], 0, 2e13, "RMSE"), expected["rmse_pa"],
                                    rel_tol=1e-14, abs_tol=0.0)):
            raise ValueError("Retained residual metrics differ")
    expected_status = ("WITHIN_DECLARED_LIMIT" if expected_metrics["holdout"]["max_abs_error_pa"] <= p["holdout_max_abs_error_pa"]
                       else "OUTSIDE_DECLARED_LIMIT")
    if result["holdout_status"] != expected_status:
        raise ValueError("Holdout decision differs from retained errors")
    candidate, refusal = _candidate(p, coefficients)
    if (content_identity(result["candidate_response_request"]) != content_identity(candidate)
            or result["candidate_export_refusal"] != refusal):
        raise ValueError("Existing-forward-model export differs from the retained coefficients")
    solver = result["solver"]
    keys(solver, {"algorithm", "scipy", "iterations", "termination_status", "max_iter", "tol", "scaled_design_condition"})
    if (solver["algorithm"] != "scipy-lsq-linear-bvls" or type(solver["iterations"]) is not int
            or not 0 <= solver["iterations"] <= MAX_ITER + MAX_MODES + 1
            or type(solver["termination_status"]) is not int or solver["termination_status"] not in (1, 2, 3)
            or type(solver["max_iter"]) is not int or solver["max_iter"] != MAX_ITER
            or type(solver["tol"]) is not float or solver["tol"] != SOLVER_TOL):
        raise ValueError("Solver declaration is invalid")
    text(solver["scipy"])
    _bound(solver["scaled_design_condition"], 1, CONDITION_LIMIT, "design condition")
    runtime = result["runtime"]
    keys(runtime, {"python", "numpy", "source_normalization", "source_refs"})
    for key in ("python", "numpy"):
        text(runtime[key])
    if runtime["source_normalization"] != "utf8_lf":
        raise ValueError("Runtime source normalization differs")
    keys(runtime["source_refs"], set(SOURCE_MODULES))
    for ref in runtime["source_refs"].values():
        content_ref(ref)
    return result


def _audit(result):
    from decimal import DecimalException
    from .polymer_rheology_identification_reference import audit_solution
    try:
        return audit_solution(result, KKT_TOL)
    except (np.linalg.LinAlgError, DecimalException) as exc:
        raise ValueError("Independent identification audit is numerically unsupported") from exc


def verify(result):
    """Independent Decimal response/GLS stationarity audit, never refits or rewrites."""
    validate_result(result)
    metrics, failures = _audit(result)
    return seal({"schema": "ciw.polymer-rheology-identification-verification.v1",
        "verification_id": new_identity("verification"), "result_id": result["result_id"],
        "execution_id": result["execution_id"], "evidence_id": result["evidence_id"],
        "result_record_digest": result["record_digest"], "status": "FAIL" if failures else "PASS",
        "failures": failures, "metrics": metrics, "reference": "decimal80-design-and-gls-kkt.v1",
        "policy": {"kkt_tolerance": KKT_TOL, "prediction_relative_tolerance": 2e-12, "ulp_allowance": 8},
        "scope": "fixed-grid-numerical-consistency-not-physical-validation", "runtime": runtime_identity(),
        "authority": deepcopy(AUTHORITY)})


def example_request():
    rows = []
    for q, coordinates in (("relaxation_modulus_pa", [0, .02, .1, .4, 1, 4, 10, 40]),
                           ("storage_modulus_pa", [.01, .1, 1, 10]), ("loss_modulus_pa", [.01, .1, 1, 10])):
        for i, x in enumerate(coordinates):
            if q == "relaxation_modulus_pa":
                value = 1e6 + 2e6 * math.exp(-x / .1) + 3e6 * math.exp(-x / 10)
            else:
                value = 1e6 if q == "storage_modulus_pa" else 0.0
                for e, tau in ((2e6, .1), (3e6, 10.0)):
                    z = x * tau
                    value += e * (z*z if q == "storage_modulus_pa" else z) / (1 + z*z)
            rows.append({"sample_id": f"synthetic-{len(rows)}", "partition": "holdout" if i % 4 == 3 else "train",
                         "quantity": q, "coordinate": float(x), "coordinate_unit": QUANTITIES[q], "value_pa": value})
    source = content_identity({"fixture": "synthetic-fixed-grid-spectrum-v1", "samples": rows})
    covariance = create_covariance_artifact(matrix=[[10000.0 if i == j else 0.0 for j in range(len(rows))]
                          for i in range(len(rows))], quantity_ids=[r["sample_id"] for r in rows],
        units=["Pa"] * len(rows), frame="uniaxial-test-frame", reference_values=[r["value_pa"] for r in rows],
        method="authored-diagonal-synthetic-covariance", basis={"kind": "observation", "id": source},
        provenance={"provider": "synthetic-fixture", "source_evidence_ids": [source], "source_covariance_ids": []},
        assumptions=["Authored synthetic values, not acquired instrument measurements."])
    return {"schema": REQUEST_SCHEMA, "model": forward.MODEL, "material_id": "synthetic.polymer.spectrum",
            "source_kind": "synthetic", "source_ref": source, "frame": "uniaxial-test-frame", "temperature_k": 300.0,
            "relaxation_times_s": [.1, 10.0], "samples": rows, "covariance": covariance,
            "holdout_max_abs_error_pa": 1.0}
