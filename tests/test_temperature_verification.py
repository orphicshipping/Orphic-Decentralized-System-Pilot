"""Independent synthetic temperature evidence; no physical qualification.

Dyadic latent-factor fixtures have exact first-order covariances derived through
their latent coordinates, independently of the processor's indexed J C J^T loop.
"""
from copy import deepcopy
from fractions import Fraction as F
import math

import pytest

from ciw.operations.runner import digest, seal
from ciw.temperature_contract import AUTHORITY, example_request
from ciw.temperature_processing import process_request, validate_report
from ciw.temperature_verification import verify, validate_verification


CHECKS = {
    "retained_contract_and_source_binding",
    "independent_affine_transform",
    "independent_joint_covariance",
    "independent_installed_correction",
    "independent_sample_decisions",
}


def _gram(rows):
    return [[sum((a * b for a, b in zip(left, right)), F(0))
             for right in rows] for left in rows]


def _floats(matrix):
    return [[float(value) for value in row] for row in matrix]


def _case(n=3, gain=2, *, extra=False, installed=False):
    request = example_request()
    request["clock"]["end_s"] = float(max(1, n - 1))
    request["raw_observations"]["samples"] = [
        {"sample_id": f"s{i}", "time_s": float(i),
         "received_time_s": float(i) + 0.125,
         "value": float(F(1) + F(i, 64)), "status": "ok"}
        for i in range(n)]
    # Four independent latent coordinates; shared factors deliberately correlate
    # raw observations with one another, gain and offset.
    latent = [[F(1 + i % 3, 32), F((-1) ** i, 64), F(i % 2, 32), F(0)]
              for i in range(n)]
    latent += [[F(1, 16), F(-1, 32), F(0), F(1, 16)],
               [F(1, 4), F(0), F(1, 8), F(1, 16)]]
    covariance = _gram(latent)
    request["calibration"].update(
        gain=float(gain), offset_K=310.0,
        coefficient_covariance=_floats([row[-2:] for row in covariance[-2:]]))
    request["joint_covariance"].update(
        order=[f"raw:s{i}" for i in range(n)] + ["gain", "offset_K"],
        matrix=_floats(covariance), cross_covariance_policy="declared")
    for row in request["budget"]:
        if row["status"] == "omitted":
            row.update(status="not_applicable", reason="Synthetic fixture assumption only",
                       evidence_ref=digest({"synthetic_budget": row["category"]}))
    request["acceptance"].update(lower=280.0, upper=350.0)
    propagated = []
    for i in range(n):
        raw = F(request["raw_observations"]["samples"][i]["value"])
        propagated.append([F(gain) * latent[i][k] + raw * latent[n][k] + latent[n + 1][k]
                           for k in range(4)])
    sensor_covariance = _gram(propagated)
    if extra:
        extra_latent = [[F(1, 16), F((-1) ** i, 32)] for i in range(n)]
        extra_covariance = _gram(extra_latent)
        request["contributions"] = [{
            "source_id": "additional", "dependency_ids": ["synthetic.independent.extra"],
            "evidence_ref": digest({"synthetic_extra": 1}),
            "correlation_policy": "independent_of_all_other_sources",
            "covariance_K2": _floats(extra_covariance)}]
        for row in request["budget"]:
            if row["category"] in {"drift", "environment"}:
                row.update(status="included", source_ids=["additional"],
                           evidence_ref=digest({"synthetic_extra_budget": 1}))
        sensor_covariance = [[sensor_covariance[i][j] + extra_covariance[i][j]
                              for j in range(n)] for i in range(n)]
    polymer_covariance = None
    if installed:
        correction_covariance = _gram([[F(1, 16), F(i % 2, 32)] for i in range(n)])
        request["installed_correction"] = {
            "source_id": "installed", "dependency_ids": ["synthetic.independent.installation"],
            "evidence_ref": digest({"synthetic_installed": 1}),
            "correlation_policy": "independent_of_all_other_sources",
            "offsets_K": [float(F(i % 3, 8)) for i in range(n)],
            "covariance_K2": _floats(correction_covariance),
            **{key: request["identity"][key] for key in
               ("installation_id", "location_id", "material_id", "specimen_id")},
            "valid_time_s": [0.0, 100.0], "ambient_range_K": [290.0, 300.0],
            "heating_rate_range_K_s": [0.0, 20.0]}
        for row in request["budget"]:
            if row["category"] == "installation":
                row.update(status="included", source_ids=["installed"],
                           evidence_ref=digest({"synthetic_installed_budget": 1}))
        polymer_covariance = [[sensor_covariance[i][j] + correction_covariance[i][j]
                               for j in range(n)] for i in range(n)]
        request["acceptance"]["quantity"] = "polymer_temperature"
    return request, sensor_covariance, polymer_covariance


