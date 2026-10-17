"""Bounded device/NET coherence over retained bytes; never physical authority.

The first profile is one int64 gauge sample and an exact rational affine map to
nanometres. Filters validate without repairing/dropping evidence. Signature,
computation, clock/calibration declarations and functional eligibility are
separate checks. All canonical identities reuse the existing CIW primitive.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
import re
import struct

from .core.identities import canonical_json, content_identity, new_identity
from .legibility import compile_bundle, sign_bundle, verify_bundle, load_json_strict

PACKET = "ciw.device-boundary-packet.v1"
PAYLOAD = "ciw.device-gauge-observation.v1"
TRANSFORM = "ciw.device-affine-length.v1"
REPORT = "ciw.device-boundary-review.v1"
PROGRAM = (b"scout.native.device-affine-length.v1\n"
           b"input: DBI1 | context[32] | count i64 | gain i64 | offset i64 | denominator u64; all LE\n"
           b"bounds: |count|<=2^40, |gain|<=2^20, |offset|<=2^60, 1<=denominator<=2^20\n"
           b"output: DBO1 | context[32] | (count*gain+offset) i128 | denominator u64; length in nm\n"
           b"exact rational arithmetic; no rounding; no physical-validity or authorization claim\n")
FILTER_D = "device.raw-i64le.v1"
FILTER_N = "net.strict-gauge-packet.v1"
MAX_BYTES = 12 * 1024 * 1024
UNITS_NM = {"m": 1_000_000_000, "mm": 1_000_000, "um": 1_000, "nm": 1}
FUNCTIONS = ("display_raw", "record_raw", "display_calibrated", "record_calibrated",
             "compare_tolerance", "generate_inspection_record", "run_model_inference",
             "release_part", "machine_control")
SUPPORTED = frozenset(FUNCTIONS[:6])
LIMITS = {"physical_validation": "not_established", "firmware_attestation": "not_established",
          "gateware_attestation": "not_established", "clock_synchronization": "not_established",
          "state_admission": "not_performed", "equipment_authority": False,
          "inspection_release": False, "scope": "recorded_scalar_processing_only"}


def encode(value: dict) -> bytes:
    raw = canonical_json(value).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("boundary document exceeds 12 MiB")
    return raw


def parse(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("boundary document requires 1..12582912 bytes")
    result = load_json_strict(raw)
    if type(result) is not dict:
        raise ValueError("boundary document must be an object")
    encode(result)
    return result


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("unexpected or missing boundary fields")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("integer outside the declared boundary")
    return value


def identifier(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value) is None:
        raise ValueError("expected bounded identity, not an address")
    return value


def hash_id(value):
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("expected SHA-256 identity")
    return value


def raw_id(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def unb64(text: str, maximum=MAX_BYTES) -> bytes:
    if type(text) is not str or len(text) > 4 * ((maximum + 2) // 3):
        raise ValueError("base64 byte budget exceeded")
    raw = base64.b64decode(text, validate=True)
    if len(raw) > maximum or base64.b64encode(raw).decode() != text:
        raise ValueError("noncanonical base64")
    return raw


def ratio(value):
    keys(value, {"numerator", "denominator"})
    n = integer(value["numerator"], -(2**100), 2**100)
    d = integer(value["denominator"], 1, 2**60)
    # Require reduced rational spelling to avoid cross-language aliasing.
    q = Fraction(n, d)
    if (q.numerator, q.denominator) != (n, d):
        raise ValueError("rational values must be reduced")
    return q


def rational(q):
    q = Fraction(q)
    return {"numerator": q.numerator, "denominator": q.denominator}


def validate_transform(value):
    keys(value, {"schema", "calibration_id", "gain", "offset", "denominator",
                 "valid_from_ns", "valid_until_ns", "count_min", "count_max",
                 "uncertainty_bound_nm", "uncertainty_basis"})
    if value["schema"] != TRANSFORM:
        raise ValueError("unsupported transformation")
    identifier(value["calibration_id"])
    integer(value["gain"], -2**20, 2**20)
    integer(value["offset"], -2**60, 2**60)
    integer(value["denominator"], 1, 2**20)
    lo = integer(value["valid_from_ns"], 0, 2**63-1)
    hi = integer(value["valid_until_ns"], 0, 2**63-1)
    if hi <= lo:
        raise ValueError("calibration validity must be a nonempty half-open interval")
    lo = integer(value["count_min"], -2**40, 2**40)
    hi = integer(value["count_max"], -2**40, 2**40)
    if hi < lo:
        raise ValueError("invalid calibration count domain")
    bound = value["uncertainty_bound_nm"]
    if bound is not None and ratio(bound) < 0:
        raise ValueError("negative uncertainty bound")
    expected = "unknown" if bound is None else "operator_declared_total_absolute_bound_not_covariance"
    if value["uncertainty_basis"] != expected:
        raise ValueError("uncertainty status contradicts its declared bound")
    return deepcopy(value)


def validate_payload(value):
    keys(value, {"schema", "device_id", "boot_id", "sequence", "sample_id", "execution_id",
                 "frame_id", "clock_id", "captured_at_ns", "time_uncertainty_ns",
                 "firmware_id", "gateware_id", "raw_b64", "raw_id", "transform",
                 "device_state", "origin", "filter_id"})
    if value["schema"] != PAYLOAD or value["origin"] not in {"synthetic", "recorded"}:
        raise ValueError("only synthetic or recorded inputs are supported; no live acquisition")
    for field in ("device_id", "boot_id", "sample_id", "frame_id", "clock_id", "filter_id"):
        identifier(value[field])
    from .core.identities import validate_identity
    validate_identity(value["execution_id"], "execution")
    integer(value["sequence"], 0, 2**53-1)
    integer(value["captured_at_ns"], 0, 2**63-1)
    if value["time_uncertainty_ns"] is not None:
        integer(value["time_uncertainty_ns"], 0, 10**12)
    hash_id(value["firmware_id"])
    if value["gateware_id"] is not None:
        hash_id(value["gateware_id"])
    raw = unb64(value["raw_b64"], 8)
    if len(raw) != 8 or raw_id(raw) != value["raw_id"]:
        raise ValueError("raw int64 bytes/identity mismatch")
    integer(struct.unpack("<q", raw)[0], -2**40, 2**40)
    validate_transform(value["transform"])
    state = value["device_state"]
    keys(state, {"quantity", "unit", "value", "frame_id"})
    if state["quantity"] != "length" or state["unit"] not in UNITS_NM or state["frame_id"] != value["frame_id"]:
        raise ValueError("state quantity, units or coordinate frame mismatch")
    ratio(state["value"])
    return deepcopy(value)


def statement(payload):
    """Host rebuilds all committed bytes, never trusts device input commitments."""
    value = validate_payload(payload)
    cfg = value["transform"]
    context = bytes.fromhex(content_identity(value)[7:])
    return b"DBI1" + context + unb64(value["raw_b64"], 8) + struct.pack(
        "<qqQ", cfg["gain"], cfg["offset"], cfg["denominator"])


def compute(raw: bytes) -> bytes:
    """Independent arbitrary-integer reference to the checked Rust guest."""
    if type(raw) is not bytes or len(raw) != 68 or raw[:4] != b"DBI1":
        raise ValueError("invalid affine statement wire format")
    count, gain, offset, den = struct.unpack("<qqqQ", raw[36:])
    integer(count, -2**40, 2**40); integer(gain, -2**20, 2**20)
    integer(offset, -2**60, 2**60); integer(den, 1, 2**20)
    return b"DBO1" + raw[4:36] + (count * gain + offset).to_bytes(16, "little", signed=True) + struct.pack("<Q", den)


def reconstruct(payload):
    output = compute(statement(payload))
    return Fraction(int.from_bytes(output[36:52], "little", signed=True), struct.unpack("<Q", output[52:])[0])


def signature_bundle(payload: dict, *, object_id: str, execution_id: str):
    """Reuse NET Legibility's existing content/artifact/signature convention."""
    raw = encode(payload)
    contract = {"schema": "ciw.legibility-contract.v1",
        "object": {"object_id": identifier(object_id), "version": content_identity(payload),
                   "label": "Device boundary evidence", "kind": "recorded-boundary-message"},
        "bindings": {"evidence_id": raw_id(raw), "operation_id": "device.boundary.review.v1",
                     "execution_id": execution_id, "verification_id": None},
        "semantics": {"coordinate_frame": "boundary-message", "properties": [],
                      "relationships": [], "assumptions": ["Signature is attribution, not physical validity"]},
        "claims": [], "qualification": {"status": "unqualified", "calibration_refs": [],
            "verification_refs": [], "canonical_admission": False},
        "vision": {"image_artifact_id": None, "annotations": []},
        "artifacts": [{"artifact_id": "payload", "media_type": "application/json",
                       "sha256": raw_id(raw), "size_bytes": len(raw)}]}
    return compile_bundle(contract, {"payload": raw})


