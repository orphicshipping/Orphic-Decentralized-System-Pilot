"""Study-to-manufacturing tests with real reducers and independent interval checks."""
from copy import deepcopy
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ciw import manufacturing_runtime as runtime
from ciw import manufacturing_study as subject
from ciw.manufacturing_contracts import content_identity as cid, load
from ciw.manufacturing_study_example import example_request
from ciw.polymer_microfab import AUTHORITY
from ciw.polymer_toolpoint_qc import PLAN_SCHEMA, OBSERVATION_SCHEMA, signal_ref

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "study-manufacturing"


@pytest.fixture
def request_data():
    return example_request(load(EXAMPLES / "study.json"), load(EXAMPLES / "assessment.json"))


def reading(request, index=0):
    evidence = request["inspection_evidence"][index]
    event = next(e for e in request["manufacturing"]["events"] if e["event_id"] == evidence["event_id"])
    return next(r for r in event["data"]["measurements"] if r["characteristic"] == evidence["characteristic"])


def reseal_sample(request, index=0, *, calibration=False):
    evidence = request["inspection_evidence"][index]
    row, observation = reading(request, index), evidence["observation"]
    if calibration:
        cref = cid(evidence["calibration"]) if evidence["calibration"] else None
        observation["calibration_ref"] = row["calibration_ref"] = cref
    row["evidence_ref"] = cid(observation)


def rebind_catalog(request):
    """Update content links, not domain declarations, after a deliberate mutation."""
    m, b = request["manufacturing"], request["binding"]
    pref = cid(m["parts"][0])
    m["configuration"]["part_ref"] = b["part_ref"] = pref
    m["routes"][0]["part_ref"] = m["orders"][0]["part_ref"] = pref
    m["orders"][0]["route_ref"] = cid(m["routes"][0])
    cref = cid(m["configuration"])
    m["orders"][0]["configuration_ref"] = m["preflight"]["configuration_ref"] = b["configuration_ref"] = cref
    for study in m["development_runs"]:
        study["configuration_ref"] = cref
    for e in m["events"]:
        if e["kind"] == "cycle":
            e["data"]["configuration_ref"] = cref
    for i, evidence in enumerate(request["inspection_evidence"]):
        if evidence["observation"] is not None:
            evidence["observation"]["part_ref"] = pref
            reseal_sample(request, i)


def quality_policy(request, *, assessed=False, deviation=False):
    """Use the unchanged real tool-point implementation; no stubs or mocks."""
    m = request["manufacturing"]
    m["schema"] = runtime.QC_SCHEMA
    config = m["configuration"]
    sensors = [{"sensor_id": "pressure." + cavity, "quantity": "cavity_pressure", "unit": "Pa",
                "location": {"kind": "near_gate", "cavity_id": cavity, "gate_id": "gate." + cavity}}
               for cavity in config["tool"]["cavity_ids"]]
    plan = {"schema": PLAN_SCHEMA, "plan_id": "synthetic.study.qc", "revision": "A", "source_kind": m["source_kind"],
        "part_ref": config["part_ref"], "configuration_ref": cid(config), "process": "injection_molding",
        "clock_id": m["clock_id"], "frame": request["study"]["identity"]["frame"],
        "valid_from_s": 0, "valid_until_s": 100, "max_clock_skew_s": 0, "max_sample_gap_s": 1,
        "cavity_ids": config["tool"]["cavity_ids"], "reference_evidence_refs": [cid({"synthetic": "qc-limits"})],
        "phases": [{"phase_id": "fill", "start_s": 0, "end_s": 1}], "sensors": sensors,
        "rules": [{"rule_id": sensor["sensor_id"], "sensor_id": sensor["sensor_id"], "phase_id": "fill", "feature": "peak",
                   "limits": {"lower": 0, "upper": 100, "unit": "Pa"}, "threshold": None, "reference_curve": None}
                  for sensor in sensors]}
    m["quality_control"] = {"schema": "ciw.manufacturing-quality-policy.v1", "monitoring_plans": [plan],
        "order_bindings": [{"work_order_id": m["orders"][0]["work_order_id"], "monitoring_plan_ref": cid(plan)}]}
    if not assessed:
        return
    additions = []
    for cycle in m["events"][:2]:
        data = cycle["data"]
        signals = []
        for s in sensors:
            value = 200 if deviation and s["location"]["cavity_id"] == "cavity.1" else 50
            signal = {**deepcopy(s), "frame": plan["frame"], "clock_id": plan["clock_id"],
                "source_ref": None, "calibration_ref": cid({"synthetic": "qc-calibration"}),
                "calibration_valid_from_s": 0, "calibration_valid_until_s": 100,
                "clock_alignment_ref": cid({"synthetic": "clock-alignment"}), "clock_skew_s": 0, "health": "good",
                "samples": [{"time_s": t, "value": value, "expanded_uncertainty": 1} for t in (0, 1)]}
            signal["source_ref"] = signal_ref(signal)
            signals.append(signal)
        observation = {"schema": OBSERVATION_SCHEMA, "source_kind": m["source_kind"], "cycle_id": data["cycle_id"],
            "cycle_evidence_ref": data["cycle_evidence_ref"], "part_ref": config["part_ref"], "configuration_ref": cid(config),
            "clock_id": plan["clock_id"], "frame": plan["frame"], "started_at_s": cycle["at_s"] - 1,
            "completed_at_s": cycle["at_s"], "signals": signals,
            "part_mapping": {"status": "cavity_resolved", "evidence_ref": cid({"synthetic": data["cycle_id"]}),
                             "items": [{"serial": o["serial"], "cavity_id": o["cavity_id"]} for o in data["outputs"]]}}
        additions.append({"event_id": "qc." + data["cycle_id"], "at_s": 2.5, "actor_id": "synthetic.qc",
            "authorization_ref": cid({"synthetic": "no-authority"}), "kind": "quality_assessment",
            "data": {"cycle_id": data["cycle_id"], "observation": observation}})
    m["events"][2:2] = additions


