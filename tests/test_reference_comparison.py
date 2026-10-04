"""Synthetic arithmetic/reference-comparison checks; no physical experiments."""
from copy import deepcopy
from fractions import Fraction
import json
import math

import pytest

from ciw import reference_comparison as comparison
from ciw.control_contracts import record, save_new
from ciw.core.covariance import covariance_identity
from ciw.core.identities import content_identity
from ciw.operations.runner import seal


def _rebind(request):
    for side in ("left", "right"):
        for row in request[side]["observations"]:
            if row["uncertainty"] is not None:
                row["uncertainty"]["reference_values"] = [row["value"]]
                row["uncertainty"]["covariance_id"] = covariance_identity(row["uncertainty"])
            seal(row)
        seal(request[side])
        request["policy"][side+"_ref"] = content_identity(request[side])
    cov = request["policy"]["uncertainty"]["joint_covariance"]
    if cov is not None:
        cov["reference_values"] = [row["value"] for side in ("left", "right") for row in request[side]["observations"]]
        cov["provenance"]["source_evidence_ids"] = [request["policy"][side+"_ref"] for side in ("left", "right")]
        cov["covariance_id"] = covariance_identity(cov)
    return request


def _constant_case(delta=0.0, variance=1.0, cross=0.0):
    request = comparison.example_request()
    for side in ("left", "right"):
        for i, row in enumerate(request[side]["observations"]):
            row["value"] = delta if side == "left" else 0.0
            row["uncertainty"]["matrix"] = [[variance]]
    matrix = [[variance if i == j else 0.0 for j in range(4)] for i in range(4)]
    matrix[0][2] = matrix[2][0] = matrix[1][3] = matrix[3][1] = cross
    request["policy"]["uncertainty"]["joint_covariance"]["matrix"] = matrix
    request["policy"].update(max_standard_uncertainty=10.0, max_abs_error=10.0)
    return _rebind(request)


@pytest.mark.parametrize("case,status", [("pass", "PASS"), ("fail", "FAIL"), ("unknown", "INDETERMINATE")])
def test_synthetic_examples_recompute_without_physical_promotion(case, status):
    request = comparison.example_request(case)
    original = deepcopy(request)
    report = comparison.compare(**request)
    comparison.validate_report(report)
    assert report["outcome"]["status"] == status
    assert report["authority"]["physical_qualification"] == "not_established"
    assert report["authority"]["origin_authentication"] == "not_established"
    assert report["request"]["policy"]["source_kind"] == "synthetic"
    assert request == original


@pytest.mark.parametrize("cross,expected", [(0.0, 2.0), (0.5, 1.0), (-0.5, 3.0), (1.0, 0.0)])
def test_explicit_covariance_correlation_sign_and_zero_variance(cross, expected):
    report = comparison.compare(**_constant_case(cross=cross))
    assert report["outcome"]["difference_covariance"] == [[expected, 0.0], [0.0, expected]]
    upper = report["outcome"]["samples"][0]["standard_uncertainty_upper"]
    assert Fraction(upper)**2 >= Fraction(expected)


def test_temporal_covariance_is_retained_without_replicate_averaging():
    request = _constant_case()
    matrix = request["policy"]["uncertainty"]["joint_covariance"]["matrix"]
    matrix[0][1] = matrix[1][0] = 0.5
    matrix[2][3] = matrix[3][2] = 0.25
    _rebind(request)
    result = comparison.compare(**request)["outcome"]
    assert result["difference_covariance"] == [[2.0, 0.75], [0.75, 2.0]]
    assert "standard_error" not in result["metrics"]


@pytest.mark.parametrize("delta,variance,limit,status", [
    (0.5, 0.0, 0.5, "PASS"), (0.5001, 0.0, 0.5, "FAIL"),
    (0.5, 0.125, 1.5, "PASS"), (0.5, 0.125, 1.0, "INDETERMINATE"),
    (2.0, 0.125, 1.0, "INDETERMINATE"), (2.01, 0.125, 1.0, "FAIL"),
    (-2.01, 0.125, 1.0, "FAIL"),
])
def test_exact_interval_decisions(delta, variance, limit, status):
    request = _constant_case(delta=delta, variance=variance)
    request["policy"]["max_abs_error"] = limit
    assert comparison.compare(**request)["outcome"]["status"] == status


