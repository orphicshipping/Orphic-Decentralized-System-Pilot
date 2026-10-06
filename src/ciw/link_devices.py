"""Device roster projected from the existing Link journal, not a fleet registry.

Registration records an operator-selected manifest. It does not enroll a device,
verify possession, set an active configuration, select a driver or open a port.
"""
from __future__ import annotations

from copy import deepcopy

from . import link_contract as c
from .link_protocol import decode_one, encode
from .link_store import decode_bound


def register(journal, raw: bytes, *, approved: bool = False) -> dict:
    if approved is not True:
        raise PermissionError("Explicit operator approval is required to retain a device declaration")
    device = c.manifest(c.loads(raw, 65536))
    reference = c.content_identity(device)
    # Repeated declarations with different JSON whitespace retain the original
    # manifest frame rather than replacing the first accepted byte sequence.
    for row in journal.rows():
        frame = decode_one(row["wire"], allow_unverified=True)
        if frame.kind == "manifest" and row["manifest_ref"] == reference:
            decode_bound(row["wire"], device, offline=True)
            return {"status": "ALREADY_RETAINED", "manifest_ref": reference,
                    "receipt": journal.receipt(row["message_key"]), "authority": "declaration_only"}
    receipt = journal.append(encode("manifest", raw), expected_manifest=device)
    return {"status": "DECLARATION_RETAINED", "manifest_ref": reference,
            "receipt": receipt, "authority": "declaration_only"}


def list_devices(journal) -> list[dict]:
    result = []
    for row in journal.rows():
        frame = decode_one(row["wire"], allow_unverified=True)
        if frame.kind != "manifest":
            continue
        device = c.manifest(c.loads(frame.payload, 65536))
        _, _, key, reference = decode_bound(row["wire"], device, offline=True)
        if row["message_key"] != key or row["manifest_ref"] != reference:
            raise ValueError("Retained manifest index disagrees with original bytes")
        result.append({"device_id": device["device_id"], "instrument_id": device["instrument_id"],
            "manifest_ref": reference, "hardware_revision": device["hardware_revision"],
            "firmware": device["firmware"], "gateware": device["gateware"],
            "configuration_revision": device["configuration"]["revision"],
            "channels": device["channels"], "capabilities": device["capabilities"],
            "wire_ref": row["wire_ref"], "connection": "NOT_PROBED",
            "identity_verification": "NOT_ESTABLISHED", "qualification": "NOT_ESTABLISHED"})
    return deepcopy(result)


def inspect_device(journal, device_id: str, *, manifest_ref: str | None = None) -> dict:
    c.text(device_id)
    if manifest_ref is not None:
        c.content_ref(manifest_ref)
    matches = [r for r in list_devices(journal) if r["device_id"] == device_id
               and (manifest_ref is None or r["manifest_ref"] == manifest_ref)]
    if len(matches) != 1:
        raise ValueError("Select exactly one retained device/manifest; absent or ambiguous binding")
    selected = matches[0]
    row = next(r for r in journal.rows() if r["message_key"] == "manifest:" + selected["manifest_ref"])
    frame = decode_one(row["wire"], allow_unverified=True)
    return {**selected, "manifest": c.loads(frame.payload, 65536),
            "original_manifest": c.packed(frame.payload, 65536), "receipt": journal.receipt(row["message_key"])}
