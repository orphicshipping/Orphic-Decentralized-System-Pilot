"""Thermal identification on NET's existing Session and operation envelopes."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import platform
import math
import tempfile
import uuid

import numpy as np

from .adapters.protocol import InstrumentManifest
from .control_contracts import keys, load, save_new
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation, default_registry
from . import thermal_identification as thermal

OPERATION = "polymer.identify-thermal-response.v1"
MAX_WORKSPACE_BYTES = 24 * 1024 * 1024


def runtime_identity() -> dict:
    from .core import covariance, identities
    modules = [thermal, covariance, identities, __import__(__name__, fromlist=["*"])]
    content = b"\0".join(Path(m.__file__).name.encode() + b"\0" +
        Path(m.__file__).read_text(encoding="utf-8").replace("\r\n", "\n").encode() for m in modules)
    return {"provider": "ciw.thermal_identification", "version": "1",
            "code_sha256": sha256(content).hexdigest(), "source_normalization": "utf8_lf",
            "scope": "conditional_cooling_model_identification_only",
            "environment": {"python": platform.python_version(), "numpy": np.__version__}}


def make_source(request: dict) -> dict:
    request = thermal.validate_request(request)
    manifest = InstrumentManifest(instrument_id="thermal-identification-evidence.v1",
        role="retained_thermal_evidence", units={"trace_declaration": "1"}, frames=(request["frame"],),
        sampling={"kind": "one_trace_declaration", "time_semantics": "synthetic_selection_envelope"},
        supported_operations=(OPERATION,),
        calibration_requirements={"authority": "references are unattested; no physical qualification"})
    source = {"run_schema": "run.v1", "run_id": "run-thermal-id-" + thermal.content_identity(request)[7:23],
        "instrument": manifest.instrument_id,
        "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
                     "coordinate_frame": request["frame"], "manifest": manifest.to_dict(),
                     "thermal_identification_request": request,
                     "provenance": {"source": request["source_kind"],
                                    "generator": "ciw.thermal_identification_workflow.make_source",
                                    "generator_version": 1}},
        "time_s": [0.0], "channels": {"trace_declaration": {"unit": "1", "values": [1.0]}}, "render": {}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_request(source: dict) -> dict:
    validate_run_structure(source)
    validate_evidence_identity(source)
    request = thermal.validate_request(source["metadata"]["thermal_identification_request"])
    if source != make_source(request):
        raise ValueError("Thermal source differs from the retained trace declaration")
    return request


def _execute(source: dict, parameters: dict) -> dict:
    keys(parameters, set())
    return thermal.fit(source_request(source))


def operation() -> Operation:
    return Operation(OPERATION, "state_estimator", _execute, runtime_identity)


def registry():
    result = default_registry()
    result.register(operation())
    return result


def validate_payload(operation_id: str, data: dict, source: dict, parameters: dict, selection: dict) -> None:
    if operation_id != OPERATION:
        raise ValueError("Unsupported thermal identification operation")
    keys(parameters, set())
    thermal.validate_result(source_request(source), data)


def run(request: dict, destination: Path) -> dict:
    from .session import Session
    from .model_reality import make
    source = make_source(request)
    request = source_request(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    save_new(destination / "request.json", source_request(source))
    session = Session(source, destination, operations=registry())
    response = session.handle({"protocol_version": 1, "request_id": uuid.uuid4().hex,
        "type": "operation.execute", "payload": {"operation_id": OPERATION, "parameters": {}}})
    if response["type"] != "response":
        raise ValueError(response["payload"]["message"])
    session.save_workspace(destination / "workspace.json")
    if response["payload"]["status"] == "completed":
        result = response["payload"]["result"]["data"]
        # A rejected estimate remains in the result evidence, but is not exposed
        # as an admitted/usable Model-Reality parameter state.
        if result["status"] == "candidate":
            covariance = result["parameter_covariance"]
            state = make(entity_id=request["identity"]["part_id"], quantity="thermal_time_constant", unit="s",
                frame="thermal.parameter.v1", clock_id=request["clock_id"], states={"estimated": {
                    "value": result["model"]["tau_s"], "standard_uncertainty": covariance["matrix"][1][1] ** 0.5,
                    "time_s": request["samples"][-1]["time_s"], "source_ref": result["record_digest"],
                    "uncertainty_scope": "conditional_model",
                    "origin": "synthetic" if request["source_kind"] == "synthetic" else "estimation"}})
            save_new(destination / "parameter-state.json", state)
    return inspect(destination)


def _read(destination: Path):
    from .session import Session
    from .model_reality import validate as validate_reality
    destination = Path(destination)
    for name, limit in (("request.json", thermal.MAX_BYTES), ("workspace.json", MAX_WORKSPACE_BYTES)):
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError("Require bounded regular thermal identification files")
    with tempfile.TemporaryDirectory(prefix="net-thermal-review-") as directory:
        session = Session.from_workspace(destination / "workspace.json", Path(directory))
    request = source_request(session.run)
    if load(destination / "request.json") != request:
        raise ValueError("Request sidecar differs from retained source")
    executions = list(session.executions.values())
    if len(executions) != 1 or executions[0]["operation_id"] != OPERATION:
        raise ValueError("Require exactly one retained thermal identification attempt")
    execution = executions[0]
    results = list(session.results.values())
    if execution["status"] == "completed":
        if len(results) != 1 or results[0]["result_id"] != execution["result_id"]:
            raise ValueError("Require the retained result of this execution")
        result = results[0]
    else:
        if results:
            raise ValueError("A refused execution cannot retain a result")
        result = None
    state_path = destination / "parameter-state.json"
    requires_state = result is not None and result["data"]["status"] == "candidate"
    if requires_state and not state_path.is_file():
        raise ValueError("Candidate bundle is missing its required parameter state")
    if state_path.exists() or state_path.is_symlink():
        if state_path.is_symlink() or not state_path.is_file() or state_path.stat().st_size > thermal.MAX_BYTES:
            raise ValueError("Require a bounded regular parameter state")
        state = validate_reality(load(state_path))
        if result is None or result["data"]["status"] != "candidate":
            raise ValueError("An unavailable/rejected candidate cannot expose a parameter state")
        data = result["data"]
        estimated = state["states"]["estimated"]
        if (state["entity_id"] != request["identity"]["part_id"] or state["quantity"] != "thermal_time_constant"
                or state["unit"] != "s" or state["frame"] != "thermal.parameter.v1"
                or state["clock_id"] != request["clock_id"] or estimated is None
                or estimated["source_ref"] != data["record_digest"]
                or estimated["time_s"] != request["samples"][-1]["time_s"]
                or estimated["uncertainty_scope"] != "conditional_model"
                or estimated["origin"] != ("synthetic" if request["source_kind"] == "synthetic" else "estimation")
                or any(value is not None for key, value in state["states"].items() if key != "estimated")):
            raise ValueError("Parameter-state source binding differs")
        if estimated["value"] != data["model"]["tau_s"]:
            raise ValueError("Parameter state must preserve the retained parameter value exactly")
        expected_uncertainty = float(data["parameter_covariance"]["matrix"][1][1] ** 0.5)
        if not math.isclose(estimated["standard_uncertainty"], expected_uncertainty, rel_tol=5e-11, abs_tol=0.0):
            raise ValueError("Parameter-state uncertainty differs at its own physical scale")
    return request, execution, result


def inspect(destination: Path) -> dict:
    request, execution, result = _read(destination)
    data = result["data"] if result is not None else None
    return {"schema": "ciw.thermal-identification-summary.v1", "source_kind": request["source_kind"],
        "operation_id": OPERATION, "execution_status": execution["status"],
        "candidate_status": data["status"] if data is not None else "unavailable",
        "model": deepcopy(data["model"]) if data is not None else None,
        "reasons": deepcopy(data["reasons"]) if data is not None else [execution["refusal"]["message"]],
        "diagnostics": deepcopy(data["diagnostics"]) if data is not None else None,
        "review_scope": "retained_integrity_bindings_and_algebra_not_search_replay",
        "workspace": str(Path(destination) / "workspace.json"), "authority": deepcopy(thermal.AUTHORITY)}


def replay(original: Path, destination: Path) -> dict:
    request, _, first = _read(original)
    summary = run(request, destination)
    _, _, second = _read(destination)
    if first is None or second is None:
        raise ValueError("Numerical replay requires two completed result occurrences")
    # Hashes retain their exact bytes; floats are compared numerically separately.
    for key in ("status", "reasons", "model", "diagnostics", "residuals", "work", "authority"):
        thermal._numeric_equal(second["data"][key], first["data"][key], "replay." + key)
    a, b = first["data"]["parameter_covariance"], second["data"]["parameter_covariance"]
    thermal.covariance_consistent(b, a)
    if a is not None:
        thermal._numeric_equal(b["reference_values"], a["reference_values"], "replay.covariance.reference_values")
    summary["numerical_replay"] = "consistent_within_declared_float_tolerance"
    summary["replay_runtime_code_matches"] = first["runtime"]["code_sha256"] == second["runtime"]["code_sha256"]
    return summary
