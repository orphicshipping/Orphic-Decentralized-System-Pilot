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
