"""Typed retained QC review through the existing manufacturing containment reducer.

Workbench owns the original bytes and correction history. The native journal,
SQLite store and manufacturing reducer remain authoritative for their domains.
This local replay projection never acquires signals, writes a store, clears a
hold, admits state, authenticates source declarations or authorizes shipment.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import platform

from .core.identities import canonical_json, content_identity
from .manufacturing_contracts import items, keys, number
from .operations.registry import Operation

REVIEW = "data.review-toolpoint-containment.v1"
SCHEMA = "ciw.data-toolpoint-containment-review.v1"
MANUFACTURING_SCHEMAS = frozenset({"ciw.polymer-manufacturing-report.v1", "ciw.polymer-manufacturing-report.v2"})
STREAM_SCHEMA = "ciw.toolpoint-stream-report.v1"
MAX_PARAMETERS_BYTES = 8 * 1024 * 1024
_RUNTIME_FILES = (
    "data_manufacturing_bridge.py", "manufacturing_contracts.py", "manufacturing_runtime.py",
    "manufacturing_quality.py", "polymer_microfab.py", "polymer_toolpoint_qc.py",
    "toolpoint_stream.py", "toolpoint_stream_containment.py", "core/identities.py",
)


def runtime_identity():
    root = Path(__file__).parent
    hashes = {name: sha256((root / name).read_bytes()).hexdigest() for name in _RUNTIME_FILES}
    return {"provider": "ciw.native-toolpoint-containment", "version": "1",
            "source_files_sha256": hashes, "environment": {"python": platform.python_version()},
            "scope": "bounded_same_implementation_retained_record_replay_no_physical_authority"}


def _shape(parameters):
    keys(parameters, {"manufacturing_source", "stream_sources", "assessed_at_s"})
    items(parameters["stream_sources"], maximum=64)
    number(parameters["assessed_at_s"])
    try:
        if len(canonical_json(parameters).encode("utf-8")) > MAX_PARAMETERS_BYTES:
            raise ValueError("Retained QC review exceeds its parameter byte budget")
    except (OverflowError, RecursionError) as exc:
        raise ValueError("Retained QC review exceeds its bounded JSON profile") from exc
    descriptors = [parameters["manufacturing_source"], *parameters["stream_sources"]]
    ids = []
    for source in descriptors:
        if type(source) is not dict or type(source.get("source_id")) is not str:
            raise ValueError("QC review requires full retained source descriptors")
        ids.append(source["source_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("QC review refuses duplicate source descriptors")
    return descriptors


def source_ids(parameters):
    """Explicit dependencies of a completed, already validated QC occurrence."""
    return [source["source_id"] for source in _shape(parameters)]


def _inputs(parameters):
    from .data_workflow import _source
    descriptors = _shape(parameters)
    records = [_source(source) for source in descriptors]
    manufacturing, streams = records[0], records[1:]
    if manufacturing["schema"] not in MANUFACTURING_SCHEMAS:
        raise ValueError("QC review requires a complete native manufacturing report")
    if any(report["schema"] != STREAM_SCHEMA for report in streams):
        raise ValueError("QC review requires complete native toolpoint-stream reports")
    # The unchanged native projection verifies every report, exact cycle/part/
    # configuration/clock mapping and the complete cohort. Missing streams hold.
    request = {"schema": "ciw.toolpoint-stream-containment-request.v1",
               "manufacturing_report": manufacturing, "streams": streams,
               "assessed_at_s": parameters["assessed_at_s"]}
    evidence = {"manufacturing": {
        "source_id": descriptors[0]["source_id"], "evidence_id": descriptors[0]["evidence_id"],
        "native_report_ref": content_identity(manufacturing),
        "evaluation_id": manufacturing["evaluation_id"],
        "request_ref": manufacturing["request_ref"], "result_ref": manufacturing["result_ref"]},
        "streams": [{"source_id": source["source_id"], "evidence_id": source["evidence_id"],
            "native_report_ref": content_identity(report), "execution_id": report["execution_id"],
            "spec_ref": report["spec_ref"], "head_ref": report["receipts"][-1]["receipt_ref"],
            "result_ref": report["result_ref"], "cycle_id": report["spec"]["binding"]["cycle_id"],
            "source_kind": report["spec"]["binding"]["source_kind"]}
            for source, report in zip(descriptors[1:], streams, strict=True)]}
    return request, evidence


def review(parameters):
    """Consume exact retained native reports; no independent store or detector."""
    from .toolpoint_stream_containment import project
    request, evidence = _inputs(parameters)
    result = project(request)
    return {"schema": SCHEMA, "inputs": evidence,
            "native_request_ref": content_identity(request),
            "containment": result, "containment_result_ref": content_identity(result),
            "authority": deepcopy(result["authority"]),
            "scope": "retained_record_containment_review_not_physical_verification",
            "storage_write": "not_performed", "hold_release": "not_performed"}


def operation():
    return Operation(REVIEW, "backend", lambda run, parameters: review(parameters), runtime_identity)


def validate_payload(operation_id, data, run, parameters, selection):
    # Offline validation uses bounded local native replay, never SQLite or an
    # external provider. Framework occurrence/runtime identities remain intact.
    if operation_id != REVIEW or canonical_json(data) != canonical_json(review(parameters)):
        raise ValueError("QC review differs from its exact retained native containment")


def validate_live_dependencies(operation_id, parameters, sources):
    if operation_id != REVIEW:
        return
    for source in _shape(parameters):
        if canonical_json(sources.get(source["source_id"])) != canonical_json(source):
            raise ValueError("QC review requires every source present unchanged in this Workbench")
    _inputs(parameters)


def validate_saved_dependencies(results, sources):
    for result in results.values():
        if result["operation_id"] == REVIEW:
            validate_live_dependencies(REVIEW, result["parameters"], sources)


def review_parameters(workbench, *, manufacturing_source_id, stream_source_ids, assessed_at_s):
    items(stream_source_ids, maximum=64)
    parameters = {"manufacturing_source": workbench.get_source(manufacturing_source_id),
        "stream_sources": [workbench.get_source(source_id) for source_id in stream_source_ids],
        "assessed_at_s": assessed_at_s}
    _shape(parameters)
    return deepcopy(parameters)
