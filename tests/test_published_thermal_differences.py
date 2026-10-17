"""Historical source tokens; synthetic PDF bytes test file handling only."""
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from ciw.measurement_evidence import digest, dump, load
import ciw.published_thermal_differences as thermal

# Independently transcribed complete Table 23: lab,t,R,Rextrap,W,d,En,U.
SOURCE_ROWS = [
    ("CEM_first_calibration", "99.283", "138.2936", "138.5660", "1.385549", "0.000", "0.00", "0.022"),
    ("CENAM", "99.971", "138.5459", "138.5569", "1.385514", "-0.009", "0.37", "0.009"),
    ("SENCAMER", "99.928", "138.5030", "138.5304", "1.385248", "-0.079", "2.36", "0.024"),
    ("IEN-CMFT", "99.985", "138.6182", "138.6239", "1.385551", "0.001", "0.02", "0.028"),
    ("SIC", "100.003", "138.5675", "138.5662", "1.385525", "-0.006", "0.21", "0.018"),
    ("INDECOPI", "100.004", "138.5732", "138.5719", "1.385602", "0.014", "0.34", "0.034"),
    ("IBMETRO", "99.740", "138.4660", "138.5649", "1.385532", "-0.004", "0.14", "0.022"),
    ("CEM_last_calibration", "99.683", "138.4466", "138.5673", "1.385548", "0.000", "0.00", "0.022"),
]
FACTORS = {"CENAM": ("2", 51), "SENCAMER": (None, 72), "IEN-CMFT": (None, 59),
           "SIC": ("2", 75), "INDECOPI": ("2", 67), "IBMETRO": ("2", 56)}

def document(source_hash):
    rows, controls = [], []
    for lab, t, r, rt, w, d, en, u in SOURCE_ROWS:
        common = dict(source_measured_temperature_C=t, source_measured_resistance_ohm=r,
            source_extrapolated_resistance_ohm=rt, source_W_ratio=w, source_pdf_page=33)
        if lab.startswith("CEM_"):
            controls.append(dict(common, role=lab, reported_difference_C=d, reported_En=en,
                reported_expanded_uncertainty_C=u))
            continue
        k, page = FACTORS[lab]
        row = {name: "Curation text" for name in thermal.ROW_FIELDS.split()}
        with localcontext() as ctx:
            ctx.prec = 200
            ul, ur = Decimal(u), Decimal("0.022")
            ud = (ul * ul + ur * ur).sqrt()
            row.update(common, id="SIM.T-S2-T23-" + lab, material="Large PRT 16004/1",
                candidate_lab=lab, reference_lab="CEM", quantity="temperature_difference",
                nominal_temperature_C="100", source_unit="degC_difference", unit="uK",
                source_table="Table23", source_locator=thermal.SOURCE_URL + "#page=33",
                reported_difference=d, reported_difference_unit="degC_difference",
                reported_expanded_uncertainty=None, candidate_reported_expanded_uncertainty=u,
                reference_reported_expanded_uncertainty="0.022", expanded_uncertainty_source_unit="degC_difference",
                candidate_reported_coverage_factor=k, reference_reported_coverage_factor="2",
                coverage_source_pdf_page=page, reference_coverage_source_pdf_page=49,
                reconstruction_coverage_convention=k, reported_En=en,
                source_issue="Unresolved source evidence." if lab in ("SENCAMER", "IEN-CMFT", "IBMETRO") else None,
                eligible_for_published_pass_fail=False, difference_uK=str(Decimal(d) * 1000000),
                candidate_standard_uncertainty_uK_under_k2_convention=str(ul * 500000) if k else None,
                reference_standard_uncertainty_uK_under_k2_convention="11000",
                reconstructed_difference_expanded_uncertainty_uK=format(ud * 1000000, ".45g"),
                reconstructed_absolute_normalized_deviation=format(abs(Decimal(d)) / ud, ".45g"),
                reconstruction_is_reported_literal=False, reconstructed_difference_coverage_factor=k,
                formal_qualification="not_established")
        rows.append(row)
    doc = {name: "Curation text" for name in thermal.DOC_FIELDS.split()}
    doc.update(dataset_id="SIM.T-S2-Table23-large-PRT-100C",
        status="retrospective_published_measurement_table_with_reconstructed_uncertainty",
        release_status="unchanged", physical_validation="not_established",
        local_instrument_qualification="not_established", local_criteria_approval="not_established",
        source_url=thermal.SOURCE_URL, source_doi="https://doi.org/10.1088/0026-1394/50/1A/03008",
        publication_date="2013-04", measurement_period="2004-2005",
        raw_source_sha256=source_hash.split(":")[1], temperature_scale="ITS-90",
        reference_assignment=dict(
            construction="Mean of CEM first and last calibrations only; participant values excluded from assignment.",
            source_pdf_page=14, equations=["2", "3"], covariance_treatment="Correlated CEM uncertainties retained.",
            table_reference_expanded_uncertainty_C="0.022", reference_standard_uncertainty_under_k2_C="0.011",
            candidate_reference_covariance="Author convention, not independently established covariance."),
        criterion=dict(source_equation_pdf_page=15, protocol_equation_pdf_page=85,
            source_equation="E_L,t = (W_L,t - W_R,t) / sqrt(U_L,t^2 + U_R,t^2)",
            published_numeric_acceptance_threshold_found=False, published_less_than_vs_less_than_or_equal=None,
            safe_analysis_basis="descriptive_analysis", optional_descriptive_rule="Conditional interval.",
            optional_descriptive_boundary="abs(d) <= reconstructed_U_difference", local_instrument_approved=False,
            coverage_limit="No confidence probability claim."),
        source_pages=dict(measurement_dates_and_ITS90=3, instruments_and_traceability=13,
            reference_construction_and_uncertainty=14, difference_definitions_and_equation=15,
            full_selected_table=33, later_SENCAMER_corrections=41,
            discussion_of_SENCAMER_large_PRT_deviations=46, protocol_reporting_and_uncertainties=84,
            protocol_analysis_equation=85),
        pilot_control_rows=controls, recording_notes=["Source digits retained."],
        candidate_screening=[dict(comparison=x, primary_url="https://example.invalid/screening",
            reason_not_selected="Not used.") for x in ("APMP.T-S5", "AFRIMETS.T-S5")], records=rows)
    return doc