def signed_packet(payload, private_key, *, proof=None):
    value = validate_payload(payload)
    bundle = signature_bundle(value, object_id=value["device_id"], execution_id=value["execution_id"])
    packet = {"schema": PACKET, "payload": value, "signature": sign_bundle(bundle, private_key), "proof": proof}
    encode(packet)
    return packet


def authenticated(packet, trusted_keys):
    keys(packet, {"schema", "payload", "signature", "proof"})
    if packet["schema"] != PACKET:
        raise ValueError("unsupported packet schema")
    p = validate_payload(packet["payload"])
    bundle = signature_bundle(p, object_id=p["device_id"], execution_id=p["execution_id"])
    report = verify_bundle(bundle, packet["signature"], artifacts={"payload": encode(p)},
                           trusted_keys=trusted_keys, expected_object_id=p["device_id"],
                           expected_version=content_identity(p))
    return report


def make_reply(review: dict, private_key, *, net_id: str, now_ns: int, ttl_ns: int):
    """Signed review response, deliberately not a transferable execution permit."""
    integer(now_ns, 0, 2**63-1); integer(ttl_ns, 1, 10**12)
    integer(now_ns+ttl_ns, 0, 2**63-1)
    body = {"schema": "ciw.device-boundary-reply.v1", "net_id": identifier(net_id),
            "execution_id": new_identity("execution"), "packet_id": review["packet_id"],
            "review_id": review["review_id"], "device_id": review["device_id"],
            "policy_id": review["policy_id"], "issued_at_ns": now_ns, "expires_at_ns": now_ns+ttl_ns,
            "canonical_state_nm": deepcopy(review["canonical_state_nm"]),
            "capabilities": deepcopy(review["capabilities"]), "authorizes_execution": False}
    bundle = signature_bundle(body, object_id=net_id, execution_id=body["execution_id"])
    return {"payload": body, "signature": sign_bundle(bundle, private_key)}


