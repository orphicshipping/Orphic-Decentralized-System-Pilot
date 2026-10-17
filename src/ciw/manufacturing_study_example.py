"""Synthetic closure-tab fixture using an explicitly supplied retained study.

Tolerances, samples, calibration and cell settings are authored test data.
No real part, calibration certificate or validated machine recipe is supplied.
"""
from copy import deepcopy

from .manufacturing_contracts import content_identity as cid
from .manufacturing_example import example_request as manufacturing_example
from .manufacturing_study import SCHEMA, replay


def example_request(study, assessment):
    if study.get("source_kind") != "synthetic":
        raise ValueError("Synthetic example refuses a nonsynthetic study")
    candidates = {c["candidate_id"]: c for c in study["candidates"]}
    if "tab-reference" not in candidates:
        raise ValueError("This example requires the retained tab-reference study candidate")
    candidate = candidates["tab-reference"]
    component = next(c for c in study["components"] if c["component_id"] == candidate["component_id"])
    material = next(m for m in study["materials"] if m["material_id"] == candidate["material_id"])
    requirements = {r["requirement_id"]: r for r in study["requirements"] if r["subject"] == component["component_id"]}
    request = manufacturing_example()
    source = lambda name: cid({"synthetic_only": name})
    q = lambda value, unit: {"value": value, "unit": unit}
    part = {"part_number": component["part_number"], "revision": component["part_revision"], "bom": [],
        "requirement_refs": [cid(r) for r in requirements.values()], "inspection_plan": [
            {"characteristic": "thickness_m", "unit": "m", "lower": 0.00095, "upper": 0.00105,
             "condition": "conditioned", "specification_ref": cid({"geometry": candidate["geometry"], "dimension": "thickness_m"})},
            {"characteristic": "mass_kg", "unit": "kg", "lower": 0.00009, "upper": 0.00012,
             "condition": "conditioned", "specification_ref": cid(requirements["R-tab-mass"])}]}
    pref = cid(part)
    config = request["configuration"]
    config["part_ref"] = pref
    config["material"]["grade_id"] = material["material_id"]
    config["material"]["specification_ref"] = cid(material)
    config["machine"]["compatible_grade_ids"] = [material["material_id"]]
    config["development_refs"] = [cid(study), cid(assessment)]
    cref = cid(config)
    request["parts"] = [part]
    request["routes"] = [{"route_id": "synthetic.tab-route", "revision": "A", "part_ref": pref, "steps": ["form", "inspect"]}]
    request["orders"] = [{"work_order_id": "WO-TAB-STUDY", "part_ref": pref, "route_ref": cid(request["routes"][0]),
                          "quantity": 1, "max_starts": 4, "configuration_ref": cref}]
    request["preflight"]["configuration_ref"] = cref
    request["inventory"]["mass"] = q(0.001, "kg")
    request["development_runs"] = [{"study_id": study["identity"]["study_id"], "configuration_ref": cref,
        "hypothesis_ref": cid(study), "model_ref": cid(candidate), "result_ref": cid(assessment), "evidence_refs": [cid(study)]}]
    request["events"] = []
    observations = []

    def event(kind, data):
        index = len(request["events"])
        value = {"event_id": f"synthetic.tab.event.{index}", "at_s": float(index + 1), "actor_id": "synthetic.inspector",
            "authorization_ref": source("declared-authorization-not-authenticated"), "kind": kind, "data": data}
        request["events"].append(value)
        return value

    for cycle in range(2):
        event("cycle", {"cycle_id": f"synthetic.tab.cycle.{cycle}", "work_order_id": "WO-TAB-STUDY",
            "configuration_ref": cref, "material_lot_id": request["inventory"]["lot_id"],
            "issued_mass": q(0.0003, "kg"), "waste_mass": q(0.0001, "kg"),
            "outputs": [{"serial": f"tab{cycle * 2 + cavity:03d}", "cavity_id": f"cavity.{cavity}",
                         "mass": q(0.0001, "kg")} for cavity in (1, 2)],
            "cycle_evidence_ref": source(f"cycle-{cycle}-not-acquired"), "model_prediction_ref": cid(assessment)})
    for index, thickness in enumerate((0.001, 0.00110, 0.00105, 0.001)):
        serial = f"tab{index + 1:03d}"
        inspection = event("inspection", {"serial": serial, "condition": "conditioned", "measurements": []})
        for characteristic, unit, value, instrument in (("thickness_m", "m", thickness, "synthetic.micrometer"),
                                                        ("mass_kg", "kg", 0.0001, "synthetic.balance")):
            calibration = {"schema": "ciw.study-calibration-declaration.v1", "instrument_id": instrument,
                "source_kind": "synthetic", "unit": unit, "clock_id": request["clock_id"],
                "frame_id": study["identity"]["frame"], "valid_from_s": 0, "valid_until_s": 100,
                "certificate_ref": source(instrument + "-not-a-real-certificate")}
            if index == 3 and characteristic == "thickness_m":
                calibration = None
            obs = {"schema": "ciw.study-part-observation.v1", "observation_id": f"synthetic.sample.{serial}.{characteristic}",
                "source_kind": "synthetic", "serial": serial, "part_ref": pref,
                "material_lot_id": request["inventory"]["lot_id"], "cycle_id": f"synthetic.tab.cycle.{index // 2}",
                "cavity_id": f"cavity.{index % 2 + 1}", "characteristic": characteristic, "unit": unit,
                "condition": "conditioned", "value": value, "expanded_uncertainty": 0.000001,
                "instrument_id": instrument, "clock_id": request["clock_id"], "frame_id": study["identity"]["frame"],
                "observed_at_s": inspection["at_s"], "calibration_ref": cid(calibration) if calibration else None}
            reading = {field: obs[field] for field in ("characteristic", "unit", "value", "expanded_uncertainty", "calibration_ref")}
            reading["evidence_ref"] = cid(obs)
            inspection["data"]["measurements"].append(reading)
            observations.append({"event_id": inspection["event_id"], "characteristic": characteristic,
                                 "observation": obs, "calibration": calibration})
    event("scrap", {"serial": "tab002", "reason": "Declared thickness interval is wholly above the authored tolerance",
                    "evidence_ref": source("synthetic-scrap-disposition")})
    output = {"schema": SCHEMA, "study": deepcopy(study), "assessment": deepcopy(assessment),
        "binding": {"study_ref": cid(study), "assessment_ref": cid(assessment), "candidate_id": "tab-reference",
            "work_order_id": "WO-TAB-STUDY", "part_ref": pref, "configuration_ref": cref,
            "inspection_links": [{"characteristic": "thickness_m", "kind": "design_dimension", "target": "thickness_m"},
                                 {"characteristic": "mass_kg", "kind": "requirement_support", "target": "R-tab-mass"}]},
        "manufacturing": request, "inspection_evidence": observations}
    replay(output)
    return output
