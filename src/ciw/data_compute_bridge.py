"""Bounded native compute-state and polymer consumers for retained NET data.

Compute Runtime keeps its own CanonicalState/Version identity, fields, directed
edges and ordering. This bridge does not lower those records into scalar traces
or confer state admission. Only an explicitly bound operator-owned source root
may execute the external package; workspace records never select executable code.
Saved validation is local and does not import or run that package or the polymer
numerical models. Arbitrary tensors are outside Compute Runtime's field contract.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import platform
import re
import sys

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import _bounded_process, _json
from .control_contracts import detached, keys, number, text
from .core.identities import canonical_json
from .operations.registry import Operation
from .operations.runner import digest

SCHEMA = "ciw.compute-canonical-state.v1"
PROJECT = "data.project-compute-state.v1"
ASSESS_POLYMER = "data.assess-polymer-cycle.v1"
OPERATION_IDS = frozenset({PROJECT, ASSESS_POLYMER})
MAX_BYTES = 8 * 1024 * 1024
AUTHORITY = {"projection": "exact_native_fields_and_edges", "physical_validation": "not_established",
             "state_admission": "not_performed", "proof_verification": "not_performed"}
_FILES = ("core/__init__.py", "core/canonical/__init__.py", "core/canonical/schema.py",
          "core/canonical/state.py", "core/canonical/version.py", "core/projection/__init__.py",
          "core/projection/project.py")
# Run the actual package in an isolated interpreter. Only the process-local
# binding supplies argv[1]; no retained record controls a module or source path.
_WORKER = r'''
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
for name, relative in (("core", "core/__init__.py"),
                       ("core.canonical", "core/canonical/__init__.py"),
                       ("core.canonical.schema", "core/canonical/schema.py"),
                       ("core.canonical.state", "core/canonical/state.py"),
                       ("core.canonical.version", "core/canonical/version.py"),
                       ("core.projection", "core/projection/__init__.py"),
                       ("core.projection.project", "core/projection/project.py")):
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile((root / relative).read_bytes(), str(root / relative), "exec"), module.__dict__)
from core.canonical.state import CanonicalState, Field, EdgeRecord
from core.canonical.version import ProvenanceInfo, make_version, canonical_content
from core.projection.project import project_state
for name in ("core", "core.canonical", "core.canonical.schema", "core.canonical.state",
             "core.canonical.version", "core.projection", "core.projection.project"):
    if not pathlib.Path(sys.modules[name].__file__).resolve().is_relative_to(root):
        raise ValueError("Compute module resolved outside explicit source binding")
v = json.load(sys.stdin)["version"]
s = v["state"]
freeze = lambda value: tuple(value) if isinstance(value, list) else value
fields = {key: Field(id=f["id"], type=f["type"], value=freeze(f["value"]), unit=f["unit"])
          for key, f in s["fields"].items()}
edges = tuple(EdgeRecord(id=e["id"], from_=e["from"], to=e["to"], type=e["type"],
                         attributes={key: freeze(value) for key, value in e["attributes"].items()})
              for e in s["edges"])
state = CanonicalState(schema_version=s["schema_version"], fields=fields, edges=edges)
version = make_version(state, v["parent"], ProvenanceInfo(**v["provenance"]), v["timestamp"])
if version.id != v["id"] or canonical_content(state) != s:
    raise ValueError("Native compute-state identity or representation changed")
projected = project_state(version)
# Canonical encoding is owned by the package, including tuple conversion and
# directed edge order. Projection itself performs no admission or inference.
projected_state = CanonicalState(projected.schema_version, projected.fields, projected.edges)
print(json.dumps({"source_version": projected.source_version,
                  "state": canonical_content(projected_state)}, allow_nan=False))
'''


def _bounded(value):
    detached(value)
    if len(canonical_json(value).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Compute bridge record exceeds its byte budget")


def _version_id(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Compute Runtime version identity must be 64 lowercase hex digits")
    return value


def _timestamp(value):
    stamp = datetime.fromisoformat(text(value))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Compute version timestamps require an explicit timezone")


def _field_value(kind, value):
    if kind == "scalar":
        number(value)
    elif kind == "string":
        if type(value) is not str:
            raise ValueError("Compute string field must retain a string")
    elif kind == "bool":
        if type(value) is not bool:
            raise ValueError("Compute bool field must retain a boolean")
    elif kind in {"vector3", "quaternion"}:
        width = 3 if kind == "vector3" else 4
        if type(value) is not list or len(value) != width:
            raise ValueError(f"Compute {kind} field requires exactly {width} components")
        for item in value:
            number(item)
    else:
        raise ValueError("Unsupported Compute Runtime field type; tensors are not canonical fields")


def _state(value):
    keys(value, {"schema_version", "fields", "edges"})
    text(value["schema_version"])
    fields, edges = value["fields"], value["edges"]
    if type(fields) is not dict or len(fields) > 4096 or type(edges) is not list or len(edges) > 8192:
        raise ValueError("Compute state requires bounded native fields and edges")
    for key, field in fields.items():
        text(key)
        keys(field, {"id", "type", "value", "unit"})
        if field["id"] != key:
            raise ValueError("Compute field identity must match its native key; no repair is performed")
        text(field["type"])
        _field_value(field["type"], field["value"])
        if field["unit"] is not None:
            text(field["unit"])
    ids = set()
    for edge in edges:
        keys(edge, {"id", "from", "to", "type", "attributes"})
        for key in ("id", "from", "to", "type"):
            text(edge[key])
        if edge["id"] in ids:
            raise ValueError("Duplicate native edge identity")
        ids.add(edge["id"])
        if type(edge["attributes"]) is not dict:
            raise ValueError("Native edge attributes must be a mapping")
        for key, attribute in edge["attributes"].items():
            text(key)
            if type(attribute) in {str, bool}:
                continue
            if type(attribute) is list:
                # FieldValue's native tuple is flat, not a nested tensor.
                for component in attribute:
                    number(component)
            else:
                number(attribute)
    return value


def _native_identity(state):
    # Byte-for-byte equivalent to core.canonical.version.canonical_json_bytes;
    # do not substitute NET's ASCII/identity convention or include provenance.
    return sha256(json.dumps(state, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def validate_record(value: dict) -> dict:
    """Check a retained native Version without loading or executing its provider."""
    _bounded(value)
    keys(value, {"schema", "version", "authority"})
    if value["schema"] != SCHEMA or value["authority"] != AUTHORITY:
        raise ValueError("Unsupported compute-state schema or authority")
    version = value["version"]
    keys(version, {"id", "parent", "state", "schema_version", "provenance", "timestamp"})
    state = _state(version["state"])
    if (version["schema_version"] != state["schema_version"]
            or _version_id(version["id"]) != _native_identity(state)):
        raise ValueError("Compute native content identity/schema mismatch")
    if version["parent"] is not None:
        _version_id(version["parent"])
    _timestamp(version["timestamp"])
    provenance = version["provenance"]
    keys(provenance, {"author", "transaction_id", "source", "timestamp"})
    for key in ("author", "transaction_id", "source"):
        text(provenance[key])
    if provenance["timestamp"] is not None:
        _timestamp(provenance["timestamp"])
    return deepcopy(value)


def canonical_record(native_state: dict, *, timestamp: str, provenance: dict,
                     parent_version_id: str | None = None) -> dict:
    """Wrap exact canonical content; this records a declaration, never admits it.

    The Version content identity follows the native package's exact encoding.
    Provenance and parent are retained but remain outside that content identity,
    exactly as in Compute Runtime. This bounded interchange profile accepts only
    complete finite native fields; it does not invent missingness or tensors.
    """
    _bounded(native_state)
    state = deepcopy(_state(native_state))
    return validate_record({"schema": SCHEMA, "version": {
        "id": _native_identity(state), "parent": parent_version_id, "state": state,
        "schema_version": state.get("schema_version"), "provenance": deepcopy(provenance),
        "timestamp": timestamp}, "authority": deepcopy(AUTHORITY)})


def _source(parameters, expected_schema):
    from .data_sources import native_record
    keys(parameters, {"source"})
    source = parameters["source"]
    if type(source) is not dict or source.get("kind") != "data-record":
        raise ValueError("Compute bridge requires a retained data-record Workbench source")
    native = native_record(source)
    if native["schema"] != expected_schema:
        raise ValueError("Compute consumer requires its exact native source schema")
    return native


def source_ids(parameters):
    source = parameters.get("source")
    return [source["source_id"]] if type(source) is dict and type(source.get("source_id")) is str else []


def source_parameters(workbench, source_id):
    """Pin the full retained source; its provider is bound separately by an operator."""
    return {"source": deepcopy(workbench.get_source(source_id))}


def _projection_payload(parameters, projection):
    record = _source(parameters, SCHEMA)
    state = record["version"]["state"]
    return {"schema": "ciw.compute-state-projection.v1", "source_id": parameters["source"]["source_id"],
            "native_record_ref": digest(record), "projection": deepcopy(projection),
            "conservation": {"field_count": len(state["fields"]), "edge_count": len(state["edges"]),
                             "field_ids": sorted(state["fields"]),
                             "edge_ids": [edge["id"] for edge in state["edges"]],
                             "field_values_units_and_types": "preserved_exactly",
                             "edge_direction_order_and_attributes": "preserved_exactly",
                             "discarded_fields": [], "discarded_edges": []},
            "authority": deepcopy(AUTHORITY)}


class ComputeRuntime:
    """Explicit process-local binding to trusted, consolidated Compute Runtime.

    Binding snapshots executable source hashes; drift refuses before execution.
    The source directory is deliberately absent from data records and restored
    workspaces never reconstruct this binding. The current trusted Python
    interpreter is used with isolated startup and bounded input/output/time.
    """
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self._hashes = self._source_hashes()

    def _source_hashes(self):
        hashes = {}
        for name in _FILES:
            path = self.root / name
            if not path.is_file() or not path.resolve().is_relative_to(self.root):
                raise AdapterRefusal("compute_runtime_unavailable", "Missing native Compute Runtime source file: " + name)
            hashes[name] = sha256(path.read_bytes()).hexdigest()
        return hashes

    def runtime_identity(self):
        if self._source_hashes() != self._hashes:
            raise AdapterRefusal("compute_runtime_changed", "Bound Compute Runtime source bytes changed")
        return {"provider": "compute-runtime.core.projection.project_state", "version": "1",
                "source_files_sha256": deepcopy(self._hashes), "bridge_code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "environment": {"python": platform.python_version()}, "binding": "explicit_process_local"}

    def execute(self, parameters):
        record = _source(parameters, SCHEMA)
        self.runtime_identity()
        code, raw = _bounded_process([sys.executable, "-I", "-S", "-B", "-c", _WORKER, str(self.root)],
                                    cwd=self.root, timeout=30.0, limit=MAX_BYTES,
                                    stdin=canonical_json(record).encode("utf-8"))
        if code:
            raise AdapterRefusal("compute_projection_failed", "Native Compute Runtime projection refused")
        # Validate the provider output against the exact source. Hashes alone
        # are insufficient evidence of conservation, nor do they confer trust.
        projection = _json(raw)
        self.runtime_identity()
        expected = {"source_version": record["version"]["id"], "state": record["version"]["state"]}
        if canonical_json(projection) != canonical_json(expected):
            raise ValueError("Native projection changed canonical fields, edges or identity")
        return _projection_payload(parameters, projection)

    def operation(self):
        return Operation(PROJECT, "backend", lambda run, parameters: self.execute(parameters), self.runtime_identity)


def _polymer(parameters):
    from .polymer_contract import SCHEMA as polymer_schema
    from .polymer_workflow import _assess, make_source
    request = _source(parameters, polymer_schema)
    return {"schema": "ciw.data-polymer-assessment.v1", "source_id": parameters["source"]["source_id"],
            "native_request_ref": digest(request), "assessment": _assess(make_source(request), {}),
            "conservation": _polymer_conservation(request)}


def _polymer_conservation(request):
    return {"sensor_ids": [sensor["sensor_id"] for sensor in request["sensors"]],
            "sample_counts": {sensor["sensor_id"]: len(sensor["samples"]) for sensor in request["sensors"]},
            "native_request_retention": "complete", "unit_conversion": "not_performed",
            "resampling": "not_performed", "state_admission": "not_performed"}


def polymer_operation():
    """Assess the selected native cycle in the current Workbench's data catalog."""
    from .data_runtime import polymer_identity
    return Operation(ASSESS_POLYMER, "backend", lambda run, parameters: _polymer(parameters),
                     polymer_identity)