def _assert_pass(request, report):
    audit = verify(request, report)
    assert audit["status"] == "PASS"
    assert audit["authority"] == AUTHORITY
    assert audit["request_ref"] == digest(request)
    assert audit["report_ref"] == digest(report)
    assert {check["name"] for check in audit["checks"]} == CHECKS
    assert all(check["status"] == "PASS" for check in audit["checks"])
    validate_verification(request, report, audit)
    return audit


@pytest.mark.parametrize("n,gain,extra,installed", [
    (1, 2, False, False), (3, 2, False, False),
    (3, -2, False, False), (3, 2, True, False),
    (3, -2, True, True), (64, 2, True, True),
])
def test_exact_dyadic_latent_oracle(n, gain, extra, installed):
    request, expected_sensor, expected_polymer = _case(
        n, gain, extra=extra, installed=installed)
    report = process_request(request)
    sensor = report["calibrated_sensor_temperature"]
    # All these finite dyadic products/sums fit binary64 exactly.
    assert sensor["covariance_K2"] == _floats(expected_sensor)
    assert [row["value_K"] for row in sensor["samples"]] == [
        float(F(gain) * F(row["value"]) + F(310))
        for row in request["raw_observations"]["samples"]]
    if installed:
        polymer = report["polymer_temperature"]
        assert polymer["covariance_K2"] == _floats(expected_polymer)
        assert [row["value_K"] for row in polymer["samples"]] == [
            float(F(sensor["samples"][i]["value_K"]) + F(offset))
            for i, offset in enumerate(request["installed_correction"]["offsets_K"])]
    _assert_pass(request, report)


@pytest.mark.parametrize("installed", [False, True])
def test_verifier_uses_no_processing_calculation(monkeypatch, installed):
    request, _, _ = _case(installed=installed, extra=True)
    report = process_request(request)

    def forbidden(*args, **kwargs):
        raise AssertionError("Independent verification dispatched processing")

    monkeypatch.setattr("ciw.temperature_processing.process_request", forbidden)
    monkeypatch.setattr("ciw.temperature_processing._sensor_output", forbidden)
    if installed:
        monkeypatch.setattr("ciw.temperature_processing._polymer_output", forbidden)
    _assert_pass(request, report)


@pytest.mark.parametrize("change", ["sensor_value", "sensor_covariance", "polymer_value", "polymer_covariance"])
def test_resealed_structurally_consistent_wrong_numbers_fail_independent_verification(change):
    request, _, _ = _case(installed=True, extra=True)
    request["acceptance"] = None
    report = process_request(request)
    changed = deepcopy(report)
    output = changed["polymer_temperature" if change.startswith("polymer") else "calibrated_sensor_temperature"]
    if change.endswith("value"):
        output["samples"][0]["value_K"] += 0.25
    else:
        # Add a PSD common-mode component, preserving shape/symmetry/PSD and the
        # exact reported diagonal-to-standard-uncertainty relationship.
        for row in output["covariance_K2"]:
            for j in range(len(row)):
                row[j] += 0.125
        for i, sample in enumerate(output["samples"]):
            sample["standard_uncertainty_K"] = math.sqrt(output["covariance_K2"][i][i])
    seal(changed)
    validate_report(request, changed)  # Structural integrity is deliberately not numerical truth.
    audit = verify(request, changed)
    assert audit["status"] == "FAIL"
    assert any(check["status"] == "FAIL" for check in audit["checks"])
    assert audit["authority"] == AUTHORITY
    validate_verification(request, changed, audit)


