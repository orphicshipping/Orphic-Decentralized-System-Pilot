"""Data-only preliminary product studies in the existing native data envelope.

The operator explicitly maps requirements and calculated values to retained JSON
artifacts. This module does not load delivered code, fetch suppliers, regenerate
CAD, interpret text as numbers, qualify a material, or approve manufacturing.
Original private deliveries remain operator-local; public tests use new fixtures.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy

from .adapters.subprocess import _json
from .control_contracts import bytes_ref, content_ref, detached, keys, number, text
from .core.identities import content_identity

SCHEMA = "ciw.product-study.v1"
MAX_ARTIFACT_BYTES = 48 * 1024
MAX_ARTIFACTS = 64
AUTHORITY = {"machine_commands": False, "part_release": False,
             "state_admission": False, "physical_qualification": False}
ROLES = frozenset({"requirements", "design", "material", "process", "cad",
                   "calculation", "verification", "supplier_document", "calibration", "measurement"})


def _items(value, label, maximum=128, nonempty=False):
    if type(value) is not list or not (1 if nonempty else 0) <= len(value) <= maximum:
        raise ValueError(f"{label} requires a bounded list")
    return value


def _texts(value, label, nonempty=False):
    _items(value, label, nonempty=nonempty)
    for item in value:
        text(item)
    if len(set(value)) != len(value):
        raise ValueError(f"Duplicate {label}")
    return value


def artifact(raw: bytes, *, artifact_id: str, role: str, media_type: str) -> dict:
    """Retain small source/STEP artifacts exactly; never execute their contents."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_ARTIFACT_BYTES:
        raise ValueError("Product artifact exceeds its 48 KiB exact-byte budget")
    value = {"artifact_id": artifact_id, "role": role, "media_type": media_type,
             "bytes_b64": base64.b64encode(raw).decode("ascii"),
             "sha256": bytes_ref(raw), "byte_count": len(raw)}
    _artifact(value)
    return value


def _artifact(value):
    keys(value, {"artifact_id", "role", "media_type", "bytes_b64", "sha256", "byte_count"})
    text(value["artifact_id"])
    if text(value["role"]) not in ROLES:
        raise ValueError("Unknown product artifact role")
    if text(value["media_type"]) not in {"application/json", "model/step", "text/plain", "application/pdf"}:
        raise ValueError("Unsupported product artifact media type")
    encoded = value["bytes_b64"]
    if type(encoded) is not str or len(encoded) > 65536:
        raise ValueError("Product artifact encoding exceeds budget")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Product artifact requires canonical base64") from exc
    if (not 0 < len(raw) <= MAX_ARTIFACT_BYTES or base64.b64encode(raw).decode() != encoded
            or type(value["byte_count"]) is not int or value["byte_count"] != len(raw)
            or content_ref(value["sha256"]) != bytes_ref(raw)):
        raise ValueError("Product artifact byte identity mismatch")
    if value["media_type"] == "application/json":
        detached(_json(raw))
    return raw


def _reference(ref, artifacts, roles):
    keys(ref, {"artifact_id", "path"})
    item = artifacts.get(text(ref["artifact_id"]))
    if item is None or item["role"] not in roles or item["media_type"] != "application/json":
        raise ValueError("Product reference requires a retained JSON artifact of the correct role")
    _items(ref["path"], "JSON path", maximum=16)
    value = _json(base64.b64decode(item["bytes_b64"], validate=True))
    for step in ref["path"]:
        if type(value) is dict and type(step) is str and step in value:
            value = value[step]
        elif type(value) is list and type(step) is int and 0 <= step < len(value):
            value = value[step]
        else:
            raise ValueError("Product JSON path is missing or ambiguous")
    return value


