"""NET Link: bounded device records, local outbox/sync and retained investigations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from . import link_contract as c, link_gateway as gateway, link_workflow as workflow
from .control_contracts import save_new
from .hardware_workflow import read_bytes
from .link_profiles import unified_catalog, profile_examples, selectors, manifest_view
from .link_protocol import MAX_FRAME, encode
from .link_store import Journal, safe_path


def _load(path):
    return c.loads(read_bytes(safe_path(path), limit=c.MAX_CAPTURE_BYTES))


def _key(path):
    if path is None:
        return None
    key = read_bytes(safe_path(path), limit=64)
    if not 32 <= len(key) <= 64:
        raise ValueError("HMAC key file must contain 32..64 raw bytes")
    return key


def example(persona, output):
    output = safe_path(output)
    output.mkdir(parents=True, exist_ok=False)
    device, records = profile_examples(persona)
    save_new(output / "manifest.json", device)
    wire = b"".join(encode(kind, c.bounded(value)) for kind, value in
                     [("manifest", device)] + [("observation", v) for v in records])
    with (output / "capture.link").open("xb") as stream:
        stream.write(wire)
    return {"status": "CREATED_SYNTHETIC", "manifest": str(output / "manifest.json"),
            "wire": str(output / "capture.link"), "boot_id": "synthetic.boot.1", "observations": len(records)}


def demo(persona, output):
    from .link_view import report
    output = safe_path(output)
    output.mkdir(parents=True, exist_ok=False)
    info = example(persona, output / "input")
    device = _load(info["manifest"])
    wire = read_bytes(Path(info["wire"]), limit=c.MAX_CAPTURE_BYTES)
    with Journal(output / "device.sqlite", create=True) as edge, Journal(output / "gateway.sqlite", create=True) as receiver:
        ingress = gateway.receive((wire[i:i+4096] for i in range(0,len(wire),4096)), edge, expected_manifest=device)
        delivered = edge.sync_to(receiver, manifests=[device])
        repeated = edge.sync_to(receiver, manifests=[device])
        retained = gateway.capture(receiver, expected_manifest=device, boot_id=info["boot_id"])
        save_new(output / "capture.json", retained)
    summary = workflow.run(retained, output / "investigation", raw=read_bytes(output / "capture.json", limit=c.MAX_CAPTURE_BYTES))
    replay = workflow.replay(output / "investigation", output / "replay")
    native = workflow.export_native(output / "investigation", output / "native")
    panel = report(output / "investigation", output / "dashplate.html")
    receipt = {"schema": "ciw.link-demo.v1", "status": "PASS" if summary["status"] == "RETAINED" else "REFUSED",
               "profile": persona, "origin": "synthetic", "input": info, "ingress": ingress,
               "delivery": delivered, "retry": repeated, "evidence_id": summary["evidence_id"],
               "execution_id": summary["execution_id"], "replay_execution_id": replay["execution_id"],
               "replay_preserved_evidence": replay["evidence_id"] == summary["evidence_id"],
               "native": native, "report": panel, "limits": dict(c.LIMITS)}
    save_new(output / "demo-receipt.json", receipt)
    return receipt


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["reference"]:
        from .link_reference_io import main as reference_main
        return reference_main(argv[1:])
    if argv[:1] == ["pinwheel"]:
        from .link_pinwheel_cli import main as pinwheel_main
        return pinwheel_main(argv[1:])
    if argv[:1] == ["installation"]:
        from .link_installation_io import main as installation_main
        return installation_main(argv[1:])
    if argv[:1] == ["inspection"]:
        from .link_inspection_cli import main as inspection_main
        return inspection_main(argv[1:])
    if argv[:1] == ["delivery"]:
        from .link_delivery_cli import main as delivery_main
        return delivery_main(argv[1:])
    if argv[:1] == ["study"]:
        from .link_study_cli import main as study_main
        return study_main(argv[1:])
    parser = argparse.ArgumentParser(prog="net link", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("catalog", help="One Link application; profiles are declarations, not drivers")
    p.add_argument("--manifest", type=Path, help="Inspect declared channel/profile matches without discovery")
    p.add_argument("--manifest-ref", help="External exact-byte identity required with --manifest")
    p = commands.add_parser("semantics", help="Read existing scalar lineage through NET morphism/preservation contracts")
    p.add_argument("directory", type=Path); p.add_argument("--request-ref", required=True)
    p.add_argument("--output", type=Path, help="Optional create-only mathematical review; no new scientific execution")
    commands.add_parser("reference", help="Pinned institutional/academic data review; no calibration transfer")
    commands.add_parser("pinwheel", help="Evidence-bound five-blade review and local uncertainty-improvement proposals")
    commands.add_parser("installation", help="Retained reference-paired installation response; no hardware access")
    commands.add_parser("inspection", help="Digital Inspection: one recorded preview, clear trust checks, Advanced details")
    commands.add_parser("delivery", help="Node-signed local custody receipts; no network or equipment grant")
    commands.add_parser("study", help="Retained scalar decoding, affine correction, model and independent arithmetic audit")
    p = commands.add_parser("capture-serial", help="Explicit finite Linux TTY receive into the existing outbox; no polling")
    p.add_argument("port"); p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--journal", type=Path, required=True); p.add_argument("--create", action="store_true")
    p.add_argument("--channel", required=True); p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--duration", type=float, default=10); p.add_argument("--allow-read", action="store_true")
    p.add_argument("--allow-pty", action="store_true"); p.add_argument("--output", type=Path, required=True)
    for name in ("example", "demo"):
        p = commands.add_parser(name)
        p.add_argument("--profile", choices=selectors(), default="gauge")
        p.add_argument("--output-dir", type=Path, required=True)
    p = commands.add_parser("record", help="Import a finite byte stream; '-' explicitly selects stdin")
    p.add_argument("wire"); p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--journal", type=Path, required=True); p.add_argument("--create", action="store_true")
    p.add_argument("--hmac-key", type=Path); p.add_argument("--require-auth", action="store_true")
    p = commands.add_parser("sync", help="Explicit local store-to-store copy, not remote network access")
    p.add_argument("--source", type=Path, required=True); p.add_argument("--receiver", type=Path, required=True)
    p.add_argument("--create-receiver", action="store_true"); p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--hmac-key", type=Path); p.add_argument("--require-auth", action="store_true")
    p = commands.add_parser("capture")
    p.add_argument("--journal", type=Path, required=True); p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--boot-id", required=True); p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("run"); p.add_argument("capture", type=Path); p.add_argument("--output-dir", type=Path, required=True)
    for name in ("inspect", "replay", "export", "report"):
        p = commands.add_parser(name); p.add_argument("directory", type=Path)
        if name in {"replay", "export"}:
            p.add_argument("--output-dir", type=Path, required=True)
        elif name == "report":
            p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("config-plan", help="Prepare only; does not send configuration to a device")
    p.add_argument("--manifest", type=Path, required=True); p.add_argument("--configuration", type=Path, required=True)
    p.add_argument("--expires-ns", type=int, required=True); p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("verify-update", help="Offline signature/target/generation check; never flash")
    p.add_argument("--manifest", type=Path, required=True); p.add_argument("--package", type=Path, required=True)
    p.add_argument("--image", type=Path, required=True); p.add_argument("--public-key", type=Path, required=True)
    p.add_argument("--minimum-generation", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            if (args.manifest is None) != (args.manifest_ref is None):
                raise ValueError("Supply manifest and externally retained manifest-ref together")
            result = unified_catalog() if args.manifest is None else manifest_view(
                read_bytes(safe_path(args.manifest), limit=65536), expected_ref=args.manifest_ref)
        elif args.command == "semantics":
            from .link_semantics import review
            result = review(args.directory, expected_request_ref=args.request_ref)
            if args.output is not None:
                save_new(safe_path(args.output), result)
        elif args.command == "capture-serial":
            from .link_serial import capture_serial
            from .link_runtime import Authority
            import time
            if not args.allow_read:
                raise ValueError("Explicit --allow-read is required; a saved manifest grants no device access")
            safe_path(args.output)
            if not args.output.parent.is_dir():
                raise ValueError("Create the receipt output parent before acquisition")
            if args.output.exists():
                raise ValueError("Receipt output must be new before acquiring any bytes")
            device = c.manifest(_load(args.manifest))
            authority = Authority("operator-local", device["device_id"], frozenset({"READ"}), time.time_ns() + int((args.duration + 2)*1e9))
            with Journal(args.journal, create=args.create) as journal:
                result = capture_serial(args.port, device=device, journal=journal, authority=authority,
                    channel_id=args.channel, baud=args.baud, duration_s=args.duration, allow_pty=args.allow_pty)
            save_new(safe_path(args.output), result)
        elif args.command in {"example", "demo"}:
            result = (example if args.command == "example" else demo)(args.profile, args.output_dir)
        elif args.command == "record":
            device = c.manifest(_load(args.manifest)); key = _key(args.hmac_key)
            raw = sys.stdin.buffer.read(c.MAX_CAPTURE_BYTES + 1) if args.wire == "-" else read_bytes(safe_path(args.wire), limit=c.MAX_CAPTURE_BYTES)
            if not raw or len(raw) > c.MAX_CAPTURE_BYTES:
                raise ValueError("Require a bounded nonempty finite wire stream")
            with Journal(args.journal, create=args.create) as journal:
                result = gateway.receive((raw[i:i+4096] for i in range(0,len(raw),4096)), journal,
                                         expected_manifest=device, key=key, require_auth=args.require_auth)
        elif args.command == "sync":
            with Journal(args.source) as source, Journal(args.receiver, create=args.create_receiver) as receiver:
                result = source.sync_to(receiver, manifests=[_load(args.manifest)], key=_key(args.hmac_key), require_auth=args.require_auth)
        elif args.command == "capture":
            with Journal(args.journal) as journal:
                result = gateway.capture(journal, expected_manifest=_load(args.manifest), boot_id=args.boot_id)
            save_new(safe_path(args.output), result)
        elif args.command == "run":
            raw = read_bytes(safe_path(args.capture), limit=c.MAX_CAPTURE_BYTES)
            result = workflow.run(c.loads(raw), args.output_dir, raw=raw)
        elif args.command == "inspect":
            result = workflow.inspect(args.directory)
        elif args.command == "replay":
            result = workflow.replay(args.directory, args.output_dir)
        elif args.command == "export":
            result = workflow.export_native(args.directory, args.output_dir)
        elif args.command == "report":
            from .link_view import report
            result = report(args.directory, args.output)
        elif args.command == "config-plan":
            from .link_runtime import prepare_configuration
            result = prepare_configuration(_load(args.manifest), _load(args.configuration), expires_ns=args.expires_ns)
            save_new(safe_path(args.output), result)
        else:
            from .link_security import verify_update, MAX_IMAGE_BYTES
            result = verify_update(_load(args.package), read_bytes(safe_path(args.image), limit=MAX_IMAGE_BYTES),
                device=_load(args.manifest), trusted_public_key=read_bytes(safe_path(args.public_key), limit=32), minimum_generation=args.minimum_generation)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("status") == "REFUSED" else 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, RecursionError, OverflowError, sqlite3.Error) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc), "physical_dispatch": "not_performed"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
