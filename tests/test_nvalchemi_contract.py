"""Provider-free mathematics/contract tests, NOT native ALCHEMI qualification."""
from copy import deepcopy
import math

import pytest

from ciw import nvalchemi_contract as c


def prediction(state, values=None):
    return {
        "schema": "ciw.atomic-lj-prediction.v1", "prediction_id": "prediction-" + "1" * 32,
        "input_sha256": c.digest(state), "atom_ids": deepcopy(state["atom_ids"]),
        "model": deepcopy(c.MODEL), "units": deepcopy(c.UNITS),
        "values": c.reference(state) if values is None else values,
        "uncertainty": {"status": "unavailable", "covariance": None},
        "execution_device": {"device": "cpu", "dtype": "float64", "torch_version": "transport-double",
                             "cuda_runtime": None, "gpu_name": None, "compute_capability": None, "driver_version": None},
        "environment_sha256": c.digest("transport-double-not-native"),
    }


def payload(state=None, values=None, run=None):
    state = c.example_input() if state is None else state
    run = {"evidence_id": "synthetic-session-carrier"} if run is None else run
    data = prediction(state, values)
    request = c.canonical({"schema": "ciw.adapter-request.v1", "operation_id": c.OPERATION,
                           "inputs": {"configuration": state, "device": "cpu",
                                      "environment_sha256": data["environment_sha256"]}})
    response = c.canonical({"schema": "ciw.adapter-response.v1", "status": "ok", "data": data})
    return c.make_payload(run, state, "cpu", data, request, response)


def cluster():
    result = c.example_input()
    result["atom_ids"] = ["a", "b", "c", "d"]
    result["atomic_numbers"] = [18] * 4
    result["positions"] = [[0., 0., 0.], [4., 0., 0.], [0., 4., 0.], [0., 0., 4.]]
    return c.validate_input(result)


def test_known_pair_limits_and_force_signs():
    at_sigma = c.reference(c.example_input(c.MODEL["sigma_angstrom"]))
    assert at_sigma["energy_eV"] == pytest.approx(0., abs=1e-14)
    assert at_sigma["forces_eV_per_angstrom"][0][0] < 0.  # repulsive
    rmin = 2 ** (1 / 6) * c.MODEL["sigma_angstrom"]
    at_min = c.reference(c.example_input(rmin))
    assert at_min["energy_eV"] == pytest.approx(-c.MODEL["epsilon_eV"], abs=1e-14)
    assert abs(at_min["forces_eV_per_angstrom"][0][0]) < 1e-14
    attractive = c.reference(c.example_input(5.))
    assert attractive["forces_eV_per_angstrom"][0][0] > 0.
    far = c.reference(c.example_input(9.))
    assert far == {"energy_eV": 0., "forces_eV_per_angstrom": [[0., 0., 0.], [0., 0., 0.]]}


@pytest.mark.parametrize("state", [c.example_input(3.0), c.example_input(4.0), cluster()])
def test_scalar_forces_are_negative_energy_gradients(state):
    expected = c.reference(state)
    h = 1e-5
    for i in range(len(state["atom_ids"])):
        for k in range(3):
            plus, minus = deepcopy(state), deepcopy(state)
            plus["positions"][i][k] += h
            minus["positions"][i][k] -= h
            derivative = -(c.reference(plus)["energy_eV"] - c.reference(minus)["energy_eV"]) / (2 * h)
            assert derivative == pytest.approx(expected["forces_eV_per_angstrom"][i][k], rel=2e-8, abs=1e-9)


