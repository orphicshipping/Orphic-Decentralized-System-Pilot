"""Authored synthetic fixture; no grade data, real measurements or machine recipe."""
from __future__ import annotations

from .manufacturing_contracts import content_identity as identity
from .polymer_microfab import CHECKS


def example_request() -> dict:
    def source(name):
        return identity({"synthetic_only": name})

    def q(value, unit):
        return {"value": value, "unit": unit}

    def window(lower, upper, unit="K"):
        return {"lower": lower, "upper": upper, "unit": unit}

    def plan(name, condition):
        return [{"characteristic": name, "unit": "m", "lower": 0.0199, "upper": 0.0201,
                 "condition": condition, "specification_ref": source(name + ".spec")}]

    component = {"part_number": "synthetic.housing-half", "revision": "A",
        "requirement_refs": [source("component.requirement")], "bom": [],
        "inspection_plan": plan("width", "conditioned")}
    component_ref = identity(component)
    assembly = {"part_number": "synthetic.enclosure", "revision": "A",
        "requirement_refs": [source("assembly.requirement")],
        "bom": [{"part_ref": component_ref, "quantity": 2}],
        "inspection_plan": plan("assembly-width", "assembled")}
    assembly_ref = identity(assembly)
    tool = {"tool_id": "synthetic.tool", "revision": "A", "cad_ref": source("tool.cad"),
        "cavity_ids": ["cavity.1", "cavity.2"], "melt_temperature": window(460, 540),
        "mold_temperature": window(300, 380), "max_injection_pressure": q(80000000, "Pa"),
        "projected_area": q(0.001, "m2")}
    config = {"schema": "ciw.polymer-microfab-configuration.v1",
        "configuration_id": "synthetic.config.A", "source_kind": "synthetic", "part_ref": component_ref,
        "material": {"grade_id": "synthetic.resin", "lot_id": "synthetic.lot.1",
            "specification_ref": source("material.spec"), "characterization_refs": [source("characterization")],
            "drying_record_ref": source("drying"),
            "moisture": {"value": 0.0001, "unit": "kg/kg", "expanded_uncertainty": 0.00001},
            "moisture_max": q(0.0002, "kg/kg"), "melt_temperature": window(450, 550),
            "mold_temperature": window(300, 400), "max_residence_time": q(600, "s")},
        "machine": {"machine_id": "synthetic.press", "screw_id": "synthetic.screw",
            "capability_ref": source("machine.capability"), "compatible_tool_refs": [identity(tool)],
            "compatible_grade_ids": ["synthetic.resin"], "melt_temperature": window(400, 600),
            "mold_temperature": window(290, 420), "shot_volume": window(0.000001, 0.001, "m3"),
            "max_injection_pressure": q(100000000, "Pa"), "max_clamp_force": q(1000000, "N")},
        "tool": tool,
        "recipe": {"process": "injection_molding", "melt_temperature": q(500, "K"),
            "mold_temperature": q(350, "K"), "injection_pressure": q(40000000, "Pa"),
            "cavity_pressure_bound": q(20000000, "Pa"), "shot_volume": q(0.0001, "m3"),
            "residence_time": q(120, "s"), "clamp_factor": q(1.5, "1")},
        "development_refs": [source("previous.development.result")]}
    config_ref = identity(config)
    routes = [{"route_id": "synthetic.form-route", "revision": "A", "part_ref": component_ref,
               "steps": ["form", "inspect"]},
              {"route_id": "synthetic.assembly-route", "revision": "A", "part_ref": assembly_ref,
               "steps": ["assemble", "inspect"]}]
    events = []

    def event(kind, data):
        index = len(events)
        events.append({"event_id": f"synthetic.event.{index}", "at_s": float(index + 1),
            "actor_id": "synthetic.operator", "authorization_ref": source("declared.authorization"),
            "kind": kind, "data": data})

    for cycle in range(2):
        event("cycle", {"cycle_id": f"synthetic.cycle.{cycle}", "work_order_id": "WO-COMPONENT",
            "configuration_ref": config_ref, "material_lot_id": "synthetic.lot.1",
            "issued_mass": q(0.5, "kg"), "waste_mass": q(0.3, "kg"),
            "outputs": [{"serial": f"p{cycle * 2 + cavity:03d}", "cavity_id": f"cavity.{cavity}",
                         "mass": q(0.1, "kg")} for cavity in (1, 2)],
            "cycle_evidence_ref": source(f"cycle.{cycle}.trajectories"),
            "model_prediction_ref": source(f"cycle.{cycle}.prediction")})

    def inspect(serial, characteristic, condition, value):
        event("inspection", {"serial": serial, "condition": condition, "measurements": [
            {"characteristic": characteristic, "unit": "m", "value": value,
             "expanded_uncertainty": 0.00001, "evidence_ref": source(serial + ".measurement"),
             "calibration_ref": source("synthetic.calibration")} ]})

    for serial in ("p001", "p002", "p003"):
        inspect(serial, "width", "conditioned", 0.0200)
    inspect("p004", "width", "conditioned", 0.021)
    event("scrap", {"serial": "p004", "reason": "Outside declared width tolerance", "evidence_ref": source("scrap")})
    event("assemble", {"serial": "a001", "work_order_id": "WO-ASSEMBLY", "component_serials": ["p001", "p002"],
                       "assembly_evidence_ref": source("assembly")})
    inspect("a001", "assembly-width", "assembled", 0.0200)
    event("pack", {"package_id": "synthetic.package", "serials": ["a001", "p003"], "packaging_ref": source("packaging")})
    return {"schema": "ciw.polymer-manufacturing-request.v1", "source_kind": "synthetic", "clock_id": "synthetic.clock",
        "parts": [component, assembly], "routes": routes,
        "orders": [{"work_order_id": "WO-COMPONENT", "part_ref": component_ref, "route_ref": identity(routes[0]),
                    "quantity": 3, "max_starts": 4, "configuration_ref": config_ref},
                   {"work_order_id": "WO-ASSEMBLY", "part_ref": assembly_ref, "route_ref": identity(routes[1]),
                    "quantity": 1, "max_starts": 1, "configuration_ref": None}],
        "configuration": config,
        "preflight": {"configuration_ref": config_ref, "checks": [
            {"check": name, "status": "PASS", "evidence_ref": source("preflight." + name),
             "observed_at_s": 0, "valid_until_s": 100, "source_kind": "synthetic"} for name in sorted(CHECKS)]},
        "inventory": {"lot_id": "synthetic.lot.1", "mass": q(2.0, "kg"), "evidence_ref": source("inventory")},
        "development_runs": [{"study_id": "synthetic.study.1", "configuration_ref": config_ref,
            "hypothesis_ref": source("hypothesis"), "model_ref": source("model"),
            "evidence_refs": [source("experimental.evidence")], "result_ref": source("experimental.result")}],
        "events": events}
