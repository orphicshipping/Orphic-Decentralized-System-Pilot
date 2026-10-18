#!/usr/bin/env python3
"""Present retained heat inputs; never manufacture a proof occurrence."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples/workflows"))
from usecase_catalog import INDEX, read_index
from usecase_templates import EMIT_FUNCTIONS

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results/thermal-proof-gate_render.json")
    args = parser.parse_args()
    entry = next(e for e in read_index(INDEX, set(EMIT_FUNCTIONS))["entries"] if e["slug"] == "thermal-proof-gate")
    EMIT_FUNCTIONS[entry["family"]](args.output, entry)
    print(f"wrote {args.output}; proof_status=NOT_CHECKED fresh_verification=NOT_RUN")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