def validate_payload(operation_id, data, run, parameters, selection):
    """Inspect saved correspondence without dispatching either numerical provider."""
    _bounded(data)
    if operation_id == PROJECT:
        record = _source(parameters, SCHEMA)
        expected = _projection_payload(parameters, {"source_version": record["version"]["id"],
                                                     "state": record["version"]["state"]})
        if canonical_json(data) != canonical_json(expected):
            raise ValueError("Compute projection differs from its exact retained source")
    elif operation_id == ASSESS_POLYMER:
        from .polymer_contract import SCHEMA as polymer_schema
        from .polymer_workflow import _validate_assessment
        request = _source(parameters, polymer_schema)
        keys(data, {"schema", "source_id", "native_request_ref", "assessment", "conservation"})
        if (data["schema"] != "ciw.data-polymer-assessment.v1"
                or data["source_id"] != parameters["source"]["source_id"]
                or data["native_request_ref"] != digest(request)
                or canonical_json(data["conservation"]) != canonical_json(_polymer_conservation(request))):
            raise ValueError("Polymer assessment differs from retained data source")
        _validate_assessment(request, data["assessment"])
    else:
        raise ValueError("Unsupported compute bridge operation")


def validate_live_dependencies(operation_id, parameters, sources):
    if operation_id not in OPERATION_IDS:
        return
    from .polymer_contract import SCHEMA as polymer_schema
    _source(parameters, SCHEMA if operation_id == PROJECT else polymer_schema)
    source = parameters["source"]
    if (source["source_id"] not in sources
            or canonical_json(sources[source["source_id"]]) != canonical_json(source)):
        raise ValueError("Compute consumer requires a source present unchanged in this Workbench")


def validate_saved_dependencies(results, sources):
    for result in results.values():
        if result["operation_id"] in OPERATION_IDS:
            validate_live_dependencies(result["operation_id"], result["parameters"], sources)
