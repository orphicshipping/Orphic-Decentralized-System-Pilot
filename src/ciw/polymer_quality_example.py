"""Authored synthetic tool-point traces, not process limits for a real resin/tool."""
from __future__ import annotations

from copy import deepcopy

from .manufacturing_contracts import content_identity
from .manufacturing_example import example_request as manufacturing_base
from .polymer_toolpoint_qc import OBSERVATION_SCHEMA, PLAN_SCHEMA, signal_ref

CASES = {"normal", "short-shot-suspected", "flow-deviation", "missing-sensor", "uncertain-mapping"}


def source(name):
    return content_identity({"synthetic_qc_fixture_only": name})


def monitoring_plan(manufacturing: dict) -> dict:
    config = manufacturing["configuration"]
    plan = {"schema": PLAN_SCHEMA, "plan_id": "synthetic.toolpoint-plan", "revision": "A",
            "source_kind": "synthetic", "part_ref": config["part_ref"],
            "configuration_ref": content_identity(config), "process": "injection_molding",
            "clock_id": manufacturing["clock_id"], "frame": "synthetic.tool-frame",
            "valid_from_s": 0, "valid_until_s": 100, "max_clock_skew_s": 0.002,
            "max_sample_gap_s": 0.11, "cavity_ids": list(config["tool"]["cavity_ids"]),
            "reference_evidence_refs": [source("reference-basis")],
            "phases": [{"phase_id": "fill", "start_s": 0, "end_s": 0.5},
                       {"phase_id": "pack", "start_s": 0.5, "end_s": 1}], "sensors": [], "rules": []}

    def sensor(sid, quantity, unit, kind, cavity=None):
        plan["sensors"].append({"sensor_id": sid, "quantity": quantity, "unit": unit,
            "location": {"kind": kind, "cavity_id": cavity,
                         "gate_id": None if cavity is None else "synthetic.gate." + cavity}})

    def rule(rid, sid, feature, lower, upper, unit, phase="fill", threshold=None, curve=None):
        plan["rules"].append({"rule_id": rid, "sensor_id": sid, "phase_id": phase, "feature": feature,
            "limits": {"lower": lower, "upper": upper, "unit": unit}, "threshold": threshold, "reference_curve": curve})

    sensor("nozzle", "melt_pressure", "Pa", "nozzle")
    rule("nozzle-peak", "nozzle", "peak", 4e6, 6e6, "Pa")
    curve_samples = [{"time_s": i / 10, "value": value, "expanded_uncertainty": 10000}
                     for i, value in enumerate([0, 2e6, 4e6, 5e6, 5e6, 5e6])]
    curve = {"model_ref": source("nozzle-piecewise-reference"), "samples": curve_samples,
             "samples_ref": content_identity(curve_samples)}
    rule("nozzle-residual", "nozzle", "max_abs_residual", 0, 1e6, "Pa", curve=curve)
    for cavity in plan["cavity_ids"]:
        gate, end = "gate-" + cavity, "end-" + cavity
        sensor(gate, "cavity_pressure", "Pa", "near_gate", cavity)
        sensor(end, "cavity_pressure", "Pa", "end_of_fill", cavity)
        rule(gate + "-peak", gate, "peak", 3.5e6, 4.5e6, "Pa")
        rule(gate + "-integral", gate, "pressure_integral", 1.2e6, 1.9e6, "Pa*s", phase="pack")
        rule(end + "-arrival", end, "arrival_time", 0.2, 0.4, "s", threshold=1e6)
    sensor("tool-temperature", "tool_temperature", "K", "tool_body")
    rule("tool-temperature-peak", "tool-temperature", "peak", 345, 355, "K", phase="pack")
    return plan


