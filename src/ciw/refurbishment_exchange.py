"""Portable registry archives and exact-cost CSV handoff.

Archives retain source occurrences as declarations. Restoring replays commands
into a new registry with new occurrence identities; it is not event impersonation.
"""
from __future__ import annotations

import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unicodedata

from .operations.runner import digest
from .hardware_workflow import read_bytes
from .session import loads_json
from .refurbishment_report import publish_bytes

ARCHIVE_SCHEMA = "ciw.refurbishment-archive.v1"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def _encode(value):
    raw = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("Registry export exceeds the 64 MiB archive limit")
    return raw


def export_archive(store, destination: Path) -> dict:
    bundle = store.export_bundle()
    value = {"schema": ARCHIVE_SCHEMA, "bundle": bundle, "bundle_ref": digest(bundle),
             "authority": "operator_declared_history_not_authenticated_physical_evidence"}
    raw = _encode(value)
    publish_bytes(destination, raw)
    return {"status": "created", "archive_sha256": sha256(raw).hexdigest(),
            "source_registry_id": bundle["registry_id"], "events": len(bundle["events"]),
            "assets": len(bundle["assets"]), "output": str(destination)}


def import_archive(source: Path, destination: Path) -> dict:
    """Validate/replay completely before exclusively publishing a new registry."""
    from .refurbishment_store import RefurbishmentStore, validate_export_bundle
    raw = read_bytes(source, limit=MAX_ARCHIVE_BYTES)
    value = loads_json(raw.decode("utf-8"))
    if (type(value) is not dict or set(value) != {"schema", "bundle", "bundle_ref", "authority"}
            or value["schema"] != ARCHIVE_SCHEMA
            or value["authority"] != "operator_declared_history_not_authenticated_physical_evidence"
            or digest(value["bundle"]) != value["bundle_ref"]):
        raise ValueError("Invalid registry archive or content reference")
    bundle = validate_export_bundle(value["bundle"])
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Registry import requires a new destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".net-registry-import-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "registry.sqlite3"
        with RefurbishmentStore.create(staged) as target:
            for event in bundle["events"]:
                target.execute(event["command"])
            reconstructed = target.export_bundle()
            if reconstructed["assets"] != bundle["assets"]:
                raise ValueError("Archive snapshots differ from replayed commands")
            registry_id = target.registry_id
            old_ids = {event["event_id"] for event in bundle["events"]}
            new_ids = {event["event_id"] for event in reconstructed["events"]}
            if (registry_id == bundle["registry_id"] or reconstructed["registry_id"] != registry_id
                    or len(new_ids) != len(bundle["events"]) or not old_ids.isdisjoint(new_ids)
                    or [event["command"] for event in reconstructed["events"]]
                    != [event["command"] for event in bundle["events"]]):
                raise ValueError("Archive restore did not produce fresh command replay occurrences")
            restored_bundle_ref = digest(reconstructed)
        # The store closes all SQLite handles and uses rollback journaling.
        os.link(staged, destination)
    return {"schema": "ciw.refurbishment-import.v1", "status": "created",
            "archive_sha256": sha256(raw).hexdigest(), "source_registry_id": bundle["registry_id"],
            "registry_id": registry_id, "events": len(bundle["events"]), "assets": len(bundle["assets"]),
            "output": str(destination), "occurrences": "fresh_command_replay",
            "source_bundle_ref": value["bundle_ref"], "restored_bundle_ref": restored_bundle_ref,
            "source_history_authentication": "not_established"}


def _cell(value):
    text = "" if value is None else str(value)
    # Quoting alone does not stop spreadsheet formula execution. Leading whitespace
    # and control characters must not hide an initial formula operator.
    prefix = 0
    while prefix < len(text) and (text[prefix].isspace()
                                  or unicodedata.category(text[prefix]) in {"Cc", "Cf"}):
        prefix += 1
    if (text[prefix:].startswith(("=", "+", "-", "@"))
            or text and unicodedata.category(text[0]) in {"Cc", "Cf"}):
        return "'" + text
    return text


def export_csv(store, destination: Path, *, kind: str = "inventory") -> dict:
    bundle = store.export_bundle()
    if kind == "inventory":
        columns = ["asset_id", "manufacturer", "model", "serial_number", "category", "location", "condition", "stage",
                   "revision", "source_kind", "configuration_ref", "current_release_id", "currency", "minor_unit_exponent",
                   "acquisition_cost_minor", "total_repair_cost_minor", "total_cost_minor"]
        records = [{**asset, **{key:asset["configuration"][key] for key in ("manufacturer", "model", "serial_number")}}
                   for asset in bundle["assets"]]
    elif kind == "costs":
        columns = ["asset_id", "work_order_id", "repair_id", "description", "currency", "minor_unit_exponent",
                   "parts_cost_minor", "labor_cost_minor", "other_cost_minor", "total_cost_minor"]
        records = []
        for asset in bundle["assets"]:
            for order in asset["work_orders"].values():
                for repair in order["repairs"]:
                    records.append({**repair, "asset_id":asset["asset_id"], "work_order_id":order["work_order_id"],
                        "currency":asset["currency"], "minor_unit_exponent":asset["minor_unit_exponent"],
                        "parts_cost_minor":sum(p["quantity"] * p["unit_cost_minor"] for p in repair["parts"])})
    else:
        raise ValueError("CSV kind must be inventory or costs")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(columns)
    for record in records:
        writer.writerow([_cell(record.get(column)) for column in columns])
    raw = stream.getvalue().encode("utf-8-sig")
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("CSV exceeds the 64 MiB export limit")
    publish_bytes(destination, raw)
    return {"status": "created", "kind": kind, "rows": len(records), "output": str(destination),
            "sha256": sha256(raw).hexdigest(), "money": "integer_minor_units_with_explicit_currency_and_exponent",
            "spreadsheet_formula_cells": "apostrophe_prefixed", "external_system_updated": False}
