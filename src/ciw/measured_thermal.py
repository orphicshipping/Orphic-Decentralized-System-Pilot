"""Read-only, evidence-bound thermal recording ingress; no calibration or device IO.

Four already calibrated scalar observation streams feed the existing two-capacity
reference. Core reference values never enter inference. Applicability is checked
against declared acquisition times; certificate authenticity remains unestablished.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import timedelta
import math

from . import thermal_contract as thermal
from . import thermal_reference
from .adapters.subprocess import _json
from .calibration_status import _timestamp
from .control_contracts import bytes_ref, content_ref, keys, number, text, observation
from .control_checks import inspect_record, compare
from .core.identities import content_identity
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .operations.runner import seal
from .telemetry import canonical

SOURCE_SCHEMA = "ciw.measured-thermal-source.v1"
DATA_SCHEMA = "ciw.measured-thermal-data.v1"
SOURCE_LIMIT = 1024 * 1024
RESULT_LIMIT = 8 * 1024 * 1024
POLICY = {
    "plant": "two_capacity_linear_thermal",
    "inference": "existing_python_reference_shell_only",
    "inputs": "declared_constant_over_preceding_sample_interval",
    "calibration": "already_applied; declared_historical_applicability_only",
    "reference": "core_reference_withheld_from_inference",
    "physical_validation": "not_established",
    "state_admission": "not_performed",
    "hardware_actuation": "not_performed",
}
AUTHORITY = {"physical_validation": "not_established", "state_admission": "not_performed",
             "hardware_actuation": "not_performed"}
CHANNELS = {"shell": ("shell_temperature", "K"), "core_reference": ("core_temperature", "K"),
            "heat_power": ("heat_power", "W"), "ambient": ("temperature", "K")}
ASSUMPTIONS = {
    "filter_noise": "process_and_measurement_noise_independent_across_steps_and_of_prior",
    "input_model_clock_uncertainty": "retained_not_propagated_by_this_kernel",
    "calibration_correlation": "not_inferred_from_marginal_uncertainties",
}


def _artifacts(source):
    artifacts = source["artifacts"]
    if type(artifacts) is not list or not 1 <= len(artifacts) <= 32:
        raise ValueError("Retain a bounded list of exact recording artifacts")
    result = {}
    for item in artifacts:
        keys(item, {"sha256", "bytes_b64"})
        ref = content_ref(item["sha256"])
        if ref in result or type(item["bytes_b64"]) is not str:
            raise ValueError("Duplicate artifact or malformed artifact bytes")
        try:
            raw = base64.b64decode(item["bytes_b64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Artifact requires canonical base64") from exc
        if (not raw or len(raw) > SOURCE_LIMIT or bytes_ref(raw) != ref or
                base64.b64encode(raw).decode("ascii") != item["bytes_b64"]):
            raise ValueError("Retained artifact bytes differ from their identity")
        result[ref] = raw
    return result


def _ref(ref, artifacts):
    content_ref(ref)
    if ref not in artifacts:
        raise ValueError("Recording has a dangling evidence reference")
    return ref


def _evidence_lineage(ref, artifacts, covariance_index, forbidden, visiting=(), checked=None):
    """Follow explicit retained typed lineage, not the meaning of opaque bytes.

    A covariance or observation-stream wrapper is not independent evidence when
    its declared ancestry contains the withheld recording. Byte identities and
    covariance identities remain distinct; both participate in cycle bounds.
    """
    _ref(ref, artifacts)
    if ref in forbidden:
        raise ValueError("Inference evidence references held-out data")
    marker = "evidence:" + ref
    if marker in visiting or len(visiting) > 32:
        raise ValueError("Cyclic or excessive evidence lineage")
    if checked is None:
        checked = set()
    if marker in checked:
        return
    try:
        candidate = _json(artifacts[ref])
    except (ValueError, UnicodeError):
        return
    if type(candidate) is not dict:
        return
    path = (*visiting, marker)
    if candidate.get("schema") == "covariance-artifact.v1":
        _covariance_lineage(candidate, artifacts, covariance_index, forbidden, path, checked)
    elif candidate.get("schema") in {"ciw.observation-stream.v1", "ciw.observation.v1", "ciw.state.v1"}:
        inspect_record(candidate)
        rows = candidate["observations"] if candidate["schema"] == "ciw.observation-stream.v1" else [candidate]
        for row in rows:
            for source_ref in row["provenance"]["sources"]:
                _evidence_lineage(source_ref, artifacts, covariance_index, forbidden, path, checked)
            _covariance_lineage(row["uncertainty"], artifacts, covariance_index, forbidden, path, checked)
    checked.add(marker)


def _covariance_lineage(covariance, artifacts, covariance_index, forbidden, visiting=(), checked=None):
    if covariance is None:
        return
    validate_covariance_artifact(covariance)
    identity = covariance["covariance_id"]
    if identity in visiting or len(visiting) > 32:
        raise ValueError("Cyclic or excessive covariance lineage")
    if checked is None:
        checked = set()
    if identity in checked:
        return
    for ref in covariance["provenance"]["source_evidence_ids"]:
        _ref(ref, artifacts)
        if ref in forbidden:
            raise ValueError("Inference uncertainty references held-out data")
        _evidence_lineage(ref, artifacts, covariance_index, forbidden,
                          (*visiting, covariance["covariance_id"]), checked)
    for identity in covariance["provenance"]["source_covariance_ids"]:
        if identity not in covariance_index:
            raise ValueError("Unresolved source covariance identity")
        _covariance_lineage(covariance_index[identity], artifacts, covariance_index,
                            forbidden, (*visiting, covariance["covariance_id"]), checked)
    checked.add(covariance["covariance_id"])


def streams(source):
    artifacts = _artifacts(source)
    return {name: _json(artifacts[ref]) for name, ref in source["series"].items()}


def execution_request(source):
    """Compile only the three inference streams; no held-out values or criteria."""
    selected = streams(source)
    request = deepcopy(source["request"])
    request["inputs"] = [[p["value"], a["value"]] for p, a in zip(
        selected["heat_power"]["observations"], selected["ambient"]["observations"])]
    request["observations"] = [[None, row["value"]] for row in selected["shell"]["observations"]]
    thermal.validate_request(request)
    return request


def _validate(source):
    keys(source, {"schema", "experiment_id", "configuration", "origin", "asset_id",
                  "configuration_id", "clock", "frame", "request", "series", "channels",
                  "artifacts", "model_evidence_refs", "assumptions", "held_out", "acceptance"})
    if source["schema"] != SOURCE_SCHEMA or source["configuration"] != POLICY:
        raise ValueError("Unsupported measured thermal schema or authority policy")
    for name in ("experiment_id", "asset_id", "configuration_id", "frame"):
        text(source[name])
    if source["origin"] not in {"measured", "test_fixture"}:
        raise ValueError("Declare measured or test_fixture recording origin")
    if source["assumptions"] != ASSUMPTIONS:
        raise ValueError("Declare the bounded filter uncertainty assumptions")
    keys(source["clock"], {"id", "epoch_utc", "uncertainty_s"})
    text(source["clock"]["id"])
    epoch = _timestamp(source["clock"]["epoch_utc"], "clock epoch", strict=True)
    clock_uncertainty = source["clock"]["uncertainty_s"]
    if clock_uncertainty is not None and not 0 <= number(clock_uncertainty) <= 3600:
        raise ValueError("Clock uncertainty is unknown or bounded nonnegative seconds")
    # Request template omits arrays; they are compiled only from bound records.
    keys(source["request"], {"schema", "operation_id", "model", "sample_interval_s", "prior",
                            "process_noise_covariance", "observation_noise_covariance", "selection"})
    keys(source["series"], set(CHANNELS))
    keys(source["channels"], set(CHANNELS))
    artifacts = _artifacts(source)
    if len(set(source["series"].values())) != 4:
        raise ValueError("Use four distinct typed recording streams")
    for ref in source["series"].values():
        _ref(ref, artifacts)
    refs = source["model_evidence_refs"]
    if type(refs) is not list or not 1 <= len(refs) <= 8 or len(set(refs)) != len(refs):
        raise ValueError("Retain model/prior/noise justification references")
    for ref in refs:
        _ref(ref, artifacts)
    selected = streams(source)
    covariance_index = {}
    for raw in artifacts.values():
        try:
            candidate = _json(raw)
        except (ValueError, UnicodeError):
            continue
        if isinstance(candidate, dict) and candidate.get("schema") == "covariance-artifact.v1":
            validate_covariance_artifact(candidate)
            covariance_index[candidate["covariance_id"]] = candidate
    reference_channel = source["channels"]["core_reference"]
    keys(reference_channel, {"device_id", "raw_ref", "calibration_ref", "application_ref",
                             "valid_from", "valid_until", "range", "quantity", "unit"})
    reference_data = {source["series"]["core_reference"], reference_channel["raw_ref"], reference_channel["application_ref"]}
    if reference_data.intersection(refs):
        raise ValueError("Held-out recording cannot justify fitted inference parameters")
    for ref in refs:
        _evidence_lineage(ref, artifacts, covariance_index, reference_data)
    request = execution_request(source)
    count = len(request["inputs"])
    model_id = content_identity(request["model"])
    dt = request["sample_interval_s"]
    semantics = "observed" if source["origin"] == "measured" else "simulated"
    for name, (quantity, unit) in CHANNELS.items():
        channel = source["channels"][name]
        keys(channel, {"device_id", "raw_ref", "calibration_ref", "application_ref",
                       "valid_from", "valid_until", "range", "quantity", "unit"})
        text(channel["device_id"])
        for key in ("raw_ref", "calibration_ref", "application_ref"):
            _ref(channel[key], artifacts)
        if channel["quantity"] != quantity or channel["unit"] != unit:
            raise ValueError("Calibration channel quantity/unit differs")
        limits = channel["range"]
        if type(limits) is not list or len(limits) != 2 or number(limits[0]) >= number(limits[1]):
            raise ValueError("Declare an ordered calibration operating range")
        start = _timestamp(channel["valid_from"], "calibration valid_from", strict=True)
        end = _timestamp(channel["valid_until"], "calibration valid_until", strict=True)
        if start >= end:
            raise ValueError("Calibration validity interval is empty")
        value = selected[name]
        inspect_record(value)
        if value["schema"] != "ciw.observation-stream.v1" or len(value["observations"]) != count:
            raise ValueError("Recording must be a complete bounded observation stream")
        signature = None
        for i, row in enumerate(value["observations"]):
            # Inputs describe a zero-order hold on the preceding interval.
            time_s = (i + 1) * dt
            if (row["identity"]["model_id"] != model_id or row["identity"]["entity_id"] != source["asset_id"] or
                    row["clock"] != {"id": source["clock"]["id"], "time_s": time_s} or
                    row["frame"] != source["frame"] or row["quantity"] != quantity or row["unit"] != unit or
                    row["provenance"]["semantics"] != semantics or row["provenance"]["provider"] != channel["device_id"]):
                raise ValueError("Recording identity, clock, frame, unit or origin differs")
            current = row["identity"]["execution_id"]
            if i and current != signature:
                raise ValueError("One recording cannot mix acquisition executions")
            signature = current
            if not {channel[key] for key in ("raw_ref", "calibration_ref", "application_ref")} <= set(row["provenance"]["sources"]):
                raise ValueError("Observation must bind raw, calibration and application evidence")
            for ref in row["provenance"]["sources"]:
                _ref(ref, artifacts)
                if name != "core_reference" and ref in reference_data:
                    raise ValueError("Inference recording references held-out data")
                _evidence_lineage(ref, artifacts, covariance_index,
                                  reference_data if name != "core_reference" else set())
            _covariance_lineage(row["uncertainty"], artifacts, covariance_index,
                                reference_data if name != "core_reference" else set())
            x = row["value"]
            if x is not None and (type(x) not in (int, float) or not limits[0] <= number(x) <= limits[1]):
                raise ValueError("Calibrated values must be scalars within declared range")
            if x is None and name in {"heat_power", "ambient"}:
                raise ValueError("Missing driving inputs cannot be imputed")
            # Unknown clock uncertainty remains unknown; no fabricated zero is retained.
            radius = 0 if clock_uncertainty is None else clock_uncertainty
            left = i * dt if name in {"heat_power", "ambient"} else time_s
            if not (start <= epoch + timedelta(seconds=left - radius) and
                    epoch + timedelta(seconds=time_s + radius) < end):
                raise ValueError("Calibration is inapplicable at the declared acquisition interval")
    held = source["held_out"]
    keys(held, {"excluded_from", "evidence_refs"})
    if held["excluded_from"] != ["model_fit", "prior", "noise_tuning", "conditioning", "acceptance_selection"]:
        raise ValueError("Reference must be declared held out from fitting and conditioning")
    if type(held["evidence_refs"]) is not list or not 1 <= len(held["evidence_refs"]) <= 8:
        raise ValueError("Retain evidence for the declared held-out protocol")
    for ref in held["evidence_refs"]:
        _ref(ref, artifacts)
    reference_channel = source["channels"]["core_reference"]
    for name in ("shell", "heat_power", "ambient"):
        channel = source["channels"][name]
        if (channel["device_id"] == reference_channel["device_id"] or
                channel["raw_ref"] == reference_channel["raw_ref"] or
                channel["application_ref"] == reference_channel["application_ref"]):
            raise ValueError("Held-out reference must have distinct device and data lineage")
    if source["series"]["core_reference"] in refs or reference_channel["raw_ref"] in refs:
        raise ValueError("Held-out recording cannot justify fitted inference parameters")
    acceptance = source["acceptance"]
    if acceptance is not None:
        keys(acceptance, {"atol_k", "rtol", "criteria_ref"})
        if not 0 <= number(acceptance["atol_k"]) <= 1000 or not 0 <= number(acceptance["rtol"]) <= 1:
            raise ValueError("Declare bounded nonnegative held-out error criteria")
        _ref(acceptance["criteria_ref"], artifacts)
        if acceptance["criteria_ref"] in reference_data:
            raise ValueError("Held-out recording cannot justify acceptance criteria")
        _evidence_lineage(acceptance["criteria_ref"], artifacts, covariance_index, reference_data)
    return source


def validate_source(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= SOURCE_LIMIT:
        raise ValueError("Measured thermal source requires bounded exact bytes")
    try:
        return _validate(_json(raw))
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError("Malformed measured thermal recording") from exc


def _projection(source, observer, execution_id=None):
    """Scalar core estimate matching the reference coordinates; marginal covariance."""
    reference = streams(source)["core_reference"]["observations"]
    result = []
    for row, native in zip(reference, observer["trace"]):
        estimate = native["posterior_mean"][0]
        variance = native["posterior_covariance"][0][0]
        provenance = {"provider": "ciw.measured-thermal.v1", "sources": list(dict.fromkeys([source["series"][name] for name in ("shell", "heat_power", "ambient")] + source["model_evidence_refs"])),
                      "semantics": "estimated"}
        covariance = create_covariance_artifact(
            matrix=[[variance]], quantity_ids=["core_temperature"], units=["K"], frame=source["frame"],
            reference_values=[estimate], method="existing_thermal_reference_core_marginal",
            basis={"kind": "estimated_state", "id": bytes_ref(canonical(source)) + "/core"},
            provenance={"provider": provenance["provider"], "source_evidence_ids": provenance["sources"],
                        "source_covariance_ids": []},
            assumptions=[ASSUMPTIONS["filter_noise"], ASSUMPTIONS["input_model_clock_uncertainty"],
                         "Conditional marginal only; not empirical coverage or cross-tick covariance"])
        result.append(observation(identity={**row["identity"], "execution_id": execution_id},
            clock=deepcopy(row["clock"]), frame=source["frame"], quantity="core_temperature", value=estimate,
            unit="K", uncertainty=covariance, provenance=provenance))
    return result


def _calculate(source, native=None):
    request = execution_request(source)
    if native is None:
        native = thermal_reference.reference(request)
    estimated = _projection(source, native["observer"])
    reference = streams(source)["core_reference"]["observations"]
    acceptance = source["acceptance"]
    comparison = None if acceptance is None else compare(estimated, reference,
        atol=acceptance["atol_k"], rtol=acceptance["rtol"])
    errors = [None if ref["value"] is None else est["value"] - ref["value"]
              for est, ref in zip(estimated, reference)]
    finite_errors = [x for x in errors if x is not None]
    return {"schema": DATA_SCHEMA, "request": request, **native,
        "recording": {key: deepcopy(source[key]) for key in ("origin", "asset_id", "configuration_id", "clock", "frame", "series", "channels", "assumptions")},
        "evaluation": {"comparison": comparison, "status": "INDETERMINATE" if comparison is None else comparison["outcome"]["status"],
            "reason": "criteria_not_declared" if comparison is None else comparison["outcome"]["reason"],
            "estimation_mode": "prediction_only" if all(row[1] is None for row in request["observations"]) else "shell_conditioned",
            "errors_k": errors, "reference_sample_count": len(finite_errors),
            "rmse_k": None if not finite_errors else math.hypot(*finite_errors) / math.sqrt(len(finite_errors)),
            "reference_exclusion": "enforced_for_conditioning; fitting_and_independence_are_declarations",
            "calibration_check": "declared_historical_applicability_only",
            "calibration_application": "retained_declaration_not_recomputed",
            "calibration_authenticity": "not_established", "uncertainty_coverage": "not_established",
            "clock_applicability": "declared_times_only" if source["clock"]["uncertainty_s"] is None else "declared_time_bounds",
            "claim_scope": "declared_numerical_condition_only"},
        "claim_scope": ("linear_thermal_prediction_and_held_out_core_comparison" if all(row[1] is None for row in request["observations"])
                        else "shell_conditioned_linear_thermal_estimate_and_held_out_core_comparison"),
        "authority": deepcopy(AUTHORITY)}


def calculate(source):
    _validate(source)
    result = _calculate(source)
    if len(canonical(result)) > RESULT_LIMIT:
        raise ValueError("Measured thermal result exceeds its byte budget")
    return result


def validate_data(source, data):
    # Offline inspection checks equations using the existing bounded Python reference.
    # This is no provider launch, execution occurrence, or independent verification.
    _validate(source)
    keys(data, {"schema", "request", "model", "observer", "selection", "recording", "evaluation", "claim_scope", "authority"})
    request = execution_request(source)
    expected = thermal_reference.reference(request)
    for key in ("model", "observer", "selection"):
        thermal._close(data[key], expected[key], key)
    # Rebuild derived comparisons from the retained numerics, preserving their seals.
    # Cross-host checking uses the existing binary64 tolerances; replay still needs
    # the exact runtime and identical numerical result identity.
    retained = {key: data[key] for key in ("model", "observer", "selection")}
    if len(canonical(data)) > RESULT_LIMIT or canonical(data) != canonical(_calculate(source, retained)):
        raise ValueError("Measured thermal data differs from retained inputs and reference calculation")


def observation_stream(source, data, *, stage, execution_id):
    validate_data(source, data)
    if stage == "posterior":
        rows = _projection(source, data["observer"], execution_id)
    elif stage in {"measurement", "reference"}:
        rows = streams(source)["shell" if stage == "measurement" else "core_reference"]["observations"]
    else:
        raise ValueError("Measured thermal exports posterior core, shell measurement, or held-out reference")
    return seal({"schema": "ciw.observation-stream.v1", "observations": deepcopy(rows)})
