"""Review a preliminary product study through the existing NET Session."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from .adapters.protocol import AdapterRefusal
from .data_cli import _read, _save_new, MAX_WORKSPACE_BYTES
from .adapters.subprocess import _json


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net product-study", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Check selected bytes and scalar screens without an execution occurrence")
    inspect.add_argument("study", type=Path)
    review = commands.add_parser("review", help="Consume a retained versioned study and save a new workspace")
    review.add_argument("--source-id", required=True)
    review.add_argument("--workspace", required=True, type=Path)
    review.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            from .product_study import review
            result = review(_json(_read(args.study)))
        else:
            from .session import Session
            from .product_study_workflow import REVIEW, parameters
            if args.output.exists():
                raise ValueError("Output workspace already exists; choose a new path")
            with tempfile.TemporaryDirectory(prefix="net-product-study-") as directory:
                session = Session.from_workspace(args.workspace, output_dir=Path(directory))
                response = session.handle({"protocol_version": 1, "request_id": "product-study-review",
                    "type": "operation.execute", "payload": {"operation_id": REVIEW,
                        "parameters": parameters(session.workbench, args.source_id)}})
                if response["type"] == "error":
                    raise ValueError(response["payload"]["message"])
                result = response["payload"]
                _save_new(args.output, session._workspace(), MAX_WORKSPACE_BYTES)
                result = {"output": str(args.output), **result}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") == "refused" else 0
    except (ValueError, OSError, AdapterRefusal) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
