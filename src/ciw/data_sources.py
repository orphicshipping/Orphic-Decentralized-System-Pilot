"""Strict, retained native NET data, with a deliberately narrow CSV adapter.

The envelope records dataset lineage and exact input bytes, not a universal
scientific model. Native contracts remain authoritative. Importing never starts
providers, authenticates a producer, qualifies a model, or admits state.

CSV is wide, UTF-8, comma-delimited with one header row. Every data column is
explicitly bound to one scalar quantity and every row emits every quantity.
Empty/missing cells, unknown columns, repeated timestamps and unit conversions
are refused. Use native JSON to retain explicit null/missing observations or
full covariance artifacts; neither can be invented from a scalar CSV column.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import csv
from datetime import datetime
from decimal import Decimal
import io
import re
from typing import Any

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import _json
from .control_checks import inspect_record
from .control_contracts import (
    MAX_SAMPLES, _identity, bytes_ref, content_ref, detached, keys, number,
    observation, text,
)
from .core.identities import canonical_json, content_identity

SOURCE_SCHEMA = "ciw.data-source.v1"
SOURCE_KIND = "data-record"
RECEIPT_SCHEMA = "ciw.csv-observation-adapter-receipt.v1"
MAX_BYTES = 32 * 1024 * 1024
# A closed, data-only routing table: persisted data cannot choose executable code.
SCHEMA_FAMILIES = {
    **{f"ciw.{name}.v1": name for name in (
        "state", "observation", "observation-stream", "artifact", "comparison",
        "verification", "parameter-space", "experiment", "checkpoint", "graph-run",
        "check-plan", "check-report", "thermal-math-inspection", "thermal-observation-view",
    )},
    "ciw.thermal-math-inspection.v2": "thermal-math-inspection",
    "covariance-artifact.v1": "covariance",
    "ciw.field-observation-mapping.v1": "field-observation-mapping",
    "ciw.compute-canonical-state.v1": "compute-canonical-state",
    "ciw.gltf-reference.v1": "gltf-reference",
    "ciw.polymer-cycle-request.v1": "polymer-cycle-request",
    "ciw.hoomd-study.v1": "hoomd-study",
    "ciw.hoomd-result.v1": "hoomd-result",
    "ciw.product-study.v1": "product-study",
    "ciw.polymer-manufacturing-report.v1": "manufacturing-report",
    "ciw.polymer-manufacturing-report.v2": "manufacturing-report",
    "ciw.toolpoint-stream-report.v1": "toolpoint-stream-report",
}
_JSON_MEDIA = "application/json"
_CSV_MEDIA = "text/csv; charset=utf-8"
_ENVELOPE_FIELDS = {
    "schema", "dataset_id", "revision", "parent_source_id", "recorded_at",
    "data_family", "native_schema", "native_record", "source_bytes",
    "adapter_receipt", "source_id",
}
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def _encoded(value: Any) -> bytes:
    try:
        return canonical_json(value).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("Data source must contain finite, bounded JSON") from exc


def _bounded(raw: bytes) -> bytes:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Data source requires bounded, nonempty exact bytes")
    return raw


def _native(value: dict, family: str) -> None:
    text(family)
    if type(value) is not dict or type(value.get("schema")) is not str:
        raise ValueError("Native data must declare its versioned schema")
    schema = text(value["schema"])
    expected = SCHEMA_FAMILIES.get(schema)
    if expected is None:
        raise AdapterRefusal("data_family_unsupported", f"Unsupported native data family/schema: {family}/{schema}")
    if family != expected:
        raise ValueError(f"Native schema {schema} belongs to data family {expected}, not {family}")
    # Bounds and strict numbers before either native validator. The standalone
    # covariance validator otherwise allows unbounded JSON metadata.
    detached(value)
    if family == "covariance":
        from .core.covariance import validate_covariance_artifact
        validate_covariance_artifact(value)
    elif family == "field-observation-mapping":
        from .data_field_bridge import validate
        validate(value)
    elif family == "gltf-reference":
        from .gltf_reference import validate_record
        validate_record(value)
    elif family == "compute-canonical-state":
        from .data_compute_bridge import validate_record
        validate_record(value)
    elif family in {"hoomd-study", "hoomd-result"}:
        from .hoomd_contract import validate_study, validate_result
        (validate_study if family == "hoomd-study" else validate_result)(value)
    elif family == "product-study":
        from .product_study import validate
        validate(value)
    elif family == "manufacturing-report":
        from .manufacturing_runtime import verify
        verify(value)
    elif family == "toolpoint-stream-report":
        from .toolpoint_stream import NATIVE, verify
        spec = value.get("spec")
        if (type(spec) is not dict or type(spec.get("plan")) is not dict
                or spec["plan"].get("schema") != NATIVE):
            raise ValueError("Retained QC data supports only the qualified native monitoring profile")
        verify(value)
    elif family == "polymer-cycle-request":
        from .polymer_contract import validate_request
        validate_request(value)
    else:
        inspect_record(value)


def _retained_bytes(raw: bytes, media_type: str) -> dict:
    _bounded(raw)
    return {"media_type": media_type, "bytes_b64": base64.b64encode(raw).decode("ascii"),
            "sha256": bytes_ref(raw), "byte_count": len(raw)}


def _read_bytes(value: dict) -> bytes:
    keys(value, {"media_type", "bytes_b64", "sha256", "byte_count"})
    if text(value["media_type"]) not in {_JSON_MEDIA, _CSV_MEDIA}:
        raise ValueError("Unsupported retained input media type")
    encoded = value["bytes_b64"]
    if type(encoded) is not str or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("Retained source bytes exceed their bound")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Source bytes must be canonical base64") from exc
    _bounded(raw)
    if (base64.b64encode(raw).decode("ascii") != encoded
            or type(value["byte_count"]) is not int or value["byte_count"] != len(raw)
            or content_ref(value["sha256"]) != bytes_ref(raw)):
        raise ValueError("Retained input byte count, encoding or digest mismatch")
    return raw


def _lineage(value: dict) -> None:
    text(value["dataset_id"])
    if type(value["revision"]) is not int or not 1 <= value["revision"] <= 2**53 - 1:
        raise ValueError("Dataset revision must be a positive safe integer")
    if value["parent_source_id"] is not None:
        content_ref(value["parent_source_id"])
    stamp = datetime.fromisoformat(text(value["recorded_at"]))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Dataset recorded_at must have an explicit timezone")


def _validate(value: dict) -> dict:
    keys(value, _ENVELOPE_FIELDS)
    if value["schema"] != SOURCE_SCHEMA:
        raise ValueError("Unsupported data-source envelope schema")
    _lineage(value)
    _native(value["native_record"], value["data_family"])
    if value["native_schema"] != value["native_record"]["schema"]:
        raise ValueError("Envelope native schema contradicts its retained record")
    raw = _read_bytes(value["source_bytes"])
    if value["adapter_receipt"] is None:
        if value["source_bytes"]["media_type"] != _JSON_MEDIA:
            raise ValueError("Native JSON retention cannot claim a CSV media type")
        if _encoded(_json(raw)) != _encoded(value["native_record"]):
            raise ValueError("Retained native record differs from its exact source bytes")
    else:
        if value["source_bytes"]["media_type"] != _CSV_MEDIA or value["data_family"] != "observation-stream":
            raise ValueError("CSV adapter can only produce the observation-stream family")
        receipt = value["adapter_receipt"]
        if type(receipt) is not dict or "mapping" not in receipt:
            raise ValueError("CSV adapter must retain its complete mapping receipt")
        native, expected = _adapt_csv(raw, receipt["mapping"])
        if _encoded(native) != _encoded(value["native_record"]) or _encoded(receipt) != _encoded(expected):
            raise ValueError("CSV adapter receipt or native record contradicts retained input and mapping")
    expected_id = content_identity({key: item for key, item in value.items() if key != "source_id"})
    if content_ref(value["source_id"]) != expected_id:
        raise ValueError("Data source content identity mismatch")
    if len(_encoded(value)) > MAX_BYTES:
        raise ValueError("Data source envelope exceeds its byte budget")
    return deepcopy(value)


def _source(raw: bytes) -> dict:
    """Workbench adapter: validate an exact retained envelope without execution."""
    return _validate(_json(_bounded(raw)))


def _make(native: dict, raw: bytes, *, dataset_id: str, revision: int,
          parent_source_id: str | None, recorded_at: str, data_family: str,
          receipt: dict | None = None) -> dict:
    value = {"schema": SOURCE_SCHEMA, "dataset_id": dataset_id, "revision": revision,
             "parent_source_id": parent_source_id, "recorded_at": recorded_at,
             "data_family": data_family, "native_schema": native.get("schema"),
             "native_record": deepcopy(native),
             "source_bytes": _retained_bytes(raw, _JSON_MEDIA if receipt is None else _CSV_MEDIA),
             "adapter_receipt": deepcopy(receipt)}
    value["source_id"] = content_identity(value)
    return _validate(value)


def retain_record(native: dict | bytes, *, dataset_id: str, revision: int,
                  parent_source_id: str | None, recorded_at: str, data_family: str) -> dict:
    """Retain exact JSON input bytes, or canonical bytes for an in-memory record.

    ``parent_source_id`` names another envelope's content identity. This local
    validator checks its syntax, not whether that parent exists in a catalog.
    """
    if type(native) is bytes:
        raw = _bounded(native)
        native = _json(raw)
    elif type(native) is dict:
        _native(native, data_family)
        raw = _encoded(native)
    else:
        raise ValueError("Require native JSON record or exact JSON bytes")
    if type(native) is not dict:
        raise ValueError("Native record must be a JSON object")
    return _make(native, raw, dataset_id=dataset_id, revision=revision,
                 parent_source_id=parent_source_id, recorded_at=recorded_at, data_family=data_family)


def _unwrap(source: dict) -> dict:
    if type(source) is dict and source.get("schema") == "ciw.workbench-source.v1":
        if source.get("kind") != SOURCE_KIND:
            raise ValueError("Require a data-record workbench source")
        # Verify catalog-level labels, bytes, IDs and byte counts, not just b64.
        from .workbench import _source as retained_source
        fields = {"schema", "kind", "label", "source_schema", "evidence_id", "byte_count", "source_id", "bytes_b64", "data"}
        if "input_source_ids" in source:
            fields.add("input_source_ids")
        keys(source, fields)
        payload = {key: source[key] for key in ("kind", "label", "bytes_b64")}
        if "input_source_ids" in source:
            payload["input_source_ids"] = source["input_source_ids"]
        checked = retained_source(payload)
        if _encoded(checked) != _encoded(source):
            raise ValueError("Workbench source descriptor differs from its exact retained bytes")
        return _source(base64.b64decode(source["bytes_b64"], validate=True))
    return _validate(source)


def native_record(source: dict) -> dict:
    """Return an independently validated, detached original native record."""
    return _unwrap(source)["native_record"]


def descriptor(source: dict) -> dict:
    """Catalog metadata only; scientific shape and missingness stay native."""
    value = _unwrap(source)
    result = {key: value[key] for key in (
        "schema", "source_id", "dataset_id", "revision", "parent_source_id",
        "recorded_at", "data_family", "native_schema",
    )}
    result.update(source_sha256=value["source_bytes"]["sha256"],
                  source_byte_count=value["source_bytes"]["byte_count"],
                  native_record_sha256=bytes_ref(_encoded(value["native_record"])),
                  adapter_receipt=deepcopy(value["adapter_receipt"]),
                  integrity="native_contract_checked", physical_validation="not_performed",
                  state_admission="not_performed", source_authentication="not_established_by_hashes")
    return result


def _mapping(value: dict) -> dict:
    value = detached(value)
    keys(value, {"identity", "clock", "frame", "provenance", "quantities"})
    _identity(value["identity"])
    text(value["frame"])
    keys(value["clock"], {"id", "column", "unit"})
    text(value["clock"]["id"])
    text(value["clock"]["column"])
    if value["clock"]["unit"] != "s":
        raise AdapterRefusal("csv_clock_unit", "CSV clock requires declared seconds (s); no conversion is performed")
    keys(value["provenance"], {"provider", "semantics"})
    text(value["provenance"]["provider"])
    if text(value["provenance"]["semantics"]) not in {"observed", "estimated", "simulated", "reference"}:
        raise ValueError("CSV source must explicitly declare observation semantics")
    quantities = value["quantities"]
    if type(quantities) is not list or not 1 <= len(quantities) <= 64:
        raise ValueError("CSV adapter requires 1..64 explicit scalar quantities")
    columns, names = {value["clock"]["column"]}, set()
    for item in quantities:
        keys(item, {"column", "quantity", "unit"})
        for field in item.values():
            text(field)
        if item["column"] in columns or item["quantity"] in names:
            raise ValueError("CSV mapping column or quantity collision")
        columns.add(item["column"])
        names.add(item["quantity"])
    return value


def _csv_number(value: str, row: int, column: str) -> int | float:
    if not _NUMBER.fullmatch(value):
        raise ValueError(f"CSV row {row}, column {column}: require explicit finite numeric value; missing cells are not converted")
    result = float(value) if any(ch in value for ch in ".eE") else int(value)
    number(result)
    if result == 0 and Decimal(value) != 0:
        raise ValueError(f"CSV row {row}, column {column}: numeric underflow")
    return result


def _adapt_csv(raw: bytes, mapping: dict) -> tuple[dict, dict]:
    from .control_plane import ObservationBus
    _bounded(raw)
    mapping = _mapping(mapping)
    try:
        source = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("CSV adapter requires UTF-8 bytes") from exc
    reader = csv.reader(io.StringIO(source, newline=""), strict=True)
    try:
        columns = next(reader, None)
        if columns is None or len(columns) > 65:
            raise ValueError("CSV requires a bounded explicit header")
        for name in columns:
            text(name)
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate CSV header column")
        declared = {mapping["clock"]["column"]} | {item["column"] for item in mapping["quantities"]}
        if set(columns) != declared:
            raise ValueError("Every CSV column must be accounted for exactly once; missing/unaccounted columns are refused")
        positions = {name: index for index, name in enumerate(columns)}
        bus = ObservationBus(MAX_SAMPLES)
        provenance = {**mapping["provenance"], "sources": [bytes_ref(raw)]}
        row_count = 0
        for row_count, row in enumerate(reader, 1):
            if len(row) != len(columns):
                raise ValueError(f"CSV row {row_count}: column count differs from header; no row was skipped")
            if row_count * len(mapping["quantities"]) > MAX_SAMPLES:
                raise ValueError("CSV exceeds observation capacity; no row was dropped")
            values = {name: _csv_number(row[index], row_count, name) for name, index in positions.items()}
            for item in mapping["quantities"]:
                bus.publish(observation(identity=mapping["identity"],
                    clock={"id": mapping["clock"]["id"], "time_s": values[mapping["clock"]["column"]]},
                    frame=mapping["frame"], quantity=item["quantity"], value=values[item["column"]],
                    unit=item["unit"], provenance=provenance, uncertainty=None))
    except csv.Error as exc:
        raise ValueError("Malformed CSV; no rows were silently recovered") from exc
    native = bus.snapshot()
    receipt = {"schema": RECEIPT_SCHEMA, "adapter": "ciw.csv-observation-stream.v1",
               "source_sha256": bytes_ref(raw), "target_sha256": bytes_ref(_encoded(native)),
               "source_columns": columns, "mapping": mapping, "input_row_count": row_count,
               "output_observation_count": len(native["observations"]),
               "observations_per_row": len(mapping["quantities"]),
               "row_policy": "all_rows_in_source_order", "missing_data_policy": "refuse_missing_cells",
               "unit_conversion": "not_performed", "interpolation": "not_performed",
               "physical_validation": "not_performed", "state_admission": "not_performed"}
    return native, receipt


def csv_observation_stream(raw: bytes, *, mapping: dict, dataset_id: str, revision: int,
                           parent_source_id: str | None, recorded_at: str) -> dict:
    """Explicit loss-accounted CSV → native scalar observations, without inference.

    Mapping keys: identity (model_id/entity_id/execution_id), clock
    (id/column/unit='s'), frame, provenance (provider/semantics), and quantities
    ([{column, quantity, unit}]). No columns may be ignored or multiply consumed.
    The replayable receipt is checked again whenever its envelope is loaded.
    """
    native, receipt = _adapt_csv(raw, mapping)
    return _make(native, raw, dataset_id=dataset_id, revision=revision,
                 parent_source_id=parent_source_id, recorded_at=recorded_at,
                 data_family="observation-stream", receipt=receipt)
