"""Five-blade retained GaugeLink investigation and covariance-improvement review."""
import argparse
import json
from pathlib import Path
import sys
from . import link_contract as c, link_pinwheel as wheel, link_improvement as improvement
from . import link_measurement_io as exchange
from .control_contracts import save_new
from .hardware_workflow import read_bytes


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net link pinwheel", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="Author explicitly hypothetical input-error reductions, not an authorization")
    p.add_argument("bundle"); p.add_argument("--instance-id", required=True)
    p.add_argument("--target-uncertainty", type=float, required=True); p.add_argument("--output", required=True)
    p = sub.add_parser("run"); p.add_argument("bundle"); p.add_argument("plan"); p.add_argument("--output-dir", required=True)
    for name in ("inspect", "replay", "restore-source", "report", "mcp"):
        p = sub.add_parser(name); p.add_argument("directory")
        if name in {"replay", "restore-source"}: p.add_argument("--output-dir", required=True)
        if name == "report": p.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = wheel.plan_for(exchange.load(args.bundle), instance_id=args.instance_id,
                                    max_standard_uncertainty=args.target_uncertainty)
            save_new(Path(args.output), result)
        elif args.command == "run":
            raw = read_bytes(Path(args.plan), limit=improvement.MAX_PLAN)
            result = wheel.run(exchange.load(args.bundle), c.loads(raw, improvement.MAX_PLAN), args.output_dir, raw_plan=raw)
        elif args.command == "inspect": result = wheel.inspect(args.directory)
        elif args.command == "replay": result = wheel.replay(args.directory, args.output_dir)
        elif args.command == "restore-source": result = wheel.restore_source(args.directory, args.output_dir)
        elif args.command == "report": result = wheel.report(args.directory, args.output)
        else:
            from .agent_mcp import serve
            with wheel.agent(args.directory) as host: return serve(host, sys.stdin.buffer, sys.stdout.buffer)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") == "incomplete" else 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, OverflowError) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc), "physical_qualification": "NOT_ESTABLISHED"}), file=sys.stderr)
        return 2


if __name__ == "__main__": raise SystemExit(main())
