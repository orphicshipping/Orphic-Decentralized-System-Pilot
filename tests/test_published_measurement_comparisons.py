"""Synthetic source files exercise retrospective comparison software, not metrology."""
from copy import deepcopy
from decimal import Decimal
import pytest

from ciw.measurement_evidence import digest, dump, load
from ciw.published_measurement_comparisons import (
    SCHEMA, PublishedComparison, adapt_campaign, evaluate_row)


def criterion(rule="En_lt_1", basis="published_report"):
    return dict(rule=rule, basis=basis, source_locator="SYNTHETIC page 1",
                local_instrument_approved=False)


def row(**changes):
    result = dict(id="synthetic-row", material="SYNTHETIC material",
        candidate_lab="SYNTHETIC candidate", reference_lab="SYNTHETIC reference",
        quantity="liquid_density", unit="kg/m3", kind="independent_pair",
        candidate_value="756.988", reference_value="756.990",
        candidate_standard_uncertainty="0.016", reference_standard_uncertainty="0.006",
        reported_difference="-0.002", reported_expanded_uncertainty="0.034",
        reported_expanded_coverage_factor="2",
        reported_En="0.1", source_locator="SYNTHETIC table 1",
        source_issue=None, eligible=True)
    result.update(changes)
    return result


class SyntheticPublishedCampaign:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.path = root / "campaign.json"
        self.rows = [row()]
        self.m = dict(schema=SCHEMA, campaign_id="SYNTHETIC software test",
            analysis_mode="retrospective_published_report",
            source=self.write("source.txt", b"SYNTHETIC source fixture; no physical measurements.\r\n"),
            source_url="https://example.invalid/synthetic-source", source_title="SYNTHETIC source",
            publication_date="SYNTHETIC publication-date field", records=None,
            method_summary="SYNTHETIC method", calibration_summary="SYNTHETIC calibration",
            reference_summary="SYNTHETIC reference",
            reference_independence="reported_independent_assignment",
            criterion=criterion(), selection_summary="SYNTHETIC fixed selection")
        self.save()

    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        return dict(file=name, sha256=digest(raw))

    def save(self, records=True):
        if records:
            self.m["records"] = self.write("rows.json", dump(self.rows))
        self.path.write_bytes(dump(self.m))
        return self.path

    def evaluate(self, records=True):
        return PublishedComparison(self.save(records)).evaluate()


@pytest.fixture
def campaign(tmp_path):
    return SyntheticPublishedCampaign(tmp_path / "campaign")


@pytest.mark.parametrize("x,ux,d,En,passes", [
    ("756.988", "0.016", "-0.002", "0.0585205735967", True),
    ("757.471", "0.060", "0.481", "3.98844073742", False),
])
def test_known_independent_density_comparisons(x, ux, d, En, passes):
    result = evaluate_row(row(candidate_value=x, candidate_standard_uncertainty=ux), criterion(), True)
    assert Decimal(result["difference"]) == Decimal(d)
    assert float(result["absolute_normalized_deviation"]) == pytest.approx(float(En))
    assert result["rule_satisfied_numerically"] is passes
    assert result["eligible_independent_comparison"] is True


def test_chemistry_mass_fraction_keeps_unit_and_difference():
    result = evaluate_row(row(quantity="mass_fraction", unit="mg/g", candidate_value="999.14",
        reference_value="999.18", candidate_standard_uncertainty="0.020",
        reference_standard_uncertainty="0.015"), criterion(), True)
    assert result["unit"] == "mg/g"
    assert Decimal(result["difference"]) == Decimal("-0.04")
    assert Decimal(result["expanded_uncertainty_k2"]) == Decimal("0.050")
    assert Decimal(result["absolute_normalized_deviation"]) == Decimal("0.8")


@pytest.mark.parametrize("rule,passes", [("En_lt_1", False), ("interval_includes_zero_k2", True)])
def test_strict_and_inclusive_rules_differ_at_exact_boundary(rule, passes):
    # 2*sqrt(3^2+4^2)=10 exactly, so En is exactly one.
    result = evaluate_row(row(candidate_value="20", reference_value="10",
        candidate_standard_uncertainty="3", reference_standard_uncertainty="4",
        reported_difference=None, reported_expanded_uncertainty=None,
        reported_expanded_coverage_factor=None, reported_En=None),
        criterion(rule), True)
    assert Decimal(result["absolute_normalized_deviation"]) == 1
    assert result["rule_satisfied_numerically"] is passes


