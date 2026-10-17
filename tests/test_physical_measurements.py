"""Synthetic campaign control-flow checks; no measurements or traceability claims."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from ciw.measurement_evidence import digest, dump, load
from ciw.physical_measurement_profiles import DEFERRED, PROFILES, get_profile
from ciw.physical_measurements import (
    CALIBRATION_SCHEMA, CHAIN_FIELDS, MeasurementCampaign, adapt_campaign,
    main, read_csv, write_specialized_template, write_templates,
)


class SyntheticMeasurementCampaign:
    """All readings, certificates and review names are fabricated test data."""

    def __init__(self, root, profile_id="mass.static.v1"):
        self.root = Path(root)
        self.path = write_templates(self.root, profile_id)
        self.profile = get_profile(profile_id)
        self.manifest = load(self.path.read_bytes())
        self.manifest.update(campaign_id="SYNTHETIC-CONTROL-FLOW-ONLY", evidence_kind="synthetic")
        unit = self.profile["canonical_unit"]
        fraction = self.profile["physical_max"] == 1
        self.values = [0.2, 0.3, 0.4] if fraction else [293.0, 294.0, 295.0] if unit == "K" else [1.0, 2.0, 3.0]
        self.reference_delta = 0.01 if fraction else 0.1
        scope = self.manifest["scope"]
        scope.update(medium=self.profile["media"][0], minimum=1.0 if unit == "K" else 0.0 if self.profile["physical_min"] == 0 else -100.0,
                     maximum=1.0 if fraction else 400.0, resolution=0.001,
                     sampling_interval_s=1.0, bandwidth_hz=0.1,
                     instrument_specification=self.write("specification.txt", b"SYNTHETIC specification; no instrument exists."))
        for field in scope["conditions"]:
            scope["conditions"][field] = self.profile.get("condition_allowed_values", {}).get(field, ["SYNTHETIC condition"])[0]
        certificate = self.write("certificate.txt", b"SYNTHETIC certificate. No calibration or measurement occurred.\r\n")
        validity = self.write("validity-policy.txt", b"SYNTHETIC applicability policy: complete acquisition must be within validity.")
        self.calibrations = {}
        for role, channel in self.manifest["channels"].items():
            channel.update({field: "synthetic-" + role + "-" + field for field in CHAIN_FIELDS})
            channel["unit"] = unit
            cal = load((self.root / "calibration.template.json").read_bytes())
            cal.update({field: channel[field] for field in CHAIN_FIELDS})
            cal.update(calibration_id="synthetic-" + role, input_unit=unit,
                       scope_sha256=digest(dump(scope)), input_min=scope["minimum"], input_max=scope["maximum"],
                       scale=1.0, offset=0.0, coefficient_covariance=[[0.0, 0.0], [0.0, 0.0]],
                       residual_standard_uncertainty=0.002, valid_from_utc="2026-01-01T00:00:00Z",
                       valid_until_utc="2027-01-01T00:00:00Z", validity_policy=validity,
                       certificate=certificate, traceability_statement="SYNTHETIC test only; no traceability.",
                       fit_run_ids=["synthetic-calibration-run"])
            self.calibrations[role] = cal
            channel["calibration"] = self.write_json(role + "-calibration.json", cal)
        run = load((self.root / "run.template.json").read_bytes())
        run.update(run_id="synthetic-held", split="held_out", condition_label="synthetic-condition",
                   specimen_id="synthetic-held-specimen", acquisition_start_utc="2026-06-01T12:00:00Z",
                   acquisition_end_utc="2026-06-01T12:00:02Z",
                   observation=self.write_readings("observation.csv", self.values),
                   reference=self.write_readings("reference.csv", [v + self.reference_delta for v in self.values]),
                   estimates=self.write("estimates.csv", ("timestamp_s,estimate\r\n" + "".join(f"{i},{v + self.reference_delta + 0.02}\r\n" for i, v in enumerate(self.values))).encode()),
                   reference_extra_uncertainty={k: 0.001 for k in ("placement", "timing", "environment", "other")})
        self.manifest["runs"] = [run]
        self.manifest["uncertainty_budget"] = {"assumption": "independent_disjoint_components", "reviewed_by": "synthetic-test-actor",
            "evidence": self.write("budget.txt", b"SYNTHETIC disjoint components for software tests only.")}
        self.manifest["criteria"] = {"status": "agreed", "unit": unit, "approved_by": "synthetic-test-actor",
            "approved_at_utc": "2026-05-01T00:00:00Z", "decision_rule": "conservative_pointwise_reference_guard",
            "coverage_factor": 2.0, "min_held_out_runs": 1, "required_condition_labels": ["synthetic-condition"],
            "min_observed_fraction": 1.0, "min_reference_fraction": 1.0, "min_reference_span": 0.05,
            "max_abs_bias": 1.0, "max_rmse": 1.0, "max_abs_error": 1.0, "max_reference_expanded_uncertainty": 1.0}
        self.manifest["held_out_policy"] = {"split_unit": "specimen", "development_run_ids": [], "reserved_run_ids": [run["run_id"]],
            "model_artifact": self.write("model.txt", b"SYNTHETIC fixed model declaration"),
            "prediction_sha256_by_run": {run["run_id"]: run["estimates"]["sha256"]}, "criteria_sha256": None,
            "split_frozen_at_utc": "2026-05-01T00:00:00Z", "model_frozen_at_utc": "2026-05-02T00:00:00Z",
            "predictions_frozen_at_utc": "2026-06-01T12:00:03Z", "reference_unsealed_at_utc": "2026-06-01T12:00:04Z",
            "reference_used_for_fit": False, "independence_statement": "SYNTHETIC control-flow declaration, no independent laboratory.",
            "reviewed_by": "synthetic-test-actor"}
        self.save()

    @property
    def run(self):
        return self.manifest["runs"][0]

    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        return {"file": name, "sha256": digest(raw)}

    def write_json(self, name, value):
        return self.write(name, dump(value))

    def write_readings(self, name, values, uncertainty=0.003):
        rows = "".join(f"{i},{value},{uncertainty},valid\r\n" for i, value in enumerate(values))
        return self.write(name, ("timestamp_s,value,standard_uncertainty,quality\r\n" + rows).encode())

    def replace_readings(self, role, rows):
        self.run[role] = self.write(role + ".csv", ("timestamp_s,value,standard_uncertainty,quality\n" + rows).encode())

    def change_calibration(self, role, **changes):
        self.calibrations[role].update(changes)
        self.manifest["channels"][role]["calibration"] = self.write_json(role + "-calibration.json", self.calibrations[role])

    def rebind_scope(self):
        for role in self.calibrations:
            self.change_calibration(role, scope_sha256=digest(dump(self.manifest["scope"])))

    def save(self, refresh_policy=True):
        if refresh_policy and self.manifest["held_out_policy"] is not None:
            self.manifest["held_out_policy"]["criteria_sha256"] = digest(dump(self.manifest["criteria"]))
        self.path.write_bytes(dump(self.manifest))
        return self.path

    def evaluate(self, refresh_policy=True):
        return MeasurementCampaign(self.save(refresh_policy)).evaluate()

    def pretend_measured_for_control_flow_test(self):
        # Deliberately fabricated fixture exercises declared-measured branching only.
        # File contents and campaign identity remain explicitly SYNTHETIC.
        self.manifest["evidence_kind"] = "measured"

    def add_development_run(self, same_specimen=False):
        development = deepcopy(self.run)
        development.update(run_id="synthetic-development", split="development", reference=None, estimates=None,
                           specimen_id=self.run["specimen_id"] if same_specimen else "synthetic-development-specimen",
                           observation=self.write_readings("development.csv", [v + 0.03 for v in self.values]))
        self.manifest["runs"].append(development)
        self.manifest["held_out_policy"]["development_run_ids"] = [development["run_id"]]
        return development


@pytest.fixture
def campaign(tmp_path):
    return SyntheticMeasurementCampaign(tmp_path / "synthetic-campaign")


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_templates_all_profiles_are_incomplete_not_physical_evidence(tmp_path, profile_id):
    path = write_templates(tmp_path / profile_id, profile_id)
    report, inference, reference = MeasurementCampaign(path).evaluate()
    assert report["evidence_status"] == "blocked_missing_evidence"
    assert report["criteria_result"] == "not_evaluated"
    assert {"measurement_runs_missing", "agreed_criteria_missing", "held_out_policy_missing"} <= set(report["blockers"])
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert inference["runs"] == reference["runs"] == []


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_populated_profiles_retain_synthetic_origin_and_never_establish_physical_validation(tmp_path, profile_id):
    fixture = SyntheticMeasurementCampaign(tmp_path / profile_id, profile_id)
    report, _, _ = fixture.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert "synthetic_data_cannot_establish_physical_validation" in report["blockers"]
    assert report["evaluated_run_count"] == 1
    assert report["physical_validation"] == "not_established"


def test_declared_measured_control_flow_pass_keeps_physical_and_release_boundary(campaign):
    campaign.pretend_measured_for_control_flow_test()
    report, inference, reference = campaign.evaluate()
    assert report["criteria_result"] == "passed_declared_criteria"
    assert report["blockers"] == []
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert report["evidence_authentication"] == "operator_assertions_and_local_hashes_only"
    row = report["per_run_metrics"][0]
    uncertainty = math.sqrt(0.003**2 + 0.002**2 + 4*0.001**2)
    assert row["bias"] == pytest.approx(0.02)
    assert row["guarded_abs_bias"] == pytest.approx(0.02 + 2*uncertainty)
    assert row["max_reference_standard_uncertainty"] == pytest.approx(uncertainty)
    assert "estimates" not in inference["runs"][0]
    assert "reference" not in inference["runs"][0]
    assert reference["runs"][0]["estimate_unit"] == "kg"


@pytest.mark.parametrize("profile_id,unit,raw,canonical,readout_uncertainty,canonical_uncertainty", [
    ("mass.static.v1", "g", 2000, 2.0, 3.0, 0.003),
    ("level.liquid.v1", "cm", 20, 0.2, 0.3, 0.003),
    ("dimension.length.v1", "mm", 20, 0.02, 0.3, 0.0003),
    ("pressure.absolute.v1", "kPa", 0.02, 20.0, 0.003, 3.0),
    ("strain.axial.v1", "microstrain", 2000, 0.002, 3, 0.000003),
    ("flow.volume_actual.v1", "L/min", 60, 0.001, 0.3, 0.000005),
    ("water_content.volumetric.v1", "%vol", 20, 0.2, 0.3, 0.003),
])
def test_affine_correction_propagates_noncanonical_readout_units(tmp_path, profile_id, unit, raw, canonical, readout_uncertainty, canonical_uncertainty):
    fixture = SyntheticMeasurementCampaign(tmp_path / "case", profile_id)
    fixture.manifest["channels"]["observation"]["unit"] = unit
    fixture.change_calibration("observation", input_unit=unit, input_min=0, input_max=100000,
        scale=1.01, offset=0.001, coefficient_covariance=[[1e-6, -1e-6], [-1e-6, 4e-6]])
    fixture.replace_readings("observation", "".join(f"{i},{raw},{readout_uncertainty},valid\n" for i in range(3)))
    _, inference, _ = fixture.evaluate()
    first = inference["runs"][0]["rows"][0]
    expected = math.sqrt((1.01*canonical_uncertainty)**2 + canonical**2*1e-6 - 2*canonical*1e-6 + 4e-6 + 0.002**2)
    assert first["raw_value"] == raw
    assert first["value"] == pytest.approx(1.01*canonical + 0.001)
    assert first["standard_uncertainty"] == pytest.approx(expected)


def test_celsius_offset_affects_value_not_uncertainty(tmp_path):
    fixture = SyntheticMeasurementCampaign(tmp_path / "temperature", "temperature.contact.v1")
    fixture.manifest["channels"]["observation"]["unit"] = "degC"
    fixture.change_calibration("observation", input_unit="degC", input_min=-20, input_max=100, scale=1.001, offset=-0.4)
    fixture.replace_readings("observation", "0,20,0.03,valid\n1,21,0.03,valid\n2,22,0.03,valid\n")
    _, inference, _ = fixture.evaluate()
    row = inference["runs"][0]["rows"][0]
    assert row["value"] == pytest.approx(1.001*293.15 - 0.4)
    assert row["standard_uncertainty"] == pytest.approx(math.hypot(1.001*0.03, 0.002))


def test_bundle_retains_original_bytes_and_keeps_reference_out_of_inference(campaign, tmp_path):
    campaign.save()
    output = tmp_path / "bundle"
    report = adapt_campaign(campaign.path, output)
    assert (output / "campaign.json").read_bytes() == campaign.path.read_bytes()
    for role in ("observation", "reference", "estimates"):
        descriptor = campaign.run[role]
        retained = output / "raw" / (descriptor["sha256"].split(":")[1] + ".bin")
        assert retained.read_bytes() == (campaign.root / descriptor["file"]).read_bytes()
    inference = load((output / "inference-input.json").read_bytes())
    assert inference["runs"][0]["source_sha256"] == campaign.run["observation"]["sha256"]
    assert campaign.run["reference"]["sha256"] not in json.dumps(inference)
    assert report["manifest_sha256"] == digest(campaign.path.read_bytes())


@pytest.mark.parametrize("field,value", [("profile_id", "pressure.absolute.v1"), ("canonical_unit", "g"),
    ("input_unit", "g"), ("sensor_id", "different"), ("instrument_id", "different"),
    ("readout_id", "different"), ("channel_id", "different"), ("model", "raw_bridge_conversion")])
def test_calibration_cannot_change_measurand_or_chain_binding(campaign, field, value):
    campaign.change_calibration("observation", **{field: value})
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("profile_id,field,value", [
    ("mass.static.v1", "mass_convention", "weight"),
    ("pressure.absolute.v1", "pressure_type", "gauge"),
    ("pressure.gauge.v1", "pressure_type", "absolute"),
    ("pressure.differential.v1", "pressure_type", "gauge"),
    ("moisture.mass_fraction.v1", "mass_basis", "dry_basis"),
])
def test_quantity_conventions_cannot_be_renamed_as_equivalent(tmp_path, profile_id, field, value):
    fixture = SyntheticMeasurementCampaign(tmp_path / "case", profile_id)
    fixture.manifest["scope"]["conditions"][field] = value
    fixture.rebind_scope()
    with pytest.raises(ValueError, match="Unsupported condition"):
        fixture.evaluate()


def test_units_medium_and_scope_are_bound(campaign):
    campaign.manifest["channels"]["observation"]["unit"] = "N"
    with pytest.raises(ValueError, match="unit"):
        campaign.evaluate()
    campaign.manifest["channels"]["observation"]["unit"] = "kg"
    campaign.manifest["scope"]["medium"] = "vacuum"
    with pytest.raises(ValueError, match="Medium"):
        campaign.evaluate()
    campaign.manifest["scope"]["medium"] = "liquid"
    with pytest.raises(ValueError, match="scope"):
        campaign.evaluate()


@pytest.mark.parametrize("change", ["condition", "resolution", "range"])
def test_calibration_scope_digest_catches_changed_conditions(campaign, change):
    if change == "condition":
        campaign.manifest["scope"]["conditions"]["mass_convention"] = "conventional_mass"
    elif change == "resolution":
        campaign.manifest["scope"]["resolution"] *= 2
    else:
        campaign.manifest["scope"]["maximum"] += 1
    with pytest.raises(ValueError, match="scope"):
        campaign.evaluate()


@pytest.mark.parametrize("field,expected", [("certificate", "observation:certificate_missing"),
    ("validity_policy", "observation:validity_policy_missing"), ("traceability_statement", "observation:traceability_statement_missing")])
def test_missing_calibration_evidence_blocks_review(campaign, field, expected):
    campaign.pretend_measured_for_control_flow_test()
    campaign.change_calibration("observation", **{field: None})
    report, _, _ = campaign.evaluate()
    assert expected in report["blockers"]
    assert report["criteria_result"] == "not_evaluated"


@pytest.mark.parametrize("changes", [
    {"valid_from_utc": "2026-06-01T12:00:01Z"}, {"valid_until_utc": "2026-06-01T12:00:01Z"},
    {"input_max": 1.5}, {"valid_until_utc": "2025-01-01T00:00:00Z"},
    {"fit_run_ids": ["synthetic-held"]},
])
def test_inapplicable_or_leaked_calibration_refused(campaign, changes):
    campaign.change_calibration("observation", **changes)
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_calibration_endpoint_matches_explicit_inclusive_applicability(campaign):
    campaign.change_calibration("observation", valid_until_utc=campaign.run["acquisition_end_utc"])
    assert campaign.evaluate()[0]["processing_status"] == "valid"


@pytest.mark.parametrize("field", ["sensor_id", "instrument_id", "readout_id"])
def test_shared_reference_chain_blocks_independence(campaign, field):
    shared = campaign.manifest["channels"]["observation"][field]
    campaign.manifest["channels"]["reference"][field] = shared
    campaign.change_calibration("reference", **{field: shared})
    campaign.pretend_measured_for_control_flow_test()
    report, _, _ = campaign.evaluate()
    assert "independent_reference_chain_required:" + field in report["blockers"]
    assert report["criteria_result"] == "not_evaluated"


def test_held_out_specimen_cannot_appear_in_development(campaign):
    campaign.add_development_run(same_specimen=True)
    with pytest.raises(ValueError, match="[Ss]pecimen"):
        campaign.evaluate()


def test_distinct_development_run_is_retained_without_reference_targets(campaign):
    campaign.add_development_run()
    report, inference, reference = campaign.evaluate()
    assert len(inference["runs"]) == 2
    assert len(reference["runs"]) == report["held_out_run_count"] == 1


def test_development_targets_refused(campaign):
    development = campaign.add_development_run()
    development["reference"] = campaign.run["reference"]
    with pytest.raises(ValueError, match="Development targets"):
        campaign.evaluate()


def test_duplicate_run_or_measurement_bytes_refused(campaign):
    development = campaign.add_development_run()
    development["observation"] = campaign.run["observation"]
    with pytest.raises(ValueError, match="reused"):
        campaign.evaluate()
    development["run_id"] = campaign.run["run_id"]
    with pytest.raises(ValueError, match="Duplicate run"):
        campaign.evaluate()


def test_reference_cannot_reuse_observation_export(campaign):
    campaign.run["reference"] = campaign.run["observation"]
    with pytest.raises(ValueError, match="duplicate"):
        campaign.evaluate()


@pytest.mark.parametrize("mutation", ["predictions", "criteria", "fit", "partition"])
def test_frozen_policy_identities_and_partition_enforced(campaign, mutation):
    policy = campaign.manifest["held_out_policy"]
    if mutation == "predictions":
        policy["prediction_sha256_by_run"][campaign.run["run_id"]] = "sha256:" + "0"*64
    elif mutation == "criteria":
        policy["criteria_sha256"] = "sha256:" + "0"*64
    elif mutation == "fit":
        policy["reference_used_for_fit"] = True
    else:
        policy["reserved_run_ids"] = []
    with pytest.raises(ValueError):
        campaign.evaluate(refresh_policy=False)


@pytest.mark.parametrize("field,value", [
    ("split_frozen_at_utc", "2026-06-01T12:00:00Z"),
    ("model_frozen_at_utc", "2026-06-01T12:00:01Z"),
    ("predictions_frozen_at_utc", "2026-06-01T12:00:01Z"),
    ("reference_unsealed_at_utc", "2026-06-01T12:00:03Z"),
])
def test_freeze_chronology_refused(campaign, field, value):
    campaign.manifest["held_out_policy"][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("stamp", ["2026-06-01T12:00:00", "2026-06-01T13:00:00+01:00", "2026-06-01T12:00:00.0000001Z"])
def test_acquisition_requires_exact_explicit_utc(campaign, stamp):
    campaign.run["acquisition_start_utc"] = stamp
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_missingness_is_explicit_and_scored_separately(campaign):
    campaign.replace_readings("observation", "0,1,0.003,valid\n1,,,missing\n2,3,0.003,valid\n")
    campaign.replace_readings("reference", "0,1.1,0.003,valid\n1,,,missing\n2,3.1,0.003,valid\n")
    campaign.pretend_measured_for_control_flow_test()
    report, inference, reference = campaign.evaluate()
    assert report["criteria_result"] == "failed_declared_criteria"
    assert report["per_run_metrics"][0]["observed_fraction"] == pytest.approx(2/3)
    assert report["per_run_metrics"][0]["reference_fraction"] == pytest.approx(2/3)
    assert inference["runs"][0]["rows"][1]["value"] is None
    assert reference["runs"][0]["reference"]["rows"][1]["standard_uncertainty"] is None
    campaign.manifest["criteria"].update(min_observed_fraction=2/3, min_reference_fraction=2/3)
    assert campaign.evaluate()[0]["criteria_result"] == "passed_declared_criteria"


def test_observation_dropout_is_not_hidden_by_complete_reference(campaign):
    campaign.replace_readings("observation", "0,1,0.003,valid\n1,,,missing\n2,3,0.003,valid\n")
    campaign.pretend_measured_for_control_flow_test()
    report, _, _ = campaign.evaluate()
    metric = report["per_run_metrics"][0]
    assert metric["reference_fraction"] == 1
    assert metric["observed_fraction"] < 1
    assert report["criteria_result"] == "failed_declared_criteria"


@pytest.mark.parametrize("role", ["observation", "reference"])
def test_no_valid_measurements_blocks_evaluation(campaign, role):
    campaign.replace_readings(role, "0,,,missing\n1,,,missing\n2,,,missing\n")
    campaign.pretend_measured_for_control_flow_test()
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert any("no_valid_" in b for b in report["blockers"])


@pytest.mark.parametrize("role,rows", [
    ("observation", "0,1,0.003,valid\n2,2,0.003,valid\n"),
    ("observation", "0,1,0.003,valid\n1,2,0.003,valid\n3,3,0.003,valid\n"),
    ("reference", "0,1.1,0.003,valid\n1.01,2.1,0.003,valid\n2,3.1,0.003,valid\n"),
])
def test_sampling_or_alignment_never_interpolated(campaign, role, rows):
    campaign.replace_readings(role, rows)
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("limit", ["max_abs_bias", "max_rmse", "max_abs_error", "max_reference_expanded_uncertainty"])
def test_reference_uncertainty_guard_can_fail_zeroish_residuals(campaign, limit):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["criteria"][limit] = 0.001
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "failed_declared_criteria"
    assert report["physical_validation"] == "not_established"


def test_required_conditions_and_run_count_block_partial_campaign(campaign):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["criteria"].update(min_held_out_runs=2, required_condition_labels=["unmeasured-condition"])
    report, _, _ = campaign.evaluate()
    assert "required_run_or_condition_coverage_missing" in report["blockers"]
    assert report["criteria_result"] == "not_evaluated"


@pytest.mark.parametrize("row", [
    "0,nan,0.1,valid", "0,inf,0.1,valid", "0,1,inf,valid", "0,1,-0.1,valid",
    "nan,1,0.1,valid", "-1,1,0.1,valid", "86401,1,0.1,valid", "0,1,0.1,missing",
    "0,,,valid", "0,1,0.1,questionable", "0,1,0.1,valid,extra", "0,1,valid",
])
def test_readout_parser_rejects_invalid_values_and_shape(row):
    with pytest.raises(ValueError):
        read_csv(("timestamp_s,value,standard_uncertainty,quality\n" + row + "\n").encode())


def test_parser_rejects_empty_wrong_columns_and_duplicate_times():
    for raw in [b"timestamp_s,value,standard_uncertainty,quality\n", b"value,timestamp_s,standard_uncertainty,quality\n1,0,0.1,valid\n",
                b"timestamp_s,value,standard_uncertainty,quality\n0,1,0.1,valid\n0,2,0.1,valid\n"]:
        with pytest.raises(ValueError):
            read_csv(raw)


@pytest.mark.parametrize("raw", [b"timestamp_s,estimate\n0,nan\n", b"timestamp_s,estimate\n0,1\n0,2\n"])
def test_prediction_parser_rejects_nonfinite_or_duplicate_time(raw):
    with pytest.raises(ValueError):
        read_csv(raw, estimates=True)


def test_source_hash_tamper_is_refused(campaign):
    (campaign.root / "observation.csv").write_bytes(b"changed after descriptor frozen")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.evaluate()


@pytest.mark.parametrize("location", ["../outside.txt", "absolute", "self"])
def test_evidence_paths_cannot_escape_or_reference_manifest(campaign, tmp_path, location):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside evidence")
    if location == "absolute":
        name = str(outside.resolve())
    elif location == "self":
        name = "campaign.json"
    else:
        name = location
    campaign.manifest["scope"]["instrument_specification"] = {"file": name, "sha256": digest(outside.read_bytes())}
    with pytest.raises(ValueError, match="inside"):
        campaign.evaluate()


def test_duplicate_json_keys_rejected_in_manifest_and_calibration(campaign):
    campaign.path.write_bytes(b'{"schema":"first","schema":"second"}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        MeasurementCampaign(campaign.path)
    campaign.save()
    descriptor = campaign.write("observation-calibration.json", b'{"schema":"first","schema":"second"}')
    campaign.manifest["channels"]["observation"]["calibration"] = descriptor
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        campaign.evaluate()


def test_new_bundle_and_template_directory_never_overwrite(campaign, tmp_path):
    campaign.save()
    output = tmp_path / "retained"
    adapt_campaign(campaign.path, output)
    original = (output / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        adapt_campaign(campaign.path, output)
    assert (output / "report.json").read_bytes() == original
    with pytest.raises(FileExistsError):
        write_templates(campaign.root, "mass.static.v1")


def test_cli_catalog_init_init_all_and_adapt_status(tmp_path, capsys):
    assert main(["catalog"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert len(catalog["profiles"]) == 13
    assert catalog["hardware_inventory"] == "not_supplied"
    single = tmp_path / "single"
    assert main(["init", "--profile", "mass.static.v1", "--medium", "liquid", "--output-dir", str(single)]) == 0
    capsys.readouterr()
    assert load((single / "campaign.json").read_bytes())["scope"]["medium"] == "liquid"
    all_profiles = tmp_path / "all"
    assert main(["init-all", "--output-dir", str(all_profiles)]) == 0
    capsys.readouterr()
    assert {p.name for p in all_profiles.iterdir() if p.is_dir()} == set(PROFILES) | {"specialized"}
    assert {p.name for p in (all_profiles / "specialized").iterdir()} == set(DEFERRED)
    assert main(["adapt", "--manifest", str(single / "campaign.json"), "--output-dir", str(tmp_path / "blocked")]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["criteria_result"] == "not_evaluated"
    fixture = SyntheticMeasurementCampaign(tmp_path / "fixture")
    fixture.pretend_measured_for_control_flow_test()
    fixture.save()
    assert main(["adapt", "--manifest", str(fixture.path), "--output-dir", str(tmp_path / "review")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["physical_validation"] == "not_established"
    assert main(["init", "--profile", "electrical.ac", "--output-dir", str(tmp_path / "unsupported")]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"


@pytest.mark.parametrize("rows", [
    "1,2,0.003,valid\n",
    "1,2,0.003,valid\n2,3,0.003,valid\n",
    "0,1,0.003,valid\n1,2,0.003,valid\n",
])
def test_omitted_endpoints_cannot_count_as_complete_acquisition(campaign, rows):
    campaign.replace_readings("observation", rows)
    with pytest.raises(ValueError, match="endpoint"):
        campaign.evaluate()


def test_sampling_interval_must_divide_acquisition_duration(campaign):
    campaign.manifest["scope"]["sampling_interval_s"] = 0.6
    campaign.rebind_scope()
    with pytest.raises(ValueError, match="grid"):
        campaign.evaluate()


def test_run_level_partition_explicitly_allows_repeat_specimen(campaign):
    campaign.add_development_run(same_specimen=True)
    campaign.manifest["held_out_policy"]["split_unit"] = "run"
    campaign.pretend_measured_for_control_flow_test()
    assert campaign.evaluate()[0]["criteria_result"] == "passed_declared_criteria"


@pytest.mark.parametrize("split_unit", [None, "sample", "row", True])
def test_split_unit_must_be_explicit_whole_run_or_specimen(campaign, split_unit):
    campaign.manifest["held_out_policy"]["split_unit"] = split_unit
    with pytest.raises(ValueError, match="holdout unit"):
        campaign.evaluate()


@pytest.mark.parametrize("profile_id,invalid_prediction", [
    ("mass.static.v1", -0.01), ("pressure.absolute.v1", -1.0),
    ("temperature.contact.v1", 0.0), ("water_content.volumetric.v1", 1.01),
    ("moisture.mass_fraction.v1", 1.01),
])
def test_estimates_must_obey_measurand_physical_domain_even_with_loose_limits(tmp_path, profile_id, invalid_prediction):
    fixture = SyntheticMeasurementCampaign(tmp_path / "case", profile_id)
    fixture.pretend_measured_for_control_flow_test()
    fixture.run["estimates"] = fixture.write("estimates.csv", ("timestamp_s,estimate\n" + "".join(f"{i},{invalid_prediction}\n" for i in range(3))).encode())
    fixture.manifest["held_out_policy"]["prediction_sha256_by_run"][fixture.run["run_id"]] = fixture.run["estimates"]["sha256"]
    fixture.manifest["criteria"].update(max_abs_bias=10000, max_rmse=10000, max_abs_error=10000)
    with pytest.raises(ValueError, match="physical domain"):
        fixture.evaluate()


@pytest.mark.parametrize("raw", [
    b"timestamp_s,value,standard_uncertainty,quality\n0,1,1e-999,valid\n",
    b"timestamp_s,value,standard_uncertainty,quality\n0,1e-999,0.1,valid\n",
    b"timestamp_s,value,standard_uncertainty,quality\n1e-999,1,0.1,valid\n",
])
def test_csv_decimal_underflow_never_silently_becomes_zero(raw):
    with pytest.raises(ValueError, match="underflow"):
        read_csv(raw)


def test_prediction_decimal_underflow_never_silently_becomes_zero():
    with pytest.raises(ValueError, match="underflow"):
        read_csv(b"timestamp_s,estimate\n0,1e-999\n", estimates=True)


@pytest.mark.parametrize("token", ["1e-999", "-1e-999", "1e999", "NaN", "Infinity"])
def test_json_nonfinite_or_underflowed_decimal_is_refused(token):
    with pytest.raises(ValueError):
        load(('{"uncertainty":' + token + '}').encode())


@pytest.mark.parametrize("field,value", [
    ("scale", 0), ("scale", True),
    ("coefficient_covariance", [[1, 0.1], [0.2, 1]]),
    ("coefficient_covariance", [[1, 2], [2, 1]]),
    ("coefficient_covariance", [[-1, 0], [0, 1]]),
    ("coefficient_covariance", [[1, 0, 0], [0, 1, 0]]),
    ("residual_standard_uncertainty", -1),
])
def test_invalid_calibration_coefficients_and_covariance_are_refused(campaign, field, value):
    campaign.change_calibration("observation", **{field: value})
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_missing_calibration_retains_raw_without_claiming_corrected_values(campaign):
    campaign.manifest["channels"]["observation"]["calibration"] = None
    report, inference, _ = campaign.evaluate()
    assert "observation:calibration_missing" in report["blockers"]
    assert inference["runs"][0]["calibration_status"] == "not_applied"
    assert inference["runs"][0]["rows"][0]["raw_value"] == 1
    assert "value" not in inference["runs"][0]["rows"][0]


def test_unknown_component_covariance_blocks_even_with_other_checks_complete(campaign):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["uncertainty_budget"]["assumption"] = "unknown"
    report, _, _ = campaign.evaluate()
    assert "unsupported_cross_component_covariance" in report["blockers"]
    assert report["criteria_result"] == "not_evaluated"


@pytest.mark.parametrize("name", ["certificate.txt", "validity-policy.txt", "budget.txt", "model.txt", "specification.txt", "reference.csv", "estimates.csv"])
def test_every_retained_support_artifact_is_hash_bound(campaign, name):
    path = campaign.root / name
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.evaluate()


def test_draft_rules_do_not_become_agreed_from_complete_measurements(campaign):
    campaign.pretend_measured_for_control_flow_test()
    campaign.manifest["criteria"]["status"] = "draft"
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "not_evaluated"
    assert "agreed_criteria_missing" in report["blockers"]


def test_criteria_approval_must_precede_confirmatory_acquisition(campaign):
    campaign.manifest["criteria"]["approved_at_utc"] = campaign.run["acquisition_start_utc"]
    with pytest.raises(ValueError, match="precede"):
        campaign.evaluate()


@pytest.mark.parametrize("family_id", list(DEFERRED))
def test_specialized_intake_is_unfilled_and_cannot_dispatch_generic_adapter(tmp_path, family_id):
    path = write_specialized_template(tmp_path / family_id, family_id)
    request = load(path.read_bytes())
    assert request["status"] == "specialized_adapter_required"
    assert request["physical_validation"] == "not_established"
    assert request["release_status"] == "unchanged"
    assert request["raw_exports"] == request["calibration_evidence"] == request["independent_reference_evidence"] == []
    assert request["held_out_policy"] is request["agreed_criteria"] is None
    assert not (path.parent / "calibration.template.json").exists()
    with pytest.raises(ValueError):
        MeasurementCampaign(path).evaluate()


@pytest.mark.parametrize("mantissa,accepted", [("1", False), ("0", True), ("-0.000", True)])
def test_extreme_exponents_have_bounded_numeric_refusal(mantissa, accepted):
    token = mantissa + "e-" + "9"*1000
    raw = ("timestamp_s,value,standard_uncertainty,quality\n0,1," + token + ",valid\n").encode()
    if accepted:
        assert read_csv(raw)[0]["raw_standard_uncertainty"] == 0.0
    else:
        with pytest.raises(ValueError, match="underflow"):
            read_csv(raw)


def test_cli_returns_refusal_for_underflowed_readout(campaign, tmp_path, capsys):
    campaign.replace_readings("observation", "0,1,1e-999,valid\n1,2,0.1,valid\n2,3,0.1,valid\n")
    campaign.save()
    assert main(["adapt", "--manifest", str(campaign.path), "--output-dir", str(tmp_path / "refused")]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert not (tmp_path / "refused").exists()


def test_shared_calibration_uncertainty_is_not_reduced_by_repeated_readings(campaign):
    campaign.change_calibration("reference", coefficient_covariance=[[0, 0], [0, 0.09]], residual_standard_uncertainty=0)
    campaign.replace_readings("reference", "0,1.1,0,valid\n1,1.1,0,valid\n2,1.1,0,valid\n")
    campaign.run["reference_extra_uncertainty"] = {k: 0 for k in campaign.run["reference_extra_uncertainty"]}
    report, _, reference = campaign.evaluate()
    for row in reference["runs"][0]["reference"]["rows"]:
        assert row["standard_uncertainty"] == pytest.approx(0.3)
    metric = report["per_run_metrics"][0]
    assert metric["guarded_abs_bias"] == pytest.approx(abs(metric["bias"]) + 0.6)
    assert "no confidence/coverage claim" in report["uncertainty_scope"]
