"""Offline point-of-process QC verification; no physical detector qualification."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from ciw.manufacturing_contracts import content_identity, load, save_new
from ciw.manufacturing_example import example_request as v1_request
from ciw.manufacturing_runtime import evaluate as manufacture, replay, trace, verify as verify_manufacture
from ciw.polymer_quality_cli import main
from ciw.polymer_quality_example import CASES, example_request, manufacturing_request, observation, source
from ciw.polymer_toolpoint_qc import AUTHORITY, GOOD, assess, evaluate, signal_ref, validate_plan, verify


def signal(request, sid="nozzle"):
    return next(s for s in request["observation"]["signals"] if s["sensor_id"] == sid)


def reseal(request):
    for s in request["observation"]["signals"]:
        s["source_ref"] = signal_ref(s)


def decision(request, rid="nozzle-peak"):
    result = assess(request["plan"], request["observation"])
    return next(r for r in result["rules"] if r["rule_id"] == rid)


def rule(request, rid="nozzle-peak"):
    return next(r for r in request["plan"]["rules"] if r["rule_id"] == rid)


def prefix(request, through):
    value = deepcopy(request)
    value["observation"]["completed_at_s"] = value["observation"]["started_at_s"] + through
    for s in value["observation"]["signals"]:
        s["samples"] = [row for row in s["samples"] if row["time_s"] <= through]
    reseal(value)
    return value


def interval(row):
    return tuple(Decimal(row["feature_interval"][k]) for k in ("lower_decimal", "upper_decimal"))


def qc_events(request):
    return [e for e in request["events"] if e["kind"] == "quality_assessment"]


def first_shot(request):
    # Retain two births/QC events and component inspections, but no assembly/scrap/pack.
    request["events"] = [e for e in request["events"] if e["kind"] in {"cycle", "quality_assessment", "inspection"}
                         and e["data"].get("serial") != "a001"]
    return request


@pytest.mark.parametrize("case,status,held", [
    ("normal", GOOD, []), ("short-shot-suspected", "DEVIATION", ["p002"]),
    ("flow-deviation", "DEVIATION", ["p001", "p002"]),
    ("missing-sensor", "INSUFFICIENT_EVIDENCE", ["p002"]),
    ("uncertain-mapping", "INSUFFICIENT_EVIDENCE", ["p001", "p002"])])
def test_synthetic_cases(case, status, held):
    data = example_request(case)
    before = deepcopy(data)
    report = evaluate(data)
    assert data == before
    result = report["result"]
    assert result["status"] == status
    assert result["containment"]["held_serials"] == held
    assert result["authority"] == AUTHORITY
    assert result["final_part_conformity"] == "not_assessed"
    assert verify(report)["status"] == "PASS"


def test_hand_calculated_peak_arrival_integral_residual():
    data = example_request()
    assert interval(decision(data)) == (Decimal("4990000"), Decimal("5010000"))
    assert interval(decision(data, "gate-cavity.1-integral")) == (Decimal("1545000"), Decimal("1555000"))
    lo, hi = interval(decision(data, "end-cavity.1-arrival"))
    assert lo == Decimal("0.2485") and hi == Decimal("0.2515")
    assert interval(decision(data, "nozzle-residual")) == (Decimal(0), Decimal(20000))


@pytest.mark.parametrize("seed", range(25))
def test_independent_fraction_trapezoid_oracle(seed):
    rng = random.Random(seed)
    data = example_request()
    s = signal(data, "gate-cavity.1")
    for row in s["samples"]:
        row["value"] = rng.randrange(1, 10000000)
        row["expanded_uncertainty"] = rng.randrange(0, 10000)
    reseal(data)
    rows = [r for r in s["samples"] if r["time_s"] >= 0.5]
    lo = hi = Fraction(0)
    for left, right in zip(rows, rows[1:]):
        dt = Fraction(str(right["time_s"])) - Fraction(str(left["time_s"]))
        lo += dt * (left["value"] + right["value"] - left["expanded_uncertainty"] - right["expanded_uncertainty"]) / 2
        hi += dt * (left["value"] + right["value"] + left["expanded_uncertainty"] + right["expanded_uncertainty"]) / 2
    result = interval(decision(data, "gate-cavity.1-integral"))
    assert Fraction(result[0]) == lo and Fraction(result[1]) == hi


@pytest.mark.parametrize("through", [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.9])
def test_capture_prefix_never_passes_full_shot(through):
    report = evaluate(prefix(example_request(), through))
    assert report["result"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["result"]["containment"]["held_serials"] == ["p001", "p002"]


def test_causal_early_pressure_detection():
    data = prefix(example_request("flow-deviation"), 0.3)
    result = evaluate(data)["result"]
    peak = next(r for r in result["rules"] if r["rule_id"] == "nozzle-peak")
    assert result["status"] == "DEVIATION"
    assert peak["reason"] == "FEATURE_ABOVE_LIMIT"
    assert Decimal(peak["detected_at_relative_s_decimal"]) == Decimal("0.2")
    assert peak["detected_at_relative_s_decimal"] < "0.5"
    assert result["hypotheses"] == []  # Missing arrival has not yet been established.


def test_cause_remains_hypothesis():
    result = evaluate(example_request("flow-deviation"))["result"]
    assert result["hypotheses"][0]["code"] == "FLOW_DEVIATION_SUSPECTED"
    assert result["hypotheses"][0]["root_cause"] == "not_established"
    assert "GATE_BLOCKED" not in json.dumps(result)


@pytest.mark.parametrize("field,value,reason", [
    ("health", "fault", "SIGNAL_HEALTH_FAULT"), ("health", "stale", "SIGNAL_HEALTH_STALE"),
    ("health", "saturated", "SIGNAL_HEALTH_SATURATED"), ("health", "unknown", "SIGNAL_HEALTH_UNKNOWN"),
    ("calibration_ref", None, "MISSING_CALIBRATION_OR_CLOCK_EVIDENCE"),
    ("clock_alignment_ref", None, "MISSING_CALIBRATION_OR_CLOCK_EVIDENCE"),
    ("clock_skew_s", None, "MISSING_CALIBRATION_OR_CLOCK_EVIDENCE"),
    ("calibration_valid_from_s", None, "MISSING_CALIBRATION_OR_CLOCK_EVIDENCE"),
    ("calibration_valid_until_s", None, "MISSING_CALIBRATION_OR_CLOCK_EVIDENCE"),
    ("calibration_valid_from_s", 0.1, "CALIBRATION_OUTSIDE_DECLARED_VALIDITY"),
    ("calibration_valid_until_s", 0.9, "CALIBRATION_OUTSIDE_DECLARED_VALIDITY"),
    ("clock_skew_s", 0.003, "CLOCK_SKEW_EXCEEDS_PLAN")])
def test_channel_evidence_fail_closed(field, value, reason):
    data = example_request()
    signal(data)[field] = value
    result = decision(data)
    assert result["status"] == "INSUFFICIENT_EVIDENCE" and result["reason"] == reason
    assert evaluate(data)["result"]["containment"]["held_serials"] == ["p001", "p002"]


@pytest.mark.parametrize("mode,reason", [("empty", "NO_SAMPLES"), ("missing_start", "INCOMPLETE_PHASE_COVERAGE"),
    ("missing_end", "INCOMPLETE_PHASE_COVERAGE"), ("gap", "SAMPLING_GAP_EXCEEDS_PLAN"),
    ("unknown_uncertainty", "UNKNOWN_MEASUREMENT_UNCERTAINTY")])
def test_sampling_evidence(mode, reason):
    data = example_request()
    s = signal(data)
    if mode == "empty":
        s["samples"] = []
    elif mode == "missing_start":
        s["samples"] = s["samples"][1:]
    elif mode == "missing_end":
        s["samples"] = s["samples"][:5]
    elif mode == "gap":
        del s["samples"][2]
    else:
        s["samples"][1]["expanded_uncertainty"] = None
    reseal(data)
    row = decision(data)
    assert row["status"] == "INSUFFICIENT_EVIDENCE" and row["reason"] == reason


@pytest.mark.parametrize("field,value", [("valid_until_s", 0.9), ("valid_from_s", 0.1)])
def test_plan_validity_is_not_inherited(field, value):
    data = example_request()
    data["plan"][field] = value
    assert decision(data)["reason"] == "PLAN_OUTSIDE_VALIDITY"


@pytest.mark.parametrize("field,value", [("value", True), ("value", float("inf")), ("value", float("nan")),
    ("expanded_uncertainty", -1), ("expanded_uncertainty", True), ("time_s", True), ("time_s", -1),
    ("time_s", 2)])
def test_invalid_sample_numbers_refused(field, value):
    data = example_request()
    signal(data)["samples"][0][field] = value
    with pytest.raises(ValueError):
        # Some malformed values fail canonicalization itself, also fail closed.
        reseal(data)
        evaluate(data)


@pytest.mark.parametrize("field,value", [("configuration_ref", source("changed")), ("part_ref", source("changed")),
    ("source_kind", "retained_observation"), ("clock_id", "different"), ("frame", "different")])
def test_observation_bindings(field, value):
    data = example_request()
    data["observation"][field] = value
    with pytest.raises(ValueError, match="binding"):
        evaluate(data)


@pytest.mark.parametrize("field,value", [("unit", "MPa"), ("quantity", "cavity_pressure"),
    ("clock_id", "other-clock"), ("frame", "other-frame"), ("sensor_id", "unknown")])
def test_sensor_meaning_substitution_refused(field, value):
    data = example_request()
    signal(data)[field] = value
    reseal(data)
    with pytest.raises(ValueError):
        evaluate(data)


def test_location_substitution_refused_even_after_rehash():
    data = example_request()
    signal(data, "end-cavity.1")["location"]["cavity_id"] = "cavity.2"
    reseal(data)
    with pytest.raises(ValueError, match="location"):
        evaluate(data)


def test_duplicate_sensor_and_timestamps():
    data = example_request()
    data["observation"]["signals"].append(deepcopy(signal(data)))
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate(data)
    data = example_request()
    signal(data)["samples"][1]["time_s"] = 0
    reseal(data)
    with pytest.raises(ValueError, match="ordered"):
        evaluate(data)


def test_signal_and_reference_content_seals():
    data = example_request()
    signal(data)["samples"][2]["value"] += 1
    with pytest.raises(ValueError, match="content mismatch"):
        evaluate(data)
    data = example_request()
    rule(data, "nozzle-residual")["reference_curve"]["samples"][2]["value"] += 1
    with pytest.raises(ValueError, match="content mismatch"):
        evaluate(data)


@pytest.mark.parametrize("owner", ["request", "plan", "observation", "signal", "sample", "rule", "location", "mapping"])
def test_unknown_fields_fail_closed(owner):
    data = example_request()
    targets = {"request": data, "plan": data["plan"], "observation": data["observation"],
               "signal": signal(data), "sample": signal(data)["samples"][0], "rule": rule(data),
               "location": data["plan"]["sensors"][0]["location"], "mapping": data["observation"]["part_mapping"]}
    targets[owner]["unexpected"] = "must refuse"
    reseal(data)
    with pytest.raises(ValueError):
        evaluate(data)


@pytest.mark.parametrize("mutation", ["no_cavity_coverage", "unused_sensor", "overlap_phases", "duplicate_rule",
    "invalid_unit", "inverted_limits", "wrong_feature", "extra_threshold", "missing_threshold", "bad_reference", "arrival_outside_phase"])
def test_plan_refusals(mutation):
    data = example_request()
    plan = data["plan"]
    if mutation == "no_cavity_coverage":
        plan["cavity_ids"].append("cavity.3")
    elif mutation == "unused_sensor":
        plan["rules"] = [r for r in plan["rules"] if r["sensor_id"] != "tool-temperature"]
    elif mutation == "overlap_phases":
        plan["phases"][1]["start_s"] = 0.4
    elif mutation == "duplicate_rule":
        plan["rules"].append(deepcopy(rule(data)))
    elif mutation == "invalid_unit":
        rule(data)["limits"]["unit"] = "MPa"
    elif mutation == "inverted_limits":
        rule(data)["limits"]["upper"] = 1
    elif mutation == "wrong_feature":
        rule(data)["feature"] = "magic-defect-detector"
    elif mutation == "extra_threshold":
        rule(data)["threshold"] = 1
    elif mutation == "missing_threshold":
        rule(data, "end-cavity.1-arrival")["threshold"] = None
    elif mutation == "arrival_outside_phase":
        rule(data, "end-cavity.1-arrival")["limits"]["upper"] = 2
    else:
        curve = rule(data, "nozzle-residual")["reference_curve"]
        curve["samples"].pop()
        curve["samples_ref"] = content_identity(curve["samples"])
    with pytest.raises(ValueError):
        validate_plan(plan)


@pytest.mark.parametrize("uncertainty,status", [(0, GOOD), (1e-100, "INDETERMINATE"), (1, "INDETERMINATE")])
def test_tiny_uncertainty_cannot_round_away_at_boundary(uncertainty, status):
    data = example_request()
    for row in signal(data)["samples"]:
        row["value"] = 6e6
        row["expanded_uncertainty"] = uncertainty
    reseal(data)
    assert decision(data)["status"] == status


def test_low_peak_detected_only_after_fill():
    data = example_request()
    for row in signal(data)["samples"]:
        row["value"] = 1e6
    reseal(data)
    assert decision(prefix(data, 0.3))["status"] == "INSUFFICIENT_EVIDENCE"
    assert decision(data)["reason"] == "FEATURE_BELOW_LIMIT"


def test_unknown_reference_uncertainty_is_not_zero():
    data = example_request()
    curve = rule(data, "nozzle-residual")["reference_curve"]
    curve["samples"][2]["expanded_uncertainty"] = None
    curve["samples_ref"] = content_identity(curve["samples"])
    assert decision(data, "nozzle-residual")["reason"] == "UNKNOWN_REFERENCE_UNCERTAINTY"


def test_residual_retains_model_error():
    data = example_request()
    for row in signal(data)["samples"]:
        row["value"] += 500000
    reseal(data)
    assert interval(decision(data, "nozzle-residual")) == (Decimal(480000), Decimal(520000))


def test_residual_uses_reference_knots_not_only_measurement_times():
    data = example_request()
    curve = rule(data, "nozzle-residual")["reference_curve"]
    curve["samples"].insert(3, {"time_s": 0.25, "value": 1e6, "expanded_uncertainty": 10000})
    curve["samples_ref"] = content_identity(curve["samples"])
    assert interval(decision(data, "nozzle-residual")) == (Decimal(3480000), Decimal(3520000))


@pytest.mark.parametrize("kind", ["threshold_at_start", "uncertain_crossing", "early", "late"])
def test_arrival_boundaries(kind):
    data = example_request()
    s = signal(data, "end-cavity.1")
    if kind == "threshold_at_start":
        s["samples"][0]["value"] = 1e6
        expected = "ARRIVAL_LEFT_CENSORED"
    elif kind == "uncertain_crossing":
        for row in s["samples"][1:]:
            row["value"], row["expanded_uncertainty"] = 1e6, 2e6
        expected = "ARRIVAL_NOT_YET_RESOLVED"
    else:
        for row in s["samples"]:
            row["value"] = (2e6 if row["time_s"] >= (0.1 if kind == "early" else 0.5) else 0)
        expected = "FEATURE_BELOW_LIMIT" if kind == "early" else "FEATURE_ABOVE_LIMIT"
    reseal(data)
    assert decision(data, "end-cavity.1-arrival")["reason"] == expected


def test_arrival_deadline_respects_clock_skew():
    data = prefix(example_request("short-shot-suspected"), 0.4)
    assert decision(data, "end-cavity.2-arrival")["status"] == "INSUFFICIENT_EVIDENCE"
    data = prefix(example_request("short-shot-suspected"), 0.5)
    assert decision(data, "end-cavity.2-arrival")["reason"] == "ARRIVAL_NOT_OBSERVED_BY_DEADLINE"


def test_plan_order_and_signal_order_do_not_change_decisions():
    data = example_request()
    baseline = evaluate(data)["result"]
    data["observation"]["signals"].reverse()
    data["plan"]["sensors"].reverse()
    data["plan"]["rules"].reverse()
    current = evaluate(data)["result"]
    assert current["status"] == baseline["status"]
    assert sorted(current["rules"], key=lambda r: r["rule_id"]) == sorted(baseline["rules"], key=lambda r: r["rule_id"])


def test_occurrence_identity_is_separate_from_evidence_and_operation():
    data = example_request()
    first, second = evaluate(data), evaluate(data)
    assert first["evaluation_id"] != second["evaluation_id"]
    assert first["request_ref"] == second["request_ref"] and first["result_ref"] == second["result_ref"]
    a, b = verify(first), verify(first)
    assert a["verification_id"] != b["verification_id"]
    assert a["operation_id"] == first["result"]["operation_id"]


@pytest.mark.parametrize("resealed", [False, True])
def test_tamper_detection(resealed):
    report = evaluate(example_request("flow-deviation"))
    report["result"]["status"] = GOOD
    if resealed:
        report["result_ref"] = content_identity(report["result"])
    with pytest.raises(ValueError):
        verify(report)


def test_v1_replay_unchanged_against_retained_baseline():
    # Fixture is in the user change-set, not necessarily in a full repository.
    path = Path("validation/polymer-manufacturing/demo-report.json")
    if path.exists():
        retained = load(path)
        assert replay(v1_request()) == retained["result"]
    assert manufacture(v1_request())["schema"] == "ciw.polymer-manufacturing-report.v1"


def test_integrated_v2_genealogy_and_latched_hold():
    data = manufacturing_request()
    report = manufacture(data)
    assert report["schema"] == "ciw.polymer-manufacturing-report.v2"
    assert verify_manufacture(report)["status"] == "PASS"
    r = report["result"]
    assert r["instances"]["p003"]["process_quality"]["status"] == GOOD
    assert r["instances"]["p004"]["process_quality"]["status"] == "DEVIATION"
    assert len(r["instances"]["p004"]["quality_holds"]) == 2
    assert r["instances"]["p004"]["disposition"] == "SCRAPPED"
    assert r["instances"]["a001"]["disposition"] == "PACKED"
    assert r["shipment"]["status"] == "HELD"
    view = trace(report, "p004")
    assert view["schema"] == "ciw.manufacturing-genealogy.v2"
    assert any(e["kind"] == "quality_assessment" for e in view["events"])
    assert view["quality_control"]["release_gates"]["p004"]["record_gate"] == "BLOCKED"


def test_passing_dimension_does_not_clear_process_hold():
    data = first_shot(manufacturing_request())
    for event in data["events"]:
        if event["kind"] == "inspection" and event["data"]["serial"] == "p004":
            event["data"]["measurements"][0]["value"] = 0.02
    result = replay(data)
    instance = result["instances"]["p004"]
    assert instance["quality"] == "PASS" and instance["process_quality"]["status"] == "DEVIATION"
    assert instance["disposition"] == "QUARANTINED"
    assert result["orders"]["WO-COMPONENT"]["accepted_quantity"] == 3


def test_clean_signature_does_not_clear_final_inspection_failure():
    data = first_shot(manufacturing_request())
    for e in data["events"]:
        if e["kind"] == "inspection" and e["data"]["serial"] == "p001":
            e["data"]["measurements"][0]["value"] = 0.03
    result = replay(data)
    instance = result["instances"]["p001"]
    assert instance["process_quality"]["status"] == GOOD and instance["quality"] == "FAIL"
    assert instance["disposition"] == "QUARANTINED" and instance["quality_holds"]


def test_missing_assessment_blocks_assembly():
    data = manufacturing_request()
    data["events"] = [e for e in data["events"] if e["kind"] != "quality_assessment"]
    with pytest.raises(ValueError, match="Assembly"):
        replay(data)
    result = replay(first_shot(data))
    assert result["instances"]["p001"]["process_quality"]["status"] == "AWAITING_EVIDENCE"
    assert result["instances"]["p001"]["disposition"] == "QUARANTINED"


def test_final_inspection_can_precede_clean_qc():
    data = first_shot(manufacturing_request())
    qc = [deepcopy(e) for e in qc_events(data)]
    data["events"] = [e for e in data["events"] if e["kind"] != "quality_assessment"]
    for e in qc:
        e["at_s"] = 10
        data["events"].append(e)
    result = replay(data)
    assert result["instances"]["p001"]["disposition"] == "AVAILABLE"
    assert result["instances"]["p004"]["disposition"] == "QUARANTINED"


@pytest.mark.parametrize("mode", ["unresolved", "missing_evidence", "empty_hints"])
def test_ambiguous_mapping_holds_entire_shot(mode):
    data = first_shot(manufacturing_request())
    ob = qc_events(data)[0]["data"]["observation"]
    if mode in {"unresolved", "empty_hints"}:
        ob["part_mapping"]["status"] = "unresolved"
    if mode == "missing_evidence":
        ob["part_mapping"]["evidence_ref"] = None
    if mode == "empty_hints":
        ob["part_mapping"]["items"] = []
    result = replay(data)
    for serial in ("p001", "p002"):
        assert result["instances"][serial]["disposition"] == "QUARANTINED"
        assert result["instances"][serial]["quality_holds"]


@pytest.mark.parametrize("mode", ["different_serial", "swapped_cavity", "omitted_output", "foreign_hint"])
def test_mapping_mismatch_refused(mode):
    data = first_shot(manufacturing_request())
    mapping = qc_events(data)[0]["data"]["observation"]["part_mapping"]
    if mode in {"different_serial", "foreign_hint"}:
        mapping["items"][0]["serial"] = "foreign"
        if mode == "foreign_hint":
            mapping["status"] = "unresolved"
    elif mode == "swapped_cavity":
        mapping["items"][0]["cavity_id"], mapping["items"][1]["cavity_id"] = "cavity.2", "cavity.1"
    else:
        mapping["items"].pop()
    with pytest.raises(ValueError, match="mapping"):
        replay(data)


@pytest.mark.parametrize("mode", ["missing_binding", "duplicate_binding", "wrong_plan_ref", "stale_plan", "changed_revision",
    "missing_policy", "unused_plan"])
def test_policy_cannot_silently_opt_out(mode):
    data = manufacturing_request()
    policy = data["quality_control"]
    if mode == "missing_binding":
        policy["order_bindings"] = []
    elif mode == "duplicate_binding":
        policy["order_bindings"].append(deepcopy(policy["order_bindings"][0]))
    elif mode == "wrong_plan_ref":
        policy["order_bindings"][0]["monitoring_plan_ref"] = source("wrong-plan")
    elif mode == "stale_plan":
        policy["monitoring_plans"][0]["configuration_ref"] = source("stale")
    elif mode == "changed_revision":
        policy["monitoring_plans"][0]["revision"] = "B"
    elif mode == "missing_policy":
        data.pop("quality_control")
    else:
        extra = deepcopy(policy["monitoring_plans"][0]); extra["revision"] = "B"
        policy["monitoring_plans"].append(extra)
    with pytest.raises(ValueError):
        replay(data)


@pytest.mark.parametrize("mode", ["cycle_id", "cycle_evidence_ref", "future_capture", "duplicate_assessment"])
def test_assessment_binding_and_temporal_integrity(mode):
    data = first_shot(manufacturing_request())
    event = qc_events(data)[0]
    ob = event["data"]["observation"]
    if mode in {"cycle_id", "cycle_evidence_ref"}:
        ob[mode] = "wrong-cycle" if mode == "cycle_id" else source("different-evidence")
    elif mode == "future_capture":
        ob["completed_at_s"] = 2
    else:
        duplicate = deepcopy(event); duplicate["event_id"] += ".duplicate"
        data["events"].insert(2, duplicate)
    with pytest.raises(ValueError):
        replay(data)


def test_late_hold_propagates_to_packed_assembly_without_erasing_genealogy():
    data = manufacturing_request()
    data["events"].append({"event_id": "synthetic.late-hold", "at_s": 11,
        "actor_id": "synthetic.inspector", "authorization_ref": source("declared-authority"),
        "kind": "quality_hold", "data": {"serial": "p001", "reason": "Later independent inspection finding",
                                         "evidence_ref": source("late-evidence")}})
    report = manufacture(data)
    result = report["result"]
    assert result["instances"]["p001"]["disposition"] == "CONSUMED"
    assert result["instances"]["a001"]["disposition"] == "PACKED"
    assert result["instances"]["p001"]["children"] == ["a001"]
    assert result["instances"]["a001"]["quality_holds"] == result["instances"]["p001"]["quality_holds"]
    assert result["quality_control"]["release_gates"]["a001"]["record_gate"] == "BLOCKED"
    assert result["orders"]["WO-ASSEMBLY"]["accepted_quantity"] == 0
    assert result["packages"]["synthetic.package"]["shipment_status"] == "HELD"
    assert any(e["kind"] == "quality_hold" for e in trace(report, "a001")["events"])


def test_manual_hold_is_not_cleared_by_later_clean_assessment():
    data = first_shot(manufacturing_request())
    hold = {"event_id": "manual", "at_s": 1, "actor_id": "inspector", "authorization_ref": source("actor"),
            "kind": "quality_hold", "data": {"serial": "p001", "reason": "Visual concern", "evidence_ref": source("visual")}}
    data["events"].insert(1, hold)
    result = replay(data)
    assert result["instances"]["p001"]["quality"] == "PASS"
    assert result["instances"]["p001"]["process_quality"]["status"] == GOOD
    assert result["instances"]["p001"]["disposition"] == "QUARANTINED"


@pytest.mark.parametrize("kind", ["quality_release", "ship", "execute", "rework", "override_interlock"])
def test_no_authority_escalation_events(kind):
    data = manufacturing_request()
    data["events"].append({"event_id": "unsafe", "at_s": 11, "actor_id": "actor", "authorization_ref": source("grant"),
                           "kind": kind, "data": {}})
    with pytest.raises(ValueError, match="Unsupported event"):
        replay(data)


def test_v1_cannot_silently_accept_qc_extension():
    data = manufacturing_request()
    data["schema"] = "ciw.polymer-manufacturing-request.v1"
    with pytest.raises(ValueError):
        replay(data)


def test_retained_manufacturing_qc_tampering_with_recomputed_hash():
    report = manufacture(manufacturing_request())
    report["result"]["instances"]["p004"]["process_quality"]["status"] = GOOD
    report["result_ref"] = content_identity(report["result"])
    with pytest.raises(ValueError, match="fresh replay"):
        verify_manufacture(report)


@pytest.mark.parametrize("case", sorted(CASES))
def test_real_cli_lifecycle(case, tmp_path, capsys):
    path = tmp_path / "request.json"
    report = tmp_path / "report.json"
    assert main(["example", "--case", case, "--output", str(path)]) == 0
    expected = 0 if case == "normal" else 2
    assert main(["assess", str(path), "--output", str(report)]) == expected
    assert main(["verify", str(report)]) == 0
    assert main(["demo", "--output", str(report)]) == 1  # No replacement.
    assert '"status": "REFUSE"' in capsys.readouterr().err


@pytest.mark.parametrize("raw", ['{"schema":"x","schema":"y"}', '{"value":NaN}', '{"value":1e-400}', '{"value":Infinity}'])
def test_strict_json_loader(raw, tmp_path):
    path = tmp_path / "invalid.json"; path.write_text(raw)
    with pytest.raises(ValueError):
        load(path)


def test_atomic_output_failure(tmp_path, monkeypatch):
    import ciw.manufacturing_contracts as contracts
    def fail(*args):
        raise OSError("Injected output failure")
    monkeypatch.setattr(contracts.os, "link", fail)
    target = tmp_path / "result.json"
    with pytest.raises(OSError):
        save_new(target, evaluate(example_request()))
    assert not target.exists() and list(tmp_path.iterdir()) == []


def test_subprocess_manufacturing_demo(tmp_path):
    path = tmp_path / "manufacturing.json"
    args = [sys.executable, "-m", "ciw.polymer_manufacturing_cli"]
    result = subprocess.run(args + ["demo", "--quality-control", "--output", str(path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    verification = subprocess.run(args + ["verify", str(path)], capture_output=True, text=True, timeout=30)
    assert verification.returncode == 0, verification.stderr
    assert load(path)["schema"] == "ciw.polymer-manufacturing-report.v2"


def test_full_repository_qc_dispatch(tmp_path):
    if importlib.util.find_spec("ciw.control_contracts") is None:
        pytest.skip("Full-repository dispatcher dependency absent from focused snapshot")
    from ciw.polymer_cli import main as polymer_main
    path = tmp_path / "qc.json"
    assert polymer_main(["qc", "demo", "--output", str(path)]) == 0
    assert verify(load(path))["status"] == "PASS"


def test_clean_qc_does_not_relabel_manual_hold_as_process_defect():
    data = first_shot(manufacturing_request())
    data["events"].insert(1, {"event_id": "manual-before-qc", "at_s": 1, "actor_id": "inspector",
        "authorization_ref": source("inspector"), "kind": "quality_hold",
        "data": {"serial": "p001", "reason": "Independent visual finding", "evidence_ref": source("visual")}})
    result = replay(data)
    holds = result["instances"]["p001"]["quality_holds"]
    assert len(holds) == 1
    assert result["quality_control"]["nonconformances"][holds[0]]["category"] == "DECLARED_NONCONFORMANCE"
    assert result["instances"]["p001"]["process_quality"]["status"] == GOOD


def test_clean_qc_does_not_relabel_prior_dimensional_failure():
    data = first_shot(manufacturing_request())
    qcs = qc_events(data)
    data["events"] = [e for e in data["events"] if e["kind"] != "quality_assessment"]
    for e in data["events"]:
        if e["kind"] == "inspection" and e["data"]["serial"] == "p001":
            e["data"]["measurements"][0]["value"] = 0.03
    for e in qcs:
        e["at_s"] = 10
        data["events"].append(e)
    result = replay(data)
    holds = result["instances"]["p001"]["quality_holds"]
    assert len(holds) == 1
    assert result["quality_control"]["nonconformances"][holds[0]]["category"] == "FINAL_INSPECTION"


@pytest.mark.parametrize("sid", ["nozzle", "gate-cavity.1", "end-cavity.1", "gate-cavity.2", "end-cavity.2", "tool-temperature"])
def test_each_required_missing_sensor_holds_its_exact_scope(sid):
    data = example_request()
    definition = signal(data, sid)
    local_cavity = definition["location"]["cavity_id"]
    data["observation"]["signals"] = [s for s in data["observation"]["signals"] if s["sensor_id"] != sid]
    result = evaluate(data)["result"]
    for row in data["observation"]["part_mapping"]["items"]:
        assert (row["serial"] in result["containment"]["held_serials"]) == (local_cavity in {None, row["cavity_id"]})


@pytest.mark.parametrize("seed", range(15))
def test_increased_uncertainty_cannot_turn_bad_peak_into_pass(seed):
    rng = random.Random(seed)
    data = example_request()
    value = rng.randint(6100000, 20000000)
    for row in signal(data)["samples"]:
        row["value"], row["expanded_uncertainty"] = value, 0
    reseal(data)
    assert decision(data)["status"] == "DEVIATION"
    for row in signal(data)["samples"]:
        row["expanded_uncertainty"] = rng.randint(0, 50000000)
    reseal(data)
    assert decision(data)["status"] != GOOD


def test_no_future_capture_sample_accepted_after_decimal_cancellation():
    data = example_request()
    data["plan"]["valid_until_s"] = 1e50
    data["observation"]["started_at_s"] = 1e-100
    data["observation"]["completed_at_s"] = 1e50
    signal(data)["samples"][-1]["time_s"] = 1e50
    reseal(data)
    with pytest.raises(ValueError, match="capture"):
        evaluate(data)


def test_indeterminate_inspection_creates_distinct_nonconformance():
    data = first_shot(manufacturing_request())
    for e in data["events"]:
        if e["kind"] == "inspection" and e["data"]["serial"] == "p001":
            e["data"]["measurements"][0]["expanded_uncertainty"] = None
    result = replay(data)
    record = result["instances"]["p001"]
    assert record["process_quality"]["status"] == GOOD and record["quality"] == "INDETERMINATE"
    nc = result["quality_control"]["nonconformances"][record["quality_holds"][0]]
    assert nc["reason"] == "FINAL_INSPECTION_INDETERMINATE" and nc["confirmed_part_defect"] == "not_established"
