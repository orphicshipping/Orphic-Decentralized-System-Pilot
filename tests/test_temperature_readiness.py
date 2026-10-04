"""Pilot declaration readiness is separate from physical evidence adequacy."""
from copy import deepcopy
import json

import pytest

from ciw.operations.runner import digest
from ciw.temperature_readiness import AUTHORITY, FIELDS, assess_plan, main


def empty_plan():
    plan = {section: {field: None for field in fields} for section, fields in FIELDS.items()}
    plan.update(schema="ciw.temperature-pilot-plan.v1", status="synthetic_fixture",
                purpose="Software test only", evidence_origin="synthetic_fixture",
                physical_qualification="not_established")
    plan["measurand"]["reported_unit"] = "K"
    plan["optional_sp1"].update(status="not_implemented", physical_qualification_inferred=False)
    return plan


def complete_plan():
    plan = empty_plan()
    for section, fields in FIELDS.items():
        if section in {"product_conformity_assessment", "optional_sp1"}:
            continue
        for field in fields:
            plan[section][field] = "Synthetic declaration only"
    plan["measurand"].update(reported_unit="K", optional_model_output_target=None)
    plan["acceptance_criteria"].update(required_uncertainty_K=2.0,
                                      reproducibility_limit_K=1.0,
                                      pinn_gpinn_improvement_or_noninferiority_margin=None)
    plan["operating_envelope"].update(
        temperature_range_K=[320.0, 350.0], heating_and_cooling_rate_range_K_per_s=[-0.1, 0.1],
        sampling_interval_s=0.2, allowed_timing_uncertainty_s=0.01)
    return plan


def test_missing_setup_reference_and_evidence_are_explicit():
    plan = empty_plan()
    report = assess_plan(plan)
    assert report["status"] == "incomplete"
    assert "measurement_chain.sensor_type_model_serial" in report["stages"]["setup_definition"]["missing_fields"]
    assert "operating_envelope.temperature_range_K" in report["stages"]["setup_definition"]["missing_fields"]
    assert "acceptance_criteria.required_uncertainty_K" in report["stages"]["setup_definition"]["missing_fields"]
    assert "measurement_chain.reference_instrument_and_calibration_chain" in report["stages"]["qualification_evidence_inventory"]["missing_fields"]
    assert "uncertainty_evidence.joint_raw_gain_offset_covariance_record" in report["stages"]["qualification_evidence_inventory"]["missing_fields"]
    assert report["authority"] == AUTHORITY


def test_complete_synthetic_declarations_never_become_physical_evidence():
    plan = complete_plan()
    original = deepcopy(plan)
    report = assess_plan(plan)
    assert report["status"] == "declarations_complete_for_review"
    assert report["evidence_origin"] == "synthetic_fixture"
    assert report["authority"] == AUTHORITY
    assert report["plan_ref"] == digest(plan)
    assert report == assess_plan(plan)
    assert plan == original
    assert report["optional"]["model_comparison"]["status"] == "not_requested"
    assert report["optional"]["temperature_sp1"]["status"] == "not_requested"


@pytest.mark.parametrize("mode", ["field", "section", "partial_record", "placeholder"])
def test_missing_or_incomplete_records_are_not_hidden_by_other_content(mode):
    plan = complete_plan()
    target = "measurement_chain.sensor_type_model_serial"
    if mode == "field":
        del plan["measurement_chain"]["sensor_type_model_serial"]
    elif mode == "section":
        del plan["measurement_chain"]
    elif mode == "partial_record":
        plan["measurement_chain"]["sensor_type_model_serial"] = {
            "published_type": "K", "installed_serial": None}
    else:
        plan["measurement_chain"]["sensor_type_model_serial"] = " pending "
    report = assess_plan(plan)
    assert report["status"] == "incomplete"
    assert target in report["stages"]["setup_definition"]["missing_fields"]


