"""Prepare retained synthetic data consumers. Preparation does not run them.

The destination must not exist. Comparison, polymer assessment, product review,
toolpoint containment and glTF projection read exact retained records. None of
those results is a release.
"""
from __future__ import annotations

import argparse
import base64
import json
from copy import deepcopy
from pathlib import Path

from ciw.control_contracts import save_new
from ciw.control_plane import experiment
from ciw.data_manufacturing_bridge import REVIEW as TOOLPOINT
from ciw.data_manufacturing_bridge import review_parameters
from ciw.data_sources import csv_observation_stream, retain_record
from ciw.data_workflow import COMPARE, comparison_parameters
from ciw.gltf_reference import make_record
from ciw.gltf_reference_runtime import PROJECT, source_parameters as gltf_parameters
from ciw.instruments import make_demo_run
from ciw.manufacturing_runtime import evaluate as manufacture
from ciw.polymer_quality_example import manufacturing_request, source as quality_source
from ciw.product_study_workflow import REVIEW as PRODUCT
from ciw.product_study_workflow import parameters as product_parameters
from ciw.telemetry import canonical
from ciw.toolpoint_stream_example import demo
from ciw.toolpoint_stream_store import StreamStore, initialize, MAX_DATABASE_BYTES
import ciw.toolpoint_stream_store as toolpoint_store
from ciw.workbench import Workbench
import ciw.product_study as product
import sqlite3


