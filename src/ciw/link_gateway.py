"""Bounded Link ingress and calibration-declaration resolution, no admission.

Original wire frames, device ticks, host-receipt times and scientific records
remain distinct. Discovery is data inspection; the caller must bind a manifest.
"""
from __future__ import annotations

from copy import deepcopy

from . import link_contract as c
from .link_protocol import Decoder, MAX_FRAME
from .link_store import decode_bound


def receive(chunks, journal, *, expected_manifest, key=None, require_auth=False):
    """Read a finite iterable of byte chunks. A failure keeps committed prefix.

    No sockets are opened. The operator owns the file/pipe/stream binding.
    This function cannot choose a serial port, send a poll, or load device code.
    """
    decoder = Decoder(key=key, require_auth=require_auth)
    receipts = []
    byte_count = 0

    def retain(frame):
        # Run for each verified frame BEFORE parsing a later frame in the same
        # chunk. Journal.append returns only after commit and exact readback.
        receipts.append(journal.append(frame.wire, expected_manifest=expected_manifest,
                                       key=key, require_auth=require_auth))

    for chunk in chunks:
        byte_count += len(chunk)
        if byte_count > c.MAX_CAPTURE_BYTES:
            raise ValueError("Finite Link receive budget exceeded; committed prefix retained")
        decoder.feed(chunk, on_frame=retain)
    decoder.finish()
    return {"status": "RECEIVED_FINITE_INPUT", "receipts": receipts, "bytes": byte_count,
            "scope": "durable_local_buffer_only", "device_completeness": "not_established"}


def capture(journal, *, expected_manifest, boot_id, calibrations=None, joint_covariance=None):
    device = c.manifest(expected_manifest)
    c.text(boot_id)
    manifest_ref = c.content_identity(device)
    manifest_wire = None
    frames = []
    for row in journal.rows():
        if row["manifest_ref"] != manifest_ref:
            continue
        frame, value, _, _ = decode_bound(row["wire"], device, offline=True)
        if frame.kind == "manifest":
            manifest_wire = c.packed(row["wire"], MAX_FRAME)
        elif value["boot_id"] == boot_id:
            frames.append({"wire": c.packed(row["wire"], MAX_FRAME), "received_ns": row["received_ns"],
                           "host_clock": row["host_clock"], "authentication_at_capture": row["authentication_at_capture"]})
    if manifest_wire is None:
        raise ValueError("Selected manifest bytes are absent")
    value = {"schema": c.CAPTURE_SCHEMA, "manifest": device, "manifest_wire": manifest_wire,
             "gateway_id": journal.store_id, "boot_id": boot_id, "frames": frames,
             "calibrations": [] if calibrations is None else calibrations, "joint_covariance": joint_covariance,
             "transport": "local_retained_byte_stream", "closure": "operator_selected_finite_capture"}
    validate_capture(value)
    return value


def calibration_declaration(value):
    c.bounded(value, 65536)
    c.keys(value, {"schema", "calibration_id", "manifest_ref", "channel_id", "unit", "valid_from", "valid_until",
                  "certificate", "reference_standard_ref"})
    if value["schema"] != "ciw.link-calibration-declaration.v1":
        raise ValueError("Unsupported calibration declaration")
    c.text(value["calibration_id"]); c.text(value["channel_id"]); c.text(value["unit"])
    c.content_ref(value["manifest_ref"])
    if value["reference_standard_ref"] is not None:
        c.content_ref(value["reference_standard_ref"])
    c.unpack(value["certificate"], 32768)
    from .calibration_status import _timestamp
    start = _timestamp(value["valid_from"], "valid_from", strict=True)
    end = _timestamp(value["valid_until"], "valid_until", strict=True)
    if start >= end:
        raise ValueError("Invalid calibration validity interval")
    return value


def _calibration_status(item, device, declarations):
    ref = item["calibration_ref"]
    status = "MISSING" if ref is None else "UNRESOLVED_REFERENCE"
    declaration = declarations.get(ref)
    if declaration is not None:
        channel = c.channel_for(device, item["channel_id"])
        if (declaration["manifest_ref"] != item["manifest_ref"] or declaration["channel_id"] != item["channel_id"]
                or declaration["unit"] != channel["unit"]):
            status = "CONFIGURATION_CHANNEL_OR_UNIT_MISMATCH"
        elif item["acquired_at"] is None:
            status = "ACQUISITION_TIME_UNKNOWN"
        else:
            from .calibration_status import _timestamp
            at = _timestamp(item["acquired_at"], "acquired_at", strict=True)
            start = _timestamp(declaration["valid_from"], "valid_from", strict=True)
            end = _timestamp(declaration["valid_until"], "valid_until", strict=True)
            status = "DECLARED_APPLICABLE_AT_ACQUISITION" if start <= at < end else "OUTSIDE_DECLARED_VALIDITY"
    return {"measurement_id": item["measurement_id"], "calibration_ref": ref, "status": status,
            "traceability": "not_established", "certificate_authentication": "not_established"}


