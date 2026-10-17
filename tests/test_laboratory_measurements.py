"""Fabricated laboratory records test software only; never physical qualification."""
from copy import deepcopy
import csv
import io
import json
import math
import pytest
from ciw.laboratory_measurements import COLUMNS, LaboratoryCampaign, adapt_campaign, write_templates
from ciw.laboratory_profiles import PROFILES, get_profile
from ciw.measurement_evidence import digest, dump, load


class SyntheticCampaign:
    def __init__(self, root, profile_id="chemistry.mass_concentration.v1"):
        self.root = root
        self.path = write_templates(root, profile_id)
        self.m = load(self.path.read_bytes())
        self.profile = get_profile(profile_id)
        self.unit = self.profile["canonical_unit"]
        values = [0.2, 0.4] if "fraction" in profile_id else [10, 20]
        if ".dsc_" in profile_id:
            values = [300, 310]
        self.m.update(campaign_id="SYNTHETIC software test", evidence_kind="synthetic")
        scope = self.m["scope"]
        scope.update(target_identity="SYNTHETIC target", matrix="SYNTHETIC matrix",
            reporting_basis="SYNTHETIC basis", minimum=values[0]/2,
            maximum=0.9 if "fraction" in profile_id else values[-1]*2,
            method_scope_evidence=self.evidence("scope"))
        for field in scope["conditions"]:
            scope["conditions"][field] = self.profile.get("condition_allowed_values", {}).get(
                field, ["SYNTHETIC fixed condition"])[0]
        self.rows = {}
        for role in ("candidate", "reference"):
            self.m[role].update(laboratory_id="SYNTHETIC-" + role + "-lab",
                method_id="SYNTHETIC-" + role + "-method", method_revision="test-1",
                instrument_ids=["SYNTHETIC-" + role + "-instrument"], unit=self.unit,
                scope_sha256=digest(dump(scope)), raw_evidence=[self.evidence(role + "-raw")],
                calibration_evidence=[self.evidence(role + "-calibration")],
                method_validation_evidence=self.evidence(role + "-validation"),
                qc_evidence=self.evidence(role + "-qc"), preparation_evidence=self.evidence(role + "-prep"),
                valid_from_utc="2026-01-01T00:00:00Z", valid_until_utc="2027-01-01T00:00:00Z",
                calibration_material_ids=["SYNTHETIC-" + role + "-calibrant"],
                calibration_sample_ids=["SYNTHETIC-" + role + "-cal-sample"],
                calibration_lot_ids=["SYNTHETIC-" + role + "-cal-lot"])
            self.rows[role] = [dict(result_id=f"{role}-{i}", sample_id=f"{role}-sample-{i}",
                parent_sample_id=f"parent-{i}", lot_id=f"lot-{i}", preparation_id=f"{role}-prep-{i}",
                run_id=f"{role}-run-{i}", replicate_id="1", measured_at_utc=f"2026-06-01T12:00:0{i}Z",
                value=str(v + (0.02 if role == "candidate" else 0)),
                standard_uncertainty="0.03" if role == "candidate" else "0.04",
                qualifier="quantified", detection_limit="", quantification_limit="",
                reference_material_id="") for i, v in enumerate(values)]
        self.m["comparisons"] = [dict(comparison_id=f"comparison-{i}",
            candidate_result_id=f"candidate-{i}", reference_result_id=f"reference-{i}",
            split="held_out") for i in range(2)]
        self.m["comparability_review"] = dict(reviewed_by="SYNTHETIC reviewer",
            evidence=self.evidence("comparability"), same_measurand=True,
            compatible_methods=True, valid_specimen_pairing=True)
        self.m["uncertainty_budget"] = dict(assumption="independent_candidate_reference",
            reviewed_by="SYNTHETIC reviewer", evidence=self.evidence("uncertainty"))
        self.m["criteria"] = dict(status="agreed", unit=self.unit, approved_by="SYNTHETIC reviewer",
            approved_at_utc="2026-05-01T00:00:00Z",
            decision_rule="independent_measurement_difference_guard", coverage_factor=2,
            min_held_out_groups=2, min_quantified_fraction=1, min_reference_span=0.1,
            max_abs_bias=1, max_rmse=1, max_abs_error=1, max_combined_expanded_uncertainty=1)
        self.m["held_out_policy"] = dict(split_unit="lot", development_group_ids=[],
            reserved_group_ids=["lot-0", "lot-1"], method_artifact=self.evidence("method"),
            candidate_results_sha256=None, reference_results_sha256=None, criteria_sha256=None,
            split_frozen_at_utc="2026-05-01T00:00:00Z", method_frozen_at_utc="2026-05-02T00:00:00Z",
            candidate_results_frozen_at_utc="2026-06-01T12:00:02Z",
            reference_unsealed_at_utc="2026-06-01T12:00:03Z",
            reference_used_for_development=False, independence_evidence=self.evidence("independence"),
            reviewed_by="SYNTHETIC reviewer")
        self.save()

    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        return {"file": name, "sha256": digest(raw)}

    def evidence(self, name):
        return self.write(name + ".txt", ("SYNTHETIC " + name +
            "; software fixture only. No measurement or calibration occurred.\r\n").encode())

    def save(self, refresh=True):
        for role, rows in self.rows.items():
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(rows)
            self.m[role]["results"] = self.write(role + ".csv", stream.getvalue().encode())
        policy = self.m["held_out_policy"]
        if policy and refresh:
            for role in self.rows:
                policy[role + "_results_sha256"] = self.m[role]["results"]["sha256"]
            policy["criteria_sha256"] = digest(dump(self.m["criteria"]))
        self.path.write_bytes(dump(self.m))
        return self.path

    def evaluate(self, refresh=True):
        return LaboratoryCampaign(self.save(refresh)).evaluate()

    def declared_measured_branch(self):
        # This operator assertion is fabricated solely for software branch coverage.
        self.m["evidence_kind"] = "measured"

    def add_development(self):
        for role in self.rows:
            row = deepcopy(self.rows[role][0])
            row.update(result_id=role + "-dev", sample_id=role + "-dev-sample",
                parent_sample_id="dev-parent", lot_id="dev-lot", measured_at_utc="2026-04-01T12:00:00Z")
            self.rows[role].append(row)
        self.m["comparisons"].append(dict(comparison_id="dev", candidate_result_id="candidate-dev",
            reference_result_id="reference-dev", split="development"))
        self.m["held_out_policy"]["development_group_ids"] = ["dev-lot"]


