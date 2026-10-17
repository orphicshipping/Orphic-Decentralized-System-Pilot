"""New synthetic fixtures only; no private source, CAD or study payloads."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ciw import product_study as product
from ciw.core.identities import content_identity
from ciw.data_sources import retain_record
from ciw.instruments import make_demo_run
from ciw.operations.runner import seal
from ciw.product_study_workflow import REVIEW, parameters
from ciw.session import Session


def ref(artifact_id, *path):
    return {"artifact_id": artifact_id, "path": list(path)}


def fixture(variant="synthetic-bracket-A", prediction=9.0):
    objects = {
        "requirements": {"revision": "synthetic-R1", "items": [{"id":"mass", "text": "Synthetic mass screen", "limit": 10.0, "unit":"g"}, {"id":"durability", "text":"Physical durability test pending"}]},
        "design": {"product_id": "synthetic-bracket", "revision": variant, "nominal_height_mm": 20},
        "material": {"grade_id": "fictional-grade", "density": 1.0}, "process": {"route": "fictional_process_review_only"},
        "calculation": {"mass_upper": prediction},
    }
    artifacts = [product.artifact(json.dumps(value).encode(), artifact_id=name, role=name, media_type="application/json")
                 for name, value in objects.items()]
    artifacts.append(product.artifact(b"SYNTHETIC STEP-LABELED BYTES; NOT REAL CAD", artifact_id="cad", role="cad", media_type="model/step"))
    return {"schema": product.SCHEMA,
        "identity": {"study_id": "synthetic-test", "variant_id": variant, "product_id": "synthetic-bracket",
                     "design_revision": variant, "requirements_revision": "synthetic-R1"},
        "identity_refs": {"product_id": ref("design", "product_id"), "design_revision": ref("design", "revision"),
                          "requirements_revision": ref("requirements", "revision")},
        "requirements_inventory_ref": ref("requirements", "items"),
        "provenance": {"bundle_sha256": "sha256:" + "1"*64, "retention_scope": "selected_artifacts_only",
                       "distribution": "synthetic_fixture", "source_authentication": "not_established"},
        "artifacts": artifacts,
        "requirements": [{"requirement_id": "mass", "statement_ref": ref("requirements", "items", 0), "requirement_id_ref": ref("requirements", "items", 0, "id"),
            "screen": {"metric": "mass", "unit": "g", "unit_ref": ref("requirements", "items", 0, "unit"), "relation": "le", "limit": 10.0,
                "limit_ref": ref("requirements", "items", 0, "limit"), "limit_origin": "retained_numeric_requirement",
                "prediction_ref": ref("calculation", "mass_upper"), "prediction_unit": "g", "material_dependencies": ["density"]}},
            {"requirement_id": "durability", "statement_ref": ref("requirements", "items", 1), "requirement_id_ref": ref("requirements", "items", 1, "id"), "screen": None}],
        "material": {"grade_id": "fictional-grade", "grade_ref": ref("material", "grade_id"), "lot_id": None, "lot_ref": None, "source_document_ids": [],
            "properties": [{"property_id": "density", "value": 1.0, "unit": "g/cm^3", "origin": "supplier_typical_transcription",
                "source_ref": ref("material", "density"), "uncertainty": None}]},
        "process": {"family": "fictional_process", "review_ref": ref("process"), "production_recipe": None},
        "physical_evidence": {"calibration_artifact_ids": [], "measurement_artifact_ids": []},
        "open_gates": ["Physical tests remain pending"], "limitations": ["Synthetic fixture; no engineering claim"],
        "authority": deepcopy(product.AUTHORITY)}


def envelope(value, revision=1, parent=None):
    return retain_record(value, dataset_id=value["identity"]["variant_id"], revision=revision,
        parent_source_id=parent, recorded_at="2026-10-05T20:00:00Z", data_family="product-study")


def add(session, value):
    return session.workbench.add_source({"kind": "data-record", "label": "synthetic product study",
        "bytes_b64": base64.b64encode(json.dumps(value).encode()).decode()})


def call(session, kind, payload):
    response = session.handle({"protocol_version": 1, "request_id": "study-test", "type": kind, "payload": payload})
    assert response["type"] == "response", response
    return response["payload"]


def execute(session, source):
    return call(session, "operation.execute", {"operation_id": REVIEW, "parameters": parameters(session.workbench, source["source_id"])})


def test_scalar_review_retains_holds_and_missing_measurements():
    result = product.review(fixture())
    assert result["requirements"][0]["screen_status"] == "PASS_WITHIN_DECLARED_ASSUMPTIONS"
    assert result["requirements"][1]["screen_status"] == "NOT_SCREENED"
    assert result["release_status"] == "BLOCKED"
    assert {"original_supplier_source_bytes_missing", "calibration_missing", "physical_measurement_missing",
            "material_lot_identity_missing", "material_uncertainty_unspecified"} <= set(result["holds"])
    assert result["authority"] == product.AUTHORITY
    assert result["physics_reexecution"] == result["cad_regeneration"] == "not_performed"


def test_variants_remain_independent_in_same_workspace_and_replay(tmp_path):
    session = Session(make_demo_run(), tmp_path / "live")
    a = add(session, envelope(fixture()))
    b = add(session, envelope(fixture("synthetic-bracket-B", 11.0)))
    first, second = execute(session, a), execute(session, b)
    assert first["status"] == second["status"] == "completed"
    assert first["result"]["data"]["requirements"][0]["screen_status"] == "PASS_WITHIN_DECLARED_ASSUMPTIONS"
    assert second["result"]["data"]["requirements"][0]["screen_status"] == "FAIL"
    assert first["result"]["data"]["study_ref"] != second["result"]["data"]["study_ref"]
    path = session.save_workspace(tmp_path / "workspace.json")
    reopened = Session.from_workspace(path, output_dir=tmp_path / "reopened")
    assert reopened.results == session.results
    replay = execute(reopened, a)
    assert replay["result"]["data"] == first["result"]["data"]
    assert replay["execution"]["execution_id"] != first["execution"]["execution_id"]
    assert replay["result"]["result_id"] != first["result"]["result_id"]
    assert replay["result"]["verification_status"] == "not_verified"


def test_missing_material_property_stays_unknown():
    value = fixture()
    prop = value["material"]["properties"][0]
    prop.update(value=None, origin="unknown", source_ref=None)
    report = product.review(value)
    assert report["requirements"][0]["screen_status"] == "INDETERMINATE"
    assert report["requirements"][0]["missing_material_properties"] == ["density"]
    assert "material_properties_missing" in report["holds"]


def test_missing_prediction_is_not_zero():
    value = fixture()
    value["requirements"][0]["screen"]["prediction_ref"] = None
    report = product.review(value)
    assert report["requirements"][0]["prediction"] is None
    assert report["requirements"][0]["screen_status"] == "INDETERMINATE"


@pytest.mark.parametrize("mutation", [
    lambda v: v["authority"].update(part_release=True),
    lambda v: v["authority"].update(part_release=0),
    lambda v: v["material"].update(grade_id="another-grade"),
    lambda v: v["material"].update(lot_id="invented-lot"),
    lambda v: v["requirements"][0]["screen"].update(unit_ref=None),
    lambda v: v["requirements"][0]["screen"].update(unit="kg", prediction_unit="kg"),
    lambda v: v["requirements"][0]["screen"].update(limit_ref=ref("requirements", "items", 1, "limit")),
    lambda v: v["provenance"].update(source_authentication="verified"),
    lambda v: v["provenance"].update(distribution="public_release"),
    lambda v: v["artifacts"][0].update(sha256="sha256:"+"f"*64),
    lambda v: v["artifacts"][0].update(bytes_b64="%%%"),
    lambda v: v["artifacts"][0].update(byte_count=True),
    lambda v: v["artifacts"].append(deepcopy(v["artifacts"][0])),
    lambda v: v["artifacts"].pop(),
    lambda v: v["requirements"].append(deepcopy(v["requirements"][0])),
    lambda v: v["requirements"][0]["screen"].update(prediction_unit="kg"),
    lambda v: v["requirements"][0]["screen"].update(limit=999),
    lambda v: v["requirements"][0]["screen"].update(limit=True),
    lambda v: v["requirements"][0]["screen"].update(limit=float("nan")),
    lambda v: v["requirements"][0]["screen"].update(prediction_ref=ref("calculation", "absent")),
    lambda v: v["requirements"][0]["screen"].update(prediction_ref=ref("material", "density")),
    lambda v: v["requirements"][0]["screen"].update(material_dependencies=["unknown"]),
    lambda v: v["material"]["properties"][0].update(value=2),
    lambda v: v["material"]["properties"][0].update(origin="measured"),
    lambda v: v["material"]["properties"][0].update(uncertainty={"kind":"confidence_interval", "lower":0, "upper":2}),
    lambda v: v["material"].update(source_document_ids=["material"]),
    lambda v: v["physical_evidence"].update(calibration_artifact_ids=["absent"]),
    lambda v: v["physical_evidence"].update(measurement_artifact_ids=["calculation"]),
    lambda v: v["process"].update(production_recipe={"temperature":200}),
    lambda v: v["identity"].update(design_revision="swapped"),
    lambda v: v["requirements"].pop(),
    lambda v: v["requirements"][0].update(requirement_id="invented"),
    lambda v: v["requirements"][0].update(statement_ref=ref("requirements", "items", 1)),
    lambda v: v.update(open_gates=[]),
    lambda v: v.update(limitations=[]),
])
def test_negative_contracts_refuse(mutation):
    value = fixture()
    mutation(value)
    with pytest.raises((ValueError, TypeError)):
        product.validate(value)


def test_artifact_size_budget_and_byte_identity():
    with pytest.raises(ValueError):
        product.artifact(b"x" * (product.MAX_ARTIFACT_BYTES + 1), artifact_id="big", role="cad", media_type="model/step")
    value = fixture()
    before = content_identity(value)
    value["artifacts"][-1] = product.artifact(b"CHANGED SYNTHETIC STEP", artifact_id="cad", role="cad", media_type="model/step")
    assert product.review(value)["study_ref"] != before


def test_unretained_source_records_refused_occurrence(tmp_path):
    other = Session(make_demo_run(), tmp_path / "other")
    source = add(other, envelope(fixture()))
    session = Session(make_demo_run(), tmp_path / "empty")
    result = call(session, "operation.execute", {"operation_id": REVIEW, "parameters": parameters(other.workbench, source["source_id"])})
    assert result["status"] == "refused" and result["result"] is None
    assert len(session.executions) == 1
    assert not session.results


def test_variant_cannot_be_relabelled_as_another_dataset(tmp_path):
    session = Session(make_demo_run(), tmp_path)
    value = fixture()
    wrong = retain_record(value, dataset_id="another-variant", revision=1, parent_source_id=None,
                          recorded_at="2026-10-05T20:00:00Z", data_family="product-study")
    result = execute(session, add(session, wrong))
    assert result["status"] == "refused"


def test_accepted_correction_stales_only_its_variant(tmp_path):
    session = Session(make_demo_run(), tmp_path / "live")
    original = envelope(fixture())
    old = add(session, original)
    unrelated = add(session, envelope(fixture("synthetic-bracket-B")))
    old_result, unrelated_result = execute(session, old)["result"], execute(session, unrelated)["result"]
    new = add(session, envelope(fixture(prediction=11), revision=2, parent=original["source_id"]))
    proposal = call(session, "correction.propose", {"old_source_id": old["source_id"], "new_source_id": new["source_id"],
        "kind": "source_data", "reason": "new explicit calculation evidence"})
    call(session, "correction.review", {"correction_id": proposal["correction_id"], "decision":"accept",
        "expected_revision": session.correction_journal.revision, "reviewer":"operator", "reason":"reviewed"})
    status = session.dependency_status()["artifact_status"]
    assert status[old_result["result_id"]]["status"] == "stale"
    assert status[unrelated_result["result_id"]]["status"] == "current"
    assert execute(session, new)["result"]["data"]["requirements"][0]["screen_status"] == "FAIL"
    reopened = Session.from_workspace(session.save_workspace(tmp_path / "saved.json"), output_dir=tmp_path / "reopen")
    assert reopened.dependency_status() == session.dependency_status()


@pytest.mark.parametrize("tamper", ["drop-source", "reseal-release"])
def test_reopen_rejects_removed_source_or_resealed_escalation(tmp_path, tamper):
    session = Session(make_demo_run(), tmp_path / "live")
    execute(session, add(session, envelope(fixture())))
    value = session._workspace()
    if tamper == "drop-source":
        value["workbench"]["sources"] = []
        value["workbench"]["revision"] -= 1
    else:
        result = value["results"][0]
        result["data"]["release_status"] = "RELEASED"
        value["results"][0] = seal({k:v for k,v in result.items() if k != "record_digest"})
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        Session.from_workspace(path, output_dir=tmp_path / "bad")


def test_cli_creates_fresh_replay_and_refuses_overwrite(tmp_path):
    session = Session(make_demo_run(), tmp_path / "live")
    source = add(session, envelope(fixture()))
    workspace = session.save_workspace(tmp_path / "in.json")
    output = tmp_path / "out.json"
    command = [sys.executable, "-m", "ciw.net", "product-study", "review", "--source-id", source["source_id"],
               "--workspace", str(workspace), "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    result = json.loads(first.stdout)
    assert result["result"]["data"]["release_status"] == "BLOCKED"
    before = output.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 2
    assert output.read_bytes() == before
    reopened = Session.from_workspace(output, output_dir=tmp_path / "reopened")
    assert len(reopened.results) == 1
