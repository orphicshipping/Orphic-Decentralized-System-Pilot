"""Explicit MEMS Studio CSV projection into NET's existing observation contract.

No GUI automation, binary Datalog decoder, device communication, calibration or
implicit resampling. Column/bit mappings are operator-declared and retained.
"""
from __future__ import annotations

import csv
from fractions import Fraction
import math
import re

from .control_contracts import bytes_ref, content_ref, keys, text
from .control_plane import ObservationBus
from .core.identities import content_identity

MAX_ROWS = 4096
MAX_CHANNELS = 32
MAX_CSV = 2 * 1024 * 1024
TIME_SCALES = {"s": Fraction(1), "ms": Fraction(1, 1000), "us": Fraction(1, 1000000)}
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")


def _number(token):
    if type(token) is not str or len(token) > 128 or not NUMBER.fullmatch(token):
        raise ValueError("Expected a finite decimal token, not a formula or missing value")
    if "e" in token.lower() and abs(int(token.lower().split("e")[1])) > 400:
        raise ValueError("Decimal exponent exceeds parser budget")
    exact = Fraction(token)
    value = float(exact)
    if not math.isfinite(value) or abs(value) > 1e150 or (value == 0 and exact != 0):
        raise ValueError("Decimal outside the native numeric envelope")
    return exact, value


def validate_profile(profile):
    keys(profile, {"schema", "studio_version", "configuration_ref", "origin", "delimiter", "header", "time_column",
                   "time_unit", "clock_id", "time_basis", "segments", "freshness", "channels", "ignored_columns"})
    if profile["schema"] != "ciw.mems-csv-profile.v1":
        raise ValueError("Unsupported explicitly mapped MEMS CSV profile")
    for key in ("studio_version", "clock_id", "time_column"):
        text(profile[key])
    content_ref(profile["configuration_ref"])
    if profile["origin"] not in ("synthetic", "caller_declared_acquisition"):
        raise ValueError("Declare synthetic or caller-declared acquisition origin")
    if profile["time_basis"] not in ("export_timestamp", "device_timestamp", "reconstructed_timestamp"):
        raise ValueError("Declare timestamp basis without claiming clock qualification")
    if profile["delimiter"] not in (",", ";", "\t") or profile["time_unit"] not in TIME_SCALES:
        raise ValueError("Unsupported explicit delimiter or time unit")
    header = profile["header"]
    if type(header) is not list or not 3 <= len(header) <= 128 or any(type(c) is not str or not c for c in header) or len(set(header)) != len(header):
        raise ValueError("Header must contain unique nonempty declared columns")
    if profile["time_column"] not in header:
        raise ValueError("Time column absent")
    segments = profile["segments"]
    if type(segments) is not list or not segments or segments[0] != 0 or any(type(v) is not int or not 0 <= v < MAX_ROWS for v in segments) or segments != sorted(set(segments)):
        raise ValueError("Declare ordered zero-based segment start rows, beginning at zero")
    fresh = profile["freshness"]
    keys(fresh, {"kind", "column", "base", "data_columns"})
    if fresh["kind"] not in ("datalog2-column-mask", "explicit-sensor-mask") or fresh["column"] not in header or fresh["column"] == profile["time_column"]:
        raise ValueError("An explicit freshness bit-field mapping is required")
    if type(fresh["base"]) is not int or fresh["base"] not in (10, 16):
        raise ValueError("Declare decimal or hexadecimal freshness encoding")
    if type(fresh["data_columns"]) is not list or not 1 <= len(fresh["data_columns"]) <= 64 or len(set(fresh["data_columns"])) != len(fresh["data_columns"]):
        raise ValueError("Freshness data-column order must be explicit")
    if any(c not in header or c in (profile["time_column"], fresh["column"]) for c in fresh["data_columns"]):
        raise ValueError("Invalid freshness data columns")
    channels = profile["channels"]
    if type(channels) is not list or not 1 <= len(channels) <= MAX_CHANNELS:
        raise ValueError("Require 1..32 selected scalar channels")
    used, identities, bits = [], set(), set()
    for channel in channels:
        keys(channel, {"column", "bit", "entity_id", "model_id", "quantity", "unit", "frame"})
        for key in ("column", "entity_id", "model_id", "quantity", "unit", "frame"):
            text(channel[key])
        bit = channel["bit"]
        if type(bit) is not int or not 0 <= bit < 64 or channel["column"] not in fresh["data_columns"]:
            raise ValueError("Invalid selected channel bit or column")
        if fresh["kind"] == "datalog2-column-mask" and bit != fresh["data_columns"].index(channel["column"]):
            raise ValueError("Datalog2 bits follow the declared sensor-data column order")
        identity = (channel["model_id"], channel["entity_id"], channel["quantity"])
        if identity in identities:
            raise ValueError("Duplicate native channel identity")
        identities.add(identity); used.append(channel["column"]); bits.add(bit)
    ignored = profile["ignored_columns"]
    if type(ignored) is not list or any(type(v) is not str for v in ignored):
        raise ValueError("Ignored columns must be explicitly listed")
    accounted = [profile["time_column"], fresh["column"], *used, *ignored]
    if len(accounted) != len(set(accounted)) or set(accounted) != set(header):
        raise ValueError("Every column must have exactly one interpretation or explicit omission")
    # Unknown sensor bits are not guessed. For column masks all data columns are known,
    # including deliberately ignored columns; sensor-mask maps only the declared bits.
    return (1 << len(fresh["data_columns"])) - 1 if fresh["kind"] == "datalog2-column-mask" else sum(1 << v for v in bits)


