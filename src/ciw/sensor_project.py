"""Retained electronics/sensor/visual investigation on existing NET Session.

Native CAD/assets stay authoritative in their original files. This is a bounded
composition and lookup adapter, not a digital-twin engine or a second data store.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import math
from pathlib import Path
import platform
import tempfile
from threading import Lock

from . import foundry_packets as packets, sensor_project_mems as mems
from .adapters.protocol import InstrumentManifest
from .control_contracts import bytes_ref, content_ref, detached, keys, text
from .control_plane import CapabilityRegistry, ObservationBus
from .core.identities import content_identity, evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation
from .operations.runner import seal, check_seal
from .operations.schemas import register_payload_validator
from .session import Session, loads_json

OPERATION = "engineering.sensor-project.v1"
CHANNEL = "project_declaration"
MAX_TOTAL = 2 * 1024 * 1024
MAX_WORKSPACE = 32 * 1024 * 1024
KINDS = {"electronics.project", "electronics.schematic", "electronics.pcb", "electronics.library", "electronics.rules",
         "mechanical.cad", "geometry.step", "asset.blend", "asset.glb", "asset.gltf", "asset.buffer",
         "sensor.configuration", "sensor.mems-csv", "observations.native", "solver.result", "model.input",
         "check.erc", "check.drc", "manufacturing.output", "source.notice", "transformation.record",
         "acquisition.capture", "acquisition.workspace", "manufacturing.record", "calibration.record", "firmware.image", "tool.configuration"}
CLAIMS = {"physical_validation": "not_established", "geometry_equivalence": "not_established",
          "source_authentication": "not_established", "manufacturing_approval": False, "equipment_control": False,
          "state_admission": False, "verification_id": None}
_LOCK = Lock()
_REGISTERED = False


def same(a, b):
    return content_identity(a) == content_identity(b)


def _pack(raw):
    return [base64.b64encode(raw[i:i+24000]).decode("ascii") for i in range(0, len(raw), 24000)]


def _unpack(chunks):
    if type(chunks) is not list or len(chunks) > 90 or any(type(v) is not str or len(v) > 32000 for v in chunks):
        raise ValueError("Encoded file exceeds budget")
    raw = b"".join(base64.b64decode(v, validate=True) for v in chunks)
    if len(raw) > packets.MAX_FILE or _pack(raw) != chunks:
        raise ValueError("Noncanonical or oversized file encoding")
    return raw


def validate_spec(spec, inventory):
    keys(spec, {"schema", "project_id", "hardware_revision", "mode", "baseline_ref", "artifacts", "mems", "native_streams", "bindings"})
    if spec["schema"] != "ciw.sensor-project-request.v1":
        raise ValueError("Unsupported project declaration")
    text(spec["project_id"]); text(spec["hardware_revision"])
    if spec["mode"] not in {"REPLAY", "SIMULATION", "COUNTERFACTUAL"}:
        raise ValueError("Retained-file mode must be REPLAY, SIMULATION or COUNTERFACTUAL; LIVE requires a separate live authority")
    artifacts = spec["artifacts"]
    if type(artifacts) is not list or not 1 <= len(artifacts) <= 128:
        raise ValueError("Require 1..128 explicit artifact roles")
    paths, roles = [], {}
    for item in artifacts:
        keys(item, {"path", "kind", "parents", "tool", "origin"})
        path = packets.relative(item["path"]); paths.append(path); roles[path] = item["kind"]
        if item["kind"] not in KINDS or item["origin"] not in {"synthetic", "caller_supplied"}:
            raise ValueError("Unknown artifact role or origin")
        keys(item["tool"], {"name", "version"}); text(item["tool"]["name"]); text(item["tool"]["version"])
        if type(item["parents"]) is not list or len(item["parents"]) > 32 or len(item["parents"]) != len(set(item["parents"])):
            raise ValueError("Invalid artifact ancestry")
        required = {"electronics.project": ".kicad_pro", "electronics.schematic": ".kicad_sch", "electronics.pcb": ".kicad_pcb",
                    "asset.blend": ".blend", "asset.glb": ".glb", "asset.gltf": ".gltf"}.get(item["kind"])
        if required and not path.endswith(required):
            raise ValueError("Native file role and extension disagree")
    if len(paths) != len(set(paths)) or set(paths) != set(inventory["files"]):
        raise ValueError("Every retained file requires exactly one role; unlisted inputs refuse")
    parents = {a["path"]: a["parents"] for a in artifacts}
    def visit(path, active, done):
        if path not in parents or path in active:
            raise ValueError("Missing or cyclic artifact ancestry")
        if path not in done:
            for p in parents[path]: visit(p, active | {path}, done)
            done.add(path)
    done = set()
    for path in paths: visit(path, set(), done)
    if spec["mode"] == "COUNTERFACTUAL":
        content_ref(spec["baseline_ref"])
        if spec["baseline_ref"] not in {v["sha256"] for v in inventory["files"].values()}:
            raise ValueError("Counterfactual baseline must be among retained source artifacts")
    elif spec["baseline_ref"] is not None:
        raise ValueError("Only counterfactual mode carries this explicit baseline reference")
    if type(spec["mems"]) is not list or len(spec["mems"]) > 8:
        raise ValueError("MEMS source count exceeds budget")
    selected = []
    for mapping in spec["mems"]:
        keys(mapping, {"path", "configuration_path", "profile"})
        if roles.get(mapping["path"]) != "sensor.mems-csv" or roles.get(mapping["configuration_path"]) != "sensor.configuration":
            raise ValueError("MEMS log and configuration must resolve to retained files")
        mems.validate_profile(mapping["profile"])
        if mapping["profile"]["configuration_ref"] != inventory["files"][mapping["configuration_path"]]["sha256"]:
            raise ValueError("MEMS configuration hash differs from retained original")
        if spec["mode"] != "REPLAY" and mapping["profile"]["origin"] != "synthetic":
            raise ValueError("Acquired data cannot be relabelled as a simulated/counterfactual result")
        selected.append(mapping["path"])
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate CSV ingestion; do not create duplicate observation events")
    if type(spec["native_streams"]) is not list or len(spec["native_streams"]) > 16 or len(set(spec["native_streams"])) != len(spec["native_streams"]):
        raise ValueError("Invalid native stream selection")
    if any(roles.get(p) != "observations.native" for p in spec["native_streams"]):
        raise ValueError("Native stream must resolve to retained observations")
    if type(spec["bindings"]) is not list or len(spec["bindings"]) > 128:
        raise ValueError("Binding count exceeds budget")
    ancestor_cache = {}
    def ancestors(path):
        if path not in ancestor_cache:
            result = set()
            for parent in parents[path]:
                result.add(parent); result.update(ancestors(parent))
            ancestor_cache[path] = frozenset(result)
        return ancestor_cache[path]
    nodes = set()
    for binding in spec["bindings"]:
        keys(binding, {"asset_path", "node", "entity_id", "channels", "design_paths", "source_frame", "asset_frame", "metres_per_source_unit", "transform"})
        key = (binding["asset_path"], binding["node"])
        if roles.get(binding["asset_path"]) not in {"asset.glb", "asset.gltf"} or key in nodes:
            raise ValueError("Ambiguous node binding or unsupported visual asset")
        packets.relative(binding["node"]); nodes.add(key)
        for k in ("entity_id", "source_frame", "asset_frame"): text(binding[k])
        if type(binding["channels"]) is not list or not binding["channels"] or len(binding["channels"]) > 32:
            raise ValueError("Binding requires explicit channel identities")
        seen = set()
        for ch in binding["channels"]:
            keys(ch, {"model_id", "entity_id", "quantity"})
            for v in ch.values(): text(v)
            ident = tuple(ch[k] for k in ("model_id", "entity_id", "quantity"))
            if ident in seen: raise ValueError("Duplicate binding channel")
            seen.add(ident)
        if type(binding["design_paths"]) is not list or not binding["design_paths"] or len(set(binding["design_paths"])) != len(binding["design_paths"]):
            raise ValueError("Design ancestry must be explicit")
        if any(roles.get(p) not in {"electronics.pcb", "electronics.schematic", "mechanical.cad"} for p in binding["design_paths"]):
            raise ValueError("Binding needs native engineering-design references")
        if not set(binding["design_paths"]) <= ancestors(binding["asset_path"]):
            raise ValueError("Visual binding does not descend from the selected native designs")
        scale = binding["metres_per_source_unit"]
        if type(scale) not in (int, float) or not math.isfinite(scale) or not 1e-9 <= scale <= 1e6:
            raise ValueError("Invalid explicit geometric unit scale")
        m = binding["transform"]
        if type(m) is not list or len(m) != 4 or any(type(r) is not list or len(r) != 4 for r in m):
            raise ValueError("Transform must be a 4x4 affine matrix")
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e12 for r in m for v in r) or m[3] != [0, 0, 0, 1]:
            raise ValueError("Invalid affine transform")
        det = (m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1]) - m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0]) + m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]))
        if det == 0: raise ValueError("Singular geometry transform")
    return roles


def manifest(operation=OPERATION):
    return InstrumentManifest(instrument_id="sensor-project-retention.v1" if operation == OPERATION else "kicad-design-checks.v1", role="engineering_projection", units={CHANNEL: "1"},
        frames=("project-declaration-not-physical-clock",), sampling={"kind": "one_declaration"},
        supported_operations=(operation,), calibration_requirements={"physical_qualification": "not_established"})


def source(root, spec, spec_raw=None):
    root = packets.root_dir(root)
    inv = packets.inventory(root)
    if sum(v["bytes"] for v in inv["files"].values()) > MAX_TOTAL:
        raise ValueError("This bounded source profile supports at most 2 MiB; larger designs require the parent artifact store")
    validate_spec(spec, inv)
    raw = (json.dumps(spec, indent=2, allow_nan=False) + "\n").encode() if spec_raw is None else spec_raw
    if type(raw) is not bytes or len(raw) > 128*1024 or not same(loads_json(raw.decode("utf-8")), spec):
        raise ValueError("Original request bytes differ from declared project")
    files = {p: packets.read_file(root, p) for p in inv["files"]}
    if packets.inventory(root) != inv or any(bytes_ref(files[p]) != inv["files"][p]["sha256"] for p in files):
        raise ValueError("Project changed during snapshot")
    context = {"spec": deepcopy(spec), "spec_bytes": _pack(raw), "inventory": inv, "files": {p: _pack(b) for p, b in files.items()}}
    run = {"run_schema": "run.v1", "run_id": "run-sensor-project-" + content_identity(context)[7:23],
           "instrument": "sensor-project-retention.v1", "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
           "coordinate_frame": "project-declaration-not-physical-clock", "manifest": manifest().to_dict(), "sensor_project": context,
           "provenance": {"source": "operator-declared-retained-project", "generator": "ciw.sensor_project", "generator_version": 1}},
           "time_s": [0.0], "channels": {CHANNEL: {"unit": "1", "values": [1.0]}}, "render": {}}
    run["evidence_id"] = evidence_id(run)
    detached(run)
    return run


def context(run):
    validate_run_structure(run); validate_evidence_identity(run)
    value = run["metadata"]["sensor_project"]
    keys(value, {"spec", "spec_bytes", "inventory", "files"})
    packets.validate_inventory(value["inventory"])
    if set(value["files"]) != set(value["inventory"]["files"]): raise ValueError("Project file set differs")
    files = {p: _unpack(chunks) for p, chunks in value["files"].items()}
    if sum(len(v) for v in files.values()) > MAX_TOTAL: raise ValueError("Project exceeds retained byte limit")
    if any(bytes_ref(v) != value["inventory"]["files"][p]["sha256"] or len(v) != value["inventory"]["files"][p]["bytes"] for p, v in files.items()):
        raise ValueError("Native artifact byte identity mismatch")
    if not same(loads_json(_unpack(value["spec_bytes"]).decode("utf-8")), value["spec"]): raise ValueError("Request bytes differ")
    validate_spec(value["spec"], value["inventory"])
    if run["instrument"] != "sensor-project-retention.v1" or run["time_s"] != [0.0] or not same(run["channels"], {CHANNEL: {"unit": "1", "values": [1.0]}}):
        raise ValueError("The project declaration is not a physical time series")
    return value["spec"], value["inventory"], files


def compile_project(run, parameters):
    if not same(parameters, {"channel": CHANNEL, "interval_s": [0.0, 1.0]}): raise ValueError("Use complete project declaration")
    spec, inv, files = context(run)
    projections = {m["path"]: mems.project(files[m["path"]], m["profile"]) for m in spec["mems"]}
    streams = {path: p["native_stream"] for path, p in projections.items()}
    from .control_checks import inspect_record
    for path in spec["native_streams"]:
        native = loads_json(files[path].decode("utf-8"))
        inspect_record(native)
        if native["schema"] != "ciw.observation-stream.v1": raise ValueError("Expected existing native observation stream")
        streams[path] = native
    observations, seen = [], set()
    for path, stream in streams.items():
        for row in stream["observations"]:
            if row["record_digest"] in seen: raise ValueError("Duplicate native observation across inputs")
            seen.add(row["record_digest"])
            if spec["mode"] != "REPLAY" and row["provenance"]["semantics"] != "simulated":
                raise ValueError("Observed or estimated data cannot become simulated by changing a view label")
            observations.append({"source_path": path, "observation": deepcopy(row)})
    resolved = []
    for binding in spec["bindings"]:
        channels = {tuple(c[k] for k in ("model_id", "entity_id", "quantity")) for c in binding["channels"]}
        selected = [item for item in observations if (item["observation"]["identity"]["model_id"], item["observation"]["identity"]["entity_id"], item["observation"]["quantity"]) in channels]
        matched = {(v["observation"]["identity"]["model_id"], v["observation"]["identity"]["entity_id"], v["observation"]["quantity"]) for v in selected}
        resolved.append({"binding": deepcopy(binding), "asset_ref": inv["files"][binding["asset_path"]]["sha256"],
                         "design_refs": [inv["files"][p]["sha256"] for p in binding["design_paths"]],
                         "observation_refs": [v["observation"]["record_digest"] for v in selected],
                         "missing_channels": [dict(zip(("model_id", "entity_id", "quantity"), v)) for v in sorted(channels - matched)],
                         "node_binding_verification": "requires-native-scene-review", "geometry_transform": "declared-not-verified"})
    return {"schema": "ciw.sensor-project-result.v1", "project_id": spec["project_id"], "hardware_revision": spec["hardware_revision"],
            "mode": spec["mode"], "baseline_ref": spec["baseline_ref"], "inventory_id": inv["inventory_id"],
            "artifacts": deepcopy(spec["artifacts"]), "mems_projections": projections, "observations": observations,
            "bindings": resolved, "claims": deepcopy(CLAIMS),
            "native_execution": {"kicad": "not_executed_by_retention", "mems_studio": "export_only", "blender": "not_executed_by_retention", "godot": "not_executed_by_retention"}}


def validate_payload(operation, data, run, parameters, selection):
    if operation != OPERATION: raise ValueError("Wrong sensor-project operation")
    if not same({k: selection[k] for k in ("channel", "interval_s")}, {"channel": CHANNEL, "interval_s": [0.0, 1.0]}):
        raise ValueError("Incomplete project selection")
    if not same(data, compile_project(run, parameters)): raise ValueError("Retained projection differs from exact sources and mapping")


def register_schema():
    global _REGISTERED
    with _LOCK:
        if not _REGISTERED:
            register_payload_validator(OPERATION, validate_payload, role="analysis")
            from .sensor_project_kicad import OPERATION as KICAD, validate_payload as validate_kicad
            register_payload_validator(KICAD, validate_kicad, role="backend")
            _REGISTERED = True


def runtime_identity():
    return {"provider": "ciw.sensor-project", "version": "1", "python": platform.python_version(),
            "sources": {name: bytes_ref(Path(__file__).with_name(name).read_bytes().replace(b"\r\n", b"\n")) for name in ("sensor_project.py", "sensor_project_mems.py", "foundry_packets.py", "control_plane.py", "control_contracts.py")},
            "scope": "retained mapping; no native electronics/visual solver or physical authority"}


def registry(binding=None):
    register_schema(); r = CapabilityRegistry()
    r.advertise(manifest(), runtime=runtime_identity(), capabilities={OPERATION: ["engineering.retained-sensor-project"]})
    r.bind(Operation(OPERATION, "analysis", compile_project, runtime_identity))
    if binding is not None:
        native = binding.operation()
        r.advertise(manifest(native.operation_id), runtime=binding.runtime_identity(),
                    capabilities={native.operation_id: ["electronics.kicad-package"]})
        r.bind(native)
    return r


def _destination(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)): raise ValueError("No symlink output paths")
    return path


def execute(run, output, *, binding=None, operation=OPERATION):
    context(run)
    output = _destination(output); output.mkdir(parents=True, exist_ok=False)
    session = Session(deepcopy(run), output, operations=registry(binding).operations)
    response = session.handle({"protocol_version": 1, "request_id": "sensor-project", "type": "operation.execute", "payload": {"operation_id": operation, "parameters": {}}})
    session.save_workspace(output / "workspace.json")
    if response["type"] == "error": raise ValueError(str(response["payload"]))
    return inspect(output)


def load(directory, expected=None):
    register_schema()
    directory = packets.root_dir(directory)
    path = directory / "workspace.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_WORKSPACE: raise ValueError("Invalid workspace file")
    raw = path.read_bytes()
    if expected is not None:
        content_ref(expected)
        if bytes_ref(raw) != expected: raise ValueError("Snapshot differs from operator-held anchor")
    # Session restoration validates before touching the disposable destination.
    with tempfile.TemporaryDirectory(prefix="net-sensor-reopen-") as tmp:
        tmp = Path(tmp); (tmp / "workspace.json").write_bytes(raw)
        session = Session.from_workspace(tmp / "workspace.json", tmp / "restored")
    context(session.run)
    if len(session.executions) != 1 or any(v["operation_id"] not in (OPERATION, "electronics.kicad-package.v1") for v in session.executions.values()):
        raise ValueError("Expected one sensor-project execution")
    if len(session.results) > 1: raise ValueError("Unexpected result set")
    return session, bytes_ref(raw)


def inspect(directory, expected=None):
    session, anchor = load(directory, expected)
    execution = next(iter(session.executions.values()))
    result = session.results.get(execution.get("result_id"))
    return {"status": "REFUSED" if result is None else "RETAINED", "snapshot_ref": anchor,
            "evidence_id": session.run["evidence_id"], "execution_id": execution["execution_id"],
            "result_id": None if result is None else result["result_id"], "data": None if result is None else deepcopy(result["data"]),
            "refusal": execution.get("refusal"), "claims": deepcopy(CLAIMS)}


def replay(directory, output, expected=None):
    session, _ = load(directory, expected)
    execution = next(iter(session.executions.values()))
    if execution["operation_id"] != OPERATION:
        raise ValueError("Native KiCad replay requires explicit operator rebinding; use kicad with retained input export")
    return execute(session.run, output)


def select(snapshot, *, asset_ref, node, offset=0, limit=16):
    """A read-only selector shared by MCP and a Godot caller. Never controls hardware."""
    content_ref(asset_ref); packets.relative(node)
    if type(offset) is not int or not 0 <= offset <= 65536 or type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError("Bounded page required")
    data = snapshot["data"]
    if data is None: raise ValueError("Projection refused; original source remains in retained workspace")
    if data.get("schema") != "ciw.sensor-project-result.v1": raise ValueError("Map the retained project before scene lookup")
    matches = [b for b in data["bindings"] if b["asset_ref"] == asset_ref and b["binding"]["node"] == node]
    if len(matches) != 1: raise ValueError("Unknown or ambiguous asset/node binding")
    item = matches[0]
    lookup = {v["observation"]["record_digest"]: v for v in data["observations"]}
    rows = [lookup[r] for r in item["observation_refs"]]
    return {"snapshot_ref": snapshot["snapshot_ref"], "evidence_id": snapshot["evidence_id"], "result_id": snapshot["result_id"],
            "mode": data["mode"], "binding": deepcopy(item), "count": len(rows), "items": deepcopy(rows[offset:offset+limit]),
            "next_offset": offset+limit if offset+limit < len(rows) else None, "claims": deepcopy(CLAIMS)}
