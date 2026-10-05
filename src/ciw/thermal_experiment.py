"""Frozen-model assessment against a declared separate cooling experiment.

No fitting, controller, state admission or physical qualification. Joint
second moments are declared inputs, not metrological certification. Repeated
samples share model uncertainty: the full residual covariance is retained.
"""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np

from .control_contracts import content_ref, detached, keys, number, text
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import canonical_json, content_identity
from .operations.runner import check_seal, seal, validate_execution
from . import thermal_identification as thermal

SCHEMA = "ciw.thermal-experiment-request.v1"
RESULT_SCHEMA = "ciw.thermal-experiment-assessment.v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_SAMPLES = 128  # Dense joint covariance is intentionally bounded.
AUTHORITY = deepcopy(thermal.AUTHORITY)
ASSUMPTIONS = ["frozen_training_model_no_refit", "same_declared_boundary_and_initial_condition",
               "exact_elapsed_times_since_each_experiment_start",
               "experiment_separation_is_declared_not_independently_attested",
               "local_linearized_parameter_uncertainty"]
LIMITATIONS = ["no_calibration_authenticity_or_traceability_established",
               "no_boundary_clock_bias_or_model_discrepancy_uncertainty_added",
               "numerical_gates_are_not_physical_qualification_or_causal_diagnosis",
               "same_declared_machine_tool_material_lot_and_frame_only",
               "no_signature_or_proof_that_training_or_experiment_actually_occurred"]


