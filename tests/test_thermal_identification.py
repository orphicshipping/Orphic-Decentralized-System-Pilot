from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from ciw import thermal_identification as thermal
from ciw import thermal_identification_workflow as workflow
from ciw import model_reality as reality
from ciw.core.covariance import covariance_identity, validate_covariance_artifact
from ciw.core.identities import content_identity
from ciw.operations.runner import seal


@pytest.fixture
def request_data():
    return thermal.example_request()


def state(value, origin, uncertainty=0.2, stamp=1.0):
    return {"value": value, "origin": origin, "time_s": stamp,
            "standard_uncertainty": uncertainty, "source_ref": content_identity({"origin": origin}),
            "uncertainty_scope": "unknown" if uncertainty is None else "declared_marginal"}


def contract(**states):
    return reality.make(entity_id="part.1", quantity="part_temperature", unit="K",
                        frame="part.v1", clock_id="clock.1", states=states)


@pytest.mark.parametrize("amplitude", [10.0, 80.0, 250.0])
@pytest.mark.parametrize("tau", [2.0, 12.0, 60.0])
def test_recovers_known_fixture_parameters(request_data, amplitude, tau):
    for i, sample in enumerate(request_data["samples"]):
        sample["time_s"] = i * tau / 6
        sample["temperature_k"] = 300 + amplitude * math.exp(-sample["time_s"] / tau)
    result = thermal.fit(request_data)
    assert result["status"] == "candidate"
    assert result["model"]["tau_s"] == pytest.approx(tau, rel=1e-9)
    assert result["model"]["initial_excess_k"] == pytest.approx(amplitude, rel=1e-9)
    assert thermal.validate_result(request_data, result) == result
    assert result["authority"] == thermal.AUTHORITY


def test_known_measurement_noise_not_scaled_to_zero(request_data):
    result = thermal.fit(request_data)
    covariance = result["parameter_covariance"]
    validate_covariance_artifact(covariance)
    assert covariance["matrix"][1][1] > 0
    assert covariance["matrix"][0][1] < 0  # Cross-covariance is retained.
    a, tau = result["model"]["initial_excess_k"], result["model"]["tau_s"]
    t, _, u = thermal._arrays(request_data, "training")
    e = np.exp(-t / tau)
    j = np.column_stack((e, a * e * t / tau**2)) / u[:, None]
    gram = j.T @ j
    # Independent analytic inverse of a 2x2 information matrix.
    det = gram[0, 0] * gram[1, 1] - gram[0, 1] * gram[1, 0]
    expected = np.array([[gram[1, 1], -gram[0, 1]], [-gram[1, 0], gram[0, 0]]]) / det
    np.testing.assert_allclose(covariance["matrix"], expected, rtol=1e-12, atol=1e-15)


def test_prediction_retains_covariance_cross_terms(request_data):
    result = thermal.fit(request_data)
    prediction = thermal.predict(request_data, result, 10.0)
    assert prediction["temperature_k"] == pytest.approx(300 + 80 * math.exp(-10 / 12))
    cov = np.array(result["parameter_covariance"]["matrix"])
    e = math.exp(-10 / 12)
    j = np.array([e, 80 * e * 10 / 12**2])
    assert prediction["standard_uncertainty_k"]**2 == pytest.approx(j @ cov @ j, rel=1e-9)
    assert prediction["uncertainty_scope"] == "conditional_mean_parameter_only"


@pytest.mark.parametrize("time", [-1.0, 46.00001, True, float("nan"), float("inf")])
def test_prediction_refuses_extrapolation_and_bad_times(request_data, time):
    with pytest.raises(ValueError):
        thermal.predict(request_data, thermal.fit(request_data), time)


def test_holdout_never_enters_fit(request_data):
    original = thermal.fit(request_data)
    for sample in request_data["samples"]:
        if sample["split"] == "holdout":
            sample["temperature_k"] += 15
    altered = thermal.fit(request_data)
    assert altered["model"] == original["model"]
    assert altered["status"] == "rejected"
    assert "holdout_residual_gate_failed" in altered["reasons"]
    with pytest.raises(ValueError):
        thermal.predict(request_data, altered, 5.0)


