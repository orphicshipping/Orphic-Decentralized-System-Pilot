"""Bind retained engineering calculations to the existing manufacturing reducer.

This is a dossier adapter, not a second state ledger, numerical verifier, MES,
or authority service. Manufacturing replay remains the disposition owner.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from . import manufacturing_runtime as runtime
from .core.identities import new_identity, validate_identity
from .manufacturing_contracts import bounded, content_identity as cid, items, keys, load, number, ref, save_new, text
from .polymer_microfab import AUTHORITY

SCHEMA = "ciw.study-manufacturing-request.v1"
REPORT = "ciw.study-manufacturing-report.v1"


def _unique(rows, key):
    result = {}
    for row in items(rows, maximum=128):
        if type(row) is not dict or key not in row:
            raise ValueError("Missing study record identity")
        identity = text(row[key])
        if identity in result:
            raise ValueError("Duplicate study record identity")
        result[identity] = row
    return result


def _same(left, right, reason):
    # Bind the entire declared value, including the number's JSON representation.
    if cid(left) != cid(right):
        raise ValueError(reason)


def _binding(request):
    study, assessment, bind, manufacturing = (request[k] for k in
        ("study", "assessment", "binding", "manufacturing"))
    keys(study, {"schema", "source_kind", "identity", "service", "components", "requirements",
                 "materials", "candidates", "interfaces"})
    if study["schema"] != "notation.polymer-engineering-study.v1":
        raise ValueError("Unsupported engineering study schema")
    # The upstream v1 design instrument currently declares synthetic material data.
    if study["source_kind"] != "synthetic":
        raise ValueError("This study format does not establish measured material data")
    keys(study["identity"], {"study_id", "product_id", "product_revision", "requirement_revision", "frame"})
    for value in study["identity"].values():
        text(value)
    keys(bind, {"study_ref", "assessment_ref", "candidate_id", "work_order_id", "part_ref",
                "configuration_ref", "inspection_links"})
    for field, obj in (("study_ref", study), ("assessment_ref", assessment),
                       ("configuration_ref", manufacturing["configuration"])):
        if ref(bind[field]) != cid(obj):
            raise ValueError("Stale study, assessment or configuration binding")
    components = _unique(study["components"], "component_id")
    candidates = _unique(study["candidates"], "candidate_id")
    materials = _unique(study["materials"], "material_id")
    requirements = _unique(study["requirements"], "requirement_id")
    selected = text(bind["candidate_id"])
    if selected not in candidates:
        raise ValueError("Unknown design candidate")
    candidate = candidates[selected]
    keys(candidate, {"candidate_id", "component_id", "geometry", "material_id", "process"})
    if candidate["component_id"] not in components or candidate["material_id"] not in materials:
        raise ValueError("Candidate component or material is absent")
    component, material = components[candidate["component_id"]], materials[candidate["material_id"]]
    keys(component, {"component_id", "function", "part_number", "part_revision", "quantity"})
    keys(assessment, {"schema", "study_digest", "numerics_digest", "candidates", "assembly_requirements", "authority"})
    if assessment["schema"] != "notation.polymer-engineering-assessment.v1" or assessment["study_digest"] != cid(study):
        raise ValueError("Assessment belongs to a different study")
    ref(assessment["numerics_digest"])
    assessed = _unique(assessment["candidates"], "candidate_id")
    if set(assessed) != set(candidates):
        raise ValueError("Assessment candidate set differs from the study")
    derived = assessed[selected]
    keys(derived, {"candidate_id", "component_id", "constraints", "geometry_digest", "material_digest",
                   "mechanics_applicable", "numerical_metrics", "process", "process_review_obligations",
                   "requirements", "status"})
    if derived["status"] not in {"SATISFIED", "VIOLATED", "UNKNOWN"}:
        raise ValueError("Unsupported retained calculation status")
    for obligation in _unique(derived["process_review_obligations"], "id").values():
        keys(obligation, {"id", "reason", "status"})
        text(obligation["reason"])
        if obligation["status"] != "UNKNOWN":
            raise ValueError("V1 process review does not establish engineering approval")
    for field, value in (("component_id", candidate["component_id"]), ("process", candidate["process"]),
                         ("geometry_digest", cid(candidate["geometry"])), ("material_digest", cid(material))):
        if derived.get(field) != value:
            raise ValueError("Assessment candidate binding mismatch")
    expected_authority = {"engineering_approval": "not_performed", "hardware_actuation": "not_performed",
        "material_qualification": "not_performed", "may_authorize": False, "physical_qualification": "not_performed",
        "production_release": "not_performed", "state_admission": "not_performed"}
    _same(assessment["authority"], expected_authority, "Engineering authority escalation")
    relevant = {}
    for name, requirement in requirements.items():
        keys(requirement, {"requirement_id", "subject", "metric", "relation", "limit", "unit", "evaluation",
                           "measurement_condition", "description", "method_ref"})
        if requirement["relation"] not in {"le", "ge"} or requirement["evaluation"] not in {
                "calculation", "physical_test", "document_review"}:
            raise ValueError("Unsupported requirement semantics")
        number(requirement["limit"], minimum=-1e50)
        for field in ("subject", "metric", "unit", "measurement_condition", "description", "method_ref"):
            text(requirement[field])
        if requirement["subject"] == candidate["component_id"]:
            relevant[name] = requirement
    if not relevant:
        raise ValueError("Selected component has no requirements")
    assessments = _unique(derived["requirements"], "requirement_id")
    if set(assessments) != set(relevant):
        raise ValueError("Assessment must retain every selected component requirement")
    for name, retained in assessments.items():
        keys(retained, {"limit", "margin", "metric", "physical_conformity", "reason", "relation", "requirement_id",
                        "status", "uncertainty", "unit", "value"})
        for field in ("limit", "metric", "relation", "unit"):
            _same(retained[field], relevant[name][field], "Assessment requirement declaration mismatch")
        if (retained["physical_conformity"] != "not_established"
                or retained["status"] not in {"SATISFIED", "VIOLATED", "UNKNOWN"}):
            raise ValueError("Unsupported requirement status or physical-conformity promotion")
    part_ref = ref(bind["part_ref"])
    parts = {cid(p): p for p in manufacturing["parts"]}
    orders = _unique(manufacturing["orders"], "work_order_id")
    oid = text(bind["work_order_id"])
    if part_ref not in parts or oid not in orders:
        raise ValueError("Missing bound part or work order")
    part, order, config = parts[part_ref], orders[oid], manufacturing["configuration"]
    if (part["part_number"], part["revision"]) != (component["part_number"], component["part_revision"]):
        raise ValueError("Part number or revision differs from the study")
    if part["bom"] or order["part_ref"] != part_ref or order["configuration_ref"] != cid(config):
        raise ValueError("Study binding requires the exact forming part and order")
    if set(part["requirement_refs"]) != {cid(r) for r in relevant.values()}:
        raise ValueError("Part must retain every selected component requirement")
    if (config["part_ref"] != part_ref or config["material"]["grade_id"] != material["material_id"]
            or config["material"]["specification_ref"] != cid(material)
            or config["recipe"]["process"] != candidate["process"]):
        raise ValueError("Material, process or part substitution")
    if cid(study) not in config["development_refs"] or cid(assessment) not in config["development_refs"]:
        raise ValueError("Configuration does not retain the engineering basis")
    matching = [s for s in manufacturing["development_runs"] if s["study_id"] == study["identity"]["study_id"]]
    if len(matching) != 1:
        raise ValueError("One exact development-study link is required")
    linked = matching[0]
    if (linked["configuration_ref"] != cid(config) or linked["hypothesis_ref"] != cid(study)
            or linked["model_ref"] != cid(candidate) or linked["result_ref"] != cid(assessment)
            or cid(study) not in linked["evidence_refs"]):
        raise ValueError("Development study reference substitution")
    for event in manufacturing["events"]:
        if event["kind"] == "cycle" and event["data"]["work_order_id"] == oid:
            if event["data"]["model_prediction_ref"] != cid(assessment):
                raise ValueError("Cycle is not linked to the retained design assessment")
    specs = {s["characteristic"]: s for s in part["inspection_plan"]}
    links = _unique(bind["inspection_links"], "characteristic")
    if set(links) != set(specs):
        raise ValueError("Every inspection characteristic needs an explicit study link")
    for characteristic, link in links.items():
        keys(link, {"characteristic", "kind", "target"})
        spec = specs[characteristic]
        if link["kind"] == "design_dimension":
            dimensions = candidate["geometry"].get("dimensions", {})
            if link["target"] not in dimensions or spec["unit"] != "m":
                raise ValueError("Unknown or unit-mismatched design dimension")
            nominal = number(dimensions[link["target"]], minimum=1e-50)
            if not spec["lower"] <= nominal <= spec["upper"]:
                raise ValueError("Declared tolerance excludes the design nominal")
            expected = cid({"geometry": candidate["geometry"], "dimension": link["target"]})
        elif link["kind"] == "requirement_support":
            if link["target"] not in relevant:
                raise ValueError("Inspection references another component's requirement")
            req = relevant[link["target"]]
            if characteristic != req["metric"] or spec["unit"] != req["unit"]:
                raise ValueError("Inspection metric or unit differs from its requirement")
            if ((req["relation"] == "le" and spec["upper"] > req["limit"])
                    or (req["relation"] == "ge" and spec["lower"] < req["limit"])):
                raise ValueError("Inspection plan is weaker than the linked requirement")
            expected = cid(req)
        else:
            raise ValueError("Unknown inspection link kind")
        if spec["specification_ref"] != expected:
            raise ValueError("Inspection specification binding mismatch")
    return candidate, derived, relevant, links


def _observations(request, manufacturing_result):
    """Resolve exact per-part samples and declared calibration validity locally."""
    manufacturing = request["manufacturing"]
    events = {e["event_id"]: e for e in manufacturing["events"]}
    expected = {(e["event_id"], row["characteristic"]): (e, row)
        for e in manufacturing["events"] if e["kind"] == "inspection"
        for row in e["data"]["measurements"]}
    seen, observation_ids = set(), set()
    for binding in items(request["inspection_evidence"], maximum=4096):
        keys(binding, {"event_id", "characteristic", "observation", "calibration"})
        key = (text(binding["event_id"]), text(binding["characteristic"]))
        if key in seen or key not in expected:
            raise ValueError("Duplicate or unbound inspection evidence")
        seen.add(key)
        event, reading = expected[key]
        obs, calibration = binding["observation"], binding["calibration"]
        if obs is None:
            if reading["evidence_ref"] is not None or reading["calibration_ref"] is not None or calibration is not None:
                raise ValueError("Missing observation cannot carry resolved references")
            continue
        keys(obs, {"schema", "observation_id", "source_kind", "serial", "part_ref", "material_lot_id", "cycle_id",
                   "cavity_id", "characteristic", "unit", "condition", "value", "expanded_uncertainty",
                   "instrument_id", "clock_id", "frame_id", "observed_at_s", "calibration_ref"})
        if obs["schema"] != "ciw.study-part-observation.v1":
            raise ValueError("Unsupported part observation schema")
        if text(obs["observation_id"]) in observation_ids:
            raise ValueError("An observation occurrence cannot be reused")
        observation_ids.add(obs["observation_id"])
        serial = event["data"]["serial"]
        instance = manufacturing_result["instances"][serial]
        genealogy = instance["genealogy"]
        if "cycle_id" not in genealogy:
            raise ValueError("V1 study observations cover formed parts, not assembly metrology")
        for field, value in (("serial", serial), ("part_ref", instance["part_ref"]),
                ("source_kind", manufacturing["source_kind"]), ("condition", event["data"]["condition"]),
                ("clock_id", manufacturing["clock_id"]), ("frame_id", request["study"]["identity"]["frame"])):
            _same(obs[field], value, "Observation identity, source, frame or condition mismatch")
        for field in ("material_lot_id", "cycle_id", "cavity_id"):
            _same(obs[field], genealogy[field], "Observation genealogy mismatch")
        for field in ("characteristic", "unit", "value", "expanded_uncertainty", "calibration_ref"):
            _same(obs[field], reading[field], "Retained sample differs from inspection reading")
        if reading["evidence_ref"] != cid(obs):
            raise ValueError("Observation content reference mismatch")
        text(obs["instrument_id"])
        stamp = number(obs["observed_at_s"])
        birth = next(e for e in events.values() if cid(e) == instance["birth_event_ref"])
        if not birth["at_s"] <= stamp <= event["at_s"]:
            raise ValueError("Observation predates part or postdates inspection")
        if calibration is None:
            if obs["calibration_ref"] is not None:
                raise ValueError("Unresolved calibration reference")
            continue
        keys(calibration, {"schema", "instrument_id", "source_kind", "unit", "clock_id", "frame_id",
                           "valid_from_s", "valid_until_s", "certificate_ref"})
        if calibration["schema"] != "ciw.study-calibration-declaration.v1" or obs["calibration_ref"] != cid(calibration):
            raise ValueError("Calibration content reference mismatch")
        for field in ("instrument_id", "source_kind", "unit", "clock_id", "frame_id"):
            _same(calibration[field], obs[field], "Calibration applicability mismatch")
        low, high = number(calibration["valid_from_s"]), number(calibration["valid_until_s"])
        if not low <= stamp <= high:
            raise ValueError("Calibration declaration is not valid at the observation time")
        ref(calibration["certificate_ref"])
    if seen != set(expected):
        raise ValueError("Every retained inspection reading needs an evidence binding, including missing evidence")


def replay(request):
    bounded(request)
    keys(request, {"schema", "study", "assessment", "binding", "manufacturing", "inspection_evidence"})
    if request["schema"] != SCHEMA:
        raise ValueError("Wrong study-manufacturing schema")
    # The existing reducer validates inventory, identities, ordering and dispositions.
    result = runtime.replay(request["manufacturing"])
    candidate, assessed, requirements, links = _binding(request)
    _observations(request, result)
    oid = request["binding"]["work_order_id"]
    serials = sorted(s for s, i in result["instances"].items() if i["work_order_id"] == oid)
    unresolved = [{"requirement_id": name, "evaluation": req["evaluation"],
        "measurement_condition": req["measurement_condition"], "status": "PHYSICAL_CONFORMITY_NOT_ESTABLISHED",
        "supporting_characteristics": sorted(k for k, link in links.items()
            if link["kind"] == "requirement_support" and link["target"] == name)}
        for name, req in requirements.items()]
    records, nonconformances = {}, []
    for serial in serials:
        instance = result["instances"][serial]
        inspection = instance["inspection"]
        record = {"serial": serial, "study_ref": request["binding"]["study_ref"],
            "assessment_ref": request["binding"]["assessment_ref"], "candidate_id": candidate["candidate_id"],
            "part_ref": instance["part_ref"], "work_order_id": oid, "genealogy": deepcopy(instance["genealogy"]),
            "inspection": deepcopy(inspection), "record_quality": instance["quality"],
            "record_disposition": instance["disposition"], "shipment_status": "HELD"}
        if "quality_holds" in instance:
            record["record_release_gate"] = deepcopy(result["quality_control"]["release_gates"][serial])
        records[serial] = record
        if inspection is not None and inspection["status"] != "PASS":
            issue = {"serial": serial, "inspection_event_ref": inspection["event_ref"],
                "classification": "NONCONFORMING" if inspection["status"] == "FAIL" else "INSUFFICIENT_EVIDENCE",
                "characteristics": deepcopy(inspection["characteristics"]),
                "record_disposition": instance["disposition"], "closure": "NOT_RELEASED"}
            nonconformances.append({"nonconformance_ref": cid(issue), **issue})
    output = {"schema": "ciw.study-manufacturing-replay.v1", "status": "RECORDED_NOT_RELEASED",
        "manufacturing_result": result, "study_records": records, "inspection_nonconformances": nonconformances,
        "evidence_semantics": {"design_source_kind": request["study"]["source_kind"],
            "manufacturing_source_kind": request["manufacturing"]["source_kind"],
            "acquisition_authenticity": "not_established", "calibration_authenticity": "not_established",
            "process_qc": "required_by_manufacturing_v2" if request["manufacturing"]["schema"] == runtime.QC_SCHEMA
                          else "not_required_by_manufacturing_v1"},
        "study_coverage": {"selected_component_requirements": unresolved,
            "other_requirement_ids": sorted(r["requirement_id"] for r in request["study"]["requirements"]
                if r["requirement_id"] not in requirements),
            "process_review_obligations": deepcopy(assessed["process_review_obligations"]),
            "calculation_status_as_retained": assessed["status"], "calculation_reexecuted": False,
            "assessment_authenticity": "not_established", "physical_conformity": "not_established"},
        "authority": dict(AUTHORITY)}
    bounded(output)
    return output


def evaluate(request):
    result = replay(request)
    report = {"schema": REPORT, "evaluation_id": new_identity("execution"), "request": deepcopy(request),
        "request_ref": cid(request), "result": result, "result_ref": cid(result)}
    bounded(report)
    return report


def verify(report):
    bounded(report)
    keys(report, {"schema", "evaluation_id", "request", "request_ref", "result", "result_ref"})
    if report["schema"] != REPORT:
        raise ValueError("Wrong study-manufacturing report schema")
    validate_identity(report["evaluation_id"], "execution")
    if report["request_ref"] != cid(report["request"]) or report["result_ref"] != cid(report["result"]):
        raise ValueError("Retained dossier content identity mismatch")
    if cid(replay(report["request"])) != report["result_ref"]:
        raise ValueError("Retained dossier differs from fresh replay")
    return {"schema": "ciw.study-manufacturing-verification.v1", "status": "PASS",
        "verification_id": new_identity("verification"), "evaluation_id": report["evaluation_id"],
        "request_ref": report["request_ref"], "result_ref": report["result_ref"],
        "scope": "bound content and existing reducer replay; not numerical or physical verification",
        "authority": dict(AUTHORITY)}


def trace(report, *, serial=None, lot=None):
    verify(report)
    if (serial is None) == (lot is None):
        raise ValueError("Select exactly one serial or material lot")
    records = report["result"]["manufacturing_result"]["instances"]
    if serial is not None:
        if text(serial) not in records:
            raise ValueError("Unknown serial")
        seeds = {serial}
        directions = ("parents", "children")
    else:
        text(lot)
        if lot != report["request"]["manufacturing"]["inventory"]["lot_id"]:
            raise ValueError("Unknown material lot")
        seeds = {s for s, row in records.items() if row["genealogy"].get("material_lot_id") == lot}
        directions = ("children",)
    # Walk ancestors and descendants independently; siblings are not ancestors.
    selected = set(seeds)
    for direction in directions:
        frontier, visited = list(seeds), set(seeds)
        while frontier:
            for target in records[frontier.pop()][direction]:
                if target not in visited:
                    frontier.append(target)
                    visited.add(target)
        selected.update(visited)
    study_records = report["result"]["study_records"]
    selected_events = []
    for event in report["request"]["manufacturing"]["events"]:
        data = event["data"]
        mentioned = set(data.get("component_serials", [])) | set(data.get("serials", []))
        mentioned.update(o["serial"] for o in data.get("outputs", []))
        if "serial" in data:
            mentioned.add(data["serial"])
        if event["kind"] == "quality_assessment":
            mentioned.update(s for s in selected if records[s]["genealogy"].get("cycle_id") == data["cycle_id"])
        if mentioned & selected:
            selected_events.append(deepcopy(event))
    inspection_ids = {e["event_id"] for e in selected_events if e["kind"] == "inspection"}
    output = {"schema": "ciw.study-manufacturing-trace.v1", "serial": serial, "material_lot_id": lot,
        "records": {s: deepcopy(records[s]) for s in sorted(selected)},
        "study_records": {s: deepcopy(study_records[s]) for s in sorted(selected) if s in study_records},
        "study_ref": report["request"]["binding"]["study_ref"],
        "assessment_ref": report["request"]["binding"]["assessment_ref"],
        "inventory": deepcopy(report["result"]["manufacturing_result"]["inventory"]),
        "inspection_nonconformances": [deepcopy(n) for n in report["result"]["inspection_nonconformances"] if n["serial"] in selected],
        "events": selected_events,
        "inspection_evidence": [deepcopy(e) for e in report["request"]["inspection_evidence"] if e["event_id"] in inspection_ids],
        "configuration": deepcopy(report["request"]["manufacturing"]["configuration"]),
        "source_dossier_ref": cid(report), "authority": dict(AUTHORITY)}
    bounded(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    example = sub.add_parser("example")
    example.add_argument("--study", required=True, type=Path)
    example.add_argument("--assessment", required=True, type=Path)
    example.add_argument("--output", required=True, type=Path)
    for command in ("run", "verify", "trace"):
        p = sub.add_parser(command)
        p.add_argument("input", type=Path)
        p.add_argument("--output", required=True, type=Path)
        if command == "trace":
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--serial")
            group.add_argument("--lot")
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            from .manufacturing_study_example import example_request
            result = example_request(load(args.study), load(args.assessment))
        elif args.command == "run":
            result = evaluate(load(args.input))
        elif args.command == "verify":
            result = verify(load(args.input))
        else:
            result = trace(load(args.input), serial=args.serial, lot=args.lot)
        save_new(args.output, result)
        print(json.dumps({"command": args.command, "output": str(args.output),
                          "status": result.get("status", "RETAINED"), "shipment_release": "not_authorized"}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RecursionError, OverflowError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