def validate_request(value: dict) -> dict:
    """Validate exact occurrence/evidence bindings before numerical assessment."""
    from . import thermal_identification_workflow as workflow
    from .session import _recording_file, _validate_saved_result
    keys(value, {"schema", "training_request", "training_execution", "training_result",
                 "experiment", "dependence", "gates", "assumptions"})
    detached(value)
    if len(canonical_json(value).encode()) > MAX_BYTES or value["schema"] != SCHEMA:
        raise ValueError("Unsupported or oversized thermal experiment request")
    training = thermal.validate_request(value["training_request"])
    execution, result = value["training_execution"], value["training_result"]
    if (type(result) is not dict or result.get("operation_id") != workflow.OPERATION
            or result.get("role") != "state_estimator" or result.get("parameters") != {}):
        raise ValueError("Require the exact retained thermal identification occurrence")
    source = workflow.make_source(training)
    # Reuse NET's trusted offline occurrence validation; never reconstruct a fit.
    _validate_saved_result(result, source, 0, _recording_file(source))
    validate_execution(execution, source, 0, {result["result_id"]: result})
    if (execution["status"] != "completed" or execution["result_id"] != result["result_id"]
            or result["data"]["status"] != "candidate" or result["data"]["parameter_covariance"] is None):
        raise ValueError("Require a completed, numerically usable training candidate")
    if value["assumptions"] != ASSUMPTIONS:
        raise ValueError("Declare the exact separate-experiment assumptions")
    experiment = value["experiment"]
    keys(experiment, {"experiment_id", "identity", "source_kind", "source_ref", "calibration_ref",
                      "clock_id", "frame", "quantity", "unit", "boundary_temperature_k",
                      "samples", "observation_covariance"})
    text(experiment["experiment_id"])
    text(experiment["clock_id"])
    keys(experiment["identity"], thermal.IDENTITY_FIELDS)
    for field, item in experiment["identity"].items():
        text(item)
        if field in {"cycle_id", "part_id"}:
            if item == training["identity"][field]:
                raise ValueError("A separate experiment requires a different cycle and part")
        elif item != training["identity"][field]:
            raise ValueError("Experiment is outside the machine/tool/material applicability binding")
    for field in ("source_kind", "frame", "quantity", "unit", "boundary_temperature_k"):
        if type(experiment[field]) is not type(training[field]) or experiment[field] != training[field]:
            raise ValueError("Experiment source semantics or model applicability differs: " + field)
    content_ref(experiment["source_ref"])
    if experiment["source_ref"] == training["source_ref"]:
        raise ValueError("Training source cannot be reused as a separate experiment")
    if experiment["calibration_ref"] is not None:
        content_ref(experiment["calibration_ref"])
    samples = experiment["samples"]
    if type(samples) is not list or not 3 <= len(samples) <= MAX_SAMPLES:
        raise ValueError("Require 3..128 separately acquired experiment samples")
    ids, previous = set(), -1.0
    training_ids = {s["sample_id"] for s in training["samples"]}
    for sample in samples:
        keys(sample, {"sample_id", "time_s", "temperature_k"})
        sid = text(sample["sample_id"])
        if sid in ids or sid in training_ids:
            raise ValueError("Duplicate or reused sample identity")
        ids.add(sid)
        stamp = number(sample["time_s"])
        if not 0 <= stamp <= training["samples"][-1]["time_s"] or stamp <= previous:
            raise ValueError("Require ordered elapsed times inside the retained applicability interval")
        previous = stamp
        if not 0 < number(sample["temperature_k"]) <= 1e6:
            raise ValueError("Require positive finite absolute temperature")
    cov = experiment["observation_covariance"]
    validate_covariance_artifact(cov, expected_quantity_ids=[s["sample_id"] for s in samples],
                                expected_units=["K"] * len(samples), expected_frame=experiment["frame"])
    if (cov["reference_values"] != [s["temperature_k"] for s in samples]
            or cov["basis"] != {"kind": "observation", "id": experiment["source_ref"]}
            or cov["provenance"]["source_evidence_ids"] != [experiment["source_ref"]]):
        raise ValueError("Observation covariance must bind to the exact retained experiment")
    if any(cov["matrix"][i][i] <= 0 for i in range(len(samples))):
        raise ValueError("Require known positive observation variances, not missing uncertainty")
    dependence = value["dependence"]
    keys(dependence, {"kind", "parameter_observation_cross_covariance", "basis_ref"})
    content_ref(dependence["basis_ref"])
    kind = dependence["kind"]
    if type(kind) is not str or kind not in {"unknown", "declared_independent", "explicit_cross_covariance"}:
        raise ValueError("Declare whether parameter/observation dependence is known")
    cross = dependence["parameter_observation_cross_covariance"]
    if kind == "explicit_cross_covariance":
        if type(cross) is not list or len(cross) != 2:
            raise ValueError("Cross covariance needs two parameter rows")
        for row in cross:
            if type(row) is not list or len(row) != len(samples):
                raise ValueError("Cross covariance must match the ordered experiment samples")
            for item in row:
                number(item)
    elif cross is not None:
        raise ValueError("Only explicit cross covariance may supply a matrix")
    gates = value["gates"]
    keys(gates, {"max_rmse_k", "max_abs_bias_k", "max_whitened_rms", "max_residual_condition"})
    for item in gates.values():
        if not 0 < number(item) <= 1e12:
            raise ValueError("Invalid declared experiment gate")
    if gates["max_residual_condition"] < 1:
        raise ValueError("Residual condition limit must be at least one")
    return deepcopy(value)


def _covariance(matrix, *, axes, units, frame, reference, basis, evidence, parents, method):
    return create_covariance_artifact(matrix=np.asarray(matrix).tolist(), quantity_ids=axes, units=units,
        frame=frame, reference_values=reference, method=method, basis=basis,
        provenance={"provider": "ciw.thermal_experiment.v1", "source_evidence_ids": evidence,
                    "source_covariance_ids": parents}, assumptions=deepcopy(ASSUMPTIONS))