def test_four_dispositions_and_no_parallel_state(request_data):
    original = deepcopy(request_data)
    result = subject.replay(request_data)
    assert request_data == original
    assert result["manufacturing_result"] == runtime.replay(request_data["manufacturing"])
    assert {k: (v["record_quality"], v["record_disposition"]) for k, v in result["study_records"].items()} == {
        "tab001": ("PASS", "AVAILABLE"), "tab002": ("FAIL", "SCRAPPED"),
        "tab003": ("INDETERMINATE", "QUARANTINED"), "tab004": ("INDETERMINATE", "QUARANTINED")}
    assert result["authority"] == AUTHORITY
    assert all(x["shipment_status"] == "HELD" for x in result["study_records"].values())
    inv = result["manufacturing_result"]["inventory"]
    assert Fraction(inv["remaining_mass_kg_decimal"]) == Fraction(4, 10000)
    assert Fraction(inv["allocated_waste_kg_decimal"]) == Fraction(2, 10000)
    assert result["manufacturing_result"]["orders"]["WO-TAB-STUDY"]["accepted_quantity"] == 1


def test_requirements_stay_open_even_when_a_measured_characteristic_passes(request_data):
    coverage = subject.replay(request_data)["study_coverage"]
    assert not coverage["calculation_reexecuted"]
    assert coverage["physical_conformity"] == "not_established"
    assert coverage["assessment_authenticity"] == "not_established"
    requirements = {r["requirement_id"]: r for r in coverage["selected_component_requirements"]}
    assert set(requirements) == {"R-tab-mass", "R-tab-deflection", "R-tab-stress", "R-tab-life"}
    assert all(r["status"] == "PHYSICAL_CONFORMITY_NOT_ESTABLISHED" for r in requirements.values())
    assert requirements["R-tab-mass"]["supporting_characteristics"] == ["mass_kg"]
    assert coverage["other_requirement_ids"] and coverage["process_review_obligations"]


@pytest.mark.parametrize("field", ["study_ref", "assessment_ref", "configuration_ref", "part_ref"])
def test_stale_binding(request_data, field):
    request_data["binding"][field] = cid({"wrong": field})
    with pytest.raises(ValueError):
        subject.replay(request_data)


@pytest.mark.parametrize("field,value", [("candidate_id", "other"), ("work_order_id", "absent")])
def test_absent_candidate_or_order(request_data, field, value):
    request_data["binding"][field] = value
    with pytest.raises(ValueError):
        subject.replay(request_data)


@pytest.mark.parametrize("field,value", [("part_number", "OTHER"), ("revision", "B")])
def test_resealed_part_cannot_substitute_design(request_data, field, value):
    request_data["manufacturing"]["parts"][0][field] = value
    rebind_catalog(request_data)
    with pytest.raises(ValueError, match="Part number or revision"):
        subject.replay(request_data)


