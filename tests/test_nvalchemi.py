"""Offline contract tests are distinct from the opt-in real provider gate."""
from copy import deepcopy
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ciw.adapters.nvalchemi_contract import (MODEL, OPERATION, UNITS, checked_input,
                                           configuration_id, example_input,
                                           validate_input, validate_output)
from ciw.adapters.protocol import AdapterRefusal

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_nvalchemi", ROOT / "scripts/check_nvalchemi.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def synthetic_result(request):
    energy, forces = gate.reference(request)
    return {"schema": "ciw.nvalchemi-lj-result.v1", "configuration_id": configuration_id(request),
            "atom_ids": list(request["atom_ids"]), "energy": energy, "forces": forces,
            "units": deepcopy(UNITS), "model": deepcopy(MODEL), "interpretation": "model_prediction",
            "energy_reference": "unshifted-pair-sum-zero-at-infinite-separation",
            "uncertainty": {"status": "unavailable"}}


def test_reference_known_pair_values_and_sign():
    request = example_input()
    request["positions"][1][0] = MODEL["sigma_angstrom"]
    energy, forces = gate.reference(request)
    assert energy == 0
    assert forces[0][0] == pytest.approx(-24 * MODEL["epsilon_ev"] / MODEL["sigma_angstrom"])
    assert forces[0][0] == -forces[1][0]
    request["positions"][1][0] *= 2 ** (1 / 6)
    energy, forces = gate.reference(request)
    assert energy == pytest.approx(-MODEL["epsilon_ev"])
    assert abs(forces[0][0]) < 1e-15


def test_input_detaches_and_roundtrips_without_execution():
    request = example_input()
    saved = deepcopy(request)
    copy = checked_input(request)
    copy["positions"][0][0] = 1
    assert request == saved
    result = synthetic_result(request)
    validate_output(json.loads(json.dumps(result)), request)
    assert configuration_id(json.loads(json.dumps(request))) == configuration_id(request)
    assert "execution_id" not in request
    assert "verification_id" not in result


@pytest.mark.parametrize("key,value", [
    ("schema", "unknown"), ("atom_ids", ["same", "same"]), ("atom_ids", []),
    ("atom_ids", ["x"]), ("atomic_numbers", [True, 18]), ("atomic_numbers", [1, 1]),
    ("positions", [[0, 0], [4.25, 0, 0]]), ("positions", [[0, 0, 0], [math.nan, 0, 0]]),
    ("positions", [[0, 0, 0], [math.inf, 0, 0]]), ("positions", [[0, 0, 0], [True, 0, 0]]),
    ("positions", [[0, 0, 0], [0, 0, 0]]), ("positions", [[1001, 0, 0], [4.25, 0, 0]]),
    ("units", {"positions": "nm", "energy": "eV", "forces": "eV/angstrom"}),
    ("pbc", [1, 0, 0]), ("pbc", [True, False, False]), ("pbc", [False]),
    ("cell", [[0, 0, 0]] * 3), ("cell", [[10, 0, 0], [0, 10, 0], [0, 0, 10]]),
    ("charge", 1), ("charge", False), ("spin", 0), ("coordinate_frame", ""),
    ("model", {**MODEL, "sigma_angstrom": 1}), ("model", {**MODEL, "half_list": 0}),
    ("model", {**MODEL, "switch_width_angstrom": False}),
])
def test_input_refusals(key, value):
    request = example_input()
    request[key] = value
    with pytest.raises(AdapterRefusal, match=".+") as raised:
        checked_input(request)
    assert raised.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("key,value", [
    ("configuration_id", "sha256:" + "0" * 64), ("atom_ids", ["atom-b", "atom-a"]),
    ("energy", math.nan), ("energy", True), ("forces", [[0, 0, 0]]),
    ("forces", [[0, 0, 0], [0, math.inf, 0]]), ("interpretation", "measurement"),
    ("uncertainty", {"status": "available", "sigma": 0}),
    ("energy_reference", "unknown"), ("units", {}), ("schema", "unknown"),
])
def test_output_refusals(key, value):
    request = example_input()
    result = synthetic_result(request)
    result[key] = value
    with pytest.raises(ValueError):
        validate_output(result, request)


def test_mutated_numerics_fail_independent_check():
    request = example_input()
    result = synthetic_result(request)
    result["forces"][0][0] *= -1
    validate_output(result, request)  # Shape validity is not numerical agreement.
    assert not gate.numerical_check(request, result)["passed"]


def test_reference_permutation_rotation_translation():
    cases = gate.cases()
    energy, forces = gate.reference(cases["three_atom"])
    translated_e, translated_f = gate.reference(cases["translated"])
    rotated_e, rotated_f = gate.reference(cases["rotated"])
    permuted_e, permuted_f = gate.reference(cases["permuted"])
    assert translated_e == rotated_e == pytest.approx(energy)
    assert permuted_e == pytest.approx(energy)
    assert translated_f == forces
    assert rotated_f == [[-y, x, z] for x, y, z in forces]
    assert permuted_f == [forces[i] for i in (2, 0, 1)]


def test_worker_refuses_before_vendor_import():
    request = example_input()
    request["charge"] = 1.0
    envelope = {"schema": "ciw.adapter-request.v1", "operation_id": OPERATION, "inputs": request}
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run([sys.executable, "-B", "-m", "ciw.adapters.nvalchemi_worker"],
                            input=json.dumps(envelope), text=True, capture_output=True,
                            env=environment, cwd=ROOT, timeout=20)
    response = json.loads(result.stdout)
    assert result.returncode == 0
    assert response["status"] == "refused"
    assert response["refusal"]["code"] == "INVALID_INPUT"
    assert "data" not in response


def test_unknown_input_fields_do_not_select_an_executable():
    request = example_input()
    request["module"] = "arbitrary.provider"
    with pytest.raises(ValueError):
        validate_input(request)


def test_refused_gate_retains_attempts_and_never_claims_provider_success():
    class Unavailable:
        def runtime_identity(self):
            return {"test_fixture": "unavailable_provider"}

        def evaluate(self, request):
            checked_input(request)
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "Synthetic missing runtime")

    report = gate.qualify(Unavailable())
    assert report["status"] == "FAIL"
    assert report["provider_execution"] == "no_successful_execution"
    assert len(report["cases"]) == len(gate.cases())
    for row in report["cases"].values():
        assert row["status"] == "refused"
        assert row["result"] is None and row["result_id"] is None
        assert row["refusal"]["code"] == "RUNTIME_UNAVAILABLE"
    assert report["finite_difference_force_error"] is None
    assert report["replay_max_error"] is None


@pytest.mark.integration
def test_real_pinned_nvalchemi_cpu():
    executable = os.environ.get("CIW_NVALCHEMI_PYTHON")
    revision = os.environ.get("CIW_NVALCHEMI_REVISION")
    if not executable or not revision:
        pytest.skip("Requires explicitly bound ALCHEMI CPU environment and clean NET source revision")
    from ciw.adapters.nvalchemi import NvalchemiLJBinding
    binding = NvalchemiLJBinding(ROOT, revision, python_executable=executable)
    report = gate.qualify(binding)
    assert report["status"] == "PASS"
    assert report["provider_execution"] == "performed"
    assert report["physical_validation"] == "not_performed"