def assess(value: dict) -> dict:
    """Assess a frozen model; unknown dependence never silently becomes zero."""
    request = validate_request(value)
    result, experiment = request["training_result"], request["experiment"]
    model = result["data"]["model"]
    pcov = result["data"]["parameter_covariance"]
    ocov = experiment["observation_covariance"]
    samples = experiment["samples"]
    t = np.array([s["time_s"] for s in samples])
    y = np.array([s["temperature_k"] for s in samples])
    a, tau = model["initial_excess_k"], model["tau_s"]
    e = np.exp(-t / tau)
    predictions = model["boundary_temperature_k"] + a * e
    errors = y - predictions
    jacobian = np.column_stack((e, a * e * (t / tau) / tau))
    metrics = {"sample_count": len(samples), "rmse_k": float(np.sqrt(np.mean(errors ** 2))),
               "bias_k": float(np.mean(errors)), "whitened_rms": None,
               "residual_condition_number": None}
    reasons = []
    for name, magnitude, gate in (("rmse", metrics["rmse_k"], "max_rmse_k"),
                                   ("bias", abs(metrics["bias_k"]), "max_abs_bias_k")):
        if magnitude > request["gates"][gate]:
            reasons.append(name + "_gate_failed")
    rcov = None
    joint_ref = None
    standardized = [None] * len(samples)
    covariance_status = "unknown"
    dependence = request["dependence"]
    if dependence["kind"] == "unknown":
        reasons.append("parameter_observation_dependence_unknown")
    else:
        p, o = np.array(pcov["matrix"]), np.array(ocov["matrix"])
        cross = (np.zeros((2, len(samples))) if dependence["kind"] == "declared_independent"
                 else np.asarray(dependence["parameter_observation_cross_covariance"], dtype=float))
        joint = np.block([[p, cross], [cross.T, o]])
        # A plausible cross term still has to form a valid *joint* covariance.
        joint_artifact = _covariance(joint,
            axes=["parameter.initial_excess", "parameter.tau"] + ["observation." + str(i) for i in range(len(samples))],
            units=["K", "s"] + ["K"] * len(samples), frame="thermal.experiment-joint.v1",
            reference=[a, tau] + y.tolist(), basis={"kind": "parameter", "id": content_identity(request)},
            evidence=[content_identity(request)], parents=[pcov["covariance_id"], ocov["covariance_id"]],
            method="declared_joint_parameter_observation_second_moments")
        joint_ref = joint_artifact["covariance_id"]
        transform = np.column_stack((-jacobian, np.eye(len(samples))))
        matrix = transform @ joint @ transform.T
        rcov = _covariance(matrix, axes=[s["sample_id"] for s in samples], units=["K"] * len(samples),
            frame=experiment["frame"], reference=errors.tolist(),
            basis={"kind": "residual", "id": content_identity(request)},
            evidence=[content_identity(request)], parents=[joint_ref],
            method="observed_minus_frozen_prediction_full_joint_linearized_covariance")
        diagonal = np.diag(matrix)
        if np.any(diagonal <= 0):
            covariance_status = "singular"
            reasons.append("residual_covariance_not_positive_definite")
        else:
            scale = np.sqrt(diagonal)
            correlation = matrix / scale[:, None] / scale[None, :]
            # Check both stored triangles; do not silently average or repair.
            eigens = [np.linalg.eigvalsh(correlation, UPLO=triangle) for triangle in ("L", "U")]
            smallest = min(float(ev[0]) for ev in eigens)
            largest = max(float(ev[-1]) for ev in eigens)
            standardized = (errors / scale).tolist()
            if smallest <= 0:
                covariance_status = "singular"
                reasons.append("residual_covariance_not_positive_definite")
            else:
                condition = largest / smallest
                metrics["residual_condition_number"] = condition
                if condition > request["gates"]["max_residual_condition"]:
                    covariance_status = "ill_conditioned"
                    reasons.append("residual_covariance_ill_conditioned")
                else:
                    # Whitening, not diagonal standardization, accounts for
                    # correlations shared across the separate experiment trace.
                    lower = np.linalg.cholesky(correlation)
                    whitened = np.linalg.solve(lower, errors / scale)
                    metrics["whitened_rms"] = float(np.sqrt(np.mean(whitened ** 2)))
                    covariance_status = "usable"
                    if metrics["whitened_rms"] > request["gates"]["max_whitened_rms"]:
                        reasons.append("whitened_residual_gate_failed")
    failed = any(reason.endswith("_gate_failed") for reason in reasons)
    status = "rejected" if failed else "numerically_consistent" if covariance_status == "usable" else "indeterminate"
    rows = [{"sample_id": s["sample_id"], "time_s": s["time_s"], "observed_k": s["temperature_k"],
             "predicted_k": float(prediction), "residual_k": float(error),
             "marginal_standardized_residual": normalized,
             "origin": "synthetic" if experiment["source_kind"] == "synthetic" else "retained_observation"}
            for s, prediction, error, normalized in zip(samples, predictions, errors, standardized)]
    return seal({"schema": RESULT_SCHEMA, "request_ref": content_identity(request),
        "training_result_id": result["result_id"], "training_execution_id": result["execution_id"],
        "training_result_ref": result["record_digest"], "model_ref": result["data"]["model_ref"],
        "experiment_id": experiment["experiment_id"], "experiment_source_ref": experiment["source_ref"],
        "source_kind": experiment["source_kind"], "status": status, "reasons": reasons,
        "metrics": metrics, "residuals": rows, "residual_covariance": rcov,
        "joint_covariance_ref": joint_ref, "covariance_status": covariance_status,
        "dependence_kind": dependence["kind"], "work": {"fitting_evaluations": 0, "predictions": len(samples)},
        "scope": "frozen_model_separate_declared_experiment_conditional_numerical_assessment",
        "limitations": deepcopy(LIMITATIONS), "authority": deepcopy(AUTHORITY)})


