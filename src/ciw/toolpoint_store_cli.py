"""Durable local QC capture: no device acquisition, control or shipment authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .manufacturing_contracts import load, save_new
from .toolpoint_stream_store import StreamStore, initialize


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net polymer qc store", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "register", "import", "append", "append-capture", "head", "provenance", "export", "verify", "audit"):
        cmd = commands.add_parser(name)
        cmd.add_argument("database", type=Path)
        if name not in {"init", "audit"}:
            cmd.add_argument("stream_id")
        if name in {"register", "import", "append", "append-capture"}:
            cmd.add_argument("input", type=Path)
        if name in {"import", "append", "append-capture", "export", "verify", "head", "provenance"}:
            cmd.add_argument("--expected-spec-ref", required=name in {"import", "append", "append-capture"})
        if name in {"import", "export", "verify", "append-capture"}:
            cmd.add_argument("--expected-head-ref", required=name in {"import", "append-capture"},
                             help="Use 'none' only for the initial append-capture parent")
        if name in {"export", "verify"}:
            cmd.add_argument("--expected-commit-ref")
        if name == "append-capture":
            cmd.add_argument("--received-at-s", required=True, type=float)
            cmd.add_argument("--final", action="store_true")
        if name not in {"init", "register", "import", "append", "append-capture"}:
            cmd.add_argument("--output", type=Path, required=name == "export")
        # Mutating commands deliberately emit acknowledgements only to stdout.
        # A separate file-publication error cannot masquerade as a rolled-back commit.
        if name != "init":
            cmd.add_argument("--lock-timeout-s", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.database)
        else:
            store = StreamStore(args.database, timeout_s=args.lock_timeout_s)
            if args.command == "register":
                result = store.register(args.stream_id, load(args.input))
            elif args.command == "import":
                result = store.import_report(args.stream_id, load(args.input),
                    expected_spec_ref=args.expected_spec_ref, expected_head_ref=args.expected_head_ref)
            elif args.command == "append":
                result = store.append(args.stream_id, load(args.input), expected_spec_ref=args.expected_spec_ref)
            elif args.command == "append-capture":
                head = None if args.expected_head_ref == "none" else args.expected_head_ref
                result = store.append_capture(args.stream_id, load(args.input), received_at_s=args.received_at_s,
                    final=args.final, expected_head_ref=head, expected_spec_ref=args.expected_spec_ref)
            elif args.command in {"head", "provenance"}:
                method = store.head if args.command == "head" else store.provenance
                result = method(args.stream_id, expected_spec_ref=args.expected_spec_ref)
            elif args.command in {"export", "verify"}:
                method = store.export if args.command == "export" else store.verify
                result = method(args.stream_id, expected_spec_ref=args.expected_spec_ref,
                    expected_head_ref=args.expected_head_ref, expected_commit_ref=args.expected_commit_ref)
            else:
                result = store.audit()
        if getattr(args, "output", None) is not None:
            save_new(args.output, result)
        print(json.dumps(result, indent=2, allow_nan=False))
        # Commit success is distinct from quality success. Exit 2 can be COMMITTED+HELD.
        if args.command in {"verify", "audit", "init"}:
            return 0
        state = result.get("quality_status", result.get("result", {}).get("status"))
        return 2 if state in {"HELD", "PENDING", "AWAITING_EVIDENCE"} else 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
            ImportError, sqlite3.Error) as exc:
        # IO/COMMIT outcomes can be ambiguous after response loss or storage faults.
        # Retrying the exact same anchored update is safe; never infer a new head here.
        print(json.dumps({"status": "REFUSE", "reason": str(exc),
            "commit_outcome": "not_confirmed_by_this_response",
            "recovery": "Inspect the store or retry the identical update; never regenerate its parent silently"}),
            file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