def test_verification_record_readback_does_not_rerun_oracle(monkeypatch):
    request, _, _ = _case()
    report = process_request(request)
    audit = verify(request, report)

    def forbidden(*args, **kwargs):
        raise AssertionError("Reading retained audit dispatched the oracle")

    monkeypatch.setattr("ciw.temperature_verification.verify", forbidden)
    validate_verification(request, report, audit)
    stale = deepcopy(report)
    stale["calibrated_sensor_temperature"]["samples"][0]["value_K"] += 0.25
    seal(stale)
    with pytest.raises(ValueError):
        validate_verification(request, stale, audit)


@pytest.mark.parametrize("change", ["broken_seal", "request_binding"])
def test_malformed_integrity_or_source_binding_cannot_be_verified(change):
    request, _, _ = _case()
    report = process_request(request)
    if change == "broken_seal":
        report["calibrated_sensor_temperature"]["samples"][0]["value_K"] += 0.25
    else:
        request["identity"]["cycle_id"] = "different-cycle"
    with pytest.raises(ValueError):
        verify(request, report)


@pytest.mark.parametrize("status,value", [("missing", None), ("saturated", 1.0), ("invalid", None)])
def test_independent_verification_preserves_invalid_axes(status, value):
    request, _, _ = _case(installed=True)
    request["raw_observations"]["samples"][1].update(status=status, value=value)
    report = process_request(request)
    for quantity in ("calibrated_sensor_temperature", "polymer_temperature"):
        output = report[quantity]
        assert output["samples"][1]["value_K"] is None
        assert output["covariance_K2"][1] == [None] * 3
        assert all(row[1] is None for row in output["covariance_K2"])
        assert output["samples"][0]["value_K"] is not None
    assert report["acceptance"]["status"] == "indeterminate"
    _assert_pass(request, report)


@pytest.mark.parametrize("domain", [
    "raw_lower", "raw_upper", "output_lower", "output_upper",
    "ambient_lower", "ambient_upper", "ramp_lower", "ramp_upper",
    "time_lower", "time_upper", "latency",
])
@pytest.mark.parametrize("outside", [False, True], ids=["closed-endpoint", "one-ulp-outside"])
def test_sensor_applicability_exact_boundary_and_nextafter(domain, outside):
    request, _, _ = _case()
    calibration = request["calibration"]
    samples = request["raw_observations"]["samples"]
    index = 0
    if domain.startswith("raw"):
        lower = domain.endswith("lower")
        value = calibration["input_range"][0 if lower else 1]
        samples[0]["value"] = math.nextafter(value, -math.inf if lower else math.inf) if outside else value
        flag = "raw_outside_calibration"
    elif domain.startswith("output"):
        lower = domain.endswith("lower")
        index = 0 if lower else len(samples) - 1
        nominal = calibration["gain"] * samples[index]["value"] + calibration["offset_K"]
        calibration["output_range_K"][0 if lower else 1] = (
            math.nextafter(nominal, math.inf if lower else -math.inf) if outside else nominal)
        flag = "temperature_outside_calibration"
    elif domain.startswith(("ambient", "ramp")):
        ambient = domain.startswith("ambient")
        key, envelope = (("ambient_temperature_K", "ambient_range_K") if ambient else
                         ("maximum_heating_rate_K_s", "heating_rate_range_K_s"))
        if not ambient:
            calibration[envelope] = [1.0, 20.0]
        lower = domain.endswith("lower")
        bound = calibration[envelope][0 if lower else 1]
        request["conditions"][key] = math.nextafter(bound, -math.inf if lower else math.inf) if outside else bound
        flag = "ambient_outside_calibration" if ambient else "heating_rate_outside_calibration"
    elif domain.startswith("time"):
        lower = domain.endswith("lower")
        index = 0 if lower else len(samples) - 1
        bound = samples[index]["time_s"]
        calibration["valid_time_s"][0 if lower else 1] = (
            math.nextafter(bound, math.inf if lower else -math.inf) if outside else bound)
        flag = "time_outside_calibration"
    else:
        bound = request["clock"]["max_latency_s"]
        samples[0]["received_time_s"] = math.nextafter(bound, math.inf) if outside else bound
        flag = "stale_observation"
    report = process_request(request)
    row = report["calibrated_sensor_temperature"]["samples"][index]
    assert (flag in row["flags"]) is outside
    assert (row["value_K"] is None) is outside
    if outside:
        assert report["acceptance"]["status"] == "indeterminate"
    _assert_pass(request, report)


