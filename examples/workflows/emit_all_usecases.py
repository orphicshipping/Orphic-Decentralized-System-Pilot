#!/usr/bin/env python3
"""Emit validated data-only teaching scenarios through their owned emitters."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from usecase_catalog import INDEX, ROOT, read_index
from usecase_templates import EMIT_FUNCTIONS, _EMITTERS, _owned_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        catalog = read_index(args.index, set(EMIT_FUNCTIONS))
        entries = catalog["entries"]
        output_root = args.output_root.resolve()
        if args.receipt and args.receipt.exists():
            raise ValueError("receipt already exists; choose a fresh evidence path")
        # Validate the complete manifest and all owned payloads before writing.
        for entry in entries:
            output = output_root / entry["render_path"]
            if args.receipt and args.receipt.resolve() == output.resolve():
                raise ValueError("receipt cannot overwrite a render output")
            if not output.resolve().is_relative_to(output_root):
                raise ValueError("scenario output resolves outside the output root")
            if not _EMITTERS[entry["family"]].is_file():
                raise ValueError("missing owned teaching emitter")
            json.dumps(_owned_payload(entry["family"]), allow_nan=False)
        if args.dry_run:
            print(f"validated={len(entries)} emitted=0 dry_run=true")
            return 0
        receipts = []
        for entry in entries:
            output = output_root / entry["render_path"]
            EMIT_FUNCTIONS[entry["family"]](output, entry)
            raw = output.read_bytes()
            receipts.append({"slug": entry["slug"], "family": entry["family"],
                             "render_path": entry["render_path"], "sha256": sha256(raw).hexdigest(), "bytes": len(raw)})
        receipt = {"catalog_sha256": sha256(args.index.read_bytes()).hexdigest(),
                   "emitted": len(receipts), "entries": receipts, "layer_export": "PASS",
                   "native_provider_qualification": "NOT_RUN", "fresh_verification": "NOT_RUN",
                   "historical_9074_corpus_restored": False, "may_authorize": False}
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
        print(f"emitted={len(receipts)} failures=0 layer_export=PASS native_provider_qualification=NOT_RUN")
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
