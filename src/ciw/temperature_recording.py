"""Bounded, offline ingestion of reviewed public TCLab temperature recordings.

Temperature columns remain reported temperatures and heater settings remain
commands in percent. Importing retained bytes is never a new acquisition,
calibration, physical validation, or permission to operate equipment.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import csv
import io
import re
from urllib.parse import urlsplit

from .control_contracts import bytes_ref, content_ref, keys, number, observation, text
from .operations.runner import check_seal, digest, seal
from .telemetry import canonical
from .temperature_recording_sources import PUBLIC_SOURCES


SOURCE_SCHEMA = "ciw.temperature-recording-source.v1"
SCHEMA = "ciw.temperature-recording.v1"
MAX_BYTES = 48 * 1024
MAX_ROWS = 4096
CHANNELS = ("T1", "T2")
COMMANDS = ("Q1", "Q2")
AUTHORITY = {
    "scope": "retained_recording_import_and_structural_checks",
    "new_physical_acquisition": "not_performed",
    "producer_authentication": "not_established",
    "calibration_application": "not_performed",
    "physical_validation": "not_established",
    "state_admission": "not_performed",
    "hardware_actuation": "not_performed",
}
_MANIFEST_FIELDS = {
    "schema", "raw_sha256", "origin", "producer", "source_url", "revision",
    "license", "protocol_url", "columns", "clock", "time_unit",
    "temperature_unit", "command_unit", "source_description",
}
_BUNDLE_FIELDS = {
    "schema", "manifest", "raw_bytes_b64", "raw_sha256", "rows",
    "recording_context", "authority", "record_digest",
}
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def _url(value):
    text(value)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("Malformed recording source URL") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment):
        raise ValueError("Recording provenance requires an HTTPS URL without credentials or fragment")
    return parsed


def _manifest(value):
    keys(value, _MANIFEST_FIELDS)
    if value["schema"] != SOURCE_SCHEMA:
        raise ValueError("Unsupported temperature recording source schema")
    content_ref(value["raw_sha256"])
    if type(value["origin"]) is not str or value["origin"] not in {"public_experimental_recording", "test_fixture"}:
        raise ValueError("Declare reviewed public recording or test fixture origin")
    for field in ("producer", "license", "source_description"):
        text(value[field])
    source_url = _url(value["source_url"])
    _url(value["protocol_url"])
    revision = value["revision"]
    if type(revision) is not str or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Recording source requires an exact 40-character revision")
    if revision not in source_url.path.split("/"):
        raise ValueError("Source URL must bind the exact retained revision")
    keys(value["columns"], {"time", *CHANNELS, *COMMANDS})
    for name in value["columns"].values():
        text(name)
        if len(name) > 64:
            raise ValueError("Recording column names must be bounded and exact")
    if len(set(value["columns"].values())) != 5:
        raise ValueError("Each recording quantity needs a distinct source column")
    keys(value["clock"], {"time_kind"})
    if type(value["clock"]["time_kind"]) is not str or value["clock"]["time_kind"] != "elapsed_host_time":
        raise ValueError("Require elapsed host time in seconds; sample indices need a separate conversion contract")
    if (value["time_unit"] != "s" or value["temperature_unit"] != "degC"
            or value["command_unit"] != "%"):
        raise ValueError("Recording units must preserve seconds, degrees Celsius, and heater command percent")
    if value["origin"] == "public_experimental_recording":
        # Public provenance is an exact, reviewed source binding. Re-sealing a
        # synthetic recording with an observed label does not grant this origin.
        if not any(value == reviewed for reviewed in PUBLIC_SOURCES.values()):
            raise ValueError("Public experimental origin requires an exact reviewed source manifest")
    return deepcopy(value)


def _numeric(value, *, nullable=False):
    if value == "" and nullable:
        return None
    if len(value) > 128 or _DECIMAL.fullmatch(value) is None:
        raise ValueError("Recording cells require finite decimal numbers; only temperatures may be empty")
    result = float(value)
    number(result)
    # Inspect the already-validated significand without parsing an unbounded exponent.
    nonzero_significand = any(char in "123456789" for char in value.lower().split("e", 1)[0])
    if result == 0.0 and nonzero_significand:
        raise ValueError("Nonzero decimal is too small to retain as a numeric value; refusing silent conversion to zero")
    return result


def _rows(raw, manifest):
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader)
        columns = manifest["columns"]
        if len(header) != 5 or len(set(header)) != 5 or set(header) != set(columns.values()):
            raise ValueError("CSV header must contain exactly the five distinct declared columns")
        indices = {name: header.index(column) for name, column in columns.items()}
        rows, previous = [], None
        for row in reader:
            record_number = len(rows) + 2  # Header is record 1, independent of quoted CSV lines.
            if len(rows) >= MAX_ROWS or len(row) != 5:
                raise ValueError(f"CSV record {record_number}: recording row count or row width exceeds the declared profile")
            values = {}
            for name, index in indices.items():
                try:
                    values[name] = _numeric(row[index], nullable=name in CHANNELS)
                except ValueError as exc:
                    raise ValueError(f"CSV record {record_number}, column {name}: {exc}") from exc
            stamp = values.pop("time")
            if stamp < 0 or previous is not None and stamp <= previous:
                raise ValueError(f"CSV record {record_number}, column time: recording times must be nonnegative and strictly increasing")
            for channel in COMMANDS:
                if not 0 <= values[channel] <= 100:
                    raise ValueError(f"CSV record {record_number}, column {channel}: heater command percentages must lie in [0, 100]")
            rows.append({"row_number": len(rows) + 2, "time_s": stamp, **values})
            previous = stamp
        if not rows:
            raise ValueError("Recording requires at least one data row")
        return rows
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise ValueError("Recording requires a bounded UTF-8 CSV with header and data rows") from exc


def _context(manifest):
    ref = manifest["raw_sha256"]
    return {
        "recording_id": "recording:" + ref,
        "coordinate_scope": "log_columns_only; not_an_apparatus_frame",
        "frame": "recording-columns:" + ref,
        "clock": {"id": "recording-clock:" + ref,
                  "time_kind": manifest["clock"]["time_kind"],
                  "epoch_utc": None, "uncertainty_s": None,
                  "acquisition_to_receipt_alignment": "unknown"},
        "acquisition_execution_id": None,
        "instrument_serial": None,
        "installed_configuration_id": None,
        "calibration_evidence": None,
        "measurement_covariance": None,
        "independent_reference": None,
        "temperature_semantics": "source_reported_temperature; calibration_not_established",
        "command_semantics": "recorded_heater_settings_percent; not_measured_heat_power",
        "origin_basis": ("reviewed_public_source_binding; not_device_authentication"
                         if manifest["origin"] == "public_experimental_recording"
                         else "explicit_software_test_fixture"),
    }


def import_recording(raw: bytes, manifest: dict) -> dict:
    """Parse finite retained bytes without device IO, normalization, or interpolation."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Temperature recording requires 1 byte to 48 KiB of exact CSV bytes")
    retained = _manifest(manifest)
    if bytes_ref(raw) != retained["raw_sha256"]:
        raise ValueError("Recording bytes differ from the manifest's exact raw digest")
    return seal({
        "schema": SCHEMA, "manifest": retained,
        "raw_bytes_b64": base64.b64encode(raw).decode("ascii"),
        "raw_sha256": bytes_ref(raw), "rows": _rows(raw, retained),
        "recording_context": _context(retained), "authority": deepcopy(AUTHORITY),
    })


