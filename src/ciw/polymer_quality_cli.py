"""Retained tool-point quality assessment. No acquisition or hardware commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .manufacturing_contracts import load, save_new
from .polymer_quality_example import CASES, example_request
from .polymer_toolpoint_qc import GOOD, evaluate, verify


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net polymer qc", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("example", "demo", "assess", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=name == "example")
        if name in {"example", "demo"}:
            command.add_argument("--case", choices=sorted(CASES), default="normal")
        else:
            command.add_argument("input", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            result = example_request(args.case)
        elif args.command == "demo":
            result = evaluate(example_request(args.case))
        elif args.command == "assess":
            result = evaluate(load(args.input))
        else:
            result = verify(load(args.input))
        if args.output is not None:
            save_new(args.output, result)
        status = result.get("result", result).get("status")
        shown = result if args.output is None else {"schema": result["schema"], "output": str(args.output), "status": status}
        print(json.dumps(shown, indent=2, allow_nan=False))
        return 0 if status in {None, "PASS", GOOD} else 2
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