@pytest.fixture
def campaign(tmp_path):
    return SyntheticCampaign(tmp_path / "campaign")


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_blank_templates_block(tmp_path, profile_id):
    path = write_templates(tmp_path / profile_id, profile_id)
    report, candidate, reference = LaboratoryCampaign(path).evaluate()
    assert report["criteria_result"] == "blocked_missing_evidence"
    assert report["blockers"] and report["metrics"] is None
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert candidate["results"] == reference["comparisons"] == []


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_populated_endpoint_preserves_synthetic_origin(tmp_path, profile_id):
    fixture = SyntheticCampaign(tmp_path / profile_id, profile_id)
    report, candidate, reference = fixture.evaluate()
    assert report["criteria_result"] == "blocked_missing_evidence"
    assert report["metrics"] is None
    assert report["quantified_held_out_comparison_count"] == 2
    assert any("Synthetic" in item for item in report["blockers"])
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert candidate["results"][0]["value"] == reference["comparisons"][0]["candidate"]["value"]


def test_both_method_uncertainties_enter_guard(campaign):
    campaign.declared_measured_branch()
    report, _, reference = campaign.evaluate()
    assert report["criteria_result"] == "passed_declared_criteria"
    assert report["physical_validation"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert reference["comparisons"][0]["combined_standard_uncertainty"] == pytest.approx(0.05)
    group = report["metrics"]["groups"]["lot-0"]
    assert group["bias"] == pytest.approx(0.02)
    assert group["max_combined_expanded_uncertainty"] == pytest.approx(0.1)
    for metric in ("abs_bias_guard", "rmse_guard", "max_abs_error_guard"):
        assert group[metric] == pytest.approx(0.12)


@pytest.mark.parametrize("role", ["candidate", "reference"])
def test_either_method_uncertainty_can_fail_criteria(campaign, role):
    campaign.declared_measured_branch()
    campaign.rows[role][0]["standard_uncertainty"] = "1"
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "failed_declared_criteria"
    assert "lot-0:max_combined_expanded_uncertainty" in report["failed_checks"]


@pytest.mark.parametrize("qualifier,limit", [
    ("below_detection", "detection_limit"), ("below_quantification", "quantification_limit")])
def test_censoring_is_retained_and_never_becomes_zero(campaign, qualifier, limit):
    campaign.declared_measured_branch()
    row = campaign.rows["candidate"][0]
    row.update(value="", standard_uncertainty="", qualifier=qualifier)
    row[limit] = "0.1"
    report, candidate, reference = campaign.evaluate()
    assert candidate["results"][0]["value"] is None
    assert candidate["results"][0]["standard_uncertainty"] is None
    assert candidate["results"][0][limit] == pytest.approx(0.1)
    pair = reference["comparisons"][0]
    assert not pair["quantified"] and "difference" not in pair
    assert report["metrics"]["quantified_fraction"] == 0.5
    assert "min_quantified_fraction" in report["failed_checks"]


@pytest.mark.parametrize("qualifier", ["below_detection", "below_quantification", "missing", "invalid", "out_of_range"])
def test_unquantified_row_rejects_numeric_substitution(campaign, qualifier):
    campaign.rows["candidate"][0].update(value="0", standard_uncertainty="", qualifier=qualifier,
        detection_limit="0.1", quantification_limit="0.2")
    with pytest.raises(ValueError, match="substitute"):
        campaign.evaluate()


@pytest.mark.parametrize("change", ["missing_lod", "missing_loq", "reversed_limits", "quantified_below_loq"])
def test_detection_limits_fail_closed(campaign, change):
    row = campaign.rows["candidate"][0]
    if change.startswith("missing"):
        row.update(value="", standard_uncertainty="",
            qualifier="below_detection" if change == "missing_lod" else "below_quantification")
    elif change == "reversed_limits":
        row.update(detection_limit="2", quantification_limit="1")
    else:
        row["quantification_limit"] = "20"
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_temperature_does_not_accept_concentration_censoring(tmp_path):
    fixture = SyntheticCampaign(tmp_path / "dsc", "polymer.dsc_tg_midpoint.v1")
    fixture.rows["candidate"][0].update(value="", standard_uncertainty="",
        qualifier="below_detection", detection_limit="100")
    with pytest.raises(ValueError, match="concentration or fraction"):
        fixture.evaluate()


@pytest.mark.parametrize("role", ["candidate", "reference"])
@pytest.mark.parametrize("field", ["sample_id", "parent_sample_id", "lot_id"])
def test_development_holdout_lineage_overlap_is_refused(campaign, role, field):
    campaign.add_development()
    campaign.rows[role][-1][field] = campaign.rows[role][0][field]
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("field", ["sample_id", "parent_sample_id", "lot_id"])
def test_cross_role_lineage_overlap_is_refused(campaign, field):
    campaign.add_development()
    campaign.rows["reference"][-1][field] = campaign.rows["candidate"][0][field]
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("row_role", ["candidate", "reference"])
@pytest.mark.parametrize("cal_role", ["candidate", "reference"])
@pytest.mark.parametrize("kind", ["sample_id", "parent_sample_id", "lot_id"])
def test_calibration_overlap_is_refused_across_roles(campaign, row_role, cal_role, kind):
    field = "calibration_lot_ids" if kind == "lot_id" else "calibration_sample_ids"
    campaign.m[cal_role][field].append(campaign.rows[row_role][0][kind])
    with pytest.raises(ValueError, match="calibration"):
        campaign.evaluate()


@pytest.mark.parametrize("row_role", ["candidate", "reference"])
@pytest.mark.parametrize("cal_role", ["candidate", "reference"])
def test_reference_material_cannot_be_calibrant(campaign, row_role, cal_role):
    campaign.rows[row_role][0]["reference_material_id"] = campaign.m[cal_role]["calibration_material_ids"][0]
    with pytest.raises(ValueError, match="[Cc]alibr"):
        campaign.evaluate()


@pytest.mark.parametrize("field", ["instrument_ids", "calibration_material_ids"])
def test_shared_instrument_or_calibrant_requires_covariance_model(campaign, field):
    campaign.m["reference"][field] = campaign.m["candidate"][field][:]
    with pytest.raises(ValueError, match="covariance"):
        campaign.evaluate()


def test_correlated_budget_cannot_use_independent_guard(campaign):
    campaign.m["uncertainty_budget"]["assumption"] = "correlated"
    with pytest.raises(ValueError, match="covariance"):
        campaign.evaluate()


def test_same_method_and_laboratory_cannot_be_reference(campaign):
    for field in ("laboratory_id", "method_id"):
        campaign.m["reference"][field] = campaign.m["candidate"][field]
    with pytest.raises(ValueError, match="independent reference"):
        campaign.evaluate()


def test_replicates_do_not_inflate_independent_groups(campaign):
    campaign.declared_measured_branch()
    for rows in campaign.rows.values():
        for row in rows:
            row.update(parent_sample_id="parent-0", lot_id="lot-0")
    campaign.m["held_out_policy"]["reserved_group_ids"] = ["lot-0"]
    report, _, _ = campaign.evaluate()
    assert report["metrics"]["held_out_groups"] == 1
    assert "min_held_out_groups" in report["failed_checks"]
    assert report["metrics"]["groups"]["lot-0"]["abs_bias_guard"] == pytest.approx(0.12)


def test_parent_sample_grouping_counts_original_samples(campaign):
    campaign.declared_measured_branch()
    campaign.m["held_out_policy"].update(split_unit="parent_sample",
        reserved_group_ids=["parent-0", "parent-1"])
    report, _, _ = campaign.evaluate()
    assert report["metrics"]["held_out_groups"] == 2


@pytest.mark.parametrize("field", ["candidate_results_sha256", "reference_results_sha256", "criteria_sha256"])
def test_frozen_digest_tampering_is_refused(campaign, field):
    campaign.m["held_out_policy"][field] = "sha256:" + "0"*64
    with pytest.raises(ValueError, match="Frozen"):
        campaign.evaluate(refresh=False)


@pytest.mark.parametrize("field,value", [
    ("split_frozen_at_utc", "2026-06-01T12:00:00Z"),
    ("method_frozen_at_utc", "2026-06-01T12:00:00Z"),
    ("candidate_results_frozen_at_utc", "2026-06-01T12:00:00Z"),
    ("reference_unsealed_at_utc", "2026-06-01T12:00:02Z")])
def test_freeze_chronology_is_enforced(campaign, field, value):
    campaign.m["held_out_policy"][field] = value
    with pytest.raises(ValueError, match="chronology"):
        campaign.evaluate()


def test_criteria_must_precede_heldout_measurements(campaign):
    campaign.m["criteria"]["approved_at_utc"] = "2026-06-01T12:00:00Z"
    with pytest.raises(ValueError, match="before held-out"):
        campaign.evaluate()


def test_draft_criteria_block_metrics(campaign):
    campaign.declared_measured_branch()
    campaign.m["criteria"]["status"] = "draft"
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "blocked_missing_evidence"
    assert report["metrics"] is None


@pytest.mark.parametrize("field", ["comparability_review", "uncertainty_budget", "held_out_policy", "criteria"])
def test_missing_reviews_block_acceptance(campaign, field):
    campaign.declared_measured_branch()
    campaign.m[field] = None
    report, _, _ = campaign.evaluate()
    assert report["criteria_result"] == "blocked_missing_evidence" and report["metrics"] is None


def test_reference_cannot_be_used_for_development(campaign):
    campaign.m["held_out_policy"]["reference_used_for_development"] = True
    with pytest.raises(ValueError, match="excluded from development"):
        campaign.evaluate()


@pytest.mark.parametrize("role", ["candidate", "reference"])
def test_development_data_must_precede_method_freeze(campaign, role):
    campaign.add_development()
    campaign.rows[role][-1]["measured_at_utc"] = "2026-06-01T12:00:00Z"
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_reference_measurements_cannot_postdate_unseal(campaign):
    campaign.rows["reference"][0]["measured_at_utc"] = "2026-06-02T12:00:00Z"
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_imported_rows_cannot_be_silently_excluded(campaign):
    campaign.m["comparisons"].pop()
    with pytest.raises(ValueError, match="Every imported result"):
        campaign.evaluate()


def test_method_reference_result_cannot_be_reused(campaign):
    campaign.m["comparisons"][1]["reference_result_id"] = "reference-0"
    with pytest.raises(ValueError, match="Only certified"):
        campaign.evaluate()


def test_crm_assigned_value_can_be_reused(campaign):
    campaign.m["reference"].update(evidence_type="crm_assigned_value",
        reporting_stage="certified_assigned_value", reference_certificate=campaign.evidence("CRM"))
    campaign.rows["reference"] = [campaign.rows["reference"][0]]
    campaign.rows["reference"][0]["reference_material_id"] = "SYNTHETIC-independent-CRM"
    campaign.m["comparisons"][1]["reference_result_id"] = "reference-0"
    report, _, reference = campaign.evaluate()
    assert len(reference["comparisons"]) == 2 and report["physical_validation"] == "not_established"


def test_scope_mutation_invalidates_role_binding(campaign):
    campaign.m["scope"]["matrix"] = "changed matrix"
    with pytest.raises(ValueError, match="scope binding"):
        campaign.evaluate()


@pytest.mark.parametrize("field,value", [("value", "NaN"), ("value", "1e9999"), ("value", "1e-9999"),
    ("standard_uncertainty", "-0.1"), ("qualifier", "estimated"),
    ("measured_at_utc", "2026-06-01T12:00:00"), ("measured_at_utc", "2025-06-01T12:00:00Z")])
def test_invalid_result_tokens_and_applicability_are_refused(campaign, field, value):
    campaign.rows["candidate"][0][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_bad_evidence_hash_is_refused(campaign):
    descriptor = campaign.m["candidate"]["raw_evidence"][0]
    (campaign.root / descriptor["file"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.evaluate()


def test_evidence_path_cannot_escape_campaign(campaign):
    outside = campaign.root.parent / "outside.txt"
    outside.write_bytes(b"SYNTHETIC outside")
    campaign.m["candidate"]["raw_evidence"][0] = {"file": "../outside.txt", "sha256": digest(outside.read_bytes())}
    with pytest.raises(ValueError, match="inside its campaign"):
        campaign.evaluate()


def test_retained_bytes_reference_separation_and_create_only_output(campaign, tmp_path):
    campaign.save()
    original = campaign.path.read_bytes()
    output = tmp_path / "bundle"
    report = adapt_campaign(campaign.path, output)
    assert (output / "campaign.json").read_bytes() == original
    assert report["manifest_sha256"] == digest(original)
    for role in ("candidate", "reference"):
        data = campaign.m[role]
        for descriptor in [data["results"], *data["raw_evidence"], *data["calibration_evidence"]]:
            path = output / "raw" / (descriptor["sha256"].split(":")[1] + ".bin")
            assert path.read_bytes() == (campaign.root / descriptor["file"]).read_bytes()
    candidate = load((output / "candidate-input.json").read_bytes())
    assert len(candidate["results"]) == 2
    assert all(row["result_id"].startswith("candidate-") for row in candidate["results"])
    assert "reference-0" not in json.dumps(candidate)
    assert campaign.m["reference"]["results"]["sha256"] not in json.dumps(candidate)
    with pytest.raises(FileExistsError):
        adapt_campaign(campaign.path, output)
    assert (output / "campaign.json").read_bytes() == original



def test_identical_result_bytes_cannot_be_independent_despite_distinct_metadata(campaign):
    campaign.rows["reference"] = deepcopy(campaign.rows["candidate"])
    for comparison in campaign.m["comparisons"]:
        comparison["reference_result_id"] = comparison["candidate_result_id"]
    campaign.save()
    a, b = campaign.m["candidate"], campaign.m["reference"]
    assert a["results"]["file"] != b["results"]["file"]
    assert a["laboratory_id"] != b["laboratory_id"]
    assert a["instrument_ids"] != b["instrument_ids"]
    assert a["results"]["sha256"] == b["results"]["sha256"]
    with pytest.raises(ValueError, match="identical result evidence"):
        campaign.evaluate()


@pytest.mark.parametrize("field", ["raw_evidence", "calibration_evidence"])
def test_identical_raw_or_calibration_bytes_require_specialized_independence(campaign, field):
    original = campaign.m["candidate"][field][0]
    raw = (campaign.root / original["file"]).read_bytes()
    copy = campaign.write("different-reference-" + field + ".txt", raw)
    campaign.m["reference"][field] = [copy]
    assert original["file"] != copy["file"] and original["sha256"] == copy["sha256"]
    with pytest.raises(ValueError, match="specialized independence/covariance"):
        campaign.evaluate()


@pytest.mark.parametrize("second_role", ["candidate", "reference"])
@pytest.mark.parametrize("same_lot", [False, True])
def test_sample_id_cannot_relabel_an_original_parent_as_a_nested_sample(campaign, second_role, same_lot):
    # Reusing a parent as a child of another parent changes original lineage,
    # even when the outer lot matches and every comparison is held out.
    campaign.rows["candidate"][0].update(sample_id="s1", parent_sample_id="p1", lot_id="L1")
    campaign.rows[second_role][1].update(sample_id="p1", parent_sample_id="p2",
                                       lot_id="L1" if same_lot else "L2")
    with pytest.raises(ValueError, match="Global sample/parent identity"):
        campaign.evaluate()
