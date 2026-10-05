"""Quality-policy bindings and monotone record holds for manufacturing v2.

These helpers do not own canonical state or authenticate release authority.
Final inspection remains independent of process signature assessment.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from .manufacturing_contracts import content_identity, items, keys, ref, text
from .polymer_toolpoint_qc import GOOD, assess, validate_plan


def validate_policy(request: dict, parts: dict, orders: dict) -> dict:
    policy = request["quality_control"]
    keys(policy, {"schema", "monitoring_plans", "order_bindings"})
    if policy["schema"] != "ciw.manufacturing-quality-policy.v1":
        raise ValueError("Wrong manufacturing quality-policy schema")
    plans, names = {}, set()
    for plan in items(policy["monitoring_plans"], minimum=1, maximum=32):
        validate_plan(plan)
        identity = (plan["plan_id"], plan["revision"])
        if identity in names:
            raise ValueError("Duplicate monitoring plan revision")
        names.add(identity)
        if (plan["configuration_ref"] != content_identity(request["configuration"])
                or plan["part_ref"] != request["configuration"]["part_ref"]
                or plan["source_kind"] != request["source_kind"] or plan["clock_id"] != request["clock_id"]
                or set(plan["cavity_ids"]) != set(request["configuration"]["tool"]["cavity_ids"])):
            raise ValueError("Monitoring plan is stale or not bound to this manufacturing configuration")
        plans[content_identity(plan)] = plan
    bindings = {}
    forming = {oid for oid, order in orders.items() if not parts[order["part_ref"]]["bom"]}
    for binding in items(policy["order_bindings"], minimum=1, maximum=128):
        keys(binding, {"work_order_id", "monitoring_plan_ref"})
        oid, pref = text(binding["work_order_id"]), ref(binding["monitoring_plan_ref"])
        if oid in bindings or oid not in forming or pref not in plans:
            raise ValueError("Duplicate, absent or inapplicable quality binding")
        if plans[pref]["part_ref"] != orders[oid]["part_ref"]:
            raise ValueError("Quality plan binds a different part revision")
        bindings[oid] = pref
    if set(bindings) != forming or set(bindings.values()) != set(plans):
        raise ValueError("Every forming order requires a monitoring plan; unused plans are refused")
    return {"plans": plans, "bindings": bindings}


def initialize(instance: dict, plan_ref: str | None) -> None:
    instance["process_quality"] = {"status": "AWAITING_EVIDENCE" if plan_ref else "NOT_APPLICABLE_ASSEMBLY",
                                    "monitoring_plan_ref": plan_ref, "assessment_ref": None}
    instance["quality_holds"] = []


def process_clear(instance: dict) -> bool:
    return (not instance["quality_holds"]
            and instance["process_quality"]["status"] in {GOOD, "NOT_APPLICABLE_ASSEMBLY"})


def refresh(instance: dict) -> None:
    # Never erase consumption, packaging or scrap history when a later hold appears.
    if instance["disposition"] in {"CONSUMED", "PACKED", "SCRAPPED"}:
        return
    if instance["quality_holds"] or instance["inspection"] is not None and not process_clear(instance):
        instance["disposition"] = "QUARANTINED"
    elif instance["inspection"] is None:
        instance["disposition"] = "WIP"
    else:
        instance["disposition"] = "AVAILABLE" if instance["quality"] == "PASS" else "QUARANTINED"


def add_hold(instances: dict, nonconformances: dict, *, targets: list, event_ref: str,
             reason: str, evidence_ref: str, category: str) -> str:
    affected, pending = set(), list(targets)
    while pending:
        serial = pending.pop()
        if serial not in instances:
            raise ValueError("Quality hold requires an existing serial")
        if serial not in affected:
            affected.add(serial)
            pending.extend(instances[serial]["children"])
    record = {"schema": "ciw.manufacturing-nonconformance.v1", "category": category,
              "origin_serials": sorted(targets), "affected_serials": sorted(affected), "reason": reason,
              "evidence_ref": evidence_ref, "event_ref": event_ref, "status": "OPEN_HOLD",
              "physical_containment": "not_performed", "confirmed_part_defect": "not_established"}
    record_ref = content_identity(record)
    nonconformances[record_ref] = record
    for serial in sorted(affected):
        instance = instances[serial]
        if record_ref not in instance["quality_holds"]:
            instance["quality_holds"].append(record_ref)
        refresh(instance)
    return record_ref


def apply_assessment(*, policy: dict, cycles: dict, instances: dict, assessments: dict,
                     nonconformances: dict, data: dict, event_ref: str, at_s: float) -> None:
    keys(data, {"cycle_id", "observation"})
    cid = text(data["cycle_id"])
    if cid not in cycles or cid in assessments:
        raise ValueError("QC requires an existing unassessed cycle; reassessment cannot overwrite a hold")
    cycle = cycles[cid]
    pref = policy["bindings"][cycle["work_order_id"]]
    plan, observation = policy["plans"][pref], data["observation"]
    assessment = assess(plan, observation)
    if observation["cycle_id"] != cid or observation["cycle_evidence_ref"] != cycle["cycle_evidence_ref"]:
        raise ValueError("QC observation binds a different cycle or cycle evidence")
    if (Decimal(str(observation["completed_at_s"])) > Decimal(str(cycle["recorded_at_s"]))
            or Decimal(str(cycle["recorded_at_s"])) > Decimal(str(at_s))):
        raise ValueError("QC capture cannot postdate the recorded cycle or its assessment event")
    expected = sorted(cycle["outputs"], key=lambda row: row["serial"])
    actual = sorted(observation["part_mapping"]["items"], key=lambda row: row["serial"])
    resolved = assessment["mapping_status"] == "RESOLVED_DECLARATION"
    if resolved and actual != expected:
        raise ValueError("Resolved cavity-to-part mapping differs from the cycle outputs")
    # Even unresolved hints must not insert foreign serials or contradictory cavity labels.
    if any(row not in expected for row in actual):
        raise ValueError("QC mapping hints contradict recorded cycle output identities")
    aref = content_identity(assessment)
    assessments[cid] = {"assessment_ref": aref, "event_ref": event_ref, "assessment": assessment}
    for row in expected:
        instance = instances[row["serial"]]
        state = assessment["cavities"][row["cavity_id"]]["status"] if resolved else "INSUFFICIENT_EVIDENCE"
        instance["process_quality"] = {"status": state, "monitoring_plan_ref": pref, "assessment_ref": aref}
        refresh(instance)
    targets = [row["serial"] for row in expected if instances[row["serial"]]["process_quality"]["status"] != GOOD]
    if targets:
        reason = "UNRESOLVED_CAVITY_TO_PART_MAPPING" if not resolved else "PROCESS_SIGNATURE_NOT_ACCEPTABLE"
        add_hold(instances, nonconformances, targets=targets, event_ref=event_ref,
                 reason=reason, evidence_ref=aref, category="IN_PROCESS_QC")


def release_view(instance: dict) -> dict:
    reasons = []
    if instance["quality_holds"]:
        reasons.append("OPEN_QUALITY_HOLD")
    if instance["process_quality"]["status"] not in {GOOD, "NOT_APPLICABLE_ASSEMBLY"}:
        reasons.append("PROCESS_EVIDENCE_NOT_ACCEPTABLE")
    if instance["quality"] != "PASS":
        reasons.append("FINAL_INSPECTION_NOT_PASSING")
    if instance["disposition"] == "SCRAPPED":
        reasons.append("SCRAPPED")
    return {"record_gate": "BLOCKED" if reasons else "DECLARED_REQUIREMENTS_SATISFIED",
            "reasons": reasons, "assembly_or_packaging_eligible": not reasons and instance["disposition"] == "AVAILABLE",
            "shipment_release": "NOT_AUTHORIZED"}
