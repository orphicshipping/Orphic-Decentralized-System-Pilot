"""Bounded NSR affine reconciliation through the existing pinned process boundary.

The kernel is imported only by a host-selected child interpreter. Caller source
bindings remain assertions; canonical replay is not source authentication,
evidence admission, physical qualification or an execution grant.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib import resources
import json
import math
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import PinnedSubprocessAdapter, _json
from .agent_tools import _shape
from .coordinator import _Lease

PROFILE = "nsr.affine-exact.v1"
PIN = "daf43fc870ba926289c3bc4908db784691c7addd"
OPERATION = "cbsr.affine-exact.v1"
MAX_ARGUMENT = 65536
MAX_RESPONSE = 262144
PROCESS_TIMEOUT = 5.0
CPU_SECONDS = 3
ADDRESS_SPACE = 256 * 1024 * 1024
FILE_BYTES = 1024 * 1024
OPEN_FILES = 32
STATUS = frozenset({"accepted", "held", "refused"})
CLAIMS = {"physical_validity": False, "fault_isolation": False,
          "evidence_admission": False, "stability": False}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")
_RECEIPT_KEYS = frozenset({"schema", "operation_id", "execution_id", "input_state_id", "output_state_id",
    "constraint_id", "request_digest", "request", "evidence_refs", "numerical_result_id",
    "source_binding_verification", "status", "reason", "candidate", "reconciled", "correction",
    "residual_pre", "residual_post", "residual_covariance_pre", "residual_covariance_post",
    "normalized_residual", "residual_rank", "condition_inf_independent_rows", "feasible",
    "rational_constraint_residual_zero", "output_constraint_residual_zero", "arithmetic", "claims", "result_id"})

# Like calibrated_observable._invoke, this source-controlled bootstrap calls the
# existing function and returns its receipt. It does not reinterpret CLI exit 3
# as a failed process: held/refused are valid scientific receipts. No input can
# choose code, modules, paths, interpreter, limits or a different revision.
_BOOTSTRAP = r'''
import json, pathlib, resource, sys
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import cbsr
if not pathlib.Path(cbsr.__file__).resolve().is_relative_to(root):
    raise RuntimeError('pinned import root mismatch')
from cbsr import ContractError, reconcile_affine_exact
request = json.loads(sys.stdin.buffer.read(65537))
try:
    receipt = reconcile_affine_exact(request)
    result = {'kind': 'receipt', 'receipt': receipt}
except ContractError:
    result = {'kind': 'invalid_contract'}
result['limits'] = {'cpu_seconds': resource.getrlimit(resource.RLIMIT_CPU)[0],
    'address_space_bytes': resource.getrlimit(resource.RLIMIT_AS)[0],
    'file_bytes': resource.getrlimit(resource.RLIMIT_FSIZE)[0],
    'open_files': resource.getrlimit(resource.RLIMIT_NOFILE)[0]}
print(json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False))
'''


class RecompilerRefusal(ValueError):
    """Sanitized transport/binding refusal, distinct from scientific status."""


def canonical(value):
    def check(item, depth=0):
        if depth > 24:
            raise ValueError("JSON depth limit")
        if item is None or type(item) in (bool, int):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("finite JSON required")
        elif type(item) is str:
            if len(item) > MAX_RESPONSE:
                raise ValueError("JSON string limit")
        elif type(item) is list and len(item) <= 4096:
            for child in item:
                check(child, depth + 1)
        elif type(item) is dict and len(item) <= 1024 and all(type(k) is str for k in item):
            for key, child in item.items():
                check(key, depth + 1)
                check(child, depth + 1)
        else:
            raise ValueError("bounded JSON required")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _digest(value):
    return sha256(canonical(value)).hexdigest()


def _spec(description, field=None):
    return {"description": description,
            "inputSchema": {"type": "object", "properties": {} if field is None else {field: {"type": "object"}},
                            "required": [] if field is None else [field], "additionalProperties": False},
            "outputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False}}


TOOLS = {
    "ncp_recompiler_describe": _spec("Describe the host-pinned bounded NSR affine profile; no scientific execution or grant."),
    "ncp_recompiler_reconcile": _spec("Reconcile a closed NSR affine request in the pinned subprocess. Preserve the caller's execution_id. Accepted, held, refused and invalid_contract are distinct; no evidence or state admission.", "request"),
    "ncp_recompiler_verify_receipt": _spec("Replay a complete NSR receipt's retained request and compare every canonical field and digest. Receipt IDs alone are insufficient. This does not authenticate asserted sources.", "receipt"),
}


def _tool_descriptions():
    return [{"name": name, **deepcopy(spec)} for name, spec in TOOLS.items()]


def _receipt_contract(receipt):
    if (type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS or
            receipt["schema"] != "cbsr.affine-exact-result.v1" or receipt["operation_id"] != OPERATION or
            receipt["status"] not in STATUS or type(receipt["request"]) is not dict or
            type(receipt["request_digest"]) is not str or not _HEX.fullmatch(receipt["request_digest"]) or
            type(receipt["result_id"]) is not str or not _REF.fullmatch(receipt["result_id"]) or
            type(receipt["numerical_result_id"]) is not str or not _REF.fullmatch(receipt["numerical_result_id"]) or
            receipt["source_binding_verification"] != "caller_assertion_only" or
            canonical(receipt["claims"]) != canonical(CLAIMS)):
        raise RecompilerRefusal("invalid_receipt_contract")


class RecompilerHost:
    instructions = ("Host-bound NSR affine reconciliation and canonical receipt replay only. "
                    "Caller source bindings are unverified assertions. No state/evidence admission, "
                    "physical qualification, source authentication or new execution authority.")
    resource_templates = []

    def __init__(self, *, checkout, python_executable, principal, expected_python_sha256=None,
                 expected_python_version=None, expected_dependencies=None, process_policy=None):
        if (os.name != "posix" or type(principal) is not str or not _PRINCIPAL.fullmatch(principal)
                or not Path(checkout).is_absolute() or not Path(python_executable).is_absolute()):
            raise RecompilerRefusal("invalid_or_unsupported_host_binding")
        pin = json.loads(resources.files("ciw").joinpath("calibrated-observable-runtimes.json").read_text())["cbsr"]
        if pin != {"revision": PIN, "module": "cbsr", "source_root": "src"}:
            raise RecompilerRefusal("recompiler_pin_mismatch")
        self._bound_principal = principal
        self._adapter = PinnedSubprocessAdapter(checkout, PIN, "cbsr", source_root="src",
            python_executable=python_executable, timeout_seconds=PROCESS_TIMEOUT, max_output_bytes=MAX_RESPONSE,
            expected_python_sha256=expected_python_sha256, expected_python_version=expected_python_version,
            expected_dependencies=expected_dependencies, process_policy=process_policy)
        # Reuse the interruption-safe lease owner; a failed acquisition cannot
        # release another call's descriptor. This is ephemeral admission state,
        # not a result store, a cross-process deployment lock or a new grant.
        self._lease_directory = TemporaryDirectory(prefix="odsp-nsr-call-")

    def _runtime(self):
        identity = self._adapter.runtime_identity()
        runtime = {key: identity[key] for key in ("schema", "adapter_version", "revision", "source_tree", "module",
                                               "source_root", "python_sha256", "python_version", "dependencies")}
        if self._adapter.process_policy is not None:
            runtime['process_policy_ref'] = self._adapter.process_policy.identity()['policy_sha256']
        return runtime

    def _kernel(self, request):
        raw = canonical(request)
        if len(raw) > MAX_ARGUMENT:
            raise RecompilerRefusal("argument_budget_exceeded")
        runtime = self._runtime()
        code, output = self._adapter._run(_BOOTSTRAP, [str(self._adapter.source_root)], raw)
        if code:
            raise RecompilerRefusal("recompiler_runtime_refused")
        if self._runtime() != runtime:
            raise RecompilerRefusal("recompiler_runtime_changed")
        result = _json(output)
        expected_limits = {"cpu_seconds": CPU_SECONDS, "address_space_bytes": ADDRESS_SPACE,
                           "file_bytes": FILE_BYTES, "open_files": OPEN_FILES}
        if (type(result) is not dict or result.get("limits") != expected_limits or
                set(result) != ({"kind", "limits", "receipt"} if result.get("kind") == "receipt" else {"kind", "limits"})):
            raise RecompilerRefusal("recompiler_response_invalid")
        if result["kind"] == "invalid_contract":
            return None, runtime
        if result["kind"] != "receipt":
            raise RecompilerRefusal("recompiler_response_invalid")
        receipt = result["receipt"]
        _receipt_contract(receipt)
        if (canonical(receipt["request"]) != raw or receipt["request_digest"] != _digest(request) or
                receipt["result_id"] != "sha256:" + _digest({k: v for k, v in receipt.items() if k != "result_id"})):
            raise RecompilerRefusal("recompiler_response_invalid")
        return receipt, runtime

    @staticmethod
    def _claims():
        return {"profile": PROFILE, "source_binding_verification": "caller_assertion_only",
                "authorizes_execution": False, "authorizes_state_admission": False,
                "source_authenticated": False, "physical_qualification": "not_performed"}

    def describe(self):
        return {"schema": "odsp.nsr-description.v1", **self._claims(), "runtime": self._runtime(),
                "operation_id": OPERATION, "max_dimension": 16,
                "request_contract": {
                    "required": ["state_id", "estimate", "covariance", "state_labels", "state_units", "frame_ref",
                                 "constraints", "crosscov_policy", "max_normalized_residual", "evidence_refs", "execution_id"],
                    "optional_paired": ["source_result_id", "source_result_digest"],
                    "constraint_required": ["constraint_id", "coefficients", "rhs", "row_units", "coefficient_policy"],
                    "additional_fields": "invalid_contract", "numerical_contract": "finite_exactly_representable_binary64",
                    "validation_owner": "pinned cbsr.affine; no request repair or normalization"},
                "limits": {"argument_bytes": MAX_ARGUMENT, "mcp_envelope_bytes": MAX_RESPONSE,
                           "child_wall_seconds": PROCESS_TIMEOUT, "kernel_cpu_seconds": CPU_SECONDS,
                           "kernel_address_space_bytes": ADDRESS_SPACE, "kernel_file_bytes": FILE_BYTES,
                           "kernel_open_files": OPEN_FILES, "concurrent_calls": 1},
                "execution_identity": "caller_execution_id_preserved; exact replay reuses the complete request",
                "idempotency_registry": "none; caller occurrence IDs are not independently authenticated",
                "process_isolation": "pinned interpreter and import root; no environment or network sandbox",
                "verification": "full_canonical_receipt_replay; no independent source authentication",
                "qualification": "local_POSIX_only; native_Windows_and_live_upstream_not_established"}

    def reconcile(self, request):
        receipt, runtime = self._kernel(request)
        # Every issued receipt must be admissible to the full verification tool
        # through the common principal boundary, including its call envelope.
        if receipt is not None and len(canonical({"name": "ncp_recompiler_verify_receipt",
                "arguments": {"receipt": receipt}})) > MAX_ARGUMENT:
            raise RecompilerRefusal("receipt_verification_budget_exceeded")
        return {"schema": "odsp.nsr-reconciliation.v1", **self._claims(), "runtime": runtime,
                "outcome": "invalid_contract" if receipt is None else receipt["status"],
                "receipt": receipt, "reason": "invalid_contract" if receipt is None else receipt["reason"]}

    def verify_receipt(self, receipt):
        result = {"schema": "odsp.nsr-verification.v1", **self._claims(),
                  "outcome": "invalid_contract", "canonical_receipt_match": False,
                  "request_digest_match": False, "result_digest_match": False,
                  "original_status": None}
        try:
            _receipt_contract(receipt)
        except (ValueError, TypeError, KeyError):
            return result
        result["original_status"] = receipt["status"]
        result["request_digest_match"] = receipt["request_digest"] == _digest(receipt["request"])
        result["result_digest_match"] = receipt["result_id"] == "sha256:" + _digest({k: v for k, v in receipt.items() if k != "result_id"})
        expected, runtime = self._kernel(receipt["request"])
        result["runtime"] = runtime
        if expected is None:
            return result
        result["canonical_receipt_match"] = canonical(expected) == canonical(receipt)
        result["outcome"] = "verified" if result["canonical_receipt_match"] else "mismatch"
        result["replayed_execution_id"] = expected["execution_id"]
        result["replayed_result_id"] = expected["result_id"]
        return result

    def call(self, name, arguments):
        try:
            return self._call(name, arguments)
        except OSError as exc:
            # Cleanup may fail after a calculation returned. Preserve the full
            # primary/cleanup chain for trusted SDK diagnostics, but do not put
            # OS paths or exception text in the public error message. A cleanup
            # failure takes precedence; any cancellation stays in its context.
            raise RecompilerRefusal("recompiler_runtime_io_refused") from exc

    def _call(self, name, arguments):
        lease = _Lease(Path(self._lease_directory.name) / "call.lease")
        try:
            if type(name) is not str or name not in TOOLS:
                raise RecompilerRefusal("unknown_recompiler_operation")
            if len(canonical({"name": name, "arguments": arguments})) > MAX_ARGUMENT:
                raise RecompilerRefusal("argument_budget_exceeded")
            _shape(arguments, TOOLS[name]["inputSchema"])
            if not lease.try_acquire():
                raise RecompilerRefusal("recompiler_busy")
            args = deepcopy(arguments)
            value = (self.describe() if name == "ncp_recompiler_describe" else
                     self.reconcile(args["request"]) if name == "ncp_recompiler_reconcile" else
                     self.verify_receipt(args["receipt"]))
            response = {"status": "ok", "data": value}
            self.mcp_result(name, response)
            return response
        except RecompilerRefusal as exc:
            return {"status": "refused", "reason": str(exc)}
        except AdapterRefusal as exc:
            return {"status": "refused", "reason": "runtime_cleanup_uncertain" if
                    exc.code == "RUNTIME_CLEANUP_UNCERTAIN" else "recompiler_runtime_refused"}
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return {"status": "refused", "reason": "invalid_arguments"}
        finally:
            lease.release()

    @staticmethod
    def mcp_result(name, value):
        envelope = {"content": [{"type": "text", "text": canonical(value).decode()}],
                    "structuredContent": value, "isError": value.get("status") != "ok"}
        if len(canonical(envelope)) > MAX_RESPONSE:
            raise RecompilerRefusal("response_budget_exceeded")
        return envelope

    def descriptions(self):
        return _tool_descriptions()

    def list_resources(self):
        return []

    def read_resource(self, uri):
        raise RecompilerRefusal("resource_unavailable")


def invoke_recompiler(host, name, arguments):
    if not isinstance(host, RecompilerHost):
        raise RecompilerRefusal("recompiler_host_required")
    result = host.call(name, arguments)
    if result["status"] != "ok":
        raise RecompilerRefusal(result["reason"])
    return result["data"]


def open_recompiler(**bindings):
    """Trusted launcher only. Payloads cannot create or replace this binding."""
    return RecompilerHost(**bindings)


def profile_manifest():
    return {"profile_id": PROFILE, "operation_id": OPERATION, "adapter_contract_commit": PIN,
            "host_factory": "ciw.recompiler_profile.open_recompiler", "service": "ciw.services.invoke",
            "source_binding_verification": "caller_assertion_only", "authorizes_execution": False,
            "authorizes_state_admission": False, "qualification": "not_performed_by_discovery",
            "tools": _tool_descriptions()}
