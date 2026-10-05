"""Declaration-only polymer configuration compiler; never a safety controller.

The resulting envelope covers only the named scalar constraints. It is neither
a complete process-window qualification nor evidence that a machine is safe.
"""
from __future__ import annotations

from decimal import Decimal, localcontext

from .manufacturing_contracts import (bounded, content_identity, interval, items,
                                      keys, number, ref, refs, scalar, text)

CHECKS = frozenset({"material_identity", "drying", "machine_setup", "tool_setup",
                    "coolant", "sensors", "independent_safety_review"})
AUTHORITY = {"hardware_actuation": "not_performed", "physical_qualification": "not_established",
             "state_admission": "not_performed", "operator_authentication": "not_established",
             "shipment_release": "not_authorized"}


def validate_configuration(config: dict) -> None:
    bounded(config)
    keys(config, {"schema", "configuration_id", "source_kind", "part_ref", "material",
                  "machine", "tool", "recipe", "development_refs"})
    if config["schema"] != "ciw.polymer-microfab-configuration.v1":
        raise ValueError("Wrong microfab configuration schema")
    if config["source_kind"] not in {"synthetic", "retained_observation"}:
        raise ValueError("Declare synthetic or retained observation semantics")
    text(config["configuration_id"])
    ref(config["part_ref"])
    refs(config["development_refs"])
    material, machine, tool, recipe = (config[k] for k in ("material", "machine", "tool", "recipe"))
    keys(material, {"grade_id", "lot_id", "specification_ref", "characterization_refs",
                    "drying_record_ref", "moisture", "moisture_max", "melt_temperature",
                    "mold_temperature", "max_residence_time"})
    text(material["grade_id"])
    text(material["lot_id"])
    ref(material["specification_ref"])
    refs(material["characterization_refs"])
    if material["drying_record_ref"] is not None:
        ref(material["drying_record_ref"])
    if material["moisture"] is not None:
        keys(material["moisture"], {"value", "unit", "expanded_uncertainty"})
        moisture = material["moisture"]
        if moisture["unit"] != "kg/kg" or number(moisture["value"]) > 1:
            raise ValueError("Invalid moisture mass fraction")
        if moisture["expanded_uncertainty"] is not None and number(moisture["expanded_uncertainty"]) > 1:
            raise ValueError("Invalid moisture uncertainty")
    if material["moisture_max"] is not None and scalar(material["moisture_max"], "kg/kg") > 1:
        raise ValueError("Invalid maximum moisture fraction")
    if material["max_residence_time"] is not None:
        scalar(material["max_residence_time"], "s", minimum=1e-12)
    keys(machine, {"machine_id", "screw_id", "capability_ref", "compatible_tool_refs",
                   "compatible_grade_ids", "melt_temperature", "mold_temperature",
                   "shot_volume", "max_injection_pressure", "max_clamp_force"})
    for field in ("machine_id", "screw_id"):
        text(machine[field])
    if machine["capability_ref"] is not None:
        ref(machine["capability_ref"])
    refs(machine["compatible_tool_refs"])
    grades = items(machine["compatible_grade_ids"], maximum=64)
    for grade in grades:
        text(grade)
    if len(grades) != len(set(grades)):
        raise ValueError("Duplicate compatible grade")
    keys(tool, {"tool_id", "revision", "cad_ref", "cavity_ids", "melt_temperature",
                "mold_temperature", "max_injection_pressure", "projected_area"})
    text(tool["tool_id"])
    text(tool["revision"])
    ref(tool["cad_ref"])
    cavities = items(tool["cavity_ids"], minimum=1, maximum=128)
    for cavity in cavities:
        text(cavity)
    if len(set(cavities)) != len(cavities):
        raise ValueError("Duplicate cavity identity")
    for owner in (material, machine, tool):
        for field in ("melt_temperature", "mold_temperature"):
            if owner[field] is not None:
                interval(owner[field], "K", minimum=1e-12)
    for owner, field, unit in ((machine, "max_injection_pressure", "Pa"),
                               (machine, "max_clamp_force", "N"),
                               (tool, "max_injection_pressure", "Pa"),
                               (tool, "projected_area", "m2")):
        if owner[field] is not None:
            scalar(owner[field], unit, minimum=1e-12)
    if machine["shot_volume"] is not None:
        interval(machine["shot_volume"], "m3", minimum=1e-12)
    keys(recipe, {"process", "melt_temperature", "mold_temperature", "injection_pressure",
                  "cavity_pressure_bound", "shot_volume", "residence_time", "clamp_factor"})
    if recipe["process"] != "injection_molding":
        raise ValueError("Only the injection-molding declaration profile is implemented")
    for field, unit in (("melt_temperature", "K"), ("mold_temperature", "K"),
                        ("injection_pressure", "Pa"), ("cavity_pressure_bound", "Pa"),
                        ("shot_volume", "m3"), ("residence_time", "s")):
        scalar(recipe[field], unit, minimum=1e-12)
    scalar(recipe["clamp_factor"], "1", minimum=1)


