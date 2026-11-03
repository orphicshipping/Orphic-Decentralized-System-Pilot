"""Bounded SRA assessment over the existing host-pinned subprocess workflow.

Public receipts redact launch paths and use their own digest. Reproduction is
not source authentication, companion execution, observation or state admission.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
import time

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import PinnedSubprocessAdapter, _bounded_process, _json
from .agent_api import encode
from .agent_tools import _shape
from .coordinator import _Lease
from .declared_workload import (ASSESSMENT_POLICY, AUTHORITY, DeclaredWorkflow,
                               PINS, VERIFY_SCHEMA, _verification)
from .telemetry import _bundle_digest, byte_digest, canonical, digest

PROFILE = "sra.schematic.v1"
PIN = PINS["schematic-assessment"]["revision"]
OPERATION = "ciw.schematic-assessment.v1"
RECEIPT_SCHEMA = "odsp.sra.receipt.v1"
MAX_ARGUMENT = 65536
MAX_ENVELOPE = 262144
MAX_PROCESS_OUTPUT = 131072
CALL_SECONDS = 20
CHILD_SECONDS = 5
CPU_SECONDS = 3
ADDRESS_BYTES = 256 * 1024 * 1024
AUTHORITY_STATEMENT = {
    **AUTHORITY, "authorizes_execution": False, "companion_execution": "not_performed",
    "source_authentication": "not_performed", "certificate_authentication": "not_performed",
    "binding": "caller_assertion_only", "independent_verification": False,
}
_PATH_KEYS = frozenset({"repository_root", "python_executable"})

# This is a process resource bound, not a network or filesystem sandbox. The
# existing adapter environment behavior is unchanged. Fixed imports explicitly
# exclude companion and rendering entry points, even when a graph is eligible.
_GUARD = f"""
import sys, resource
resource.setrlimit(resource.RLIMIT_CPU, ({CPU_SECONDS}, {CPU_SECONDS}))
resource.setrlimit(resource.RLIMIT_AS, ({ADDRESS_BYTES}, {ADDRESS_BYTES}))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
class _SRAImports:
    def find_spec(self, fullname, path=None, target=None):
        blocked=('schematics.agent','schematics.adapters','schematics.kernels',
                 'schematics.projections','schematics.render','schematics.project_mermaid',
                 'schematics.project_usd','schematics.project_usd_read','jspt','plsr','rci')
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ImportError('companion_or_projection_import_refused')
sys.meta_path.insert(0, _SRAImports())
"""

_READ_BOOTSTRAP = """
import json, sys
sys.path.insert(0, sys.argv[1])
from schematics.io import from_dict, to_dict
from schematics.retrieve import blanket
request=json.loads(sys.stdin.buffer.read())
graph=from_dict(request['source']['schematic'])
if request['action']=='validate':
    value={'valid':True,'schematic':to_dict(graph)}
elif request['action']=='retrieve':
    value={'queries':request['source']['queries'],
           'neighborhoods':{key:blanket(graph,key) for key in request['source']['queries']}}
else:
    raise ValueError('unknown_read')
