"""Adapter checks using generated data only; these tests are not physical evidence."""
from __future__ import annotations

import copy
from hashlib import sha256
import json
import math
from pathlib import Path

import pytest

from ciw.fluid_thermal_measurements import (
    CALIBRATION_SCHEMA,
    SCHEMA,
    Campaign,
    adapt_campaign,
    main,
    write_templates,
)


def content_hash(raw):
    return "sha256:" + sha256(raw).hexdigest()


class SyntheticCampaign:
    """Reusable file-backed fixture. Every value and certificate is fabricated."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        self.path = self.root / "campaign.json"
        self.calibrations = {}
        channels = {}
        certificate = self.write(
            "certificate.txt",
            b"SYNTHETIC TEST FIXTURE. No measurement or calibration occurred.\r\n",
        )
        for role in ("observation", "reference"):
            cal = {
                "schema": CALIBRATION_SCHEMA,
                "calibration_id": "synthetic-" + role,
                "instrument_id": "synthetic-probe-" + role,
                "logger_id": "synthetic-logger-" + role,
                "input_unit": "degC",
                "input_min": 5.0,
                "input_max": 80.0,
                "scale": 1.0,
                "offset_k": 0.0,
                "coefficient_covariance": [[0.0, 0.0], [0.0, 0.0]],
                "residual_standard_uncertainty_k": 0.02,
                "valid_from_utc": "2026-01-01T00:00:00Z",
                "valid_until_utc": "2026-12-31T00:00:00Z",
                "certificate": certificate,
                "traceability_statement": "Synthetic software test only; no metrological traceability.",
                "fit_run_ids": ["synthetic-calibration-run"],
            }
            self.calibrations[role] = cal
            channels[role] = {
                "instrument_id": cal["instrument_id"],
                "logger_id": cal["logger_id"],
                "location": "synthetic-" + role,
                "calibration": self.write_json(role + "-calibration.json", cal),
            }
        readings = b"timestamp_s,temperature,standard_uncertainty,quality\r\n"
        obs = self.write("observation.csv", readings + b"0,20,0.03,valid\r\n1,21,0.03,valid\r\n2,22,0.03,valid\r\n")
        ref = self.write("reference.csv", readings + b"0,20.1,0.03,valid\r\n1,21.1,0.03,valid\r\n2,22.1,0.03,valid\r\n")
        estimates = self.write("estimates.csv", b"timestamp_s,temperature_k\r\n0,293.45\r\n1,294.45\r\n2,295.45\r\n")
        model_artifact = self.write("model.txt", b"synthetic-model-for-tests-only")
        budget_evidence = self.write("uncertainty-review.txt", b"Synthetic uncertainty-budget test declaration only.")
        self.manifest = {
            "schema": SCHEMA,
            "campaign_id": "synthetic-software-test-only",
            "evidence_kind": "synthetic",
            "scope": {
                "fluid": "water", "phase": "liquid", "measurand": "local_fluid_temperature",
                "installation_id": "synthetic-installation", "mixing_description": "Invented fixture; no apparatus exists.",
                "min_temperature_k": 278.15, "max_temperature_k": 353.15,
                "min_pressure_pa": 90000, "max_pressure_pa": 110000,
            },
            "uncertainty_budget": {"assumption": "independent_disjoint_components", "reviewed_by": "synthetic-test-actor", "evidence": budget_evidence},
            "channels": channels,
            "runs": [{
                "run_id": "synthetic-held", "split": "held_out", "phase": "heating",
                "fluid_batch_id": "synthetic-water-batch",
                "acquisition_start_utc": "2026-06-01T12:00:00Z",
                "acquisition_end_utc": "2026-06-01T12:00:02Z",
                "pressure_pa": 101325,
                "observation": obs, "reference": ref, "estimates": estimates,
                "reference_extra_uncertainty_k": {"installation": 0.01, "timing": 0.02, "dynamic": 0.04},
            }],
            "held_out_policy": {
                "development_run_ids": [], "reserved_run_ids": ["synthetic-held"],
                "model_sha256": model_artifact["sha256"],
                "model_artifact": model_artifact,
                "prediction_sha256_by_run": {"synthetic-held": estimates["sha256"]},
                "criteria_sha256": None,
                "split_frozen_at_utc": "2026-05-01T00:00:00Z",
                "model_frozen_at_utc": "2026-05-02T00:00:00Z",
                "predictions_frozen_at_utc": "2026-06-01T12:00:03Z",
                "reference_unsealed_at_utc": "2026-06-01T12:00:04Z",
                "reference_used_for_fit": False,
                "independence_statement": "Synthetic test declaration; no actual independent laboratory evidence.",
                "reviewed_by": "synthetic-test-actor",
            },
            "criteria": {
                "status": "agreed", "approved_by": "synthetic-test-actor",
                "approved_at_utc": "2026-05-01T00:00:00Z",
                "decision_rule": "conservative_pointwise_reference_guard", "coverage_factor": 2,
                "min_held_out_runs": 1, "required_phases": ["heating"], "min_scored_fraction": 1,
                "min_temperature_span_k": 1, "max_abs_bias_k": 1,
                "max_rmse_k": 1, "max_abs_error_k": 1,
                "max_reference_expanded_uncertainty_k": 1,
            },
        }
        self.save()

    @property
    def run(self):
        return self.manifest["runs"][0]

    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        return {"file": name, "sha256": content_hash(raw)}

    def write_json(self, name, value):
        return self.write(name, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode())

    def save(self):
        policy = self.manifest["held_out_policy"]
        if policy is not None:
            policy["criteria_sha256"] = content_hash((json.dumps(self.manifest["criteria"], indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
        self.write_json("campaign.json", self.manifest)
        return self.path

    def change_calibration(self, role, **changes):
        self.calibrations[role].update(changes)
        self.manifest["channels"][role]["calibration"] = self.write_json(
            role + "-calibration.json", self.calibrations[role]
        )

    def replace_readings(self, role, rows):
        self.run[role] = self.write(
            role + ".csv",
            ("timestamp_s,temperature,standard_uncertainty,quality\n" + rows).encode(),
        )

    def evaluate(self):
        self.save()
        return Campaign(self.path).evaluate()

    def pretend_measured_for_control_flow_test(self):
        # Deliberately exercises the declared-measured branch with fabricated data.
        # The campaign ID, certificate, declarations, and test module retain its origin.
        self.manifest["evidence_kind"] = "measured"


@pytest.fixture
def campaign(tmp_path):
    return SyntheticCampaign(tmp_path / "synthetic-campaign")


def test_default_synthetic_fixture_cannot_qualify(campaign):
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert "synthetic_data_cannot_establish_physical_validation" in report["blockers"]
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"


def test_all_declared_checks_pass_without_asserting_physical_validation(campaign):
    campaign.pretend_measured_for_control_flow_test()
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "passed_declared_criteria"
    assert report["evidence_status"] == "eligible_for_review"
    assert report["blockers"] == []
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert report["evidence_authentication"] == "operator_assertions_and_local_hashes_only"
    metric = report["per_run_metrics"][0]
    assert metric["sample_count"] == 3
    assert metric["bias_k"] == pytest.approx(0.2)
    u = math.sqrt(0.03**2 + 0.02**2 + 0.01**2 + 0.02**2 + 0.04**2)
    assert metric["max_reference_standard_uncertainty_k"] == pytest.approx(u)
    for field in ("guarded_abs_bias_k", "guarded_rmse_k", "guarded_max_abs_error_k"):
        assert metric[field] == pytest.approx(0.2 + 2 * u)


@pytest.mark.parametrize("unit,raw", [("degC", 20), ("K", 293.15)])
def test_affine_calibration_and_full_covariance_propagation(campaign, unit, raw):
    campaign.change_calibration(
        "observation", input_unit=unit, input_min=0, input_max=400, scale=1.001,
        offset_k=-0.4, coefficient_covariance=[[1e-6, -0.0002], [-0.0002, 0.09]],
    )
    campaign.replace_readings("observation", f"0,{raw},0.03,valid\n1,{raw + 1},0.03,valid\n2,{raw + 2},0.03,valid\n")
    _, inference, _ = campaign.evaluate()
    first = inference["runs"][0]["observations"][0]
    x = 293.15
    expected_variance = x*x*1e-6 + 2*x*(-0.0002) + 0.09 + (1.001*0.03)**2 + 0.02**2
    assert first["raw_temperature"] == raw
    assert first["temperature_k"] == pytest.approx(1.001*x - 0.4)
    assert first["standard_uncertainty_k"] == pytest.approx(math.sqrt(expected_variance))


def test_missing_samples_preserved_without_interpolation(campaign):
    campaign.replace_readings("observation", "0,20,0.03,valid\n1,,,missing\n2,22,0.03,valid\n")
    campaign.replace_readings("reference", "0,20.1,0.03,valid\n1,,,missing\n2,22.1,0.03,valid\n")
    report, inference, evaluation = campaign.evaluate()
    obs = inference["runs"][0]["observations"][1]
    assert obs["raw_temperature"] is None and obs["temperature_k"] is None
    assert obs["raw_standard_uncertainty"] is None and obs["standard_uncertainty_k"] is None
    assert evaluation["runs"][0]["reference"][1]["quality"] == "missing"
    assert report["per_run_metrics"][0]["sample_count"] == 2
    assert report["per_run_metrics"][0]["scored_fraction"] == pytest.approx(2 / 3)


def test_all_missing_reference_is_blocked(campaign):
    campaign.replace_readings("reference", "0,,,missing\n1,,,missing\n2,,,missing\n")
    report, _, _ = campaign.evaluate()
    assert "synthetic-held:no_valid_reference_samples" in report["blockers"]
    assert report["evaluated_run_count"] == 0


def test_bundle_preserves_exact_raw_bytes_and_separates_reference(campaign, tmp_path):
    original = {p.name: p.read_bytes() for p in campaign.root.iterdir()}
    report = adapt_campaign(campaign.path, tmp_path / "bundle")
    bundle = tmp_path / "bundle"
    assert (bundle / "campaign.json").read_bytes() == original["campaign.json"]
    for name, raw in original.items():
        if name != "campaign.json":
            assert (bundle / "raw" / (sha256(raw).hexdigest() + ".bin")).read_bytes() == raw
    inference = json.loads((bundle / "inference-input.json").read_bytes())
    evaluation = json.loads((bundle / "reference-evaluation.json").read_bytes())
    assert set(inference["runs"][0]) == {"run_id", "split", "observation_sha256", "observations"}
    assert "reference" not in json.dumps(inference)
    assert "reference" in evaluation["runs"][0]
    assert report["manifest_sha256"] == content_hash(original["campaign.json"])


def test_reference_changes_do_not_change_inference_inputs(campaign):
    _, before, _ = campaign.evaluate()
    campaign.replace_readings("reference", "0,29.1,0.03,valid\n1,30.1,0.03,valid\n2,31.1,0.03,valid\n")
    _, after, evaluation = campaign.evaluate()
    assert before == after
    assert evaluation["runs"][0]["reference"][0]["temperature_k"] == pytest.approx(302.25)


@pytest.mark.parametrize("criteria", [None, "draft"])
def test_no_agreed_criteria_means_no_qualification(campaign, criteria):
    campaign.pretend_measured_for_control_flow_test()
    if criteria is None:
        campaign.manifest["criteria"] = None
    else:
        campaign.manifest["criteria"]["status"] = criteria
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert "agreed_criteria_missing" in report["blockers"]


@pytest.mark.parametrize("field,limit", [
    ("max_abs_bias_k", 0.25), ("max_rmse_k", 0.25), ("max_abs_error_k", 0.25),
    ("max_reference_expanded_uncertainty_k", 0.01), ("min_temperature_span_k", 3),
])
def test_declared_threshold_failure(campaign, field, limit):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["criteria"][field] = limit
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "failed_declared_criteria"
    assert not report["per_run_metrics"][0]["criteria_passed"]
    assert report["physical_validation"] == "not_established"


@pytest.mark.parametrize("field,value", [("min_held_out_runs", 2), ("required_phases", ["cooling"])])
def test_run_and_condition_coverage_is_required(campaign, field, value):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["criteria"][field] = value
    report, _, _ = campaign.evaluate()
    assert "required_held_out_run_or_condition_coverage_missing" in report["blockers"]
    assert report["criteria_result"] == "not_evaluated"


@pytest.mark.parametrize("rows", [
    "0,NaN,0.03,valid\n", "0,20,inf,valid\n", "nan,20,0.03,valid\n",
    "0,20,0.03,valid\n0,21,0.03,valid\n", "1,20,0.03,valid\n0,21,0.03,valid\n",
    "0,20,0.03,valid,extra\n", "0,20,0.03\n", "0,20,0.03,suspect\n",
    "0,20,,missing\n", "0,,,valid\n", "", "3,20,0.03,valid\n",
])
def test_invalid_reading_csv_is_rejected(campaign, rows):
    campaign.replace_readings("observation", rows)
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_wrong_csv_column_order_is_rejected(campaign):
    campaign.run["observation"] = campaign.write("observation.csv", b"temperature,timestamp_s,standard_uncertainty,quality\n20,0,0.03,valid\n")
    with pytest.raises(ValueError, match="ordered columns"):
        campaign.evaluate()


def test_reference_timestamps_must_match_without_hidden_alignment(campaign):
    campaign.replace_readings("reference", "0,20.1,0.03,valid\n1.1,21.1,0.03,valid\n2,22.1,0.03,valid\n")
    with pytest.raises(ValueError, match="common timestamps"):
        campaign.evaluate()


def test_tampered_bytes_rejected_before_output_creation(campaign, tmp_path):
    (campaign.root / "observation.csv").write_bytes(b"tampered")
    output = tmp_path / "refused-bundle"
    with pytest.raises(ValueError, match="hash mismatch"):
        adapt_campaign(campaign.path, output)
    assert not output.exists()


@pytest.mark.parametrize("raw", [b'{"schema": 1, "schema": 2}', b'{"schema": NaN}', b'{"schema": Infinity}', b'{invalid'])
def test_malformed_and_nonfinite_json_rejected(campaign, raw):
    campaign.path.write_bytes(raw)
    with pytest.raises(ValueError):
        Campaign(campaign.path).evaluate()


def test_local_evidence_cannot_escape_campaign_directory(campaign, tmp_path):
    raw = b"outside campaign"
    outside = tmp_path / "outside.csv"
    outside.write_bytes(raw)
    campaign.run["observation"] = {"file": "../outside.csv", "sha256": content_hash(raw)}
    with pytest.raises(ValueError, match="inside the campaign directory"):
        campaign.evaluate()


@pytest.mark.parametrize("hash_value", ["0"*64, "sha256:" + "A"*64, "sha256:bad"])
def test_malformed_content_identity_rejected(campaign, hash_value):
    campaign.run["observation"]["sha256"] = hash_value
    with pytest.raises(ValueError, match="SHA-256"):
        campaign.evaluate()


def test_observation_cannot_be_its_own_reference(campaign):
    campaign.run["reference"] = dict(campaign.run["observation"])
    with pytest.raises(ValueError, match="duplicate observation"):
        campaign.evaluate()


def test_renaming_observation_bytes_does_not_create_held_out_run(campaign):
    other = copy.deepcopy(campaign.run)
    other["run_id"] = "synthetic-renamed"
    raw = (campaign.root / "observation.csv").read_bytes()
    other["observation"] = campaign.write("renamed-observation.csv", raw)
    campaign.manifest["runs"].append(other)
    campaign.manifest["held_out_policy"]["reserved_run_ids"].append("synthetic-renamed")
    with pytest.raises(ValueError, match="Observation bytes reused"):
        campaign.evaluate()


def test_reference_cannot_be_reused_in_a_later_observation(campaign):
    development = copy.deepcopy(campaign.run)
    development.update(run_id="synthetic-development", split="development", reference=None, estimates=None)
    development["observation"] = dict(campaign.run["reference"])
    campaign.manifest["runs"].append(development)
    campaign.manifest["held_out_policy"]["development_run_ids"] = [development["run_id"]]
    with pytest.raises(ValueError, match="Observation bytes reused"):
        campaign.evaluate()


@pytest.mark.parametrize("role", ["observation", "reference"])
def test_calibration_must_not_fit_held_out_runs(campaign, role):
    campaign.change_calibration(role, fit_run_ids=["synthetic-held"])
    with pytest.raises(ValueError, match="calibration fit"):
        campaign.evaluate()


@pytest.mark.parametrize("field", ["instrument_id", "logger_id"])
def test_independent_reference_instrument_and_readout_required(campaign, field):
    same = campaign.manifest["channels"]["observation"][field]
    campaign.manifest["channels"]["reference"][field] = same
    campaign.change_calibration("reference", **{field: same})
    report, _, _ = campaign.evaluate()
    assert "independent_reference_" + field + "_required" in report["blockers"]


@pytest.mark.parametrize("field,value", [
    ("reference_used_for_fit", True),
    ("predictions_frozen_at_utc", "2026-06-01T12:00:04Z"),
    ("model_frozen_at_utc", "2026-06-01T12:00:04Z"),
    ("split_frozen_at_utc", "2026-06-01T12:00:00Z"),
    ("reserved_run_ids", []),
])
def test_leakage_and_invalid_freeze_order_rejected(campaign, field, value):
    campaign.manifest["held_out_policy"][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_criteria_cannot_be_agreed_after_reference_access(campaign):
    campaign.manifest["criteria"]["approved_at_utc"] = "2026-06-01T12:00:04Z"
    with pytest.raises(ValueError, match="Acceptance criteria must be agreed before"):
        campaign.evaluate()


@pytest.mark.parametrize("changes", [
    {"valid_until_utc": "2026-06-01T12:00:01Z"},
    {"valid_from_utc": "2026-06-01T12:00:01Z"},
    {"input_min": 20.5},
    {"instrument_id": "wrong-probe"},
    {"input_unit": "volts"},
    {"coefficient_covariance": [[1, 2], [2, 1]]},
    {"coefficient_covariance": [[1, 0], [0.1, 1]]},
])
def test_invalid_calibration_applicability_and_covariance(campaign, changes):
    campaign.change_calibration("observation", **changes)
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_calibrated_temperature_must_remain_in_scope(campaign):
    campaign.change_calibration("observation", offset_k=70)
    with pytest.raises(ValueError, match="outside declared fluid scope"):
        campaign.evaluate()


@pytest.mark.parametrize("field", ["certificate", "traceability_statement"])
def test_missing_calibration_evidence_blocks_qualification(campaign, field):
    campaign.change_calibration("observation", **{field: None})
    report, _, _ = campaign.evaluate()
    assert any("missing" in item and item.startswith("observation:") for item in report["blockers"])


def test_bundle_is_never_overwritten(campaign, tmp_path):
    output = tmp_path / "bundle"
    adapt_campaign(campaign.path, output)
    before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        adapt_campaign(campaign.path, output)
    assert before == {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def test_empty_template_reports_concrete_missing_evidence(tmp_path):
    path = write_templates(tmp_path / "empty-intake")
    report, inference, evaluation = Campaign(path).evaluate()
    assert set(report["blockers"]) == {
        "observation:calibration_missing", "reference:calibration_missing", "measured_runs_missing",
        "held_out_runs_missing", "held_out_policy_missing", "agreed_criteria_missing", "reviewed_uncertainty_budget_missing",
    }
    assert report["criteria_result"] == "not_evaluated"
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert inference["runs"] == [] and evaluation["runs"] == []
    with pytest.raises(FileExistsError):
        write_templates(path.parent)


def test_cli_distinguishes_blocked_and_invalid_inputs(campaign, tmp_path, capsys):
    assert main(["adapt", "--manifest", str(campaign.path), "--output-dir", str(tmp_path / "bundle")]) == 2
    assert json.loads(capsys.readouterr().out)["release_status"] == "unchanged"
    campaign.path.write_bytes(b"invalid JSON")
    assert main(["adapt", "--manifest", str(campaign.path), "--output-dir", str(tmp_path / "invalid")]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"


def test_prospective_model_freeze_must_precede_held_out_acquisition(campaign):
    campaign.manifest["held_out_policy"]["model_frozen_at_utc"] = "2026-06-01T12:00:01Z"
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_predictions_cannot_be_frozen_before_acquisition_completes(campaign):
    campaign.manifest["held_out_policy"]["predictions_frozen_at_utc"] = "2026-06-01T12:00:01Z"
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_criteria_agreement_must_precede_acquisition(campaign):
    campaign.manifest["criteria"]["approved_at_utc"] = "2026-06-01T12:00:00Z"
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_predictions_must_match_frozen_content_identity(campaign):
    campaign.manifest["held_out_policy"]["prediction_sha256_by_run"]["synthetic-held"] = content_hash(b"other predictions")
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_model_identity_must_match_retained_artifact(campaign):
    campaign.manifest["held_out_policy"]["model_sha256"] = content_hash(b"different model")
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_criteria_must_match_frozen_content_identity(campaign):
    campaign.save()
    manifest = json.loads(campaign.path.read_bytes())
    manifest["held_out_policy"]["criteria_sha256"] = content_hash(b"different criteria")
    campaign.write_json("campaign.json", manifest)
    with pytest.raises(ValueError):
        Campaign(campaign.path).evaluate()


def test_missing_model_artifact_cannot_qualify(campaign):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["held_out_policy"]["model_artifact"] = None
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert any("model" in item and "missing" in item for item in report["blockers"])


def test_uncertainty_budget_must_be_reviewed(campaign):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["uncertainty_budget"] = None
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert any("uncertainty" in item and "missing" in item for item in report["blockers"])


def test_entirely_missing_observations_cannot_qualify(campaign):
    campaign.pretend_measured_for_control_flow_test()
    campaign.replace_readings("observation", "0,,,missing\n1,,,missing\n2,,,missing\n")
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert any("observation" in item for item in report["blockers"])


def test_empty_evidence_is_rejected(campaign):
    campaign.change_calibration("observation", certificate=campaign.write("empty-certificate.txt", b""))
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_absolute_path_is_refused_even_inside_campaign(campaign):
    campaign.run["observation"]["file"] = str(campaign.root / "observation.csv")
    with pytest.raises(ValueError, match="inside the campaign directory"):
        campaign.evaluate()


def test_zero_length_manifest_is_refused(campaign):
    campaign.path.write_bytes(b"")
    with pytest.raises(ValueError, match="Manifest size"):
        Campaign(campaign.path)
