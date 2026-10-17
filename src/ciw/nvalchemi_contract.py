"""Provider-free contract and independent scalar reference for the ALCHEMI pilot.

This is a synthetic, single-species, non-periodic Lennard-Jones profile, not a
polymer force field. No third-party numerical runtime is imported by this file.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import math
import re
import uuid

OPERATION = "atomic.lj-energy-forces.v1"
INPUT_SCHEMA = "ciw.atomic-lj-input.v1"
OUTPUT_SCHEMA = "ciw.atomic-lj-result.v1"
TOOLKIT_REVISION = "bf605f9191b2978fc865f4286d11e4481b679999"
MAX_ATOMS = 32
MAX_BYTES = 1024 * 1024
MODEL = {
    "name": "LennardJonesModelWrapper", "epsilon_eV": 0.0104,
    "sigma_angstrom": 3.4, "cutoff_angstrom": 8.5,
    "switch_width_angstrom": 0.0, "half_list": False,
    "energy_reference": "unshifted_pair_potential_zero_at_infinite_separation",
    "applicability": "synthetic_single_species_fixture_only",
    "checkpoint": None,
}
UNITS = {"position": "angstrom", "energy": "eV", "force": "eV/angstrom"}
POLICY = {
    "id": "ciw.lj-reference-check.v1", "precision": "float64",
    "energy_atol_eV": 1e-8, "force_atol_eV_per_angstrom": 1e-8,
    "rtol": 1e-6, "qualification": "provisional_not_hardware_qualified",
}


def keys(value: object, expected: set[str]) -> None:
    if type(value) is not dict or set(value) != expected:
        raise ValueError("Unexpected or missing ALCHEMI contract fields")


def number(value: object, limit: float = 1e100) -> float:
    if type(value) not in (int, float) or abs(value) > limit or not math.isfinite(value):
        raise ValueError("Expected a finite, bounded number (not a boolean)")
    return float(value)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value: object) -> str:
    return "sha256:" + sha256(canonical(value)).hexdigest()


def loads(raw: bytes) -> object:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError("ALCHEMI JSON byte limit exceeded")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("Non-finite JSON constant")
    def floating(text):
        value = float(text)
        if not math.isfinite(value) or (value == 0.0 and Decimal(text) != 0):
            raise ValueError("JSON float overflows or underflows")
        return value
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant, parse_float=floating)
        canonical(value)  # also rejects exponent overflow to infinity
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Malformed ALCHEMI JSON") from exc


def _identity(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value) is None:
        raise ValueError("Expected a bounded, explicit scientific identity")


def validate_input(value: dict) -> dict:
    keys(value, {"schema", "configuration_id", "origin", "frame_id", "atom_ids",
                 "atomic_numbers", "positions", "units", "cell", "pbc"})
    if value["schema"] != INPUT_SCHEMA or value["origin"] != "synthetic":
        raise ValueError("Only the declared synthetic LJ input profile is supported")
    _identity(value["configuration_id"])
    _identity(value["frame_id"])
    for field in ("atom_ids", "atomic_numbers", "positions"):
        if type(value[field]) is not list or not 2 <= len(value[field]) <= MAX_ATOMS:
            raise ValueError("The LJ pilot requires 2--32 atoms")
    count = len(value["atom_ids"])
    if len(value["positions"]) != count or len(value["atomic_numbers"]) != count:
        raise ValueError("Atom arrays differ in length")
    for atom_id in value["atom_ids"]:
        _identity(atom_id)
    if len(set(value["atom_ids"])) != count:
        raise ValueError("Atom identities must be unique")
    if any(type(z) is not int or z != 18 for z in value["atomic_numbers"]):
        raise ValueError("Only the synthetic Z=18 single-species fixture is supported")
    if value["units"] != UNITS or type(value["units"]) is not dict:
        raise ValueError("Explicit angstrom/eV/eV-per-angstrom units are required")
    if (value["cell"] is not None or type(value["pbc"]) is not list
            or len(value["pbc"]) != 3 or any(flag is not False for flag in value["pbc"])):
        raise ValueError("Periodic cells and boundary conditions are not qualified by this profile")
    for row in value["positions"]:
        if type(row) is not list or len(row) != 3:
            raise ValueError("Positions must be an N by 3 array")
        for x in row:
            number(x, 100.0)
    for i, a in enumerate(value["positions"]):
        for b in value["positions"][i + 1:]:
            r = math.dist(a, b)
            if r < 0.75 * MODEL["sigma_angstrom"]:
                raise ValueError("Overlapping or too-close atoms are outside the fixture envelope")
            if abs(r - MODEL["cutoff_angstrom"]) < 1e-3:
                raise ValueError("Hard-cutoff discontinuity guard violated")
    if len(canonical(value)) > MAX_BYTES:
        raise ValueError("Input exceeds the byte budget")
    return deepcopy(value)


def example_input(distance: float = 4.0) -> dict:
    return validate_input({
        "schema": INPUT_SCHEMA, "configuration_id": "synthetic-lj-pair",
        "origin": "synthetic", "frame_id": "cartesian:fixture",
        "atom_ids": ["atom-0", "atom-1"], "atomic_numbers": [18, 18],
        "positions": [[0.0, 0.0, 0.0], [distance, 0.0, 0.0]],
        "units": deepcopy(UNITS), "cell": None, "pbc": [False, False, False],
    })


def reference(value: dict) -> dict:
    """All-pairs scalar implementation independent of torch/Warp/ALCHEMI."""
    state = validate_input(value)
    positions = state["positions"]
    terms = []
    forces = [[0.0, 0.0, 0.0] for _ in positions]
    for i, a in enumerate(positions):
        for j in range(i + 1, len(positions)):
            delta = [a[k] - positions[j][k] for k in range(3)]
            r2 = math.fsum(x * x for x in delta)
            if r2 >= MODEL["cutoff_angstrom"] ** 2:
                continue
            s6 = (MODEL["sigma_angstrom"] ** 2 / r2) ** 3
            terms.append(4.0 * MODEL["epsilon_eV"] * (s6 * s6 - s6))
            scale = 24.0 * MODEL["epsilon_eV"] * (2.0 * s6 * s6 - s6) / r2
            for k in range(3):
                f = scale * delta[k]
                forces[i][k] += f
                forces[j][k] -= f
    return {"energy_eV": math.fsum(terms), "forces_eV_per_angstrom": forces}


def validate_values(value: dict, count: int) -> None:
    keys(value, {"energy_eV", "forces_eV_per_angstrom"})
    number(value["energy_eV"])
    forces = value["forces_eV_per_angstrom"]
    if type(forces) is not list or len(forces) != count:
        raise ValueError("Returned forces do not match the atom count")
    for row in forces:
        if type(row) is not list or len(row) != 3:
            raise ValueError("Returned forces must have shape N by 3")
        for force in row:
            number(force)


def compare(value: dict, predicted: dict) -> dict:
    state = validate_input(value)
    validate_values(predicted, len(state["atom_ids"]))
    expected = reference(state)
    e_error = abs(predicted["energy_eV"] - expected["energy_eV"])
    force_errors = [abs(a - b) for p, q in zip(predicted["forces_eV_per_angstrom"],
                                              expected["forces_eV_per_angstrom"])
                    for a, b in zip(p, q)]
    force_ok = all(abs(a - b) <= POLICY["force_atol_eV_per_angstrom"] + POLICY["rtol"] * abs(b)
                   for p, q in zip(predicted["forces_eV_per_angstrom"], expected["forces_eV_per_angstrom"])
                   for a, b in zip(p, q))
    energy_ok = e_error <= POLICY["energy_atol_eV"] + POLICY["rtol"] * abs(expected["energy_eV"])
    return {
        "schema": "ciw.atomic-lj-reference-check.v1", "policy": deepcopy(POLICY),
        "status": "pass" if energy_ok and force_ok else "fail",
        "reference": expected, "energy_absolute_error_eV": e_error,
        "force_max_absolute_error_eV_per_angstrom": max(force_errors),
        "net_force_eV_per_angstrom": [math.fsum(row[k] for row in predicted["forces_eV_per_angstrom"])
                                        for k in range(3)],
        "scope": "independent_scalar_implementation_same_declared_LJ_equations",
        "physical_validation": "not_performed", "authorizes_execution": False,
    }


def blob(raw: bytes) -> dict:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError("Transport byte budget exceeded")
    return {"sha256": "sha256:" + sha256(raw).hexdigest(),
            "bytes_b64": base64.b64encode(raw).decode("ascii")}


def unblob(value: dict) -> bytes:
    keys(value, {"sha256", "bytes_b64"})
    if type(value["bytes_b64"]) is not str or len(value["bytes_b64"]) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("Transport encoding exceeds byte budget")
    try:
        raw = base64.b64decode(value["bytes_b64"], validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid transport encoding") from exc
    if blob(raw) != value:
        raise ValueError("Transport byte identity differs")
    return raw


def validate_worker_data(data: dict, state: dict, device: str) -> None:
    validate_input(state)
    keys(data, {"schema", "prediction_id", "input_sha256", "atom_ids", "model", "units",
                "values", "uncertainty", "execution_device", "environment_sha256"})
    if data["schema"] != "ciw.atomic-lj-prediction.v1":
        raise ValueError("Unexpected worker prediction schema")
    if type(data["prediction_id"]) is not str or re.fullmatch(r"prediction-[0-9a-f]{32}", data["prediction_id"]) is None:
        raise ValueError("Missing prediction identity")
    if data["input_sha256"] != digest(state) or data["atom_ids"] != state["atom_ids"]:
        raise ValueError("Worker input or atom-order binding differs")
    if canonical(data["model"]) != canonical(MODEL) or data["units"] != UNITS:
        raise ValueError("Worker model or units differ from the bounded profile")
    if data["uncertainty"] != {"status": "unavailable", "covariance": None}:
        raise ValueError("The LJ pilot supplies no uncertainty model")
    info = data["execution_device"]
    keys(info, {"device", "dtype", "torch_version", "cuda_runtime", "gpu_name", "compute_capability", "driver_version"})
    if device not in {"cpu", "cuda:0"} or info["device"] != device or info["dtype"] != "float64":
        raise ValueError("Device or precision contradicts the operator binding")
    if type(info["torch_version"]) is not str or not 1 <= len(info["torch_version"]) <= 96:
        raise ValueError("Missing numerical runtime version")
    for field in ("cuda_runtime", "gpu_name", "driver_version"):
        if info[field] is not None and (type(info[field]) is not str or not 1 <= len(info[field]) <= 256):
            raise ValueError("Malformed hardware declaration")
    if device == "cpu" and (info["gpu_name"] is not None or info["compute_capability"] is not None):
        raise ValueError("CPU execution cannot claim GPU hardware")
    if device == "cuda:0":
        cap = info["compute_capability"]
        if info["gpu_name"] is None or info["cuda_runtime"] is None or type(cap) is not list or len(cap) != 2 or any(type(x) is not int or x < 0 for x in cap):
            raise ValueError("GPU execution needs device and capability declarations")
    if type(data["environment_sha256"]) is not str or re.fullmatch(r"sha256:[a-f0-9]{64}", data["environment_sha256"]) is None:
        raise ValueError("Missing environment fingerprint")
    validate_values(data["values"], len(state["atom_ids"]))


def make_payload(run: dict, state: dict, device: str, data: dict, request: bytes, response: bytes) -> dict:
    validate_worker_data(data, state, device)
    payload = {
        "schema": OUTPUT_SCHEMA, "context_evidence_id": run["evidence_id"],
        "atomic_input": deepcopy(state), "atomic_input_sha256": digest(state),
        "prediction": deepcopy(data), "numerical_check_id": "check-" + uuid.uuid4().hex,
        "numerical_check": compare(state, data["values"]),
        "transport": {"request": blob(request), "response": blob(response)},
        "physical_validation": "not_performed", "authorizes_execution": False,
    }
    validate_payload(OPERATION, payload, run, state, {})
    return payload


def validate_payload(operation_id: str, data: dict, run: dict, parameters: dict, selection: dict) -> None:
    """Offline structural/semantic checks; never launch or re-run the provider.

    Only bounded scalar reference diagnostics are recomputed. They are not new
    provider execution or an experimental validation, and never promote a result.
    """
    if operation_id != OPERATION:
        raise ValueError("Unexpected ALCHEMI operation")
    state = validate_input(parameters)
    keys(data, {"schema", "context_evidence_id", "atomic_input", "atomic_input_sha256",
                "prediction", "numerical_check_id", "numerical_check", "transport",
                "physical_validation", "authorizes_execution"})
    if (data["schema"] != OUTPUT_SCHEMA or data["context_evidence_id"] != run["evidence_id"]
            or canonical(data["atomic_input"]) != canonical(state) or data["atomic_input_sha256"] != digest(state)):
        raise ValueError("Atomic input or context binding differs")
    if data["physical_validation"] != "not_performed" or data["authorizes_execution"] is not False:
        raise ValueError("Atomic calculations do not grant physical validity or authority")
    if type(data["numerical_check_id"]) is not str or re.fullmatch(r"check-[a-f0-9]{32}", data["numerical_check_id"]) is None:
        raise ValueError("Missing independent numerical-check identity")
    keys(data["transport"], {"request", "response"})
    request = loads(unblob(data["transport"]["request"]))
    response = loads(unblob(data["transport"]["response"]))
    keys(request, {"schema", "operation_id", "inputs"})
    keys(request["inputs"], {"configuration", "device", "environment_sha256"})
    if (request["schema"] != "ciw.adapter-request.v1" or request["operation_id"] != OPERATION
            or canonical(request["inputs"]["configuration"]) != canonical(state)):
        raise ValueError("Retained request differs from the captured atomic configuration")
    validate_worker_data(data["prediction"], state, request["inputs"]["device"])
    if request["inputs"]["environment_sha256"] != data["prediction"]["environment_sha256"]:
        raise ValueError("Worker environment differs from requested binding")
    if canonical(response) != canonical({"schema": "ciw.adapter-response.v1", "status": "ok", "data": data["prediction"]}):
        raise ValueError("Retained response differs from the prediction")
    expected = compare(state, data["prediction"]["values"])
    # These diagnostics are a small floating-point computation, not exact proofs.
    # Require exact structure/status/policy and tight portable numeric agreement.
    actual = data["numerical_check"]
    keys(actual, set(expected))
    if canonical(actual["policy"]) != canonical(POLICY):
        raise ValueError("The retained numerical-check policy is immutable")
    def match(a, b):
        if type(b) is dict:
            keys(a, set(b))
            for k in b:
                match(a[k], b[k])
        elif type(b) is list:
            if type(a) is not list or len(a) != len(b):
                raise ValueError("Numerical-check shape differs")
            for x, y in zip(a, b):
                match(x, y)
        elif type(b) is float:
            if not math.isclose(number(a), b, rel_tol=1e-12, abs_tol=1e-14):
                raise ValueError("Retained numerical check differs from scalar reference")
        elif type(a) is not type(b) or a != b:
            raise ValueError("Retained numerical-check policy or status differs")
    match(actual, expected)


def validate_runtime_binding(data: dict, runtime: dict) -> None:
    """Bind prediction environment/device to the outer execution record offline."""
    keys(runtime, {"schema", "adapter_version", "repository_root", "revision", "source_tree",
                   "module", "source_root", "python_executable", "python_sha256",
                   "python_version", "dependencies", "alchemi_profile", "device_binding",
                   "precision", "dependency_binary_attestation"})
    if (runtime["schema"] != "ciw.subprocess-runtime.v1"
            or runtime["adapter_version"] != "ciw-pinned-subprocess-v1"
            or runtime["module"] != "ciw.nvalchemi_worker" or runtime["source_root"] != "src"
            or runtime["alchemi_profile"] != OPERATION or runtime["precision"] != "float64"
            or runtime["dependency_binary_attestation"] != "not_established"):
        raise ValueError("Unexpected ALCHEMI execution runtime contract")
    for name in ("revision", "source_tree"):
        if type(runtime[name]) is not str or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", runtime[name]) is None:
            raise ValueError("Missing NET source revision/tree pin")
    if type(runtime["python_sha256"]) is not str or re.fullmatch(r"[a-f0-9]{64}", runtime["python_sha256"]) is None:
        raise ValueError("Missing interpreter byte pin")
    for name in ("repository_root", "python_executable", "python_version"):
        if type(runtime[name]) is not str or not 1 <= len(runtime[name]) <= 4096:
            raise ValueError("Missing runtime identity declaration")
    deps = runtime["dependencies"]
    keys(deps, {"nvalchemi-toolkit", "nvalchemi-toolkit-ops", "torch", "warp-lang", "numpy", "pydantic",
                "toolkit_revision", "toolkit_files_sha256", "ops_files_sha256", "distribution_inventory_sha256"})
    if any(type(value) is not str or not 1 <= len(value) <= 256 for value in deps.values()):
        raise ValueError("Invalid environment dependency declaration")
    if deps["toolkit_revision"] != TOOLKIT_REVISION:
        raise ValueError("Upstream toolkit pin differs")
    for name in ("toolkit_files_sha256", "ops_files_sha256", "distribution_inventory_sha256"):
        if re.fullmatch(r"sha256:[a-f0-9]{64}", deps[name]) is None:
            raise ValueError("Missing toolkit/environment fingerprint")
    prediction = data["prediction"]
    validate_worker_data(prediction, data["atomic_input"], runtime["device_binding"])
    if prediction["environment_sha256"] != digest({"python_version": runtime["python_version"], "dependencies": deps}):
        raise ValueError("Prediction is not bound to the outer execution environment")
    if prediction["execution_device"]["torch_version"] != deps["torch"]:
        raise ValueError("Prediction torch version differs from the execution runtime")