def verify_reply(reply, *, trusted_keys, packet_id, device_id, policy_id, net_id, now_ns):
    keys(reply, {"payload", "signature"}); body = reply["payload"]
    keys(body, {"schema", "net_id", "execution_id", "packet_id", "review_id", "device_id",
                "policy_id", "issued_at_ns", "expires_at_ns", "canonical_state_nm", "capabilities", "authorizes_execution"})
    integer(now_ns, 0, 2**63-1)
    if (body["schema"] != "ciw.device-boundary-reply.v1" or body["authorizes_execution"] is not False
            or (body["packet_id"], body["device_id"], body["policy_id"], body["net_id"]) != (packet_id, device_id, policy_id, net_id)
            or not integer(body["issued_at_ns"], 0, 2**63-1) <= now_ns < integer(body["expires_at_ns"], 0, 2**63-1)):
        raise ValueError("reply context or freshness mismatch; not a capability permit")
    bundle = signature_bundle(body, object_id=body["net_id"], execution_id=body["execution_id"])
    result = verify_bundle(bundle, reply["signature"], artifacts={"payload": encode(body)}, trusted_keys=trusted_keys)
    if (result["signature_valid"] is not True or not result["issuer_trusted"]
            or result["content_intact"] is not True or result["artifact_status"]!="verified"):
        raise ValueError("untrusted or invalid NET reply")
    return {"status": "accepted_historical_review", "reply_id": content_identity(reply), "authorizes_execution": False}
