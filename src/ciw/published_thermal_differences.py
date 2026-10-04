"""Replay the published SIM.T-S2 Table 23 expanded-component calculation.

This is a fixed historical table reader, not a temperature calibration or a
qualification workflow. Printed differences remain differences; no artificial
candidate/reference readings are introduced.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import re
import sys

from .measurement_evidence import EvidenceStore, digest, dump, keys, load, text

SCHEMA = "ciw.published-thermal-differences.v1"
SOURCE_URL = "https://www.bipm.org/documents/20126/45532447/SIM.T-S2.pdf/9b4b7dd4-325b-2a86-4353-16ceeb7c8393"
SOURCE_SHA256 = "sha256:ea41023c7308bc52badd25349b7fc773febcf673f008888a68d878a864bd05e9"
DATASET = "SIM.T-S2-Table23-large-PRT-100C"

# Reviewed Table 23, PDF/printed page 33: t, R, R extrapolated, W, d, En, U.
# These literals bind this deliberately narrow reader to the reviewed table.
TABLE = {
    "CEM_first_calibration": ("99.283", "138.2936", "138.5660", "1.385549", "0.000", "0.00", "0.022"),
    "CENAM": ("99.971", "138.5459", "138.5569", "1.385514", "-0.009", "0.37", "0.009"),
    "SENCAMER": ("99.928", "138.5030", "138.5304", "1.385248", "-0.079", "2.36", "0.024"),
    "IEN-CMFT": ("99.985", "138.6182", "138.6239", "1.385551", "0.001", "0.02", "0.028"),
    "SIC": ("100.003", "138.5675", "138.5662", "1.385525", "-0.006", "0.21", "0.018"),
    "INDECOPI": ("100.004", "138.5732", "138.5719", "1.385602", "0.014", "0.34", "0.034"),
    "IBMETRO": ("99.740", "138.4660", "138.5649", "1.385532", "-0.004", "0.14", "0.022"),
    "CEM_last_calibration": ("99.683", "138.4466", "138.5673", "1.385548", "0.000", "0.00", "0.022"),
}
LABS = ("CENAM", "SENCAMER", "IEN-CMFT", "SIC", "INDECOPI", "IBMETRO")
COVERAGE = {"CENAM": ("2", 51), "SENCAMER": (None, 72), "IEN-CMFT": (None, 59),
            "SIC": ("2", 75), "INDECOPI": ("2", 67), "IBMETRO": ("2", 56)}
REVIEW_ISSUES = {
    "SENCAMER": "Coverage unresolved: page 72 has approximately twofold expansion but no explicit k declaration.",
    "IEN-CMFT": "Coverage unresolved: inspected page 59 budget identifies the short PRT, not this large PRT.",
    "IBMETRO": "Unresolved source discrepancy: Table 23 U=0.022 C; page 56 large-PRT budget U(k=2)=0.040 C.",
}
NUMERIC_SOURCE_FIELDS = ("source_measured_temperature_C", "source_measured_resistance_ohm",
                         "source_extrapolated_resistance_ohm", "source_W_ratio")
DOC_FIELDS = "dataset_id status release_status physical_validation local_instrument_qualification local_criteria_approval source_title source_url source_doi publication_date measurement_period raw_source_path raw_source_sha256 selection instrument temperature_scale quantity_semantics reference_assignment criterion source_pages pilot_control_rows recording_notes candidate_screening records"
ROW_FIELDS = "id material candidate_lab reference_lab quantity nominal_temperature_C source_unit unit source_pdf_page source_table source_locator source_measured_temperature_C source_measured_resistance_ohm source_extrapolated_resistance_ohm source_W_ratio reported_difference reported_difference_unit reported_expanded_uncertainty candidate_reported_expanded_uncertainty reference_reported_expanded_uncertainty expanded_uncertainty_source_unit candidate_reported_coverage_factor reference_reported_coverage_factor coverage_source_pdf_page reference_coverage_source_pdf_page reconstruction_coverage_convention reported_En source_issue eligible_for_published_pass_fail difference_uK candidate_standard_uncertainty_uK_under_k2_convention reference_standard_uncertainty_uK_under_k2_convention reconstructed_difference_expanded_uncertainty_uK reconstructed_absolute_normalized_deviation reconstruction_is_reported_literal reconstruction_formula no_covariance_proof reconstructed_difference_coverage_factor formal_qualification"
CONTROL_FIELDS = "role source_measured_temperature_C source_measured_resistance_ohm source_extrapolated_resistance_ohm source_W_ratio reported_difference_C reported_En reported_expanded_uncertainty_C source_pdf_page"
SOURCE_PAGES = dict(measurement_dates_and_ITS90=3, instruments_and_traceability=13,
    reference_construction_and_uncertainty=14, difference_definitions_and_equation=15,
    full_selected_table=33, later_SENCAMER_corrections=41,
    discussion_of_SENCAMER_large_PRT_deviations=46, protocol_reporting_and_uncertainties=84,
    protocol_analysis_equation=85)


def decimal(value, *, positive=False):
    if type(value) is not str or len(value) > 100 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
        raise ValueError("Require a bounded exact decimal string")
    result = Decimal(value)
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Expanded uncertainty must be positive and finite")
    return result


def same(actual, expected, label):
    if type(actual) is not type(expected) or actual != expected:
        raise ValueError("Unexpected " + label)


def half_quantum(value):
    """Half the last displayed decimal unit, not a measurement uncertainty."""
    return Decimal(1).scaleb(decimal(value).as_tuple().exponent) / 2


def validate_rounded(value, calculated):
    """Validate old curated derived digits; never substitute them for recalculation."""
    parsed = decimal(value)
    if abs(parsed - calculated) > half_quantum(value):
        raise ValueError("Curated reconstruction disagrees with source-component calculation")


def reconstruct(difference, candidate_U, reference_U, reported_En, output_unit="uK"):
    """Descriptive author-equation replay, without assuming a coverage factor."""
    if output_unit not in ("K", "uK"):
        raise ValueError("Output unit must be K or uK for temperature differences")
    with localcontext() as ctx:
        ctx.prec = 200
        d = decimal(difference)
        ul, ur = decimal(candidate_U, positive=True), decimal(reference_U, positive=True)
        en = decimal(reported_En)
        if en < 0:
            raise ValueError("Reported normalized deviation must be nonnegative")
        ud = (ul * ul + ur * ur).sqrt()
        ratio = abs(d) / ud
        # Account for rounding of all four printed columns, not only En.
        hd, hl, hr, he = map(half_quantum, (difference, candidate_U, reference_U, reported_En))
        if ul <= hl or ur <= hr:
            raise ValueError("Printed uncertainty rounding interval reaches zero")
        minimum = max(Decimal(0), abs(d) - hd) / ((ul + hl)**2 + (ur + hr)**2).sqrt()
        maximum = (abs(d) + hd) / ((ul - hl)**2 + (ur - hr)**2).sqrt()
        compatible = minimum <= en + he and maximum >= max(Decimal(0), en - he)
        scale = Decimal(1) if output_unit == "K" else Decimal(1000000)
        fmt = lambda value: format(value, "f")
        return dict(output_unit=output_unit, difference=fmt(d * scale),
            candidate_expanded_component=fmt(ul * scale), reference_expanded_component=fmt(ur * scale),
            reconstructed_expanded_difference=fmt(ud * scale),
            absolute_normalized_deviation=fmt(ratio),
            conditional_source_expanded_interval_includes_zero=abs(d) <= ud,
            source_rounding_check=dict(compatible=compatible,
                minimum_ratio_from_printed_rounding=fmt(minimum), maximum_ratio_from_printed_rounding=fmt(maximum),
                reported_En=reported_En, reported_En_half_quantum=fmt(he),
                recomputed_minus_reported=fmt(ratio - en)))


def validate_metadata(doc):
    keys(doc, DOC_FIELDS)
    for name, expected in dict(dataset_id=DATASET,
            status="retrospective_published_measurement_table_with_reconstructed_uncertainty",
            release_status="unchanged", physical_validation="not_established",
            local_instrument_qualification="not_established", local_criteria_approval="not_established",
            source_url=SOURCE_URL, source_doi="https://doi.org/10.1088/0026-1394/50/1A/03008",
            publication_date="2013-04", measurement_period="2004-2005", temperature_scale="ITS-90",
            raw_source_sha256=SOURCE_SHA256.split(":")[1]).items():
        same(doc[name], expected, name)
    for name in ("source_title", "raw_source_path", "selection", "instrument", "quantity_semantics"):
        text(doc[name])
    same(doc["source_pages"], SOURCE_PAGES, "source page coordinates")
    ref = doc["reference_assignment"]
    keys(ref, "construction source_pdf_page equations covariance_treatment table_reference_expanded_uncertainty_C reference_standard_uncertainty_under_k2_C candidate_reference_covariance")
    same(ref["source_pdf_page"], 14, "reference page")
    same(ref["equations"], ["2", "3"], "reference equations")
    same(ref["table_reference_expanded_uncertainty_C"], "0.022", "reference component")
    same(ref["reference_standard_uncertainty_under_k2_C"], "0.011", "reference standard uncertainty")
    same(ref["construction"], "Mean of CEM first and last calibrations only; participant values excluded from assignment.", "reference construction")
    for name in ("construction", "covariance_treatment", "candidate_reference_covariance"):
        text(ref[name])
    criterion = doc["criterion"]
    keys(criterion, "source_equation_pdf_page protocol_equation_pdf_page source_equation published_numeric_acceptance_threshold_found published_less_than_vs_less_than_or_equal safe_analysis_basis optional_descriptive_rule optional_descriptive_boundary local_instrument_approved coverage_limit")
    for name, expected in dict(source_equation_pdf_page=15, protocol_equation_pdf_page=85,
            source_equation="E_L,t = (W_L,t - W_R,t) / sqrt(U_L,t^2 + U_R,t^2)",
            published_numeric_acceptance_threshold_found=False, published_less_than_vs_less_than_or_equal=None,
            safe_analysis_basis="descriptive_analysis", local_instrument_approved=False).items():
        same(criterion[name], expected, name)
    for name in ("source_equation", "optional_descriptive_rule", "optional_descriptive_boundary", "coverage_limit"):
        text(criterion[name])
    if type(doc["recording_notes"]) is not list or not 1 <= len(doc["recording_notes"]) <= 20:
        raise ValueError("Require bounded curation notes")
    for note in doc["recording_notes"]:
        text(note)
    if type(doc["candidate_screening"]) is not list or len(doc["candidate_screening"]) != 2:
        raise ValueError("Require original bounded source screening metadata")
    for item in doc["candidate_screening"]:
        keys(item, "comparison primary_url reason_not_selected")
        for value in item.values():
            text(value)


def validate_row(row):
    keys(row, ROW_FIELDS)
    lab = row["candidate_lab"]
    if type(lab) is not str or lab not in LABS:
        raise ValueError("Unexpected Table 23 candidate")
    factor, page = COVERAGE[lab]
    expected = dict(id="SIM.T-S2-T23-" + lab, reference_lab="CEM", quantity="temperature_difference",
        nominal_temperature_C="100", source_unit="degC_difference", unit="uK", source_pdf_page=33,
        source_table="Table23", source_locator=SOURCE_URL + "#page=33",
        reported_difference_unit="degC_difference", reported_expanded_uncertainty=None,
        reference_reported_expanded_uncertainty="0.022", expanded_uncertainty_source_unit="degC_difference",
        candidate_reported_coverage_factor=factor, reference_reported_coverage_factor="2",
        coverage_source_pdf_page=page, reference_coverage_source_pdf_page=49,
        reconstruction_coverage_convention=factor, eligible_for_published_pass_fail=False,
        reconstruction_is_reported_literal=False, reconstructed_difference_coverage_factor=factor,
        formal_qualification="not_established")
    names = NUMERIC_SOURCE_FIELDS + ("reported_difference", "reported_En", "candidate_reported_expanded_uncertainty")
    expected.update(zip(names, TABLE[lab]))
    for name, value in expected.items():
        same(row[name], value, name)
    for name in ("material", "reconstruction_formula", "no_covariance_proof"):
        text(row[name])
    if lab in REVIEW_ISSUES:
        text(row["source_issue"])
    else:
        same(row["source_issue"], None, "source issue declaration")
    # Derived research fields are checked at their own printed precision. They
    # never become reported literals and do not set the new 200-digit results.
    with localcontext() as ctx:
        ctx.prec = 200
        d = decimal(row["reported_difference"])
        ul = decimal(row["candidate_reported_expanded_uncertainty"], positive=True)
        ur = decimal(row["reference_reported_expanded_uncertainty"], positive=True)
        ud = (ul * ul + ur * ur).sqrt()
        validate_rounded(row["difference_uK"], d * 1000000)
        validate_rounded(row["reference_standard_uncertainty_uK_under_k2_convention"], ur * 500000)
        if factor is None:
            same(row["candidate_standard_uncertainty_uK_under_k2_convention"], None, "unknown coverage standard uncertainty")
        else:
            validate_rounded(row["candidate_standard_uncertainty_uK_under_k2_convention"], ul * 500000)
        validate_rounded(row["reconstructed_difference_expanded_uncertainty_uK"], ud * 1000000)
        validate_rounded(row["reconstructed_absolute_normalized_deviation"], abs(d) / ud)


class PublishedThermalDifferences(EvidenceStore):
    def evaluate(self):
        m = self.manifest
        keys(m, "schema campaign_id analysis_mode source records output_unit")
        same(m["schema"], SCHEMA, "manifest schema")
        same(m["analysis_mode"], "retrospective_published_report", "analysis mode")
        text(m["campaign_id"])
        if m["output_unit"] not in ("K", "uK"):
            raise ValueError("Output unit must be K or uK")
        source = self.read(m["source"])
        if digest(source) != SOURCE_SHA256 or not source.startswith(b"%PDF-"):
            raise ValueError("Only the reviewed SIM.T-S2 PDF edition is supported")
        doc = load(self.read(m["records"]))
        validate_metadata(doc)
        rows, controls = doc["records"], doc["pilot_control_rows"]
        if type(rows) is not list or len(rows) != 6 or type(controls) is not list or len(controls) != 2:
            raise ValueError("Require all six external rows and both pilot controls")
        if [r.get("candidate_lab") if type(r) is dict else None for r in rows] != list(LABS):
            raise ValueError("Require complete unique candidates in source table order")
        pilot_roles = ("CEM_first_calibration", "CEM_last_calibration")
        for row, role in zip(controls, pilot_roles):
            keys(row, CONTROL_FIELDS)
            same(row["role"], role, "pilot control role")
            same(row["source_pdf_page"], 33, "pilot control page")
            names = NUMERIC_SOURCE_FIELDS + ("reported_difference_C", "reported_En", "reported_expanded_uncertainty_C")
            for name, value in zip(names, TABLE[role]):
                same(row[name], value, "pilot source literal " + name)
        results = []
        for row in rows:
            validate_row(row)
            lab = row["candidate_lab"]
            result = reconstruct(row["reported_difference"], row["candidate_reported_expanded_uncertainty"],
                row["reference_reported_expanded_uncertainty"], row["reported_En"], m["output_unit"])
            issue = REVIEW_ISSUES.get(lab)
            rounding_issue = not result["source_rounding_check"]["compatible"]
            result.update(id=row["id"], candidate_lab=lab, reference_lab="CEM",
                source_locator=SOURCE_URL + "#page=33", source_table="23", source_unit="degC_difference",
                source_literals=dict(difference=row["reported_difference"],
                    candidate_expanded_component=row["candidate_reported_expanded_uncertainty"],
                    reference_expanded_component=row["reference_reported_expanded_uncertainty"],
                    absolute_normalized_deviation=row["reported_En"]),
                source_candidate_coverage_factor=COVERAGE[lab][0], source_reference_coverage_factor="2",
                reconstructed_difference_coverage_factor=COVERAGE[lab][0],
                reconstruction_is_reported_literal=False, source_issue=issue,
                source_En_rounding_issue="Printed En cannot be reconciled within displayed source rounding." if rounding_issue else None,
                eligible=issue is None and not rounding_issue,
                eligibility_scope="Unflagged descriptive reconstruction only; never qualification or acceptance.",
                status="source_review_required" if issue or rounding_issue else "descriptive_only",
                formal_qualification="not_established")
            results.append(result)
        return dict(schema="ciw.published-thermal-difference-results.v1", campaign_id=m["campaign_id"],
            analysis_mode="retrospective_published_report", source_url=SOURCE_URL,
            source_sha256=digest(source), records_sha256=m["records"]["sha256"], manifest_sha256=digest(self.raw_manifest),
            publication_date="2013-04", measurement_period="2004-2005", temperature_scale="ITS-90",
            source_coordinates=dict(pdf_page=33, printed_page=33, table="23", nominal_temperature_C="100", instrument="Isotech large PRT 16004/1, model 935/14/16"),
            reference_assignment="CEM first/last mean; external participant values excluded. Reference-internal correlation is treated by source equations 2 and 3 (page 14).",
            uncertainty_convention="Reproduce source equation 4 (page 15): U(d)=sqrt(UL^2+UR^2). This assumes zero candidate/reference covariance for this calculation; independent assignment alone does not establish that assumption.",
            unit_conversion="A difference of 1 degree C equals 1 K or 1000000 uK; no absolute-temperature offset is applied.",
            numerical_precision_digits=200, rounding_check="Closed half-last-digit intervals for d, UL, UR and printed En; an overlap is a rounding compatibility check, not validation of the source uncertainty budget.",
            criterion=dict(basis="descriptive_analysis", rule="conditional_source_expanded_interval_includes_zero",
                expression="abs(d) <= sqrt(UL^2 + UR^2)", source_numerical_acceptance_threshold_found=False,
                local_instrument_approved=False, confidence_probability_claimed=None),
            counts=dict(retained_candidate_rows=6, retained_pilot_controls=2,
                unflagged_descriptive_rows=sum(r["eligible"] for r in results),
                source_review_rows=sum(r["status"] == "source_review_required" for r in results),
                conditional_intervals_including_zero=sum(r["conditional_source_expanded_interval_includes_zero"] for r in results)),
            results=results, pilot_controls=[dict(source_literals=r, independent_candidate=False,
                numerical_interval_evaluated=False, status="pilot_control_only") for r in controls],
            formal_qualification="not_established", physical_validation="not_established",
            prospective_held_out_campaign="not_established", local_instrument_qualification="not_established",
            local_criteria_approval="not_established", release_status="unchanged")


def adapt_campaign(manifest_path, output_dir):
    campaign = PublishedThermalDifferences(manifest_path)
    report = campaign.evaluate()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "campaign.json").write_bytes(campaign.raw_manifest)
    raw = target / "raw"
    raw.mkdir()
    for identity, content in campaign.retained.items():
        (raw / (identity.split(":")[1] + ".bin")).write_bytes(content)
    (target / "source-records.json").write_bytes(campaign.retained[campaign.manifest["records"]["sha256"]])
    # A report exists only after all retained evidence has been written.
    (target / "report.json").write_bytes(dump(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = adapt_campaign(args.manifest, args.output_dir)
        print(dump(report).decode())
        return 0  # Completed descriptive replay, not acceptance of a laboratory.
    except (ValueError, OSError, KeyError, TypeError, InvalidOperation) as error:
        print(dump(dict(status="refused", error=str(error))).decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
