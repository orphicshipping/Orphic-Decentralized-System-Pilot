#!/usr/bin/env python3
"""Emit validated data-only teaching scenarios through their owned emitters."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from usecase_catalog import INDEX, ROOT, read_index
from usecase_templates import EMIT_FUNCTIONS, _EMITTERS, _owned_payload


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def projector_command(args, render: Path) -> list[str]:
    """Executable selection belongs to this host invocation, never the catalog."""
    if not args.scenario or args.binary is None or not args.binary.is_absolute():
        raise ValueError("--client requires --scenario and an absolute --binary path")
    binary = args.binary.resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("projector binary is not an executable file")
    if args.frames is not None and not 1 <= args.frames <= 10000:
        raise ValueError("frame count must be 1..10000")
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        raise ValueError("timeout must be positive and finite")
    command = [str(binary)]
    if args.client == "godot":
        command += ["--audio-driver", "Dummy", "--path", str(ROOT / "godot"), "--", "--retained-render", str(render)]
    if args.frames is not None:
        command += ["--frames", str(args.frames)]
    if args.client == "bevy":
        command.append(str(render))
    return command


def launch_projector(command: list[str], render: Path, timeout: float | None) -> dict:
    identity = file_digest(render)
    receipt = {"client_binary_sha256": file_digest(Path(command[0])), "argv": command,
               "input_sha256": identity, "may_authorize": False,
               "display_observation": "NOT_CHECKED_BY_LAUNCHER"}
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        receipt.update(status="COMPLETED" if process.returncode == 0 else "FAILED",
                       exit_code=process.returncode, stdout=process.stdout[-8192:], stderr=process.stderr[-8192:])
    except subprocess.TimeoutExpired as error:
        receipt.update(status="TIMED_OUT", exit_code=None,
                       stdout=(error.stdout or b"").decode(errors="replace")[-8192:],
                       stderr=(error.stderr or b"").decode(errors="replace")[-8192:])
    except OSError as error:
        receipt.update(status="FAILED", exit_code=None, error=str(error))
    receipt["input_unchanged"] = render.is_file() and file_digest(render) == identity
    if not receipt["input_unchanged"]:
        receipt["status"] = "FAILED"
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scenario", help="Emit one catalog slug instead of the complete catalog")
    parser.add_argument("--client", choices=["godot", "bevy"], help="Optionally open the emitted record")
    parser.add_argument("--binary", type=Path, help="Absolute, operator-selected projector executable")
    parser.add_argument("--frames", type=int, help="Close the projector after 1..10000 frames")
    parser.add_argument("--timeout", type=float, help="Optional projector process deadline in seconds")
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
        if args.scenario:
            entries = [entry for entry in entries if entry["slug"] == args.scenario]
            if not entries:
                raise ValueError("unknown scenario slug")
        command = None
        if args.client:
            command = projector_command(args, (output_root / entries[0]["render_path"]).resolve())
            if (output_root / entries[0]["render_path"]).resolve() == Path(command[0]):
                raise ValueError("render output cannot overwrite the projector executable")
            if args.receipt and args.receipt.resolve() == Path(command[0]):
                raise ValueError("receipt cannot overwrite the projector executable")
        elif any(value is not None for value in (args.binary, args.frames, args.timeout)):
            raise ValueError("--binary, --frames and --timeout require --client")
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
        if command is not None:
            receipt["projector"] = launch_projector(command, output_root / entries[0]["render_path"], args.timeout)
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
        print(f"emitted={len(receipts)} failures=0 layer_export=PASS native_provider_qualification=NOT_RUN")
        if command is not None:
            print("projector=" + receipt["projector"]["status"])
        return 0 if command is None or receipt["projector"]["status"] == "COMPLETED" else 1
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
