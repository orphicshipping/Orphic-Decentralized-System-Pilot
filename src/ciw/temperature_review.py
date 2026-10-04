"""Human presentation of already validated retained temperature occurrences.

This formatter neither reads workspaces nor calls processing or verification.
Use NET workspace inspection to validate records and dependency bindings first.
"""
from __future__ import annotations

import json

from .temperature_workflow import PROCESS, VERIFY


def _quoted(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def summary_lines(results: dict) -> list[str]:
    """Keep each saved audit attached to its exact processing occurrence."""
    processing = [row for row in results.values() if row["operation_id"] == PROCESS]
    if not processing:
        return []
    audits = [row for row in results.values() if row["operation_id"] == VERIFY]
    lines = ["Temperature measurement review (retained records)"]
    for result in processing:
        report = result["data"]
        lines.extend((
            "Processing result: " + result["result_id"],
            "  Execution: " + result["execution_id"],
            "  Source kind: " + report["source_kind"],
            "  Processing report: " + report["record_digest"],
            "  Numerical procedure: " + report["numerical_contract"]["id"],
            "  Generic verification status: " + result["verification_status"],
            f"  Raw observations: {len(report['raw_observations']['samples'])}; unit "
            + _quoted(report["raw_observations"]["unit"]),
        ))
        for name, label in (("calibrated_sensor_temperature", "Calibrated sensor temperature"),
                            ("polymer_temperature", "Polymer temperature estimate")):
            output = report[name]
            lines.append(f"  {label}: {output['status']}")
            if output["status"] == "unavailable":
                lines.append("    Reason: " + _quoted(output["reason"]))
            else:
                invalid = [row for row in output["samples"] if row["status"] == "invalid"]
                lines.append(f"    Retained samples: {len(output['samples'])}; invalid: {len(invalid)}; unit K")
                for row in invalid:
                    lines.append("    Invalid sample " + _quoted(row["sample_id"]) + ": " + _quoted(row["flags"]))
                if len(invalid) == len(output["samples"]):
                    lines.append("    No valid temperature samples; saved audit PASS does not verify unavailable temperature values.")
        lines.extend((
            "  Declared applicability: " + report["applicability"]["calibration_status"],
            "  Installed correction: " + report["applicability"]["installed_correction_status"],
            "  Uncertainty budget: " + report["uncertainty"]["status"],
            "  Missing/omitted contributions: " + _quoted(report["uncertainty"]["missing_or_omitted_categories"]),
            "  Operational acceptance: " + report["acceptance"]["status"]
            + "; quantity " + _quoted(report["acceptance"]["quantity"]),
            "  Acceptance reasons: " + _quoted(report["acceptance"]["reasons"]),
        ))
        decisions = {}
        for sample in report["acceptance"]["sample_results"]:
            status = sample["status"]
            decisions[status] = decisions.get(status, 0) + 1
        lines.append("  Sample decisions: " + _quoted(decisions))
        matches = [row for row in audits if row["data"]["processing_result_id"] == result["result_id"]]
        if not matches:
            lines.append("  Saved numerical audit: not present")
        for row in matches:
            payload = row["data"]
            if (payload["processing_execution_id"] != result["execution_id"]
                    or payload["processing_record_digest"] != result["record_digest"]):
                raise ValueError("Saved numerical audit is not bound to this processing occurrence")
            audit = payload["report"]
            lines.append("  Saved numerical audit (historical, unauthenticated): " + audit["status"]
                         + "; occurrence " + payload["verification_id"])
            failed = [check["name"] for check in audit["checks"] if check["status"] == "FAIL"]
            lines.append(f"    Fixed checks: {len(audit['checks'])}; failed: " + _quoted(failed))
            compared = sum(check["compared_values"] for check in audit["checks"])
            mismatches = sum(check["mismatches"] for check in audit["checks"])
            lines.append(f"    Compared values: {compared}; mismatches: {mismatches}")
            for check in audit["checks"]:
                lines.append("    " + check["name"] + f": {check['status']}; compared values: {check['compared_values']}; mismatches: {check['mismatches']}")
        lines.append("  Authority: " + "; ".join(key + "=" + value for key, value in report["authority"].items()))
    lines.extend((
        "Fresh numerical verification: not performed by inspection.",
        "Acceptance covers submitted sample instants only; saved audit PASS does not establish physical qualification.",
        "Use --json for individual sample values, covariance and complete retained records.",
    ))
    return lines
