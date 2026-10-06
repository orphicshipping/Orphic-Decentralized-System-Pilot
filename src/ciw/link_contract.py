"""Strict Link producer records; existing NET observations/covariance stay native.

Device declarations are not trusted driver registrations. Hashes bind content,
not physical identity, calibration, firmware installation, or process release.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
import math
import re

from .control_contracts import bytes_ref, content_ref, keys, observation, record
from .core.identities import canonical_json, content_identity

DEVICE_SCHEMA = "ciw.link-device.v1"
OBS_SCHEMA = "ciw.link-observation.v1"
EVENT_SCHEMA = "ciw.link-event.v1"
CAPTURE_SCHEMA = "ciw.link-capture.v1"
OPERATION = "link.project-observations.v1"
MAX_RECORDS = 256
MAX_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_RAW = 4096
PERSONAS = ("GaugeLink", "MachineLink", "ConditionLink", "FieldLink", "ThermalLink", "DAQLink", "CalLink")
PERMISSIONS = frozenset({"READ", "CONFIGURE", "CALIBRATE", "UPDATE", "CONTROL"})
STAGES = frozenset({"raw", "reported", "calibrated", "derived", "inferred", "decision"})
CONTEXT_FIELDS = frozenset({"asset_id", "part_id", "part_revision", "lot_id", "cavity_id", "cycle_id", "serial_id",
                            "machine_id", "tool_id", "work_order_id", "sample_id", "coordinate_reference"})
LIMITS = {"physical_validation": "not_established", "source_authentication": "not_established",
          "calibration_traceability": "not_established", "hardware_actuation": "not_performed",
          "state_admission": "not_performed", "production_release": "not_performed",
          "verification_status": "not_verified"}


def text(value, *, maximum=128):
    if (type(value) is not str or not value or value != value.strip() or len(value) > maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Require bounded nonempty text without control characters")
    return value


def integer(value, *, maximum=2**64 - 1, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Require a bounded exact integer, not a boolean")
    return value


def finite(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e100:
        raise ValueError("Require a finite scalar, not a boolean")
    return value


def bounded(value, maximum=MAX_CAPTURE_BYTES):
    count = 0
    def visit(v, depth=0):
        nonlocal count
        count += 1
        if count > 100000 or depth > 24:
            raise ValueError("Link JSON complexity bound exceeded")
        if type(v) is dict and len(v) <= 1024:
            for k, x in v.items():
                text(k, maximum=512); visit(x, depth + 1)
        elif type(v) is list and len(v) <= 1024:
            for x in v:
                visit(x, depth + 1)
        elif type(v) is str and len(v) <= 131072:
            # Encode now to refuse lone UTF-16 surrogates, including escaped ones.
            v.encode("utf-8")
        elif v is None or type(v) is bool:
            pass
        elif type(v) in (int, float):
            finite(v)
        else:
            raise ValueError("Unsupported or oversized Link JSON value")
    visit(value)
    raw = canonical_json(value).encode("utf-8")
    if len(raw) > maximum:
        raise ValueError("Link JSON byte budget exceeded")
    return raw


def loads(raw: bytes, maximum=MAX_CAPTURE_BYTES):
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise ValueError("Require bounded nonempty JSON bytes")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def real(token):
        value = float(token)
        if not math.isfinite(value) or (value == 0 and Decimal(token) != 0):
            raise ValueError("Nonfinite or underflowed JSON number")
        return value
    def no_constant(token):
        raise ValueError("Nonstandard JSON number")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=real, parse_constant=no_constant)
    bounded(value, maximum)
    return value


def packed(raw: bytes, maximum=MAX_RAW):
    if type(raw) is not bytes or len(raw) > maximum:
        raise ValueError("Raw evidence size exceeded")
    return {"bytes_b64": base64.b64encode(raw).decode("ascii"), "byte_count": len(raw), "sha256": bytes_ref(raw)}


def unpack(value, maximum=MAX_RAW):
    keys(value, {"bytes_b64", "byte_count", "sha256"})
    integer(value["byte_count"], maximum=maximum)
    encoded = value["bytes_b64"]
    if type(encoded) is not str or len(encoded) > 4 * ((maximum + 2) // 3):
        raise ValueError("Raw evidence encoding too large")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) != value["byte_count"] or packed(raw, maximum) != value:
        raise ValueError("Raw byte identity/count/canonical encoding mismatch")
    return raw


def _image(value):
    keys(value, {"version", "content_ref"})
    text(value["version"])
    content_ref(value["content_ref"])


def configuration(value, channels):
    keys(value, {"revision", "enabled_channels", "sample_rate_hz", "ranges"})
    integer(value["revision"], maximum=2**53 - 1, minimum=1)
    enabled = value["enabled_channels"]
    if (type(enabled) is not list or not enabled or len(enabled) != len(set(enabled))
            or not set(enabled) <= set(channels)):
        raise ValueError("Configuration must explicitly select unique known channels")
    keys(value["sample_rate_hz"], set(enabled)); keys(value["ranges"], set(enabled))
    for channel in enabled:
        rate = value["sample_rate_hz"][channel]
        if rate is not None and not 0 < finite(rate) <= 1e9:
            raise ValueError("Unsupported declared sample rate")
        bounds = value["ranges"][channel]
        if bounds is not None:
            if type(bounds) is not list or len(bounds) != 2 or finite(bounds[0]) >= finite(bounds[1]):
                raise ValueError("Invalid declared input range")
    return deepcopy(value)


def manifest(value):
    bounded(value, 65536)
    keys(value, {"schema", "device_id", "instrument_id", "hardware_revision", "firmware", "gateware",
                 "persona", "channels", "clock", "configuration", "capabilities", "control"})
    if value["schema"] != DEVICE_SCHEMA or value["persona"] not in PERSONAS or value["control"] != "NONE":
        raise ValueError("Unsupported Link device schema/persona/control declaration")
    for key in ("device_id", "instrument_id", "hardware_revision"):
        text(value[key])
    _image(value["firmware"])
    if value["gateware"] is not None:
        _image(value["gateware"])
    caps = value["capabilities"]
    if type(caps) is not list or len(caps) != len(set(caps)) or not set(caps) <= PERMISSIONS - {"CONTROL"} or "READ" not in caps:
        raise ValueError("Unsupported capability declaration; control is never enabled")
    channels = value["channels"]
    if type(channels) is not list or not 1 <= len(channels) <= 16:
        raise ValueError("Declare 1..16 channels")
    names = set()
    for channel in channels:
        keys(channel, {"id", "transducer_id", "quantity", "unit", "frame", "encoding"})
        for key in channel:
            text(channel[key])
        if channel["id"] in names or channel["encoding"] not in {"notation-ascii-scalar.v1", "opaque.v1"}:
            raise ValueError("Duplicate channel or unsupported input encoding")
        names.add(channel["id"])
    configuration(value["configuration"], names)
    clock = value["clock"]
    keys(clock, {"id", "tick_hz", "timestamp_event", "sync_reference", "uncertainty_ns"})
    text(clock["id"])
    if clock["tick_hz"] is not None:
        integer(clock["tick_hz"], minimum=1, maximum=10**12)
    if clock["timestamp_event"] not in {"sensor_sample", "instrument_report_receipt", "declared_event"}:
        raise ValueError("Declare the actual timestamped event")
    if clock["sync_reference"] is not None:
        content_ref(clock["sync_reference"])
    if clock["uncertainty_ns"] is not None:
        integer(clock["uncertainty_ns"])
    return deepcopy(value)


def channel_for(device, name):
    for channel in device["channels"]:
        if channel["id"] == name:
            if name not in device["configuration"]["enabled_channels"]:
                raise ValueError("Channel is not enabled in the bound configuration")
            return channel
    raise ValueError("Channel is absent from the bound device manifest")


def scalar(raw, unit):
    """Notation's exact test/bridge grammar, NOT a universal vendor protocol."""
    if type(raw) is not bytes or len(raw) > MAX_RAW:
        raise ValueError("Invalid scalar message bytes")
    match = re.fullmatch(rb"([+-]?[0-9]+(?:\.[0-9]+)?) ([A-Za-z0-9_./^%-]+)\r?\n", raw)
    if match is None or match[2].decode("ascii") != unit:
        raise ValueError("Scalar record grammar or exact unit mismatch")
    exact = Decimal(match[1].decode("ascii"))
    value = float(exact)
    if not math.isfinite(value) or abs(value) > 1e100 or (value == 0 and exact != 0):
        raise ValueError("Scalar conversion overflow/underflow")
    return value


