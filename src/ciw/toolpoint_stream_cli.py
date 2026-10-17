"""Append/verify bounded QC capture snapshots. No device or machine interface."""
import argparse
import json
from pathlib import Path
import sys

from .manufacturing_contracts import load, save_new
from .toolpoint_stream import CaptureJournal, final_observation, verify


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net polymer qc stream", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("demo")
    cmd.add_argument("--profile", choices=("native", "legacy"), default="native")
    cmd.add_argument("--case", choices=("nominal", "late-unknown", "health-recovery", "single-packet-fault", "high-nozzle", "sampling-gap"), default="late-unknown")
    cmd.add_argument("--output", type=Path, required=True)
    for name in ("start", "append"):
        cmd = sub.add_parser(name)
        cmd.add_argument("input", type=Path, help="Stream spec for start; prior report for append")
        cmd.add_argument("capture", type=Path)
        cmd.add_argument("--received-at-s", type=float, required=True)
        cmd.add_argument("--final", action="store_true")
        if name == "append":
            cmd.add_argument("--expected-head-ref")
        cmd.add_argument("--output", type=Path, required=True)
    for name in ("verify", "final", "summary", "contain", "verify-containment"):
        cmd = sub.add_parser(name)
        cmd.add_argument("input", type=Path)
        cmd.add_argument("--output", type=Path, required=name in {"final", "contain"})
        if name == "verify":
            cmd.add_argument("--expected-head-ref")
            cmd.add_argument("--expected-spec-ref")
    cmd = sub.add_parser("gate")
    cmd.add_argument("input", type=Path)
    cmd.add_argument("--serial", action="append", required=True)
    cmd.add_argument("--action", choices=("assemble", "pack", "release_review"), required=True)
    cmd.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .toolpoint_stream_example import demo
            result = demo(profile=args.profile, case=args.case)
        elif args.command in {"start", "append"}:
            value = load(args.input)
            journal = CaptureJournal(value) if args.command == "start" else CaptureJournal.restore(value, expected_head_ref=args.expected_head_ref)
            journal.append(journal.update(load(args.capture), received_at_s=args.received_at_s, final=args.final))
            result = journal.report()
        elif args.command == "verify":
            result = verify(load(args.input), expected_head_ref=args.expected_head_ref, expected_spec_ref=args.expected_spec_ref)
        elif args.command == "final":
            result = final_observation(load(args.input))
        elif args.command == "summary":
            report = load(args.input)
            verify(report)
            result = report["result"]
        else:
            from .toolpoint_stream_containment import evaluate, gate, verify as verify_containment
            value = load(args.input)
            result = (evaluate(value) if args.command == "contain" else verify_containment(value)
                      if args.command == "verify-containment" else gate(value, serials=args.serial, action=args.action))
        status = result.get("result", result).get("status")
        if args.output is not None:
            save_new(args.output, result)
            shown = {"schema": result["schema"], "status": status, "output": str(args.output)}
        else:
            shown = result
        print(json.dumps(shown, indent=2, allow_nan=False))
        return 2 if status in {"HELD", "PENDING"} else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, ImportError) as exc:
        print(json.dumps({"status": "REFUSE", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
