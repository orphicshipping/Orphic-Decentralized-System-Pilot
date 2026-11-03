"""Compute-only, host-pinned TBRT mapping and complete canonical receipt replay.

The existing scientific implementation runs only in the selected subprocess.
Clock frames and evidence references are caller assertions, not authentication.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from importlib import resources
import json
import math
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import PinnedSubprocessAdapter, _bounded_process, _json
from .agent_tools import _shape
from .coordinator import _Lease

PROFILE = "clocksync.affine.v1"
PIN = "40507060ca7a9126a9d641999b994a757eef3bfd"
OPERATION = "tbrt.affine-clock-reconcile.v1"
RECEIPT_SCHEMA = "odsp.clocksync-receipt.v1"
MAX_ARGUMENT = 65536
MAX_RESPONSE = 262144
MAX_PROCESS_OUTPUT = 131072
CALL_SECONDS = 20.0
PROCESS_SECONDS = 5.0
CPU_SECONDS = 3
ADDRESS_BYTES = 256 * 1024 * 1024
FILE_BYTES = 1024 * 1024
OPEN_FILES = 32
AUTHORITY = {
    "source_binding_verification": "caller_assertion_only", "authorizes_execution": False,
    "authorizes_state_admission": False, "source_authenticated": False,
    "synchronization_performed": False, "utc_conversion_performed": False,
    "traceability_verified": False, "evidence_admission": False,
}
_FRAME = {"clock_id", "time_scale", "unit"}
_OBSERVATION = {"device_time", "frame", "evidence_id", "received_at", "known_at"}
_MODEL = {"model_id", "source_frame", "reference_frame", "device_origin", "reference_origin",
          "skew", "offset", "valid_device_interval", "synchronization_evidence_ids"}
_REQUEST = {"execution_id", "observation", "model", "expected_reference", "joint_covariance"}
_NUMERICAL = {"observation", "model", "event_time_delta", "variance", "jacobian", "joint_covariance",
              "propagation", "operation_id", "reference_origin", "reference_frame", "event_time",
              "standard_uncertainty", "event_time_representation"}
_RECEIPT = {"schema", "profile", "operation_id", "execution_id", "request", "request_digest",
            "runtime", "comparison_policy", "outcome", "numerical_result", "numerical_result_id",
            "authority", "receipt_digest"}
_RUNTIME = {"schema", "adapter_version", "revision", "source_tree", "module", "source_root",
            "python_sha256", "python_version", "dependencies"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")

# These limits apply to the runtime probe too. Thread settings keep NumPy's
# installed BLAS within the memory bound; this is not environment scrubbing or
# a network/filesystem sandbox. Neither wrapper nor kernel reads event clocks.
_GUARD = r'''
import os, resource
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
'''
_BOOTSTRAP = r'''
import dataclasses, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import tbrt
import tbrt.clock
if any(not pathlib.Path(module.__file__).resolve().is_relative_to(root)
       for module in (tbrt, tbrt.clock)):
    raise RuntimeError('pinned import root mismatch')
from tbrt import AffineClockModel, ClockFrame, TimePoint, TimestampObservation, reconcile_time
request = json.loads(sys.stdin.buffer.read(65537))
def point(value):
    return None if value is None else TimePoint(value['value'], ClockFrame(**value['frame']))
observation = dict(request['observation'])
observation['frame'] = ClockFrame(**observation['frame'])
for key in ('received_at', 'known_at'):
    observation[key] = point(observation[key])
model = dict(request['model'])
for key in ('source_frame', 'reference_frame'):
    model[key] = ClockFrame(**model[key])
try:
    mapped = reconcile_time(TimestampObservation(**observation), AffineClockModel(**model),
        request['joint_covariance']['matrix'],
        expected_reference=ClockFrame(**request['expected_reference']),
        require_synchronization_evidence=True)
except (ValueError, TypeError, OverflowError):
    response = {'kind': 'refused'}
else:
    result = dataclasses.asdict(mapped)
    result.update(reference_origin=mapped.reference_origin,
        reference_frame=dataclasses.asdict(mapped.reference_frame),
        event_time=mapped.event_time, standard_uncertainty=mapped.standard_uncertainty,
        event_time_representation='rounded_binary64_convenience')
    response = {'kind': 'reconciled', 'numerical_result': result}
response['limits'] = {'cpu_seconds': resource.getrlimit(resource.RLIMIT_CPU)[0],
    'address_space_bytes': resource.getrlimit(resource.RLIMIT_AS)[0],
    'file_bytes': resource.getrlimit(resource.RLIMIT_FSIZE)[0],
    'open_files': resource.getrlimit(resource.RLIMIT_NOFILE)[0]}
print(json.dumps(response, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False))
'''
COMPARISON_POLICY = {
    "comparison_id": "odsp.clocksync.canonical-replay.v1",
    "comparison": "complete_canonical_json_exact", "scope": "same_bound_runtime",
    "cross_platform_bitwise_guarantee": False,
    "dependency_identity": "numpy_scipy_version_labels_only_not_binary_attestation",
    "propagation": "first-order.v1", "origins": "fixed_constants",
    "bridge_sha256": sha256((_GUARD + _BOOTSTRAP).encode()).hexdigest(),
    "blas_threads": 1,
}


class ClockSyncRefusal(ValueError):
    """Public refusal with sanitized detail; scientific refusals issue no receipt."""


def _require(condition, reason="invalid_contract"):
    if not condition:
        raise ClockSyncRefusal(reason)


def canonical(value):
    """Bound both the JSON tree and ASCII output before accepting a payload."""
    remaining = 8192

    def check(item, depth=0):
        nonlocal remaining
        remaining -= 1
        _require(remaining >= 0 and depth <= 16, "json_structure_budget_exceeded")
        if item is None or type(item) in (bool, int):
            return
        if type(item) is float:
            _require(math.isfinite(item))
        elif type(item) is str:
            _require(len(item) <= MAX_RESPONSE, "json_structure_budget_exceeded")
        elif type(item) is list and len(item) <= 32:
            for child in item:
                check(child, depth + 1)
        elif type(item) is dict and len(item) <= 32 and all(type(key) is str for key in item):
            for key, child in item.items():
                check(key, depth + 1)
                check(child, depth + 1)
        else:
            raise ClockSyncRefusal("invalid_json")

    check(value)
    chunks, length = [], 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    for chunk in encoder.iterencode(value):
        length += len(chunk)
        _require(length <= MAX_RESPONSE, "json_byte_budget_exceeded")
        chunks.append(chunk)
    return "".join(chunks).encode("ascii")


def _digest(value):
    return sha256(canonical(value)).hexdigest()


def _receipt_digest(receipt):
    return "sha256:" + _digest({key: value for key, value in receipt.items() if key != "receipt_digest"})


def _exact(value, fields):
    _require(type(value) is dict and set(value) == fields)


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 256 and value == value.strip()
             and all(ord(char) >= 32 for char in value))


def _number(value):
    _require(type(value) in (int, float) and math.isfinite(value))
    _require(Fraction(value) == Fraction(float(value)), "input_precision_loss")


def _refs(value):
    _require(type(value) is list and 1 <= len(value) <= 16)
    for item in value:
        _text(item)
    _require(len(set(value)) == len(value))


def _frame(value):
    _exact(value, _FRAME)
    for item in value.values():
        _text(item)
    _require(value["unit"] == "s")


def _observation(value):
    _exact(value, _OBSERVATION)
    _number(value["device_time"])
    _frame(value["frame"])
    _text(value["evidence_id"])
    for key in ("received_at", "known_at"):
        point = value[key]
        if point is not None:
            _exact(point, {"value", "frame"})
            _number(point["value"])
            _frame(point["frame"])


def _model(value):
    _exact(value, _MODEL)
    _text(value["model_id"])
    for key in ("source_frame", "reference_frame"):
        _frame(value[key])
    for key in ("device_origin", "reference_origin", "skew", "offset"):
        _number(value[key])
    _require(type(value["valid_device_interval"]) is list and len(value["valid_device_interval"]) == 2)
    for item in value["valid_device_interval"]:
        _number(item)
    _refs(value["synchronization_evidence_ids"])


def _matrix(value):
    _require(type(value) is list and len(value) == 3)
    for row in value:
        _require(type(row) is list and len(row) == 3)
        for item in row:
            _number(item)


def _request(value):
    _exact(value, _REQUEST)
    _text(value["execution_id"])
    _observation(value["observation"])
    _model(value["model"])
    # This field is independent input; never derive it from the supplied model.
    _frame(value["expected_reference"])
    covariance = value["joint_covariance"]
    _exact(covariance, {"order", "matrix", "cross_covariance_policy", "evidence_ids"})
    _require(covariance["order"] == ["device_time", "skew", "offset"])
    _require(covariance["cross_covariance_policy"] in ("declared", "declared_zero"))
    _refs(covariance["evidence_ids"])
    _matrix(covariance["matrix"])
    if covariance["cross_covariance_policy"] == "declared_zero":
        _require(all(covariance["matrix"][i][j] == 0 for i in range(3) for j in range(3) if i != j))


def _numerical(value):
    _exact(value, _NUMERICAL)
    _observation(value["observation"])
    _model(value["model"])
    _frame(value["reference_frame"])
    _matrix(value["joint_covariance"])
    _require(type(value["jacobian"]) is list and len(value["jacobian"]) == 3)
    for item in value["jacobian"]:
        _number(item)
    for key in ("event_time_delta", "variance", "reference_origin", "event_time", "standard_uncertainty"):
        _number(value[key])
    _require(value["variance"] >= 0 and value["standard_uncertainty"] >= 0)
    _require(value["propagation"] == "first-order.v1" and value["operation_id"] == OPERATION
             and value["event_time_representation"] == "rounded_binary64_convenience")


def _receipt(value):
    _exact(value, _RECEIPT)
    _require(value["schema"] == RECEIPT_SCHEMA and value["profile"] == PROFILE
             and value["operation_id"] == OPERATION and value["outcome"] == "reconciled")
    _text(value["execution_id"])
    _request(value["request"])
    _numerical(value["numerical_result"])
    runtime_keys = _RUNTIME | ({'process_policy_ref'} if 'process_policy_ref' in value['runtime'] else set())
    _exact(value["runtime"], runtime_keys)
    if 'process_policy_ref' in value['runtime']:
        _require(type(value['runtime']['process_policy_ref']) is str and
                 re.fullmatch(r'sha256:[0-9a-f]{64}', value['runtime']['process_policy_ref']))
    _exact(value["runtime"]["dependencies"], {"numpy", "scipy"})
    for key in _RUNTIME - {"dependencies"}:
        _text(value["runtime"][key])
    for item in value["runtime"]["dependencies"].values():
        if item is not None:
            _text(item)
    _require(canonical(value["authority"]) == canonical(AUTHORITY))
    _require(canonical(value["comparison_policy"]) == canonical(COMPARISON_POLICY))
    _require(type(value["request_digest"]) is str and _HEX.fullmatch(value["request_digest"]))
    for key in ("numerical_result_id", "receipt_digest"):
        _require(type(value[key]) is str and _REF.fullmatch(value[key]))


def _spec(description, field=None):
    return {"description": description,
            "inputSchema": {"type": "object", "properties": {} if field is None else {field: {"type": "object"}},
                            "required": [] if field is None else [field], "additionalProperties": False},
            "outputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False}}


TOOLS = {
    "ncp_clocksync_describe": _spec("Describe bounded host-pinned TBRT mapping; no synchronization or authority grant."),
    "ncp_clocksync_reconcile": _spec("Map one declared event with an independently supplied expected reference, retained origin/delta and first-order covariance. Caller execution_id is preserved.", "request"),
    "ncp_clocksync_replay": _spec("Replay and compare every field of a complete receipt under the same runtime and comparison policy. No new occurrence identity or source authentication.", "receipt"),
}


def _tool_descriptions():
    return [{"name": name, **deepcopy(spec)} for name, spec in TOOLS.items()]


class _ClockAdapter(PinnedSubprocessAdapter):
    def __init__(self, *args, **kwargs):
        self.deadline = time.monotonic() + CALL_SECONDS
        super().__init__(*args, **kwargs)
        self._remaining()
        self.deadline = None

    def _remaining(self):
        remaining = PROCESS_SECONDS if self.deadline is None else min(PROCESS_SECONDS, self.deadline - time.monotonic())
        _require(remaining > 0, "clocksync_deadline_exceeded")
        return remaining

    def _git(self, *arguments):
        from .process_policy import require_companion_policy
        code, data = _bounded_process(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *arguments],
            cwd=self.repository_root, timeout=self._remaining(), limit=4 * 1024 * 1024,
            process_policy=require_companion_policy(self.process_policy))
        if code:
            raise AdapterRefusal("SOURCE_PIN_MISMATCH", "Cannot verify bound checkout")
        return data

    def _run(self, code, arguments, stdin=b""):
        self.timeout_seconds = self._remaining()
        return super()._run(_GUARD + code, arguments, stdin)


class ClockSyncHost:
    instructions = ("One host-pinned affine clock mapping and complete receipt replay only. "
                    "Caller clock frames and evidence are assertions. No clock control, synchronization, "
                    "UTC conversion, traceability certification, evidence/state admission or execution grant.")
    resource_templates = []

    def __init__(self, *, checkout, python_executable, principal, expected_python_sha256=None,
                 expected_python_version=None, expected_dependencies=None, process_policy=None):
        _require(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "unsupported_platform")
        _require(type(principal) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}", principal),
                 "invalid_host_binding")
        _require(Path(checkout).is_absolute() and Path(python_executable).is_absolute(), "invalid_host_binding")
        for name in ("calibrated-window-runtimes.json", "calibrated-observable-runtimes.json"):
            pin = json.loads(resources.files("ciw").joinpath(name).read_text())["tbrt"]
            _require(pin == {"revision": PIN, "module": "tbrt.clock", "source_root": "src"}, "clocksync_pin_mismatch")
        self._bound_principal = principal
        self._adapter = _ClockAdapter(checkout, PIN, "tbrt.clock", source_root="src",
            python_executable=python_executable, timeout_seconds=PROCESS_SECONDS, max_output_bytes=MAX_PROCESS_OUTPUT,
            expected_python_sha256=expected_python_sha256, expected_python_version=expected_python_version,
            expected_dependencies=expected_dependencies, process_policy=process_policy)
        self._guard_directory = TemporaryDirectory(prefix="odsp-clocksync-call-")
        self._closed = False

    def close(self):
        if self._closed:
            return
        lease = _Lease(Path(self._guard_directory.name) / "call.lease")
        try:
            _require(lease.try_acquire(), "clocksync_busy")
            self._closed = True
        finally:
            lease.release()
        self._guard_directory.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _runtime(self):
        identity = self._adapter.runtime_identity()
        self._adapter._remaining()
        runtime = {key: identity[key] for key in sorted(_RUNTIME)}
        if self._adapter.process_policy is not None:
            runtime['process_policy_ref'] = self._adapter.process_policy.identity()['policy_sha256']
        return runtime

    def _kernel(self, request, expected_runtime=None):
        _request(request)
        raw = canonical(request)
        _require(len(raw) <= MAX_ARGUMENT, "argument_budget_exceeded")
        runtime = self._runtime()
        if expected_runtime is not None:
            _require(canonical(expected_runtime) == canonical(runtime), "runtime_binding_mismatch")
        code, output = self._adapter._run(_BOOTSTRAP, [str(self._adapter.source_root)], raw)
        _require(not code, "clocksync_runtime_refused")
        _require(canonical(self._runtime()) == canonical(runtime), "clocksync_runtime_changed")
        response = _json(output)
        _require(type(response) is dict and response.get("kind") in ("reconciled", "refused"),
                 "clocksync_response_invalid")
        _exact(response, {"kind", "limits", "numerical_result"} if response["kind"] == "reconciled" else {"kind", "limits"})
        limits = {"cpu_seconds": CPU_SECONDS, "address_space_bytes": ADDRESS_BYTES,
                  "file_bytes": FILE_BYTES, "open_files": OPEN_FILES}
        _require(canonical(response["limits"]) == canonical(limits), "clocksync_response_invalid")
        _require(response["kind"] == "reconciled", "clock_model_refused")
        _numerical(response["numerical_result"])
        return response["numerical_result"], runtime

    def describe(self):
        return {"schema": "odsp.clocksync-description.v1", "profile": PROFILE,
                "operation_id": OPERATION, "runtime": self._runtime(), "authority": deepcopy(AUTHORITY),
                "request_contract": {"required": sorted(_REQUEST),
                    "observation_required": sorted(_OBSERVATION), "model_required": sorted(_MODEL),
                    "frame_required": sorted(_FRAME), "metadata_timepoint": "null_or_value_and_frame",
                    "covariance_required": ["order", "matrix", "cross_covariance_policy", "evidence_ids"],
                    "covariance_order": ["device_time", "skew", "offset"], "max_evidence_refs": 16,
                    "cross_covariance": ["declared", "declared_zero"], "additional_fields": "refused",
                    "expected_reference": "independently_supplied_required_destination_frame",
                    "number_contract": "finite_binary64; reject_inexact_integer_conversion",
                    "input_precision": "cannot_recover_precision_already_lost_before_submission",
                    "validation_owner": "closed_profile_shape_then_pinned_tbrt_scientific_checks"},
                "limits": {"argument_bytes": MAX_ARGUMENT, "mcp_envelope_bytes": MAX_RESPONSE,
                    "process_output_bytes": MAX_PROCESS_OUTPUT, "call_wall_seconds": CALL_SECONDS,
                    "child_wall_seconds": PROCESS_SECONDS, "kernel_cpu_seconds": CPU_SECONDS,
                    "kernel_address_space_bytes": ADDRESS_BYTES, "kernel_file_bytes": FILE_BYTES,
                    "kernel_open_files": OPEN_FILES, "concurrent_calls": 1},
                "comparison_policy": deepcopy(COMPARISON_POLICY),
                "execution_identity": "caller_execution_id_preserved; replay_reuses_complete_request",
                "idempotency_registry": "none; caller_occurrence_ids_not_authenticated",
                "time_representation": "retain_reference_origin_and_event_time_delta; event_time_is_rounded_convenience",
                "uncertainty_scope": "first_order_with_fixed_origins; not_exact_transformed_mean_or_distribution",
                "applicability": "nominal_device_timestamp_only; not_uncertainty_support",
                "process_isolation": "pinned_interpreter_and_import_root; no_environment_or_network_sandbox",
                "qualification": "local_POSIX_only; cross_platform_bitwise_equality_not_promised"}

    def reconcile(self, request):
        numerical, runtime = self._kernel(request)
        receipt = {"schema": RECEIPT_SCHEMA, "profile": PROFILE, "operation_id": OPERATION,
            "execution_id": request["execution_id"], "request": deepcopy(request), "request_digest": _digest(request),
            "runtime": runtime, "comparison_policy": deepcopy(COMPARISON_POLICY), "outcome": "reconciled",
            "numerical_result": numerical, "numerical_result_id": "sha256:" + _digest(numerical),
            "authority": deepcopy(AUTHORITY)}
        receipt["receipt_digest"] = _receipt_digest(receipt)
        _require(len(canonical({"name": "ncp_clocksync_replay", "arguments": {"receipt": receipt}})) <= MAX_ARGUMENT,
                 "receipt_replay_budget_exceeded")
        return receipt

    def replay(self, receipt):
        result = {"schema": "odsp.clocksync-replay.v1", "profile": PROFILE, "authority": deepcopy(AUTHORITY),
                  "outcome": "invalid_contract", "canonical_receipt_match": False,
                  "request_digest_match": False, "numerical_digest_match": False, "receipt_digest_match": False}
        try:
            _receipt(receipt)
        except (ClockSyncRefusal, ValueError, TypeError, KeyError, OverflowError):
            return result
        result["request_digest_match"] = receipt["request_digest"] == _digest(receipt["request"])
        result["numerical_digest_match"] = receipt["numerical_result_id"] == "sha256:" + _digest(receipt["numerical_result"])
        result["receipt_digest_match"] = receipt["receipt_digest"] == _receipt_digest(receipt)
        try:
            numerical, runtime = self._kernel(receipt["request"], expected_runtime=receipt["runtime"])
        except ClockSyncRefusal as exc:
            if str(exc) in {"runtime_binding_mismatch", "clock_model_refused"}:
                result["outcome"] = "runtime_mismatch" if str(exc) == "runtime_binding_mismatch" else "request_refused"
                return result
            raise
        expected = {**deepcopy(receipt), "execution_id": receipt["request"]["execution_id"],
                    "request_digest": _digest(receipt["request"]), "numerical_result": numerical,
                    "numerical_result_id": "sha256:" + _digest(numerical), "runtime": runtime}
        expected["receipt_digest"] = _receipt_digest(expected)
        result["canonical_receipt_match"] = canonical(receipt) == canonical(expected)
        result["outcome"] = "verified" if result["canonical_receipt_match"] else "mismatch"
        result["runtime"] = runtime
        result["comparison_policy"] = deepcopy(COMPARISON_POLICY)
        result["replayed_execution_id"] = expected["execution_id"]
        result["replayed_receipt_digest"] = expected["receipt_digest"]
        return result

    def call(self, name, arguments):
        try:
            return self._call(name, arguments)
        except OSError as exc:
            raise ClockSyncRefusal("clocksync_runtime_io_refused") from exc

    def _call(self, name, arguments):
        lease = _Lease(Path(self._guard_directory.name) / "call.lease")
        try:
            _require(not self._closed, "clocksync_host_closed")
            _require(type(name) is str and name in TOOLS, "unknown_clocksync_operation")
            _require(len(canonical({"name": name, "arguments": arguments})) <= MAX_ARGUMENT, "argument_budget_exceeded")
            _shape(arguments, TOOLS[name]["inputSchema"])
            _require(lease.try_acquire(), "clocksync_busy")
            # close() may win the lease after the earlier fast-path check.
            # Admission must observe its closed state while owning the lease.
            _require(not self._closed, "clocksync_host_closed")
            self._adapter.deadline = time.monotonic() + CALL_SECONDS
            args = deepcopy(arguments)
            value = (self.describe() if name == "ncp_clocksync_describe" else
                     self.reconcile(args["request"]) if name == "ncp_clocksync_reconcile" else
                     self.replay(args["receipt"]))
            response = {"status": "ok", "data": value}
            self.mcp_result(name, response)
            self._adapter._remaining()
            return response
        except ClockSyncRefusal as exc:
            return {"status": "refused", "reason": str(exc)}
        except AdapterRefusal as exc:
            return {"status": "refused", "reason": "runtime_cleanup_uncertain" if
                    exc.code == "RUNTIME_CLEANUP_UNCERTAIN" else "clocksync_runtime_refused"}
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return {"status": "refused", "reason": "invalid_arguments"}
        finally:
            if lease.fd is not None:
                self._adapter.deadline = None
            lease.release()

    @staticmethod
    def mcp_result(name, value):
        envelope = {"content": [{"type": "text", "text": canonical(value).decode()}],
                    "structuredContent": value, "isError": value.get("status") != "ok"}
        try:
            _require(len(canonical(envelope)) <= MAX_RESPONSE, "response_budget_exceeded")
        except ClockSyncRefusal as exc:
            raise ClockSyncRefusal("response_budget_exceeded") from exc
        return envelope

    def descriptions(self):
        return _tool_descriptions()

    def list_resources(self):
        return []

    def read_resource(self, uri):
        raise ClockSyncRefusal("resource_unavailable")


def invoke_clocksync(host, name, arguments):
    if not isinstance(host, ClockSyncHost):
        raise ClockSyncRefusal("clocksync_host_required")
    result = host.call(name, arguments)
    if result["status"] != "ok":
        raise ClockSyncRefusal(result["reason"])
    return result["data"]


def open_clocksync(**bindings):
    """Trusted launcher factory; discovery and tool payloads never bind a host."""
    return ClockSyncHost(**bindings)


def profile_manifest():
    """Static discovery only: no checkout, interpreter, probe or scientific call."""
    return {"profile_id": PROFILE, "adapter_contract_commit": PIN, "operation_id": OPERATION,
            "host_factory": "ciw.clocksync_profile.open_clocksync", "service": "ciw.services.invoke",
            "source_binding_verification": "caller_assertion_only", "authorizes_execution": False,
            "authorizes_state_admission": False, "qualification": "not_performed_by_discovery",
            "authority": deepcopy(AUTHORITY), "comparison_policy": deepcopy(COMPARISON_POLICY),
            "tools": _tool_descriptions()}