def project(raw: bytes, profile: dict) -> dict:
    known_bits = validate_profile(profile)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_CSV or b"\x00" in raw:
        raise ValueError("CSV byte budget or NUL violation")
    lines = raw.splitlines(keepends=True)
    if len(lines) < 2 or len(lines) - 1 > MAX_ROWS:
        raise ValueError("Require 1..4096 data rows")
    rows = []
    spans = []
    position = 0
    for line in lines:
        start = position; position += len(line)
        # A single physical line per record is deliberate; multiline fields refuse.
        parsed = list(csv.reader([line.decode("utf-8")], delimiter=profile["delimiter"], strict=True))
        if len(parsed) != 1:
            raise ValueError("Expected one CSV record per physical line")
        rows.append(parsed[0]); spans.append([start, position])
    if rows[0] != profile["header"] or any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError("CSV differs from exact declared header or column count")
    if profile["segments"][-1] >= len(rows) - 1:
        raise ValueError("Declared reset segment is outside the recording")
    ref, mapping_ref = bytes_ref(raw), content_identity(profile)
    bus = ObservationBus(min(65536, (len(rows) - 1) * len(profile["channels"])))
    projection, withheld, segment, previous = [], [], -1, None
    h = {v: i for i, v in enumerate(rows[0])}
    count = {c["column"]: {"new": 0, "held": 0, "missing": 0} for c in profile["channels"]}
    for index, row in enumerate(rows[1:]):
        if index in profile["segments"]:
            segment += 1; previous = None
        stamp, _ = _number(row[h[profile["time_column"]]])
        stamp *= TIME_SCALES[profile["time_unit"]]
        if stamp < 0 or (previous is not None and stamp < previous):
            raise ValueError("Timestamp reversal requires an explicit separate segment")
        previous = stamp
        time_s = float(stamp)
        if not math.isfinite(time_s) or (time_s == 0 and stamp != 0) or time_s > 1e150:
            raise ValueError("Unrepresentable timestamp; do not replace it with host time")
        token = row[h[profile["freshness"]["column"]]]
        pattern = r"[0-9]+" if profile["freshness"]["base"] == 10 else r"(?:0x)?[0-9a-fA-F]+"
        if len(token) > 24 or not re.fullmatch(pattern, token):
            raise ValueError("Malformed or missing freshness bit field")
        mask = int(token, profile["freshness"]["base"])
        if mask & ~known_bits:
            raise ValueError("Undeclared freshness bits")
        for channel in profile["channels"]:
            column = channel["column"]
            item = {"data_row": index, "physical_line": index + 2, "byte_span": spans[index + 1],
                    "column": column, "segment": segment, "freshness_mask": mask,
                    "observation_ref": None}
            if not mask & (1 << channel["bit"]):
                count[column]["held"] += 1; item["status"] = "HELD_EXPORT_VALUE_NOT_NEW_OBSERVATION"
                projection.append(item); continue
            token = row[h[column]]
            if token == "":
                count[column]["missing"] += 1; item["status"] = "FRESH_BUT_MISSING_VALUE"
                withheld.append(item); projection.append(item); continue
            _, value = _number(token)
            observation = bus.observe(identity={"model_id": channel["model_id"], "entity_id": channel["entity_id"], "execution_id": None},
                clock={"id": profile["clock_id"] + ":segment:" + str(segment), "time_s": time_s},
                frame=channel["frame"], quantity=channel["quantity"], value=value, unit=channel["unit"], uncertainty=None,
                provenance={"provider": "mems-studio-export", "sources": [ref, mapping_ref, profile["configuration_ref"]],
                            "semantics": "simulated" if profile["origin"] == "synthetic" else "observed"})
            count[column]["new"] += 1; item.update(status="PROJECTED", observation_ref=observation["record_digest"])
            projection.append(item)
    return {"source_ref": ref, "profile_ref": mapping_ref, "native_stream": bus.snapshot(), "mapping": projection,
            "rows": len(rows) - 1, "channel_counts": count, "withheld": withheld,
            "time_basis": profile["time_basis"], "clock_qualification": "not_established", "uncertainty": "unknown",
            "source_authentication": "not_established", "origin": profile["origin"],
            "scope": "CSV projection only; native Datalog bytes, acquisition completeness and calibration not established"}
