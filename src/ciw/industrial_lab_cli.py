"""Graph-first Industrial Lab commands under the existing `net system` family."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .control_contracts import load, save_new
from .industrial_lab import IndustrialLab, MAX_SOURCE_BYTES, checked_spec, project_board
from .system_spec import demo_spec
from .visual_board import write_html
from .visual_board_server import make_server


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    checked_spec(raw)
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net system lab", description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    demo = commands.add_parser("demo", help="Create a synthetic native reference and Board")
    demo.add_argument("--output-dir", type=Path, required=True)
    view = commands.add_parser("view", help="Project a supported specification without executing it")
    view.add_argument("specification", type=Path)
    view.add_argument("--output", type=Path, required=True)
    for action in ("serve", "run"):
        p = commands.add_parser(action)
        p.add_argument("specification", type=Path)
        p.add_argument("--output-dir", type=Path, required=True)
        p.add_argument("--engine", choices=("local", "subprocess"), default="subprocess")
        if action == "serve":
            p.add_argument("--port", type=int, default=0)
            p.add_argument("--allow-run", action="store_true")
        else:
            p.add_argument("--edit", type=Path, help="Optional exact Board parameter request after baseline")
    args = parser.parse_args(argv)
    try:
        if args.action == "demo":
            spec = demo_spec(cells=16)
            args.output_dir.mkdir(parents=True, exist_ok=False)
            save_new(args.output_dir / "specification.json", spec)
            save_new(args.output_dir / "board.json", project_board(spec))
            write_html(args.output_dir / "board.html", project_board(spec))
            result = {"status": "created", "synthetic": True, "provider_execution": False,
                      "output_dir": str(args.output_dir)}
        elif args.action == "view":
            board = project_board(checked_spec(_read(args.specification)))
            write_html(args.output, board)
            result = {"status": "created", "board_ref": board["record_digest"],
                      "provider_execution": False, "output": str(args.output)}
        else:
            work = IndustrialLab(_read(args.specification), args.output_dir,
                                 allow_run=args.action == "run" or args.allow_run,
                                 engine=args.engine)
            if args.action == "serve":
                server = make_server(work, port=args.port)
                print(json.dumps({"status": "serving", "url": server.board_url,
                    "execution_enabled": args.allow_run, "profile": "scientific-system-lab.v1",
                    "loopback_only": True}), flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
                return 0
            baseline = work.run_baseline()
            candidate = None
            if args.edit is not None:
                edit = load(args.edit)
                work.preview(edit)
                candidate = work.run_candidate(edit, baseline["baseline_ref"])
            # Existing native writer, not a separate scientific report implementation.
            from .system_view import write_system_review
            write_system_review(args.output_dir / "review.html", work.session)
            views = [baseline["lab"]] + ([candidate["lab"]] if candidate else [])
            passed = all(v["verification"]["report"]["status"] == "PASS" for v in views)
            result = {"status": "PASS" if passed else "FAIL", "synthetic": True,
                "workspace": str(args.output_dir / "session" / "workspace.json"),
                "review": str(args.output_dir / "review.html"),
                "baseline_result_id": baseline["baseline_ref"],
                "candidate_result_id": candidate["lab"]["source_result_id"] if candidate else None,
                "physical_validation_status": "not_assessed", "canonical_admission": False}
            save_new(args.output_dir / "summary.json", result)
            print(json.dumps(result, indent=2, allow_nan=False))
            return 0 if passed else 2
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