def test_resealed_material_cannot_substitute_design(request_data):
    request_data["manufacturing"]["configuration"]["material"]["specification_ref"] = cid({"different": "grade revision"})
    rebind_catalog(request_data)
    with pytest.raises(ValueError, match="substitution"):
        subject.replay(request_data)


def test_component_requirements_cannot_be_dropped(request_data):
    request_data["manufacturing"]["parts"][0]["requirement_refs"].pop()
    rebind_catalog(request_data)
    with pytest.raises(ValueError, match="every selected component requirement"):
        subject.replay(request_data)


@pytest.mark.parametrize("case", ["absent", "duplicate", "unknown_kind", "unknown_target", "wrong_spec", "weak_upper", "excluded_nominal"])
def test_inspection_semantic_links(request_data, case):
    links = request_data["binding"]["inspection_links"]
    plans = request_data["manufacturing"]["parts"][0]["inspection_plan"]
    if case == "absent": links.pop()
    elif case == "duplicate": links.append(deepcopy(links[0]))
    elif case == "unknown_kind": links[0]["kind"] = "automatic_authorization"
    elif case == "unknown_target": links[1]["target"] = "assembly-leak"
    elif case == "wrong_spec": plans[0]["specification_ref"] = cid({"wrong": "spec"})
    elif case == "weak_upper": plans[1]["upper"] = 0.00013
    else: plans[0]["lower"], plans[0]["upper"] = 0.002, 0.003
    rebind_catalog(request_data)
    with pytest.raises(ValueError): subject.replay(request_data)


@pytest.mark.parametrize("field", ["hypothesis_ref", "model_ref", "result_ref"])
def test_substituted_development_reference(request_data, field):
    request_data["manufacturing"]["development_runs"][0][field] = cid({"wrong": field})
    with pytest.raises(ValueError, match="reference substitution"):
        subject.replay(request_data)


def test_cycle_design_reference_not_a_free_form_note(request_data):
    request_data["manufacturing"]["events"][0]["data"]["model_prediction_ref"] = cid({"wrong": "assessment"})
    with pytest.raises(ValueError, match="Cycle is not linked"):
        subject.replay(request_data)


@pytest.mark.parametrize("field", ["serial", "part_ref", "material_lot_id", "cycle_id", "cavity_id", "source_kind", "clock_id", "frame_id", "condition"])
def test_resealed_observation_cannot_change_identity(request_data, field):
    request_data["inspection_evidence"][0]["observation"][field] = "other" if field != "part_ref" else cid({"part": "other"})
    reseal_sample(request_data)
    with pytest.raises(ValueError, match="mismatch"):
        subject.replay(request_data)


@pytest.mark.parametrize("field,value", [("value", 0.0011), ("expanded_uncertainty", 0), ("unit", "mm"),
                                        ("characteristic", "other"), ("calibration_ref", None)])
def test_resealed_observation_cannot_change_reading(request_data, field, value):
    request_data["inspection_evidence"][0]["observation"][field] = value
    reseal_sample(request_data)
    with pytest.raises(ValueError, match="sample differs"):
        subject.replay(request_data)


def test_unsealed_observation_is_rejected(request_data):
    request_data["inspection_evidence"][0]["observation"]["instrument_id"] = "substituted.instrument"
    with pytest.raises(ValueError, match="content reference mismatch"):
        subject.replay(request_data)


def test_reused_sample_id_is_rejected(request_data):
    request_data["inspection_evidence"][2]["observation"]["observation_id"] = request_data["inspection_evidence"][0]["observation"]["observation_id"]
    reseal_sample(request_data, 2)
    with pytest.raises(ValueError, match="cannot be reused"):
        subject.replay(request_data)


@pytest.mark.parametrize("case", ["absent", "duplicate", "extra", "missing_object_but_reference"])
def test_evidence_coverage(request_data, case):
    evidence = request_data["inspection_evidence"]
    if case == "absent": evidence.pop()
    elif case == "duplicate": evidence.append(deepcopy(evidence[0]))
    elif case == "extra": evidence[0]["event_id"] = "not-an-event"
    else: evidence[0]["observation"] = None
    with pytest.raises(ValueError): subject.replay(request_data)


def test_explicit_missing_observation_is_a_hold_not_pass(request_data):
    item = request_data["inspection_evidence"][0]
    row = reading(request_data)
    row["evidence_ref"] = row["calibration_ref"] = None
    item["observation"] = item["calibration"] = None
    r = subject.replay(request_data)["study_records"]["tab001"]
    assert (r["record_quality"], r["record_disposition"]) == ("INDETERMINATE", "QUARANTINED")


