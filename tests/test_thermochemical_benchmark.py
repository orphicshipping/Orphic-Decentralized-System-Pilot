"""Numerical tests for an abstract model, never physical material qualification."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import pytest

from ciw import thermochemical_benchmark as tc

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "thermochemical" / "request.json"


@pytest.fixture
def request_data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", sorted(tc.FIELDS))
def test_missing_request_fields_refused(request_data, key):
    del request_data[key]
    with pytest.raises(tc.Refusal):
        tc.validate_request(request_data)


@pytest.mark.parametrize("key", sorted(tc.SOLVER_FIELDS))
def test_missing_solver_fields_refused(request_data, key):
    del request_data["solver"][key]
    with pytest.raises(tc.Refusal):
        tc.validate_request(request_data)


@pytest.mark.parametrize("value", [True, False, "300", None, float("nan"), float("inf"), -float("inf"), 10**1000])
def test_non_numeric_and_nonfinite_refused(request_data, value):
    request_data["initial_temperature_k"] = value
    with pytest.raises(tc.Refusal):
        tc.validate_request(request_data)


@pytest.mark.parametrize("field,value", [
    ("initial_concentration_mol_m3", [-1, 1]),
    ("initial_concentration_mol_m3", [0, 0]),
    ("initial_concentration_mol_m3", [1000, 1]),
    ("initial_concentration_mol_m3", [1]),
    ("initial_concentration_mol_m3", [True, 1]),
    ("species_order", ["A", "A"]), ("species_order", ["A", "C"]),
    ("species_order", ("A", "B")),
    ("heat_capacity_j_m3_k", 0), ("heat_transfer_w_m3_k", -1),
    ("volume_m3", 0), ("activation_energy_j_mol", -1),
    ("reference_rate_s_inv", -1), ("reference_rate_s_inv", 11),
    ("time_s", [1, 2]), ("time_s", [0, 1, 1]),
    ("time_s", [0, 2, 1]), ("time_s", [0]),
    ("time_s", [0, 101]), ("time_s", [0, True]),
    ("time_s", [0, 1e-15]), ("time_s", list(range(129))),
    ("schema", "wrong"), ("model", "reaction-a-to-b.v1"),
])
def test_bad_domain_refused(request_data, field, value):
    request_data[field] = value
    with pytest.raises(tc.Refusal):
        tc.validate_request(request_data)


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, 100001])
def test_bad_budget_refused(request_data, value):
    request_data["solver"]["max_steps"] = value
    with pytest.raises(tc.Refusal):
        tc.validate_request(request_data)


def test_unknown_fields_and_unit_alias_refused(request_data):
    for key in ("provider", "code", "initial_temperature_c"):
        candidate = {**request_data, key: "not accepted"}
        with pytest.raises(tc.Refusal):
            tc.validate_request(candidate)
    request_data["solver"]["method"] = "arbitrary-executable"
    with pytest.raises(tc.Refusal):
        tc.validate_request(request_data)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":{"a":1,"a":2}}',
    b'{"x":NaN}', b'{"x":Infinity}', b'\xff', b'{', b'[]', b'x'*65537,
    b'['*2000 + b']'*2000], ids=[
        'duplicate-root-key', 'duplicate-nested-key', 'nan', 'infinity',
        'invalid-utf8', 'malformed-object', 'wrong-root', 'oversized', 'deeply-nested',
    ])
def test_bad_json_refused(raw):
    with pytest.raises(tc.Refusal):
        tc.load_request(raw)


def test_temperature_envelope_refused(request_data):
    request_data["reaction_internal_energy_j_mol"] = -1e6
    request_data["heat_capacity_j_m3_k"] = 1
    with pytest.raises(tc.Refusal, match="envelope"):
        tc.validate_request(request_data)


@pytest.mark.parametrize("thermal", [True, False])
def test_rate_envelope_refused(request_data, thermal):
    if thermal:
        request_data["heat_transfer_w_m3_k"] = 1e9
    else:
        request_data.update(reference_temperature_k=250, initial_temperature_k=500,
                            bath_temperature_k=500, activation_energy_j_mol=150000,
                            reference_rate_s_inv=10, reaction_internal_energy_j_mol=0)
    with pytest.raises(tc.Refusal, match="rate"):
        tc.validate_request(request_data)


def test_input_is_not_mutated(request_data):
    before = deepcopy(request_data)
    tc.simulate(request_data)
    assert request_data == before
    normalized = tc.validate_request(request_data)
    normalized["solver"]["max_steps"] = 1
    assert request_data == before


@pytest.mark.parametrize("k", [0.0, 0.05, 0.1, 0.5])
@pytest.mark.parametrize("du", [-20000.0, 0.0, 20000.0])
def test_constant_rate_against_analytic(request_data, k, du):
    request_data.update(activation_energy_j_mol=0, reference_rate_s_inv=k,
                        reaction_internal_energy_j_mol=du)
    result = tc.simulate(request_data)
    oracle = tc.constant_rate_reference(request_data)
    comparison = tc.compare_trajectories(request_data, result["trajectory"], oracle)
    assert comparison["within_policy"], comparison
    assert result["balance_metrics"]["energy_balance_max_abs_j"] < 1e-8


@pytest.mark.parametrize("relative_offset", [0.0, 1e-12, -1e-12, 1e-6, -1e-6])
def test_equal_and_nearly_equal_reaction_cooling_rates(request_data, relative_offset):
    request_data.update(activation_energy_j_mol=0, reference_rate_s_inv=0.05 * (1 + relative_offset))
    out = tc.make_report(request_data)
    assert out["analytic_reference"]["status"] == "passed"


@pytest.mark.parametrize("du", [-20000.0, 0.0, 20000.0])
def test_adiabatic_nonlinear_energy_and_sign(request_data, du):
    request_data.update(heat_transfer_w_m3_k=0, reaction_internal_energy_j_mol=du)
    result = tc.simulate(request_data)
    trajectory = result["trajectory"]
    assert all(q == 0 for q in trajectory["heat_to_bath_j"])
    for row, temp in zip(trajectory["concentration_mol_m3"], trajectory["temperature_k"]):
        expected = 300 - du * (row[1] - 0.25) / 2000
        assert temp == pytest.approx(expected, abs=1e-9)
    final = trajectory["temperature_k"][-1]
    assert final > 300 if du < 0 else final < 300 if du > 0 else final == 300


def test_pure_thermal_exchange_with_absent_reactant(request_data):
    request_data["initial_concentration_mol_m3"] = [0, 2]
    result = tc.simulate(request_data)["trajectory"]
    assert all(row == [0.0, 2.0] for row in result["concentration_mol_m3"])
    for t, temp in zip(result["time_s"], result["temperature_k"]):
        assert temp == pytest.approx(290 + 10 * math.exp(-0.05 * t), abs=1e-7)


def test_bath_can_add_heat(request_data):
    request_data.update(reference_rate_s_inv=0, bath_temperature_k=350)
    report = tc.make_report(request_data)
    assert report["trajectory"]["heat_to_bath_j"][-1] < 0
    assert report["trajectory"]["temperature_k"][-1] > 300
    assert report["analytic_reference"]["status"] == "passed"


def test_species_permutation_preserves_physics(request_data):
    first = tc.simulate(request_data)["trajectory"]
    request_data["species_order"].reverse()
    request_data["initial_concentration_mol_m3"].reverse()
    second = tc.simulate(request_data)["trajectory"]
    assert second["concentration_mol_m3"] == [row[::-1] for row in first["concentration_mol_m3"]]
    assert second["temperature_k"] == first["temperature_k"]
    assert second["heat_to_bath_j"] == first["heat_to_bath_j"]


def test_volume_scaling_extensive_vs_intensive(request_data):
    first = tc.simulate(request_data)["trajectory"]
    request_data["volume_m3"] *= 10
    second = tc.simulate(request_data)["trajectory"]
    for x, y in zip(first["temperature_k"], second["temperature_k"]):
        assert x == pytest.approx(y, abs=2e-6)
    for a, b in zip(first["concentration_mol_m3"], second["concentration_mol_m3"]):
        assert a == pytest.approx(b, abs=2e-8)
    for a, b in zip(first["heat_to_bath_j"], second["heat_to_bath_j"]):
        assert 10*a == pytest.approx(b, abs=2e-5)


def test_arrhenius_reference_point_and_temperature_response(request_data):
    p = tc.validate_request(request_data)
    assert tc._rate(p, 300) == 0.1
    assert tc._rate(p, 310) > tc._rate(p, 300) > tc._rate(p, 290)
    expected = 0.1 * math.exp(20000/8.31446261815324 * (1/300 - 1/310))
    assert tc._rate(p, 310) == expected


def test_wrong_rate_can_conserve_but_fails_accuracy_check(request_data):
    request_data["activation_energy_j_mol"] = 0
    wrong = deepcopy(request_data)
    wrong["reference_rate_s_inv"] *= 2
    wrong_trajectory = tc.simulate(wrong)["trajectory"]
    tc.check_balances(request_data, wrong_trajectory)
    comparison = tc.compare_trajectories(request_data, wrong_trajectory, tc.constant_rate_reference(request_data))
    assert not comparison["within_policy"]


@pytest.mark.parametrize("mutation", ["temperature", "heat", "mass", "initial", "negative", "order", "grid_bool", "missing_row"])
def test_corrupted_trajectory_refused(request_data, mutation):
    trajectory = tc.simulate(request_data)["trajectory"]
    if mutation == "temperature": trajectory["temperature_k"][-1] += 1
    elif mutation == "heat": trajectory["heat_to_bath_j"][-1] += 1
    elif mutation == "mass": trajectory["concentration_mol_m3"][-1][0] += 0.1
    elif mutation == "initial": trajectory["temperature_k"][0] += 1
    elif mutation == "negative": trajectory["concentration_mol_m3"][-1][0] = -1e-16
    elif mutation == "order": trajectory["species_order"].reverse()
    elif mutation == "grid_bool": trajectory["time_s"][0] = False
    else: trajectory["temperature_k"].pop()
    with pytest.raises(tc.Refusal):
        tc.check_balances(request_data, trajectory)


def test_exhausted_global_budget_refused(request_data):
    request_data["solver"]["max_steps"] = 1
    with pytest.raises(tc.Refusal, match="budget"):
        tc.simulate(request_data)


def test_inadmissible_trial_is_not_clipped(request_data):
    p = tc.validate_request(request_data)
    with pytest.raises(tc._TrialRejected):
        tc._rk4(p, tc._initial(p), 100.0)
    with pytest.raises(tc._TrialRejected):
        tc._rhs(p, (-1e-20, 2.25, 300.0, 0.0))


def test_step_counts_and_sample_grid(request_data):
    result = tc.simulate(request_data)
    stats = result["solver"]
    assert stats["attempted_steps"] == stats["accepted_steps"] + stats["rejected_steps"]
    assert stats["attempted_steps"] <= request_data["solver"]["max_steps"]
    assert result["trajectory"]["time_s"] == request_data["time_s"]


def test_fourth_order_convergence(request_data):
    request_data.update(activation_energy_j_mol=0, reference_rate_s_inv=0.5, time_s=[0, 2])
    p = tc.validate_request(request_data)
    exact = tc.constant_rate_reference(p)["concentration_mol_m3"][-1][0]
    errors = []
    for steps in (10, 20, 40):
        state = tc._initial(p)
        for _ in range(steps):
            state = tc._rk4(p, state, 2/steps)
        errors.append(abs(state[0] - exact))
    assert 12 < errors[0]/errors[1] < 20
    assert 12 < errors[1]/errors[2] < 20


def test_no_false_analytic_qualification(request_data):
    report = tc.make_report(request_data)
    assert report["analytic_reference"]["status"] == "not_applicable"
    assert not any(report["authority"][key] for key in (
        "physical_validation", "native_provider_qualified", "state_admission", "hardware_actuation"))
    with pytest.raises(tc.Refusal, match="not applicable"):
        tc.constant_rate_reference(request_data)


def test_content_identity_separate_from_execution(request_data):
    first, second = tc.make_report(request_data), tc.make_report(request_data)
    assert first["content_sha256"] == second["content_sha256"]
    assert first["execution_id"] != second["execution_id"]
    body = {k: v for k, v in first.items() if k not in ("content_sha256", "execution_id")}
    assert hashlib.sha256(tc._canonical(body)).hexdigest() == first["content_sha256"]
    assert hashlib.sha256(Path(tc.__file__).read_bytes()).hexdigest() == first["kernel_source_sha256"]
    request_data["activation_energy_j_mol"] = 0
    assert tc.make_report(request_data)["content_sha256"] != first["content_sha256"]


def test_cli_success_and_no_overwrite(tmp_path):
    output = tmp_path / "result.json"
    command = [sys.executable, "-m", "ciw.thermochemical_benchmark", str(EXAMPLE), "--output", str(output)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    before = output.read_bytes()
    report = json.loads(before)
    assert report["request_bytes_sha256"] == hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    again = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert again.returncode == 2
    assert output.read_bytes() == before


def test_cli_refusal_leaves_no_result(tmp_path):
    request = tmp_path / "bad.json"
    request.write_text('{"x": 1, "x": 2}', encoding="utf-8")
    output = tmp_path / "result.json"
    run = subprocess.run([sys.executable, "-m", "ciw.thermochemical_benchmark", str(request), "--output", str(output)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 2 and "Duplicate" in run.stderr
    assert not output.exists()


@pytest.mark.parametrize("method", ["DOP853", "Radau"])
@pytest.mark.parametrize("seed", range(12))
def test_nonlinear_against_independent_scipy_rhs(request_data, seed, method):
    # This optional test is not evidence of Cantera/Catalyst qualification.
    scipy_integrate = pytest.importorskip("scipy.integrate")
    rng = random.Random(seed)
    p = deepcopy(request_data)
    p.update(initial_temperature_k=rng.uniform(290, 320), bath_temperature_k=rng.uniform(280, 330),
             activation_energy_j_mol=rng.uniform(5000, 40000),
             reaction_internal_energy_j_mol=rng.uniform(-30000, 30000),
             reference_rate_s_inv=rng.uniform(0.03, 0.3),
             heat_transfer_w_m3_k=rng.uniform(0, 200))
    result = tc.simulate(p)["trajectory"]
    # Independent RHS: do not call the benchmark's _rhs or _rate helpers.
    def rhs(_t, state):
        a, _b, change, _heat = state
        temp = p["initial_temperature_k"] + change
        k = p["reference_rate_s_inv"] * math.exp(
            p["activation_energy_j_mol"] / 8.31446261815324 *
            (temp - p["reference_temperature_k"]) / (temp * p["reference_temperature_k"]))
        production = k*a
        heat_flux = p["heat_transfer_w_m3_k"] * ((p["initial_temperature_k"]-p["bath_temperature_k"]) + change)
        return [-production, production,
                (-production*p["reaction_internal_energy_j_mol"]-heat_flux)/p["heat_capacity_j_m3_k"],
                p["volume_m3"]*heat_flux]
    oracle = scipy_integrate.solve_ivp(rhs, [0, p["time_s"][-1]],
        [*p["initial_concentration_mol_m3"], 0, 0],
        t_eval=p["time_s"], method=method, rtol=1e-12, atol=[1e-13, 1e-13, 1e-11, 1e-11])
    assert oracle.success and oracle.t[-1] == p["time_s"][-1]
    expected = {"species_order": ["A", "B"], "time_s": list(p["time_s"]),
                "concentration_mol_m3": oracle.y[:2].T.tolist(),
                "temperature_k": (p["initial_temperature_k"] + oracle.y[2]).tolist(),
                "temperature_change_k": oracle.y[2].tolist(), "heat_to_bath_j": oracle.y[3].tolist()}
    comparison = tc.compare_trajectories(p, result, expected)
    assert comparison["within_policy"], comparison


@pytest.mark.parametrize("heat_capacity", [1000, 1e6, 1e9])
@pytest.mark.parametrize("volume", [1e-6, 0.01, 1])
@pytest.mark.parametrize("activation", [0, 20000])
def test_large_heat_capacity_no_cancellation(request_data, heat_capacity, volume, activation):
    request_data.update(heat_capacity_j_m3_k=heat_capacity, volume_m3=volume,
                        activation_energy_j_mol=activation)
    report = tc.make_report(request_data)
    assert report["balance_metrics"]["energy_balance_max_abs_j"] < 1e-6


def test_atomic_publication_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    def fail_link(*_args):
        raise OSError("Injected publication failure")
    monkeypatch.setattr(tc.os, "link", fail_link)
    with pytest.raises(OSError, match="Injected"):
        tc._write_new_report(output, "complete report\n")
    assert not output.exists()
    assert not list(tmp_path.glob(".thermochemical-*"))


def test_parser_giant_integer_refused():
    with pytest.raises(tc.Refusal):
        tc.load_request(b'{"x":' + b'9'*5000 + b'}')


def test_bad_json_parameter_ids_are_bounded_for_windows_environment():
    # Pytest exports the node ID as PYTEST_CURRENT_TEST. The oversized payload
    # must remain the test input, never become a 65 KiB environment value.
    mark = next(m for m in test_bad_json_refused.pytestmark if m.name == "parametrize")
    assert len(mark.kwargs["ids"]) == len(mark.args[1])
    assert all(type(value) is str and 0 < len(value) <= 64 for value in mark.kwargs["ids"])
