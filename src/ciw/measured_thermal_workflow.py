"""Retained lifecycle for operator-declared measured thermal observations.

Exact recording bytes and declared references remain evidence, not authenticated
acquisition. Numerical reproduction does not establish physical validation,
canonical admission or permission to actuate equipment. Restoring a bundle
validates its retained data without entering the workflow execution path.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import uuid

import numpy as np

from . import measured_thermal as contract
from . import thermal_contract
from . import control_checks, control_contracts, calibration_status, telemetry
from .core import covariance, identities
from .operations import runner
from .adapters import subprocess as subprocess_adapter
from . import thermal_reference as reference
from .exchange import _identity
from .telemetry import _bundle_digest, _now, byte_digest, canonical, digest, _keys

KIND = "measured-thermal"
SCHEMA = "ciw.measured-thermal-session.v1"
OPERATION = "ciw.measured-thermal.v1"
SOURCE_SCHEMA = contract.SOURCE_SCHEMA
RESULT_SCHEMA = "ciw.measured-thermal-workbench-result.v1"
VERIFY_SCHEMA = "ciw.measured-thermal-verification.v1"
ROLE = "thermal"
ROLES = set()
MAX_BYTES = contract.RESULT_LIMIT
SOURCE_LIMIT = contract.SOURCE_LIMIT
CONFIGURATION = contract.POLICY
PROFILE = "ciw.measured-thermal.python-reference.v1"
EXECUTION_SCOPE = "measured_observation_python_reference_only"
AUTHORITY = {
    "physical_validation": "not_established",
    "state_admission": "not_performed",
    "hardware_actuation": "not_performed",
}


def _text(value, limit=512):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Require bounded nonempty text")


def _algorithm_identity():
    files = [Path(contract.__file__), Path(__file__),
             Path(thermal_contract.__file__), Path(reference.__file__),
             Path(control_checks.__file__), Path(control_contracts.__file__),
             Path(calibration_status.__file__), Path(covariance.__file__),
             Path(identities.__file__), Path(runner.__file__), Path(telemetry.__file__),
             Path(subprocess_adapter.__file__)]
    content = b"\0".join(
        path.name.encode("utf-8") + b"\0" + path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
        for path in files
    )
    return {
        "profile": PROFILE,
        "code_sha256": sha256(content).hexdigest(),
        "source_normalization": "utf8_lf",
        "numpy_version": np.__version__,
    }


def runtime_identity():
    return {
        "schema": "ciw.python-reference-runtime.v1",
        "role": ROLE,
        "profile": PROFILE,
        "algorithm": _algorithm_identity(),
        "execution_scope": EXECUTION_SCOPE,
        "physical_validation": "not_established",
    }


def _runtime_projection(value):
    return deepcopy(value)


def _adapters(repositories, expected=None):
    if not isinstance(repositories, dict) or repositories:
        raise ValueError("The provider-free measured thermal reference accepts no repository bindings")
    runtime = runtime_identity()
    if expected is not None:
        expected_runtime = expected.get(ROLE, expected) if isinstance(expected, dict) else expected
        if _runtime_projection(runtime) != _runtime_projection(expected_runtime):
            raise ValueError("Measured thermal reference runtime identity differs from the retained execution")
    return None, runtime


def _native_result(source):
    return contract.calculate(source)


def _step(source, evidence_id, runtime, execution_id=None):
    occurrence = execution_id or "execution-" + uuid.uuid4().hex
    request = contract.execution_request(source)
    data = _native_result(source)
    result = {
        "schema": RESULT_SCHEMA,
        "operation_id": OPERATION,
        "execution_ref": occurrence,
        "input_refs": [evidence_id],
        "data": data,
        "authority": deepcopy(AUTHORITY),
    }
    result["result_id"] = digest(result)
    numerical = {"operation_id": OPERATION, "data": deepcopy(data)}
    return {
        "runtime_ref": ROLE,
        "operation_id": OPERATION,
        "execution_id": occurrence,
        "input_refs": [evidence_id],
        "request": deepcopy(request),
        "request_sha256": digest(request),
        "result": result,
        "result_sha256": digest(result),
        "result_id": result["result_id"],
        "numerical_result": numerical,
        "numerical_result_id": digest(numerical),
    }


def _validate_step(step, source, evidence_id):
    request = contract.execution_request(source)
    _keys(step, {"runtime_ref", "operation_id", "execution_id", "input_refs", "request", "request_sha256",
                 "result", "result_sha256", "result_id", "numerical_result", "numerical_result_id"})
    if (step["runtime_ref"] != ROLE or step["operation_id"] != OPERATION or
            not re.fullmatch(r"execution-[a-f0-9]{32}", step["execution_id"]) or
            step["input_refs"] != [evidence_id] or canonical(step["request"]) != canonical(request)
            or step["request_sha256"] != digest(request)):
        raise ValueError("Measured thermal step request or occurrence binding differs")
    result = step["result"]
    _keys(result, {"schema", "operation_id", "execution_ref", "input_refs", "data", "authority", "result_id"})
    if result["schema"] != RESULT_SCHEMA or result["operation_id"] != OPERATION or result["execution_ref"] != step["execution_id"]:
        raise ValueError("Measured thermal native result envelope differs")
    if result["input_refs"] != [evidence_id] or result["authority"] != AUTHORITY:
        raise ValueError("Measured thermal native result authority or input binding differs")
    contract.validate_data(source, result["data"])
    if result["result_id"] != digest({key: value for key, value in result.items() if key != "result_id"}):
        raise ValueError("Measured thermal native result identity differs")
    numerical = {"operation_id": OPERATION, "data": result["data"]}
    if canonical(step["numerical_result"]) != canonical(numerical) or step["numerical_result_id"] != digest(numerical):
        raise ValueError("Measured thermal numerical result identity differs")
    if step["result_id"] != result["result_id"] or step["result_sha256"] != digest(result):
        raise ValueError("Measured thermal step result commitment differs")


def _verification(bundle, reproduced):
    if canonical(bundle["steps"][0]["numerical_result"]) != canonical(reproduced["numerical_result"]):
        raise ValueError("Measured thermal replay numerical result differs")
    value = {
        "schema": VERIFY_SCHEMA,
        "subject_ref": bundle["bundle_digest"],
        "outcome": "passed",
        "independent": False,
        "method": "same_python_reference_fresh_occurrence_reproduction",
        "runtime_digest": digest(bundle["runtimes"]),
        "reproduction": deepcopy(reproduced),
        "authority": deepcopy(AUTHORITY),
    }
    value["verification_id"] = byte_digest(VERIFY_SCHEMA.encode() + b"\0" + canonical(value))
    return value


class MeasuredThermalWorkflow:
    MAX_BYTES = MAX_BYTES
    kind = KIND
    role = ROLE
    ROLES = ROLES
    SOURCE_SCHEMA = SOURCE_SCHEMA
    SOURCE_LIMIT = SOURCE_LIMIT
    schema = SCHEMA
    operation = OPERATION

    def _source(self, raw):
        return contract.validate_source(raw)

    def _adapters(self, repositories, expected=None):
        return _adapters(repositories, expected)

    @staticmethod
    def _runtime_projection(value):
        return _runtime_projection(value)

    def _step(self, source, evidence_id, bound):
        _, runtime = bound
        return _step(source, evidence_id, runtime)

    @staticmethod
    def _validate_step(step, source, evidence_id):
        return _validate_step(step, source, evidence_id)

    def _check_verification(self, bundle, verification, source, evidence):
        _keys(verification, {"schema", "subject_ref", "outcome", "independent", "method", "runtime_digest",
                             "reproduction", "authority", "verification_id"})
        _validate_step(verification["reproduction"], source, evidence)
        old, new = bundle["steps"][0], verification["reproduction"]
        if (old["execution_id"] == new["execution_id"] or old["result_id"] == new["result_id"] or
                verification["independent"] is not False or
                canonical(verification) != canonical(_verification(bundle, new))):
            raise ValueError("Measured thermal verification must bind a fresh occurrence")
        _identity(verification, "verification_id")

    def _validate(self, bundle):
        # Retained data is checked without binding providers or requiring the
        # currently installed runtime; replay separately requires an exact match.
        try:
            _keys(bundle, {"schema", "session_id", "created_at", "source", "configuration", "runtimes",
                           "steps", "bundle_digest", "verification"}, {"replay_receipts"})
            if (len(canonical(bundle)) > MAX_BYTES or bundle["schema"] != SCHEMA or
                    bundle["bundle_digest"] != _bundle_digest(bundle) or
                    not re.fullmatch(r"session-[a-f0-9]{32}", bundle["session_id"])):
                raise ValueError("Measured thermal bundle identity or size differs")
            _text(bundle["created_at"])
            evidence, = bundle["source"]["evidence"]
            if not isinstance(evidence["bytes_b64"], str) or len(evidence["bytes_b64"]) > 4 * ((SOURCE_LIMIT + 2) // 3):
                raise ValueError("Measured thermal source exceeds the byte budget")
            raw = base64.b64decode(evidence["bytes_b64"], validate=True)
            source = self._source(raw)
            expected_source = {"experiment_id": source["experiment_id"], "experiment_digest": digest(source),
                               "evidence": [{"artifact_ref": byte_digest(raw), "sha256": byte_digest(raw),
                                              "bytes_b64": base64.b64encode(raw).decode()}]}
            if evidence != expected_source["evidence"][0] or bundle["source"] != expected_source:
                raise ValueError("Measured thermal source evidence binding differs")
            if canonical(bundle["configuration"]) != canonical(source["configuration"]):
                raise ValueError("Measured thermal authority policy differs")
            _keys(bundle["runtimes"], {ROLE})
            runtime = bundle["runtimes"][ROLE]
            _keys(runtime, {"schema", "role", "profile", "algorithm", "execution_scope", "physical_validation"})
            if (runtime["schema"] != "ciw.python-reference-runtime.v1" or runtime["role"] != ROLE or
                    runtime["profile"] != PROFILE or
                    runtime["execution_scope"] != EXECUTION_SCOPE or
                    runtime["physical_validation"] != "not_established"):
                raise ValueError("Unapproved measured thermal reference runtime")
            algorithm = runtime["algorithm"]
            _keys(algorithm, {"profile", "code_sha256", "source_normalization", "numpy_version"})
            if (algorithm["profile"] != PROFILE or
                    algorithm["source_normalization"] != "utf8_lf" or
                    not re.fullmatch(r"[a-f0-9]{64}", algorithm["code_sha256"]) or
                    not isinstance(algorithm["numpy_version"], str) or
                    not algorithm["numpy_version"].strip() or len(algorithm["numpy_version"]) > 128):
                raise ValueError("Malformed measured thermal algorithm identity")
            step, = bundle["steps"]
            self._validate_step(step, source, evidence["artifact_ref"])
            self._check_verification(bundle, bundle["verification"], source, evidence["artifact_ref"])
            receipts = bundle.get("replay_receipts", [])
            if not isinstance(receipts, list) or len(receipts) > 1:
                raise ValueError("At most one measured thermal replay receipt belongs to an occurrence")
            for receipt in receipts:
                _keys(receipt, {"schema", "source_bundle_digest", "replayed_bundle_digest", "numerical_match",
                                "verification", "admission", "replay_id"})
                if (receipt["schema"] != "ciw." + KIND + "-replay.v1" or
                        receipt["replayed_bundle_digest"] != bundle["bundle_digest"] or
                        not isinstance(receipt["source_bundle_digest"], str) or
                        not re.fullmatch(r"sha256:[a-f0-9]{64}", receipt["source_bundle_digest"]) or
                        receipt["source_bundle_digest"] == bundle["bundle_digest"] or
                        receipt["numerical_match"] is not True or receipt["admission"] != "not_performed" or
                        receipt["replay_id"] != digest({key: value for key, value in receipt.items() if key != "replay_id"})):
                    raise ValueError("Invalid measured thermal replay receipt")
                verification = receipt["verification"]
                _keys(verification, {"schema", "subject_ref", "outcome", "independent", "method", "runtime_digest",
                                     "reproduction", "authority", "verification_id"})
                if (verification["schema"] != VERIFY_SCHEMA or verification["subject_ref"] != receipt["source_bundle_digest"] or
                        verification["outcome"] != "passed" or verification["independent"] is not False or
                        verification["method"] != "same_python_reference_fresh_occurrence_reproduction" or
                        verification["authority"] != AUTHORITY or
                        verification["runtime_digest"] != digest(bundle["runtimes"]) or
                        canonical(verification["reproduction"]) != canonical(step)):
                    raise ValueError("Invalid measured thermal replay verification scope")
                _validate_step(verification["reproduction"], source, evidence["artifact_ref"])
                _identity(verification, "verification_id")
            return raw
        except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
            raise ValueError("Malformed retained measured thermal session") from exc

    def _execute(self, raw, runtime):
        source = self._source(raw)
        evidence = byte_digest(raw)
        step = _step(source, evidence, runtime)
        bundle = {
            "schema": SCHEMA,
            "session_id": "session-" + uuid.uuid4().hex,
            "created_at": _now(),
            "source": {"experiment_id": source["experiment_id"], "experiment_digest": digest(source),
                       "evidence": [{"artifact_ref": evidence, "sha256": evidence,
                                     "bytes_b64": base64.b64encode(raw).decode()}]},
            "configuration": deepcopy(source["configuration"]),
            "runtimes": {ROLE: deepcopy(runtime)},
            "steps": [step],
        }
        bundle["bundle_digest"] = _bundle_digest(bundle)
        bundle["verification"] = _verification(bundle, _step(source, evidence, runtime))
        self._validate(bundle)
        return bundle

    def create_session(self, raw, repositories):
        self._source(raw)
        return self._execute(raw, self._adapters(repositories)[1])

    def replay_session(self, bundle, repositories):
        raw = self._validate(bundle)
        runtime = self._adapters(repositories, bundle["runtimes"])[1]
        fresh = self._execute(raw, runtime)
        receipt = {
            "schema": "ciw." + KIND + "-replay.v1",
            "source_bundle_digest": bundle["bundle_digest"],
            "replayed_bundle_digest": fresh["bundle_digest"],
            "numerical_match": True,
            "verification": _verification(bundle, fresh["steps"][0]),
            "admission": "not_performed",
        }
        receipt["replay_id"] = digest(receipt)
        fresh["replay_receipts"] = [receipt]
        self._validate(fresh)
        return {"session": fresh, "replay_receipt": receipt}