@pytest.mark.parametrize("field", ["instrument_id", "source_kind", "unit", "clock_id", "frame_id"])
def test_resealed_calibration_applicability(request_data, field):
    request_data["inspection_evidence"][0]["calibration"][field] = "different"
    reseal_sample(request_data, calibration=True)
    with pytest.raises(ValueError, match="Calibration applicability"):
        subject.replay(request_data)


@pytest.mark.parametrize("low,high", [(0, 2), (4, 100), (100, 0)])
def test_stale_or_reversed_calibration_is_refused(request_data, low, high):
    cal = request_data["inspection_evidence"][0]["calibration"]
    cal["valid_from_s"], cal["valid_until_s"] = low, high
    reseal_sample(request_data, calibration=True)
    with pytest.raises(ValueError, match="not valid"):
        subject.replay(request_data)


def test_closed_calibration_validity_boundary(request_data):
    cal = request_data["inspection_evidence"][0]["calibration"]
    cal["valid_from_s"] = cal["valid_until_s"] = 3
    reseal_sample(request_data, calibration=True)
    assert subject.replay(request_data)["study_records"]["tab001"]["record_quality"] == "PASS"


@pytest.mark.parametrize("time", [0, 3.1, -1, True, float("inf")])
def test_observation_causal_order(request_data, time):
    request_data["inspection_evidence"][0]["observation"]["observed_at_s"] = time
    reseal_sample(request_data) if time != float("inf") else None
    with pytest.raises(ValueError): subject.replay(request_data)


@pytest.mark.parametrize("value", [-1, True, "unknown", float("nan"), float("inf")])
def test_invalid_measurement_uncertainty(request_data, value):
    reading(request_data)["expanded_uncertainty"] = value
    with pytest.raises(ValueError): subject.replay(request_data)


@pytest.mark.parametrize("value", [0.00090, 0.000949, 0.00095, 0.000951, 0.001, 0.001049, 0.00105, 0.001051, 0.00110])
@pytest.mark.parametrize("uncertainty", [0, 0.000001, 0.00001])
def test_independent_fraction_interval_oracle(request_data, value, uncertainty):
    obs = request_data["inspection_evidence"][0]["observation"]
    row = reading(request_data)
    obs["value"] = row["value"] = value
    obs["expanded_uncertainty"] = row["expanded_uncertainty"] = uncertainty
    reseal_sample(request_data)
    v, u, low, high = (Fraction(str(x)) for x in (value, uncertainty, 0.00095, 0.00105))
    expected = "PASS" if low <= v-u and v+u <= high else "FAIL" if v+u < low or v-u > high else "INDETERMINATE"
    actual = subject.replay(request_data)["study_records"]["tab001"]
    assert actual["record_quality"] == expected
    assert actual["record_disposition"] == ("AVAILABLE" if expected == "PASS" else "QUARANTINED")


def test_tiny_positive_uncertainty_cannot_disappear_at_tolerance(request_data):
    obs, row = request_data["inspection_evidence"][0]["observation"], reading(request_data)
    obs["value"] = row["value"] = 0.00105
    obs["expanded_uncertainty"] = row["expanded_uncertainty"] = 1e-320
    reseal_sample(request_data)
    assert subject.replay(request_data)["study_records"]["tab001"]["record_quality"] == "INDETERMINATE"


def test_calibration_reference_without_object_refused(request_data):
    request_data["inspection_evidence"][0]["calibration"] = None
    with pytest.raises(ValueError, match="Unresolved calibration"):
        subject.replay(request_data)


@pytest.mark.parametrize("field", ["production_release", "hardware_actuation", "may_authorize", "physical_qualification"])
def test_resealed_design_authority_promotion_refused(request_data, field):
    request_data["assessment"]["authority"][field] = True
    request_data["binding"]["assessment_ref"] = cid(request_data["assessment"])
    with pytest.raises(ValueError, match="authority escalation"):
        subject.replay(request_data)


def test_fresh_occurrences_and_stable_content(request_data):
    a, b = subject.evaluate(request_data), subject.evaluate(request_data)
    assert a["evaluation_id"] != b["evaluation_id"]
    assert a["request_ref"] == b["request_ref"] and a["result_ref"] == b["result_ref"]
    before = deepcopy(a)
    first, second = subject.verify(a), subject.verify(a)
    assert first["status"] == "PASS" and first["verification_id"] != second["verification_id"]
    assert a == before


