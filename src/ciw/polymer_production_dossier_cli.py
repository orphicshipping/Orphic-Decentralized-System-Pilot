"""Bind retained molding cycles to serialized production and engineering review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .control_contracts import save_new
from .polymer_production_dossier_contract import example_request
from . import polymer_production_dossier as microfab


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net polymer dossier", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example", help="Create an explicitly synthetic production fixture")
    example.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="Execute bounded offline cycle workflows and retain their dossier")
    run.add_argument("request", type=Path)
    run.add_argument("--output-dir", type=Path, required=True)
    demo = commands.add_parser("demo", help="Execute the synthetic fixture; never authorize production")
    demo.add_argument("--output-dir", type=Path, required=True)
    for name in ("inspect", "verify", "replay"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name == "replay":
            command.add_argument("--output-dir", type=Path, required=True)
        if name == "verify":
            command.add_argument("--output", type=Path, help="Create-only fresh audit receipt outside the dossier")
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            save_new(args.output, example_request())
            result = {"status": "CREATED", "source_kind": "synthetic", "path": str(args.output)}
        elif args.command == "run":
            result = microfab.run(microfab.load_request(args.request), args.output_dir)
        elif args.command == "demo":
            result = microfab.run(example_request(), args.output_dir)
        elif args.command == "inspect":
            result = microfab.inspect(args.directory)
        elif args.command == "replay":
            result = microfab.replay(args.directory, args.output_dir)
        else:
            # An audit sidecar must not mutate an immutable dossier inventory.
            if args.output is not None and args.output.resolve().is_relative_to(args.directory.resolve()):
                raise ValueError("Save fresh audit receipts outside the immutable dossier directory")
            result = microfab.verify_retained(args.directory)
            if args.output is not None:
                save_new(args.output, result)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result.get("workflow_status", result.get("status")) in {"PASS", "CREATED"} else 2
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        print(json.dumps({"status": "REFUSE", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