def _connect(path, timeout_s):
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=timeout_s)
    try:
        if hasattr(db, "setlimit"):
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=EXTRA")
        db.execute("PRAGMA busy_timeout=" + str(int(timeout_s * 1000)))
        if (db.execute("PRAGMA foreign_keys").fetchone()[0] != 1
                or db.execute("PRAGMA synchronous").fetchone()[0] != 3
                or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
            raise ValueError("Required rollback-journal durability settings unavailable")
        pages = MAX_DATABASE_BYTES // db.execute("PRAGMA page_size").fetchone()[0]
        if db.execute(f"PRAGMA max_page_count={pages}").fetchone()[0] > pages:
            raise ValueError("QC database exceeds the configured page budget")
        return db
    except BaseException:
        db.close()
        raise


toolpoint_store._connect = _connect


STAMP = "2026-10-05T21:00:00+00:00"


def _add(workbench, raw, label):
    encoded = base64.b64encode(raw).decode()
    descriptor = workbench.add_source({"kind": "data-record", "label": label, "bytes_b64": encoded})
    return workbench.get_source(descriptor["source_id"])


def _csv(dataset, value="300"):
    mapping = {
        "identity": {"model_id": "temperature-test", "entity_id": "part-1", "execution_id": None},
        "clock": {"id": "test-clock", "column": "time", "unit": "s"},
        "frame": "specimen",
        "provenance": {"provider": "recorded-test", "semantics": "observed"},
        "quantities": [{"column": "temperature", "quantity": "temperature", "unit": "K"}],
    }
    return csv_observation_stream(
        f"time,temperature\n1,{value}\n2,301\n".encode(),
        mapping=mapping, dataset_id=dataset, revision=1, parent_source_id=None,
        recorded_at="2026-10-05T19:00:00+00:00",
    )


def _product(variant="synthetic-bracket-A", prediction=9.0):
    def ref(artifact_id, *path):
        return {"artifact_id": artifact_id, "path": list(path)}

    objects = {
        "requirements": {"revision": "synthetic-R1", "items": [
            {"id": "mass", "text": "Synthetic mass screen", "limit": 10.0, "unit": "g"},
            {"id": "durability", "text": "Physical durability test pending"}]},
        "design": {"product_id": "synthetic-bracket", "revision": variant, "nominal_height_mm": 20},
        "material": {"grade_id": "fictional-grade", "density": 1.0},
        "process": {"route": "fictional_process_review_only"},
        "calculation": {"mass_upper": prediction},
    }
    artifacts = [product.artifact(json.dumps(value).encode(), artifact_id=name, role=name, media_type="application/json")
                 for name, value in objects.items()]
    artifacts.append(product.artifact(b"SYNTHETIC STEP-LABELED BYTES; NOT REAL CAD", artifact_id="cad", role="cad", media_type="model/step"))
    return {
        "schema": product.SCHEMA,
        "identity": {"study_id": "synthetic-test", "variant_id": variant, "product_id": "synthetic-bracket",
                     "design_revision": variant, "requirements_revision": "synthetic-R1"},
        "identity_refs": {"product_id": ref("design", "product_id"), "design_revision": ref("design", "revision"),
                          "requirements_revision": ref("requirements", "revision")},
        "requirements_inventory_ref": ref("requirements", "items"),
        "provenance": {"bundle_sha256": "sha256:" + "1" * 64, "retention_scope": "selected_artifacts_only",
                       "distribution": "synthetic_fixture", "source_authentication": "not_established"},
        "artifacts": artifacts,
        "requirements": [
            {"requirement_id": "mass", "statement_ref": ref("requirements", "items", 0),
             "requirement_id_ref": ref("requirements", "items", 0, "id"),
             "screen": {"metric": "mass", "unit": "g", "unit_ref": ref("requirements", "items", 0, "unit"),
                        "relation": "le", "limit": 10.0, "limit_ref": ref("requirements", "items", 0, "limit"),
                        "limit_origin": "retained_numeric_requirement", "prediction_ref": ref("calculation", "mass_upper"),
                        "prediction_unit": "g", "material_dependencies": ["density"]}},
            {"requirement_id": "durability", "statement_ref": ref("requirements", "items", 1),
             "requirement_id_ref": ref("requirements", "items", 1, "id"), "screen": None},
        ],
        "material": {"grade_id": "fictional-grade", "grade_ref": ref("material", "grade_id"), "lot_id": None,
                     "lot_ref": None, "source_document_ids": [],
                     "properties": [{"property_id": "density", "value": 1.0, "unit": "g/cm^3",
                                     "origin": "supplier_typical_transcription", "source_ref": ref("material", "density"),
                                     "uncertainty": None}]},
        "process": {"family": "fictional_process", "review_ref": ref("process"), "production_recipe": None},
        "physical_evidence": {"calibration_artifact_ids": [], "measurement_artifact_ids": []},
        "open_gates": ["Physical tests remain pending"],
        "limitations": ["Synthetic fixture; no engineering claim"],
        "authority": deepcopy(product.AUTHORITY),
    }


def _gltf():
    import struct
    positions = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]] * 2
    binary = bytearray(b"\0" * 16)
    for point in positions:
        binary.extend(struct.pack("<8f", 17.0, *point, 0.0, 0.0, 1.0, 99.0))
    start = len(binary)
    binary.extend(b"\0" * 4)
    indices = [0, 1, 2, 3, 2, 1, 4, 5, 6, 7, 6, 5]
    binary.extend(struct.pack("<" + "H" * len(indices), *indices))
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"translation": [2, 3, 4], "children": [1]}, {"mesh": 0, "scale": [2, 3, 1]}],
        "meshes": [{"name": "SyntheticSeams", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
        "buffers": [{"uri": "mesh.bin", "byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 16, "byteLength": 32 * 8, "byteStride": 32},
                        {"buffer": 0, "byteOffset": start, "byteLength": len(binary) - start}],
        "accessors": [
            {"bufferView": 0, "byteOffset": 4, "componentType": 5126, "type": "VEC3", "count": 8, "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 0, "byteOffset": 16, "componentType": 5126, "type": "VEC3", "count": 8},
            {"bufferView": 1, "byteOffset": 4, "componentType": 5123, "type": "SCALAR", "count": 12},
        ],
    }
    return make_record(canonical(document), {"mesh.bin": bytes(binary)}, provenance={
        "source": "independent synthetic two-seam-patch test", "revision": "1",
        "license": "synthetic fixture", "attribution": "NET synthetic fixture"})


def _toolpoint(workbench, directory: Path):
    request = manufacturing_request()
    request["events"].append({
        "event_id": "synthetic.data-postpack-hold", "at_s": 11, "actor_id": "synthetic.inspector",
        "authorization_ref": quality_source("inspector"), "kind": "quality_hold",
        "data": {"serial": "p001", "reason": "Synthetic post-pack concern", "evidence_ref": quality_source("concern")},
    })
    report = manufacture(request)
    manufacturing = _add(workbench, json.dumps(retain_record(
        report, dataset_id="manufacturing", revision=1, parent_source_id=None,
        recorded_at=STAMP, data_family="manufacturing-report"), indent=2).encode(), "manufacturing")
    database = directory / "native.sqlite3"
    initialize(database)
    streams = []
    for index, case in enumerate(("health-recovery", "nominal")):
        journal = demo(case=case, cycle_index=index)
        spec, head = journal["spec_ref"], journal["receipts"][-1]["receipt_ref"]
        StreamStore(database).import_report(str(index), journal, expected_spec_ref=spec, expected_head_ref=head)
        exported = StreamStore(database).export(str(index), expected_spec_ref=spec, expected_head_ref=head)
        streams.append(_add(workbench, json.dumps(retain_record(
            exported, dataset_id="cycle-" + str(index), revision=1, parent_source_id=None,
            recorded_at=STAMP, data_family="toolpoint-stream-report"), indent=2).encode(), "cycle-" + str(index)))
    return review_parameters(
        workbench, manufacturing_source_id=manufacturing["source_id"],
        stream_source_ids=[item["source_id"] for item in streams], assessed_at_s=12,
    )


def prepare(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    workbench = Workbench()
    left = _add(workbench, json.dumps(_csv("sensor")).encode(), "readings")
    right = _add(workbench, json.dumps(_csv("reference")).encode(), "reference")
    compare = comparison_parameters(workbench, left_source_id=left["source_id"], right_source_id=right["source_id"],
                                    quantity="temperature", atol=0.0)
    study = _add(workbench, json.dumps(retain_record(
        _product(), dataset_id="synthetic-bracket-A", revision=1, parent_source_id=None,
        recorded_at="2026-10-05T20:00:00+00:00", data_family="product-study")).encode(), "synthetic product study")
    mesh = _add(workbench, canonical(retain_record(
        _gltf(), dataset_id="synthetic-seams", revision=1, parent_source_id=None,
        recorded_at="2026-10-05T22:00:00+00:00", data_family="gltf-reference")), "seam reference")
    graph = experiment(
        "agent-data-consumers",
        model_id="retained-data-records.v1",
        nodes=[
            {"node_id": "compare", "operation_id": COMPARE, "parameters": compare, "inputs": {}, "depends_on": []},
            {"node_id": "product", "operation_id": PRODUCT, "parameters": product_parameters(workbench, study["source_id"]),
             "inputs": {}, "depends_on": []},
            {"node_id": "mesh", "operation_id": PROJECT,
             "parameters": gltf_parameters(workbench, mesh["source_id"], source_vertex=0, target_vertex=3),
             "inputs": {}, "depends_on": []},
        ],
    )
    save_new(destination / "source.json", make_demo_run())
    save_new(destination / "experiment.json", graph)
    save_new(destination / "profile.json", {
        "schema": "ciw.agent-host-profile.v1",
        "inputs": {"source": "source.json", "baseline": "experiment.json"},
        "output_dir": "agent-output",
        "allow_operations": [COMPARE, PRODUCT, PROJECT],
        "candidate_domains": {},
        "comparisons": {},
        "check_suites": {},
        "max_executions": 4,
        "max_nodes": 8,
    })
    return destination / "profile.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(prepare(parser.parse_args().output_dir.resolve()))
