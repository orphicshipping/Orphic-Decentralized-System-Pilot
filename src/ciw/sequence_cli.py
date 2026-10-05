"""NET sequence diagnostics and an independent binary-site reference model."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

from . import sequence_kinetics as kernel


def load_request(path: Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Require a regular nonsymlink sequence request")
    with path.open("rb") as stream:
        raw = stream.read(kernel.MAX_BYTES + 1)
    if not raw or len(raw) > kernel.MAX_BYTES:
        raise ValueError("Empty or oversized request")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def finite(token):
        value = float(token)
        if not math.isfinite(value) or (value == 0 and Decimal(token) != 0):
            raise ValueError("Nonfinite or underflowing input number")
        return value

    def reject(token):
        raise ValueError("Nonfinite JSON constant: " + token)

    return kernel.validate_request(json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                                              parse_float=finite, parse_constant=reject))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net polymer sequence", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example")
    example.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("request", type=Path)
    run.add_argument("--output-dir", type=Path, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("directory", type=Path)
    replay = commands.add_parser("replay")
    replay.add_argument("directory", type=Path)
    replay.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from . import sequence_workflow as workflow
        if args.command == "example":
            from .control_contracts import save_new
            save_new(args.output, kernel.example_request())
            result = {"status": "created", "source_kind": "synthetic", "request": str(args.output)}
        elif args.command == "run":
            result = workflow.run(load_request(args.request), args.output_dir)
        elif args.command == "inspect":
            result = workflow.inspect(args.directory)
        else:
            result = workflow.replay(args.directory, args.output_dir)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") == "REFUSE" else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