@pytest.mark.parametrize("field,value", [
    ("temperature_range_K", [350.0, 320.0]),
    ("temperature_range_K", [0.0, 350.0]),
    ("temperature_range_K", [True, 350.0]),
    ("temperature_range_K", "50 to 80 Celsius"),
    ("heating_and_cooling_rate_range_K_per_s", [0.1, -0.1]),
    ("sampling_interval_s", 0.0),
    ("sampling_interval_s", True),
    ("allowed_timing_uncertainty_s", -0.01),
])
def test_invalid_numeric_planning_fields_cannot_appear_complete(field, value):
    plan = complete_plan()
    plan["operating_envelope"][field] = value
    report = assess_plan(plan)
    assert report["status"] == "incomplete"
    assert any(row["field"] == "operating_envelope." + field
               for row in report["stages"]["setup_definition"]["invalid_fields"])


def test_zero_timing_uncertainty_is_a_declaration_not_a_measured_bound():
    plan = complete_plan()
    plan["operating_envelope"]["allowed_timing_uncertainty_s"] = 0.0
    report = assess_plan(plan)
    assert report["status"] == "declarations_complete_for_review"
    assert report["authority"] == AUTHORITY


@pytest.mark.parametrize("value", [0, -1, True, "2 K"])
def test_required_uncertainty_has_explicit_positive_numeric_units(value):
    plan = complete_plan()
    plan["acceptance_criteria"]["required_uncertainty_K"] = value
    report = assess_plan(plan)
    assert any(row["field"] == "acceptance_criteria.required_uncertainty_K"
               for row in report["stages"]["setup_definition"]["invalid_fields"])


@pytest.mark.parametrize("mode", ["schema", "unknown", "section_type", "authority", "unit", "proof_authority", "nonfinite"])
def test_malformed_or_unsupported_contract_refuses(mode):
    plan = complete_plan()
    if mode == "schema":
        plan["schema"] = "different"
    elif mode == "unknown":
        plan["measurement_chain"]["typo_sensor"] = "K"
    elif mode == "section_type":
        plan["measurement_chain"] = []
    elif mode == "authority":
        plan["physical_qualification"] = "established"
    elif mode == "unit":
        plan["measurand"]["reported_unit"] = "degC"
    elif mode == "proof_authority":
        plan["optional_sp1"]["physical_qualification_inferred"] = True
    else:
        plan["acceptance_criteria"]["required_uncertainty_K"] = float("nan")
    with pytest.raises(ValueError):
        assess_plan(plan)


def test_optional_model_and_proof_fields_are_separate_unfinished_work():
    plan = complete_plan()
    plan["measurand"]["optional_model_output_target"] = "Synthetic local polymer temperature comparison"
    plan["optional_sp1"]["status"] = "proposed"
    report = assess_plan(plan)
    model = report["optional"]["model_comparison"]
    proof = report["optional"]["temperature_sp1"]
    assert model["status"] == proof["status"] == "incomplete"
    assert "acceptance_criteria.pinn_gpinn_improvement_or_noninferiority_margin" in model["missing_fields"]
    assert "optional_sp1.temperature_guest_identity" in proof["missing_fields"]
    assert report["authority"]["proof"] == "not_established"


def test_evidence_references_are_not_opened_or_authenticated(monkeypatch):
    plan = complete_plan()
    plan["measurement_chain"]["calibration_record"] = "https://example.invalid/not-a-certificate"
    from pathlib import Path

    def forbidden(*args, **kwargs):
        raise AssertionError("Readiness assessment opened a referenced artifact")

    monkeypatch.setattr(Path, "open", forbidden)
    report = assess_plan(plan)
    assert report["status"] == "declarations_complete_for_review"
    assert report["authority"]["evidence_authentication"] == "not_performed"


def test_cli_retains_missing_fields_without_overwriting_inputs_or_reports(tmp_path, capsys):
    plan = empty_plan()
    source = tmp_path / "setup.json"
    output = tmp_path / "readiness.json"
    source.write_text(json.dumps(plan), encoding="utf-8")
    original = source.read_bytes()
    assert main(["--setup", str(source), "--output", str(output)]) == 1
    first = output.read_bytes()
    assert json.loads(capsys.readouterr().out)["status"] == "incomplete"
    assert source.read_bytes() == original
    assert main(["--setup", str(source), "--output", str(output)]) == 2
    assert "refused" in capsys.readouterr().err
    assert output.read_bytes() == first