def validate(value: dict) -> dict:
    value = detached(value)
    keys(value, {"schema", "identity", "identity_refs", "requirements_inventory_ref", "provenance", "artifacts", "requirements", "material",
                 "process", "physical_evidence", "open_gates", "limitations", "authority"})
    keys(value["authority"], set(AUTHORITY))
    if value["schema"] != SCHEMA or any(flag is not False for flag in value["authority"].values()):
        raise ValueError("Product study schema or non-actuating authority differs")
    keys(value["identity"], {"study_id", "variant_id", "product_id", "design_revision", "requirements_revision"})
    for item in value["identity"].values():
        text(item)
    provenance = value["provenance"]
    keys(provenance, {"bundle_sha256", "retention_scope", "distribution", "source_authentication"})
    content_ref(provenance["bundle_sha256"])
    if (provenance["retention_scope"] != "selected_artifacts_only"
            or text(provenance["distribution"]) not in {"private_local", "synthetic_fixture"}
            or provenance["source_authentication"] != "not_established"):
        raise ValueError("Product provenance cannot imply authenticated or fully retained delivery")
    artifacts = {}
    for item in _items(value["artifacts"], "artifacts", MAX_ARTIFACTS, nonempty=True):
        _artifact(item)
        if item["artifact_id"] in artifacts:
            raise ValueError("Duplicate product artifact identity")
        artifacts[item["artifact_id"]] = item
    roles = {item["role"] for item in artifacts.values()}
    if not {"requirements", "design", "material", "process", "cad", "calculation"} <= roles:
        raise ValueError("Study needs distinct requirement, design, material, process, CAD and calculation artifacts")
    if not any(a["role"] == "cad" and a["media_type"] == "model/step" for a in artifacts.values()):
        raise ValueError("Study needs exact retained STEP identity")
    keys(value["identity_refs"], {"product_id", "design_revision", "requirements_revision"})
    for field, ref in value["identity_refs"].items():
        declared = _reference(ref, artifacts, {"requirements"} if field == "requirements_revision" else {"design"})
        if type(declared) is not str or declared != value["identity"][field]:
            raise ValueError("Product/design/requirement identity differs from retained source")
    inventory_ref = value["requirements_inventory_ref"]
    inventory = _reference(inventory_ref, artifacts, {"requirements"})
    if type(inventory) is not list or not inventory:
        raise ValueError("Requirement inventory must be the complete retained requirement list")
    material = value["material"]
    keys(material, {"grade_id", "grade_ref", "lot_id", "lot_ref", "source_document_ids", "properties"})
    text(material["grade_id"])
    if _reference(material["grade_ref"], artifacts, {"material"}) != material["grade_id"]:
        raise ValueError("Material grade differs from the retained source identity")
    if material["lot_ref"] is None:
        if material["lot_id"] is not None:
            raise ValueError("A material lot cannot be invented without its retained source")
    elif _reference(material["lot_ref"], artifacts, {"material"}) != material["lot_id"]:
        raise ValueError("Material lot differs from the retained source")
    if material["lot_id"] is not None:
        text(material["lot_id"])
    properties = {}
    for item in _items(material["properties"], "material properties", nonempty=True):
        keys(item, {"property_id", "value", "unit", "origin", "source_ref", "uncertainty"})
        name = text(item["property_id"])
        text(item["unit"])
        if name in properties:
            raise ValueError("Duplicate material property")
        properties[name] = item
        if text(item["origin"]) not in {"supplier_typical_transcription", "operator_assumption", "unknown"}:
            raise ValueError("Preliminary material data cannot claim measured or qualified properties")
        if item["value"] is not None:
            number(item["value"])
        if item["origin"] == "unknown" and item["value"] is not None:
            raise ValueError("Unknown material properties must remain null")
        if item["source_ref"] is not None:
            observed = _reference(item["source_ref"], artifacts, {"material"})
            if type(observed) not in (float, int) or observed != item["value"]:
                raise ValueError("Material property differs from its retained source value")
        elif item["origin"] == "supplier_typical_transcription":
            raise ValueError("Transcribed property needs its exact retained source reference")
        if item["uncertainty"] is not None:
            keys(item["uncertainty"], {"kind", "lower", "upper"})
            if item["uncertainty"]["kind"] != "declared_scenario_bounds":
                raise ValueError("Scenario bounds cannot claim statistical or qualified uncertainty")
            low, high = (number(item["uncertainty"][k]) for k in ("lower", "upper"))
            if item["value"] is None or not low <= item["value"] <= high:
                raise ValueError("Material scenario bounds exclude the declared value")
    seen, covered = set(), set()
    for req in _items(value["requirements"], "requirements", nonempty=True):
        keys(req, {"requirement_id", "requirement_id_ref", "statement_ref", "screen"})
        name = text(req["requirement_id"])
        if name in seen:
            raise ValueError("Duplicate requirement identity")
        seen.add(name)
        if _reference(req["requirement_id_ref"], artifacts, {"requirements"}) != name:
            raise ValueError("Requirement identity differs from its retained declaration")
        statement_ref = req["statement_ref"]
        path = statement_ref.get("path", [])
        if (statement_ref.get("artifact_id") != inventory_ref["artifact_id"]
                or path[:-1] != inventory_ref["path"] or not path
                or type(path[-1]) is not int or not 0 <= path[-1] < len(inventory)
                or path[-1] in covered
                or req["requirement_id_ref"]["artifact_id"] != statement_ref["artifact_id"]
                or req["requirement_id_ref"]["path"][:-1] != path):
            raise ValueError("Requirement must bind one distinct complete inventory entry and its identifier")
        covered.add(path[-1])
        statement = _reference(req["statement_ref"], artifacts, {"requirements"})
        if type(statement) not in (str, dict):
            raise ValueError("Requirement statement must remain retained text or a structured declaration")
        screen = req["screen"]
        if screen is None:
            continue
        keys(screen, {"metric", "unit", "unit_ref", "relation", "limit", "limit_ref", "limit_origin",
                      "prediction_ref", "prediction_unit", "material_dependencies"})
        text(screen["metric"]); text(screen["unit"])
        number(screen["limit"])
        if text(screen["relation"]) not in {"le", "ge"}:
            raise ValueError("Only explicit scalar lower/upper preliminary screens are supported")
        if screen["prediction_unit"] != screen["unit"]:
            raise ValueError("Prediction and requirement units differ; no unit conversion is performed")
        if screen["unit_ref"] is not None:
            if (_reference(screen["unit_ref"], artifacts, {"requirements"}) != screen["unit"]
                    or screen["unit_ref"]["artifact_id"] != statement_ref["artifact_id"]
                    or screen["unit_ref"]["path"][:-1] != statement_ref["path"]):
                raise ValueError("Unit differs from the same retained requirement")
        if screen["limit_ref"] is not None:
            if (screen["limit_ref"].get("artifact_id") != statement_ref["artifact_id"]
                    or screen["limit_ref"].get("path", [])[:-1] != statement_ref["path"]
                    or screen["unit_ref"] is None):
                raise ValueError("Numeric limit and unit must bind the same retained requirement")
            limit = _reference(screen["limit_ref"], artifacts, {"requirements"})
            if (screen["limit_origin"] != "retained_numeric_requirement"
                    or type(limit) not in (int, float) or limit != screen["limit"]):
                raise ValueError("Requirement limit differs from the retained numeric requirement")
        elif screen["limit_origin"] != "operator_transcription":
            raise ValueError("Unstructured requirement limits require explicit operator transcription")
        if screen["prediction_ref"] is not None:
            prediction = _reference(screen["prediction_ref"], artifacts, {"calculation"})
            if prediction is not None:
                number(prediction)
        for dep in _texts(screen["material_dependencies"], "material dependencies"):
            if dep not in properties:
                raise ValueError("Screen names an unknown material property")
    if len(covered) != len(inventory):
        raise ValueError("Incomplete requirement coverage; omitted requirements cannot disappear")
    process = value["process"]
    keys(process, {"family", "review_ref", "production_recipe"})
    text(process["family"])
    _reference(process["review_ref"], artifacts, {"process"})
    if process["production_recipe"] is not None:
        raise ValueError("Preliminary product studies cannot carry executable machine recipes")
    physical = value["physical_evidence"]
    keys(physical, {"calibration_artifact_ids", "measurement_artifact_ids"})
    for names, role in [(material["source_document_ids"], "supplier_document"),
                        (physical["calibration_artifact_ids"], "calibration"),
                        (physical["measurement_artifact_ids"], "measurement")]:
        for identity in _texts(names, role + " artifact ids"):
            if identity not in artifacts or artifacts[identity]["role"] != role:
                raise ValueError("Physical/source document reference lacks its exact retained artifact")
    _texts(value["open_gates"], "open gates", nonempty=True)
    _texts(value["limitations"], "limitations", nonempty=True)
    return value