def test_positive_subresolution_uncertainty_cannot_disappear_at_boundary():
    request = _constant_case(delta=1.0, variance=5e-324)
    request["policy"]["max_abs_error"] = 1.0
    result = comparison.compare(**request)["outcome"]
    assert result["status"] == "INDETERMINATE"
    assert result["samples"][0]["expanded_interval"][1] > 1.0


def test_naive_pointwise_pass_prevented_by_uncertainty_and_ceiling():
    request = _constant_case(delta=0.1, variance=1.0)
    request["policy"].update(max_abs_error=0.5)
    assert comparison.compare(**request)["outcome"]["status"] == "INDETERMINATE"
    request["policy"].update(max_abs_error=10.0, max_standard_uncertainty=0.1)
    result = comparison.compare(**request)["outcome"]
    assert result["status"] == "INDETERMINATE"
    assert result["samples"][0]["reason"] == "uncertainty_limit_exceeded"


@pytest.mark.parametrize("field,value,reason", [
    ("status", "incomplete", "incomplete_uncertainty_budget"),
    ("status", "unknown", "incomplete_uncertainty_budget"),
    ("scope", "conditional_only", "conditional_uncertainty_only"),
    ("scope", "unknown", "conditional_uncertainty_only"),
])
def test_only_explicit_complete_total_budget_can_pass(field, value, reason):
    request = comparison.example_request()
    request["policy"]["uncertainty"][field] = value
    result = comparison.compare(**request)["outcome"]
    assert result["status"] == "INDETERMINATE"
    assert result["reason"] == reason


def test_missing_marginal_not_invented_from_joint_matrix():
    request = comparison.example_request()
    request["left"]["observations"][0]["uncertainty"] = None
    _rebind(request)
    result = comparison.compare(**request)["outcome"]
    assert result["status"] == "INDETERMINATE"
    assert result["reason"] == "unknown_marginal_uncertainty"


def test_missing_value_stays_missing_and_reference_metrics_remain_descriptive():
    request = comparison.example_request("unknown")
    request["right"]["observations"][0].update(value=None, uncertainty=None)
    _rebind(request)
    result = comparison.compare(**request)["outcome"]
    assert result["reason"] == "missing_sample"
    assert result["samples"][0]["difference"] is None
    assert result["compared_sample_count"] == 1
    assert result["status"] == "INDETERMINATE"


@pytest.mark.parametrize("kind", ["unit", "frame", "entity", "model", "clock", "time"])
def test_semantics_and_alignment_mismatch_cannot_pass(kind):
    request = comparison.example_request("unknown")
    for row in request["right"]["observations"]:
        if kind == "unit":
            row["unit"] = "degC"
            row["uncertainty"]["units"] = ["degC"]
        elif kind == "frame":
            row["frame"] = "other.location"
            row["uncertainty"]["frame"] = "other.location"
        elif kind in {"entity", "model"}:
            row["identity"][kind+"_id"] = "other"
        elif kind == "clock":
            row["clock"]["id"] = "other"
        else:
            row["clock"]["time_s"] += 0.1
    _rebind(request)
    result = comparison.compare(**request)["outcome"]
    assert result["status"] == "INDETERMINATE"
    assert "mismatch" in result["reason"]


@pytest.mark.parametrize("kind", ["indefinite", "shape", "marginal", "reference", "source", "axis", "negative_residual"])
def test_invalid_or_misbound_joint_covariance_refuses(kind):
    request = _constant_case()
    cov = request["policy"]["uncertainty"]["joint_covariance"]
    if kind == "indefinite":
        cov["matrix"][0][2] = cov["matrix"][2][0] = 2.0
    elif kind == "negative_residual":
        cov["matrix"][0][2] = cov["matrix"][2][0] = 1.0 + 1e-12
    elif kind == "shape":
        cov["matrix"].pop()
    elif kind == "marginal":
        cov["matrix"][0][0] = 2.0
    elif kind == "reference":
        cov["reference_values"][0] += 1.0
    elif kind == "source":
        cov["provenance"]["source_evidence_ids"] = []
    else:
        cov["quantity_ids"][0] = "wrong"
    cov["covariance_id"] = covariance_identity(cov)
    with pytest.raises(ValueError):
        comparison.compare(**request)


@pytest.mark.parametrize("kind", ["status", "metric", "authority", "method"])
def test_resealed_output_tampering_is_rejected(kind):
    report = comparison.compare(**comparison.example_request())
    if kind == "status":
        report["outcome"]["status"] = "FAIL"
    elif kind == "metric":
        report["outcome"]["metrics"]["rmse"] = 0.0
    elif kind == "authority":
        report["authority"]["physical_qualification"] = "established"
    else:
        report["method"]["scope"] = "physical validation"
    seal(report)
    with pytest.raises(ValueError, match="recomputed"):
        comparison.validate_report(report)


