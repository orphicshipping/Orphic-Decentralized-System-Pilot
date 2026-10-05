"""Separate thermal experiment assessment through NET's existing Session."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import platform
import sys
import tempfile
import uuid

import numpy as np

from .adapters.protocol import InstrumentManifest
from .control_contracts import keys, load, save_new
from .core.identities import content_identity, evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation, default_registry
from . import thermal_experiment as experiment
from . import thermal_identification as thermal

OPERATION = "polymer.assess-thermal-experiment.v1"
MAX_WORKSPACE_BYTES = 24 * 1024 * 1024


def runtime_identity() -> dict:
    from .core import covariance, identities
    from . import session, thermal_identification_workflow
    modules = [experiment, thermal, covariance, identities, session, thermal_identification_workflow,
               __import__(__name__, fromlist=["*"])]
    raw = b"\0".join(Path(m.__file__).name.encode() + b"\0" +
        Path(m.__file__).read_text(encoding="utf-8").replace("\r\n", "\n").encode() for m in modules)
    return {"provider": "ciw.thermal_experiment", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "source_normalization": "utf8_lf", "scope": "frozen_model_conditional_experiment_assessment",
            "environment": {"python": platform.python_version(), "numpy": np.__version__}}


def make_source(request: dict) -> dict:
    request = experiment.validate_request(request)
    trace = request["experiment"]
    manifest = InstrumentManifest(instrument_id="thermal-experiment-evidence.v1",
        role="retained_separate_experiment", units={"experiment_declaration": "1"}, frames=(trace["frame"],),
        sampling={"kind": "one_experiment_declaration", "time_semantics": "synthetic_selection_envelope"},
        supported_operations=(OPERATION,), calibration_requirements={"authority": "not established by hashes"})
    source = {"run_schema": "run.v1", "run_id": "run-thermal-experiment-" + content_identity(request)[7:23],
        "instrument": manifest.instrument_id, "metadata": {"duration_s": 1.0, "sample_count": 1,
        "sample_rate_hz": None, "coordinate_frame": trace["frame"], "manifest": manifest.to_dict(),
        "thermal_experiment_request": request, "provenance": {"source": trace["source_kind"],
            "generator": "ciw.thermal_experiment_workflow.make_source", "generator_version": 1}},
        "time_s": [0.0], "channels": {"experiment_declaration": {"unit": "1", "values": [1.0]}}, "render": {}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_request(source: dict) -> dict:
    validate_run_structure(source)
    validate_evidence_identity(source)
    request = experiment.validate_request(source["metadata"]["thermal_experiment_request"])
    if source != make_source(request):
        raise ValueError("Experiment source differs from its retained declaration")
    return request


def _execute(source: dict, parameters: dict) -> dict:
    keys(parameters, set())
    return experiment.assess(source_request(source))


def operation() -> Operation:
    return Operation(OPERATION, "backend", _execute, runtime_identity)


def registry():
    result = default_registry()
    result.register(operation())
    return result


def validate_payload(operation_id, data, source, parameters, selection) -> None:
    if operation_id != OPERATION:
        raise ValueError("Unsupported thermal experiment operation")
    keys(parameters, set())
    experiment.validate_result(source_request(source), data)


def run(request: dict, destination: Path) -> dict:
    from .session import Session
    source = make_source(request)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    save_new(destination / "request.json", source_request(source))
    session = Session(source, destination, operations=registry())
    response = session.handle({"protocol_version": 1, "request_id": uuid.uuid4().hex,
        "type": "operation.execute", "payload": {"operation_id": OPERATION, "parameters": {}}})
    if response["type"] != "response":
        raise ValueError(response["payload"]["message"])
    session.save_workspace(destination / "workspace.json")
    return inspect(destination)


def _read(destination: Path):
    from .session import Session
    destination = Path(destination)
    for name, limit in (("request.json", experiment.MAX_BYTES), ("workspace.json", MAX_WORKSPACE_BYTES)):
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError("Require bounded regular thermal experiment files")
    with tempfile.TemporaryDirectory(prefix="net-experiment-review-") as directory:
        session = Session.from_workspace(destination / "workspace.json", Path(directory))
    request = source_request(session.run)
    if load(destination / "request.json") != request:
        raise ValueError("Experiment sidecar differs from retained source")
    executions, results = list(session.executions.values()), list(session.results.values())
    if len(executions) != 1 or executions[0]["operation_id"] != OPERATION:
        raise ValueError("Require exactly one retained experiment assessment occurrence")
    execution = executions[0]
    if execution["status"] == "completed":
        if len(results) != 1 or execution["result_id"] != results[0]["result_id"]:
            raise ValueError("Experiment result and execution differ")
        result = results[0]
    else:
        if results:
            raise ValueError("A refused execution cannot supply a result")
        result = None
    # The new assessment occurrence cannot reuse any retained training identity.
    training = request["training_result"]
    if (execution["execution_id"] == training["execution_id"]
            or result is not None and result["result_id"] == training["result_id"]):
        raise ValueError("Experiment assessment and training occurrence identities collide")
    return request, execution, result


def inspect(destination: Path) -> dict:
    request, execution, result = _read(destination)
    data = result["data"] if result is not None else None
    return {"schema": "ciw.thermal-experiment-summary.v1", "source_kind": request["experiment"]["source_kind"],
        "execution_status": execution["status"], "assessment_status": data["status"] if data else "unavailable",
        "training_result_id": request["training_result"]["result_id"], "operation_id": OPERATION,
        "metrics": deepcopy(data["metrics"]) if data else None,
        "reasons": deepcopy(data["reasons"]) if data else [execution["refusal"]["message"]],
        "model_ref": request["training_result"]["data"]["model_ref"],
        "review_scope": "retained_occurrence_bindings_and_residual_algebra_no_refit",
        "workspace": str(Path(destination) / "workspace.json"), "authority": deepcopy(experiment.AUTHORITY)}


def replay(original: Path, destination: Path) -> dict:
    request, _, first = _read(original)
    summary = run(request, destination)
    _, _, second = _read(destination)
    if first is None or second is None:
        raise ValueError("Replay requires two completed experiment assessments")
    a, b = deepcopy(first["data"]), deepcopy(second["data"])
    thermal.covariance_consistent(b["residual_covariance"], a["residual_covariance"])
    for data in (a, b):
        data.pop("record_digest")
        if data["residual_covariance"] is not None:
            data["residual_covariance"].pop("covariance_id")
            data["residual_covariance"].pop("matrix")
    thermal._numeric_equal(b, a, "experiment_replay")
    if first["execution_id"] == second["execution_id"] or first["result_id"] == second["result_id"]:
        raise ValueError("Replay must allocate fresh occurrence identities")
    summary["numerical_replay"] = "consistent_within_declared_float_tolerance"
    summary["replay_runtime_code_matches"] = first["runtime"]["code_sha256"] == second["runtime"]["code_sha256"]
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net polymer identify experiment", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example", help="Create a separate synthetic fixture; never physical evidence")
    example.add_argument("training_directory", type=Path)
    example.add_argument("--output", required=True, type=Path)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("request", type=Path)
    run_parser.add_argument("--output-dir", required=True, type=Path)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("directory", type=Path)
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("directory", type=Path)
    replay_parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            from .thermal_identification_workflow import _read as read_training
            training_request, execution, training_result = read_training(args.training_directory)
            if training_result is None:
                raise ValueError("An experiment fixture requires a completed training result")
            save_new(args.output, experiment.example_request(training_request, execution, training_result))
            result = {"status": "created", "source_kind": "synthetic", "request": str(args.output)}
        elif args.command == "run":
            if args.request.is_symlink() or not args.request.is_file() or args.request.stat().st_size > experiment.MAX_BYTES:
                raise ValueError("Require a bounded regular experiment request")
            result = run(load(args.request), args.output_dir)
        elif args.command == "inspect":
            result = inspect(args.directory)
        else:
            result = replay(args.directory, args.output_dir)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("assessment_status") in {"rejected", "indeterminate", "unavailable"} else 0
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "refused", "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
