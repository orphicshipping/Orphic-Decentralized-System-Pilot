"""HOOMD provider binding through existing NET operations and retained data."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import importlib.metadata
import platform
import math
from pathlib import Path
import tempfile

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import _bounded_process, _json
from .control_contracts import keys
from .control_plane import ObservationBus
from .core.identities import canonical_json
from .hoomd_contract import (AUTHORITY, GSD_VERSION, OPERATION, RESULT, STUDY, VERSION,
                             summarize, validate_result, validate_study, validate_runtime)
from .operations.registry import Operation
from .operations.runner import digest


class HoomdRuntime:
    """Operator-selected interpreter; restore never reconstructs this binding.

    Version, installed code/binaries, interpreter and fixed worker are pinned at
    bind time and checked before and after each run. Shared native system
    libraries are reported by platform, not authenticated or comprehensively
    hashed. This is not a sandbox or a cross-device bitwise-replay promise.
    """
    def __init__(self, python):
        self.python = Path(python).expanduser().absolute()
        if not self.python.is_file():
            raise AdapterRefusal("hoomd_runtime_unavailable", "Explicit HOOMD interpreter is missing")
        self.worker = Path(__file__).with_name("hoomd_worker.py")
        self._identity = self._probe()

    def _call(self, mode, request=None):
        with tempfile.TemporaryDirectory(prefix="net-hoomd-") as directory:
            code, output = _bounded_process([str(self.python), "-I", "-B", str(self.worker), mode],
                cwd=Path(directory), timeout=120, limit=8 * 1024 * 1024,
                stdin=b"" if request is None else canonical_json(request).encode())
        if code:
            raise AdapterRefusal("hoomd_runtime_failed", "The bound HOOMD runtime failed its fixed " + mode + " operation")
        return _json(output)

    def _probe(self):
        identity = self._call("probe")
        if (identity.get("hoomd_version") != VERSION or identity.get("gsd_version") != GSD_VERSION
                or identity.get("device") != "cpu" or identity.get("build", {}).get("md_built") is not True):
            raise AdapterRefusal("hoomd_runtime_mismatch", "Require official HOOMD 7.2.0 CPU MD and GSD 5.0.1")
        identity["worker_sha256"] = sha256(self.worker.read_bytes()).hexdigest()
        root = Path(__file__).parent
        names = ("hoomd_contract.py", "hoomd_workflow.py", "hoomd_checks.py")
        identity["bridge_code_sha256"] = sha256(b"\0".join(
            name.encode() + b"\0" + (root / name).read_bytes() for name in names)).hexdigest()
        identity["analysis_environment"] = {"python": platform.python_version(),
            "gsd": importlib.metadata.version("gsd"), "numpy": importlib.metadata.version("numpy")}
        identity["binding"] = "explicit_process_local"
        return validate_runtime(identity)

    def runtime_identity(self):
        current = self._probe()
        if current != self._identity:
            raise AdapterRefusal("hoomd_runtime_changed", "Bound HOOMD runtime or worker bytes changed")
        return deepcopy(current)

    def execute(self, parameters):
        study = _source(parameters)
        before = self.runtime_identity()
        response = self._call("run", study)
        keys(response, {"runtime", "artifacts"})
        expected = {key: value for key, value in before.items() if key not in {"worker_sha256", "binding", "bridge_code_sha256", "analysis_environment"}}
        if response["runtime"] != expected:
            raise AdapterRefusal("hoomd_runtime_changed", "Worker runtime differs from the explicit binding")
        self.runtime_identity()
        result = validate_result({"schema": RESULT, "study": study, "study_ref": digest(study),
            "artifacts": response["artifacts"], "summary": summarize(study, response["artifacts"]),
            "runtime": before, "authority": deepcopy(AUTHORITY)})
        return {"schema": "ciw.data-hoomd-execution.v1", "source_id": parameters["source"]["source_id"],
                "native_result": result}

    def operation(self):
        return Operation(OPERATION, "backend", lambda run, parameters: self.execute(parameters), self.runtime_identity)


def _source(parameters):
    from .data_sources import native_record
    keys(parameters, {"source"})
    source = parameters["source"]
    if type(source) is not dict or source.get("kind") != "data-record":
        raise ValueError("HOOMD requires a retained data-record study source")
    return validate_study(native_record(source))


def source_parameters(workbench, source_id):
    return {"source": deepcopy(workbench.get_source(source_id))}


def validate_payload(operation_id, data, run, parameters, selection):
    if operation_id != OPERATION:
        raise ValueError("Unknown particle operation")
    study = _source(parameters)
    keys(data, {"schema", "source_id", "native_result"})
    if (data["schema"] != "ciw.data-hoomd-execution.v1" or data["source_id"] != parameters["source"]["source_id"]
            or canonical_json(data["native_result"].get("study")) != canonical_json(study)):
        raise ValueError("HOOMD output differs from its retained study")
    validate_result(data["native_result"])


def validate_live_dependencies(operation_id, parameters, sources):
    if operation_id != OPERATION:
        return
    _source(parameters)
    source = parameters["source"]
    if (source["source_id"] not in sources
            or canonical_json(sources[source["source_id"]]) != canonical_json(source)):
        raise ValueError("HOOMD requires the source present unchanged in this Workbench")


def validate_saved_dependencies(results, sources):
    for result in results.values():
        if result["operation_id"] == OPERATION:
            validate_live_dependencies(OPERATION, result["parameters"], sources)


def retain_result(workbench, operation_result, *, dataset_id, recorded_at, label):
    """Keep full native GSD bytes in the same existing retained-data catalog."""
    from .data_sources import retain_record
    if operation_result.get("operation_id") != OPERATION:
        raise ValueError("Require a HOOMD operation result")
    validate_payload(OPERATION, operation_result["data"], {}, operation_result["parameters"], {})
    source_id = operation_result["data"]["source_id"]
    validate_live_dependencies(OPERATION, operation_result["parameters"],
                               {source_id: workbench.get_source(source_id)})
    envelope = retain_record(operation_result["data"]["native_result"], dataset_id=dataset_id, revision=1,
        parent_source_id=None, recorded_at=recorded_at, data_family="hoomd-result")
    return workbench.add_source({"kind": "data-record", "label": label,
        "bytes_b64": base64.b64encode(json.dumps(envelope, allow_nan=False).encode()).decode(),
        "input_source_ids": [source_id]})


def observation_stream(value):
    """Selected scalar descriptors in SI via explicit nominal scales, no hidden calibration."""
    value = validate_result(value)
    study, rows = value["study"], value["summary"]["production_descriptors"]
    scales = study["units"]
    length, energy, mass = scales["length_m"], scales["energy_J"], scales["mass_kg"]
    tau = length * math.sqrt(mass / energy)
    conversions = {"potential_energy": (energy, "J"), "kinetic_energy": (energy, "J"),
                   "pressure": (energy / length**3, "Pa"), "radius_gyration": (length, "m"),
                   "mean_square_displacement": (length**2, "m^2"),
                   "finite_lag_diffusivity": (length**2 / tau, "m^2/s")}
    # Scope identity by the specimen representation and nominal unit scales;
    # a label alone cannot conflate another model or unit mapping.
    model_ref = digest({key: study[key] for key in ("material", "representation", "topology", "potential", "units")})
    bus = ObservationBus(capacity=len(rows) * len(conversions))
    for row in rows:
        for quantity, (scale, unit) in conversions.items():
            native = row[quantity]
            bus.observe(identity={"model_id": "hoomd:" + model_ref,
                "entity_id": "specimen:" + model_ref, "execution_id": None},
                clock={"id": "hoomd-declared-si-time:" + digest(study["units"]),
                       "time_s": row["step"] * study["integrator"]["dt"] * tau},
                frame="hoomd.orthorhombic-periodic-unwrapped.v1", quantity="hoomd." + quantity,
                value=None if native is None else native * scale, unit=unit,
                provenance={"provider": "hoomd-blue.7.2.0.cpu", "semantics": "simulated",
                            "sources": [value["study_ref"], value["artifacts"]["production"]["sha256"], row["frame_id"]]})
    return bus.snapshot()
