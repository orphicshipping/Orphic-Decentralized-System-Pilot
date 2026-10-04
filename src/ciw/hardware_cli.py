"""Import hardware characterization, assess declared criteria, and export evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import hardware_contract as contract, hardware_workflow as workflow
from .control_contracts import save_new
from .session import loads_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net hardware", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example", help="Write a synthetic request; no equipment is tested")
    example.add_argument("--output", type=Path, required=True)
    configuration = commands.add_parser("configuration", help="Validate a configuration and show its content identity")
    configuration.add_argument("configuration", type=Path)
    configuration.add_argument("--output", type=Path)
    run = commands.add_parser("run", help="Import declared observations and execute the numeric assessment")
    run.add_argument("request", type=Path)
    run.add_argument("--output-dir", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="Read and validate retained evidence without provider execution")
    inspect.add_argument("directory", type=Path)
    replay = commands.add_parser("replay", help="Create a fresh assessment execution from retained inputs")
    replay.add_argument("directory", type=Path)
    replay.add_argument("--output-dir", type=Path, required=True)
    report = commands.add_parser("report", help="Export a customer-readable HTML report of the retained outcome")
    report.add_argument("directory", type=Path)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            save_new(args.output, contract.example_request())
            result = {"status": "created", "source_kind": "synthetic", "request": str(args.output)}
        elif args.command == "configuration":
            value = contract.validate_configuration(loads_json(workflow.read_bytes(args.configuration).decode("utf-8")))
            result = {"schema": "ciw.hardware-configuration-identity.v1", "asset_id": value["asset_id"],
                      "revision": value["revision"], "configuration_ref": contract.configuration_ref(value)}
            if args.output is not None:
                save_new(args.output, result)
        elif args.command == "run":
            raw = workflow.read_bytes(args.request)
            result = workflow.run(workflow.parse_request(raw), args.output_dir, raw=raw)
        elif args.command == "inspect":
            result = workflow.inspect(args.directory)
        elif args.command == "replay":
            result = workflow.replay(args.directory, args.output_dir)
        else:
            result = workflow.export_report(args.directory, args.output)
        print(json.dumps(result, indent=2, allow_nan=False))
        return {"FAIL": 2, "INDETERMINATE": 3, "REFUSED": 1}.get(result.get("status"), 0)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}), file=sys.stderr)
        return 1
