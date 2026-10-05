"""Outcome validation of existing tool-point QC, without retuning or release.

Join a declared complete part cohort to verified manufacturing genealogy, native
stream journals and separately declared inspection outcomes. Predictions use ONLY
process evidence received by the protocol deadline, never final inspection or
future stream updates. This does not authenticate outcomes or commission a cell.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext

from .core.identities import new_identity, validate_identity
from .manufacturing_contracts import bounded, content_identity, count, items, keys, number, ref, refs, text
from .manufacturing_runtime import verify as verify_manufacturing
from .toolpoint_stream import AUTHORITY, CaptureJournal, NATIVE
from .toolpoint_outcome_stats import binomial_upper

PROTOCOL = "ciw.toolpoint-outcome-protocol.v1"
REQUEST = "ciw.toolpoint-outcome-request.v1"
OPERATION = "polymer.toolpoint-outcome-benchmark.v1"
RESULT = "ciw.toolpoint-outcome-result.v1"
REPORT = "ciw.toolpoint-outcome-report.v1"
VERIFICATION = "ciw.toolpoint-outcome-verification.v1"
STATES = ("PROCESS_ALARM", "PRECAUTIONARY_HOLD", "CLEAR")
LABELS = ("NONCONFORMING", "CONFORMING", "UNKNOWN")


def unit_ref(manufacturing_request_ref: str, serial: str) -> str:
    ref(manufacturing_request_ref)
    text(serial)
    return content_identity({"manufacturing_request_ref": manufacturing_request_ref, "serial": serial})


def _names(value):
    for entry in items(value, maximum=128):
        text(entry)
    if len(set(value)) != len(value):
        raise ValueError("Duplicate declared identity")
    return value


def _validate_protocol(p):
    keys(p, {"schema", "study_id", "revision", "source_kind", "part_ref", "characteristic_id",
             "specification_ref", "inspection_condition", "clock_id", "frozen_at_s", "freeze_evidence_ref",
             "decision_deadline_after_start_s", "monitoring_plan_refs", "enrollment",
             "development_lot_ids", "development_cycle_refs", "development_evidence_refs",
             "iid_lot_sampling_declared", "sampling_basis_ref", "confidence_level", "criteria"})
    if p["schema"] != PROTOCOL or p["source_kind"] not in {"synthetic", "retained_observation"}:
        raise ValueError("Wrong outcome-study protocol or evidence semantics")
    for k in ("study_id", "revision", "characteristic_id", "inspection_condition", "clock_id"):
        text(p[k])
    for k in ("part_ref", "specification_ref", "freeze_evidence_ref"):
        ref(p[k])
    for k in ("frozen_at_s", "decision_deadline_after_start_s", "confidence_level"):
        number(p[k])
    if p["decision_deadline_after_start_s"] <= 0 or not .5 <= p["confidence_level"] <= .999:
        raise ValueError("Invalid decision deadline or confidence level")
    refs(p["monitoring_plan_refs"], minimum=1)
    refs(p["development_cycle_refs"])
    refs(p["development_evidence_refs"])
    _names(p["development_lot_ids"])
    if type(p["iid_lot_sampling_declared"]) is not bool:
        raise ValueError("Require explicit IID lot-sampling declaration")
    if p["sampling_basis_ref"] is not None:
        ref(p["sampling_basis_ref"])
    if p["iid_lot_sampling_declared"] and p["sampling_basis_ref"] is None:
        raise ValueError("IID lot sampling requires a declared sampling basis")
    enrolled = {}
    for row in items(p["enrollment"], minimum=1, maximum=512):
        keys(row, {"manufacturing_request_ref", "serial"})
        uid = unit_ref(**row)
        if uid in enrolled:
            raise ValueError("Duplicate enrolled physical record")
        enrolled[uid] = row
    c = p["criteria"]
    keys(c, {"min_defective_lots", "min_conforming_lots", "max_lot_miss_upper", "max_lot_false_hold_upper"})
    for k in ("min_defective_lots", "min_conforming_lots"):
        count(c[k], maximum=128)
    for k in ("max_lot_miss_upper", "max_lot_false_hold_upper"):
        number(c[k])
        if c[k] > 1:
            raise ValueError("Rate limits must be probabilities")
    return enrolled


def _manufacturing(p, reports):
    units, cycles, requests = {}, {}, set()
    for report in items(reports, minimum=1, maximum=32):
        verify_manufacturing(report)
        source = report["request"]
        mr = report["request_ref"]
        if mr in requests:
            raise ValueError("Duplicate manufacturing request")
        requests.add(mr)
        cfg = source["configuration"]
        if (source["source_kind"] != p["source_kind"] or source["clock_id"] != p["clock_id"]
                or cfg["part_ref"] != p["part_ref"]):
            raise ValueError("Manufacturing scope differs from the protocol")
        lot = cfg["material"]["lot_id"]
        if lot in p["development_lot_ids"]:
            raise ValueError("Development and validation material lots overlap")
        for event in source["events"]:
            if event["kind"] != "cycle":
                continue
            d = event["data"]
            cref = d["cycle_evidence_ref"]
            cid = d["cycle_id"]
            if cref in cycles or cref in p["development_cycle_refs"]:
                raise ValueError("Duplicate or development-contaminated cycle evidence")
            if Decimal(str(event["at_s"])) < Decimal(str(p["frozen_at_s"])):
                raise ValueError("Protocol freeze postdates the recorded cycle")
            if any(c["cycle_id"] == cid and c["configuration_ref"] == content_identity(cfg) for c in cycles.values()):
                raise ValueError("Physical cycle repeated under different reports")
            cycle = {"cycle_id": cid, "configuration_ref": content_identity(cfg), "cycle_ref": cref,
                     "completed_at_s": event["at_s"], "material_lot_id": lot,
                     "outputs": [{"serial": o["serial"], "cavity_id": o["cavity_id"]} for o in d["outputs"]],
                     "cavity_ids": cfg["tool"]["cavity_ids"], "unit_refs": []}
            for out in d["outputs"]:
                uid = unit_ref(mr, out["serial"])
                if uid in units:
                    raise ValueError("Duplicate formed serial")
                units[uid] = {"unit_ref": uid, "serial": out["serial"], "cavity_id": out["cavity_id"],
                              "cycle_ref": cref, "material_lot_id": lot, "manufacturing_request_ref": mr}
                cycle["unit_refs"].append(uid)
            cycles[cref] = cycle
    if not units:
        raise ValueError("No formed component cohort")
    return units, cycles


def _streams(p, reports, cycles, assessed_at):
    derived, feature_evidence = {}, set()
    for report in items(reports, maximum=64):
        # Verify every retained update, including those later than the decision deadline.
        CaptureJournal.restore(report)
        spec, result = report["spec"], report["result"]
        plan, binding = spec["plan"], spec["binding"]
        cref = binding["cycle_ref"]
        if cref not in cycles or cref in derived:
            raise ValueError("Unknown or duplicate validation cycle stream")
        cycle = cycles[cref]
        if plan["schema"] != NATIVE:
            raise ValueError("This benchmark profile requires native cavity-to-serial mapping")
        if content_identity(plan) not in p["monitoring_plan_refs"]:
            raise ValueError("Plan was not frozen into this protocol")
        if (binding["source_kind"] != p["source_kind"] or binding["cycle_id"] != cycle["cycle_id"]
                or plan["configuration_ref"] != cycle["configuration_ref"] or plan["part_ref"] != p["part_ref"]
                or plan["clock_id"] != p["clock_id"] or set(plan["cavity_ids"]) != set(cycle["cavity_ids"])):
            raise ValueError("Stream differs from manufacturing/protocol identity")
        start = Decimal(str(binding["started_at_s"]))
        if start < Decimal(str(p["frozen_at_s"])):
            raise ValueError("Protocol freeze postdates acquisition start")
        if start + Decimal(result["captured_through_s_decimal"]) > Decimal(str(cycle["completed_at_s"])):
            raise ValueError("Capture postdates recorded cycle completion")
        if any(Decimal(str(u["received_at_s"])) > assessed_at for u in report["updates"]):
            raise ValueError("Study includes future receipts")
        deadline = start + Decimal(str(p["decision_deadline_after_start_s"]))
        if assessed_at < deadline:
            raise ValueError("Assessment precedes a declared decision deadline")
        observation = report["updates"][-1]["observation"]
        mapping = observation["part_mapping"]
        if any(row not in cycle["outputs"] for row in mapping["items"]):
            raise ValueError("Mapping hints contradict manufacturing outputs")
        if mapping["status"] == "cavity_resolved" and sorted(mapping["items"], key=lambda x:x["serial"]) != sorted(cycle["outputs"], key=lambda x:x["serial"]):
            raise ValueError("Resolved stream mapping is not the complete cycle cohort")
        feature_evidence.update(plan["reference_evidence_refs"])
        for rule in plan["rules"]:
            curve = rule["reference_curve"]
            if curve is not None:
                feature_evidence.update((curve["model_ref"], curve["samples_ref"]))
        feature_evidence.update((cref, report["result_ref"], report["spec_ref"]))
        for update in report["updates"]:
            feature_evidence.update(s["source_ref"] for s in update["observation"]["signals"])
        # Reconstruct the last accepted prefix available by the declared deadline.
        journal, asof = CaptureJournal(spec), None
        for update in report["updates"]:
            if Decimal(str(update["received_at_s"])) <= deadline:
                journal.append(update)
                asof = journal.report()
        per_cavity = {}
        for cavity in cycle["cavity_ids"]:
            decision, reason = "PRECAUTIONARY_HOLD", "NO_ACCEPTED_PREFIX_BY_DEADLINE"
            alarm_time, supporting = None, []
            if asof is not None:
                state = asof["result"]
                mapping_ok = state["latest_assessment"]["mapping_status"] == "RESOLVED_DECLARATION"
                supporting = [f for f in state["latched_findings"]
                    if f["cavity_id"] in (None, cavity) and f["kind"] in {"SAMPLED_LIMIT_VIOLATION", "EVALUATOR_DEVIATION"}]
                if mapping_ok and supporting:
                    decision, reason = "PROCESS_ALARM", "RETAINED_PROCESS_DEVIATION_BY_DEADLINE"
                    alarm_time = min(Decimal(str(f["first_received_at_s"])) for f in supporting) - start
                elif mapping_ok and state["finalized"] and state["cavities"][cavity]["state"] == "NO_PROCESS_HOLD_IDENTIFIED":
                    decision, reason = "CLEAR", "FINAL_PROCESS_EVIDENCE_CLEAR_BY_DEADLINE"
                else:
                    reason = "UNRESOLVED_MAPPING" if not mapping_ok else "INCOMPLETE_OR_FAULTED_PROCESS_EVIDENCE"
            per_cavity[cavity] = {"decision": decision, "reason": reason,
                "first_alarm_receipt_after_start_s_decimal": None if alarm_time is None else str(alarm_time),
                "finding_refs": [f["finding_ref"] for f in supporting],
                "asof_head_ref": None if asof is None else asof["receipts"][-1]["receipt_ref"],
                "final_stream_head_ref": report["receipts"][-1]["receipt_ref"],
                "later_updates_excluded": len(report["updates"]) - (len(asof["updates"]) if asof else 0)}
        derived[cref] = per_cavity
    validation_evidence = {s["source_ref"] for r in reports for u in r["updates"] for s in u["observation"]["signals"]}
    validation_evidence.update(cycles)
    if validation_evidence.intersection(p["development_evidence_refs"]):
        raise ValueError("Validation acquisition evidence was used for development")
    return derived, feature_evidence


def _outcomes(p, outcomes, units, cycles, feature_evidence, assessed_at):
    found = {}
    blocked = feature_evidence | set(p["development_evidence_refs"]) | set(p["development_cycle_refs"])
    for o in items(outcomes, maximum=512):
        keys(o, {"unit_ref", "source_kind", "part_ref", "characteristic_id", "specification_ref",
                 "condition", "label", "observed_at_s", "evidence_ref", "method_ref", "calibration_ref",
                 "independence_basis_ref", "blinded_to_qc"})
        uid = ref(o["unit_ref"])
        if uid not in units or uid in found:
            raise ValueError("Unknown or duplicate independently inspected unit")
        if (o["source_kind"] != p["source_kind"] or o["part_ref"] != p["part_ref"]
                or o["characteristic_id"] != p["characteristic_id"]
                or o["specification_ref"] != p["specification_ref"] or o["condition"] != p["inspection_condition"]):
            raise ValueError("Outcome scope does not match the declared study endpoint")
        if o["label"] not in {"CONFORMING", "NONCONFORMING", "INDETERMINATE"} or type(o["blinded_to_qc"]) is not bool:
            raise ValueError("Invalid independent outcome label or blinding declaration")
        number(o["observed_at_s"])
        if not Decimal(str(cycles[units[uid]["cycle_ref"]]["completed_at_s"])) <= Decimal(str(o["observed_at_s"])) <= assessed_at:
            raise ValueError("Independent inspection time is outside retained study chronology")
        for k in ("evidence_ref", "method_ref", "calibration_ref", "independence_basis_ref"):
            if o[k] is not None:
                ref(o[k])
        if o["evidence_ref"] in blocked:
            raise ValueError("Outcome evidence is reused from process features or development")
        reasons = []
        if any(o[k] is None for k in ("evidence_ref", "method_ref", "calibration_ref", "independence_basis_ref")):
            reasons.append("MISSING_INDEPENDENT_INSPECTION_EVIDENCE")
        if not o["blinded_to_qc"]:
            reasons.append("OUTCOME_NOT_DECLARED_BLINDED")
        if o["label"] == "INDETERMINATE":
            reasons.append("INDETERMINATE_INSPECTION")
        found[uid] = {"label": "UNKNOWN" if reasons else o["label"], "reasons": reasons,
                      "outcome_ref": content_identity(o)}
    return found


def _rate(events, trials):
    return {"events": events, "trials": trials, "estimate": events/trials if trials else None}


def _statistics(rows, p):
    matrix = {label: {state: 0 for state in STATES} for label in LABELS}
    for row in rows:
        matrix[row["outcome"]][row["decision"]] += 1
    bad, good = matrix["NONCONFORMING"], matrix["CONFORMING"]
    nb, ng = sum(bad.values()), sum(good.values())
    rates = {"process_alarm_sensitivity": _rate(bad["PROCESS_ALARM"], nb),
             "process_alarm_false_positive_rate": _rate(good["PROCESS_ALARM"], ng),
             "containment_miss_rate": _rate(bad["CLEAR"], nb),
             "false_hold_rate_for_this_endpoint": _rate(ng-good["CLEAR"], ng)}
    lots = {}
    for row in rows:
        lots.setdefault(row["material_lot_id"], []).append(row)
    grouped = []
    for lot, members in sorted(lots.items()):
        defective = [r for r in members if r["outcome"] == "NONCONFORMING"]
        conforming = [r for r in members if r["outcome"] == "CONFORMING"]
        grouped.append({"material_lot_id": lot, "enrolled_parts": len(members),
            "defective_parts": len(defective), "conforming_parts": len(conforming),
            "unknown_outcomes": sum(r["outcome"] == "UNKNOWN" for r in members),
            "any_containment_miss": any(r["decision"] == "CLEAR" for r in defective),
            "any_false_hold": any(r["decision"] != "CLEAR" for r in conforming)})
    nbad = sum(g["defective_parts"] > 0 for g in grouped)
    ngood = sum(g["conforming_parts"] > 0 for g in grouped)
    # Bonferroni allocation: two upper-rate criteria share the declared family alpha.
    alpha = (Decimal(1)-Decimal(str(p["confidence_level"]))) / 2
    bounds = {"trial_unit": "material_lot_with_relevant_known_outcome", "sampling_model": "independent identically distributed lot endpoints; declared, not authenticated",
              "family_confidence_level": p["confidence_level"], "per_endpoint_alpha_decimal": str(alpha),
              "lot_any_miss": None, "lot_any_false_hold": None}
    if p["iid_lot_sampling_declared"]:
        bounds["lot_any_miss"] = binomial_upper(sum(g["any_containment_miss"] for g in grouped), nbad, alpha=str(alpha))
        bounds["lot_any_false_hold"] = binomial_upper(sum(g["any_false_hold"] for g in grouped), ngood, alpha=str(alpha))
    reasons = []
    c = p["criteria"]
    if not p["iid_lot_sampling_declared"]:
        reasons.append("IID_LOT_SAMPLING_NOT_DECLARED")
    if sum(matrix["UNKNOWN"].values()):
        reasons.append("UNRESOLVED_INDEPENDENT_OUTCOMES")
    if nbad < c["min_defective_lots"]:
        reasons.append("TOO_FEW_DEFECTIVE_LOTS")
    if ngood < c["min_conforming_lots"]:
        reasons.append("TOO_FEW_CONFORMING_LOTS")
    for name, key in (("lot_any_miss", "max_lot_miss_upper"), ("lot_any_false_hold", "max_lot_false_hold_upper")):
        b = bounds[name]
        if b is None or b["upper_decimal"] is None or Decimal(b["upper_decimal"]) > Decimal(str(c[key])):
            reasons.append(name.upper()+"_BOUND_NOT_ESTABLISHED")
    return matrix, rates, grouped, bounds, reasons


def analyze(request):
    bounded(request)
    keys(request, {"schema", "protocol", "manufacturing_reports", "streams", "outcomes", "assessed_at_s"})
    if request["schema"] != REQUEST:
        raise ValueError("Wrong outcome benchmark request schema")
    number(request["assessed_at_s"])
    p = request["protocol"]
    enrolled = _validate_protocol(p)
    with localcontext() as context:
        context.prec = 512
        units, cycles = _manufacturing(p, request["manufacturing_reports"])
        if set(units) != set(enrolled):
            raise ValueError("Enrollment must cover every formed unit in the supplied manufacturing cohort")
        assessed_at = Decimal(str(request["assessed_at_s"]))
        if any(Decimal(str(e["at_s"])) > assessed_at for r in request["manufacturing_reports"] for e in r["request"]["events"]):
            raise ValueError("Manufacturing report includes future events")
        streams, feature_evidence = _streams(p, request["streams"], cycles, assessed_at)
        outcomes = _outcomes(p, request["outcomes"], units, cycles, feature_evidence, assessed_at)
        rows = []
        for uid, unit in sorted(units.items()):
            missing = {"decision": "PRECAUTIONARY_HOLD", "reason": "MISSING_CYCLE_STREAM",
                "first_alarm_receipt_after_start_s_decimal": None, "finding_refs": [],
                "asof_head_ref": None, "final_stream_head_ref": None, "later_updates_excluded": 0}
            decision = streams[unit["cycle_ref"]][unit["cavity_id"]] if unit["cycle_ref"] in streams else missing
            outcome = outcomes.get(uid, {"label": "UNKNOWN", "reasons": ["MISSING_INDEPENDENT_OUTCOME"], "outcome_ref": None})
            rows.append({**unit, **decision, "outcome": outcome["label"], "outcome_ref": outcome["outcome_ref"],
                         "outcome_reasons": outcome["reasons"]})
        matrix, rates, grouped, bounds, reasons = _statistics(rows, p)
    if any(r["reason"] == "MISSING_CYCLE_STREAM" for r in rows):
        reasons.append("MISSING_CYCLE_STREAMS")
    empirical = "SYNTHETIC_ONLY" if p["source_kind"] == "synthetic" else "DECLARED_OBSERVATIONAL_EVIDENCE"
    result = {"schema": RESULT, "operation_ref": content_identity({"operation": OPERATION}),
        "protocol_ref": content_identity(p), "source_kind": p["source_kind"], "evidence_class": empirical,
        "enrolled_parts": len(rows), "known_outcomes": sum(r["outcome"] != "UNKNOWN" for r in rows),
        "rows": rows, "confusion_by_endpoint": matrix, "descriptive_part_rates": rates,
        "lot_trials": grouped, "conditional_lot_bounds": bounds,
        "criteria_screen": {"status": "NOT_ESTABLISHED" if reasons else "DECLARED_CRITERIA_SATISFIED", "reasons": reasons},
        "qualification": "NOT_ESTABLISHED",
        "interpretation": "Scope-limited outcome associations; precautionary holds are not process detections; no automatic qualification",
        "authority": dict(AUTHORITY)}
    bounded(result)
    return result


def evaluate(request):
    result = analyze(request)
    report = {"schema": REPORT, "operation_ref": content_identity({"operation": OPERATION}),
              "execution_id": new_identity("execution"), "request": deepcopy(request),
              "request_ref": content_identity(request), "result": result, "result_ref": content_identity(result)}
    bounded(report)
    return report


def verify(report, *, expected_protocol_ref=None):
    bounded(report)
    keys(report, {"schema", "operation_ref", "execution_id", "request", "request_ref", "result", "result_ref"})
    if report["schema"] != REPORT or report["operation_ref"] != content_identity({"operation": OPERATION}):
        raise ValueError("Wrong outcome report or operation")
    validate_identity(report["execution_id"], "execution")
    if expected_protocol_ref is not None:
        ref(expected_protocol_ref)
        if content_identity(report["request"]["protocol"]) != expected_protocol_ref:
            raise ValueError("Protocol differs from the separately retained anchor")
    if report["request_ref"] != content_identity(report["request"]) or report["result_ref"] != content_identity(report["result"]):
        raise ValueError("Outcome report content mismatch")
    if content_identity(analyze(report["request"])) != report["result_ref"]:
        raise ValueError("Outcome report differs from full replay")
    return {"schema": VERIFICATION, "status": "PASS", "verification_id": new_identity("verification"),
            "execution_id": report["execution_id"], "protocol_anchor_checked": expected_protocol_ref is not None,
            "request_ref": report["request_ref"], "result_ref": report["result_ref"],
            "scope": "Full same-implementation replay; no authenticity or physical qualification", "authority": dict(AUTHORITY)}