class Campaign:
    def __init__(self, root, monkeypatch):
        self.root = root
        root.mkdir()
        self.source = b"%PDF-1.4\nSYNTHETIC FILE-HANDLING FIXTURE, NOT MEASUREMENT EVIDENCE\n"
        # Only the fixture overrides the fixed production hash for isolated I/O.
        monkeypatch.setattr(thermal, "SOURCE_SHA256", digest(self.source))
        self.doc = document(digest(self.source))
        self.manifest = dict(schema=thermal.SCHEMA, campaign_id="test-table23",
            analysis_mode="retrospective_published_report",
            source=self.write("source.pdf", self.source), records=None, output_unit="uK")
        self.path = root / "campaign.json"
        self.save()
    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        return dict(file=name, sha256=digest(raw))
    def save(self, records=True):
        if records:
            self.manifest["records"] = self.write("records.json", dump(self.doc))
        self.path.write_bytes(dump(self.manifest))
        return self.path
    def evaluate(self, records=True):
        return thermal.PublishedThermalDifferences(self.save(records)).evaluate()

@pytest.fixture
def campaign(tmp_path, monkeypatch):
    return Campaign(tmp_path / "campaign", monkeypatch)

def test_eight_actual_source_rows_and_only_six_candidates(campaign):
    report = campaign.evaluate()
    assert report["counts"] == dict(retained_candidate_rows=6, retained_pilot_controls=2,
        unflagged_descriptive_rows=3, source_review_rows=3, conditional_intervals_including_zero=5)
    for result, source in zip(report["results"], SOURCE_ROWS[1:-1]):
        assert result["candidate_lab"] == source[0]
        assert result["source_literals"]["difference"] == source[5]
        assert result["source_literals"]["absolute_normalized_deviation"] == source[6]
        assert result["source_literals"]["candidate_expanded_component"] == source[7]
        assert result["source_rounding_check"]["compatible"] is True
    assert all(c["independent_candidate"] is False and c["numerical_interval_evaluated"] is False
               for c in report["pilot_controls"])