def test_cli_duplicate_json_fields_refuse_without_creating_report(tmp_path, capsys):
    source = tmp_path / "setup.json"
    output = tmp_path / "readiness.json"
    source.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    assert main(["--setup", str(source), "--output", str(output)]) == 2
    assert not output.exists()
    assert "refused" in capsys.readouterr().err



@pytest.mark.parametrize("marker", [
    "not supplied", "NOT-SUPPLIED", "not  implemented", "No Experimental Data Supplied",
    "  unknown  ", "not\t supplied",
])
def test_exact_placeholder_variants_remain_missing_inside_nested_records(marker):
    plan = complete_plan()
    plan["measurement_chain"]["sensor_type_model_serial"] = {
        "published_type": "K", "installation": [{"serial": marker}]}
    original = deepcopy(plan)
    report = assess_plan(plan)
    assert report["status"] == "incomplete"
    assert "measurement_chain.sensor_type_model_serial" in report["stages"]["setup_definition"]["missing_fields"]
    assert report["plan_ref"] == digest(original)
    assert plan == original


@pytest.mark.parametrize("text", [
    "Unknown manufacturer code is preserved alongside the serial",
    "Not supplied by the publisher; inspected specimen identity is recorded separately",
    "Pending review of this complete declaration",
])
def test_placeholder_matching_does_not_reject_substantive_explanatory_text(text):
    plan = complete_plan()
    plan["measurement_chain"]["sensor_type_model_serial"] = text
    report = assess_plan(plan)
    assert report["status"] == "declarations_complete_for_review"
    assert report["authority"] == AUTHORITY


@pytest.mark.parametrize("origin", ["", "  ", "unknown", "Not Supplied", "NOT-IMPLEMENTED"])
def test_evidence_origin_requires_more_than_a_placeholder(origin):
    plan = complete_plan()
    plan["evidence_origin"] = origin
    with pytest.raises(ValueError, match="non-placeholder evidence origin"):
        assess_plan(plan)


@pytest.mark.parametrize("selection", ["partial_target", "partial_margin_with_zero", "invalid_target_false"])
def test_optional_model_intent_cannot_disappear_in_an_incomplete_or_invalid_declaration(selection):
    plan = complete_plan()
    if selection == "partial_target":
        plan["measurand"]["optional_model_output_target"] = {
            "quantity": "Local polymer temperature", "coordinate_frame": None}
    elif selection == "partial_margin_with_zero":
        plan["acceptance_criteria"]["pinn_gpinn_improvement_or_noninferiority_margin"] = {
            "margin_K": 0, "frozen_decision": None}
    else:
        plan["measurand"]["optional_model_output_target"] = False
    report = assess_plan(plan)
    model = report["optional"]["model_comparison"]
    assert model["requested"] is True
    assert model["status"] == report["status"] == "incomplete"
    if selection == "invalid_target_false":
        assert any(row["field"] == "measurand.optional_model_output_target" for row in model["invalid_fields"])
    else:
        assert "measurand.optional_model_output_target" in model["missing_fields"]
    assert "acceptance_criteria.pinn_gpinn_improvement_or_noninferiority_margin" in model["missing_fields"]
    assert report["authority"] == AUTHORITY


@pytest.mark.parametrize("target", [{"quantity": None}, ["pending", None], {"nested": []}])
def test_unfilled_optional_templates_and_shared_validation_records_do_not_select_a_model(target):
    plan = complete_plan()
    plan["measurand"]["optional_model_output_target"] = target
    # The complete fixture already supplies shared split, OOD and discrepancy
    # declarations for its conventional workflow; these do not select a PINN.
    report = assess_plan(plan)
    assert report["optional"]["model_comparison"]["requested"] is False
    assert report["optional"]["model_comparison"]["status"] == "not_requested"
    assert report["status"] == "declarations_complete_for_review"


