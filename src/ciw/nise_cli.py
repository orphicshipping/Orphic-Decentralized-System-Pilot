"""NISE → NET handoff compiler."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .control_contracts import load, save_new
from .control_plane import builtin_registry
from .nise_contract import parse
from .nise_handoff import compile_handoff
from .semantic_capabilities import builtin_semantic_registry


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="net nise",
        description="Validate a NISE schematic contract and compile an operator binding plan into a NET semantic experiment; no source authentication or fresh NISE recomputation.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    compile_cmd = commands.add_parser("compile-handoff")
    compile_cmd.add_argument("schematic", type=Path)
    compile_cmd.add_argument("binding", type=Path)
    compile_cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.schematic.open("rb") as stream:
            schematic = parse(stream.read(65536 + 1))
        concrete = builtin_registry(bind=True)
        semantic = builtin_semantic_registry(concrete)
        result = compile_handoff(schematic, load(args.binding), semantic)
        save_new(args.output, result)
        print(json.dumps({
            "status": "compiled",
            "schematic_id": result["schematic_id"],
            "selected_operations": len(result["selected_operations"]),
            "authorizes_execution": result["claims"]["execution_authority"],
            "validation": result["provenance"]["validation"],
            "source_authenticated": result["provenance"]["source_authenticated"],
            "fresh_recomputation": result["provenance"]["fresh_recomputation"],
            "output": str(args.output),
        }))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
