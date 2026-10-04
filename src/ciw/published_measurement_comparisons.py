"""Reproduce published metrology comparisons without inventing a new campaign.

Historical report results are actual published measurements, not present-day
instrument measurements. This route never supplies missing prospective freezes,
raw calibration certificates, or approval of a local instrument's criteria.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import re
import sys

from .measurement_evidence import EvidenceStore, keys, text, dump, digest, load

SCHEMA = "ciw.published-measurement-comparison.v1"
ROW_FIELDS = ("id material candidate_lab reference_lab quantity unit kind candidate_value reference_value "
              "candidate_standard_uncertainty reference_standard_uncertainty reported_difference "
              "reported_expanded_uncertainty reported_expanded_coverage_factor reported_En source_locator source_issue eligible")
UNITS = {"liquid_density": "kg/m3", "mass_fraction": "mg/g", "temperature_difference": "uK"}


def decimal(value, positive=False):
    if type(value) is not str or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value) or len(value) > 80:
        raise ValueError("Published numbers must be bounded, exact decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid decimal") from error
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Uncertainty must be positive and finite")
    return result


def display(value):
    return format(value, ".12g")


def evaluate_row(row, criterion, independent):
    keys(row, ROW_FIELDS)
    for field in ("id", "material", "candidate_lab", "reference_lab", "source_locator"):
        text(row[field])
    if row["quantity"] not in UNITS or row["unit"] != UNITS[row["quantity"]]:
        raise ValueError("Unsupported or mismatched published measurand unit")
    if type(row["eligible"]) is not bool:
        raise ValueError("Eligibility must be explicit")
    if row["source_issue"] is not None:
        text(row["source_issue"])
        if row["eligible"]:
            raise ValueError("Unresolved source discrepancies cannot enter the eligible set")
    with localcontext() as context:
        context.prec = 200
        reported = {}
        if row["reported_expanded_uncertainty"] is not None:
            if decimal(row["reported_expanded_coverage_factor"], positive=True) != 2:
                raise ValueError("This route requires published expanded uncertainty with coverage factor k=2")
        elif row["reported_expanded_coverage_factor"] is not None:
            raise ValueError("Coverage factor supplied without a reported expanded uncertainty")
        for field in ("reported_difference", "reported_expanded_uncertainty", "reported_En"):
            if row[field] is not None:
                reported[field] = decimal(row[field], positive=field == "reported_expanded_uncertainty")
                if field == "reported_En" and reported[field] < 0:
                    raise ValueError("Reported En must be an absolute normalized deviation")
        if row["kind"] == "independent_pair":
            if not independent or row["candidate_lab"] == row["reference_lab"]:
                raise ValueError("Pair recombination requires an independently assigned reference")
            x, reference = (decimal(row[field]) for field in ("candidate_value", "reference_value"))
            if row["quantity"] == "liquid_density" and (x <= 0 or reference <= 0):
                raise ValueError("Liquid density must be positive")
            if row["quantity"] == "mass_fraction" and not (0 <= x <= 1000 and 0 <= reference <= 1000):
                raise ValueError("Mass fraction in mg/g must be between 0 and 1000")
            ux, ur = (decimal(row[field], positive=True) for field in ("candidate_standard_uncertainty", "reference_standard_uncertainty"))
            difference = x - reference
            expanded = Decimal(2) * (ux * ux + ur * ur).sqrt()
            calculation = "difference = candidate-reference; U_difference = 2*sqrt(u_candidate^2+u_reference^2)"
        elif row["kind"] == "reported_difference":
            if any(row[field] is not None for field in ("candidate_value", "reference_value", "candidate_standard_uncertainty", "reference_standard_uncertainty")):
                raise ValueError("Reported-difference route must not imply recombined independent measurements")
            if not all(field in reported for field in ("reported_difference", "reported_expanded_uncertainty")):
                raise ValueError("Reported differences require the published expanded uncertainty")
            difference, expanded = reported["reported_difference"], reported["reported_expanded_uncertainty"]
            calculation = "Published difference and expanded uncertainty retained; no independence or covariance inferred"
        else:
            raise ValueError("Unsupported record kind")
        ratio = abs(difference) / expanded
        rule = criterion["rule"]
        if rule == "En_lt_1":
            satisfies = ratio < 1
        elif rule == "interval_includes_zero_k2":
            satisfies = ratio <= 1
        else:
            raise ValueError("Unsupported retrospective decision rule")
        eligible = row["eligible"] and independent and row["source_issue"] is None
        status = ("satisfies_published_rule" if satisfies else "does_not_satisfy_published_rule") if eligible and criterion["basis"] == "published_report" else "descriptive_only"
        if row["source_issue"]:
            status = "source_discrepancy_requires_review"
        residuals = {}
        for field, calculated in (("reported_difference", difference), ("reported_expanded_uncertainty", expanded), ("reported_En", ratio)):
            if field in reported:
                # Reports sometimes tabulate an absolute difference; preserve
                # both the published literal and signed recomputation.
                residuals[field] = display(calculated - reported[field])
        return {"id": row["id"], "material": row["material"], "candidate_lab": row["candidate_lab"],
                "reference_lab": row["reference_lab"], "unit": row["unit"],
                "difference": display(difference), "expanded_uncertainty_k2": display(expanded),
                "absolute_normalized_deviation": display(ratio), "rule_satisfied_numerically": satisfies,
                "eligible_independent_comparison": eligible, "status": status,
                "source_issue": row["source_issue"], "source_locator": row["source_locator"],
                "calculation": calculation,
                "uncertainty_assumption": "Zero candidate-reference covariance is ASSUMED for recombination; independent value assignment alone does not establish it." if row["kind"] == "independent_pair" else "Use the source-reported difference uncertainty and coverage factor; no covariance recombination.",
                "reported_expanded_coverage_factor": row["reported_expanded_coverage_factor"],
                "published_literals": {f: row[f] for f in reported},
                "computed_minus_reported": residuals}


class PublishedComparison(EvidenceStore):
    def evaluate(self):
        m = self.manifest
        keys(m, "schema campaign_id analysis_mode source source_url source_title publication_date records method_summary calibration_summary reference_summary reference_independence criterion selection_summary")
        if m["schema"] != SCHEMA or m["analysis_mode"] != "retrospective_published_report":
            raise ValueError("Published comparison requires the explicit retrospective schema")
        for field in ("campaign_id", "source_url", "source_title", "publication_date", "method_summary", "calibration_summary", "reference_summary", "selection_summary"):
            text(m[field])
        if not m["source_url"].startswith("https://"):
            raise ValueError("Require an HTTPS primary-source locator")
        if m["reference_independence"] not in ("reported_independent_assignment", "participant_consensus", "same_data_certification"):
            raise ValueError("Reference construction must be explicit")
        criterion = m["criterion"]
        keys(criterion, "rule basis source_locator local_instrument_approved")
        if criterion["basis"] not in ("published_report", "descriptive_analysis") or criterion["local_instrument_approved"] is not False:
            raise ValueError("Historical reproduction cannot assert approval of local instrument criteria")
        text(criterion["source_locator"])
        self.read(m["source"])
        rows = load(self.read(m["records"]))
        if type(rows) is not list or not 0 < len(rows) <= 10000:
            raise ValueError("Require a bounded nonempty published record list")
        ids = [r.get("id") if type(r) is dict else None for r in rows]
        if any(type(i) is not str for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("Published record identities must be unique strings")
        independent = m["reference_independence"] == "reported_independent_assignment"
        results = [evaluate_row(row, criterion, independent) for row in rows]
        return {"schema": "ciw.published-measurement-results.v1", "campaign_id": m["campaign_id"],
                "analysis_mode": m["analysis_mode"], "data_origin": "actual_published_measurements",
                "source_url": m["source_url"], "source_sha256": m["source"]["sha256"],
                "records_sha256": m["records"]["sha256"], "manifest_sha256": digest(self.raw_manifest),
                "reference_independence": m["reference_independence"], "criterion": criterion,
                "method_summary": m["method_summary"], "calibration_summary": m["calibration_summary"],
                "reference_summary": m["reference_summary"], "selection_summary": m["selection_summary"],
                "counts": {"retained_rows": len(results), "eligible_independent_rows": sum(r["eligible_independent_comparison"] for r in results),
                    "satisfy_published_rule": sum(r["status"] == "satisfies_published_rule" for r in results),
                    "do_not_satisfy_published_rule": sum(r["status"] == "does_not_satisfy_published_rule" for r in results),
                    "source_discrepancies": sum(r["source_issue"] is not None for r in results)},
                "results": results, "prospective_held_out_campaign": "not_established",
                "local_instrument_qualification": "not_established", "release_status": "unchanged",
                "scope_limit": "Historical published method comparison only. No new measurement, hardware binding, prospective freeze, certificate validity or local acceptance approval is asserted."}


def adapt_campaign(manifest_path, output_dir):
    campaign = PublishedComparison(manifest_path)
    report = campaign.evaluate()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "campaign.json").write_bytes(campaign.raw_manifest)
    (target / "records.json").write_bytes(campaign.read(campaign.manifest["records"]))
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
        # Successful reproduction does not mean every laboratory satisfied a
        # criterion, nor that any local instrument is qualified.
        return 0
    except (ValueError, OSError, KeyError, TypeError, InvalidOperation) as error:
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