def test_descriptive_analysis_cannot_claim_published_acceptance(campaign):
    campaign.m["criterion"]["basis"] = "descriptive_analysis"
    report = campaign.evaluate()
    assert report["results"][0]["status"] == "descriptive_only"
    assert report["counts"]["satisfy_published_rule"] == 0


def test_metadata_preserves_retrospective_and_local_qualification_boundaries(campaign):
    report = campaign.evaluate()
    assert report["analysis_mode"] == "retrospective_published_report"
    assert report["prospective_held_out_campaign"] == "not_established"
    assert report["local_instrument_qualification"] == "not_established"
    assert report["release_status"] == "unchanged"
    assert report["criterion"]["local_instrument_approved"] is False
    for field in ("method_summary", "calibration_summary", "reference_summary", "selection_summary"):
        assert report[field] == campaign.m[field]


def test_flagged_source_row_stays_visible_but_ineligible(campaign):
    campaign.rows[0].update(source_issue="SYNTHETIC contradictory uncertainty", eligible=False)
    report = campaign.evaluate()
    assert report["counts"]["retained_rows"] == report["counts"]["source_discrepancies"] == 1
    assert report["counts"]["eligible_independent_rows"] == 0
    assert report["counts"]["satisfy_published_rule"] == 0
    assert report["results"][0]["status"] == "source_discrepancy_requires_review"


def test_source_issue_cannot_be_marked_eligible(campaign):
    campaign.rows[0]["source_issue"] = "SYNTHETIC contradictory uncertainty"
    with pytest.raises(ValueError, match="Unresolved source"):
        campaign.evaluate()


@pytest.mark.parametrize("bad", [True, 0.1, "NaN", "Infinity", "1e999", " 1"])
def test_nonliteral_or_nonfinite_decimals_are_rejected(bad):
    with pytest.raises(ValueError, match="decimal"):
        evaluate_row(row(candidate_value=bad), criterion(), True)


@pytest.mark.parametrize("field,value", [
    ("candidate_standard_uncertainty", "0"), ("reference_standard_uncertainty", "-0.01"),
    ("reported_expanded_uncertainty", "0"), ("reported_En", "-1")])
def test_invalid_uncertainties_and_reported_ratios_are_rejected(field, value):
    with pytest.raises(ValueError):
        evaluate_row(row(**{field: value}), criterion(), True)


@pytest.mark.parametrize("unit", ["g/cm3", "mg/g", "Pa.s"])
def test_no_silent_unit_conversion(unit):
    with pytest.raises(ValueError, match="unit"):
        evaluate_row(row(unit=unit), criterion(), True)


@pytest.mark.parametrize("change", ["duplicate", "nonstring", "empty"])
def test_record_identities_must_be_nonempty_unique_strings(campaign, change):
    if change == "duplicate":
        campaign.rows.append(deepcopy(campaign.rows[0]))
    else:
        campaign.rows[0]["id"] = 1 if change == "nonstring" else ""
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("independence", ["participant_consensus", "same_data_certification"])
def test_consensus_and_self_certification_cannot_use_independent_pair(campaign, independence):
    campaign.m["reference_independence"] = independence
    with pytest.raises(ValueError, match="independently assigned"):
        campaign.evaluate()


def test_same_laboratory_is_not_an_independent_pair():
    with pytest.raises(ValueError, match="independently assigned"):
        evaluate_row(row(candidate_lab="same", reference_lab="same"), criterion(), True)


def test_reported_difference_route_does_not_recombine_or_assert_independence(campaign):
    campaign.m["reference_independence"] = "participant_consensus"
    campaign.rows[0].update(kind="reported_difference", candidate_value=None,
        reference_value=None, candidate_standard_uncertainty=None,
        reference_standard_uncertainty=None, reported_difference="0.4",
        reported_expanded_uncertainty="0.5", reported_En="0.8")
    report = campaign.evaluate()
    result = report["results"][0]
    assert result["status"] == "descriptive_only"
    assert result["eligible_independent_comparison"] is False
    assert result["absolute_normalized_deviation"] == "0.8"
    assert "no independence or covariance inferred" in result["calculation"]
    assert "candidate_value" not in result
    assert "no covariance recombination" in result["uncertainty_assumption"]
    assert result["reported_expanded_coverage_factor"] == "2"