def test_empty_series_is_indeterminate_and_wrong_policy_binding_refuses():
    request = comparison.example_request("unknown")
    request["left"] = record("observation-stream", observations=[])
    _rebind(request)
    assert comparison.compare(**request)["outcome"]["status"] == "INDETERMINATE"
    request["policy"]["right_ref"] = request["policy"]["left_ref"]
    with pytest.raises(ValueError, match="exact left and right"):
        comparison.compare(**request)


@pytest.mark.parametrize("case,code", [("pass", 0), ("fail", 2), ("unknown", 3)])
def test_cli_demo_run_verify_and_create_only(case, code, tmp_path, capsys):
    root = tmp_path / case
    assert comparison.main(["demo", "--case", case, "--output-dir", str(root)]) == code
    assert comparison.main(["verify", str(root / "report.json")]) == code
    output = tmp_path / "fresh.json"
    assert comparison.main(["run", str(root / "request.json"), "--output", str(output)]) == code
    before = output.read_bytes()
    assert comparison.main(["run", str(root / "request.json"), "--output", str(output)]) == 1
    assert output.read_bytes() == before
    assert comparison.main(["demo", "--case", case, "--output-dir", str(root)]) == 1
    capsys.readouterr()


def test_resealed_boolean_substitution_cannot_impersonate_zero():
    report = comparison.compare(**_constant_case(delta=0.0, variance=0.0))
    report["outcome"]["samples"][0]["difference"] = False
    seal(report)
    with pytest.raises(ValueError, match="recomputed"):
        comparison.validate_report(report)


def test_simulated_observations_cannot_be_relabelled_as_retained_measurements():
    request = comparison.example_request()
    request["policy"]["source_kind"] = "retained_observation"
    with pytest.raises(ValueError, match="synthetic source kind"):
        comparison.compare(**request)


def test_sample_bound_checked_before_covariance_calculation():
    request = comparison.example_request("unknown")
    original = request["left"]["observations"][0]
    request["left"]["observations"] = []
    for i in range(65):
        row = deepcopy(original)
        row["clock"]["time_s"] = float(i)
        request["left"]["observations"].append(row)
    _rebind(request)
    with pytest.raises(ValueError, match="at most 64"):
        comparison.compare(**request)


def test_numeric_range_exceeded_refuses_without_nonfinite_result():
    request = _constant_case(delta=1e150, variance=0.0)
    for row in request["right"]["observations"]:
        row["value"] = -1e150
    _rebind(request)
    with pytest.raises(ValueError, match="bounded number"):
        comparison.compare(**request)


@pytest.mark.parametrize("kind", ["oversized", "duplicate", "invalid_utf8"])
def test_cli_strict_bounded_load_never_publishes_invalid_request(kind, tmp_path, capsys):
    path, output = tmp_path / "input.json", tmp_path / "output.json"
    if kind == "oversized":
        path.write_bytes(b" " * (comparison.MAX_BYTES + 1))
    elif kind == "duplicate":
        path.write_text('{"left": {}, "left": {}}')
    else:
        path.write_bytes(b"\xff")
    assert comparison.main(["run", str(path), "--output", str(output)]) == 1
    assert not output.exists()
    capsys.readouterr()

def test_published_report_roundtrips_at_exact_byte_limit_and_refuses_one_byte_over(
        monkeypatch, tmp_path, capsys):
    request = comparison.example_request()
    report = comparison.compare(**request)
    serialized = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")
    request_path, output = tmp_path / "request.json", tmp_path / "report.json"
    save_new(request_path, request)
    assert request_path.stat().st_size < len(serialized)
    monkeypatch.setattr(comparison, "MAX_BYTES", len(serialized))
    assert comparison.main(["run", str(request_path), "--output", str(output)]) == 0
    assert output.read_bytes() == serialized
    assert comparison.main(["verify", str(output)]) == 0
    monkeypatch.setattr(comparison, "MAX_BYTES", len(serialized) - 1)
    refused = tmp_path / "refused.json"
    assert comparison.main(["run", str(request_path), "--output", str(refused)]) == 1
    assert not refused.exists()
    assert comparison.main(["verify", str(output)]) == 1
    capsys.readouterr()