def test_translation_rotation_permutation_and_newton_balance():
    state = cluster()
    original = c.reference(state)
    translated = deepcopy(state)
    translated["positions"] = [[x + 3., y - 7., z + 2.] for x, y, z in state["positions"]]
    assert c.reference(translated) == original
    rotated = deepcopy(state)
    rotated["positions"] = [[-y, x, z] for x, y, z in state["positions"]]
    result = c.reference(rotated)
    assert result["energy_eV"] == original["energy_eV"]
    for actual, (x, y, z) in zip(result["forces_eV_per_angstrom"], original["forces_eV_per_angstrom"]):
        assert actual == pytest.approx([-y, x, z])
    permuted = deepcopy(state)
    for key in ("atom_ids", "atomic_numbers", "positions"):
        permuted[key].reverse()
    result = c.reference(permuted)
    assert result["energy_eV"] == original["energy_eV"]
    for actual, expected in zip(result["forces_eV_per_angstrom"], reversed(original["forces_eV_per_angstrom"])):
        assert actual == pytest.approx(expected)
    for k in range(3):
        assert abs(math.fsum(row[k] for row in original["forces_eV_per_angstrom"])) < 1e-14


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("origin", "measured"), ("frame_id", ""),
    ("configuration_id", "../a"), ("atom_ids", ["a", "a"]),
    ("atomic_numbers", [18, 6]), ("atomic_numbers", [18, True]),
    ("positions", [[0., 0., 0.], [math.nan, 0., 0.]]),
    ("positions", [[0., 0., 0.], [math.inf, 0., 0.]]),
    ("positions", [[0., 0., 0.], [True, 0., 0.]]),
    ("positions", [[0., 0., 0.], [1., 0., 0.]]),
    ("positions", [[0., 0., 0.], [8.5, 0., 0.]]),
    ("positions", [[0., 0., 0.], [101., 0., 0.]]),
    ("positions", [[0., 0.], [4., 0., 0.]]), ("positions", []),
    ("pbc", [0, 0, 0]), ("pbc", [True, False, False]),
    ("cell", [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]),
    ("units", {"position": "nm", "energy": "eV", "force": "eV/angstrom"}),
])
def test_input_refusals(field, value):
    state = c.example_input()
    state[field] = value
    with pytest.raises(ValueError):
        c.validate_input(state)


def test_unknown_fields_counts_and_detachment():
    state = c.example_input()
    copied = c.validate_input(state)
    state["positions"][1][0] = 7.
    assert copied["positions"][1][0] == 4.
    state["download_model"] = True
    with pytest.raises(ValueError):
        c.validate_input(state)
    state = c.example_input()
    state["atom_ids"] = [str(i) for i in range(33)]
    with pytest.raises(ValueError):
        c.validate_input(state)
    state = c.example_input()
    state["atom_ids"].append("third")
    with pytest.raises(ValueError):
        c.validate_input(state)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'NaN', b'Infinity', b'1e999', b'1e-999', b'\xff', b'', b'{' * 2000])
def test_json_refusals(raw):
    with pytest.raises(ValueError):
        c.loads(raw)