def observation_id(device_id, boot_id, sequence):
    return content_identity({"device_id": device_id, "boot_id": boot_id, "sequence": sequence})


def validate_observation(value, device):
    bounded(value, 65536)
    keys(value, {"schema", "measurement_id", "manifest_ref", "device_id", "instrument_id", "boot_id", "sequence",
                 "channel_id", "configuration_ref", "device_tick", "acquired_at", "raw", "stage", "value",
                 "missing_reason", "calibration_ref", "uncertainty", "uncertainty_reason", "procedure_ref", "context",
                 "quality_flags", "parents", "transform_ref", "origin"})
    if value["schema"] != OBS_SCHEMA or value["origin"] not in {"synthetic", "recorded"} or value["stage"] not in STAGES:
        raise ValueError("Unsupported observation schema/origin/stage")
    if (value["device_id"] != device["device_id"] or value["instrument_id"] != device["instrument_id"]
            or value["manifest_ref"] != content_identity(device)
            or value["configuration_ref"] != content_identity(device["configuration"])):
        raise ValueError("Observation device/firmware/configuration binding mismatch")
    text(value["boot_id"]); integer(value["sequence"])
    if value["measurement_id"] != observation_id(value["device_id"], value["boot_id"], value["sequence"]):
        raise ValueError("Measurement occurrence identity mismatch")
    channel = channel_for(device, value["channel_id"])
    if value["device_tick"] is not None:
        integer(value["device_tick"])
    if value["acquired_at"] is not None:
        from .calibration_status import _timestamp
        _timestamp(value["acquired_at"], "declared acquisition time", strict=True)
    raw = unpack(value["raw"])
    if value["value"] is None:
        text(value["missing_reason"])
        if value["uncertainty"] is not None:
            raise ValueError("Missing value cannot have numeric covariance")
    else:
        finite(value["value"])
        if value["missing_reason"] is not None:
            raise ValueError("Present value cannot also be a missing value")
    for key in ("calibration_ref", "procedure_ref", "transform_ref"):
        if value[key] is not None:
            content_ref(value[key])
    if type(value["parents"]) is not list or len(value["parents"]) > 32 or len(set(value["parents"])) != len(value["parents"]):
        raise ValueError("Invalid observation parents")
    for parent in value["parents"]:
        content_ref(parent)
    if value["stage"] in {"calibrated", "derived", "inferred", "decision"}:
        if not value["parents"] or value["transform_ref"] is None:
            raise ValueError("Processed stages must retain parents and transformation reference")
    elif value["parents"] or value["transform_ref"] is not None:
        raise ValueError("Raw/reported ingress cannot impersonate a processed result")
    if value["stage"] == "calibrated" and value["calibration_ref"] is None:
        raise ValueError("A declared calibrated stage must identify its calibration")
    if value["stage"] == "reported" and value["value"] is not None and channel["encoding"] == "notation-ascii-scalar.v1":
        if canonical_json(value["value"]) != canonical_json(scalar(raw, channel["unit"])):
            raise ValueError("Reported value differs from exact retained scalar bytes")
    if value["uncertainty"] is None:
        text(value["uncertainty_reason"])
    else:
        from .core.covariance import validate_covariance_artifact
        if value["uncertainty_reason"] is not None:
            raise ValueError("Supplied covariance cannot also be missing")
        validate_covariance_artifact(value["uncertainty"], expected_quantity_ids=[channel["quantity"]],
                                    expected_units=[channel["unit"]], expected_frame=channel["frame"])
        if canonical_json(value["uncertainty"]["reference_values"]) != canonical_json([value["value"]]):
            raise ValueError("Covariance reference value differs from observation")
    keys(value["context"], CONTEXT_FIELDS)
    for item in value["context"].values():
        if item is not None:
            text(item)
    if type(value["quality_flags"]) is not list or len(value["quality_flags"]) > 16:
        raise ValueError("Quality flags must be a bounded list")
    for flag in value["quality_flags"]:
        text(flag)
    return deepcopy(value)


