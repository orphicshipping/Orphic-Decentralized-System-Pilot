"""Retain private-core metrics and handheld transport context without core control."""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import sys

from . import connectivity as contract
from . import connectivity_workflow as workflow
from .control_contracts import save_new
from .hardware_workflow import read_bytes
from .session import loads_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net connectivity", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog", help="Inspect profiles; no core or network execution")
    for name in ("context", "example"):
        sub = commands.add_parser(name, help="Write explicitly synthetic input")
        sub.add_argument("--provider", choices=("open5gs", "free5gc"), default="open5gs")
        sub.add_argument("--output", type=Path, required=True)
    imported = commands.add_parser("import", help="Project one operator-supplied metrics export")
    imported.add_argument("context", type=Path)
    imported.add_argument("metrics", type=Path)
    imported.add_argument("--observed-at", required=True)
    imported.add_argument("--clock-id", required=True)
    imported.add_argument("--output", type=Path, required=True)
    capture = commands.add_parser("capture", help="One explicit GET from an authorized loopback /metrics endpoint")
    capture.add_argument("context", type=Path)
    capture.add_argument("--endpoint", required=True)
    capture.add_argument("--timeout-s", type=float, default=3.0)
    capture.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="Execute the existing Session observation operation")
    run.add_argument("request", type=Path)
    run.add_argument("--output-dir", type=Path, required=True)
    for name in ("inspect", "replay", "report"):
        sub = commands.add_parser(name)
        sub.add_argument("directory", type=Path)
        if name == "replay":
            sub.add_argument("--output-dir", type=Path, required=True)
        elif name == "report":
            sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            result = contract.catalog()
        elif args.command in ("context", "example"):
            value = contract.example_context(args.provider) if args.command == "context" else contract.example_request(args.provider)
            save_new(args.output, value)
            result = {"status": "created", "source_kind": "synthetic", "path": str(args.output)}
        elif args.command in ("import", "capture"):
            # Refuse an existing output before making even the explicit GET.
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("Output already exists")
            context = contract.validate_context(loads_json(read_bytes(args.context, limit=16384).decode("utf-8")))
            from . import connectivity_capture
            if args.command == "capture":
                value = connectivity_capture.capture(context, args.endpoint, timeout_s=args.timeout_s)
            else:
                value = connectivity_capture.imported_request(context,
                    read_bytes(args.metrics, limit=contract.MAX_EXPORT_BYTES),
                    observed_at=args.observed_at, clock_id=args.clock_id)
            save_new(args.output, value)
            result = {"status": "captured" if args.command == "capture" else "imported",
                      "request": str(args.output), "selected_lines": len(value["metrics"]["selected_lines"]),
                      "whole_export_retained": False}
        elif args.command == "run":
            raw = read_bytes(args.request, limit=contract.MAX_REQUEST_BYTES)
            result = workflow.run(workflow.parse_request(raw), args.output_dir, raw=raw)
        elif args.command == "inspect":
            result = workflow.inspect(args.directory)
        elif args.command == "replay":
            result = workflow.replay(args.directory, args.output_dir)
        else:
            result = workflow.report(args.directory, args.output)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") in ("MISSING_METRICS", "REFUSED") else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, http.client.HTTPException) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
