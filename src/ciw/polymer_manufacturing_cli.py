"""Offline NET Polymer Microfab + Discrete Manufacturing Runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .manufacturing_contracts import load, save_new
from .manufacturing_example import example_request
from .manufacturing_runtime import evaluate, trace, verify
from .polymer_microfab import compile_configuration


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net polymer manufacture", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("example", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--quality-control", action="store_true", help="Use the v2 tool-point QC fixture")
    for name in ("plan", "run", "verify", "trace"):
        command = commands.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("--output", type=Path, required=name == "run")
        if name == "trace":
            command.add_argument("--serial", required=True)
        if name == "plan":
            command.add_argument("--at-s", type=float, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command in {"example", "demo"}:
            if args.quality_control:
                from .polymer_quality_example import manufacturing_request
                fixture = manufacturing_request()
            else:
                fixture = example_request()
            result = fixture if args.command == "example" else evaluate(fixture)
        else:
            value = load(args.input)
            if args.command == "run":
                result = evaluate(value)
            elif args.command == "verify":
                result = verify(value)
            elif args.command == "trace":
                result = trace(value, args.serial)
            else:
                result = compile_configuration(value["configuration"], value["preflight"], at_s=args.at_s)
        if args.output is not None:
            save_new(args.output, result)
            shown = {"schema": result["schema"], "output": str(args.output)}
            if "result" in result:
                shown.update({"status": result["result"]["status"], "result_ref": result["result_ref"],
                              "orders": result["result"]["orders"], "shipment": result["result"]["shipment"]})
            elif "status" in result:
                shown["status"] = result["status"]
        else:
            shown = result
        print(json.dumps(shown, indent=2, allow_nan=False))
        return 2 if result.get("status") in {"INFEASIBLE", "INSUFFICIENT_EVIDENCE"} else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
