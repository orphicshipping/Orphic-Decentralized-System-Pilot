"""Data-only manufacturing identities and declared process-envelope screening.

No solver, device I/O, qualification, admission, or execution authority. A
CANDIDATE means only that the supported declared constraints intersect.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from .core.identities import canonical_json, content_identity

PLAN_SCHEMA = "ciw.microfab-plan.v1"
ENVELOPE_SCHEMA = "ciw.microfab-envelope.v1"
MAX_BYTES = 256 * 1024
AXES = {"melt_temperature": "K", "mold_temperature": "K",
        "injection_pressure": "Pa", "residence_time": "s"}
REQUIRED = {
    "material": ("melt_temperature", "mold_temperature", "residence_time"),
    "machine": ("melt_temperature", "mold_temperature", "injection_pressure"),
    "tool": ("melt_temperature", "mold_temperature", "injection_pressure"),
    "quality": tuple(AXES),
}
COMPONENTS = ("part", "material", "machine", "tool", "quality", "lot", "order")
BASE = {"id", "revision", "source_ref"}
AUTHORITY = {
    "source_authentication": "not_established",
    "physical_validation": "not_established",
    "calibration_traceability": "not_established",
    "process_qualification": "not_established",
    "production_release": "not_performed",
    "state_admission": "not_performed",
    "hardware_actuation": "not_performed",
}


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("Unexpected or missing microfab contract fields")


def _text(value):
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise ValueError("Require bounded, nonempty identity text without outer whitespace")
    if any(ord(char) < 32 for char in value):
        raise ValueError("Control characters are not identity text")


def _ref(value):
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("Require a canonical SHA256 content reference")


def bounded(value):
    """Validate a bounded JSON tree before serialization, hashing, or copying."""
    count = 0

    def visit(item, depth):
        nonlocal count
        count += 1
        if depth > 16 or count > 16000:
            raise ValueError("Microfab JSON complexity exceeds its bound")
        if item is None or type(item) is bool:
            return
        if type(item) is str:
            if len(item) > 4096:
                raise ValueError("Microfab JSON text exceeds its bound")
        elif type(item) in (int, float):
            if abs(item) > 1e100 or not math.isfinite(item):
                raise ValueError("Require finite bounded JSON numbers")
        elif type(item) is list and len(item) <= 1024:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is dict and len(item) <= 128:
            for key, child in item.items():
                _text(key)
                visit(child, depth + 1)
        else:
            raise ValueError("Require bounded JSON data")

    visit(value, 0)
    if len(canonical_json(value).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Microfab document exceeds the 256 KiB budget")


def _interval(value, axis):
    if value is None:
        return
    _keys(value, {"unit", "lower", "upper"})
    if value["unit"] != AXES[axis]:
        raise ValueError(f"{axis} requires exact SI unit {AXES[axis]}; no conversion")
    for key in ("lower", "upper"):
        number = value[key]
        if type(number) not in (int, float) or not 0 <= number <= 1e12:
            raise ValueError("Require finite nonnegative bounded interval endpoints, not booleans")
        if AXES[axis] == "K" and number <= 0:
            raise ValueError("Require positive absolute temperature")
    if value["lower"] > value["upper"]:
        raise ValueError("Reversed process interval")


def validate_plan(value):
    bounded(value)
    _keys(value, {"schema", "process", "source_kind", "facility_id", "requested", *COMPONENTS})
    if value["schema"] != PLAN_SCHEMA or value["process"] != "injection_molding":
        raise ValueError("Require the V1 microfab injection-molding plan")
    if value["source_kind"] not in ("synthetic", "declared"):
        raise ValueError("Declare synthetic or declared source semantics")
    _text(value["facility_id"])
    extras = {
        "part": {"material_id", "material_revision"},
        "material": {"limits"}, "machine": {"limits"},
        "tool": {"limits", "part_id", "part_revision", "cavity_ids"},
        "quality": {"limits", "part_id", "part_revision"},
        "lot": {"material_id", "material_revision", "disposition"},
        "order": {"part_id", "part_revision", "quantity", "route_id", "route_revision", "operation_id"},
    }
    for role in COMPONENTS:
        component = value[role]
        _keys(component, BASE | extras[role])
        for name, item in component.items():
            if name == "source_ref":
                _ref(item)
            elif name not in ("limits", "quantity", "cavity_ids"):
                _text(item)
        if role in REQUIRED:
            _keys(component["limits"], REQUIRED[role])
            for axis, interval in component["limits"].items():
                _interval(interval, axis)
    _keys(value["requested"], AXES)
    for axis, interval in value["requested"].items():
        _interval(interval, axis)
    quantity = value["order"]["quantity"]
    if type(quantity) is not int or not 1 <= quantity <= 1000000:
        raise ValueError("Order quantity must be an integer in 1..1000000")
    cavities = value["tool"]["cavity_ids"]
    if type(cavities) is not list or not 1 <= len(cavities) <= 64:
        raise ValueError("Require 1..64 individually identified cavities")
    for cavity in cavities:
        _text(cavity)
    if len(set(cavities)) != len(cavities):
        raise ValueError("Duplicate cavity identity")
    if value["lot"]["disposition"] not in ("AVAILABLE", "HOLD", "QUARANTINED"):
        raise ValueError("Unsupported declared material-lot disposition")
    return deepcopy(value)


def compile_envelope(value):
    plan = validate_plan(value)
    checks, unknowns, envelope = [], [], {}

    def check(name, condition):
        checks.append({"name": name, "status": "PASS" if condition else "REFUSE"})

    part, material = plan["part"], plan["material"]
    for role in ("part", "lot"):
        check(f"{role}.material_binding", all(
            plan[role][f"material_{key}"] == material[key] for key in ("id", "revision")))
    for role in ("tool", "quality", "order"):
        check(f"{role}.part_binding", all(
            plan[role][f"part_{key}"] == part[key] for key in ("id", "revision")))
    disposition = plan["lot"]["disposition"]
    checks.append({"name": "lot.disposition", "status": {
        "AVAILABLE": "PASS", "HOLD": "HOLD", "QUARANTINED": "REFUSE"}[disposition]})
    for axis in AXES:
        sources = [("requested", plan["requested"][axis])] + [
            (role, plan[role]["limits"][axis]) for role in REQUIRED if axis in REQUIRED[role]]
        missing = [f"{role}.{axis}" for role, interval in sources if interval is None]
        unknowns.extend(missing)
        known = [interval for _, interval in sources if interval is not None]
        low = max((x["lower"] for x in known), default=None)
        high = min((x["upper"] for x in known), default=None)
        conflict = bool(known) and low > high
        status = "REFUSE" if conflict else "HOLD" if missing else "PASS"
        checks.append({"name": f"interval.{axis}", "status": status})
        # Never publish a partially known intersection as an operating envelope.
        envelope[axis] = {"unit": AXES[axis], "lower": low, "upper": high} if status == "PASS" else None
    statuses = {entry["status"] for entry in checks}
    status = "REFUSE" if "REFUSE" in statuses else "HOLD" if "HOLD" in statuses else "CANDIDATE"
    result = {
        "schema": ENVELOPE_SCHEMA, "status": status,
        "scope": "declared_identity_and_four_axis_intersection_only",
        "source_kind": plan["source_kind"], "plan_ref": content_identity(plan),
        "component_refs": {role: content_identity(plan[role]) for role in COMPONENTS},
        "bindings": {"facility_id": plan["facility_id"], "work_order_id": plan["order"]["id"],
                     "route_id": plan["order"]["route_id"], "route_revision": plan["order"]["route_revision"],
                     "operation_id": plan["order"]["operation_id"],
                     "part_id": part["id"], "part_revision": part["revision"],
                     "material_lot_id": plan["lot"]["id"], "machine_id": plan["machine"]["id"],
                     "tool_id": plan["tool"]["id"], "cavity_ids": plan["tool"]["cavity_ids"]},
        "checks": checks, "unknowns": unknowns, "envelope": envelope,
        "authority": deepcopy(AUTHORITY),
    }
    result["envelope_ref"] = content_identity(result)
    return result


def verify_envelope(plan, envelope):
    """Fresh deterministic reconstruction, not an independent scientific verifier."""
    bounded(envelope)
    expected = compile_envelope(plan)
    if canonical_json(envelope) != canonical_json(expected):
        raise ValueError("Retained envelope differs from fresh declared-constraint compilation")
    return {"schema": "ciw.microfab-reconstruction.v1", "status": "PASS",
            "scope": "deterministic_reconstruction_only", "envelope_status": envelope["status"],
            "plan_ref": expected["plan_ref"], "envelope_ref": expected["envelope_ref"],
            "authority": deepcopy(AUTHORITY)}


def example_plan():
    """Illustrative authored values, not a material grade or executable recipe."""
    def base(role):
        return {"id": f"synthetic.{role}.1", "revision": "A",
                "source_ref": content_identity({"synthetic_source": role})}

    limits = {axis: {"unit": unit, "lower": low, "upper": high}
              for (axis, unit), (low, high) in zip(AXES.items(),
                  ((450, 500), (300, 350), (1000000, 5000000), (10, 120)))}
    part = {**base("part"), "material_id": "synthetic.material.1", "material_revision": "A"}
    plan = {"schema": PLAN_SCHEMA, "process": "injection_molding", "source_kind": "synthetic",
            "facility_id": "synthetic.facility.1", "part": part, "requested": deepcopy(limits)}
    for role, axes in REQUIRED.items():
        plan[role] = {**base(role), "limits": {axis: deepcopy(limits[axis]) for axis in axes}}
    for role in ("tool", "quality"):
        plan[role].update(part_id=part["id"], part_revision=part["revision"])
    plan["tool"]["cavity_ids"] = ["synthetic.cavity.1", "synthetic.cavity.2"]
    plan["lot"] = {**base("lot"), "material_id": "synthetic.material.1",
                   "material_revision": "A", "disposition": "AVAILABLE"}
    plan["order"] = {**base("order"), "part_id": part["id"], "part_revision": "A", "quantity": 100,
                     "route_id": "synthetic.route.1", "route_revision": "A", "operation_id": "mold"}
    return plan