@pytest.mark.parametrize("domain", [
    "installation_id", "location_id", "material_id", "specimen_id",
    "ambient_lower", "ambient_upper", "ramp_lower", "ramp_upper",
    "time_lower", "time_upper", "positive_temperature",
])
@pytest.mark.parametrize("outside", [False, True], ids=["applicable", "outside"])
def test_installed_applicability_never_invalidates_sensor_observation(domain, outside):
    request, _, _ = _case(installed=True)
    correction = request["installed_correction"]
    calibration = request["calibration"]
    calibration["ambient_range_K"] = [280.0, 310.0]
    calibration["heating_rate_range_K_s"] = [0.0, 30.0]
    samples = request["raw_observations"]["samples"]
    index = 0
    if domain.endswith("_id"):
        if outside:
            correction[domain] = "different." + domain
        flag = "installed_correction_" + domain + "_mismatch"
    elif domain.startswith(("ambient", "ramp")):
        ambient = domain.startswith("ambient")
        key, envelope = (("ambient_temperature_K", "ambient_range_K") if ambient else
                         ("maximum_heating_rate_K_s", "heating_rate_range_K_s"))
        if not ambient:
            correction[envelope] = [1.0, 20.0]
        lower = domain.endswith("lower")
        bound = correction[envelope][0 if lower else 1]
        request["conditions"][key] = math.nextafter(bound, -math.inf if lower else math.inf) if outside else bound
        flag = "ambient_outside_installed_correction" if ambient else "heating_rate_outside_installed_correction"
    elif domain.startswith("time"):
        lower = domain.endswith("lower")
        index = 0 if lower else len(samples) - 1
        bound = samples[index]["time_s"]
        correction["valid_time_s"][0 if lower else 1] = (
            math.nextafter(bound, math.inf if lower else -math.inf) if outside else bound)
        flag = "time_outside_installed_correction"
    else:
        nominal = calibration["gain"] * samples[0]["value"] + calibration["offset_K"]
        correction["offsets_K"][0] = -nominal if outside else math.nextafter(-nominal, math.inf)
        flag = "nonpositive_polymer_temperature"
    report = process_request(request)
    assert all(row["value_K"] is not None for row in report["calibrated_sensor_temperature"]["samples"])
    polymer = report["polymer_temperature"]["samples"][index]
    assert (flag in polymer["flags"]) is outside
    assert (polymer["value_K"] is None) is outside
    if outside:
        assert report["acceptance"]["status"] == "indeterminate"
    _assert_pass(request, report)


