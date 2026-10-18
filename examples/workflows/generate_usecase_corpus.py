#!/usr/bin/env python3
"""Generate the reproducible, input-backed catalog; never invent missing history."""
import argparse
import json
from pathlib import Path
from usecase_catalog import INPUTS, INDEX, generate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=INPUTS)
    parser.add_argument("--output", type=Path, default=INDEX)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(generate(args.inputs), indent=2, ensure_ascii=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text() != expected:
            parser.exit(1, "catalog differs from reviewed inputs\n")
    else:
        if args.output.resolve() == args.inputs.resolve():
            parser.error("output cannot overwrite teaching inputs")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(expected, encoding="utf-8")
    print(f"catalog={args.output} entries={len(generate(args.inputs)['entries'])} historical_9074_restored=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