def make_observation(device, *, boot_id, sequence, channel_id, raw, tick=None,
                     origin="recorded", context=None, calibration_ref=None, acquired_at=None, procedure_ref=None):
    device = manifest(device)
    channel = channel_for(device, channel_id)
    reported = channel["encoding"] == "notation-ascii-scalar.v1"
    value = {"schema": OBS_SCHEMA, "measurement_id": observation_id(device["device_id"], boot_id, sequence),
             "manifest_ref": content_identity(device), "device_id": device["device_id"], "instrument_id": device["instrument_id"],
             "boot_id": boot_id, "sequence": sequence, "channel_id": channel_id,
             "configuration_ref": content_identity(device["configuration"]), "device_tick": tick, "acquired_at": acquired_at,
             "raw": packed(raw), "stage": "reported" if reported else "raw",
             "value": scalar(raw, channel["unit"]) if reported else None,
             "missing_reason": None if reported else "opaque_record_requires_a_separate_qualified_decoder",
             "calibration_ref": calibration_ref, "uncertainty": None, "uncertainty_reason": "not_supplied",
             "procedure_ref": procedure_ref, "context": dict.fromkeys(CONTEXT_FIELDS) if context is None else context,
             "quality_flags": [], "parents": [], "transform_ref": None, "origin": origin}
    return validate_observation(value, device)