def validate_result(request: dict, result: dict) -> dict:
    """Read-only recomputation of residual algebra; no identification search."""
    detached(result)
    check_seal(result)
    expected = assess(request)
    actual = deepcopy(result)
    thermal.covariance_consistent(actual["residual_covariance"], expected["residual_covariance"])
    for item in (actual, expected):
        item.pop("record_digest")
        # The joint hash is checked against the locally reconstructed inputs;
        # its floating arithmetic uses only exact retained matrix entries.
        cov = item["residual_covariance"]
        if cov is not None:
            cov.pop("covariance_id")
            cov.pop("matrix")
    thermal._numeric_equal(actual, expected)
    return deepcopy(result)


def example_request(training_request: dict, execution: dict, result: dict) -> dict:
    """A new synthetic trace for the demo; never fabricate a physical experiment."""
    if training_request["source_kind"] != "synthetic":
        raise ValueError("Synthetic experiment generation requires synthetic training evidence")
    identity = deepcopy(training_request["identity"])
    identity["cycle_id"] = "synthetic.assessment.cycle"
    identity["part_id"] = "synthetic.assessment.part"
    samples = [{"sample_id": f"synthetic.assessment.sample.{i}", "time_s": float(3 * i),
                "temperature_k": 300.0 + 80.0 * math.exp(-3 * i / 12.0)} for i in range(12)]
    ref = content_identity({"synthetic_separate_experiment": samples})
    covariance = create_covariance_artifact(matrix=(np.eye(len(samples)) * 0.04).tolist(),
        quantity_ids=[s["sample_id"] for s in samples], units=["K"] * len(samples),
        frame=training_request["frame"], reference_values=[s["temperature_k"] for s in samples],
        method="declared_synthetic_independent_temperature_errors", basis={"kind": "observation", "id": ref},
        provenance={"provider": "ciw.thermal_experiment.example", "source_evidence_ids": [ref], "source_covariance_ids": []},
        assumptions=["synthetic_numerical_fixture_not_measurements"])
    request = {"schema": SCHEMA, "training_request": deepcopy(training_request),
        "training_execution": deepcopy(execution), "training_result": deepcopy(result),
        "experiment": {"experiment_id": "synthetic.separate-cooling-experiment", "identity": identity,
            "source_kind": "synthetic", "source_ref": ref, "calibration_ref": None,
            "clock_id": "synthetic.assessment.elapsed", "frame": training_request["frame"],
            "quantity": "part_temperature", "unit": "K", "boundary_temperature_k": 300.0,
            "samples": samples, "observation_covariance": covariance},
        "dependence": {"kind": "declared_independent", "parameter_observation_cross_covariance": None,
                       "basis_ref": content_identity({"synthetic_independence_assumption": 1})},
        "gates": {"max_rmse_k": 1.0, "max_abs_bias_k": 0.5, "max_whitened_rms": 3.0,
                  "max_residual_condition": 1e8}, "assumptions": deepcopy(ASSUMPTIONS)}
    return validate_request(request)