@pytest.mark.parametrize("status", ["not_requested", "not requested", "not-applicable", "disabled", "not implemented"])
def test_explicit_inactive_sp1_status_does_not_require_a_guest(status):
    plan = complete_plan()
    plan["optional_sp1"]["status"] = status
    plan["optional_sp1"]["claim"] = "specified_processing_of_committed_inputs_only"
    report = assess_plan(plan)
    assert report["optional"]["temperature_sp1"]["status"] == "not_requested"
    assert report["status"] == "declarations_complete_for_review"
    assert report["authority"] == AUTHORITY


@pytest.mark.parametrize("status", ["not_requested", "disabled", "not_implemented"])
def test_partial_proof_records_are_not_hidden_by_an_inactive_sp1_status(status):
    plan = complete_plan()
    plan["optional_sp1"].update(status=status, temperature_guest_identity={
        "program": "Proposed bounded temperature guest", "digest": None})
    report = assess_plan(plan)
    proof = report["optional"]["temperature_sp1"]
    assert proof["requested"] is True
    assert proof["status"] == report["status"] == "incomplete"
    assert "optional_sp1.temperature_guest_identity" in proof["missing_fields"]
    assert "optional_sp1.genuine_build_prove_verify_evidence" in proof["missing_fields"]
    assert report["authority"]["proof"] == "not_established"


@pytest.mark.parametrize("status", [False, [], {"status": "not_requested"}])
def test_optional_sp1_status_cannot_hide_a_malformed_status_type(status):
    plan = complete_plan()
    plan["optional_sp1"]["status"] = status
    with pytest.raises(ValueError, match="SP1 status must be text or null"):
        assess_plan(plan)


@pytest.mark.parametrize("mode,exit_code", [("complete", 0), ("missing", 1), ("invalid", 1)])
def test_cli_summary_preserves_sealed_json_identity_and_assesses_once(mode, exit_code, tmp_path, capsys, monkeypatch):
    from ciw import temperature_readiness as readiness

    plan = empty_plan() if mode == "missing" else complete_plan()
    if mode == "invalid":
        plan["operating_envelope"]["sampling_interval_s"] = 0
    source = tmp_path / "setup.json"
    ordinary_output = tmp_path / "ordinary.json"
    summary_output = tmp_path / "summary.json"
    source.write_text(json.dumps(plan), encoding="utf-8")
    assess = readiness.assess_plan
    calls = []

    def counted(value):
        calls.append(value)
        return assess(value)

    monkeypatch.setattr(readiness, "assess_plan", counted)
    assert main(["--setup", str(source), "--output", str(ordinary_output)]) == exit_code
    ordinary = json.loads(capsys.readouterr().out)
    assert len(calls) == 1
    assert main(["--setup", str(source), "--output", str(summary_output), "--summary"]) == exit_code
    summary = capsys.readouterr().out
    assert len(calls) == 2
    assert summary_output.read_bytes() == ordinary_output.read_bytes()
    assert ordinary["plan_ref"] in summary
    assert ordinary["record_digest"] in summary
    assert "physical_qualification=not_established" in summary
    assert "calibration_traceability=not_established" in summary
    assert "authorization_to_operate=not_granted" in summary
    assert "no evidence adequacy, instrument or experimental review" in summary
    assert "Temperature sp1: not_requested" in summary
    assert "Supply declaration: optional_sp1.temperature_guest_identity" not in summary
    if mode == "missing":
        assert "Supply declaration: measurement_chain.sensor_type_model_serial" in summary
    elif mode == "invalid":
        assert "Correct declaration: operating_envelope.sampling_interval_s" in summary
        assert "positive finite number" in summary
    else:
        assert "Supply declaration:" not in summary
        assert "Correct declaration:" not in summary


def test_cli_summary_contract_refusal_does_not_create_a_report(tmp_path, capsys):
    plan = complete_plan()
    plan["evidence_origin"] = "unknown"
    source = tmp_path / "setup.json"
    output = tmp_path / "readiness.json"
    source.write_text(json.dumps(plan), encoding="utf-8")
    assert main(["--setup", str(source), "--output", str(output), "--summary"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "non-placeholder evidence origin" in captured.err
    assert not output.exists()
