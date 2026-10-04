"""Bounded temperature processing on NET's retained evidence and MCP path.

Copyright (c) 2026 Giason Pooni. SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import platform
import re

import numpy as np

from .adapters.protocol import InstrumentManifest
from .control_contracts import keys, text
from .core.identities import evidence_id, new_identity, validate_evidence_identity, validate_identity
from .core.records import validate_run_structure
from .operations.registry import Operation
from .operations.runner import check_seal, digest
from .temperature_contract import AUTHORITY, validate_request

PROCESS = "metrology.temperature.process.v1"
VERIFY = "metrology.temperature.verify.v1"
OPERATIONS = {PROCESS, VERIFY}


def runtime_identity(kind: str = "processing") -> dict:
    if kind not in {"processing", "verification"}:
        raise ValueError("Unsupported temperature runtime kind")
    from . import temperature_contract, temperature_processing, control_contracts
    from .core import covariance, identities
    from .operations import runner
    modules = [temperature_contract, temperature_processing, control_contracts, covariance, identities,
               runner, __import__(__name__, fromlist=["*"])]
    if kind == "verification":
        from . import temperature_verification
        modules.append(temperature_verification)
    raw = b"\0".join(Path(module.__file__).name.encode() + b"\0" +
                     Path(module.__file__).read_text(encoding="utf-8").replace("\r\n", "\n").encode()
                     for module in modules)
    return {"provider": "ciw.temperature" if kind == "processing" else "ciw.temperature.verification",
            "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "source_normalization": "utf8_lf", "scope": "declared_temperature_processing_only" if kind == "processing" else
                "independent_declared_temperature_numerical_audit",
            "environment": {"python": platform.python_version(), "numpy": np.__version__,
                            "floating_point": "binary64"}}


def validate_runtime(operation_id: str, value: dict) -> None:
    """Validate retained declarations without reading current provider code."""
    keys(value, {"provider", "version", "code_sha256", "source_normalization", "scope", "environment"})
    expected = ("ciw.temperature", "declared_temperature_processing_only") if operation_id == PROCESS else (
        "ciw.temperature.verification", "independent_declared_temperature_numerical_audit")
    if (operation_id not in OPERATIONS or (value["provider"], value["scope"]) != expected
            or value["version"] != "1" or value["source_normalization"] != "utf8_lf"
            or type(value["code_sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", value["code_sha256"]) is None):
        raise ValueError("Temperature runtime differs from its declared operation")
    keys(value["environment"], {"python", "numpy", "floating_point"})
    if (type(value["environment"]["python"]) is not str
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value["environment"]["python"]) is None
            or value["environment"]["floating_point"] != "binary64"):
        raise ValueError("Malformed retained temperature runtime environment")
    text(value["environment"]["numpy"])


def make_source(request: dict) -> dict:
    request = validate_request(request)
    manifest = InstrumentManifest(
        instrument_id="temperature-measurement-evidence.v1", role="retained_measurement_declaration",
        units={"measurement_declaration": "1"}, frames=("measurement-declaration",),
        sampling={"kind": "one_measurement_declaration", "time_semantics": "selection_envelope_only"},
        supported_operations=(PROCESS,),
        calibration_requirements={"temperature": "explicit affine calibration and declared covariance; physical qualification separate"})
    source = {"run_schema": "run.v1", "run_id": "run-temperature-" + digest(request)[7:23],
              "instrument": manifest.instrument_id,
              "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
                           "coordinate_frame": "measurement-declaration", "manifest": manifest.to_dict(),
                           "temperature_request": request,
                           "provenance": {"source": request["source_kind"],
                                          "generator": "ciw.temperature_workflow.make_source", "generator_version": 1}},
              "time_s": [0.0], "channels": {"measurement_declaration": {"unit": "1", "values": [1.0]}},
              "render": {}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_request(source: dict) -> dict:
    validate_run_structure(source)
    validate_evidence_identity(source)
    request = validate_request(source["metadata"]["temperature_request"])
    if source != make_source(request):
        raise ValueError("Temperature source differs from its exact retained measurement declaration")
    return request


def _process(source: dict, parameters: dict) -> dict:
    from .temperature_processing import process_request
    keys(parameters, set())
    return process_request(source_request(source))


def operation() -> Operation:
    return Operation(PROCESS, "backend", _process, runtime_identity)


def _candidate(source: dict, parameters: dict) -> dict:
    keys(parameters, {"processing"})
    candidate = parameters["processing"]
    check_seal(candidate)
    if (candidate.get("schema") != "ciw.operation-result.v1" or candidate.get("operation_id") != PROCESS
            or candidate.get("role") != "backend" or candidate.get("evidence_id") != source["evidence_id"]
            or candidate.get("run_id") != source["run_id"] or candidate.get("parameters") != {}):
        raise ValueError("Require the exact source-bound temperature processing occurrence")
    validate_identity(candidate.get("execution_id"), "execution")
    validate_identity(candidate.get("result_id"), "result")
    validate_runtime(PROCESS, candidate["runtime"])
    from .session import _recording_file, _validate_saved_result
    _validate_saved_result(candidate, source, candidate["selection_revision"], _recording_file(source))
    return deepcopy(candidate)


def _verify(source: dict, parameters: dict) -> dict:
    from .temperature_verification import verify
    candidate = _candidate(source, parameters)
    request = source_request(source)
    return {"schema": "ciw.temperature-verification-payload.v1", "verification_id": new_identity("verification"),
            "processing_result_id": candidate["result_id"], "processing_execution_id": candidate["execution_id"],
            "processing_record_digest": candidate["record_digest"], "request_ref": digest(request),
            "processing_report_ref": digest(candidate["data"]), "report": verify(request, candidate["data"]),
            "authority": deepcopy(AUTHORITY)}


def operations() -> list[Operation]:
    return [operation(), Operation(VERIFY, "verification", _verify, lambda: runtime_identity("verification"))]


def capability_registry(*, bind: bool = False):
    from .control_plane import CapabilityRegistry, Port
    value = CapabilityRegistry()
    for item in operations():
        manifest = InstrumentManifest(instrument_id=item.operation_id, role=item.role, units={}, frames=(),
            sampling={"kind": "retained_measurement_declaration"}, supported_operations=(item.operation_id,),
            calibration_requirements={"authority": "conditional numerical calculation/audit; no physical qualification or proof"})
        value.advertise(manifest, runtime=item.runtime_identity(),
            capabilities={item.operation_id: ["metrology.temperature.process" if item.operation_id == PROCESS else "metrology.temperature.verify"]},
            inputs={item.operation_id: {"processing": Port("ciw.operation-result.v1").to_dict()} if item.operation_id == VERIFY else {}})
        if bind:
            value.bind(item)
    return value


def validate_payload(operation_id: str, data: dict, run: dict, parameters: dict, selection: dict) -> None:
    from .temperature_processing import validate_report
    if operation_id not in OPERATIONS:
        raise ValueError("Unsupported temperature operation")
    if selection["channel"] != "measurement_declaration" or selection["interval_s"] != [0.0, 1.0]:
        raise ValueError("Temperature processing requires the complete measurement declaration")
    request = source_request(run)
    if operation_id == PROCESS:
        keys(parameters, set())
        validate_report(request, data)
        return
    candidate = _candidate(run, parameters)
    keys(data, {"schema", "verification_id", "processing_result_id", "processing_execution_id",
                "processing_record_digest", "request_ref", "processing_report_ref", "report", "authority"})
    if (data["schema"] != "ciw.temperature-verification-payload.v1" or data["authority"] != AUTHORITY
            or data["request_ref"] != digest(request) or data["processing_report_ref"] != digest(candidate["data"])
            or data["processing_result_id"] != candidate["result_id"]
            or data["processing_execution_id"] != candidate["execution_id"]
            or data["processing_record_digest"] != candidate["record_digest"]):
        raise ValueError("Temperature audit differs from its retained processing occurrence or authority")
    validate_identity(data["verification_id"], "verification")
    from .temperature_verification import validate_verification
    validate_verification(request, candidate["data"], data["report"])


def validate_live_dependency(parameters: dict, retained_results: dict) -> None:
    keys(parameters, {"processing"})
    candidate = parameters["processing"]
    if (type(candidate) is not dict or type(candidate.get("result_id")) is not str
            or retained_results.get(candidate["result_id"]) != candidate):
        raise ValueError("Temperature dependency is not the actually retained processing occurrence")


def validate_result_dependencies(results: dict) -> None:
    verification_ids = set()
    for result in results.values():
        operation_id = result.get("operation_id")
        if operation_id not in OPERATIONS:
            continue
        validate_runtime(operation_id, result["runtime"])
        if operation_id == VERIFY:
            validate_live_dependency(result["parameters"], results)
            identity = result["data"]["verification_id"]
            if identity in verification_ids:
                raise ValueError("Duplicate temperature verification occurrence identity")
            verification_ids.add(identity)