def validate_recording(bundle: dict) -> dict:
    """Reconstruct all derived fields from exact bytes and the reviewed declaration."""
    keys(bundle, _BUNDLE_FIELDS)
    if bundle["schema"] != SCHEMA:
        raise ValueError("Unsupported temperature recording schema")
    encoded = bundle["raw_bytes_b64"]
    if type(encoded) is not str or not 0 < len(encoded) <= 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("Recording requires bounded canonical base64 bytes")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Recording requires bounded canonical base64 bytes") from exc
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("Recording requires canonical base64")
    check_seal(bundle)
    expected = import_recording(raw, bundle["manifest"])
    if canonical(bundle) != canonical(expected):
        raise ValueError("Recording fields contradict their retained bytes and source manifest")
    return expected


def observation_stream(bundle: dict, channel: str) -> dict:
    """Export reported temperatures only, retaining source time and missing values."""
    retained = validate_recording(bundle)
    if channel not in CHANNELS:
        raise ValueError("Only T1 and T2 are temperature observations; Q1/Q2 remain command histories")
    manifest, context = retained["manifest"], retained["recording_context"]
    semantics = "observed" if manifest["origin"] == "public_experimental_recording" else "simulated"
    sources = [retained["raw_sha256"], digest(manifest), retained["record_digest"]]
    observations = [observation(
        identity={"model_id": "recording-columns:" + retained["raw_sha256"],
                  "entity_id": context["recording_id"] + ":" + channel,
                  "execution_id": None},
        clock={"id": context["clock"]["id"], "time_s": row["time_s"]},
        frame=context["frame"], quantity="reported_temperature:" + channel,
        value=row[channel], unit=manifest["temperature_unit"], uncertainty=None,
        provenance={"provider": manifest["producer"], "sources": sources, "semantics": semantics})
        for row in retained["rows"]]
    return seal({"schema": "ciw.observation-stream.v1", "observations": observations})


def inspect_recording(bundle: dict) -> dict:
    """Report source integrity and unfilled physical obligations separately."""
    retained = validate_recording(bundle)
    rows, manifest = retained["rows"], retained["manifest"]
    deltas = [later["time_s"] - earlier["time_s"] for earlier, later in zip(rows, rows[1:])]
    return {
        "schema": SCHEMA, "record_digest": retained["record_digest"],
        "raw_sha256": retained["raw_sha256"], "origin": manifest["origin"],
        "source_url": manifest["source_url"], "revision": manifest["revision"],
        "integrity": "validated_against_retained_bytes_and_manifest",
        "source_binding": retained["recording_context"]["origin_basis"],
        "row_count": len(rows), "time_range_s": [rows[0]["time_s"], rows[-1]["time_s"]],
        "time_kind": manifest["clock"]["time_kind"],
        "sample_interval_s": {"minimum": min(deltas) if deltas else None,
                              "maximum": max(deltas) if deltas else None,
                              "resampled": False},
        "temperature_channels": {name: {"unit": "degC", "missing_count": sum(row[name] is None for row in rows),
            "uncertainty": "unknown", "calibration": "not_established"} for name in CHANNELS},
        "command_channels": {name: {"unit": "%", "semantics": "recorded_heater_command",
            "measured_heat_power": False} for name in COMMANDS},
        "physical_qualification": {
            "status": "incomplete", "measured_thermal_compatible": False,
            "missing_evidence": ["instrument_serial_and_installed_configuration",
                "calibration_traceability_and_application", "clock_epoch_alignment_and_uncertainty",
                "measurement_uncertainty_and_correlations", "independent_held_out_reference",
                "measured_heat_power_and_ambient", "shell_core_geometry_and_model_applicability"],
        },
        "authority": deepcopy(AUTHORITY),
    }