def test_bad_training_fit_rejected(request_data):
    request_data["samples"][4]["temperature_k"] += 12
    result = thermal.fit(request_data)
    assert "training_residual_gate_failed" in result["reasons"]
    thermal.validate_result(request_data, result)


def test_constant_trace_is_unidentifiable(request_data):
    for sample in request_data["samples"]:
        sample["temperature_k"] = 300.0
    result = thermal.fit(request_data)
    assert result["status"] == "rejected"
    assert "insufficient_thermal_excitation" in result["reasons"]
    assert "rank_deficient_parameter_jacobian" in result["reasons"]
    assert result["parameter_covariance"] is None
    thermal.validate_result(request_data, result)


def test_heating_candidate_is_not_cooling(request_data):
    for sample in request_data["samples"]:
        sample["temperature_k"] = 300 - 80 * math.exp(-sample["time_s"] / 12)
    result = thermal.fit(request_data)
    assert "non_cooling_candidate" in result["reasons"]
    assert result["status"] == "rejected"


def test_bound_limited_candidate_is_rejected(request_data):
    request_data["tau_bounds_s"] = [20.0, 100.0]
    result = thermal.fit(request_data)
    assert result["model"]["tau_s"] >= 20.0
    assert "bound_limited_candidate" in result["reasons"]
    thermal.validate_result(request_data, result)


def test_ill_conditioned_candidate_is_rejected(request_data):
    request_data["gates"]["max_condition_number"] = 1.01
    result = thermal.fit(request_data)
    assert "ill_conditioned_parameter_jacobian" in result["reasons"]
    assert result["parameter_covariance"] is None


@pytest.mark.parametrize("value", [True, None, float("nan"), float("inf"), -1.0, 0.0, 1e151])
def test_invalid_sample_uncertainty(request_data, value):
    request_data["samples"][0]["standard_uncertainty_k"] = value
    with pytest.raises(ValueError):
        thermal.fit(request_data)


@pytest.mark.parametrize("field,value", [("unit", "degC"), ("quantity", "mold_temperature"),
    ("source_kind", "verified"), ("source_ref", "not-a-hash"), ("calibration_ref", ""),
    ("boundary_temperature_k", 0), ("assumptions", []), ("clock_id", "")])
def test_invalid_request_fields(request_data, field, value):
    request_data[field] = value
    with pytest.raises(ValueError):
        thermal.fit(request_data)


@pytest.mark.parametrize("bounds", [[0, 1], [1, 1], [2, 1], [1], [True, 100], [1e-8, 100], [1, 1e7]])
def test_invalid_bounds(request_data, bounds):
    request_data["tau_bounds_s"] = bounds
    with pytest.raises(ValueError):
        thermal.fit(request_data)


def test_duplicate_and_reordered_samples_are_rejected(request_data):
    for key in ("sample_id", "time_s"):
        changed = deepcopy(request_data)
        changed["samples"][1][key] = changed["samples"][0][key]
        with pytest.raises(ValueError):
            thermal.fit(changed)
    request_data["samples"][19]["split"] = "training"
    with pytest.raises(ValueError):
        thermal.fit(request_data)


def test_all_training_is_not_holdout_validation(request_data):
    for sample in request_data["samples"]:
        sample["split"] = "training"
    with pytest.raises(ValueError):
        thermal.fit(request_data)


def test_work_accounting_is_bounded_and_exact(request_data, monkeypatch):
    original = thermal._profile
    count = 0
    def counted(*args):
        nonlocal count
        count += 1
        return original(*args)
    monkeypatch.setattr(thermal, "_profile", counted)
    result = thermal.fit(request_data)
    assert count == result["work"]["profile_evaluations"] == 132


def test_inspection_does_not_refit(request_data, monkeypatch):
    result = thermal.fit(request_data)
    def forbidden(*args, **kwargs):
        raise AssertionError("review must not dispatch a fit")
    monkeypatch.setattr(thermal, "fit", forbidden)
    monkeypatch.setattr(thermal, "_profile", forbidden)
    thermal.validate_result(request_data, result)