@pytest.mark.parametrize("case", ["promote", "clear_hold", "drop_nc", "accepted_quantity", "unsealed_request", "bad_execution_id"])
def test_forged_report_rejected_even_with_resealed_result(request_data, case):
    report = subject.evaluate(request_data)
    result = report["result"]
    if case == "promote": result["authority"]["shipment_release"] = "authorized"
    elif case == "clear_hold": result["study_records"]["tab003"]["record_disposition"] = "AVAILABLE"
    elif case == "drop_nc": result["inspection_nonconformances"] = []
    elif case == "accepted_quantity": result["manufacturing_result"]["orders"]["WO-TAB-STUDY"]["accepted_quantity"] = 4
    elif case == "unsealed_request": report["request"]["binding"]["candidate_id"] = "other"
    else: report["evaluation_id"] = "not-an-execution"
    report["result_ref"] = cid(result)
    with pytest.raises(ValueError): subject.verify(report)


def test_lot_and_serial_trace_keep_measurement_and_scrap_history(request_data):
    report = subject.evaluate(request_data)
    one = subject.trace(report, serial="tab002")
    assert list(one["records"]) == ["tab002"]
    assert one["records"]["tab002"]["disposition"] == "SCRAPPED"
    assert {e["kind"] for e in one["events"]} == {"cycle", "inspection", "scrap"}
    assert len(one["inspection_evidence"]) == 2
    assert one["inspection_nonconformances"][0]["classification"] == "NONCONFORMING"
    all_parts = subject.trace(report, lot=request_data["manufacturing"]["inventory"]["lot_id"])
    assert set(all_parts["records"]) == {"tab001", "tab002", "tab003", "tab004"}
    assert len(all_parts["inspection_nonconformances"]) == 3
    assert all_parts["source_dossier_ref"] == cid(report)


@pytest.mark.parametrize("selection", [{}, {"serial": "absent"}, {"lot": "absent"}, {"serial": "tab001", "lot": "synthetic.lot.1"}])
def test_invalid_trace_selection(request_data, selection):
    with pytest.raises(ValueError): subject.trace(subject.evaluate(request_data), **selection)


def test_qc_missing_process_evidence_does_not_promote_final_pass(request_data):
    quality_policy(request_data)
    result = subject.replay(request_data)
    assert result["study_records"]["tab001"]["record_quality"] == "PASS"
    assert result["study_records"]["tab001"]["record_disposition"] == "QUARANTINED"
    assert result["study_records"]["tab001"]["record_release_gate"]["record_gate"] == "BLOCKED"
    assert result["manufacturing_result"] == runtime.replay(request_data["manufacturing"])


@pytest.mark.parametrize("deviation", [False, True])
def test_real_process_assessment_remains_independent_of_final_inspection(request_data, deviation):
    quality_policy(request_data, assessed=True, deviation=deviation)
    result = subject.replay(request_data)
    assert result["study_records"]["tab001"]["record_quality"] == "PASS"
    assert result["study_records"]["tab001"]["record_disposition"] == ("QUARANTINED" if deviation else "AVAILABLE")
    assert result["study_records"]["tab004"]["record_disposition"] == "QUARANTINED"
    assert result["manufacturing_result"]["shipment"]["status"] == "HELD"
    traced = subject.trace(subject.evaluate(request_data), serial="tab001")
    assert any(e["kind"] == "quality_assessment" for e in traced["events"])


def test_late_manual_quality_hold_preserves_design_and_observations(request_data):
    quality_policy(request_data, assessed=True)
    m = request_data["manufacturing"]
    m["events"].append({"event_id": "manual-hold", "at_s": 8, "actor_id": "synthetic.inspector",
        "authorization_ref": cid({"synthetic": "authorization"}), "kind": "quality_hold",
        "data": {"serial": "tab001", "reason": "Visible damage requiring review", "evidence_ref": cid({"synthetic": "damage"})}})
    result = subject.replay(request_data)
    assert result["study_records"]["tab001"]["record_disposition"] == "QUARANTINED"
    assert result["study_records"]["tab001"]["record_quality"] == "PASS"
    assert result["manufacturing_result"]["quality_control"]["nonconformances"]