def validate_capture(value, *, key=None):
    c.bounded(value)
    c.keys(value, {"schema", "manifest", "manifest_wire", "gateway_id", "boot_id", "frames", "calibrations",
                  "joint_covariance", "transport", "closure"})
    if (value["schema"] != c.CAPTURE_SCHEMA or value["transport"] != "local_retained_byte_stream"
            or value["closure"] != "operator_selected_finite_capture"):
        raise ValueError("Unsupported capture schema/transport/closure")
    device = c.manifest(value["manifest"])
    c.text(value["gateway_id"]); c.text(value["boot_id"])
    mf, _, _, _ = decode_bound(c.unpack(value["manifest_wire"], MAX_FRAME), device, key=key, offline=key is None)
    if mf.kind != "manifest":
        raise ValueError("Capture manifest wire must contain the bound manifest")
    frames = value["frames"]
    if type(frames) is not list or not 1 <= len(frames) <= c.MAX_RECORDS:
        raise ValueError("Capture requires 1..256 frames")
    observations, events, auth = [], [], [mf.authentication]
    seen = set()
    for item in frames:
        c.keys(item, {"wire", "received_ns", "host_clock", "authentication_at_capture"})
        c.integer(item["received_ns"]); c.text(item["host_clock"])
        if item["authentication_at_capture"] not in {"not_authenticated", "shared_key_possession_verified"}:
            raise ValueError("Invalid historical authentication declaration")
        frame, record, record_key, _ = decode_bound(c.unpack(item["wire"], MAX_FRAME), device, key=key, offline=key is None)
        if frame.kind == "manifest" or record["boot_id"] != value["boot_id"] or record_key in seen:
            raise ValueError("Capture contains a duplicate/wrong-boot/non-observation frame")
        seen.add(record_key)
        # A frame with no MAC cannot be labelled authenticated, even historically.
        if frame.authentication == "not_authenticated" and item["authentication_at_capture"] != "not_authenticated":
            raise ValueError("Unsigned frame cannot claim prior HMAC verification")
        auth.append(frame.authentication)
        (observations if frame.kind == "observation" else events).append(record)
    projection = c.project(device, observations, joint_covariance=value["joint_covariance"])
    if type(value["calibrations"]) is not list or len(value["calibrations"]) > 16:
        raise ValueError("Too many calibration declarations")
    declarations = {}
    for declaration in value["calibrations"]:
        calibration_declaration(declaration)
        ref = c.content_identity(declaration)
        if ref in declarations:
            raise ValueError("Duplicate calibration declaration")
        declarations[ref] = declaration
    return {"manifest_ref": c.content_identity(device), "observations": observations, "events": events,
            "projection": projection, "calibrations": [_calibration_status(v, device, declarations) for v in observations],
            "authentication_recheck": "shared_key_possession_verified" if all(x == "shared_key_possession_verified" for x in auth)
            else "not_reverified" if "not_reverified" in auth else "not_authenticated",
            "source_authentication": "not_established", "verification_status": "not_verified"}


def register_calibration_declaration(journal, declaration, *, device, authority, now_ns):
    """Retain a host-approved declaration, never calibrate/adjust an instrument."""
    authority.check("CALIBRATE", device, now_ns)
    calibration_declaration(declaration)
    channel = c.channel_for(device, declaration["channel_id"])
    if declaration["manifest_ref"] != c.content_identity(device) or declaration["unit"] != channel["unit"]:
        raise ValueError("Calibration declaration target differs from bound configuration")
    reference = c.content_identity(declaration)
    namespace = "calibration:" + reference
    previous = journal.state(namespace)
    if previous is None:
        journal.compare_and_set_state(namespace, 0, declaration)
    elif c.canonical_json(previous["value"]) != c.canonical_json(declaration):
        raise ValueError("Calibration reference content conflict")
    return {"status": "DECLARATION_RETAINED", "calibration_ref": reference,
            "physical_calibration": "not_performed", "certificate_authentication": "not_established"}
