"""Retained temperature archives on the existing Session, without device access."""
from __future__ import annotations

import math

from .adapters.protocol import InstrumentManifest
from .control_contracts import number
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .temperature_recording import validate_recording

INSTRUMENT = "temperature-recording.v1"


def make_source(recording: dict) -> dict:
    recording = validate_recording(recording)
    rows = recording["rows"]
    frame = recording["recording_context"]["frame"]
    # Validate the derived endpoint before CLI import creates any output files.
    duration = math.nextafter(rows[-1]["time_s"], math.inf)
    try:
        number(duration)
    except ValueError as exc:
        raise ValueError("Final recording time is too large for the bounded exclusive Session selection endpoint") from exc
    units = {"T1": "degC", "T2": "degC", "Q1": "%", "Q2": "%"}
    manifest = InstrumentManifest(
        instrument_id=INSTRUMENT, role="retained_temperature_archive",
        units=units, frames=(frame,),
        sampling={"kind": "retained_irregular_rows", "time_semantics": "source_log_time"},
        calibration_requirements={"status": "unknown; archive import does not calibrate"})
    source = {
        "run_schema": "run.v1", "run_id": "run-temperature-recording-" + recording["record_digest"][7:23],
        "instrument": INSTRUMENT,
        "metadata": {
            # run.v1 needs an exclusive selection endpoint, not a new sample.
            "duration_s": duration,
            "duration_semantics": "exclusive_selection_bound; not measured cycle duration",
            "sample_count": len(rows), "sample_rate_hz": None,
            "coordinate_frame": frame, "manifest": manifest.to_dict(),
            "temperature_recording": recording,
            "provenance": {"source": recording["manifest"]["origin"],
                           "source_sha256": recording["raw_sha256"],
                           "generator": "ciw.temperature_recording_session.make_source",
                           "generator_version": 1},
        },
        "time_s": [row["time_s"] for row in rows],
        "channels": {name: {"unit": unit, "values": [row[name] for row in rows],
                            "semantics": "recorded_temperature" if name.startswith("T") else "recorded_command",
                            "uncertainty": None} for name, unit in units.items()},
        "render": {},
    }
    source["evidence_id"] = evidence_id(source)
    validate_run_structure(source)
    return source


def source_recording(source: dict) -> dict:
    validate_run_structure(source)
    validate_evidence_identity(source)
    try:
        recording = validate_recording(source["metadata"]["temperature_recording"])
    except (KeyError, TypeError) as exc:
        raise ValueError("Missing retained temperature recording") from exc
    if source != make_source(recording):
        raise ValueError("Temperature source differs from its exact retained CSV and provenance")
    return recording