def test_unknown_coverage_and_source_discrepancy_survive(campaign):
    rows = {r["candidate_lab"]: r for r in campaign.evaluate()["results"]}
    for lab in ("SENCAMER", "IEN-CMFT"):
        assert rows[lab]["source_candidate_coverage_factor"] is None
        assert rows[lab]["reconstructed_difference_coverage_factor"] is None
        assert "standard_uncertainty" not in str(rows[lab])
        assert rows[lab]["eligible"] is False
    assert "0.040" in rows["IBMETRO"]["source_issue"]
    assert rows["IBMETRO"]["source_literals"]["candidate_expanded_component"] == "0.022"
    assert rows["IBMETRO"]["eligible"] is False
    assert rows["SENCAMER"]["conditional_source_expanded_interval_includes_zero"] is False

def test_expanded_components_no_extra_factor_and_inclusive_boundary():
    r = thermal.reconstruct("-5", "3", "4", "1.0", "K")
    assert r["reconstructed_expanded_difference"] == "5"
    assert Decimal(r["absolute_normalized_deviation"]) == 1
    assert r["conditional_source_expanded_interval_includes_zero"] is True

def test_units_no_temperature_offset_and_precision200(campaign):
    micro = campaign.evaluate()["results"][0]
    campaign.manifest["output_unit"] = "K"
    kelvin = campaign.evaluate()["results"][0]
    assert Decimal(kelvin["difference"]) == Decimal("-0.009")
    assert Decimal(micro["difference"]) == Decimal("-9000")
    assert len(Decimal(kelvin["reconstructed_expanded_difference"]).as_tuple().digits) == 200
    with localcontext() as ctx:
        ctx.prec = 200
        expected = (Decimal(".009")**2 + Decimal(".022")**2).sqrt()
        assert Decimal(kelvin["reconstructed_expanded_difference"]) == expected
        assert Decimal(micro["reconstructed_expanded_difference"]) == expected * 1000000

def test_En_rounding_uses_all_source_columns_and_preserves_printed_value():
    r = thermal.reconstruct("-.079", ".024", ".022", "2.36")
    assert r["source_rounding_check"]["compatible"] is True
    r = thermal.reconstruct("-.079", ".024", ".022", "0.01")
    assert r["source_rounding_check"]["compatible"] is False
    assert r["source_rounding_check"]["reported_En"] == "0.01"

def test_no_fabricated_pair_or_qualification(campaign):
    r = campaign.evaluate()
    for field in ("formal_qualification", "physical_validation", "prospective_held_out_campaign",
                  "local_instrument_qualification", "local_criteria_approval"):
        assert r[field] == "not_established"
    assert r["release_status"] == "unchanged"
    assert r["criterion"]["local_instrument_approved"] is False
    assert r["criterion"]["confidence_probability_claimed"] is None
    assert r["criterion"]["source_numerical_acceptance_threshold_found"] is False
    assert "assumes zero candidate/reference covariance" in r["uncertainty_convention"]
    assert "candidate_value" not in str(r) and "reference_value" not in str(r)
    assert all(x["status"] in ("descriptive_only", "source_review_required") for x in r["results"])

@pytest.mark.parametrize("change", ["missing", "duplicate", "reorder", "control_missing", "control_reorder", "wrong_lab"])
def test_completeness_and_identity(campaign, change):
    d = campaign.doc
    if change == "missing": d["records"].pop()
    elif change == "duplicate": d["records"].append(deepcopy(d["records"][0]))
    elif change == "reorder": d["records"].reverse()
    elif change == "control_missing": d["pilot_control_rows"].pop()
    elif change == "control_reorder": d["pilot_control_rows"].reverse()
    else: d["records"][0]["candidate_lab"] = "CEM"
    with pytest.raises(ValueError):
        campaign.evaluate()

@pytest.mark.parametrize("field,value", [
    ("source_pdf_page", 34), ("source_table", "Table22"), ("source_locator", "https://example.invalid"),
    ("reported_difference", "-0.008"), ("candidate_reported_expanded_uncertainty", "0.010"),
    ("reported_En", "0.99"), ("reported_expanded_uncertainty", "0.024"),
    ("reconstruction_is_reported_literal", True), ("eligible_for_published_pass_fail", True),
    ("formal_qualification", "established"), ("difference_uK", "1"),
    ("reconstructed_difference_expanded_uncertainty_uK", "1"),
    ("reconstructed_absolute_normalized_deviation", "1"), ("source_unit", "degC"),
    ("candidate_value", "1"), ("reference_value", "0")])
