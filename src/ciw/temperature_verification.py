"""Independent bounded numerical comparison of a retained temperature report.

Exact rational arithmetic interprets the declared inputs as binary64 numbers.
This checks the first-order calculation, not physical model adequacy or coverage.
Saved audit validation never invokes this numerical checker afresh.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import math

from .control_contracts import content_ref, detached, keys, number
from .operations.runner import check_seal, digest, seal
from .temperature_contract import AUTHORITY, BUDGET_CATEGORIES, MAX_SAMPLES, validate_request
from .temperature_processing import validate_report

SCHEMA = "ciw.temperature-numerical-verification.v1"
CHECK_NAMES = (
    "retained_contract_and_source_binding", "independent_affine_transform",
    "independent_joint_covariance", "independent_installed_correction",
    "independent_sample_decisions",
)
CHECKER_CONTRACT = {
    "id": "net.temperature.rational-first-order-audit.v1",
    "reference": "Exact rational affine values and J C J^T from binary64 inputs; independent added covariance",
    "comparison": "Fixed comparison policy, not a universal forward-error guarantee: absolute error <= 16 * 2^-52 * sum(abs(reference terms)) + 64 * 2^-1074",
    "acceptance": "Independently reconstruct outward binary64 endpoints from reported value, uncertainty and k; same sample-only rule",
    "scope": "Finite first-order numerical comparison; not higher-order adequacy, physical validity, empirical coverage or a cryptographic proof",
    "maximum_samples": MAX_SAMPLES,
    "readback": "Saved check outcome is historical and unauthenticated; fresh verify is required for a new numerical comparison",
}
_EPSILON = Fraction(1, 2**52)
_SUBNORMAL = Fraction(1, 2**1074)


def _fraction(value):
    return Fraction(float(value))


def _up(value):
    """Return a nonnegative binary64 display value no smaller than the rational."""
    result = float(value)
    return math.nextafter(result, math.inf) if Fraction(result) < value else result


def _check(name):
    return {"name": name, "status": "PASS", "compared_values": 0,
            "mismatches": 0, "maximum_error": 0.0, "maximum_allowed_error": 0.0}


def _compare(check, actual, terms, *, squared=False):
    exact = sum(terms, Fraction())
    allowed = 16 * _EPSILON * sum(map(abs, terms), Fraction()) + 64 * _SUBNORMAL
    observed = _fraction(actual)
    error = abs((observed * observed if squared else observed) - exact)
    check["compared_values"] += 1
    check["maximum_error"] = max(check["maximum_error"], _up(error))
    check["maximum_allowed_error"] = max(check["maximum_allowed_error"], _up(allowed))
    if error > allowed:
        check["mismatches"] += 1
        check["status"] = "FAIL"


def _equal(check, actual, expected):
    check["compared_values"] += 1
    if actual != expected:
        check["mismatches"] += 1
        check["maximum_error"] = 1.0
        check["status"] = "FAIL"


def _covariance_terms(request, matrix, raw, gain, i, j):
    """Expand the affine covariance directly, independently of provider products."""
    n = len(raw)
    terms = [
        gain * gain * matrix[i][j],
        gain * raw[j] * matrix[i][n],
        gain * matrix[i][n + 1],
        raw[i] * gain * matrix[n][j],
        raw[i] * raw[j] * matrix[n][n],
        raw[i] * matrix[n][n + 1],
        gain * matrix[n + 1][j],
        raw[j] * matrix[n + 1][n],
        matrix[n + 1][n + 1],
    ]
    terms.extend(_fraction(source["covariance_K2"][i][j]) for source in request["contributions"])
    return terms


def _endpoints(sample, factor):
    center = _fraction(sample["value_K"])
    radius = _fraction(sample["standard_uncertainty_K"]) * _fraction(factor)
    lower, upper = center - radius, center + radius
    left, right = float(lower), float(upper)
    while Fraction(left) > lower:
        left = math.nextafter(left, -math.inf)
    while Fraction(right) < upper:
        right = math.nextafter(right, math.inf)
    return [left, right]


def _decisions(request, report, check):
    policy, actual = request["acceptance"], report["acceptance"]
    if policy is None:
        _equal(check, actual["status"], "not_requested")
        _equal(check, actual["sample_results"], [])
        return
    target = report[policy["quantity"]]
    budget = {entry["category"]: entry["status"] for entry in request["budget"]}
    blocked = (any(budget.get(category, "omitted") == "omitted" for category in BUDGET_CATEGORIES)
               or target["status"] == "unavailable"
               or any(row["status"] == "invalid" for row in target["samples"]))
    rows = []
    for sample in target["samples"]:
        interval, status = None, "indeterminate"
        if not blocked:
            interval = _endpoints(sample, policy["coverage_factor"])
            if policy["lower"] <= interval[0] and interval[1] <= policy["upper"]:
                status = "conditional_pass"
            elif interval[1] < policy["lower"] or interval[0] > policy["upper"]:
                status = "conditional_fail"
        rows.append({"sample_id": sample["sample_id"], "status": status, "expanded_interval_K": interval})
    if blocked or not rows:
        overall = "indeterminate"
    elif any(row["status"] == "conditional_fail" for row in rows):
        overall = "conditional_fail"
    elif all(row["status"] == "conditional_pass" for row in rows):
        overall = "conditional_pass"
    else:
        overall = "indeterminate"
    for observed, expected in zip(actual["sample_results"], rows):
        _equal(check, observed, expected)
    _equal(check, len(actual["sample_results"]), len(rows))
    _equal(check, actual["status"], overall)


def verify(value: dict, processing_report: dict) -> dict:
    """Fresh numerical audit; malformed contracts refuse, arithmetic mismatches fail.

    Shared request/report readers establish structure and bindings. The numerical
    reference never invokes process_request or its propagation implementation.
    A PASS with zero active samples means abstention was preserved, not that any
    unavailable temperature was verified. Counts remain visible in every check.
    """
    request = validate_request(value)
    report = detached(processing_report)
    validate_report(request, report)
    checks = [_check(name) for name in CHECK_NAMES]
    _equal(checks[0], report["identities"]["request_ref"], digest(request))
    raw_rows = request["raw_observations"]["samples"]
    raw = [None if row["value"] is None else _fraction(row["value"]) for row in raw_rows]
    gain = _fraction(request["calibration"]["gain"])
    offset = _fraction(request["calibration"]["offset_K"])
    matrix = [[_fraction(x) for x in row] for row in request["joint_covariance"]["matrix"]]
    sensor, polymer = report["calibrated_sensor_temperature"], report["polymer_temperature"]
    active = [i for i, row in enumerate(sensor["samples"]) if row["status"] != "invalid"]
    for i in active:
        _compare(checks[1], sensor["samples"][i]["value_K"], [gain * raw[i], offset])
        for j in active:
            _compare(checks[2], sensor["covariance_K2"][i][j],
                     _covariance_terms(request, matrix, raw, gain, i, j))
        _compare(checks[2], sensor["samples"][i]["standard_uncertainty_K"],
                 _covariance_terms(request, matrix, raw, gain, i, i), squared=True)
    correction = request["installed_correction"]
    if correction is None:
        _equal(checks[3], polymer["status"], "unavailable")
    else:
        installed = [i for i, row in enumerate(polymer["samples"]) if row["status"] != "invalid"]
        for i in installed:
            _compare(checks[3], polymer["samples"][i]["value_K"],
                     [gain * raw[i], offset, _fraction(correction["offsets_K"][i])])
            for j in installed:
                _compare(checks[3], polymer["covariance_K2"][i][j],
                         _covariance_terms(request, matrix, raw, gain, i, j) +
                         [_fraction(correction["covariance_K2"][i][j])])
            _compare(checks[3], polymer["samples"][i]["standard_uncertainty_K"],
                     _covariance_terms(request, matrix, raw, gain, i, i) +
                     [_fraction(correction["covariance_K2"][i][i])], squared=True)
    _decisions(request, report, checks[4])
    audit = seal({"schema": SCHEMA, "status": "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS",
                  "request_ref": digest(request), "report_ref": digest(report),
                  "checker_contract": deepcopy(CHECKER_CONTRACT), "checks": checks,
                  "authority": deepcopy(AUTHORITY)})
    validate_verification(request, report, audit)
    return audit


def validate_verification(value: dict, processing_report: dict, audit: dict) -> None:
    """Validate a historical audit's structure and bindings without fresh checking.

    A resealed audit can invent a successful check. Only verify executes the
    numerical reference; neither path authenticates the submitter or sensor.
    """
    request = validate_request(value)
    validate_report(request, processing_report)
    detached(audit)
    keys(audit, {"schema", "status", "request_ref", "report_ref", "checker_contract", "checks", "authority", "record_digest"})
    check_seal(audit)
    if (audit["schema"] != SCHEMA or audit["request_ref"] != digest(request)
            or audit["report_ref"] != digest(processing_report)
            or audit["checker_contract"] != CHECKER_CONTRACT or audit["authority"] != AUTHORITY):
        raise ValueError("Temperature audit differs from its bound request/report or checker authority")
    for ref in (audit["request_ref"], audit["report_ref"]):
        content_ref(ref)
    checks = audit["checks"]
    if type(checks) is not list or len(checks) != len(CHECK_NAMES):
        raise ValueError("Temperature audit requires every fixed check")
    active = sum(row["status"] != "invalid" for row in processing_report["calibrated_sensor_temperature"]["samples"])
    installed = sum(row["status"] != "invalid" for row in processing_report["polymer_temperature"]["samples"])
    decisions = 2 if request["acceptance"] is None else len(processing_report[request["acceptance"]["quantity"]]["samples"]) + 2
    counts = [1, active, active**2 + active,
              1 if request["installed_correction"] is None else installed**2 + 2 * installed, decisions]
    for check, name, expected_count in zip(checks, CHECK_NAMES, counts):
        keys(check, {"name", "status", "compared_values", "mismatches", "maximum_error", "maximum_allowed_error"})
        count, mismatches = check["compared_values"], check["mismatches"]
        if (check["name"] != name or type(count) is not int or count != expected_count
                or type(mismatches) is not int or not 0 <= mismatches <= count
                or check["status"] != ("FAIL" if mismatches else "PASS")
                or number(check["maximum_error"]) < 0 or number(check["maximum_allowed_error"]) < 0
                or mismatches == 0 and check["maximum_error"] > check["maximum_allowed_error"]
                or mismatches and check["maximum_error"] == 0
                or count == 0 and (check["maximum_error"] != 0 or check["maximum_allowed_error"] != 0)):
            raise ValueError("Invalid temperature audit check summary")
    expected = "FAIL" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    if audit["status"] != expected:
        raise ValueError("Temperature audit outcome differs from fixed checks")