def test_first_order_result_is_not_an_exact_product_distribution_claim():
    """Exact enumeration exposes an omitted product term without sampling noise.

    Independent equiprobable signs give X=1+/-1/2 and G=2+/-1/4.
    Var(GX)=g^2 Var(X)+x^2 Var(G)+Var(G)Var(X).
    This synthetic distribution does not qualify a physical calibration or k.
    """
    request, _, _ = _case(n=1)
    request["joint_covariance"]["matrix"] = [[0.25, 0.0, 0.0],
                                             [0.0, 0.0625, 0.0],
                                             [0.0, 0.0, 0.0]]
    request["calibration"]["coefficient_covariance"] = [[0.0625, 0.0], [0.0, 0.0]]
    request["joint_covariance"]["cross_covariance_policy"] = "declared_zero"
    outcomes = [(F(2) + F(sg, 4)) * (F(1) + F(sx, 2)) + F(310)
                for sg in (-1, 1) for sx in (-1, 1)]
    exact_mean = sum(outcomes) / 4
    exact_variance = sum((value - exact_mean) ** 2 for value in outcomes) / 4
    report = process_request(request)
    first_order = F(report["calibrated_sensor_temperature"]["covariance_K2"][0][0])
    assert exact_mean == F(312)
    assert first_order == F(17, 16)
    assert exact_variance == F(69, 64)
    assert exact_variance - first_order == F(1, 64)
    assert "first-order" in report["numerical_contract"]["approximation"]
    assert report["authority"] == AUTHORITY
    _assert_pass(request, report)



@pytest.mark.parametrize("lower,upper,expected", [
    (311.0, 313.0, "conditional_pass"),
    (math.nextafter(311.0, math.inf), 313.0, "indeterminate"),
    (311.0, math.nextafter(313.0, -math.inf), "indeterminate"),
    (313.0, 314.0, "indeterminate"),
    (math.nextafter(313.0, math.inf), 314.0, "conditional_fail"),
    (310.0, 311.0, "indeterminate"),
    (310.0, math.nextafter(311.0, -math.inf), "conditional_fail"),
])
def test_nonzero_uncertainty_acceptance_touching_and_one_ulp_separation(lower, upper, expected):
    request, _, _ = _case(n=1)
    request["joint_covariance"]["matrix"] = [[0.0, 0.0, 0.0],
                                             [0.0, 0.0, 0.0],
                                             [0.0, 0.0, 0.25]]
    request["calibration"]["coefficient_covariance"] = [[0.0, 0.0], [0.0, 0.25]]
    request["joint_covariance"]["cross_covariance_policy"] = "declared_zero"
    request["acceptance"].update(lower=lower, upper=upper, coverage_factor=2.0)
    report = process_request(request)
    assert report["acceptance"]["sample_results"][0]["expanded_interval_K"] == [311.0, 313.0]
    assert report["acceptance"]["status"] == expected
    _assert_pass(request, report)



def test_real_intermediate_underflow_can_fail_fixed_comparison_policy():
    """The comparison policy is not a universal forward-error enclosure.

    In one covariance product the provider first multiplies 1e-12 by 1e-312,
    losing that intermediate to underflow before its later 1e12 multiplier.
    A structurally valid unmodified report can consequently fail the independent
    exact-rational comparison. This is numerical evidence, not physical evidence.
    """
    request, _, _ = _case(n=1)
    request["raw_observations"]["samples"][0]["value"] = 1e12
    request["calibration"].update(
        gain=1e-12, offset_K=290.0, input_range=[0.0, 1e12],
        coefficient_covariance=[[math.ulp(0.0), 0.0], [0.0, 0.0]])
    request["joint_covariance"].update(
        matrix=[[1e-300, 1e-312, 0.0],
                [1e-312, math.ulp(0.0), 0.0],
                [0.0, 0.0, 0.0]],
        cross_covariance_policy="declared")
    request["acceptance"] = None
    report = process_request(request)
    validate_report(request, report)
    audit = verify(request, report)
    assert audit["status"] == "FAIL"
    covariance_check = next(check for check in audit["checks"]
                            if check["name"] == "independent_joint_covariance")
    assert covariance_check["status"] == "FAIL"
    assert report["authority"] == audit["authority"] == AUTHORITY
    assert audit["request_ref"] == digest(request)
    assert audit["report_ref"] == digest(report)
    validate_verification(request, report, audit)

