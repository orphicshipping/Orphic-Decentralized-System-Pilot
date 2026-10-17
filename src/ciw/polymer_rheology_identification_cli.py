"""Create and audit fixed-grid spectrum candidates; no automatic material admission."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .control_contracts import load, save_new
from . import polymer_rheology_identification as identification


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net polymer rheology identify", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example")
    example.add_argument("--output", type=Path, required=True)
    for name in ("run", "inspect", "verify", "export"):
        command = commands.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("--output", type=Path, required=name != "inspect")
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            result = identification.example_request()
        else:
            if args.input.is_symlink() or not args.input.is_file():
                raise ValueError("Require a regular input file, not a symlink")
            value = load(args.input)
            if args.command == "run":
                result = identification.identify(value)
            elif args.command == "verify":
                result = identification.verify(value)
            else:
                identification.validate_result(value)
                if args.command == "export":
                    result = value["candidate_response_request"]
                    if result is None:
                        raise ValueError("No admissible forward export: " + value["candidate_export_refusal"])
                else:
                    result = {"status": "STRUCTURALLY_VALID", "result_id": value["result_id"],
                              "record_digest": value["record_digest"], "holdout_status": value["holdout_status"],
                              "fresh_numerical_verification": False, "authority": value["authority"]}
        if args.output is not None:
            save_new(args.output, result)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") == "FAIL" or result.get("holdout_status") == "OUTSIDE_DECLARED_LIMIT" else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