def test_reported_difference_route_rejects_implied_pair():
    with pytest.raises(ValueError, match="must not imply"):
        evaluate_row(row(kind="reported_difference"), criterion(), False)


@pytest.mark.parametrize("approved", [True, 0, None])
def test_local_criteria_approval_cannot_be_inferred(campaign, approved):
    campaign.m["criterion"]["local_instrument_approved"] = approved
    with pytest.raises(ValueError, match="approval"):
        campaign.evaluate()


def test_source_hash_mismatch_is_rejected(campaign):
    (campaign.root / "source.txt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.evaluate()


def test_records_hash_mismatch_is_rejected(campaign):
    (campaign.root / "rows.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.evaluate(records=False)


def test_source_path_must_stay_within_campaign(campaign):
    outside = campaign.root.parent / "outside.txt"
    outside.write_bytes(b"SYNTHETIC outside")
    campaign.m["source"] = dict(file="../outside.txt", sha256=digest(outside.read_bytes()))
    with pytest.raises(ValueError, match="inside its campaign"):
        campaign.evaluate()


def test_artifact_preserves_manifest_and_rows_and_refuses_overwrite(campaign, tmp_path):
    campaign.save()
    original_manifest = campaign.path.read_bytes()
    original_rows = (campaign.root / "rows.json").read_bytes()
    output = tmp_path / "output"
    report = adapt_campaign(campaign.path, output)
    assert (output / "campaign.json").read_bytes() == original_manifest
    assert (output / "records.json").read_bytes() == original_rows
    assert report["records_sha256"] == digest(original_rows)
    assert report["manifest_sha256"] == digest(original_manifest)
    with pytest.raises(FileExistsError):
        adapt_campaign(campaign.path, output)
    assert (output / "report.json").read_bytes() == dump(report)



@pytest.mark.parametrize("factor", ["3", None, True, 2])
def test_reported_expanded_uncertainty_requires_explicit_decimal_k2(factor):
    with pytest.raises(ValueError):
        evaluate_row(row(reported_expanded_coverage_factor=factor), criterion(), True)


def test_coverage_factor_without_reported_uncertainty_is_rejected():
    with pytest.raises(ValueError, match="without a reported expanded uncertainty"):
        evaluate_row(row(reported_expanded_uncertainty=None), criterion(), True)


def test_pair_uncertainty_explicitly_labels_zero_covariance_as_assumption():
    result = evaluate_row(row(), criterion(), True)
    assert "Zero candidate-reference covariance is ASSUMED" in result["uncertainty_assumption"]
    assert "independent value assignment alone does not establish it" in result["uncertainty_assumption"]
    assert result["reported_expanded_coverage_factor"] == "2"


@pytest.mark.parametrize("field", ["candidate_value", "reference_value"])
@pytest.mark.parametrize("value", ["0", "-0.001"])
def test_density_domain_rejects_nonpositive_values_in_either_method(field, value):
    with pytest.raises(ValueError, match="Liquid density must be positive"):
        evaluate_row(row(**{field: value}), criterion(), True)


@pytest.mark.parametrize("field", ["candidate_value", "reference_value"])
@pytest.mark.parametrize("value", ["-0.001", "1000.001"])
def test_mass_fraction_domain_rejects_outside_physical_range(field, value):
    with pytest.raises(ValueError, match="Mass fraction"):
        evaluate_row(row(quantity="mass_fraction", unit="mg/g", **{field: value}), criterion(), True)


@pytest.mark.parametrize("value", ["0", "1000"])
def test_mass_fraction_domain_accepts_both_physical_endpoints(value):
    result = evaluate_row(row(quantity="mass_fraction", unit="mg/g",
        candidate_value=value, reference_value=value), criterion(), True)
    assert result["difference"] == "0"
    assert result["rule_satisfied_numerically"] is True
