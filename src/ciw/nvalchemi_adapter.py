"""Optional ALCHEMI binding over NET's existing pinned-process and operation APIs.

Trusted operator setup registers schemas/bindings. Saved JSON cannot install a
runtime, choose executable paths, download weights or register executable code.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock

from . import nvalchemi_contract as c
from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import PinnedSubprocessAdapter, _json
from .operations.registry import Operation
from .operations.schemas import register_payload_validator

_PROBE = """import sys, json
sys.path.insert(0, sys.argv[1])
from ciw.nvalchemi_worker import probe_environment
print(json.dumps(probe_environment(), allow_nan=False, sort_keys=True))
"""
_SCHEMA_REGISTERED = False
_SCHEMA_LOCK = RLock()


def register_schema() -> None:
    """Make read-only saved-payload validation available, without binding a worker."""
    global _SCHEMA_REGISTERED
    with _SCHEMA_LOCK:
        if not _SCHEMA_REGISTERED:
            register_payload_validator(c.OPERATION, c.validate_payload, role="backend")
            _SCHEMA_REGISTERED = True


class AlchemiAdapter(PinnedSubprocessAdapter):
    """One explicitly pinned worker, with mandatory reviewed environment bindings.

    The NET revision binds this wrapper, contract and worker. The upstream VCS
    revision, toolkit/ops source-file fingerprints and complete distribution
    metadata inventory are separately checked in the isolated runtime probe.
    """

    def __init__(self, net_root: str | Path, net_revision: str, *,
                 python_executable: str | Path, expected_python_sha256: str,
                 expected_python_version: str, expected_dependencies: dict,
                 device: str, timeout_seconds: float = 120.0) -> None:
        if device not in {"cpu", "cuda:0"}:
            raise ValueError("Bind cpu or cuda:0 explicitly; automatic fallback is not supported")
        if not expected_python_sha256 or not expected_python_version or not expected_dependencies:
            raise ValueError("Reviewed interpreter and dependency pins are mandatory")
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Worker timeout must be in (0, 300] seconds")
        self.device = device
        self._evaluation_lock = RLock()
        self._transport = None
        super().__init__(
            net_root, net_revision, "ciw.nvalchemi_worker", source_root="src",
            python_executable=python_executable, timeout_seconds=timeout_seconds,
            max_output_bytes=c.MAX_BYTES, expected_python_sha256=expected_python_sha256,
            expected_python_version=expected_python_version,
            expected_dependencies=deepcopy(expected_dependencies),
        )

    def _probe(self) -> dict:
        code, raw = self._run(_PROBE, [str(self.source_root)])
        if code:
            raise AdapterRefusal("RUNTIME_UNAVAILABLE", "Cannot verify the pinned ALCHEMI environment")
        result = _json(raw)
        c.keys(result, {"python_version", "dependencies"})
        if type(result["python_version"]) is not str or type(result["dependencies"]) is not dict:
            raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "Malformed ALCHEMI environment probe")
        if result["dependencies"].get("toolkit_revision") != c.TOOLKIT_REVISION:
            raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "ALCHEMI upstream revision differs")
        return result

    def _run(self, code: str, arguments: list[str], stdin: bytes = b"") -> tuple[int, bytes]:
        status, raw = super()._run(code, arguments, stdin)
        if stdin:
            self._transport = (bytes(stdin), bytes(raw))
        return status, raw

    def runtime_identity(self) -> dict:
        result = super().runtime_identity()
        result["alchemi_profile"] = c.OPERATION
        result["device_binding"] = self.device
        result["precision"] = "float64"
        result["dependency_binary_attestation"] = "not_established"
        return result

    def evaluate(self, run: dict, parameters: dict) -> dict:
        state = c.validate_input(parameters)
        with self._evaluation_lock:
            self._transport = None
            identity = self.runtime_identity()
            fingerprint = c.digest({"python_version": identity["python_version"],
                                    "dependencies": identity["dependencies"]})
            data = self.invoke(c.OPERATION, {"configuration": state, "device": self.device,
                                             "environment_sha256": fingerprint})
            if self._transport is None:
                raise AdapterRefusal("MALFORMED_RESPONSE", "No retained worker transport")
            request, response = self._transport
            if data.get("environment_sha256") != fingerprint:
                raise AdapterRefusal("RUNTIME_PIN_MISMATCH", "Result is from a different environment")
            payload = c.make_payload(run, state, self.device, data, request, response)
            c.validate_runtime_binding(payload, identity)
            return payload

    def operation(self) -> Operation:
        register_schema()
        return Operation(c.OPERATION, "backend", self.evaluate, self.runtime_identity)
