"""Offline manufacturing-plan screening; no machine commands or physical qualification."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile

from . import microfab


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Require a regular, nonsymlink JSON file")
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Require a regular JSON file")
        raw = stream.read(microfab.MAX_BYTES + 1)
    if not raw or len(raw) > microfab.MAX_BYTES:
        raise ValueError("Empty or oversized microfab JSON file")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def finite(token):
        number = float(token)
        if not math.isfinite(number) or (number == 0 and Decimal(token) != 0):
            raise ValueError("Nonfinite or underflowing JSON number")
        return number

    def reject(token):
        raise ValueError(f"Unsupported JSON constant: {token}")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_float=finite, parse_constant=reject)
    microfab.bounded(value)
    return value


def save_new(path, value):
    """Stage and publish create-only, never replacing an existing destination."""
    microfab.bounded(value)
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > microfab.MAX_BYTES:
        raise ValueError("Serialized microfab document exceeds the byte budget")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".net-microfab-", dir=path.parent) as directory:
        staged = Path(directory) / "record.json"
        with staged.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net polymer microfab", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example", help="Write an explicitly synthetic plan")
    example.add_argument("--output", type=Path, required=True)
    compile_parser = commands.add_parser("compile", help="Intersect declared constraints only")
    compile_parser.add_argument("plan", type=Path)
    compile_parser.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="Fresh deterministic reconstruction, not physical verification")
    verify.add_argument("plan", type=Path)
    verify.add_argument("envelope", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            save_new(args.output, microfab.example_plan())
            result = {"status": "created", "source_kind": "synthetic", "plan": str(args.output)}
        elif args.command == "compile":
            result = microfab.compile_envelope(load(args.plan))
            save_new(args.output, result)
        else:
            result = microfab.verify_envelope(load(args.plan), load(args.envelope))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("envelope_status", result["status"]) in ("HOLD", "REFUSE") else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
