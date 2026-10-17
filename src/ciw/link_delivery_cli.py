"""Explicit local signed delivery and historical receipt inspection; no network IO."""
from __future__ import annotations

import argparse
from dataclasses import fields
from hashlib import sha256
import json
from pathlib import Path
import secrets
import sys
import time

from . import link_contract as c, link_delivery as d
from .control_contracts import save_new
from .hardware_workflow import read_bytes
from .link_store import Journal, safe_path


def _key(path, size):
    value = read_bytes(safe_path(path), limit=size)
    if len(value) != size:
        raise ValueError(f"Require exactly {size} raw key bytes")
    return value


def _anchored(path, digest, limit=65536):
    raw = read_bytes(safe_path(path), limit=limit)
    if type(digest) is not str or sha256(raw).hexdigest() != digest:
        raise ValueError("Operator-selected file differs from its external SHA-256 anchor")
    return c.loads(raw, limit)


def demo(output):
    """Real signing and Session computations, exclusively synthetic instrument data."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from .link_profiles import example_manifest
    from .link_runtime import DeviceRuntime
    from .link_gateway import capture
    from . import link_study_io as io, link_study as s
    output = safe_path(output); output.mkdir(parents=True, exist_ok=False)
    node_key = Ed25519PrivateKey.generate(); frame_key = secrets.token_bytes(32)
    public = node_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    device = example_manifest("GaugeLink")
    device["channels"][0].update(quantity="force", unit="N", encoding="opaque.v1")
    device["clock"].update(id="link.host.monotonic_ns", tick_hz=1_000_000_000)
    now = time.time_ns()
    with Journal(output/"edge.sqlite", create=True) as edge, Journal(output/"net.sqlite", create=True) as net:
        runtime = DeviceRuntime(device, edge, boot_id="synthetic.delivery.boot", key=frame_key)
        for index, raw in enumerate((b"1.00 N\r", b"\n2.00 N\r\n3.", b"00 N\r\n")):
            runtime.emit("CH0", raw, tick=1000+index*100)
        binding = d.DeliveryBinding("synthetic.customer", "synthetic.net.node", device["device_id"],
            c.content_identity(device), edge.store_id, net.store_id, "synthetic.trust.1", public,
            now-1, now+300_000_000_000, True)
        first = d.sync_signed(edge, net, device=device, binding=binding, signing_key=node_key, now_ns=now, key=frame_key)
        repeated = d.sync_signed(edge, net, device=device, binding=binding, signing_key=node_key, now_ns=now+1, key=frame_key)
        signed = c.loads(edge.db.execute("SELECT receipt FROM acks ORDER BY message_key LIMIT 1").fetchone()[0])
        save_new(output/"signed-receipt.json", signed)
        with (output/"node-public-key.raw").open("xb") as stream: stream.write(public)
        request = {"schema": s.SCHEMA, "capture": capture(net, expected_manifest=device, boot_id=runtime.boot_id),
            "channel_id": "CH0", "calibration": {"gain": 1.0, "offset": 0.0, "input_range": [-10,10],
                "declaration": None, "joint_covariance": None},
            "model": {"reference_kind": "declared_exact", "expected_mean": 2.0, "residual_limit": 0.01}}
    study = io.run(request, output/"study")
    replay = io.replay(output/"study", output/"study-replay")
    native = io.export(output/"study", output/"native")
    summary = {"schema": "ciw.link-delivery-demo.v1", "origin": "synthetic", "delivery": first,
               "retry": repeated, "receipt_ref": c.content_identity(signed),
               "study": study, "replay": replay, "native": native,
               "private_keys_retained": False, "physical_qualification": "not_established",
               "scope": dict(d.SCOPE)}
    save_new(output/"summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net link delivery", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("demo"); p.add_argument("--output-dir", type=Path, required=True)
    p = sub.add_parser("inspect")
    p.add_argument("receipt", type=Path); p.add_argument("--receipt-ref", required=True)
    p.add_argument("--node-public-key", type=Path, required=True)
    p = sub.add_parser("sync", help="Explicit finite LOCAL journal transfer; no endpoints or sockets")
    for name in ("source", "receiver", "manifest", "binding", "node-key", "node-public-key", "output"):
        p.add_argument("--"+name, type=Path, required=True)
    p.add_argument("--manifest-sha256", required=True); p.add_argument("--binding-sha256", required=True)
    p.add_argument("--frame-key", type=Path)
    p.add_argument("--renew-expired", action="store_true", help="Explicitly replace expired, unacknowledged challenges under still-valid host trust; retain history")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            result = demo(args.output_dir)
        elif args.command == "inspect":
            signed = c.loads(read_bytes(safe_path(args.receipt), limit=d.MAX_RECEIPT), d.MAX_RECEIPT)
            result = d.inspect_receipt(signed, expected_ref=args.receipt_ref, public_key=_key(args.node_public_key,32))
        else:
            # Host selects and anchors every path. Retained wire/receipts select none.
            device = c.manifest(_anchored(args.manifest,args.manifest_sha256))
            raw_binding = _anchored(args.binding,args.binding_sha256)
            c.keys(raw_binding, {f.name for f in fields(d.DeliveryBinding)}-{"public_key"})
            binding = d.DeliveryBinding(**raw_binding, public_key=_key(args.node_public_key,32))
            frame_key = None if args.frame_key is None else _key(args.frame_key,32)
            node_key = _key(args.node_key,32)
            destination = safe_path(args.output)
            if destination.exists(): raise ValueError("Do not overwrite an existing sync summary")
            with Journal(args.source) as source, Journal(args.receiver) as receiver:
                result = d.sync_signed(source,receiver,device=device,binding=binding,signing_key=node_key,
                                       now_ns=time.time_ns(),key=frame_key,clock=time.time_ns, renew_expired=args.renew_expired)
            save_new(destination,result)
        print(json.dumps(result,indent=2,allow_nan=False)); return 0
    except (ValueError,TypeError,KeyError,PermissionError,OSError,RuntimeError) as exc:
        print(json.dumps({"status":"REFUSED","reason":str(exc),"scope":d.SCOPE}),file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