def review(value: dict) -> dict:
    """Recompute scalar comparisons only; supplier facts and physics stay unverified."""
    value = validate(value)
    artifacts = {a["artifact_id"]: a for a in value["artifacts"]}
    props = {p["property_id"]: p for p in value["material"]["properties"]}
    rows = []
    for req in value["requirements"]:
        screen = req["screen"]
        prediction = None if screen is None or screen["prediction_ref"] is None else _reference(
            screen["prediction_ref"], artifacts, {"calculation"})
        missing = [] if screen is None else [name for name in screen["material_dependencies"] if props[name]["value"] is None]
        status = "NOT_SCREENED"
        if screen is not None:
            status = "INDETERMINATE" if prediction is None or missing else (
                "PASS_WITHIN_DECLARED_ASSUMPTIONS" if (
                    prediction <= screen["limit"] if screen["relation"] == "le" else prediction >= screen["limit"]
                ) else "FAIL")
        rows.append({"requirement_id": req["requirement_id"], "statement_ref": deepcopy(req["statement_ref"]),
                     "screen": deepcopy(screen), "prediction": prediction, "missing_material_properties": missing,
                     "screen_status": status, "physical_acceptance": "NOT_ESTABLISHED"})
    holds = ["requirements_owner_approval_not_established", "physical_qualification_not_performed",
             "source_authentication_not_established", "process_and_tool_qualification_not_performed"]
    if not value["material"]["source_document_ids"]:
        holds.append("original_supplier_source_bytes_missing")
    if value["material"]["lot_id"] is None:
        holds.append("material_lot_identity_missing")
    if any(p["value"] is None for p in props.values()):
        holds.append("material_properties_missing")
    if any(p["uncertainty"] is None for p in props.values()):
        holds.append("material_uncertainty_unspecified")
    # Even populated declarations are not accepted calibration or measurements.
    for field, prefix in [("calibration_artifact_ids", "calibration"), ("measurement_artifact_ids", "physical_measurement")]:
        holds.append(prefix + ("_not_qualified" if value["physical_evidence"][field] else "_missing"))
    return {"schema": "ciw.product-study-review.v1", "study_ref": content_identity(value),
            "identity": deepcopy(value["identity"]), "provenance": deepcopy(value["provenance"]),
            "artifact_identities": [{k: a[k] for k in ("artifact_id", "role", "sha256", "byte_count", "media_type")}
                                    for a in value["artifacts"]],
            "requirements": rows, "material": deepcopy(value["material"]),
            "process": deepcopy(value["process"]), "release_status": "BLOCKED",
            "holds": holds, "open_gates": deepcopy(value["open_gates"]),
            "limitations": deepcopy(value["limitations"]), "authority": deepcopy(AUTHORITY),
            "verification_scope": "exact_selected_bytes_and_scalar_comparison_only",
            "physics_reexecution": "not_performed", "cad_regeneration": "not_performed"}
