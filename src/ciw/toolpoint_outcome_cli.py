"""Read-only outcome benchmark CLI; no machine commands or automatic qualification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .manufacturing_contracts import load, save_new
from .toolpoint_outcome_bench import evaluate, verify
from .toolpoint_outcome_example import example_request


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net polymer qc outcome", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("example", "demo"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--case", choices=("mixed", "missing-inspection", "late-alarm", "all-clear"), default="mixed")
        cmd.add_argument("--output", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("request", type=Path)
    run.add_argument("--output", required=True, type=Path)
    for name in ("verify", "summary"):
        cmd = commands.add_parser(name)
        cmd.add_argument("report", type=Path)
        cmd.add_argument("--expected-protocol-ref")
        cmd.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            result = example_request(case=args.case)
            save_new(args.output, result)
            print(json.dumps({"status": "CREATED", "output": str(args.output)}))
            return 0
        if args.command in {"demo", "run"}:
            request = example_request(case=args.case) if args.command == "demo" else load(args.request)
            report = evaluate(request)
            save_new(args.output, report)
            result = {"status": report["result"]["criteria_screen"]["status"], "qualification": "NOT_ESTABLISHED",
                      "result_ref": report["result_ref"], "output": str(args.output)}
        else:
            report = load(args.report)
            receipt = verify(report, expected_protocol_ref=args.expected_protocol_ref)
            if args.command == "verify":
                result = receipt
            else:
                result = {k: report["result"][k] for k in ("evidence_class", "enrolled_parts", "known_outcomes",
                    "confusion_by_endpoint", "descriptive_part_rates", "conditional_lot_bounds", "criteria_screen", "qualification", "authority")}
                result["verification_ref"] = receipt["result_ref"]
            if args.output is not None:
                save_new(args.output, result)
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.command == "verify":
            return 0
        return 2 if report["result"]["evidence_class"] == "SYNTHETIC_ONLY" or report["result"]["criteria_screen"]["status"] != "DECLARED_CRITERIA_SATISFIED" else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