print(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False))
"""


class SchematicRefusal(ValueError):
    pass


def _require(value, reason="invalid_contract"):
    if not value:
        raise SchematicRefusal(reason)


def _exact(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _no_paths(value):
    if type(value) is dict:
        _require(not _PATH_KEYS.intersection(value), "payload_binding_refused")
        for child in value.values():
            _no_paths(child)
    elif type(value) is list:
        for child in value:
            _no_paths(child)


def _receipt_digest(value):
    return "sha256:" + sha256(encode({key: item for key, item in value.items()
                                     if key != "receipt_digest"})).hexdigest()


def _public(native):
    bundle = deepcopy(native)
    bundle.pop("bundle_digest")
    # A replay receipt references a different session. The profile returns fresh
    # reproduction as a separate public receipt instead of transplanting it.
    _require("replay_receipts" not in bundle)
    runtime = bundle["runtimes"]["sra"]
    for key in _PATH_KEYS:
        runtime.pop(key)
    if 'process_policy' in runtime:
        runtime['process_policy_ref'] = runtime.pop('process_policy')['policy_sha256']
    runtime["launch_binding"] = "host_selected"
    for key in ("subject_ref", "runtime_digest", "verification_id"):
        bundle["verification"].pop(key)
    value = {"schema": RECEIPT_SCHEMA, "profile": PROFILE, "bundle": bundle,
             "authority": deepcopy(AUTHORITY_STATEMENT)}
    value["receipt_digest"] = _receipt_digest(value)
    # Every issued receipt is consumable by the same bounded verification API.
    _require(len(encode({"name": "ncp_schematic_verify_receipt", "arguments": {"receipt": value}}))
             <= MAX_ARGUMENT, "receipt_verification_budget_exceeded")
    return value


class _BoundedAdapter(PinnedSubprocessAdapter):
    def __init__(self, checkout, python, *, process_policy=None):
        self.deadline = time.monotonic() + CALL_SECONDS
        super().__init__(checkout, PIN, PINS["schematic-assessment"]["module"],
                         source_root="src", python_executable=python,
                         timeout_seconds=CHILD_SECONDS, max_output_bytes=MAX_PROCESS_OUTPUT,
                         process_policy=process_policy)
        self.deadline = None

    def _remaining(self):
        remaining = CHILD_SECONDS if self.deadline is None else min(CHILD_SECONDS, self.deadline - time.monotonic())
        if remaining <= 0:
            raise SchematicRefusal("runtime_deadline_exceeded")
        return remaining

    def _git(self, *arguments):
        from .process_policy import require_companion_policy
        code, data = _bounded_process(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *arguments],
            cwd=self.repository_root, timeout=self._remaining(), limit=4 * 1024 * 1024,
            process_policy=require_companion_policy(self.process_policy))
        if code:
            raise AdapterRefusal("SOURCE_PIN_MISMATCH", "Cannot verify the bound Git checkout")
        return data

    def _run(self, code, arguments, stdin=b""):
        self.timeout_seconds = self._remaining()
        # Upstream __init__ eagerly imports agent/adapters. This explicit package
        # namespace loads the unchanged pinned assessment submodules without
        # executing that broad initializer or introducing another implementation.
        namespace = ("\nimport types\n_sra_package=types.ModuleType('schematics')\n"
                     "_sra_package.__package__='schematics'\n"
                     f"_sra_package.__path__={[str(self.source_root / 'schematics')]!r}\n"
                     "sys.modules['schematics']=_sra_package\n")
        return super()._run(_GUARD + namespace + code, arguments, stdin)


class SchematicHost:
    instructions = ("Host-pinned SRA schematic validation, topology retrieval and bounded assessment only. "
                    "No companion execution or rendering. Source and certificate claims remain unauthenticated.")

    def __init__(self, *, checkout, python, principal, process_policy=None):
        _require(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "unsupported_platform")
        _require(type(principal) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}", principal),
                 "invalid_host_binding")
        _require(Path(checkout).is_absolute() and Path(python).is_absolute(), "invalid_host_binding")
        self._bound_principal = principal
        self._adapter = _BoundedAdapter(checkout, python, process_policy=process_policy)
        self._workflow = DeclaredWorkflow("schematic-assessment")
        self._guard_dir = tempfile.TemporaryDirectory(prefix="odsp-schematic-host-")
        self._lease = _Lease(Path(self._guard_dir.name) / "call.lease")
        self._closed = False

    def close(self):
        if self._closed:
            return
        lease = _Lease(self._lease.path)
        try:
            _require(lease.try_acquire(), "host_busy")
            self._closed = True
        finally:
            lease.release()
        self._guard_dir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def descriptions(self):
        return _tool_descriptions()

    def _source(self, request):
        _exact(request, {"experiment_id", "schematic", "queries"})
        source = {"schema": self._workflow.SOURCE_SCHEMA, **deepcopy(request),
                  "configuration": deepcopy(ASSESSMENT_POLICY)}
        return self._workflow._source(canonical(source))

    def _native(self, receipt, runtime):
        _exact(receipt, {"schema", "profile", "bundle", "authority", "receipt_digest"})
        _require(receipt["schema"] == RECEIPT_SCHEMA and receipt["profile"] == PROFILE
                 and encode(receipt["authority"]) == encode(AUTHORITY_STATEMENT)
                 and receipt["receipt_digest"] == _receipt_digest(receipt))
        bundle = deepcopy(receipt["bundle"])
        _exact(bundle, {"schema", "session_id", "created_at", "source", "configuration",
                        "runtimes", "steps", "verification"})
        _exact(bundle["runtimes"], {"sra"})
        public_runtime = bundle["runtimes"]["sra"]
        expected = {key: value for key, value in runtime.items() if key not in _PATH_KEYS}
        if 'process_policy' in expected:
            expected['process_policy_ref'] = expected.pop('process_policy')['policy_sha256']
        expected["launch_binding"] = "host_selected"
        _require(encode(public_runtime) == encode(expected), "runtime_binding_mismatch")
        # Only the launcher supplies executable paths. No payload field selects
        # a checkout, interpreter, pin or source root.
        bundle["runtimes"]["sra"] = deepcopy(runtime)
        verification = bundle["verification"]
        _exact(verification, {"schema", "outcome", "independent", "method", "reproduction", "authority"})
        bundle["bundle_digest"] = _bundle_digest(bundle)
        verification["subject_ref"] = bundle["bundle_digest"]
        verification["runtime_digest"] = digest(bundle["runtimes"])
        verification["verification_id"] = byte_digest(VERIFY_SCHEMA.encode() + b"\0" + canonical(verification))
        raw = self._workflow._validate(bundle)
        return bundle, raw

    def _invoke(self, name, args):
        if name == "ncp_schematic_describe":
            return profile_manifest()
        # Validate the closed graph and certificate ownership before any child
        # process is launched. The runtime probe is also a child process.
        source = None
        if name != "ncp_schematic_verify_receipt":
            request = args["request"] if name == "ncp_schematic_assess" else {
                "experiment_id": "schematic-profile-read", "schematic": args["schematic"],
                "queries": args.get("queries", [])}
            source = self._source(request)
        runtime = self._adapter.runtime_identity()
        bound = self._adapter, runtime, None
        if name == "ncp_schematic_verify_receipt":
            original, raw = self._native(args["receipt"], runtime)
            reproduced = self._workflow._execute(raw, bound)
            check = _verification(original, reproduced["steps"][0])
            _require(original["session_id"] != reproduced["session_id"]
                     and original["steps"][0]["execution_id"] != reproduced["steps"][0]["execution_id"]
                     and original["steps"][0]["result_id"] != reproduced["steps"][0]["result_id"],
                     "fresh_reproduction_required")
            return {"profile": PROFILE, "outcome": "verified", "source_receipt_digest": args["receipt"]["receipt_digest"],
                    "reproduction_receipt": _public(reproduced), "numerical_match": check["outcome"] == "passed",
                    "comparison": "existing_numerical_result_projection", "independent": False,
                    "authority": deepcopy(AUTHORITY_STATEMENT)}
        if name == "ncp_schematic_assess":
            return {"profile": PROFILE, "outcome": "assessed", "receipt": _public(
                self._workflow._execute(canonical(source), bound)), "authority": deepcopy(AUTHORITY_STATEMENT)}
        action = "validate" if name == "ncp_schematic_validate" else "retrieve"
        code, raw = self._adapter._run(_READ_BOOTSTRAP, [str(self._adapter.source_root)],
                                      canonical({"action": action, "source": source}))
        self._adapter.runtime_identity()
        _require(code == 0, "runtime_refused")
        value = _json(raw)
        if action == "validate":
            _exact(value, {"valid", "schematic"})
            _require(value["valid"] is True and encode(value["schematic"]) == encode(source["schematic"]))
        else:
            _exact(value, {"queries", "neighborhoods"})
            _require(value["queries"] == source["queries"] and type(value["neighborhoods"]) is dict
                     and set(value["neighborhoods"]) == set(source["queries"]))
        return {"profile": PROFILE, "outcome": "validated" if action == "validate" else "retrieved",
                "data": value, "authority": deepcopy(AUTHORITY_STATEMENT)}

    def call(self, name, arguments):
        try:
            _require(not self._closed, "host_closed")
            _require(type(name) is str and name in TOOLS, "unknown_schematic_operation")
            _require(type(arguments) is dict, "invalid_contract")
            _require(len(encode({"name": name, "arguments": arguments})) <= MAX_ARGUMENT,
                     "argument_budget_exceeded")
            _no_paths(arguments)
            _shape(arguments, TOOLS[name]["inputSchema"])
            lease = _Lease(self._lease.path)
            try:
                _require(lease.try_acquire(), "host_busy")
                _require(not self._closed, "host_closed")
                self._adapter.deadline = time.monotonic() + CALL_SECONDS
                value = {"status": "ok", "data": self._invoke(name, deepcopy(arguments))}
                self.mcp_result(name, value)
                return value
            finally:
                if lease.fd is not None:
                    self._adapter.deadline = None
                lease.release()
        except SchematicRefusal as exc:
            return {"status": "refused", "reason": str(exc), "authorizes_execution": False}
        except AdapterRefusal as exc:
            return {"status": "refused", "reason": "runtime_cleanup_uncertain" if
                    exc.code == "RUNTIME_CLEANUP_UNCERTAIN" else "runtime_refused",
                    "code": exc.code, "authorizes_execution": False}
        except OSError as exc:
            # Cleanup can fail after its effect, including while unwinding a
            # cancellation. Preserve the cause/context for the software caller;
            # the public message contains no path or false rollback claim.
            raise SchematicRefusal("schematic_runtime_io_refused") from exc
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            return {"status": "refused", "reason": "invalid_contract", "authorizes_execution": False}

    @staticmethod
    def mcp_result(name, value):
        try:
            response = {"content": [{"type": "text", "text": encode(value).decode()}],
                        "structuredContent": value, "isError": value.get("status") != "ok"}
            _require(len(encode(response)) <= MAX_ENVELOPE, "response_budget_exceeded")
            return response
        except (ValueError, TypeError, RecursionError):
            # The shared serializer also bounds each JSON text value. Preserve
            # that stricter limit when the duplicated MCP text is encoded.
            raise SchematicRefusal("response_budget_exceeded") from None

    def resource_descriptions(self): return []
    def list_resources(self): return []
    @property
    def resource_templates(self): return []
    def read_resource(self, uri): raise SchematicRefusal("resource_unavailable")


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_GRAPH = {"type": "object"}
_QUERIES = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 512}, "maxItems": 16}
_REQUEST = _schema({"experiment_id": {"type": "string", "minLength": 1, "maxLength": 512},
                    "schematic": _GRAPH, "queries": _QUERIES}, ["experiment_id", "schematic", "queries"])
TOOLS = {name: {"description": description, "inputSchema": schema, "outputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                "idempotentHint": name != "ncp_schematic_assess" and name != "ncp_schematic_verify_receipt",
                                "openWorldHint": False}}
         for name, description, schema in (
    ("ncp_schematic_describe", "Describe a host-pinned SRA profile; no execution grant.", _schema({}, [])),
    ("ncp_schematic_validate", "Validate a bounded declared schematic through pinned SRA; no companion execution.",
     _schema({"schematic": _GRAPH}, ["schematic"])),
    ("ncp_schematic_retrieve", "Read at most 16 declared two-hop neighborhoods; no projections or embeddings.",
     _schema({"schematic": _GRAPH, "queries": _QUERIES}, ["schematic", "queries"])),
    ("ncp_schematic_assess", "Assess declared eligibility using the existing pinned workflow; never call companions.",
     _schema({"request": _REQUEST}, ["request"])),
    ("ncp_schematic_verify_receipt", "Validate every public receipt field and freshly reproduce its numerical projection.",
     _schema({"receipt": {"type": "object"}}, ["receipt"])),
)}


def _tool_descriptions():
    return [{"name": name, **deepcopy(value)} for name, value in TOOLS.items()]


def profile_manifest():
    return {"profile_id": PROFILE, "runtime_commit": PIN, "scientific_operation": OPERATION,
            "host_factory": "ciw.schematic_profile.open_schematic", "service": "ciw.services.invoke",
            "authority": deepcopy(AUTHORITY_STATEMENT), "tools": _tool_descriptions(),
            "limits": {"nodes": 256, "edges": 1024, "queries": 16, "argument_bytes": MAX_ARGUMENT,
                       "complete_mcp_bytes": MAX_ENVELOPE, "call_deadline_seconds": CALL_SECONDS,
                       "child_wall_seconds": CHILD_SECONDS, "child_cpu_seconds": CPU_SECONDS,
                       "child_address_bytes": ADDRESS_BYTES, "concurrent_calls_per_host": 1},
            "receipt_semantics": "Public redaction has its own digest; native path-derived hashes are not public identities. Reproduction has fresh occurrence and result IDs.",
            "environment": "Existing pinned adapter inherits host environment except its stripped path variables; no network sandbox is claimed.",
            "qualification": "not_established_by_discovery", "authorizes_execution": False}


def open_schematic(**bindings):
    return SchematicHost(**bindings)


def invoke_schematic(host, name, arguments):
    _require(isinstance(host, SchematicHost), "schematic_host_required")
    value = host.call(name, arguments)
    if value["status"] != "ok":
        raise SchematicRefusal(value["reason"])
    return value["data"]
