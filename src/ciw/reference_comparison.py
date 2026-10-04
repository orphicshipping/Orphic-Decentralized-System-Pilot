"""Bounded paired reference comparisons with explicitly declared joint uncertainty.

This computes conditional pointwise decisions, never physical qualification or
empirical coverage. Temporal samples are not treated as independent replicates.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import math
import json

from .control_checks import _series
from .control_contracts import _base, content_ref, detached, keys, number, text
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import content_identity, canonical_json
from .operations.runner import check_seal, seal

SCHEMA = "ciw.reference-comparison.v1"
POLICY_SCHEMA = "ciw.reference-comparison-policy.v1"
MAX_SAMPLES = 64
MAX_BYTES = 2 * 1024 * 1024
AUTHORITY = {"physical_qualification": "not_established", "calibration_traceability": "not_established",
             "origin_authentication": "not_established", "equipment_permission": "not_granted",
             "proof": "not_generated", "empirical_coverage": "not_established"}
METHOD = {
    "difference": "left - right; C_delta = C_left + C_right - C_cross - transpose(C_cross)",
    "decision": "pointwise expanded difference interval contained in +/-max_abs_error, with uncertainty ceiling",
    "arithmetic": "exact rational decisions on declared binary64 inputs; outward binary64 display intervals",
    "scope": "conditional comparison of supplied declarations; no simultaneous coverage or independent-replicate claim",
}


def _bounded(value):
    result = detached(value)
    if len((json.dumps(result, indent=2, allow_nan=False) + "\n").encode("utf-8")) > MAX_BYTES:
        raise ValueError("Reference comparison exceeds 2 MiB")
    return result


def _stream(value):
    _base(value, "observation-stream", {"observations"})
    rows = value["observations"]
    if type(rows) is not list or len(rows) > MAX_SAMPLES:
        raise ValueError("Reference comparison requires at most 64 samples")
    signature = _series(rows)
    if any(type(row["value"]) is list for row in rows):
        raise ValueError("Reference comparison supports scalar observations only")
    return rows, signature


def _policy(policy, left, right):
    keys(policy, {"schema", "policy_id", "source_kind", "left_ref", "right_ref", "unit",
                  "coverage_factor", "max_abs_error", "max_standard_uncertainty", "uncertainty"})
    if policy["schema"] != POLICY_SCHEMA or policy["source_kind"] not in {"synthetic", "retained_observation"}:
        raise ValueError("Require explicit comparison policy and source kind")
    text(policy["policy_id"])
    text(policy["unit"])
    if policy["left_ref"] != content_identity(left) or policy["right_ref"] != content_identity(right):
        raise ValueError("Comparison policy must bind the exact left and right streams")
    if (not 1 <= number(policy["coverage_factor"]) <= 6 or number(policy["max_abs_error"]) < 0
            or number(policy["max_standard_uncertainty"]) < 0):
        raise ValueError("Require factor in [1,6] and nonnegative error/uncertainty limits")
    if policy["source_kind"] == "retained_observation" and any(
            row["provenance"]["semantics"] == "simulated"
            for stream in (left, right) for row in stream["observations"]):
        raise ValueError("Simulated observations require synthetic source kind")
    uncertainty = policy["uncertainty"]
    keys(uncertainty, {"status", "scope", "relationship", "joint_covariance", "justification", "budget_ref"})
    if (uncertainty["status"] not in {"complete", "incomplete", "unknown"}
            or uncertainty["scope"] not in {"total_comparison", "conditional_only", "unknown"}
            or uncertainty["relationship"] not in {"declared_joint", "unknown"}):
        raise ValueError("Unsupported uncertainty declaration")
    text(uncertainty["justification"])
    if uncertainty["budget_ref"] is not None:
        content_ref(uncertainty["budget_ref"])
    if uncertainty["status"] == "complete" and uncertainty["budget_ref"] is None:
        raise ValueError("A complete budget assertion requires an evidence reference")
    covariance = uncertainty["joint_covariance"]
    if (covariance is None) != (uncertainty["relationship"] == "unknown"):
        raise ValueError("A declared joint relationship requires covariance; unknown cannot supply one")
    if covariance is not None:
        # Bound matrix size before the shared eigenvalue validator does any work.
        n = len(left["observations"])
        if not n or len(right["observations"]) != n:
            raise ValueError("Joint covariance requires nonempty equally sized streams")
        axes = [f"{side}:{i}" for side in ("left", "right") for i in range(n)]
        validate_covariance_artifact(covariance, expected_quantity_ids=axes,
                                    expected_units=[policy["unit"]] * (2*n),
                                    expected_frame=left["observations"][0]["frame"])
        rows = left["observations"] + right["observations"]
        if covariance["reference_values"] != [row["value"] for row in rows]:
            raise ValueError("Joint covariance reference values differ from the paired observations")
        if not {policy["left_ref"], policy["right_ref"]} <= set(covariance["provenance"]["source_evidence_ids"]):
            raise ValueError("Joint covariance must bind both source streams")
        for i, row in enumerate(rows):
            if row["uncertainty"] is not None and covariance["matrix"][i][i] != row["uncertainty"]["matrix"][0][0]:
                raise ValueError("Joint covariance diagonal contradicts the retained marginal uncertainty")
        # Shared covariance tolerances do not authorize a negative residual variance.
        for i in range(n):
            if _difference_covariance(covariance["matrix"], n, i, i) < 0:
                raise ValueError("Joint covariance gives a negative paired difference variance")


def _f(value):
    return Fraction(float(value))


def _difference_covariance(matrix, n, i, j):
    return _f(matrix[i][j]) + _f(matrix[n+i][n+j]) - _f(matrix[i][n+j]) - _f(matrix[n+i][j])


def _outward(value, upper):
    result = float(value)
    if (_f(result) < value if upper else _f(result) > value):
        result = math.nextafter(result, math.inf if upper else -math.inf)
    return result


def _sqrt_up(value):
    if value == 0:
        return 0.0
    # Start from an upper rounded variance, then enforce the exact squared bound.
    result = math.sqrt(_outward(value, True))
    while _f(result) ** 2 < value:
        result = math.nextafter(result, math.inf)
    return result


def _evaluate(left, right, policy):
    a, signature_a = _stream(left)
    b, signature_b = _stream(right)
    _policy(policy, left, right)
    result = {"status": "INDETERMINATE", "reason": "missing_evidence", "sample_count": len(a),
              "compared_sample_count": 0, "unit": policy["unit"], "samples": [],
              "difference_covariance": None, "metrics": None}
    if not a or not b:
        return result
    if signature_a != signature_b or signature_a[3] != policy["unit"]:
        return {**result, "reason": "model_entity_quantity_unit_frame_or_clock_mismatch"}
    if len(a) != len(b) or [x["clock"]["time_s"] for x in a] != [x["clock"]["time_s"] for x in b]:
        return {**result, "reason": "time_grid_mismatch"}
    uncertainty = policy["uncertainty"]
    missing = any(row["value"] is None for row in a+b)
    absent_marginal = any(row["uncertainty"] is None for row in a+b)
    reason = ("missing_sample" if missing else
              "unknown_marginal_uncertainty" if absent_marginal else
              "unknown_joint_uncertainty" if uncertainty["joint_covariance"] is None else
              "incomplete_uncertainty_budget" if uncertainty["status"] != "complete" else
              "conditional_uncertainty_only" if uncertainty["scope"] != "total_comparison" else None)
    matrix = None if uncertainty["joint_covariance"] is None else uncertainty["joint_covariance"]["matrix"]
    errors = []
    for i, (x, y) in enumerate(zip(a, b)):
        row = {"index": i, "time_s": x["clock"]["time_s"], "difference": None,
               "standard_uncertainty_upper": None, "expanded_interval": None,
               "status": "INDETERMINATE", "reason": reason}
        if x["value"] is not None and y["value"] is not None:
            delta = _f(x["value"]) - _f(y["value"])
            row["difference"] = float(delta)
            errors.append(float(delta))
            if reason is None:
                variance = _difference_covariance(matrix, len(a), i, i)
                k, limit = _f(policy["coverage_factor"]), _f(policy["max_abs_error"])
                u = _sqrt_up(variance)
                radius = k * _f(u)
                row["standard_uncertainty_upper"] = u
                row["expanded_interval"] = [_outward(delta-radius, False), _outward(delta+radius, True)]
                if variance > _f(policy["max_standard_uncertainty"]) ** 2:
                    row.update(status="INDETERMINATE", reason="uncertainty_limit_exceeded")
                elif abs(delta) <= limit and k*k*variance <= (limit-abs(delta))**2:
                    row.update(status="PASS", reason="expanded_interval_contained")
                elif abs(delta) > limit and k*k*variance < (abs(delta)-limit)**2:
                    row.update(status="FAIL", reason="expanded_interval_outside")
                else:
                    row.update(status="INDETERMINATE", reason="expanded_interval_overlaps_limit")
        result["samples"].append(row)
    result["compared_sample_count"] = len(errors)
    if errors:
        result["metrics"] = {"signed_mean_difference": math.fsum(errors)/len(errors),
                             "rmse": math.hypot(*errors)/math.sqrt(len(errors)),
                             "max_abs_error": max(map(abs, errors))}
    if reason is not None:
        result["reason"] = reason
        return result
    n = len(a)
    # Retain off-diagonals; no independence, standard-error or global-coverage claim.
    result["difference_covariance"] = [[float(_difference_covariance(matrix, n, i, j))
                                         for j in range(n)] for i in range(n)]
    statuses = [row["status"] for row in result["samples"]]
    result["status"] = "FAIL" if "FAIL" in statuses else "PASS" if all(s == "PASS" for s in statuses) else "INDETERMINATE"
    result["reason"] = {"PASS": "all_pointwise_intervals_contained", "FAIL": "pointwise_interval_outside",
                        "INDETERMINATE": "pointwise_comparison_indeterminate"}[result["status"]]
    return result


def compare(left: dict, right: dict, policy: dict) -> dict:
    """Compare scalar streams under a declared total joint uncertainty budget.

    Completeness and total-budget scope are operator assertions, not validated
    experimental facts. Conditional observer covariance alone is insufficient.
    """
    request = _bounded({"left": left, "right": right, "policy": policy})
    outcome = _evaluate(**request)
    return _bounded(seal({"schema": SCHEMA, "request": request, "request_ref": content_identity(request),
                          "outcome": outcome, "method": deepcopy(METHOD), "authority": deepcopy(AUTHORITY)}))


def validate_report(report: dict) -> None:
    """Recompute decisions, rejecting resealed falsified outcomes as well as edits."""
    report = _bounded(report)
    keys(report, {"schema", "request", "request_ref", "outcome", "method", "authority", "record_digest"})
    check_seal(report)
    keys(report["request"], {"left", "right", "policy"})
    if canonical_json(report) != canonical_json(compare(**report["request"])):
        raise ValueError("Reference comparison report differs from recomputed evidence and policy")


def example_request(case="pass") -> dict:
    """Synthetic arithmetic fixtures; no calibration or experimental claim."""
    from .control_contracts import observation, record
    if case not in {"pass", "fail", "unknown"}:
        raise ValueError("Choose pass, fail or unknown synthetic example")
    fixture = content_identity({"synthetic_reference_comparison": 1})
    streams = []
    for side, values in (("left", [300.0, 301.0]), ("right", [300.1, 301.1] if case != "fail" else [303.0, 304.0])):
        rows = []
        for i, value in enumerate(values):
            cov = create_covariance_artifact(matrix=[[0.01]], quantity_ids=["temperature"], units=["K"],
                frame="synthetic.location", reference_values=[value], method="synthetic_declared_variance",
                basis={"kind": "observation", "id": f"synthetic.{side}.{i}"},
                provenance={"provider": "synthetic.fixture", "source_evidence_ids": [fixture], "source_covariance_ids": []},
                assumptions=["Synthetic arithmetic example; no experimental evidence"])
            rows.append(observation(identity={"model_id": "synthetic.measurand", "entity_id": "synthetic.specimen",
                "execution_id": "synthetic."+side}, clock={"id": "synthetic.clock", "time_s": float(i)},
                frame="synthetic.location", quantity="temperature", value=value, unit="K", uncertainty=cov,
                provenance={"provider": "synthetic.fixture", "sources": [fixture], "semantics": "simulated"}))
        streams.append(record("observation-stream", observations=rows))
    refs = [content_identity(stream) for stream in streams]
    joint = create_covariance_artifact(matrix=[[0.01 if i == j else 0.0 for j in range(4)] for i in range(4)],
        quantity_ids=[f"{side}:{i}" for side in ("left", "right") for i in range(2)], units=["K"]*4,
        frame="synthetic.location", reference_values=[r["value"] for s in streams for r in s["observations"]],
        method="synthetic_declared_joint_covariance", basis={"kind": "observation", "id": "synthetic.paired-series"},
        provenance={"provider": "synthetic.fixture", "source_evidence_ids": refs, "source_covariance_ids": []},
        assumptions=["Independent components declared only for this synthetic fixture"])
    return {"left": streams[0], "right": streams[1], "policy": {
        "schema": POLICY_SCHEMA, "policy_id": "synthetic.comparison", "source_kind": "synthetic",
        "left_ref": refs[0], "right_ref": refs[1], "unit": "K", "coverage_factor": 2.0,
        "max_abs_error": 1.0, "max_standard_uncertainty": 0.5,
        "uncertainty": {"status": "unknown" if case == "unknown" else "complete", "scope": "total_comparison",
                        "relationship": "unknown" if case == "unknown" else "declared_joint",
                        "joint_covariance": None if case == "unknown" else joint,
                        "justification": "Synthetic software fixture; no physical qualification", "budget_ref": fixture}}}


def _load(path):
    from .session import loads_json
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Reference comparison file exceeds 2 MiB or is empty")
    try:
        return _bounded(loads_json(raw.decode("utf-8")))
    except RecursionError as exc:
        raise ValueError("Reference comparison JSON nesting exceeds the parser limit") from exc


def summary(report: dict) -> str:
    """Format a freshly recomputed report without changing its retained contract."""
    from collections import Counter
    validate_report(report)
    outcome, policy = report["outcome"], report["request"]["policy"]
    uncertainty = policy["uncertainty"]
    counts = Counter(row["status"] for row in outcome["samples"])
    reasons = Counter(row["reason"] for row in outcome["samples"])
    unit = policy["unit"]
    display = lambda value: value.replace("_", " ")
    lines = [
        f'{outcome["status"]}: conditional paired reference comparison',
        f'Reason: {display(outcome["reason"])}',
        f'Source: {display(policy["source_kind"])}; values compared: '
        f'{outcome["compared_sample_count"]}/{outcome["sample_count"]} left samples; '
        f'right samples: {len(report["request"]["right"]["observations"])}',
        'Sample decisions: ' + ', '.join(f'{counts[status]} {status}' for status in ("PASS", "FAIL", "INDETERMINATE")),
        f'Limits: absolute difference <= {policy["max_abs_error"]:g} {unit}; '
        f'paired-difference standard uncertainty <= {policy["max_standard_uncertainty"]:g} {unit}; '
        f'coverage factor k={policy["coverage_factor"]:g}',
        f'Uncertainty (operator-declared): {display(uncertainty["status"])}; '
        f'{display(uncertainty["scope"])}; {display(uncertainty["relationship"])}',
    ]
    if reasons:
        lines.append('Sample reasons: ' + '; '.join(f'{display(reason)}: {count}' for reason, count in sorted(reasons.items())))
    if outcome["metrics"] is not None:
        metrics = outcome["metrics"]
        lines.append(f'Descriptive differences: max absolute {metrics["max_abs_error"]:.6g} {unit}; '
                     f'RMS {metrics["rmse"]:.6g} {unit}; signed mean {metrics["signed_mean_difference"]:.6g} {unit}')
    lines.append('Physical qualification: ' + display(report["authority"]["physical_qualification"]) +
                 '; empirical coverage: ' + display(report["authority"]["empirical_coverage"]))
    return "\n".join(lines)


def main(argv=None):
    import argparse
    from pathlib import Path
    import sys
    from .control_contracts import save_new
    exits = ("Exit codes: 0 PASS, 2 FAIL, 3 INDETERMINATE, 1 input/output refusal. "
             "Argument syntax errors use argparse exit code 2. "
             "PASS is a conditional pointwise comparison, never physical qualification.")
    parser = argparse.ArgumentParser(description=__doc__, epilog=exits)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Compare retained streams under their explicit uncertainty policy", epilog=exits)
    run.add_argument("request", type=Path, help="JSON object containing left/right observation streams and policy (up to 2 MiB)")
    run.add_argument("--output", type=Path, required=True, help="Create the full JSON report; existing files are never overwritten")
    verify = commands.add_parser("verify", help="Recompute a saved report and reject altered decisions", epilog=exits)
    verify.add_argument("report", type=Path, help="Previously saved full comparison report to recompute (up to 2 MiB)")
    demo = commands.add_parser("demo", help="Write an explicitly synthetic worked example and its comparison", epilog=exits)
    demo.add_argument("--case", choices=("pass", "fail", "unknown"), default="pass",
                      help="Synthetic comparison outcome to demonstrate (default: pass)")
    demo.add_argument("--output-dir", type=Path, required=True,
                      help="Create a new directory containing request.json and report.json")
    for command in (run, verify, demo):
        command.add_argument("--summary", action="store_true",
                             help="Print a concise human summary instead of JSON; saved reports remain complete JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            report = _load(args.report)
            validate_report(report)
        else:
            request = example_request(args.case) if args.command == "demo" else _load(args.request)
            keys(request, {"left", "right", "policy"})
            report = compare(**request)
            if args.command == "demo":
                args.output_dir.mkdir(parents=True, exist_ok=False)
                save_new(args.output_dir / "request.json", request)
                save_new(args.output_dir / "report.json", report)
            else:
                save_new(args.output, report)
        print(summary(report) if args.summary else json.dumps(report, indent=2, allow_nan=False))
        if args.summary:
            path = args.report if args.command == "verify" else args.output_dir / "report.json" if args.command == "demo" else args.output
            print(f'Report: {path.resolve()}')
        return {"PASS": 0, "FAIL": 2, "INDETERMINATE": 3}[report["outcome"]["status"]]
    except (ValueError, TypeError, OSError, OverflowError) as exc:
        print("reference comparison: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
