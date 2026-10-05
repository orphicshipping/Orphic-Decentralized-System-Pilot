"""Retain typed datasets and consume them through the existing NET workspace."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import tempfile

from .adapters.protocol import AdapterRefusal
from .control_contracts import load
from .data_sources import MAX_BYTES, _source, csv_observation_stream, descriptor, retain_record

MAX_WORKSPACE_BYTES = 128 * 1024 * 1024


def _read(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Data input is empty or exceeds the bounded profile")
    return raw


def _save_new(path, value, maximum):
    """Create-only bounded publication; data bytes need not fit control text limits."""
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(raw) > maximum:
        raise ValueError("Data output exceeds its declared byte budget")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".net-data-", dir=path.parent) as directory:
        staged = Path(directory) / "data.json"
        with staged.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net data", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory", help="List actual source, consumer and package routes with explicit gaps")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("source", type=Path)
    for name in ("retain", "from-csv"):
        command = commands.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("--dataset-id", required=True)
        command.add_argument("--revision", type=int, required=True)
        command.add_argument("--parent-source-id")
        command.add_argument("--recorded-at", required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "retain":
            command.add_argument("--family", required=True)
        else:
            command.add_argument("--mapping", type=Path, required=True)
    gltf = commands.add_parser("from-gltf", help="Retain bounded local glTF plus exact sibling buffers")
    gltf.add_argument("input", type=Path)
    gltf.add_argument("--dataset-id", required=True)
    gltf.add_argument("--revision", type=int, required=True)
    gltf.add_argument("--parent-source-id")
    gltf.add_argument("--recorded-at", required=True)
    gltf.add_argument("--source", required=True)
    gltf.add_argument("--publisher-revision", required=True)
    gltf.add_argument("--license", required=True)
    gltf.add_argument("--attribution", required=True)
    gltf.add_argument("--output", type=Path, required=True)
    gltf_project = commands.add_parser("project-gltf", help="Map one whole seam-indexed component to the existing mesh consumer")
    gltf_project.add_argument("--source-id", required=True)
    gltf_project.add_argument("--source-vertex", type=int, required=True)
    gltf_project.add_argument("--target-vertex", type=int, required=True)
    gltf_project.add_argument("--label", required=True)
    gltf_inspect = commands.add_parser("inspect-gltf", help="Import retained glTF with an explicitly bound Godot inspector")
    gltf_inspect.add_argument("--source-id", required=True)
    gltf_inspect.add_argument("--godot", type=Path, required=True)
    gltf_inspect.add_argument("--godot-sha256", required=True)
    add = commands.add_parser("add", help="Add an immutable source to a new copy of an existing workspace")
    add.add_argument("source", type=Path)
    add.add_argument("--label", required=True)
    add.add_argument("--input-source-id", action="append", default=[])
    compare = commands.add_parser("compare", help="Run the existing numerical observation comparison")
    compare.add_argument("--left", required=True)
    compare.add_argument("--right", required=True)
    compare.add_argument("--quantity", required=True)
    compare.add_argument("--atol", type=float, required=True)
    compare.add_argument("--rtol", type=float, default=0.0)
    project = commands.add_parser("project-observations", help="Extract a native field/thermal stream with an automatic retained dependency")
    project.add_argument("--source-id", required=True)
    project.add_argument("--channel")
    project.add_argument("--dataset-id", required=True)
    project.add_argument("--revision", type=int, required=True)
    project.add_argument("--parent-source-id")
    project.add_argument("--recorded-at", required=True)
    project.add_argument("--label", required=True)
    for command in (add, compare, project, gltf_project, gltf_inspect):
        command.add_argument("--workspace", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            from .data_inventory import inventory
            result = inventory()
        elif args.command == "inspect":
            result = descriptor(_source(_read(args.source)))
        elif args.command == "from-gltf":
            from .gltf_reference import read_local
            record = read_local(args.input, provenance={"source": args.source, "revision": args.publisher_revision,
                "license": args.license, "attribution": args.attribution})
            value = retain_record(record, dataset_id=args.dataset_id, revision=args.revision,
                parent_source_id=args.parent_source_id, recorded_at=args.recorded_at, data_family="gltf-reference")
            _save_new(args.output, value, MAX_BYTES)
            result = {"status": "retained", "source": descriptor(value), "output": str(args.output)}
        elif args.command in {"retain", "from-csv"}:
            kwargs = {"dataset_id": args.dataset_id, "revision": args.revision,
                      "parent_source_id": args.parent_source_id, "recorded_at": args.recorded_at}
            if args.command == "retain":
                value = retain_record(_read(args.input), data_family=args.family, **kwargs)
            else:
                value = csv_observation_stream(_read(args.input), mapping=load(args.mapping), **kwargs)
            _save_new(args.output, value, MAX_BYTES)
            result = {"status": "retained", "source": descriptor(value), "output": str(args.output)}
        else:
            from .session import Session
            if args.output.exists():
                raise ValueError("Output workspace already exists; choose a new path")
            # Reopening never writes into the input workspace's directory.
            with tempfile.TemporaryDirectory(prefix="net-data-") as directory:
                session = Session.from_workspace(args.workspace, output_dir=Path(directory))
                if args.command == "add":
                    raw = _read(args.source)
                    _source(raw)
                    payload = {"kind": "data-record", "label": args.label,
                               "bytes_b64": base64.b64encode(raw).decode("ascii")}
                    if args.input_source_id:
                        payload["input_source_ids"] = args.input_source_id
                    result = session.workbench.add_source(payload)
                elif args.command in {"project-gltf", "inspect-gltf"}:
                    from . import gltf_reference_runtime as bridge
                    parameters = bridge.source_parameters(session.workbench, args.source_id,
                        **({"source_vertex": args.source_vertex, "target_vertex": args.target_vertex}
                           if args.command == "project-gltf" else {}))
                    operation = bridge.PROJECT if args.command == "project-gltf" else bridge.INSPECT
                    if args.command == "inspect-gltf":
                        session.operations.register(bridge.GodotRuntime(args.godot,
                            expected_sha256=args.godot_sha256).operation())
                    answer = session.handle({"protocol_version": 1, "request_id": "data-gltf",
                        "type": "operation.execute", "payload": {"operation_id": operation, "parameters": parameters}})
                    if answer["type"] == "error":
                        raise ValueError(answer["payload"]["message"])
                    result = answer["payload"]
                    if args.command == "project-gltf" and result["status"] == "completed":
                        result = {**result, "mesh_source": bridge.retain_mesh_source(session.workbench,
                            parameters, label=args.label)}
                elif args.command == "project-observations":
                    from .data_workflow import project_observations
                    result = project_observations(session.workbench, source_id=args.source_id,
                        selector={} if args.channel is None else {"channel": args.channel},
                        dataset_id=args.dataset_id, revision=args.revision,
                        parent_source_id=args.parent_source_id, recorded_at=args.recorded_at, label=args.label)
                else:
                    from .data_workflow import COMPARE, comparison_parameters
                    result = session.handle({"protocol_version": 1, "request_id": "data-compare",
                        "type": "operation.execute", "payload": {"operation_id": COMPARE,
                            "parameters": comparison_parameters(session.workbench,
                                left_source_id=args.left, right_source_id=args.right,
                                quantity=args.quantity, atol=args.atol, rtol=args.rtol)}})
                    if result["type"] == "error":
                        raise ValueError(result["payload"]["message"])
                    result = result["payload"]
                _save_new(args.output, session._workspace(), MAX_WORKSPACE_BYTES)
                result = {"output": str(args.output), **result}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") == "refused" else 0
    except (ValueError, OSError, AdapterRefusal) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