def test_literal_and_reconstruction_integrity(campaign, field, value):
    campaign.doc["records"][0][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()

@pytest.mark.parametrize("field,value", [
    ("candidate_reported_coverage_factor", "2"), ("reconstructed_difference_coverage_factor", "2"),
    ("candidate_standard_uncertainty_uK_under_k2_convention", "12000"), ("source_issue", None)])
def test_coverage_cannot_be_promoted(campaign, field, value):
    campaign.doc["records"][1][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()

@pytest.mark.parametrize("field,value", [
    ("safe_analysis_basis", "published_report"), ("local_instrument_approved", True),
    ("local_instrument_approved", 0), ("published_numeric_acceptance_threshold_found", True),
    ("published_less_than_vs_less_than_or_equal", "<"), ("source_equation_pdf_page", 85),
    ("source_equation", "d/UL")])
def test_curation_cannot_promote_acceptance(campaign, field, value):
    campaign.doc["criterion"][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()

def test_consensus_reference_refused(campaign):
    campaign.doc["reference_assignment"]["construction"] = "Participant consensus"
    with pytest.raises(ValueError, match="reference construction"):
        campaign.evaluate()

@pytest.mark.parametrize("value", [True, .01, "NaN", "Infinity", "1e-999", " 1", "1" * 101])
def test_bad_decimal_literals(value):
    with pytest.raises(ValueError):
        thermal.reconstruct(value, ".01", ".02", ".1")

@pytest.mark.parametrize("value", ["0", "-.01"])
def test_positive_uncertainties_required(value):
    with pytest.raises(ValueError):
        thermal.reconstruct("1", value, ".02", ".1")

@pytest.mark.parametrize("unit", ["degC", "mK", "kg/m3", None])
def test_no_implicit_unit_conversion(campaign, unit):
    campaign.manifest["output_unit"] = unit
    with pytest.raises(ValueError):
        campaign.evaluate()

def test_pdf_fixed_edition_enforced(campaign):
    campaign.manifest["source"] = campaign.write("other.pdf", b"%PDF-1.4\nother edition")
    with pytest.raises(ValueError, match="reviewed SIM.T-S2"):
        campaign.evaluate()

@pytest.mark.parametrize("name", ["source.pdf", "records.json"])
def test_hash_tampering(campaign, name):
    (campaign.root / name).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.evaluate(records=False)

@pytest.mark.parametrize("descriptor", ["source", "records"])
def test_path_escape(campaign, descriptor):
    p = campaign.root.parent / "outside.bin"
    p.write_bytes(campaign.source)
    campaign.manifest[descriptor] = dict(file="../outside.bin", sha256=digest(campaign.source))
    with pytest.raises(ValueError, match="inside its campaign"):
        campaign.evaluate(records=False)

def test_duplicate_json_keys(campaign):
    campaign.manifest["records"] = campaign.write("bad.json", b'{"records":[],"records":[]}')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        campaign.evaluate(records=False)

def test_bundle_exact_evidence_and_no_overwrite(campaign, tmp_path):
    output = tmp_path / "bundle"
    r = thermal.adapt_campaign(campaign.path, output)
    assert (output / "source-records.json").read_bytes() == (campaign.root / "records.json").read_bytes()
    assert (output / "raw" / (digest(campaign.source).split(":")[1] + ".bin")).read_bytes() == campaign.source
    assert (output / "campaign.json").read_bytes() == campaign.path.read_bytes()
    assert load((output / "report.json").read_bytes()) == r
    with pytest.raises(FileExistsError):
        thermal.adapt_campaign(campaign.path, output)

def test_report_last_on_interrupted_evidence_write(campaign, tmp_path, monkeypatch):
    output = tmp_path / "partial"
    original = Path.write_bytes
    def interrupted(path, data):
        if path.name == "source-records.json":
            raise OSError("synthetic interrupted write")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        thermal.adapt_campaign(campaign.path, output)
    assert not (output / "report.json").exists()

def test_cli_completion_not_acceptance(campaign, tmp_path, capsys):
    args = ["--manifest", str(campaign.path), "--output-dir", str(tmp_path / "cli")]
    assert thermal.main(args) == 0
    assert load(capsys.readouterr().out.encode())["formal_qualification"] == "not_established"
    assert thermal.main(args) == 1
    assert load(capsys.readouterr().err.encode())["status"] == "refused"
