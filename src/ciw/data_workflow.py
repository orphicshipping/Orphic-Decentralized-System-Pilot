"""Consume retained native observations through the existing operation lifecycle.

The Workbench owns source bytes; this seam only selects one explicitly named
quantity and invokes the existing bounded numerical comparison. No units,
clocks, frames, covariance, missing values or authority are inferred.
"""
from __future__ import annotations

import base64
from copy import deepcopy

from .control_checks import compare
from .control_contracts import keys, text
from .core.identities import content_identity
from .operations.registry import Operation

COMPARE = "data.compare-observations.v1"
OPERATION_IDS = frozenset({COMPARE})


def source_ids(parameters):
    return [source["source_id"] for key in ("left_source", "right_source", "source")
            if isinstance((source := parameters.get(key)), dict)
            and isinstance(source.get("source_id"), str)]


def _source(source):
    from .data_sources import _source as parse, native_record
    from .workbench import _source as retained_source
    if type(source) is not dict or source.get("kind") != "data-record":
        raise ValueError("Data comparison requires exact retained data-record sources")
    expected = retained_source({key: source[key] for key in ("kind", "label", "bytes_b64", "input_source_ids") if key in source})
    if content_identity(expected) != content_identity(source):
        raise ValueError("Data source identity/content mismatch")
    parsed = parse(base64.b64decode(source["bytes_b64"], validate=True))
    return native_record(parsed)


def _selected(record, quantity):
    if record["schema"] == "ciw.observation.v1":
        values = [record]
    elif record["schema"] == "ciw.observation-stream.v1":
        values = record["observations"]
    else:
        raise ValueError("Data comparison consumes observation or observation-stream records")
    selected = [value for value in values if value["quantity"] == quantity]
    if not selected:
        raise ValueError("Requested quantity has no retained observations")
    return selected, {"source_observation_count": len(values),
                      "selected_observation_count": len(selected),
                      "unselected_observation_count": len(values) - len(selected),
                      "selected_record_digests": [value["record_digest"] for value in selected]}


def _compare(parameters):
    keys(parameters, {"left_source", "right_source", "quantity", "atol", "rtol"})
    quantity = text(parameters["quantity"])
    left, left_receipt = _selected(_source(parameters["left_source"]), quantity)
    right, right_receipt = _selected(_source(parameters["right_source"]), quantity)
    return {"schema": "ciw.data-comparison.v1", "comparison": compare(
                left, right, atol=parameters["atol"], rtol=parameters["rtol"]),
            "inputs": {"left": {"source_id": parameters["left_source"]["source_id"], **left_receipt},
                       "right": {"source_id": parameters["right_source"]["source_id"], **right_receipt}},
            "selection": {"quantity": quantity, "resampling": "not_performed",
                          "unit_conversion": "not_performed", "uncertainty_propagation": "not_performed"},
            "physical_validation": "not_performed", "state_admission": "not_performed"}


def operation():
    from .data_runtime import comparison_identity
    return Operation(COMPARE, "backend", lambda run, parameters: _compare(parameters),
                     comparison_identity)


def validate_payload(operation_id, data, run, parameters, selection):
    if operation_id != COMPARE or content_identity(data) != content_identity(_compare(parameters)):
        raise ValueError("Data comparison differs from its exact retained native inputs")


def validate_live_dependencies(operation_id, parameters, sources):
    if operation_id not in OPERATION_IDS:
        return
    for key in ("left_source", "right_source"):
        source = parameters.get(key)
        if (type(source) is not dict or type(source.get("source_id")) is not str
                or source["source_id"] not in sources
                or content_identity(sources[source["source_id"]]) != content_identity(source)):
            raise ValueError("Data comparison requires sources present unchanged in this Workbench")
        _source(source)


def validate_saved_dependencies(results, sources):
    from .data_compute_bridge import validate_saved_dependencies as validate_compute_dependencies
    validate_compute_dependencies(results, sources)
    from .product_study_workflow import validate_saved_dependencies as validate_product_dependencies
    validate_product_dependencies(results, sources)
    for result in results.values():
        if result["operation_id"] in OPERATION_IDS:
            validate_live_dependencies(result["operation_id"], result["parameters"], sources)


def comparison_parameters(workbench, *, left_source_id, right_source_id, quantity, atol, rtol=0.0):
    """Construct an explicit pinned request, without executing a consumer."""
    return deepcopy({"left_source": workbench.get_source(left_source_id),
                     "right_source": workbench.get_source(right_source_id),
                     "quantity": quantity, "atol": atol, "rtol": rtol})


def project_observations(workbench, *, source_id, selector, dataset_id, revision,
                         parent_source_id, recorded_at, label):
    """Retain an existing native stream with its source dependency automatically.

    This is a lossless selection of retained observations, not a numerical solve,
    interpolation, calibration or model admission. Full upstream artifacts stay
    in the same Workbench and corrections invalidate the derived stream.
    """
    import json
    from .data_sources import native_record, retain_record
    source = workbench.get_source(source_id)
    if source["kind"] == "data-record":
        keys(selector, set())
        native = native_record(source)
        if native["schema"] != "ciw.field-observation-mapping.v1":
            raise ValueError("This data record has no registered observation projection")
        from .data_field_bridge import observation_stream
        stream = observation_stream(native)
    elif source["kind"] == "measured-thermal":
        keys(selector, {"channel"})
        from . import measured_thermal
        native = measured_thermal.validate_source(base64.b64decode(source["bytes_b64"], validate=True))
        if type(selector["channel"]) is not str or selector["channel"] not in measured_thermal.CHANNELS:
            raise ValueError("Select an explicitly named measured-thermal channel")
        stream = measured_thermal.streams(native)[selector["channel"]]
    else:
        raise ValueError("This source kind has no registered observation projection")
    derived = retain_record(stream, dataset_id=dataset_id, revision=revision,
        parent_source_id=parent_source_id, recorded_at=recorded_at, data_family="observation-stream")
    return workbench.add_source({"kind": "data-record", "label": label,
        "bytes_b64": base64.b64encode(json.dumps(derived, separators=(",", ":"), allow_nan=False).encode()).decode(),
        "input_source_ids": [source_id]})
