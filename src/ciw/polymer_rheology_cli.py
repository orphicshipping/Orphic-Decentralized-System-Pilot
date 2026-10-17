"""Create, inspect and independently verify data-only polymer rheology records."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .control_contracts import load, save_new
from . import polymer_rheology as rheology


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "identify":
        from .polymer_rheology_identification_cli import main as identify_main
        return identify_main(argv[1:])
    parser = argparse.ArgumentParser(prog="net polymer rheology", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example")
    example.add_argument("--output", type=Path, required=True)
    for name in ("analyze", "inspect", "verify"):
        command = commands.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("--output", type=Path, required=name in ("analyze", "verify"))
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            result = rheology.example_request()
        else:
            if args.input.is_symlink() or not args.input.is_file():
                raise ValueError("Require a regular input file, not a symlink")
            value = load(args.input)
            if args.command == "analyze":
                result = rheology.analyze(value)
            elif args.command == "verify":
                result = rheology.verify(value)
            else:
                rheology.validate_result(value)
                result = {"status": "STRUCTURALLY_VALID", "result_id": value["result_id"],
                          "record_digest": value["record_digest"], "fresh_numerical_verification": False,
                          "authority": value["authority"]}
        if args.output is not None:
            save_new(args.output, result)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") == "FAIL" else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1
