"""Third-party numerical comparisons; optional locally, required in its CI job."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("scipy", reason="Install .[numerical-checks] to run the external numerical comparison suite")

from ciw.fluid_reservoir_solver import simulate
from ciw.polymer_models import engineering_estimate


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_external_numerics.py"
spec = importlib.util.spec_from_file_location("external_numerics", SCRIPT)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


@pytest.fixture(scope="module")
def reservoir():
    request = comparison.reservoir_cases()["coupled_damped"]
    return request, simulate(request)


@pytest.fixture(scope="module")
def cooling():
    request = comparison.cooling_cases()["biot_0.08_elapsed_1_tau"]
    return request, engineering_estimate(request)


def test_full_comparison_passes_with_versions_and_explicit_limitations():
    report = comparison.run_comparisons()
    assert report["status"] == "PASS"
    assert len(report["reservoir"]) == 4
    assert len(report["polymer_cooling"]) == 9
    assert report["versions"]["scipy"]
    assert report["versions"]["numpy"]
    assert "not_physical_validation" in report["scope"]
    assert "no independent external publication" in report["limitations"][0]
    assert all(len(value) == 64 for value in report["source_sha256"].values())
    json.dumps(report, allow_nan=False)


def test_wrong_dynamics_fails_even_when_energy_balance_is_preserved(reservoir):
    request, _ = reservoir
    wrong_request = deepcopy(request)
    wrong_request["structure"]["stiffness_n_per_m"] *= 1.1
    report = comparison.compare_reservoir(request, simulate(wrong_request))
    assert report["status"] == "FAIL"
    assert report["checks"]["discrete_balances"]
    assert not report["checks"]["finer_state"]


@pytest.mark.parametrize("field", ["fluid_energy_j", "fluid_dissipation_j", "structure_interface_work_j"])
def test_altered_energy_and_work_numbers_fail(reservoir, field):
    request, result = reservoir
    corrupt = deepcopy(result)
    corrupt["resolutions"]["finer"]["trace"][field][-1] += 0.001
    report = comparison.compare_reservoir(request, corrupt)
    assert report["status"] == "FAIL"
    assert not report["checks"]["finer_energy_and_work"]


@pytest.mark.parametrize("field", ["temperature_k", "remaining_time_s"])
def test_altered_cooling_numbers_fail(cooling, field):
    request, result = cooling
    corrupt = deepcopy(result)
    destination = corrupt["cooling"] if field == "temperature_k" else corrupt["cooling"]["target"]
    destination[field] += 1.0
    report = comparison.compare_cooling(request, corrupt)
    assert report["status"] == "FAIL"
    assert not report["checks"]["ode_temperature_k" if field == "temperature_k" else "ode_target_time_s"]


def test_slab_discrepancy_is_distinct_from_arithmetic_error(cooling):
    request, result = cooling
    report = comparison.compare_cooling(request, result)
    assert report["errors"]["ode_temperature_k"] < 1e-8
    assert 0.005 < report["errors"]["slab_temperature_initial_gap_fraction"] < 0.01
    assert 0.02 < report["errors"]["slab_target_time_relative"] < 0.03
    assert report["temperature_k"]["convective_slab_volume_average"] > result["cooling"]["temperature_k"]


def test_slab_solution_approaches_lumped_limit_as_biot_decreases():
    differences = [abs(comparison.slab_average_ratio(biot, 1.0 / biot) - math.exp(-1.0))
                   for biot in (0.08, 0.02, 0.001)]
    assert differences[0] > differences[1] > differences[2] > 0
    assert differences[-1] < 1.3e-4


def test_cli_writes_failure_report_and_returns_nonzero(tmp_path, monkeypatch):
    def failed_comparison():
        raise RuntimeError("numerical comparator failed")

    monkeypatch.setattr(comparison, "run_comparisons", failed_comparison)
    destination = tmp_path / "comparison.json"
    assert comparison.main(["--output", str(destination)]) == 1
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert "numerical comparator failed" in report["error"]


def test_nonfinite_cooling_number_cannot_pass(cooling):
    request, result = cooling
    corrupt = deepcopy(result)
    corrupt["cooling"]["temperature_k"] = float("nan")
    assert comparison.compare_cooling(request, corrupt)["status"] == "FAIL"