def test_json_and_blob_roundtrip_budget_and_tamper():
    raw = c.canonical(c.example_input())
    assert c.loads(c.unblob(c.blob(raw))) == c.example_input()
    with pytest.raises(ValueError):
        c.loads(b' ' * (c.MAX_BYTES + 1))
    record = c.blob(raw)
    record["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        c.unblob(record)
    with pytest.raises(ValueError):
        c.unblob({"sha256": "x", "bytes_b64": "%%%"})


def test_numerical_mismatch_is_retained_as_failed_not_physically_verified():
    state = c.example_input()
    values = c.reference(state)
    values["energy_eV"] += .1
    record = payload(state, values)
    assert record["numerical_check"]["status"] == "fail"
    assert record["physical_validation"] == "not_performed"
    assert record["authorizes_execution"] is False
    assert record["prediction"]["uncertainty"] == {"status": "unavailable", "covariance": None}


@pytest.mark.parametrize("field,value", [
    ("energy_eV", True), ("energy_eV", math.inf),
    ("forces_eV_per_angstrom", []),
    ("forces_eV_per_angstrom", [[0., 0., 0.], [0., math.nan, 0.]]),
])
def test_invalid_provider_numbers_refuse(field, value):
    values = c.reference(c.example_input())
    values[field] = value
    with pytest.raises(ValueError):
        payload(values=values)


@pytest.mark.parametrize("mutation", ["context", "input", "atom_order", "model", "units", "uncertainty",
                                      "device", "precision", "prediction_id", "check_id", "status", "policy",
                                      "reference", "transport", "environment", "authority", "physical"])
def test_offline_resealed_semantic_mutations_fail(mutation):
    state = c.example_input()
    record = payload(state)
    if mutation == "context": record["context_evidence_id"] = "another"
    elif mutation == "input": record["atomic_input"]["frame_id"] = "another"
    elif mutation == "atom_order": record["prediction"]["atom_ids"].reverse()
    elif mutation == "model": record["prediction"]["model"]["epsilon_eV"] = .1
    elif mutation == "units": record["prediction"]["units"]["energy"] = "J"
    elif mutation == "uncertainty": record["prediction"]["uncertainty"]["covariance"] = 0
    elif mutation == "device": record["prediction"]["execution_device"]["device"] = "cuda:0"
    elif mutation == "precision": record["prediction"]["execution_device"]["dtype"] = "float32"
    elif mutation == "prediction_id": record["prediction"]["prediction_id"] = "same-as-check"
    elif mutation == "check_id": record["numerical_check_id"] = "same-as-prediction"
    elif mutation == "status": record["numerical_check"]["status"] = "fail"
    elif mutation == "policy": record["numerical_check"]["policy"]["rtol"] = math.nextafter(c.POLICY["rtol"], math.inf)
    elif mutation == "reference": record["numerical_check"]["reference"]["energy_eV"] += .001
    elif mutation == "transport": record["transport"]["response"] = c.blob(b'{}')
    elif mutation == "environment": record["prediction"]["environment_sha256"] = c.digest("other")
    elif mutation == "authority": record["authorizes_execution"] = True
    elif mutation == "physical": record["physical_validation"] = "pass"
    with pytest.raises(ValueError):
        c.validate_payload(c.OPERATION, record, {"evidence_id": "synthetic-session-carrier"}, state, {})


def test_offline_roundoff_tolerance_is_not_a_policy_or_status_tolerance():
    state = c.example_input()
    record = payload(state)
    value = record["numerical_check"]["reference"]["energy_eV"]
    record["numerical_check"]["reference"]["energy_eV"] = math.nextafter(value, math.inf)
    c.validate_payload(c.OPERATION, record, {"evidence_id": "synthetic-session-carrier"}, state, {})


def test_reference_error_never_promotes_native_or_physical_claims():
    state = c.example_input()
    record = payload(state)
    assert record["numerical_check"]["status"] == "pass"
    assert "same_declared_LJ_equations" in record["numerical_check"]["scope"]
    assert record["numerical_check"]["physical_validation"] == "not_performed"
    assert c.loads(c.unblob(record["transport"]["response"]))["data"] == record["prediction"]


def runtime_fixture():
    deps = {name: "transport-double" for name in ("nvalchemi-toolkit", "nvalchemi-toolkit-ops", "torch", "warp-lang", "numpy", "pydantic")}
    deps.update(toolkit_revision=c.TOOLKIT_REVISION, toolkit_files_sha256=c.digest("fixture-files"),
                ops_files_sha256=c.digest("fixture-ops"), distribution_inventory_sha256=c.digest("fixture-inventory"))
    return {
        "schema": "ciw.subprocess-runtime.v1", "adapter_version": "ciw-pinned-subprocess-v1",
        "repository_root": "/transport-double-not-native", "revision": "1" * 40, "source_tree": "2" * 40,
        "module": "ciw.nvalchemi_worker", "source_root": "src", "python_executable": "/transport-double-python",
        "python_sha256": "3" * 64, "python_version": "transport-double", "dependencies": deps,
        "alchemi_profile": c.OPERATION, "device_binding": "cpu", "precision": "float64",
        "dependency_binary_attestation": "not_established",
    }


def bound_payload(state=None, run=None):
    state = c.example_input() if state is None else state
    run = {"evidence_id": "synthetic-session-carrier"} if run is None else run
    runtime = runtime_fixture()
    data = prediction(state)
    data["environment_sha256"] = c.digest({"python_version": runtime["python_version"], "dependencies": runtime["dependencies"]})
    request = c.canonical({"schema": "ciw.adapter-request.v1", "operation_id": c.OPERATION,
                           "inputs": {"configuration": state, "device": "cpu", "environment_sha256": data["environment_sha256"]}})
    response = c.canonical({"schema": "ciw.adapter-response.v1", "status": "ok", "data": data})
    return c.make_payload(run, state, "cpu", data, request, response), runtime


@pytest.mark.parametrize("field", [None, "device_binding", "revision", "module", "python_sha256", "dependencies", "binary_claim"])
def test_outer_runtime_is_bound_to_prediction(field):
    record, runtime = bound_payload()
    if field is None:
        c.validate_runtime_binding(record, runtime)
        return
    if field == "dependencies": runtime["dependencies"]["torch"] = "another-version"
    elif field == "binary_claim": runtime["dependency_binary_attestation"] = "verified"
    else: runtime[field] = "wrong"
    with pytest.raises(ValueError):
        c.validate_runtime_binding(record, runtime)