def test_float_roundoff_accepted_but_status_and_counts_exact(request_data):
    result = thermal.fit(request_data)
    altered = deepcopy(result)
    value = altered["residuals"][0]["predicted_k"]
    altered["residuals"][0]["predicted_k"] = math.nextafter(value, math.inf)
    thermal.validate_result(request_data, seal(altered))
    for change in ("status", "count", "authority", "residual", "source", "extra"):
        altered = deepcopy(result)
        if change == "status": altered["status"] = "qualified"
        elif change == "count": altered["work"]["profile_evaluations"] += 1
        elif change == "authority": altered["authority"]["hardware_actuation"] = "authorized"
        elif change == "residual": altered["residuals"][0]["predicted_k"] += 1.0
        elif change == "source": altered["request_ref"] = content_identity({"wrong": True})
        else: altered["extra"] = "not allowed"
        with pytest.raises(ValueError):
            thermal.validate_result(request_data, seal(altered))


def test_covariance_tampering_rejected_even_when_rehashed(request_data):
    result = thermal.fit(request_data)
    covariance = result["parameter_covariance"]
    covariance["matrix"][0][1] = covariance["matrix"][1][0] = 0.0
    covariance["covariance_id"] = covariance_identity(covariance)
    with pytest.raises(ValueError):
        thermal.validate_result(request_data, seal(result))


def test_request_and_result_are_detached(request_data):
    original = deepcopy(request_data)
    result = thermal.fit(request_data)
    assert request_data == original
    detached = thermal.validate_result(request_data, result)
    detached["identity"]["part_id"] = "changed"
    assert result["identity"]["part_id"] != "changed"


def test_reality_has_six_distinct_slots_and_no_verified_promotion():
    value = contract(specified=state(350, "specification"), commanded=state(350, "retained_command"),
                     predicted=state(348, "model"), observed=state(347, "retained_observation"),
                     estimated=state(346, "estimation"))
    assert tuple(value["states"]) == reality.ROLES
    assert value["states"]["verified"] is None
    assert value["states"]["specified"]["value"] != value["states"]["observed"]["value"]
    assert value["authority"]["hardware_actuation"] == "not_performed"
    with pytest.raises(ValueError):
        contract(verified=state(350, "retained_observation"))


@pytest.mark.parametrize("role,origin", [("observed", "synthetic"), ("observed", "model"),
    ("commanded", "specification"), ("predicted", "retained_observation"), ("estimated", "retained_command")])
def test_reality_rejects_evidence_role_conflation(role, origin):
    with pytest.raises(ValueError):
        contract(**{role: state(350, origin)})


def test_residual_never_assumes_independence():
    value = contract(predicted=state(350, "model", 2.0), observed=state(349, "retained_observation", 3.0))
    assert reality.residual(value)["standard_uncertainty"] is None
    assert reality.residual(value, cross_covariance=0.0)["standard_uncertainty"] == pytest.approx(math.sqrt(13))
    assert reality.residual(value, cross_covariance=6.0)["standard_uncertainty"] == 1.0
    assert reality.residual(value)["value"] == -1.0
    with pytest.raises(ValueError):
        reality.residual(value, cross_covariance=6.00001)


def test_residual_missing_uncertainty_or_unaligned_time_refused():
    value = contract(predicted=state(350, "model", None), observed=state(349, "retained_observation"))
    assert reality.residual(value)["standard_uncertainty"] is None
    with pytest.raises(ValueError):
        reality.residual(value, cross_covariance=0)
    value = contract(predicted=state(350, "model", stamp=1), observed=state(349, "retained_observation", stamp=2))
    with pytest.raises(ValueError):
        reality.residual(value)


def test_reality_tamper_and_unknown_fields_refused():
    value = contract()
    value["states"]["verified"] = state(350, "retained_observation")
    with pytest.raises(ValueError): reality.validate(seal(value))
    with pytest.raises(ValueError): contract(real=state(350, "retained_observation"))


