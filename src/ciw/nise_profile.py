"""Bounded, host-pinned NISE candidate scoping and complete receipt verification.

Selection belongs to the pinned child implementation. Catalog attributes and
question text remain inert caller assertions, never runtime bindings or grants.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import PinnedSubprocessAdapter, RuntimeCleanupUncertain, _bounded_process, _guard_cleanup
from .agent_tools import _shape
from .coordinator import _Lease
from .nise_contract import canonical, parse, validate_query, validate_schematic

PROFILE = "nise.schematic.v1"
PIN = "81908ddbe817018af5d9887a6bdd9cce3e3eafc6"
OPERATION = "inference.schematic.construct.v1"
RECEIPT_SCHEMA = "odsp.nise-receipt.v1"
MAX_ARGUMENT = 65536
MAX_RESPONSE = 262144
JSONRPC_RESERVE = 1024
MAX_PROCESS_OUTPUT = 131072
MAX_GIT_OUTPUT = 4 * 1024 * 1024
PROCESS_SECONDS = 5.0
CALL_SECONDS = 20.0
CPU_SECONDS = 3
ADDRESS_BYTES = 256 * 1024 * 1024
FILE_BYTES = 1024 * 1024
OPEN_FILES = 32
AUTHORITY = {
    "source_binding_verification": "caller_assertion_only", "source_authenticated": False,
    "authorizes_execution": False, "authorizes_state_admission": False,
    "evidence_admission": False, "physical_truth_established": False,
    "causal_proof": False, "provider_execution": False, "candidate_scoping_only": True,
}
_RUNTIME = {"schema", "adapter_version", "revision", "source_tree", "module", "source_root",
            "python_sha256", "python_version", "dependencies"}
_RECEIPT = {"schema", "profile", "operation_id", "execution_id", "request", "request_digest",
            "schematic", "runtime", "comparison_policy", "diagnostics", "authority", "receipt_digest"}
_PARTITIONS = ("V_q", "E_q", "M_q", "C_q", "O_q", "P_q")
_INSPECTION = {"schema", "schematic_id", "query_id", "catalog_id", "counts", "unresolved_capabilities",
               "frontier_count", "candidate_structure", "physical_truth_established", "execution_authority"}
_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")

_GUARD = r'''
import resource
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
'''
_BOOTSTRAP = r'''
import json, pathlib, sys
action, directory = sys.argv[1:3]
root = pathlib.Path(directory).resolve()
sys.path.insert(0, str(root))
import nise, nise.compiler, nise.contracts
if any(not pathlib.Path(module.__file__).resolve().is_relative_to(root)
       for module in (nise, nise.compiler, nise.contracts)):
    raise RuntimeError('pinned import root mismatch')
from nise.compiler import compile_schematic, inspect_schematic
from nise.contracts import validate_catalog, validate_query, validate_schematic
request = json.loads(sys.stdin.buffer.read(65537))
try:
    if action == 'validate':
        data = {'catalog': validate_catalog(request['catalog']), 'query': validate_query(request['query'])}
    elif action == 'compile':
        data = compile_schematic(request['catalog'], request['query'])
    elif action == 'inspect':
        data = inspect_schematic(validate_schematic(request['schematic']))
    elif action == 'verify':
        # Validate the submitted object independently of recompilation. Its
        # digest alone cannot stand in for its complete structural contract.
        validate_schematic(request['schematic'])
        data = compile_schematic(request['catalog'], request['query'])
    else:
        raise RuntimeError('unsupported fixed action')
except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
    result = {'kind': 'refused'}
else:
    result = {'kind': 'ok', 'data': data}
result['limits'] = {'cpu_seconds': resource.getrlimit(resource.RLIMIT_CPU)[0],
    'address_space_bytes': resource.getrlimit(resource.RLIMIT_AS)[0],
    'file_bytes': resource.getrlimit(resource.RLIMIT_FSIZE)[0],
    'open_files': resource.getrlimit(resource.RLIMIT_NOFILE)[0]}
print(json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False))
'''
COMPARISON_POLICY = {
    "comparison_id": "odsp.nise.full-recompile.v1", "comparison": "complete_canonical_json_exact",
    "scope": "same_bound_runtime_and_complete_catalog_query", "cross_platform_bitwise_guarantee": False,
    "dependency_identity": "numpy_scipy_version_labels_only_not_binary_attestation",
    "bridge_sha256": sha256((_GUARD + _BOOTSTRAP).encode()).hexdigest(),
}


class NiseRefusal(ValueError):
    """Sanitized profile refusal; underlying SDK diagnostic chains are retained."""


def _require(condition, reason="invalid_nise_contract"):
    if not condition:
        raise NiseRefusal(reason)


def _exact(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _digest(value):
    return sha256(canonical(value)).hexdigest()


def _receipt_digest(value):
    return "sha256:" + _digest({key: item for key, item in value.items() if key != "receipt_digest"})


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 256 and value == value.strip()
             and all(ord(char) >= 32 for char in value))


def _inputs(value):
    _exact(value, {"catalog", "query"})
    _require(type(value["catalog"]) is dict)
    # Catalog-specific checks remain with the pinned upstream validator. Generic
    # byte/depth/numeric/Unicode limits apply before any probe or child launch.
    canonical(value)
    validate_query(value["query"])


def _request(value):
    _exact(value, {"execution_id", "catalog", "query"})
    _text(value["execution_id"])
    _inputs({key: value[key] for key in ("catalog", "query")})


def _diagnostics(schematic):
    selected = {node["node_id"] for key in _PARTITIONS if key != "E_q" for node in schematic[key]}
    return {"focus_displaced": sorted(set(schematic["query"]["focus_node_ids"]) - selected),
            "selected_frontier": [deepcopy(row) for row in schematic["frontier"] if row["node_id"] in selected]}


def _receipt(value):
    _exact(value, _RECEIPT)
    _require(value["schema"] == RECEIPT_SCHEMA and value["profile"] == PROFILE and value["operation_id"] == OPERATION)
    _text(value["execution_id"])
    _request(value["request"])
    validate_schematic(value["schematic"])
    runtime_keys = _RUNTIME | ({'process_policy_ref'} if 'process_policy_ref' in value['runtime'] else set())
    _exact(value["runtime"], runtime_keys)
    if 'process_policy_ref' in value['runtime']:
        _require(type(value['runtime']['process_policy_ref']) is str and
                 re.fullmatch(r'sha256:[0-9a-f]{64}', value['runtime']['process_policy_ref']))
    for key in _RUNTIME - {"dependencies"}:
        _text(value["runtime"][key])
    _exact(value["runtime"]["dependencies"], {"numpy", "scipy"})
    for item in value["runtime"]["dependencies"].values():
        if item is not None:
            _text(item)
    _require(canonical(value["authority"]) == canonical(AUTHORITY))
    _require(canonical(value["comparison_policy"]) == canonical(COMPARISON_POLICY))
    _require(canonical(value["diagnostics"]) == canonical(_diagnostics(value["schematic"])))
    _require(type(value["request_digest"]) is str and _HEX.fullmatch(value["request_digest"]))
    _require(type(value["receipt_digest"]) is str and _REF.fullmatch(value["receipt_digest"]))


def _spec(description, fields=()):
    return {"description": description,
        "inputSchema": {"type": "object", "properties": {name: {"type": "object"} for name in fields},
                        "required": list(fields), "additionalProperties": False},
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}}


TOOLS = {
    "ncp_nise_describe": _spec("Describe bounded host-pinned NISE candidate scoping; no execution authority or binding from discovery."),
    "ncp_nise_validate": _spec("Validate a complete catalog and query against their pinned structural contracts; this does not compile or prove query focus applicability.", ("catalog", "query")),
    "ncp_nise_compile": _spec("Compile one explicit catalog/query into a candidate inference schematic; retain the caller execution_id and expose focus displacement/frontier overlap diagnostics.", ("request",)),
    "ncp_nise_inspect": _spec("Validate and inspect a complete supplied schematic without authenticating or recompiling its source catalog; no execution or admission.", ("schematic",)),
    "ncp_nise_verify": _spec("Validate every receipt field and submitted schematic, then recompile its complete catalog/query under matching runtime and comparison policy; no fresh occurrence IDs.", ("receipt",)),
}


def _tool_descriptions():
    return [{"name": name, **deepcopy(spec)} for name, spec in TOOLS.items()]


class _NiseAdapter(PinnedSubprocessAdapter):
    def __init__(self, *args, **kwargs):
        self.deadline = time.monotonic() + CALL_SECONDS
        super().__init__(*args, **kwargs)
        self._remaining()
        self.deadline = None

    def _remaining(self):
        remaining = PROCESS_SECONDS if self.deadline is None else min(PROCESS_SECONDS, self.deadline - time.monotonic())
        _require(remaining > 0, "nise_deadline_exceeded")
        return remaining

    @_guard_cleanup
    def _git(self, *arguments):
        code, output = _bounded_process(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *arguments],
            cwd=self.repository_root, timeout=self._remaining(), limit=MAX_GIT_OUTPUT,
            process_policy=self.process_policy)
        if code:
            raise AdapterRefusal("SOURCE_PIN_MISMATCH", "Cannot verify bound checkout")
        return output

    @_guard_cleanup
    def _run(self, code, arguments, stdin=b""):
        self.timeout_seconds = self._remaining()
        return super()._run(_GUARD + code, arguments, stdin)


class NiseHost:
    instructions = ("Host-pinned deterministic NISE candidate scoping only. Question text and attributes are inert. "
        "No natural-language inference, provider execution, physical truth, evidence admission, state control, retention or new authority.")
    resource_templates = []
    strict_mcp_input = True

    def __init__(self, *, checkout, python_executable, principal, expected_python_sha256=None,
                 expected_python_version=None, expected_dependencies=None, process_policy=None):
        _require(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "unsupported_platform")
        _require(type(principal) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}", principal), "invalid_host_binding")
        _require(Path(checkout).is_absolute() and Path(python_executable).is_absolute(), "invalid_host_binding")
        self._bound_principal = principal
        self._adapter = _NiseAdapter(checkout, PIN, "nise.cli", source_root="src", python_executable=python_executable,
            timeout_seconds=PROCESS_SECONDS, max_output_bytes=MAX_PROCESS_OUTPUT,
            expected_python_sha256=expected_python_sha256, expected_python_version=expected_python_version,
            expected_dependencies=expected_dependencies, process_policy=process_policy)
        self._guard_directory = TemporaryDirectory(prefix="odsp-nise-call-")
        self._closed = False

    def close(self):
        if self._closed:
            return
        lease = _Lease(Path(self._guard_directory.name) / "call.lease")
        try:
            _require(lease.try_acquire(), "nise_busy")
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

    def _upstream(self, action, inputs, expected_runtime=None):
        raw = canonical(inputs)
        _require(len(raw) <= MAX_ARGUMENT, "argument_budget_exceeded")
        runtime = self._runtime()
        if expected_runtime is not None:
            _require(canonical(expected_runtime) == canonical(runtime), "runtime_binding_mismatch")
        code, output = self._adapter._run(_BOOTSTRAP, [action, str(self._adapter.source_root)], raw)
        _require(not code, "nise_runtime_refused")
        _require(canonical(self._runtime()) == canonical(runtime), "nise_runtime_changed")
        response = parse(output, limit=MAX_PROCESS_OUTPUT)
        _require(type(response) is dict and response.get("kind") in ("ok", "refused"), "nise_response_invalid")
        _exact(response, {"kind", "data", "limits"} if response["kind"] == "ok" else {"kind", "limits"})
        limits = {"cpu_seconds": CPU_SECONDS, "address_space_bytes": ADDRESS_BYTES,
                  "file_bytes": FILE_BYTES, "open_files": OPEN_FILES}
        _require(canonical(response["limits"]) == canonical(limits), "nise_response_invalid")
        _require(response["kind"] == "ok", "nise_contract_refused")
        return response["data"], runtime

    def describe(self):
        return {"schema": "odsp.nise-description.v1", **profile_manifest(), "runtime": self._runtime(),
            "input_contract": {"catalog": "nise.system-catalog.v1", "query": "nise.query.v1",
                "schematic": "nise.inference-schematic.v1", "compile_request": ["execution_id", "catalog", "query"],
                "query_selection": "explicit_focus_capabilities_hops_budget_hypothesis_policy",
                "question_text": "retained_only; not_NLP_selection",
                "diagnostics": "focus_displaced_and_selected_frontier_reported_without_upstream_repair"}}

    def validate(self, catalog, query):
        inputs = {"catalog": catalog, "query": query}
        _inputs(inputs)
        data, runtime = self._upstream("validate", inputs)
        _require(canonical(data) == canonical(inputs), "nise_response_invalid")
        return {"schema": "odsp.nise-validation.v1", "profile": PROFILE, "outcome": "valid",
            "catalog_digest": "sha256:" + _digest(catalog), "query_digest": "sha256:" + _digest(query),
            "validation_scope": "separate_catalog_and_query_structural_contracts", "runtime": runtime,
            "authority": deepcopy(AUTHORITY)}

    @staticmethod
    def _issued(request, schematic, runtime):
        validate_schematic(schematic)
        _require(canonical(schematic["query"]) == canonical(request["query"])
                 and schematic["catalog_id"] == request["catalog"]["catalog_id"]
                 and schematic["catalog_digest"] == "sha256:" + _digest(request["catalog"]), "nise_response_invalid")
        receipt = {"schema": RECEIPT_SCHEMA, "profile": PROFILE, "operation_id": OPERATION,
            "execution_id": request["execution_id"], "request": deepcopy(request), "request_digest": _digest(request),
            "schematic": deepcopy(schematic), "runtime": deepcopy(runtime), "comparison_policy": deepcopy(COMPARISON_POLICY),
            "diagnostics": _diagnostics(schematic), "authority": deepcopy(AUTHORITY)}
        receipt["receipt_digest"] = _receipt_digest(receipt)
        _require(len(canonical({"name": "ncp_nise_verify", "arguments": {"receipt": receipt}})) <= MAX_ARGUMENT,
                 "receipt_verification_budget_exceeded")
        return receipt

    def compile(self, request):
        _request(request)
        schematic, runtime = self._upstream("compile", {key: request[key] for key in ("catalog", "query")})
        return self._issued(request, schematic, runtime)

    def inspect(self, schematic):
        schematic = validate_schematic(schematic)
        inspection, runtime = self._upstream("inspect", {"schematic": schematic})
        _exact(inspection, _INSPECTION)
        expected = {"schema": "nise.schematic-inspection.v1", "schematic_id": schematic["schematic_id"],
            "query_id": schematic["query"]["query_id"], "catalog_id": schematic["catalog_id"],
            "counts": {key: len(schematic[key]) for key in _PARTITIONS},
            "unresolved_capabilities": schematic["unresolved_capabilities"], "frontier_count": len(schematic["frontier"]),
            "candidate_structure": True, "physical_truth_established": False, "execution_authority": False}
        _require(canonical(inspection) == canonical(expected), "nise_response_invalid")
        return {"schema": "odsp.nise-inspection.v1", "profile": PROFILE, "inspection": inspection,
            "diagnostics": _diagnostics(schematic), "runtime": runtime, "authority": deepcopy(AUTHORITY),
            "verification_scope": "structure_and_summary_only; source_catalog_not_recompiled_or_authenticated"}

    def verify(self, receipt):
        result = {"schema": "odsp.nise-verification.v1", "profile": PROFILE, "authority": deepcopy(AUTHORITY),
            "outcome": "invalid_contract", "canonical_receipt_match": False,
            "request_digest_match": False, "receipt_digest_match": False}
        try:
            _receipt(receipt)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return result
        result["request_digest_match"] = receipt["request_digest"] == _digest(receipt["request"])
        result["receipt_digest_match"] = receipt["receipt_digest"] == _receipt_digest(receipt)
        inputs = {**{key: receipt["request"][key] for key in ("catalog", "query")}, "schematic": receipt["schematic"]}
        try:
            schematic, runtime = self._upstream("verify", inputs, expected_runtime=receipt["runtime"])
        except NiseRefusal as exc:
            if str(exc) in {"runtime_binding_mismatch", "nise_contract_refused"}:
                result["outcome"] = "runtime_mismatch" if str(exc) == "runtime_binding_mismatch" else "request_refused"
                return result
            raise
        expected = self._issued(receipt["request"], schematic, runtime)
        result["canonical_receipt_match"] = canonical(expected) == canonical(receipt)
        result["outcome"] = "verified" if result["canonical_receipt_match"] else "mismatch"
        result.update(runtime=runtime, comparison_policy=deepcopy(COMPARISON_POLICY),
                      replayed_execution_id=expected["execution_id"], replayed_receipt_digest=expected["receipt_digest"])
        return result

    def call_raw(self, raw):
        try:
            value = parse(raw, limit=MAX_ARGUMENT)
            _exact(value, {"name", "arguments"})
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return {"status": "refused", "reason": "invalid_arguments"}
        return self.call(value["name"], value["arguments"])

    def call(self, name, arguments):
        try:
            return self._call(name, arguments)
        except OSError as exc:
            previous = self._adapter._cleanup_uncertain
            if previous is not None:
                # The inner refusal may have been ready to return when lease
                # cleanup failed. Keep the first response uncertain and retain
                # both diagnostics locally; a subsequent call remains fenced.
                held = RuntimeCleanupUncertain(previous.process)
                held.__cause__ = BaseExceptionGroup('NISE runtime and lease cleanup failed', [previous, exc])
                self._adapter._cleanup_uncertain = held
                return {"status": "refused", "reason": "runtime_cleanup_uncertain"}
            raise NiseRefusal("nise_runtime_io_refused") from exc

    def _call(self, name, arguments):
        lease = _Lease(Path(self._guard_directory.name) / "call.lease")
        try:
            _require(not self._closed, "nise_host_closed")
            _require(type(name) is str and name in TOOLS, "unknown_nise_operation")
            _require(len(canonical({"name": name, "arguments": arguments})) <= MAX_ARGUMENT, "argument_budget_exceeded")
            _shape(arguments, TOOLS[name]["inputSchema"])
            _require(lease.try_acquire(), "nise_busy")
            _require(not self._closed, "nise_host_closed")
            self._adapter.deadline = time.monotonic() + CALL_SECONDS
            args = deepcopy(arguments)
            value = (self.describe() if name == "ncp_nise_describe" else
                self.validate(**args) if name == "ncp_nise_validate" else self.compile(args["request"]) if name == "ncp_nise_compile" else
                self.inspect(args["schematic"]) if name == "ncp_nise_inspect" else self.verify(args["receipt"]))
            response = {"status": "ok", "data": value}
            self.mcp_result(name, response)
            self._adapter._remaining()
            return response
        except NiseRefusal as exc:
            return {"status": "refused", "reason": str(exc)}
        except AdapterRefusal as exc:
            return {"status": "refused", "reason": "runtime_cleanup_uncertain" if
                    exc.code == "RUNTIME_CLEANUP_UNCERTAIN" else "nise_runtime_refused"}
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return {"status": "refused", "reason": "invalid_arguments"}
        finally:
            if lease.fd is not None:
                self._adapter.deadline = None
            lease.release()

    @staticmethod
    def mcp_result(name, value):
        try:
            envelope = {"content": [{"type": "text", "text": canonical(value).decode()}],
                        "structuredContent": value, "isError": value.get("status") != "ok"}
            _require(len(canonical(envelope)) <= MAX_RESPONSE - JSONRPC_RESERVE, "response_budget_exceeded")
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise NiseRefusal("response_budget_exceeded") from exc
        return envelope

    def descriptions(self):
        return _tool_descriptions()

    def list_resources(self):
        return []

    def read_resource(self, uri):
        raise NiseRefusal("resource_unavailable")


def invoke_nise(host, name, arguments):
    _require(isinstance(host, NiseHost), "nise_host_required")
    value = host.call(name, arguments)
    if value["status"] != "ok":
        raise NiseRefusal(value["reason"])
    return value["data"]


def open_nise(**bindings):
    """Trusted host factory; tool payloads and discovery never create bindings."""
    return NiseHost(**bindings)


def profile_manifest():
    return {"profile_id": PROFILE, "adapter_contract_commit": PIN, "operation_id": OPERATION,
        "category": "deterministic_schematic_relevance_and_scoping",
        "host_factory": "ciw.nise_profile.open_nise", "service": "ciw.services.invoke", "tools": _tool_descriptions(),
        "source_binding_verification": "caller_assertion_only", "authorizes_execution": False,
        "authorizes_state_admission": False, "authority": deepcopy(AUTHORITY), "comparison_policy": deepcopy(COMPARISON_POLICY),
        "qualification": "not_performed_by_discovery", "question_semantics": "retained_text_not_NLP_or_ML_selection",
        "execution_identity": "caller_execution_id_preserved; verification_reuses_complete_request; no_occurrence_registry",
        "process_isolation": "pinned_interpreter_and_import_root; no_environment_or_network_sandbox",
        "limits": {"argument_bytes": MAX_ARGUMENT, "mcp_envelope_bytes": MAX_RESPONSE,
            "mcp_result_bytes": MAX_RESPONSE - JSONRPC_RESERVE, "jsonrpc_frame_reserve_bytes": JSONRPC_RESERVE,
            "process_output_bytes": MAX_PROCESS_OUTPUT, "child_wall_seconds": PROCESS_SECONDS,
            "git_metadata_output_bytes": MAX_GIT_OUTPUT,
            "call_work_deadline_seconds": CALL_SECONDS, "kernel_cpu_seconds": CPU_SECONDS,
            "kernel_address_space_bytes": ADDRESS_BYTES, "kernel_file_bytes": FILE_BYTES,
            "kernel_open_files": OPEN_FILES, "concurrent_calls": 1}}
