"""Bounded, observation-only tool-point QC on retained aligned process traces.

All limits are supplied, revision-bound declarations. No universal defect model,
physical qualification, acquisition driver, execute grant or safety controller.
Feature intervals condition on supplied timestamps and piecewise-linear signals;
expanded uncertainties are propagated as interval envelopes, not independent noise.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext

from .core.identities import new_identity, validate_identity
from .manufacturing_contracts import bounded, content_identity, items, keys, number, ref, refs, text

PLAN_SCHEMA = "ciw.toolpoint-monitoring-plan.v1"
OBSERVATION_SCHEMA = "ciw.toolpoint-observation.v1"
OPERATION = "polymer.toolpoint-quality-assessment.v1"
QUANTITIES = {"melt_pressure": "Pa", "cavity_pressure": "Pa", "tool_temperature": "K",
              "screw_position": "m"}
LOCATIONS = {"melt_pressure": {"nozzle", "hot_runner_tip"},
             "cavity_pressure": {"near_gate", "end_of_fill"},
             "tool_temperature": {"tool_body", "near_gate"}, "screw_position": {"screw"}}
FEATURES = {"peak", "pressure_integral", "arrival_time", "max_abs_residual"}
GOOD = "PROCESS_SIGNATURE_ACCEPTABLE"
AUTHORITY = {"physical_qualification": "not_established", "sensor_authentication": "not_established",
             "reference_qualification": "not_established", "hardware_actuation": "not_performed",
             "physical_containment": "not_performed", "state_admission": "not_performed",
             "shipment_release": "not_authorized", "same_shot_control": "not_implemented"}


def _d(value) -> Decimal:
    return Decimal(str(value))


def signal_ref(signal: dict) -> str:
    """Bind the samples AND their declared measurement meanings, not just values."""
    return content_identity({k: signal[k] for k in
        ("sensor_id", "quantity", "unit", "location", "frame", "clock_id", "samples")})


def _names(values: list, maximum: int = 128) -> list:
    result = items(values, minimum=1, maximum=maximum)
    for value in result:
        text(value)
    if len(set(result)) != len(result):
        raise ValueError("Duplicate declared identity")
    return result


def _samples(values: list, unit: str, end: Decimal) -> list:
    previous = None
    for sample in items(values, maximum=2048):
        keys(sample, {"time_s", "value", "expanded_uncertainty"})
        number(sample["time_s"])
        stamp = _d(sample["time_s"])
        if stamp > end or previous is not None and stamp <= previous:
            raise ValueError("Require strictly ordered samples within the retained capture")
        number(sample["value"], minimum=1e-12 if unit == "K" else -1e50)
        if sample["expanded_uncertainty"] is not None:
            number(sample["expanded_uncertainty"])
        previous = stamp
    return values


def validate_plan(plan: dict) -> dict:
    bounded(plan)
    keys(plan, {"schema", "plan_id", "revision", "source_kind", "part_ref", "configuration_ref",
                "process", "clock_id", "frame", "valid_from_s", "valid_until_s", "max_clock_skew_s",
                "max_sample_gap_s", "cavity_ids", "reference_evidence_refs", "phases", "sensors", "rules"})
    if plan["schema"] != PLAN_SCHEMA or plan["process"] != "injection_molding":
        raise ValueError("Require the injection-molding tool-point monitoring plan")
    if plan["source_kind"] not in {"synthetic", "retained_observation"}:
        raise ValueError("Declare the evidence semantics")
    for name in ("plan_id", "revision", "clock_id", "frame"):
        text(plan[name])
    for name in ("part_ref", "configuration_ref"):
        ref(plan[name])
    refs(plan["reference_evidence_refs"], minimum=1)
    for name in ("valid_from_s", "valid_until_s", "max_clock_skew_s", "max_sample_gap_s"):
        number(plan[name])
    if plan["valid_from_s"] >= plan["valid_until_s"] or plan["max_sample_gap_s"] <= 0:
        raise ValueError("Invalid plan validity or sampling interval")
    cavities = _names(plan["cavity_ids"])
    phases, previous = {}, Decimal(0)
    for phase in items(plan["phases"], minimum=1, maximum=16):
        keys(phase, {"phase_id", "start_s", "end_s"})
        pid = text(phase["phase_id"])
        number(phase["start_s"])
        number(phase["end_s"])
        if pid in phases or _d(phase["start_s"]) < previous or phase["end_s"] <= phase["start_s"]:
            raise ValueError("Duplicate, overlapping or invalid process phase")
        phases[pid] = phase
        previous = _d(phase["end_s"])
    sensors, covered = {}, set()
    for sensor in items(plan["sensors"], minimum=1, maximum=32):
        keys(sensor, {"sensor_id", "quantity", "unit", "location"})
        sid = text(sensor["sensor_id"])
        quantity = text(sensor["quantity"])
        if sid in sensors or quantity not in QUANTITIES or sensor["unit"] != QUANTITIES[quantity]:
            raise ValueError("Duplicate sensor or incorrect declared quantity/unit")
        loc = sensor["location"]
        keys(loc, {"kind", "cavity_id", "gate_id"})
        if text(loc["kind"]) not in LOCATIONS[quantity]:
            raise ValueError("Sensor location contradicts the measured quantity")
        if loc["cavity_id"] is not None:
            if text(loc["cavity_id"]) not in cavities:
                raise ValueError("Sensor points to an undeclared cavity")
            covered.add(loc["cavity_id"])
        if loc["kind"] in {"near_gate", "end_of_fill", "hot_runner_tip"}:
            if loc["cavity_id"] is None:
                raise ValueError("A local tool-point sensor needs a cavity identity")
        if loc["kind"] in {"nozzle", "screw"} and loc["cavity_id"] is not None:
            raise ValueError("A shared machine measurement cannot claim one cavity")
        if loc["gate_id"] is not None:
            text(loc["gate_id"])
        sensors[sid] = sensor
    if covered != set(cavities):
        raise ValueError("Every declared cavity requires local monitoring coverage")
    rules, used = set(), set()
    for rule in items(plan["rules"], minimum=1, maximum=64):
        keys(rule, {"rule_id", "sensor_id", "phase_id", "feature", "limits", "threshold", "reference_curve"})
        rid, sid, pid = (text(rule[k]) for k in ("rule_id", "sensor_id", "phase_id"))
        feature = text(rule["feature"])
        if rid in rules or sid not in sensors or pid not in phases or feature not in FEATURES:
            raise ValueError("Invalid, duplicate or unbound monitoring rule")
        rules.add(rid)
        used.add(sid)
        unit = sensors[sid]["unit"]
        if feature in {"pressure_integral", "arrival_time"} and unit != "Pa":
            raise ValueError("Pressure integral/arrival requires a pressure measurement")
        expected_unit = {"pressure_integral": "Pa*s", "arrival_time": "s"}.get(feature, unit)
        limits = rule["limits"]
        keys(limits, {"lower", "upper", "unit"})
        if limits["unit"] != expected_unit:
            raise ValueError("Feature limits require the exact declared SI unit")
        number(limits["lower"], minimum=-1e50)
        number(limits["upper"], minimum=-1e50)
        if limits["lower"] >= limits["upper"]:
            raise ValueError("Invalid feature acceptance interval")
        if feature == "arrival_time":
            number(rule["threshold"], minimum=1e-12)
            phase = phases[pid]
            if not phase["start_s"] <= limits["lower"] < limits["upper"] <= phase["end_s"]:
                raise ValueError("Arrival limits must lie inside their process phase")
        elif rule["threshold"] is not None:
            raise ValueError("Only arrival rules use a threshold")
        curve = rule["reference_curve"]
        if feature == "max_abs_residual":
            if limits["lower"] != 0:
                raise ValueError("Absolute residual limits start at zero")
            keys(curve, {"model_ref", "samples", "samples_ref"})
            ref(curve["model_ref"])
            _samples(curve["samples"], unit, previous)
            if ref(curve["samples_ref"]) != content_identity(curve["samples"]):
                raise ValueError("Reference curve content mismatch")
            phase = phases[pid]
            if (len(curve["samples"]) < 2 or curve["samples"][0]["time_s"] > phase["start_s"]
                    or curve["samples"][-1]["time_s"] < phase["end_s"]):
                raise ValueError("Reference curve must cover the entire rule phase")
        elif curve is not None:
            raise ValueError("Only residual rules use a reference curve")
    if used != set(sensors):
        raise ValueError("Every required sensor must have an explicit monitoring rule")
    return plan


def _validate_observation(plan: dict, value: dict) -> dict:
    bounded(value)
    keys(value, {"schema", "source_kind", "cycle_id", "cycle_evidence_ref", "part_ref", "configuration_ref",
                 "clock_id", "frame", "started_at_s", "completed_at_s", "signals", "part_mapping"})
    if value["schema"] != OBSERVATION_SCHEMA:
        raise ValueError("Wrong tool-point observation schema")
    text(value["cycle_id"])
    ref(value["cycle_evidence_ref"])
    for field in ("source_kind", "part_ref", "configuration_ref", "clock_id", "frame"):
        if value[field] != plan[field]:
            raise ValueError("Stale or mismatched observation binding: " + field)
    start, end = (number(value[k]) for k in ("started_at_s", "completed_at_s"))
    if end < start:
        raise ValueError("Reversed acquisition interval")
    duration = _d(value["completed_at_s"]) - _d(value["started_at_s"])
    sensors = {s["sensor_id"]: s for s in plan["sensors"]}
    found = {}
    for signal in items(value["signals"], maximum=32):
        keys(signal, {"sensor_id", "quantity", "unit", "location", "frame", "clock_id", "source_ref",
                      "calibration_ref", "calibration_valid_from_s", "calibration_valid_until_s",
                      "clock_alignment_ref", "clock_skew_s", "health", "samples"})
        sid = text(signal["sensor_id"])
        if sid in found or sid not in sensors:
            raise ValueError("Duplicate or undeclared acquired sensor")
        for field in ("quantity", "unit", "location"):
            if signal[field] != sensors[sid][field]:
                raise ValueError("Acquired sensor meaning/location differs from the plan")
        if signal["frame"] != plan["frame"] or signal["clock_id"] != plan["clock_id"]:
            raise ValueError("Explicit frame and clock alignment is required")
        for field in ("calibration_ref", "clock_alignment_ref"):
            if signal[field] is not None:
                ref(signal[field])
        for field in ("calibration_valid_from_s", "calibration_valid_until_s", "clock_skew_s"):
            if signal[field] is not None:
                number(signal[field])
        if (signal["calibration_valid_from_s"] is not None and signal["calibration_valid_until_s"] is not None
                and signal["calibration_valid_from_s"] > signal["calibration_valid_until_s"]):
            raise ValueError("Reversed calibration validity")
        if signal["health"] not in {"good", "unknown", "fault", "stale", "saturated"}:
            raise ValueError("Unknown channel-health declaration")
        _samples(signal["samples"], signal["unit"], duration)
        if ref(signal["source_ref"]) != signal_ref(signal):
            raise ValueError("Retained signal content mismatch")
        found[sid] = signal
    mapping = value["part_mapping"]
    keys(mapping, {"status", "evidence_ref", "items"})
    if mapping["status"] not in {"cavity_resolved", "unresolved"}:
        raise ValueError("Declare cavity-to-part mapping certainty")
    if mapping["evidence_ref"] is not None:
        ref(mapping["evidence_ref"])
    serials, cavities = set(), set()
    for row in items(mapping["items"], maximum=128):
        keys(row, {"serial", "cavity_id"})
        serial, cavity = text(row["serial"]), text(row["cavity_id"])
        if serial in serials or cavity in cavities or cavity not in plan["cavity_ids"]:
            raise ValueError("Invalid or duplicate cavity-to-part mapping")
        serials.add(serial)
        cavities.add(cavity)
    return found


def _combine(statuses: list[str]) -> str:
    for state in ("DEVIATION", "INSUFFICIENT_EVIDENCE", "INDETERMINATE"):
        if state in statuses:
            return state
    return GOOD


def _points(samples: list) -> list:
    return [(_d(s["time_s"]), _d(s["value"]) - _d(s["expanded_uncertainty"]),
             _d(s["value"]) + _d(s["expanded_uncertainty"])) for s in samples]


def _at(points: list, t: Decimal) -> tuple:
    for index, (stamp, lo, hi) in enumerate(points):
        if stamp == t:
            return stamp, lo, hi
        if stamp > t and index:
            a, low, high = points[index - 1]
            weight = (t - a) / (stamp - a)
            return t, low + weight * (lo - low), high + weight * (hi - high)
    raise ValueError("No extrapolation of unobserved process data")


def _clip(points: list, a: Decimal, b: Decimal) -> list:
    return [_at(points, a)] + [p for p in points if a < p[0] < b] + ([_at(points, b)] if b > a else [])


def _interval_status(lo: Decimal, hi: Decimal, low: Decimal, high: Decimal) -> str:
    if low <= lo and hi <= high:
        return GOOD
    if hi < low or lo > high:
        return "DEVIATION"
    return "INDETERMINATE"


def _rule(plan: dict, observation: dict, sensors: dict, rule: dict) -> dict:
    definition = next(s for s in plan["sensors"] if s["sensor_id"] == rule["sensor_id"])
    result = {"rule_id": rule["rule_id"], "sensor_id": rule["sensor_id"],
              "location": deepcopy(definition["location"]), "phase_id": rule["phase_id"],
              "feature": rule["feature"], "status": "INSUFFICIENT_EVIDENCE", "reason": None,
              "feature_interval": None, "detected_at_relative_s_decimal": None}

    def stop(reason, status="INSUFFICIENT_EVIDENCE", time=None):
        result.update(status=status, reason=reason,
                      detected_at_relative_s_decimal=None if time is None else str(time))
        return result

    start, end = _d(observation["started_at_s"]), _d(observation["completed_at_s"])
    if not _d(plan["valid_from_s"]) <= start <= end <= _d(plan["valid_until_s"]):
        return stop("PLAN_OUTSIDE_VALIDITY")
    signal = sensors.get(rule["sensor_id"])
    if signal is None:
        return stop("MISSING_REQUIRED_SIGNAL")
    if signal["health"] != "good":
        return stop("SIGNAL_HEALTH_" + signal["health"].upper())
    if any(signal[field] is None for field in ("calibration_ref", "calibration_valid_from_s",
            "calibration_valid_until_s", "clock_alignment_ref", "clock_skew_s")):
        return stop("MISSING_CALIBRATION_OR_CLOCK_EVIDENCE")
    if not _d(signal["calibration_valid_from_s"]) <= start <= end <= _d(signal["calibration_valid_until_s"]):
        return stop("CALIBRATION_OUTSIDE_DECLARED_VALIDITY")
    skew = _d(signal["clock_skew_s"])
    if skew > _d(plan["max_clock_skew_s"]):
        return stop("CLOCK_SKEW_EXCEEDS_PLAN")
    raw = signal["samples"]
    if not raw:
        return stop("NO_SAMPLES")
    if any(s["expanded_uncertainty"] is None for s in raw):
        return stop("UNKNOWN_MEASUREMENT_UNCERTAINTY")
    points = _points(raw)
    phase = next(p for p in plan["phases"] if p["phase_id"] == rule["phase_id"])
    a, b = _d(phase["start_s"]), _d(phase["end_s"])
    through = min(b, end - start)
    if through < a or points[0][0] > a or points[-1][0] < through:
        return stop("INCOMPLETE_PHASE_COVERAGE")
    # Check ORIGINAL adjacent acquisition times, not artificial clipped endpoints.
    for left, right in zip(points, points[1:]):
        if right[0] > a and left[0] < through and right[0] - left[0] > _d(plan["max_sample_gap_s"]):
            return stop("SAMPLING_GAP_EXCEEDS_PLAN")
    selected = _clip(points, a, through)
    complete = through == b
    low, high = _d(rule["limits"]["lower"]), _d(rule["limits"]["upper"])
    feature = rule["feature"]
    detected = through
    if feature == "arrival_time":
        threshold = _d(rule["threshold"])
        if selected[0][2] >= threshold:
            return stop("ARRIVAL_LEFT_CENSORED")
        possible, definite = None, None
        for left, right in zip(selected, selected[1:]):
            if possible is None and right[2] >= threshold:
                possible = left[0] + (threshold - left[2]) * (right[0] - left[0]) / (right[2] - left[2])
            if definite is None and right[1] >= threshold:
                definite = left[0] + (threshold - left[1]) * (right[0] - left[0]) / (right[1] - left[1])
                detected = right[0]
        if definite is None:
            if possible is None and through - skew > high:
                return stop("ARRIVAL_NOT_OBSERVED_BY_DEADLINE", "DEVIATION", through)
            return stop("ARRIVAL_NOT_YET_RESOLVED")
        lo, hi = possible - skew, definite + skew
    elif feature == "pressure_integral":
        if not complete:
            return stop("PHASE_NOT_COMPLETE")
        lo = sum(((r[0] - l[0]) * (l[1] + r[1]) / 2 for l, r in zip(selected, selected[1:])), Decimal(0))
        hi = sum(((r[0] - l[0]) * (l[2] + r[2]) / 2 for l, r in zip(selected, selected[1:])), Decimal(0))
    elif feature == "max_abs_residual":
        curve = rule["reference_curve"]["samples"]
        if any(s["expanded_uncertainty"] is None for s in curve):
            return stop("UNKNOWN_REFERENCE_UNCERTAINTY")
        reference = _points(curve)
        times = sorted({p[0] for p in selected} | {p[0] for p in reference if a < p[0] < through})
        residuals = []
        for stamp in times:
            _, vlow, vhigh = _at(points, stamp)
            _, rlow, rhigh = _at(reference, stamp)
            x, y = vlow - rhigh, vhigh - rlow
            residuals.append((stamp, Decimal(0) if x <= 0 <= y else min(abs(x), abs(y)), max(abs(x), abs(y))))
        lo, hi = max(p[1] for p in residuals), max(p[2] for p in residuals)
        detected = next((p[0] for p in residuals if p[1] > high), through)
    else:
        lo, hi = max(p[1] for p in selected), max(p[2] for p in selected)
        detected = next((p[0] for p in selected if p[1] > high), through)
    result["feature_interval"] = {"lower_decimal": str(lo), "upper_decimal": str(hi), "unit": rule["limits"]["unit"]}
    if feature in {"peak", "max_abs_residual"} and not complete and lo <= high:
        return stop("PHASE_NOT_COMPLETE")
    status = _interval_status(lo, hi, low, high)
    reason = ("FEATURE_ABOVE_LIMIT" if lo > high else "FEATURE_BELOW_LIMIT" if hi < low else
              "UNCERTAINTY_OVERLAPS_LIMIT" if status == "INDETERMINATE" else "WITHIN_DECLARED_LIMITS")
    return stop(reason, status, detected)


def assess(plan: dict, observation: dict) -> dict:
    """Evaluate retained data or a capture prefix; a prefix cannot imply a full-shot pass."""
    with localcontext() as context:
        context.prec = 512
        validate_plan(plan)
        sensors = _validate_observation(plan, observation)
        decisions = [_rule(plan, observation, sensors, rule) for rule in plan["rules"]]
    cavities = {}
    for cavity in plan["cavity_ids"]:
        relevant = [r for r in decisions if r["location"]["cavity_id"] in (None, cavity)]
        cavities[cavity] = {"status": _combine([r["status"] for r in relevant]),
                            "rule_ids": [r["rule_id"] for r in relevant]}
    mapping = observation["part_mapping"]
    resolved = mapping["status"] == "cavity_resolved" and mapping["evidence_ref"] is not None and bool(mapping["items"])
    held = [r["serial"] for r in mapping["items"] if not resolved or cavities[r["cavity_id"]]["status"] != GOOD]
    hypotheses = []
    high_nozzle = [r["rule_id"] for r in decisions if r["location"]["kind"] == "nozzle"
                   and r["feature"] == "peak" and r["reason"] == "FEATURE_ABOVE_LIMIT"]
    late_cavity = [r["rule_id"] for r in decisions if r["feature"] == "arrival_time" and r["status"] == "DEVIATION"
                   and r["reason"] in {"FEATURE_ABOVE_LIMIT", "ARRIVAL_NOT_OBSERVED_BY_DEADLINE"}]
    if high_nozzle and late_cavity:
        hypotheses.append({"code": "FLOW_DEVIATION_SUSPECTED", "basis_rule_ids": high_nozzle + late_cavity,
                           "root_cause": "not_established"})
    status = _combine([r["status"] for r in decisions] + ([] if resolved else ["INSUFFICIENT_EVIDENCE"]))
    result = {"schema": "ciw.toolpoint-quality-assessment.v1", "operation_id": OPERATION,
              "plan_ref": content_identity(plan), "observation_ref": content_identity(observation),
              "cycle_id": observation["cycle_id"], "source_kind": observation["source_kind"],
              "status": status, "rules": decisions, "cavities": cavities, "hypotheses": hypotheses,
              "mapping_status": "RESOLVED_DECLARATION" if resolved else "UNRESOLVED_HOLD_COHORT",
              "containment": {"held_serials": held, "action": "HOLD_CYCLE_COHORT" if not resolved else "RECORD_QUALITY_HOLD" if held else "NO_PROCESS_HOLD_IDENTIFIED",
                              "physical_action": "not_performed"},
              "scope": "supplied limits; retained piecewise-linear signals; conditional on declared aligned times",
              "final_part_conformity": "not_assessed", "authority": dict(AUTHORITY)}
    bounded(result)
    return result


def evaluate(request: dict) -> dict:
    bounded(request)
    keys(request, {"schema", "plan", "observation"})
    if request["schema"] != "ciw.toolpoint-quality-request.v1":
        raise ValueError("Wrong tool-point quality request schema")
    result = assess(request["plan"], request["observation"])
    report = {"schema": "ciw.toolpoint-quality-report.v1", "evaluation_id": new_identity("execution"),
              "request": deepcopy(request), "request_ref": content_identity(request),
              "result": result, "result_ref": content_identity(result)}
    bounded(report)
    return report


def verify(report: dict) -> dict:
    bounded(report)
    keys(report, {"schema", "evaluation_id", "request", "request_ref", "result", "result_ref"})
    if report["schema"] != "ciw.toolpoint-quality-report.v1":
        raise ValueError("Wrong tool-point quality report schema")
    validate_identity(report["evaluation_id"], "execution")
    keys(report["request"], {"schema", "plan", "observation"})
    if report["request"]["schema"] != "ciw.toolpoint-quality-request.v1":
        raise ValueError("Wrong embedded tool-point quality request schema")
    if report["request_ref"] != content_identity(report["request"]) or report["result_ref"] != content_identity(report["result"]):
        raise ValueError("Retained QC content identity mismatch")
    fresh = assess(report["request"]["plan"], report["request"]["observation"])
    if content_identity(fresh) != report["result_ref"]:
        raise ValueError("Retained QC report differs from fresh replay")
    return {"schema": "ciw.toolpoint-quality-verification.v1", "status": "PASS",
            "verification_id": new_identity("verification"), "evaluation_id": report["evaluation_id"],
            "operation_id": OPERATION, "request_ref": report["request_ref"], "result_ref": report["result_ref"],
            "scope": "content integrity and same-implementation deterministic replay", "authority": dict(AUTHORITY)}