@pytest.mark.parametrize("case", ["lot_swap", "duplicate_serial", "duplicate_cavity", "overspend", "mass_imbalance", "repeat_inspection", "release_event"])
def test_manufacturing_invariants_remain_enforced(request_data, case):
    m = request_data["manufacturing"]
    cycle = m["events"][0]["data"]
    if case == "lot_swap": cycle["material_lot_id"] = "other-lot"
    elif case == "duplicate_serial": cycle["outputs"][1]["serial"] = cycle["outputs"][0]["serial"]
    elif case == "duplicate_cavity": cycle["outputs"][1]["cavity_id"] = cycle["outputs"][0]["cavity_id"]
    elif case == "overspend": m["inventory"]["mass"]["value"] = 0.0001
    elif case == "mass_imbalance": cycle["issued_mass"]["value"] = 0.0004
    elif case == "repeat_inspection":
        event = deepcopy(m["events"][2]); event["event_id"] = "repeat"; event["at_s"] = 8; m["events"].append(event)
    else:
        event = deepcopy(m["events"][-1]); event["event_id"] = "release"; event["kind"] = "release"; event["at_s"] = 8; m["events"].append(event)
    with pytest.raises(ValueError): subject.replay(request_data)


def cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "ciw.polymer_manufacturing_cli", "study", *map(str, args)],
                          cwd=cwd, capture_output=True, text=True, timeout=20)


def test_real_manufacturing_cli_example_run_verify_and_trace(tmp_path):
    req, result, verification, lot = (tmp_path / name for name in ("request.json", "report.json", "verify.json", "lot.json"))
    assert cli("example", "--study", EXAMPLES / "study.json", "--assessment", EXAMPLES / "assessment.json", "--output", req).returncode == 0
    run = cli("run", req, "--output", result)
    assert run.returncode == 0, run.stderr
    assert cli("verify", result, "--output", verification).returncode == 0
    assert load(verification)["status"] == "PASS"
    assert cli("trace", result, "--lot", "synthetic.lot.1", "--output", lot).returncode == 0
    assert len(load(lot)["records"]) == 4
    before = result.read_bytes()
    refused = cli("run", req, "--output", result)
    assert refused.returncode == 2 and result.read_bytes() == before
    assert "REFUSED" in refused.stderr
    assert json.loads(run.stdout)["shipment_release"] == "not_authorized"


@pytest.mark.parametrize("payload", ['{"schema":1,"schema":2}', '{"schema":NaN}', '{"value":1e-9999}', '[]', 'null'])
def test_cli_rejects_malformed_or_wrong_json(tmp_path, payload):
    source, dest = tmp_path / "bad.json", tmp_path / "out.json"
    source.write_text(payload)
    response = cli("run", source, "--output", dest)
    assert response.returncode == 2 and not dest.exists()


def test_api_refuses_unknown_fields(request_data):
    request_data["authorize"] = True
    with pytest.raises(ValueError): subject.replay(request_data)


def rebind_assessment(request):
    previous = request["binding"]["assessment_ref"]
    current = cid(request["assessment"])
    request["binding"]["assessment_ref"] = current
    m = request["manufacturing"]
    m["configuration"]["development_refs"] = [current if r == previous else r for r in m["configuration"]["development_refs"]]
    m["development_runs"][0]["result_ref"] = current
    for event in m["events"]:
        if event["kind"] == "cycle": event["data"]["model_prediction_ref"] = current
    rebind_catalog(request)


@pytest.mark.parametrize("case", ["drop_requirement", "change_limit", "physical_promotion", "process_approval", "bad_calculation_status"])
def test_coherently_resealed_assessment_cannot_suppress_obligations(request_data, case):
    selected = next(c for c in request_data["assessment"]["candidates"] if c["candidate_id"] == "tab-reference")
    if case == "drop_requirement": selected["requirements"].pop()
    elif case == "change_limit": selected["requirements"][0]["limit"] = 1.0
    elif case == "physical_promotion": selected["requirements"][0]["physical_conformity"] = "established"
    elif case == "process_approval": selected["process_review_obligations"][0]["status"] = "SATISFIED"
    else: selected["status"] = "RELEASED"
    rebind_assessment(request_data)
    with pytest.raises(ValueError): subject.replay(request_data)


def test_rehashed_wrong_calculation_is_not_claimed_as_independently_verified(request_data):
    selected = next(c for c in request_data["assessment"]["candidates"] if c["candidate_id"] == "tab-reference")
    selected["numerical_metrics"]["tip_deflection_m"] = 0.0
    rebind_assessment(request_data)
    result = subject.replay(request_data)
    # This adapter binds content, but does not run the original numerical verifier.
    assert not result["study_coverage"]["calculation_reexecuted"]
    assert result["study_coverage"]["physical_conformity"] == "not_established"
    assert result["authority"]["shipment_release"] == "not_authorized"
