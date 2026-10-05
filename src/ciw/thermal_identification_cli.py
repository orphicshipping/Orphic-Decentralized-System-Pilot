"""NET polymer identify: retained cooling-model estimation, not machine control."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .control_contracts import load, save_new
from . import thermal_identification as thermal
from . import thermal_identification_workflow as workflow


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["experiment"]:
        from .thermal_experiment_workflow import main as experiment_main
        return experiment_main(argv[1:])
    parser = argparse.ArgumentParser(prog="net polymer identify", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("experiment", help="Assess a frozen model against a separate declared experiment")
    example = commands.add_parser("example")
    example.add_argument("--output", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("request", type=Path)
    run.add_argument("--output-dir", required=True, type=Path)
    demo = commands.add_parser("demo")
    demo.add_argument("--output-dir", required=True, type=Path)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("directory", type=Path)
    replay = commands.add_parser("replay")
    replay.add_argument("directory", type=Path)
    replay.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            save_new(args.output, thermal.example_request())
            result = {"status": "created", "source_kind": "synthetic", "request": str(args.output)}
        elif args.command == "demo":
            result = workflow.run(thermal.example_request(), args.output_dir)
        elif args.command == "run":
            if args.request.is_symlink() or not args.request.is_file() or args.request.stat().st_size > thermal.MAX_BYTES:
                raise ValueError("Require a bounded regular thermal request file")
            result = workflow.run(load(args.request), args.output_dir)
        elif args.command == "inspect":
            result = workflow.inspect(args.directory)
        else:
            result = workflow.replay(args.directory, args.output_dir)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("candidate_status") in {"rejected", "unavailable"} else 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"status": "refused", "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
