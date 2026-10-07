"""Create-only measurement exchange and optional existing Workbench attachment.

The object directory is an export, not a new canonical ledger. PostgreSQL files
are staging inputs, never a connection or migration. Parquet is an explicitly
requested derivative and always travels with the complete covariance/evidence.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import csv
import io
import os
from pathlib import Path
import tempfile

from . import link_contract as c, link_measurements as m, link_study_io as study_io
from .hardware_workflow import read_bytes
from .link_store import safe_path

ARCHIVE_SCHEMA = "notations.measurement-object-export.v1"
COLUMNS = ("bundle_id", "measurement_id", "device_id", "channel_id", "stage", "quantity", "unit", "record_json")
POSTGRES_DDL = """-- Operator-reviewed staging only; no automatic database connection or admission.
-- Load measurements.csv with PostgreSQL COPY ... FROM STDIN WITH (FORMAT CSV, HEADER TRUE).
-- Load into a fresh transaction/table; duplicate primary keys refuse rather than overwrite.
CREATE TABLE notation_measurement_stage_v1 (
    bundle_id text NOT NULL,
    measurement_id text NOT NULL,
    device_id text NOT NULL,
    channel_id text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('indicated', 'corrected')),
    quantity text NOT NULL,
    unit text NOT NULL,
    record_json text NOT NULL,
    PRIMARY KEY (bundle_id, measurement_id)
);
-- record_json is exact canonical JSON text, not a substitute for the source objects.
-- Full covariance and original investigation bytes remain in the companion archive.
"""


def _write(path: Path, raw: bytes):
    path = safe_path(path)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def save(value, path):
    value = m.validate(value)
    raw = c.bounded(value, m.MAX_BYTES)
    # Stage and publish with a hard link, so an existing destination is never replaced.
    path = safe_path(path)
    with tempfile.TemporaryDirectory(prefix=".net-measurement-", dir=path.parent) as temp:
        stage = Path(temp)/"bundle.json"
        _write(stage, raw)
        os.link(stage, path)
    return {"status": "RETAINED", "bundle_id": value["bundle_id"], "path": str(path)}


def load(path):
    return m.validate(c.loads(read_bytes(safe_path(path), limit=m.MAX_BYTES), m.MAX_BYTES))


def restore(value, output):
    """Recover exact source bytes with no new scientific operation occurrences."""
    value = m.validate(value)
    raw_files = {name: m._unpack(packed) for name, packed in value["source_files"].items()}
    root = safe_path(output)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, raw in raw_files.items():
        _write(root/name, raw)
    # Native offline validators may recompute consistency checks, never record
    # fresh operations or contact a device/provider. The source bytes stay exact.
    reopened = m.build(root)
    if not c.canonical_json(reopened) == c.canonical_json(value):
        raise ValueError("Restored measurement evidence differs")
    return {"status": "RESTORED", "bundle_id": value["bundle_id"], "path": str(root),
            "new_executions": 0, "device_contact": False}


def _rows(value):
    return [{"bundle_id": value["bundle_id"], "measurement_id": r["identity"]["measurement_id"],
        "device_id": r["identity"]["device_id"], "channel_id": r["identity"]["channel_id"],
        "stage": r["quantity"]["stage"], "quantity": r["quantity"]["kind"], "unit": r["quantity"]["unit"],
        "record_json": c.canonical_json(r)} for r in value["measurements"]]


def _csv(value):
    target = io.StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(_rows(value))
    return target.getvalue().encode("utf-8")


def _parquet_modules():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Parquet export requires separately installed pyarrow==25.0.1") from exc
    if pa.__version__ != "25.0.1":
        raise RuntimeError("Parquet qualification requires unchanged pyarrow==25.0.1 pin")
    return pa, pq


def _parquet(value):
    pa, pq = _parquet_modules()
    # Text columns preserve exact scalar JSON numbers, integer clocks and missingness.
    # This is an analytical index, not a lossy flattened replacement for covariance.
    schema = pa.schema([(name, pa.string()) for name in COLUMNS], metadata={
        b"notation.scope": b"index_with_exact_record_json_full_evidence_in_companion_archive",
        b"notation.bundle_id": value["bundle_id"].encode()})
    table = pa.Table.from_pylist(_rows(value), schema=schema)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="NONE", version="2.6")
    raw = sink.getvalue().to_pybytes()
    recovered = pq.read_table(pa.BufferReader(raw))
    if not recovered.schema.equals(schema, check_metadata=True) or recovered.to_pylist() != _rows(value):
        raise ValueError("Parquet round trip failed exact record/index retention")
    return raw


def export_objects(value, output, *, parquet=False):
    value = m.validate(value)
    if type(parquet) is not bool:
        raise ValueError("Parquet selection must be explicit boolean")
    native = value["native"]
    files = {"measurement-bundle.json": c.bounded(value, m.MAX_BYTES),
             **{name: m._unpack(packed) for name, packed in value["source_files"].items()},
             "observations.json": c.bounded(native["stream"], m.MAX_BYTES),
             "native-mapping.json": c.bounded({k:v for k,v in native.items() if k not in {"stream", "covariance"}}, m.MAX_BYTES),
             "postgres-schema.sql": POSTGRES_DDL.encode(), "measurements.csv": _csv(value)}
    for name, cov in (("input-covariance.json", value["input_covariance"]), ("output-covariance.json", native["covariance"])):
        if cov is not None:
            files[name] = c.bounded(cov, m.MAX_BYTES)
    if parquet:
        files["measurements.parquet"] = _parquet(value)  # Refuse before creating output on missing runtime.
    root = safe_path(output)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    objects = root/"objects"
    objects.mkdir(mode=0o700)
    written = set()
    entries = {}
    for name, raw in files.items():
        reference = c.bytes_ref(raw)
        entries[name] = {"sha256": reference, "byte_count": len(raw)}
        if reference not in written:
            _write(objects/(reference[7:]+".bin"), raw)
            written.add(reference)
    manifest = {"schema": ARCHIVE_SCHEMA, "bundle_id": value["bundle_id"], "files": entries,
        "postgres": "STAGING_FILES_ONLY_NOT_LOADED", "parquet": "EXPORTED" if parquet else "NOT_REQUESTED",
        "physical_qualification": "NOT_ESTABLISHED", "state_admission": "NOT_PERFORMED"}
    manifest["export_id"] = c.content_identity(manifest)
    _write(root/"manifest.json", c.bounded(manifest, 65536))  # Complete marker is written last.
    review_objects(root)
    return manifest


def review_objects(directory, *, verify_parquet=True):
    """Recover checked primary evidence; optionally validate the Parquet derivative.

    Disabling derivative review checks its retained bytes only, not its meaning.
    Primary bundle/native/covariance/CSV validation is never bypassed.
    """
    if type(verify_parquet) is not bool:
        raise ValueError("Derivative review mode must be explicit boolean")
    root = safe_path(directory)
    manifest = c.loads(read_bytes(root/"manifest.json", limit=65536), 65536)
    c.keys(manifest, {"schema", "bundle_id", "files", "postgres", "parquet", "physical_qualification", "state_admission", "export_id"})
    if (manifest["schema"] != ARCHIVE_SCHEMA or manifest["export_id"] != c.content_identity({k:v for k,v in manifest.items() if k!="export_id"})
            or manifest["postgres"] != "STAGING_FILES_ONLY_NOT_LOADED" or manifest["parquet"] not in {"EXPORTED", "NOT_REQUESTED"}
            or manifest["physical_qualification"] != "NOT_ESTABLISHED" or manifest["state_admission"] != "NOT_PERFORMED"):
        raise ValueError("Unsupported export contract or altered manifest")
    if type(manifest["files"]) is not dict or not 1 <= len(manifest["files"]) <= 11:
        raise ValueError("Invalid object inventory")
    files = {}
    for name, entry in manifest["files"].items():
        c.keys(entry, {"sha256", "byte_count"})
        ref = c.content_ref(entry["sha256"])
        c.integer(entry["byte_count"], minimum=1, maximum=m.MAX_BYTES)
        raw = read_bytes(safe_path(root/"objects"/(ref[7:]+".bin")), limit=m.MAX_BYTES)
        if len(raw) != entry["byte_count"] or c.bytes_ref(raw) != ref:
            raise ValueError("Object bytes differ from manifest")
        files[name] = raw
    if "measurement-bundle.json" not in files:
        raise ValueError("Export has no retained measurement bundle")
    value = m.validate(c.loads(files["measurement-bundle.json"], m.MAX_BYTES))
    expected = {"measurement-bundle.json": c.bounded(value,m.MAX_BYTES),
        **{name:m._unpack(packed) for name,packed in value["source_files"].items()},
        "observations.json":c.bounded(value["native"]["stream"],m.MAX_BYTES),
        "native-mapping.json":c.bounded({k:v for k,v in value["native"].items() if k not in {"stream","covariance"}},m.MAX_BYTES),
        "postgres-schema.sql":POSTGRES_DDL.encode(), "measurements.csv":_csv(value)}
    for name,cov in (("input-covariance.json",value["input_covariance"]),("output-covariance.json",value["native"]["covariance"])):
        if cov is not None: expected[name]=c.bounded(cov,m.MAX_BYTES)
    if manifest["parquet"] == "EXPORTED":
        raw = files.get("measurements.parquet")
        if raw is None:
            raise ValueError("Missing declared Parquet object")
        if verify_parquet:
            pa,pq = _parquet_modules()
            # Round-trip comparison avoids relying on deterministic Parquet encoding.
            table = pq.read_table(pa.BufferReader(raw))
            if (table.column_names != list(COLUMNS) or table.to_pylist()!=_rows(value)
                    or table.schema.metadata != {b"notation.scope":b"index_with_exact_record_json_full_evidence_in_companion_archive",
                                                  b"notation.bundle_id":value["bundle_id"].encode()}
                    or any(field.type != pa.string() for field in table.schema)):
                raise ValueError("Parquet index differs from retained measurements")
        expected["measurements.parquet"]=raw
    if value["bundle_id"]!=manifest["bundle_id"] or files!=expected:
        raise ValueError("Exported derivatives differ from retained native evidence")
    actual_objects = {p.name for p in (root/"objects").iterdir()}
    if actual_objects != {entry["sha256"][7:]+".bin" for entry in manifest["files"].values()}:
        raise ValueError("Unaccounted objects in export")
    if {p.name for p in root.iterdir()} != {"manifest.json", "objects"}:
        raise ValueError("Unaccounted files in export")
    return value


def _attach_workbench_locked(value, session, *, dataset_id: str, recorded_at: str):
    """Stage one bundle into the existing Workbench using the owner's native APIs.

    Requires the typed-data Workbench implementation from PR185/200. No shared
    module is replaced or monkeypatched. The caller retains its session only
    after this complete validated staging succeeds. No scientific operation runs.
    """
    value = m.validate(value)
    c.text(dataset_id)
    if session.run["evidence_id"] != value["measurements"][0]["identity"]["source_evidence_id"]:
        raise ValueError("Measurement attachment requires its original Session source")
    # Equal evidence alone is insufficient: a numerical replay has the same
    # input but different occurrences. Bind all original retained results.
    with tempfile.TemporaryDirectory(prefix="net-measurement-session-check-") as tmp:
        for name, packed in value["source_files"].items():
            (Path(tmp)/name).write_bytes(m._unpack(packed))
        _, _, original, _ = study_io.read(tmp)
    for identity, result in original.results.items():
        if c.canonical_json(session.results.get(identity)) != c.canonical_json(result):
            raise ValueError("Measurement attachment requires its exact retained result occurrences")
    for identity, execution in original.executions.items():
        if c.canonical_json(session.executions.get(identity)) != c.canonical_json(execution):
            raise ValueError("Measurement attachment requires its exact retained execution occurrences")
    try:
        from .data_sources import retain_record
    except ImportError as exc:
        raise RuntimeError("Workbench attachment requires the existing typed-data integration (PR185/200)") from exc
    from .control_contracts import artifact
    from .operations.runner import seal
    from .workbench import Workbench
    staged = Workbench.restore(session.workbench.serialize())
    raw = c.bounded(value, m.MAX_BYTES)
    # Retain the complete recoverable bundle as an existing artifact family.
    native_artifact = artifact(raw, media_type="application/vnd.notation.measurement-bundle+json",
        producer="ciw.link-measurement-view.v1", experiment_id=dataset_id,
        metadata={"bundle_id":value["bundle_id"], "payload":m._pack(raw), "physical_qualification":"NOT_ESTABLISHED"})
    native_artifact["created_at"]=recorded_at
    native_artifact=seal(native_artifact)
    descriptors=[]
    def add(native, suffix, family, dependencies):
        envelope=retain_record(native, dataset_id=dataset_id+suffix, revision=1, parent_source_id=None,
                               recorded_at=recorded_at,data_family=family)
        payload={"kind":"data-record","label":dataset_id+suffix,
                 "bytes_b64":base64.b64encode(c.canonical_json(envelope).encode("utf-8")).decode()}
        if dependencies: payload["input_source_ids"]=dependencies
        descriptor=staged.add_source(payload);descriptors.append(descriptor)
        return descriptor["source_id"]
    root_id=add(native_artifact,"/bundle","artifact",[])
    prior=None
    if value["input_covariance"] is not None:
        prior=add(value["input_covariance"],"/input-covariance","covariance",[root_id])
    output=None
    if value["native"]["covariance"] is not None:
        output=add(value["native"]["covariance"],"/output-covariance","covariance",[root_id]+([prior] if prior else []))
    add(value["native"]["stream"],"/observations","observation-stream",[root_id]+([output] if output else []))
    Workbench.restore(staged.serialize())
    session.workbench=staged
    return {"status":"RETAINED_IN_WORKBENCH","bundle_id":value["bundle_id"],"sources":descriptors,
            "new_executions":0,"physical_qualification":"NOT_ESTABLISHED","state_admission":"NOT_PERFORMED"}


def attach_workbench(value, session, *, dataset_id: str, recorded_at: str):
    """Atomically attach to the caller's existing Session under its own lock."""
    with session._lock:
        return _attach_workbench_locked(value, session, dataset_id=dataset_id, recorded_at=recorded_at)
