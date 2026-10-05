"""Bounded uniaxial generalized-Maxwell reference responses, not material qualification.

Uses existing NET content seals and occurrence identities. No parameter fitting,
temperature shifting, arbitrary loading, native-provider grant, or second ledger.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import math
from pathlib import Path
import platform

from .control_contracts import content_ref, detached, keys, number, text
from .core.identities import content_identity, new_identity, validate_identity
from .operations.runner import check_seal, seal

REQUEST_SCHEMA = "ciw.polymer-rheology-request.v1"
RESULT_SCHEMA = "ciw.polymer-rheology-result.v1"
OPERATION = "polymer.rheology.reference.v1"
MODEL = "uniaxial-generalized-maxwell-isothermal.v1"
MAX_MODES = 32
MAX_SAMPLES = 256
MAX_WORK = 16384
AUTHORITY = {"physical_validation": False, "parameter_identification": False,
             "uncertainty_propagation": False, "state_admission": False, "hardware_actuation": False}
TIME_FIELDS = {"relaxation_modulus_pa", "stress_pa", "stored_energy_j_m3",
               "dissipated_energy_j_m3", "dissipation_rate_w_m3"}
FREQUENCY_FIELDS = {"storage_modulus_pa", "loss_modulus_pa"}


def _bound(value, low, high, label):
    x = number(value)
    if not low <= x <= high:
        raise ValueError(f"{label} outside [{low}, {high}]")
    return x


def _grid(value, label, *, initial_zero):
    if type(value) is not list or not (2 if initial_zero else 1) <= len(value) <= MAX_SAMPLES:
        raise ValueError(f"{label} requires a bounded list")
    result = [_bound(t, 0, 1e9 if initial_zero else 1e12, label) for t in value]
    if ((initial_zero and result[0] != 0) or any(0 < t < 1e-12 for t in result)
            or any(b <= a for a, b in zip(result, result[1:]))):
        raise ValueError(f"{label} requires increasing samples; positive samples must be >= 1e-12")
    return result


def validate_request(request):
    keys(request, {"schema", "model", "material_id", "source_kind", "parameter_source_ref",
                   "temperature_k", "equilibrium_modulus_pa", "modes", "step_strain",
                   "time_s", "angular_frequency_rad_s"})
    p = detached(request)
    if p["schema"] != REQUEST_SCHEMA or p["model"] != MODEL:
        raise ValueError("Unsupported rheology schema or model")
    text(p["material_id"])
    if p["source_kind"] not in ("synthetic", "caller_declared"):
        raise ValueError("A parameter declaration is not a qualified measurement")
    content_ref(p["parameter_source_ref"])
    p["temperature_k"] = _bound(p["temperature_k"], 200, 600, "temperature_k")
    p["equilibrium_modulus_pa"] = _bound(p["equilibrium_modulus_pa"], 0, 1e12, "equilibrium modulus")
    p["step_strain"] = _bound(p["step_strain"], -0.02, 0.02, "step strain")
    if 0 < abs(p["step_strain"]) < 1e-12:
        raise ValueError("Nonzero step strain must be at least 1e-12 in magnitude")
    if type(p["modes"]) is not list or len(p["modes"]) > MAX_MODES:
        raise ValueError("At most 32 Maxwell modes are supported")
    identifiers = set()
    for mode in p["modes"]:
        keys(mode, {"mode_id", "modulus_pa", "relaxation_time_s"})
        identifier = text(mode["mode_id"])
        if identifier in identifiers:
            raise ValueError("Duplicate Maxwell mode identity")
        identifiers.add(identifier)
        mode["modulus_pa"] = _bound(mode["modulus_pa"], 1e-6, 1e12, "mode modulus")
        mode["relaxation_time_s"] = _bound(mode["relaxation_time_s"], 1e-9, 1e9, "relaxation time")
    if not p["modes"] and p["equilibrium_modulus_pa"] == 0:
        raise ValueError("The model must contain a positive stiffness")
    p["time_s"] = _grid(p["time_s"], "time_s", initial_zero=True)
    p["angular_frequency_rad_s"] = _grid(p["angular_frequency_rad_s"], "angular_frequency_rad_s", initial_zero=False)
    if len(p["modes"]) * (len(p["time_s"]) + len(p["angular_frequency_rad_s"])) > MAX_WORK:
        raise ValueError("Rheology mode-sample budget exceeded")
    return p


def runtime_identity():
    from . import control_contracts, polymer_rheology_reference
    from .core import identities
    from .operations import runner
    modules = [__import__(__name__, fromlist=["*"]), polymer_rheology_reference, control_contracts, identities, runner]
    return {"python": platform.python_version(), "source_normalization": "utf8_lf",
            "source_refs": {m.__name__: "sha256:" + sha256(Path(m.__file__).read_text(encoding="utf-8")
                            .replace("\r\n", "\n").encode()).hexdigest() for m in modules}}


def _weighted_decay(weight, exponent):
    # Do not underflow exp(-x) before multiplication by a large modulus/rate.
    # Within this profile weight <= 1e21, so x > 800 rounds to binary64 zero.
    if exponent > 800:
        return 0.0
    if exponent < 600:
        return weight * math.exp(-exponent)
    half = math.exp(-exponent / 2)
    return (weight * half) * half


def _compute(p):
    """Binary64 production path; no call to the Decimal verification path."""
    e0, strain = p["equilibrium_modulus_pa"], p["step_strain"]
    time = {key: [] for key in TIME_FIELDS}
    for t in p["time_s"]:
        decay, energy, dissipated, rate = [], [], [], []
        for mode in p["modes"]:
            e, tau = mode["modulus_pa"], mode["relaxation_time_s"]
            x = t / tau
            decay.append(_weighted_decay(e, x))
            energy.append(_weighted_decay(e, 2*x))
            dissipated.append(e * -math.expm1(-2*x))
            rate.append(_weighted_decay(e / tau, 2*x))
        modulus = math.fsum([e0, *decay])
        row = {"relaxation_modulus_pa": modulus, "stress_pa": strain * modulus,
               "stored_energy_j_m3": 0.5 * strain**2 * math.fsum([e0, *energy]),
               "dissipated_energy_j_m3": 0.5 * strain**2 * math.fsum(dissipated),
               "dissipation_rate_w_m3": strain**2 * math.fsum(rate)}
        for key in time:
            time[key].append(row[key])
    frequency = {key: [] for key in FREQUENCY_FIELDS}
    for omega in p["angular_frequency_rad_s"]:
        storage, loss = [e0], []
        for mode in p["modes"]:
            x, e = omega * mode["relaxation_time_s"], mode["modulus_pa"]
            if x <= 1:
                storage.append(e * x*x / (1 + x*x))
                loss.append(e * x / (1 + x*x))
            else:
                inverse = 1 / x
                storage.append(e / (1 + inverse*inverse))
                loss.append(e * inverse / (1 + inverse*inverse))
        frequency["storage_modulus_pa"].append(math.fsum(storage))
        frequency["loss_modulus_pa"].append(math.fsum(loss))
    return {"time_response": time, "frequency_response": frequency}


def _work(p):
    return {"mode_time_evaluations": len(p["modes"]) * len(p["time_s"]),
            "mode_frequency_evaluations": len(p["modes"]) * len(p["angular_frequency_rad_s"])}


def analyze(request):
    p = validate_request(request)
    result = seal({"schema": RESULT_SCHEMA, "operation_id": OPERATION,
                   "evidence_id": content_identity(p), "execution_id": new_identity("execution"),
                   "result_id": new_identity("result"), "request": p, "runtime": runtime_identity(),
                   "response": _compute(p), "work": _work(p), "verification_status": "not_verified",
                   "authority": deepcopy(AUTHORITY)})
    validate_result(result)
    return result


def validate_result(result):
    """Structural inspection only; never execute a response or numerical oracle."""
    detached(result)
    check_seal(result)
    keys(result, {"schema", "operation_id", "evidence_id", "execution_id", "result_id", "request", "runtime",
                  "response", "work", "verification_status", "authority", "record_digest"})
    p = validate_request(result["request"])
    if (result["schema"] != RESULT_SCHEMA or result["operation_id"] != OPERATION
            or result["evidence_id"] != content_identity(p) or result["verification_status"] != "not_verified"
            or content_identity(result["authority"]) != content_identity(AUTHORITY)):
        raise ValueError("Rheology evidence, schema or authority binding differs")
    validate_identity(result["execution_id"], "execution")
    validate_identity(result["result_id"], "result")
    runtime = result["runtime"]
    keys(runtime, {"python", "source_normalization", "source_refs"})
    text(runtime["python"])
    if runtime["source_normalization"] != "utf8_lf":
        raise ValueError("Unsupported runtime source normalization")
    keys(runtime["source_refs"], {"ciw.polymer_rheology", "ciw.polymer_rheology_reference",
                                 "ciw.control_contracts", "ciw.core.identities", "ciw.operations.runner"})
    for ref in runtime["source_refs"].values():
        content_ref(ref)
    keys(result["response"], {"time_response", "frequency_response"})
    for group, fields, count in (("time_response", TIME_FIELDS, len(p["time_s"])),
                                 ("frequency_response", FREQUENCY_FIELDS, len(p["angular_frequency_rad_s"]))):
        keys(result["response"][group], fields)
        for key, values in result["response"][group].items():
            if type(values) is not list or len(values) != count:
                raise ValueError("Response sampling dimensions differ")
            for value in values:
                number(value)
                if key != "stress_pa" and value < 0:
                    raise ValueError("Passive response quantities must be nonnegative")
    keys(result["work"], set(_work(p)))
    if any(type(v) is not int for v in result["work"].values()) or result["work"] != _work(p):
        raise ValueError("Mode-sample work accounting differs")
    return result


def verify(result):
    """Fresh independent high-precision numerical audit; never rewrites evidence."""
    validate_result(result)
    from .polymer_rheology_reference import reference_response
    expected = reference_response(result["request"])
    failures, max_relative = [], 0.0
    for group, values in expected.items():
        for field, reference in values.items():
            for i, (actual, target) in enumerate(zip(result["response"][group][field], reference)):
                error = abs(actual - target)
                # No engineering-scale absolute floor. Eight ULPs cover the
                # binary64 underflow fringe, not a material/experimental bound.
                limit = max(2e-12 * abs(target), 8 * math.ulp(target))
                scale = max(abs(actual), abs(target))
                if scale != 0:
                    max_relative = max(max_relative, error / scale)
                p = result["request"]
                exact_zero = (group == "frequency_response" and p["angular_frequency_rad_s"][i] == 0
                              and (field == "loss_modulus_pa" or p["equilibrium_modulus_pa"] == 0))
                exact_zero = exact_zero or (group == "time_response" and field != "relaxation_modulus_pa"
                              and p["step_strain"] == 0)
                exact_zero = exact_zero or (group == "time_response" and field == "dissipated_energy_j_m3" and i == 0)
                exact_zero = exact_zero or (not p["modes"] and field in {
                              "loss_modulus_pa", "dissipated_energy_j_m3", "dissipation_rate_w_m3"})
                if error > limit or (exact_zero and actual != 0):
                    failures.append({"group": group, "field": field, "index": i})
    return seal({"schema": "ciw.polymer-rheology-verification.v1", "verification_id": new_identity("verification"),
                 "result_id": result["result_id"], "execution_id": result["execution_id"],
                 "evidence_id": result["evidence_id"], "result_record_digest": result["record_digest"],
                 "status": "PASS" if not failures else "FAIL", "failures": failures,
                 "maximum_symmetric_relative_discrepancy": max_relative,
                 "policy": {"relative_tolerance": 2e-12, "ulp_allowance": 8},
                 "reference": "independent-decimal80-closed-forms.v1", "runtime": runtime_identity(),
                 "authority": deepcopy(AUTHORITY)})


def example_request():
    return {"schema": REQUEST_SCHEMA, "model": MODEL, "material_id": "synthetic.polymer.spectrum",
            "source_kind": "synthetic", "parameter_source_ref": content_identity({"fixture": "maxwell-two-mode-v1"}),
            "temperature_k": 300.0, "equilibrium_modulus_pa": 1e6,
            "modes": [{"mode_id": "fast", "modulus_pa": 2e6, "relaxation_time_s": 0.1},
                      {"mode_id": "slow", "modulus_pa": 3e6, "relaxation_time_s": 10.0}],
            "step_strain": 0.01, "time_s": [0.0, 0.001, 0.01, 0.1, 1.0, 10.0, 100.0],
            "angular_frequency_rad_s": [0.0, 0.001, 0.01, 0.1, 1.0, 10.0, 1000.0]}