def compile_configuration(config: dict, preflight: dict, *, at_s: float) -> dict:
    """Intersect declared limits and evaluate time-bound records, without grants."""
    validate_configuration(config)
    now = number(at_s)
    bounded(preflight)
    keys(preflight, {"configuration_ref", "checks"})
    if ref(preflight["configuration_ref"]) != content_identity(config):
        raise ValueError("Preflight is stale or bound to a different configuration")
    diagnostics, envelope = [], {}

    def note(check: str, status: str, reason: str) -> None:
        diagnostics.append({"check": check, "status": status, "reason": reason})

    m, machine, tool, recipe = (config[k] for k in ("material", "machine", "tool", "recipe"))
    for name, present in (("characterization", bool(m["characterization_refs"])),
                          ("drying_record", m["drying_record_ref"] is not None),
                          ("machine_capability", machine["capability_ref"] is not None)):
        note(name, "PASS" if present else "UNKNOWN", "Declared evidence present" if present else "Evidence missing")
    for name, approved, actual in (("tool_compatibility", machine["compatible_tool_refs"], content_identity(tool)),
                                    ("grade_compatibility", machine["compatible_grade_ids"], m["grade_id"])):
        note(name, "UNKNOWN" if not approved else "PASS" if actual in approved else "FAIL",
             "Membership in the declared compatibility set")
    for field in ("melt_temperature", "mold_temperature"):
        windows = [owner[field] for owner in (m, machine, tool)]
        if any(window is None for window in windows):
            note(field, "UNKNOWN", "At least one declared window is missing")
            envelope[field] = None
        else:
            low = max(window["lower"] for window in windows)
            high = min(window["upper"] for window in windows)
            value = recipe[field]["value"]
            valid = low <= high and low <= value <= high
            envelope[field] = {"lower": low, "upper": high, "unit": "K"} if low <= high else None
            note(field, "PASS" if valid else "FAIL", "Recipe checked against all three declared windows")
    shot = machine["shot_volume"]
    note("shot_volume", "UNKNOWN" if shot is None else
         "PASS" if shot["lower"] <= recipe["shot_volume"]["value"] <= shot["upper"] else "FAIL",
         "Declared shot-volume range, not nominal resin shot weight")
    for label, demand, limit in (("machine_pressure", recipe["injection_pressure"], machine["max_injection_pressure"]),
                                 ("tool_pressure", recipe["injection_pressure"], tool["max_injection_pressure"]),
                                 ("residence_time", recipe["residence_time"], m["max_residence_time"])):
        note(label, "UNKNOWN" if limit is None else "PASS" if demand["value"] <= limit["value"] else "FAIL",
             "Recipe checked against a declared upper bound")
    moisture, limit = m["moisture"], m["moisture_max"]
    if moisture is None or limit is None or moisture["expanded_uncertainty"] is None:
        note("moisture", "UNKNOWN", "Moisture value, uncertainty or limit missing")
    else:
        with localcontext() as context:
            context.prec = 512
            value = Decimal(str(moisture["value"]))
            uncertainty = Decimal(str(moisture["expanded_uncertainty"]))
            bound = Decimal(str(limit["value"]))
            state = "PASS" if value + uncertainty <= bound else "FAIL" if value - uncertainty > bound else "UNKNOWN"
        note("moisture", state, "Entire declared expanded-uncertainty interval must meet the moisture bound")
    area, force = tool["projected_area"], machine["max_clamp_force"]
    demand = None
    if area is not None:
        with localcontext() as context:
            context.prec = 512
            demand = (Decimal(str(area["value"])) * Decimal(str(recipe["cavity_pressure_bound"]["value"]))
                      * Decimal(str(recipe["clamp_factor"]["value"])))
        number(float(demand))
    envelope["required_clamp_force"] = None if demand is None else {"value": float(demand), "decimal_value": str(demand), "unit": "N"}
    note("clamp_force", "UNKNOWN" if demand is None or force is None else
         "PASS" if demand <= Decimal(str(force["value"])) else "FAIL",
         "Total projected area times declared cavity-pressure bound times declared factor; not injection pressure")
    note("pressure_basis", "PASS" if recipe["cavity_pressure_bound"]["value"] <= recipe["injection_pressure"]["value"] else "FAIL",
         "This profile requires a cavity-pressure bound no greater than injection pressure")
    seen = set()
    for check in items(preflight["checks"], maximum=len(CHECKS)):
        keys(check, {"check", "status", "evidence_ref", "observed_at_s", "valid_until_s", "source_kind"})
        name = text(check["check"])
        if name not in CHECKS or name in seen or check["status"] not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ValueError("Unsupported, repeated or invalid preflight check")
        seen.add(name)
        start, end = number(check["observed_at_s"]), number(check["valid_until_s"])
        if start > end or check["source_kind"] != config["source_kind"]:
            raise ValueError("Invalid preflight validity or mixed evidence semantics")
        if check["evidence_ref"] is not None:
            ref(check["evidence_ref"])
        state = check["status"]
        if state != "FAIL" and (not start <= now <= end or check["evidence_ref"] is None):
            state = "UNKNOWN"
        note("preflight." + name, state, "Time-bound declared record; not authenticated safety feedback")
    for name in sorted(CHECKS - seen):
        note("preflight." + name, "UNKNOWN", "Required preflight record is absent")
    states = {row["status"] for row in diagnostics}
    status = "INFEASIBLE" if "FAIL" in states else "INSUFFICIENT_EVIDENCE" if "UNKNOWN" in states else "CANDIDATE"
    return {"schema": "ciw.polymer-microfab-evaluation.v1", "status": status,
            "configuration_ref": content_identity(config), "evaluated_at_s": now,
            "declared_envelope": envelope, "diagnostics": diagnostics, "authority": dict(AUTHORITY)}