def observation(plan: dict, cycle_event: dict, case: str = "normal") -> dict:
    if case not in CASES:
        raise ValueError("Unknown authored synthetic QC scenario")
    data = cycle_event["data"]
    value = {"schema": OBSERVATION_SCHEMA, "source_kind": "synthetic", "cycle_id": data["cycle_id"],
             "cycle_evidence_ref": data["cycle_evidence_ref"], "part_ref": plan["part_ref"],
             "configuration_ref": plan["configuration_ref"], "clock_id": plan["clock_id"], "frame": plan["frame"],
             "started_at_s": cycle_event["at_s"] - 1, "completed_at_s": cycle_event["at_s"], "signals": [],
             "part_mapping": {"status": "cavity_resolved", "evidence_ref": source("cavity-part-mapping"),
                 "items": [{"serial": r["serial"], "cavity_id": r["cavity_id"]} for r in data["outputs"]]}}
    for spec in plan["sensors"]:
        kind, cavity = spec["location"]["kind"], spec["location"]["cavity_id"]
        if kind == "nozzle":
            samples = [0, 2e6, 4e6, 5e6, 5e6, 5e6, 4e6, 4e6, 4e6, 4e6, 4e6]
            if case == "flow-deviation":
                samples[2:6] = [9e6] * 4
        elif kind == "near_gate":
            samples = [0, 1e6, 3e6, 4e6, 4e6, 4e6, 3e6, 3e6, 3e6, 3e6, 3e6]
        elif kind == "end_of_fill":
            samples = [0, 0, 0, 2e6, 3e6, 3e6, 3e6, 3e6, 3e6, 3e6, 3e6]
            if cavity == plan["cavity_ids"][-1] and case in {"short-shot-suspected", "flow-deviation"}:
                samples = [0] * 11
        else:
            samples = [350] * 11
        signal = {**deepcopy(spec), "frame": plan["frame"], "clock_id": plan["clock_id"],
                  "source_ref": None, "calibration_ref": source(spec["sensor_id"] + ".calibration"),
                  "calibration_valid_from_s": 0, "calibration_valid_until_s": 100,
                  "clock_alignment_ref": source("clock-alignment"), "clock_skew_s": 0.001, "health": "good",
                  "samples": [{"time_s": i / 10, "value": x,
                               "expanded_uncertainty": 0.5 if spec["unit"] == "K" else 10000}
                              for i, x in enumerate(samples)]}
        signal["source_ref"] = signal_ref(signal)
        value["signals"].append(signal)
    if case == "missing-sensor":
        value["signals"] = [s for s in value["signals"] if s["sensor_id"] != "end-" + plan["cavity_ids"][-1]]
    if case == "uncertain-mapping":
        value["part_mapping"]["status"] = "unresolved"
        value["part_mapping"]["evidence_ref"] = None
    return value


def example_request(case: str = "normal") -> dict:
    manufacturing = manufacturing_base()
    plan = monitoring_plan(manufacturing)
    return {"schema": "ciw.toolpoint-quality-request.v1", "plan": plan,
            "observation": observation(plan, manufacturing["events"][0], case)}


def manufacturing_request() -> dict:
    """Normal first shot; local abnormality in cavity 2 of shot 2; existing inspection retained."""
    value = manufacturing_base()
    value["schema"] = "ciw.polymer-manufacturing-request.v2"
    plan = monitoring_plan(value)
    value["quality_control"] = {"schema": "ciw.manufacturing-quality-policy.v1", "monitoring_plans": [plan],
        "order_bindings": [{"work_order_id": "WO-COMPONENT", "monitoring_plan_ref": content_identity(plan)}]}
    events = []
    for event in value["events"]:
        events.append(event)
        if event["kind"] == "cycle":
            case = "normal" if event["data"]["cycle_id"].endswith(".0") else "short-shot-suspected"
            events.append({"event_id": event["event_id"] + ".qc", "at_s": event["at_s"],
                "actor_id": "synthetic.qc-evaluator", "authorization_ref": source("declared.qc-authority"),
                "kind": "quality_assessment", "data": {"cycle_id": event["data"]["cycle_id"],
                    "observation": observation(plan, event, case)}})
    value["events"] = events
    return value
