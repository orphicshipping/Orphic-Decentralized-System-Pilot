"""Explicit affine-temperature handoff to native measured-thermal recordings.

This bridge changes only a named temperature channel in an already-valid native
recording. The existing channel is the operator's sensor/location/frame binding;
matching a sensor identifier does not prove that physical binding. Neither clocks
nor units are converted and no samples are dropped, shifted or interpolated.

The full processing report, request, numerical audit and temporal covariance are
retained. Scalar covariance marginals point to that full covariance. The thermal
estimator still uses its separately declared Q/R; it does not propagate these
recorded calibration, temporal, input or clock uncertainties.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
import math

from . import measured_thermal as measured
from .calibration_status import _timestamp
from .control_contracts import bytes_ref, number, observation
from .core.covariance import create_covariance_artifact
from .core.identities import canonical_json, content_identity
from .operations.runner import seal
from .temperature_contract import AUTHORITY, LEGACY_NUMERICAL_CONTRACT, NUMERICAL_CONTRACT, validate_request
from .temperature_processing import (
    _applicability, _applicability_record, _identities, _uncertainty, validate_report,
)
from .temperature_verification import verify
from .telemetry import canonical

APPLICATION_SCHEMA = "ciw.temperature-recording-application.v1"
PROVIDER = "ciw.temperature_recording_bridge.v1"
UNCERTAINTY_SCOPE = (
    "Full temporal covariance retained; scalar marginals are projections, not "
    "independent observations; recorded uncertainty is not propagated by the "
    "measured-thermal estimator and never replaces its declared Q/R"
)


def _exact_source_copies(request, report):
    """Copied declarations retain their canonical identity, not Python equality.

    Python considers True, 1 and 1.0 equal. A retained copy must not substitute
    those representations, while actually computed numeric outputs still use
    the separate rational checker's tolerance rather than byte equality.
    """
    numerical = report["numerical_contract"]
    if canonical_json(numerical) not in {
        canonical_json(NUMERICAL_CONTRACT), canonical_json(LEGACY_NUMERICAL_CONTRACT)
    }:
        raise ValueError("Temperature numerical contract differs from its exact declaration")
    sensor_flags, polymer_flags = _applicability(request)
    expected = {
        "source_kind": request["source_kind"],
        "measurement_identity": request["identity"],
        "raw_observations": request["raw_observations"],
        "identities": _identities(request, numerical),
        "uncertainty": _uncertainty(request),
        "applicability": _applicability_record(request, sensor_flags, polymer_flags),
        "authority": AUTHORITY,
    }
    for field, value in expected.items():
        if canonical_json(report[field]) != canonical_json(value):
            raise ValueError("Temperature report " + field + " differs from its exact source copy")
    for name in ("calibrated_sensor_temperature", "polymer_temperature"):
        for sample, raw in zip(report[name]["samples"], request["raw_observations"]["samples"]):
            actual = {field: sample[field] for field in ("sample_id", "time_s")}
            copied = {field: raw[field] for field in ("sample_id", "time_s")}
            if canonical_json(actual) != canonical_json(copied):
                raise ValueError("Temperature sample identity/time differs from its exact source copy")
    # Derived interval endpoints are numerical outputs, not copied source data;
    # reject boolean aliases without imposing bitwise equality on arithmetic.
    for sample in report["acceptance"]["sample_results"]:
        if sample["expanded_interval_K"] is not None:
            for endpoint in sample["expanded_interval_K"]:
                number(endpoint)


def _evidence_refs(request):
    """Every upstream evidence commitment in the retained request must resolve."""
    refs = set(request["calibration"]["reference_refs"])
    refs.add(request["joint_covariance"]["evidence_ref"])
    refs.update(item["evidence_ref"] for item in request["contributions"])
    refs.update(item["evidence_ref"] for item in request["budget"] if item["evidence_ref"] is not None)
    if request["installed_correction"] is not None:
        refs.add(request["installed_correction"]["evidence_ref"])
    if request["acceptance"] is not None:
        refs.add(request["acceptance"]["evidence_ref"])
    return sorted(refs)


def _seconds(delta):
    return Fraction((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds, 1000000)


def _validity(source, channel, request):
    """Conservatively intersect closed numeric and half-open UTC declarations.

    UTC text supports microseconds. Round the lower endpoint upward and upper
    endpoint downward, never expanding the processing calibration interval to
    accommodate that representation. Its closed upper endpoint is deliberately
    not promoted to an exclusive endpoint just after it.
    """
    epoch = _timestamp(source["clock"]["epoch_utc"], "clock epoch", strict=True)
    previous = source["channels"][channel]
    lower, upper = map(lambda x: Fraction(float(x)), request["calibration"]["valid_time_s"])
    old_start = _timestamp(previous["valid_from"], "valid_from", strict=True)
    old_end = _timestamp(previous["valid_until"], "valid_until", strict=True)
    lower = max(lower, _seconds(old_start - epoch))
    upper = min(upper, _seconds(old_end - epoch))
    start = epoch + timedelta(microseconds=math.ceil(lower * 1000000))
    end = epoch + timedelta(microseconds=math.floor(upper * 1000000))
    if start >= end:
        raise ValueError("Temperature and recording calibration intervals do not overlap")
    radius = Fraction(float(source["clock"]["uncertainty_s"] or 0))
    dt = source["request"]["sample_interval_s"]
    for i, sample in enumerate(request["raw_observations"]["samples"]):
        left = Fraction(float(i * dt if channel == "ambient" else sample["time_s"]))
        right = Fraction(float(sample["time_s"]))
        if not (_seconds(start - epoch) <= left - radius and right + radius < _seconds(end - epoch)):
            raise ValueError("Temperature calibration is inapplicable over the recording acquisition interval")
    return start.isoformat(), end.isoformat()


def bridge_temperature_recording(source: dict, request: dict, report: dict, *, channel: str = "shell") -> dict:
    """Return a detached, validated native source with one channel replaced.

    ``source`` supplies the explicitly declared asset, thermal model, frame,
    clock epoch/uncertainty and sensor-to-channel binding. Only ``shell``,
    ``core_reference`` and ``ambient`` accept calibrated_sensor_temperature in K;
    polymer estimates and heat-power conversion are deliberately unsupported.
    The request's sensor and clock IDs, origin, sample count and exact timestamps
    must match the selected channel. Every upstream evidence hash must already
    resolve in source.artifacts, including declaration-only synthetic evidence.

    All samples must be applicable. Missing or otherwise invalid processing
    samples refuse the handoff rather than being silently converted to thermal
    dropouts. Incomplete budgets and indeterminate processing acceptance are
    retained unchanged and do not become physical or thermal acceptance.

    No acquisition execution ID is invented: the new rows carry null. The
    original processing report may be a retained operation's ``data``; this
    helper checks its numerical content, but does not authenticate or synthesize
    a processing/verification execution occurrence. It performs no provider IO.
    Existing artifacts remain retained, so native artifact/byte limits may
    refuse repeated replacements. The MCP wrapper has its own smaller limit.
    """
    if channel not in {"shell", "core_reference", "ambient"}:
        raise ValueError("Temperature bridge requires shell, core_reference or ambient")
    original = measured.validate_source(canonical(source))
    request = validate_request(request)
    validate_report(request, report)
    _exact_source_copies(request, report)
    report = deepcopy(report)
    if request["identity"]["sensor_id"] != original["channels"][channel]["device_id"]:
        raise ValueError("Temperature sensor does not match the declared recording channel device")
    if request["clock"]["id"] != original["clock"]["id"]:
        raise ValueError("Temperature clock differs; implicit clock alignment is forbidden")
    expected_kind = "synthetic" if original["origin"] == "test_fixture" else "retained_observation"
    if request["source_kind"] != expected_kind:
        raise ValueError("Temperature and recording origins differ; synthetic data cannot become measured")
    output = report["calibrated_sensor_temperature"]
    rows = output["samples"]
    old_rows = measured.streams(original)[channel]["observations"]
    if len(rows) != len(old_rows) or any(row["time_s"] != old["clock"]["time_s"] for row, old in zip(rows, old_rows)):
        raise ValueError("Temperature samples must exactly match recording timestamps and count; no resampling")
    if report["applicability"]["calibration_status"] != "within_declared_applicability" or any(row["status"] == "invalid" or row["flags"] for row in rows):
        raise ValueError("Inapplicable or invalid temperature samples cannot enter a recording")
    artifacts = measured._artifacts(original)
    evidence_refs = _evidence_refs(request)
    if not set(evidence_refs) <= artifacts.keys():
        raise ValueError("Temperature request has unresolved upstream evidence; retain its exact bytes")
    # A valid seal is not evidence of correct arithmetic. Execute the independent
    # bounded reference now, rather than trusting a resealed historical report.
    audit = verify(request, report)
    if audit["status"] != "PASS":
        raise ValueError("Temperature numerical audit failed; report cannot enter a recording")
    valid_from, valid_until = _validity(original, channel, request)
    low = max(original["channels"][channel]["range"][0], request["calibration"]["output_range_K"][0])
    high = min(original["channels"][channel]["range"][1], request["calibration"]["output_range_K"][1])
    if low >= high or any(not low <= row["value_K"] <= high for row in rows):
        raise ValueError("Temperature values exceed the intersection of declared recording/calibration ranges")

    result = deepcopy(original)

    def retain(value, *, temperature_commitment=False):
        # Temperature identities use the historical CIW encoder (ASCII escaped
        # JSON), while native recording artifacts usually use UTF-8 JSON. Keep
        # the former for raw/calibration commitments even with non-ASCII IDs.
        raw = canonical_json(value).encode("utf-8") if temperature_commitment else canonical(value)
        ref = bytes_ref(raw)
        if ref not in artifacts:
            result["artifacts"].append({"sha256": ref, "bytes_b64": base64.b64encode(raw).decode("ascii")})
            artifacts[ref] = raw
        return ref

    quantity, unit = measured.CHANNELS[channel]
    raw_ref = retain(request["raw_observations"], temperature_commitment=True)
    calibration_ref = retain(request["calibration"], temperature_commitment=True)
    application = seal({
        "schema": APPLICATION_SCHEMA, "request": request, "report": report,
        "numerical_audit": audit,
        "binding": {"channel": channel, "quantity": quantity, "unit": unit,
                    "asset_id": original["asset_id"], "configuration_id": original["configuration_id"],
                    "frame": original["frame"], "clock": deepcopy(original["clock"]),
                    "measurement_identity": deepcopy(request["identity"]),
                    "previous_channel": deepcopy(original["channels"][channel]),
                    "previous_stream_ref": original["series"][channel]},
        "raw_commitment_scope": report["identities"]["raw_commitment_scope"],
        "binding_scope": "operator_declared_sensor_location_channel_and_frame; physical_binding_not_established",
        "uncertainty_scope": UNCERTAINTY_SCOPE,
        "processing_occurrence": "not_authenticated_or_created_by_bridge",
        "acquisition_execution_id": None,
        "authority": deepcopy(report["authority"]),
    })
    application_ref = retain(application)
    refs = list(dict.fromkeys([raw_ref, calibration_ref, application_ref, *evidence_refs]))
    covariance = create_covariance_artifact(
        matrix=output["covariance_K2"],
        quantity_ids=[quantity + ":" + row["sample_id"] for row in rows],
        units=[unit] * len(rows), frame=original["frame"],
        reference_values=[row["value_K"] for row in rows],
        method="retained_temperature_first_order_full_temporal_covariance",
        basis={"kind": "calibrated_observation", "id": application_ref},
        provenance={"provider": PROVIDER, "source_evidence_ids": refs, "source_covariance_ids": [],
                    "metadata": {"sample_ids": [row["sample_id"] for row in rows],
                                 "time_s": [row["time_s"] for row in rows],
                                 "clock_id": original["clock"]["id"]}},
        assumptions=[report["uncertainty"]["limitations"], UNCERTAINTY_SCOPE,
                     "Upstream calibration, covariance dependencies and budget remain submitted declarations"])
    covariance_ref = retain(covariance)
    observations = []
    for i, row in enumerate(rows):
        marginal = create_covariance_artifact(
            matrix=[[covariance["matrix"][i][i]]], quantity_ids=[quantity], units=[unit],
            frame=original["frame"], reference_values=[row["value_K"]],
            method="exact_scalar_projection_of_retained_temperature_covariance",
            basis={"kind": "calibrated_observation", "id": application_ref + "/" + row["sample_id"]},
            provenance={"provider": PROVIDER, "source_evidence_ids": [*refs, covariance_ref],
                        "source_covariance_ids": [covariance["covariance_id"]]},
            assumptions=[UNCERTAINTY_SCOPE])
        observations.append(observation(
            identity={"model_id": content_identity(original["request"]["model"]),
                      "entity_id": original["asset_id"], "execution_id": None},
            clock={"id": original["clock"]["id"], "time_s": row["time_s"]},
            frame=original["frame"], quantity=quantity, value=row["value_K"], unit=unit,
            uncertainty=marginal,
            provenance={"provider": original["channels"][channel]["device_id"],
                        "sources": [*refs, covariance_ref],
                        "semantics": "simulated" if expected_kind == "synthetic" else "observed"}))
    result["series"][channel] = retain(seal({"schema": "ciw.observation-stream.v1", "observations": observations}))
    result["channels"][channel].update(raw_ref=raw_ref, calibration_ref=calibration_ref,
                                       application_ref=application_ref, valid_from=valid_from,
                                       valid_until=valid_until, range=[low, high])
    # Recheck the native ingress, including byte/artifact budgets and all
    # inference-vs-held-out exclusions, after adding new upstream lineage.
    return measured.validate_source(canonical(result))