def project(device, observations, *, joint_covariance=None):
    """Build native observations without inventing clocks, covariance or calibration.

    Only raw/reported scalars are projected. Other stages remain retained device
    declarations until a separately qualified producer transformation is bound.
    """
    from .control_plane import ObservationBus
    device = manifest(device)
    if type(observations) is not list or not 1 <= len(observations) <= MAX_RECORDS:
        raise ValueError("Link capture must contain 1..256 observations")
    values = [validate_observation(v, device) for v in observations]
    boot = values[0]["boot_id"]
    if any(v["boot_id"] != boot for v in values):
        raise ValueError("One capture cannot merge boot/clock domains")
    if len({v["origin"] for v in values}) != 1:
        raise ValueError("One capture cannot mix synthetic and recorded origins")
    seq = [v["sequence"] for v in values]
    if seq != sorted(set(seq)):
        raise ValueError("Duplicate, conflicting or reordered observation sequences")
    gaps = []
    expected = 0
    for n in seq:
        if n > expected:
            gaps.append({"first_missing": expected, "last_missing": n - 1})
        expected = n + 1
    known = set()
    for item in values:
        if any(p not in known for p in item["parents"]):
            raise ValueError("Processed observation parent must be retained earlier in this capture")
        known.add(content_identity(item))
    if joint_covariance is not None:
        from .core.covariance import validate_covariance_artifact
        channels = [channel_for(device, v["channel_id"]) for v in values]
        if any(v["value"] is None or v["uncertainty"] is not None for v in values) or len({c["frame"] for c in channels}) != 1:
            raise ValueError("Joint covariance requires all scalar values in one declared frame and no duplicate marginal budgets")
        axes = [v["measurement_id"] for v in values]
        validate_covariance_artifact(joint_covariance, expected_quantity_ids=axes,
                                    expected_units=[c["unit"] for c in channels], expected_frame=channels[0]["frame"])
        if canonical_json(joint_covariance["reference_values"]) != canonical_json([v["value"] for v in values]):
            raise ValueError("Joint covariance reference values differ")
    bus = ObservationBus(MAX_RECORDS)
    withheld, mappings = [], []
    ticks = [v["device_tick"] for v in values if v["device_tick"] is not None]
    origin_tick = min(ticks) if ticks else None
    hz = device["clock"]["tick_hz"]
    native_clock_id = content_identity({"device_id": device["device_id"], "manifest_ref": content_identity(device),
                                        "device_clock": device["clock"]["id"], "boot_id": boot,
                                        "tick_hz": hz, "origin_tick": origin_tick})
    last_tick = {}
    for item in values:
        channel = channel_for(device, item["channel_id"])
        why = None
        if item["quality_flags"]:
            # Native observations cannot express these unclassified flags.
            # Preserve the original record, not an unflagged scalar surrogate.
            why = "quality_flags_require_review"
        elif item["device_tick"] is None or hz is None:
            why = "clock_mapping_unavailable"
        elif item["stage"] not in {"raw", "reported"}:
            why = "processed_stage_requires_qualified_transform"
        if why:
            withheld.append({"measurement_id": item["measurement_id"], "reason": why})
            continue
        tick = item["device_tick"]
        if item["channel_id"] in last_tick and tick <= last_tick[item["channel_id"]]:
            raise ValueError("Nonincreasing device tick for one channel; boot/reset not declared")
        last_tick[item["channel_id"]] = tick
        delta = tick - origin_tick
        seconds = float(Fraction(delta, hz))
        if round(Fraction.from_float(seconds) * hz) != delta:
            withheld.append({"measurement_id": item["measurement_id"], "reason": "native_time_precision_insufficient"})
            continue
        refs = [content_identity(item), item["raw"]["sha256"], content_identity(device)]
        if joint_covariance is not None:
            refs.append(joint_covariance["covariance_id"])
        native = observation(identity={"model_id": item["manifest_ref"], "entity_id": content_identity({"instrument_id": item["instrument_id"], "channel_id": item["channel_id"]}), "execution_id": None},
            clock={"id": native_clock_id, "time_s": seconds}, frame=channel["frame"],
            quantity=channel["quantity"], value=item["value"], unit=channel["unit"], uncertainty=item["uncertainty"],
            provenance={"provider": "ciw.link", "sources": list(dict.fromkeys(refs)),
                        "semantics": "simulated" if item["origin"] == "synthetic" else "observed"})
        bus.publish(native)
        mappings.append({"measurement_id": item["measurement_id"], "observation_ref": native["record_digest"],
                         "tick": tick, "origin_tick": origin_tick, "tick_hz": hz, "native_clock_id": native_clock_id,
                         "exact_relative_seconds": {"numerator": delta, "denominator": hz},
                         "projection": "binary64_checked_to_nearest_device_tick",
                         "timestamp_event": device["clock"]["timestamp_event"],
                         "uncertainty_scope": "joint_covariance_retained_separately" if joint_covariance is not None else "as_declared"})
    return {"schema": "ciw.link-projection.v1", "native_stream": bus.snapshot(), "mappings": mappings,
            "withheld": withheld, "gaps": gaps, "joint_covariance": deepcopy(joint_covariance),
            "clock_relation_to_host": "UNKNOWN", "limits": deepcopy(LIMITS)}