def test_end_to_end_session_workspace_and_replay(request_data, tmp_path, monkeypatch):
    destination = tmp_path / "first"
    summary = workflow.run(request_data, destination)
    assert summary["execution_status"] == "completed"
    assert summary["candidate_status"] == "candidate"
    request, execution, result = workflow._read(destination)
    assert result["role"] == "state_estimator"
    assert result["verification_status"] == "not_verified"
    assert result["verification_id"] is None
    value = json.loads((destination / "parameter-state.json").read_text())
    assert reality.validate(value)["states"]["verified"] is None
    repeated = workflow.replay(destination, tmp_path / "second")
    assert repeated["numerical_replay"] == "consistent_within_declared_float_tolerance"
    _, e2, r2 = workflow._read(tmp_path / "second")
    assert execution["execution_id"] != e2["execution_id"]
    assert result["evidence_id"] == r2["evidence_id"]
    monkeypatch.setattr(thermal, "fit", lambda *a: pytest.fail("read dispatched fit"))
    assert workflow.inspect(destination)["candidate_status"] == "candidate"


def test_rejected_fit_retained_without_state_export(request_data, tmp_path):
    for sample in request_data["samples"]:
        sample["temperature_k"] = 300.0
    summary = workflow.run(request_data, tmp_path / "rejected")
    assert summary["execution_status"] == "completed"
    assert summary["candidate_status"] == "rejected"
    assert not (tmp_path / "rejected" / "parameter-state.json").exists()


def test_source_integrity_and_sidecar_binding(request_data, tmp_path):
    source = workflow.make_source(request_data)
    source["metadata"]["thermal_identification_request"]["unit"] = "degC"
    with pytest.raises(ValueError):
        workflow.source_request(source)
    directory = tmp_path / "case"
    workflow.run(request_data, directory)
    sidecar = json.loads((directory / "parameter-state.json").read_text())
    sidecar["states"]["estimated"]["value"] = 99.0
    (directory / "parameter-state.json").write_text(json.dumps(seal(sidecar)))
    with pytest.raises(ValueError):
        workflow.inspect(directory)


def test_cli_existing_net_dispatch_and_create_only(tmp_path, capsys):
    from ciw.net import main
    args = ["polymer", "identify", "demo", "--output-dir", str(tmp_path / "demo")]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["source_kind"] == "synthetic"
    assert main(["polymer", "identify", "inspect", str(tmp_path / "demo")]) == 0
    capsys.readouterr()
    assert main(args) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "refused"


def test_noisy_fixture_recovery(request_data):
    rng = np.random.default_rng(7331)
    for sample, noise in zip(request_data["samples"], rng.normal(0, 0.2, 24)):
        sample["temperature_k"] += float(noise)
    result = thermal.fit(request_data)
    assert result["status"] == "candidate"
    assert result["model"]["tau_s"] == pytest.approx(12.0, abs=0.15)
    assert result["diagnostics"]["holdout"]["rms_z"] < 3


def test_no_implicit_execution_grant():
    from ciw.operations.registry import default_registry
    assert workflow.OPERATION not in {o["operation_id"] for o in default_registry().describe()}


def test_hard_bounds_not_relaxed_by_float_tolerance(request_data):
    result = thermal.fit(request_data)
    result["model"]["tau_s"] = math.nextafter(request_data["tau_bounds_s"][0], -math.inf)
    with pytest.raises(ValueError, match="hard bounds"):
        thermal.validate_result(request_data, seal(result))


def test_conditional_uncertainty_not_promoted_to_measurement():
    item = state(350, "retained_observation")
    item["uncertainty_scope"] = "conditional_model"
    with pytest.raises(ValueError):
        contract(observed=item)
    item = state(350, "model", None)
    item["uncertainty_scope"] = "declared_marginal"
    with pytest.raises(ValueError):
        contract(predicted=item)


def test_offline_inspection_leaves_bundle_bytes_unchanged(request_data, tmp_path):
    directory = tmp_path / "review"
    workflow.run(request_data, directory)
    before = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    workflow.inspect(directory)
    after = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert before == after
