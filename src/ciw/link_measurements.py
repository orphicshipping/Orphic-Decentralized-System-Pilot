"""Versioned measurement views over retained Link/NET results.

No second observation store, numerical kernel, clock map or calibration model.
The bundle carries the original investigation bytes and existing native records.
A measurement ID identifies an interpretation occurrence, not a new physical
sample. Replaying computation preserves capture/line identity and allocates new
operation/result identities. Hashes establish consistency, not authenticity.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile

from . import link_contract as c, link_study as study, link_study_io as io
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .link_protocol import MAX_FRAME

SCHEMA = "notations.measurement.v1"
BUNDLE_SCHEMA = "notations.measurement-bundle.v1"
MAX_BYTES = 8 * 1024 * 1024
FILES = {"request.json": study.MAX_BYTES, "workspace.json": MAX_BYTES, "graph.json": MAX_BYTES}


def _pack(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Measurement source requires bounded exact bytes")
    return {"sha256": c.bytes_ref(raw), "byte_count": len(raw),
            "chunks": [c.packed(raw[i:i+32768], 32768) for i in range(0, len(raw), 32768)]}


def _unpack(value: dict) -> bytes:
    c.keys(value, {"sha256", "byte_count", "chunks"})
    c.integer(value["byte_count"], minimum=1, maximum=MAX_BYTES)
    if type(value["chunks"]) is not list or not 1 <= len(value["chunks"]) <= MAX_BYTES // 32768:
        raise ValueError("Invalid source chunk count")
    raw = b"".join(c.unpack(part, 32768) for part in value["chunks"])
    if c.bounded(_pack(raw), MAX_BYTES*2) != c.bounded(value, MAX_BYTES*2):
        raise ValueError("Original source bytes, chunking or digest mismatch")
    return raw


def _records(request: dict, session) -> tuple[list[dict], dict]:
    results = {r["operation_id"]: r for r in session.results.values()}
    if set(results) != set(study.OPS):
        raise ValueError("Measurement projection requires the complete retained scalar investigation")
    decoded_result, corrected_result = (results[op] for op in study.OPS[:2])
    decoded, corrected = decoded_result["data"], corrected_result["data"]
    capture = request["capture"]
    device = capture["manifest"]
    channel = c.channel_for(device, request["channel_id"])
    # Preserve every receipt corresponding to a contributing chunk, not merely
    # a floating point time chosen to make downstream native streams possible.
    from .link_store import decode_bound
    receipts = {}
    for frame in capture["frames"]:
        _, value, _, _ = decode_bound(c.unpack(frame["wire"], MAX_FRAME), device, offline=True)
        if value.get("schema") == c.OBS_SCHEMA:
            receipts[value["measurement_id"]] = {"received_ns": frame["received_ns"],
                "host_clock": frame["host_clock"], "wire_ref": frame["wire"]["sha256"],
                "authentication_at_capture": frame["authentication_at_capture"]}
    native = study.native(request, decoded, corrected, execution_id=corrected_result["execution_id"],
                         source_evidence=session.run["evidence_id"])
    native_refs = {m["line_id"]: m["native_ref"] for m in native["mappings"]}
    withheld = {m["id"]: m["reason"] for m in native["withheld"]}
    measurements = []
    for i, row in enumerate(decoded["rows"]):
        indicated_id = None
        for stage, result, value, covariance in (
            ("indicated", decoded_result, row["value"], request["calibration"]["joint_covariance"]),
            ("corrected", corrected_result, corrected["values"][i], corrected["covariance"])):
            mid = c.content_identity({"schema": SCHEMA, "line_id": row["id"],
                                      "result_id": result["result_id"], "stage": stage})
            if stage == "indicated":
                indicated_id = mid
            uncertainty = {"status": "UNKNOWN", "covariance_ref": None, "index": None,
                           "quantity_id": None, "variance": None, "scope": "not_declared"}
            if covariance is not None:
                uncertainty = {"status": "DECLARED_CONDITIONAL", "covariance_ref": covariance["covariance_id"],
                    "index": i, "quantity_id": covariance["quantity_ids"][i], "variance": covariance["matrix"][i][i],
                    "scope": "full_joint_matrix_retained_not_independent_marginals"}
            record = {"schema": SCHEMA,
                "identity": {"measurement_id": mid, "line_id": row["id"],
                    "capture_ref": decoded["capture_ref"], "source_evidence_id": session.run["evidence_id"],
                    "device_id": device["device_id"], "instrument_id": device["instrument_id"],
                    "channel_id": channel["id"], "transducer_id": channel["transducer_id"]},
                "quantity": {"kind": channel["quantity"], "stage": stage, "value": value, "unit": row["unit"]},
                "time": {"device_clock": device["clock"], "boot_id": decoded["boot_id"],
                    "report_receipt_tick": row["receipt_tick"], "acquisition_time": row["acquired_at"],
                    "clock_relation": "UNKNOWN", "receipt_scope": decoded["timing"],
                    "chunk_receipts": [{"chunk_id": x["measurement_id"], **receipts[x["measurement_id"]]}
                                       for x in row["chunks"]]},
                "metrology": {"calibration_ref": corrected["calibration_ref"] if stage == "corrected" else None,
                    "declared_applicability": corrected["applicability"][i] if stage == "corrected" else "NOT_APPLIED",
                    "uncertainty": uncertainty, "traceability": "NOT_ESTABLISHED",
                    "inspection_qualification": "HELD"},
                "provenance": {"operation_id": result["operation_id"], "execution_id": result["execution_id"],
                    "result_id": result["result_id"], "runtime": result["runtime"], "decoder_id": decoded["decoder"],
                    "manifest_ref": c.content_identity(device), "hardware_revision": device["hardware_revision"],
                    "firmware": device["firmware"], "gateware": device["gateware"], "configuration": device["configuration"],
                    "original_line": row["raw"], "stream_byte_range": row["byte_range"],
                    "chunks": row["chunks"], "procedure_ref": decoded["procedure_ref"],
                    "parents": [] if stage == "indicated" else [indicated_id],
                    "origin": decoded["origin"], "authentication_recheck": "NOT_PERFORMED"},
                "context": {"frame": channel["frame"], "bindings": decoded["context"],
                            "environment": {"status": "NOT_DECLARED"}},
                "native": {"observation_ref": native_refs.get(row["id"]) if stage == "corrected" else None,
                    "withheld_reason": withheld.get(row["id"]) if stage == "corrected" else "indicated_view_only"},
                "verification": {"status": "NOT_PHYSICALLY_VERIFIED", "arithmetic_result_ref": results[study.OPS[3]]["result_id"],
                    "arithmetic_status": results[study.OPS[3]]["data"]["status"],
                    "scope": "inherited_arithmetic_check_not_sensor_validation"}}
            measurements.append(deepcopy(record))
    return measurements, native


def build(directory) -> dict:
    root = safe_path(directory)
    request, raw, session, _ = io.read(root)
    measurements, native = _records(request, session)
    sources = {name: _pack(raw if name == "request.json" else read_bytes(root/name, limit=limit))
               for name, limit in FILES.items()}
    # Revalidate these exact bytes, rather than risk mixing concurrent revisions
    # of an investigation between io.read and the final file reads.
    with tempfile.TemporaryDirectory(prefix="net-measurement-snapshot-") as tmp:
        for name, packed in sources.items():
            (Path(tmp)/name).write_bytes(_unpack(packed))
        exact_request, _, exact_session, _ = io.read(tmp)
        exact_measurements, exact_native = _records(exact_request, exact_session)
    if not study._equal([measurements, native], [exact_measurements, exact_native]):
        raise ValueError("Investigation changed during measurement snapshot")
    value = {"schema": BUNDLE_SCHEMA, "measurements": measurements, "native": native,
             "input_covariance": request["calibration"]["joint_covariance"],
             "source_files": sources, "limits": dict(c.LIMITS)}
    value["bundle_id"] = c.content_identity(value)
    c.bounded(value, MAX_BYTES)
    return value


def validate(value: dict) -> dict:
    c.bounded(value, MAX_BYTES)
    c.keys(value, {"schema", "measurements", "native", "input_covariance", "source_files", "limits", "bundle_id"})
    if value["schema"] != BUNDLE_SCHEMA:
        raise ValueError("Unsupported measurement bundle")
    c.keys(value["source_files"], set(FILES))
    with tempfile.TemporaryDirectory(prefix="net-measurement-review-") as tmp:
        for name, packed in value["source_files"].items():
            raw = _unpack(packed)
            if len(raw) > FILES[name]:
                raise ValueError("Retained source exceeds its native bound")
            (Path(tmp)/name).write_bytes(raw)
        expected = build(tmp)
    if not study._equal(value, expected):
        raise ValueError("Measurement view differs from its retained NET investigation")
    return deepcopy(value)


def query(value: dict, *, device_id=None, channel_id=None, stage=None, measurement_id=None) -> list[dict]:
    value = validate(value)
    for key in (device_id, channel_id, measurement_id):
        if key is not None:
            c.text(key)
    if stage is not None and stage not in {"indicated", "corrected"}:
        raise ValueError("Measurement stage must be indicated or corrected")
    return [m for m in value["measurements"] if
        (device_id is None or m["identity"]["device_id"] == device_id) and
        (channel_id is None or m["identity"]["channel_id"] == channel_id) and
        (measurement_id is None or m["identity"]["measurement_id"] == measurement_id) and
        (stage is None or m["quantity"]["stage"] == stage)]
