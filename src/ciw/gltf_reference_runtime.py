"""Existing mesh/Godot consumers behind retained Session operations.

Runtime paths come only from explicit process-local operator bindings. Offline
validation checks retained comparisons and identities, never executes an engine
and cannot authenticate a fabricated historical execution. No asset is promoted.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
import math
import re
import tempfile

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import _json
from .control_contracts import bytes_ref, keys
from .gltf_reference import SCHEMA, _unblob, mesh_projection, validate_record
from .operations.registry import Operation
from .telemetry import canonical, digest

PROJECT = "data.project-gltf-mesh.v1"
INSPECT = "data.inspect-gltf.v1"
OPERATION_IDS = frozenset({PROJECT, INSPECT})


def _source(parameters, operation):
    from .data_sources import native_record
    keys(parameters, {"source", "source_vertex", "target_vertex"} if operation == PROJECT else {"source"})
    source = parameters["source"]
    if type(source) is not dict or source.get("kind") != "data-record":
        raise ValueError("glTF consumer requires a retained data-record source")
    record = native_record(source)
    if record["schema"] != SCHEMA:
        raise ValueError("glTF consumer requires its exact source schema")
    return validate_record(record)


def source_parameters(workbench, source_id, *, source_vertex=None, target_vertex=None):
    value = {"source": workbench.get_source(source_id)}
    if source_vertex is not None or target_vertex is not None:
        value.update(source_vertex=source_vertex, target_vertex=target_vertex)
    return value


def project(parameters):
    record = _source(parameters, PROJECT)
    return {"source_id": parameters["source"]["source_id"], **mesh_projection(record,
            source_vertex=parameters["source_vertex"], target_vertex=parameters["target_vertex"])}


def projection_operation():
    return Operation(PROJECT, "backend", lambda run, p: project(p), lambda: {
        "provider": "ciw.gltf-reference.component-projection", "version": "1",
        "adapter_sha256": bytes_ref(Path(__file__).with_name("gltf_reference.py").read_bytes()),
        "mesh_solver": "not_executed_by_projection"})


def retain_mesh_source(workbench, parameters, *, label):
    """Add the same checked projection with an automatic source dependency."""
    validate_live_dependencies(PROJECT, parameters, workbench.retained_sources())
    mapped = project(parameters)
    return workbench.add_source({"kind": "mesh-path", "label": label,
        "bytes_b64": base64.b64encode(canonical(mapped["source"])).decode(),
        "input_source_ids": [mapped["source_id"]]})


def _request(parameters):
    record = _source(parameters, INSPECT)
    return {"schema": "ciw.gltf-import-request.v1", "source_id": parameters["source"]["source_id"],
            "native_record_ref": digest(record), "asset_sha256": record["gltf"]["sha256"]}


def _comparison(record, observation, request):
    keys(observation, {"schema", "request", "asset_sha256", "engine_version", "parts", "rays"})
    rays = observation["rays"]
    if type(rays) is not list or len(rays) != 6:
        raise ValueError("Require six retained legacy probe observations")
    for ray in rays:
        keys(ray, {"from", "to", "hit", "position", "name"})
        if type(ray["hit"]) is not bool:
            raise ValueError("Malformed legacy probe hit")
        for field in ("from", "to", "position"):
            if field == "position" and ray[field] is None and not ray["hit"]:
                continue
            point = ray[field]
            if (type(point) is not list or len(point) != 3 or any(type(x) not in (int, float)
                    or not math.isfinite(x) for x in point)):
                raise ValueError("Malformed legacy probe coordinates")
        if ((ray["hit"] and (type(ray["name"]) is not str or not ray["name"]))
                or (not ray["hit"] and (ray["position"] is not None or ray["name"] is not None))):
            raise ValueError("Malformed legacy probe collider")
    if (observation.get("schema") != "ciw.workbench-import.v1" or observation.get("request") != request
            or observation.get("asset_sha256") != record["gltf"]["sha256"]
            or type(observation.get("engine_version")) is not str
            or not re.fullmatch(r"4\.\d+\.\d+-stable \(official\)", observation["engine_version"])):
        raise ValueError("Godot source/request/version binding differs")
    parts = observation.get("parts")
    if type(parts) is not dict or len(parts) != 1:
        raise ValueError("Expected one imported mesh instance")
    part = next(iter(parts.values()))
    keys(part, {"min", "max", "vertices", "triangles", "surfaces"})
    expected = record["decoded"]
    if (type(part["vertices"]) is not int or part["vertices"] != len(expected["positions"])
            or type(part["triangles"]) is not int or part["triangles"] != len(expected["triangles"])
            or type(part["surfaces"]) is not list or len(part["surfaces"]) != 1):
        raise ValueError("Godot altered surface or seam vertex counts")
    surface = part["surfaces"][0]
    keys(surface, {"local_positions", "world_positions", "indices"})
    # Godot's clockwise surface convention is the explicit, reversible import
    # mapping. No sorting, welding, vertex permutation or best-fit match is used.
    expected_indices = [i for a, b, c in expected["triangles"] for i in (a, c, b)]
    if canonical(surface["indices"]) != canonical(expected_indices):
        raise ValueError("Godot topology differs from explicit reversed triangle winding")
    maximum_error = 0.0
    for name, reference in (("local_positions", expected["positions"]),
                            ("world_positions", expected["world_positions"])):
        points = surface[name]
        if type(points) is not list or len(points) != len(reference):
            raise ValueError("Godot changed vertex order/count")
        for actual, target in zip(points, reference):
            if type(actual) is not list or len(actual) != 3:
                raise ValueError("Malformed Godot vertex")
            for x, y in zip(actual, target):
                if type(x) not in (float, int) or not math.isfinite(x) or abs(x-y) > 2e-6 * max(1, abs(y)):
                    raise ValueError("Godot geometry differs from declared source transform")
                maximum_error = max(maximum_error, abs(x-y))
    for key, fn in (("min", min), ("max", max)):
        target = [fn(p[i] for p in surface["world_positions"]) for i in range(3)]
        if part[key] != target:
            raise ValueError("Godot retained bounds contradict imported geometry")
    return {"vertex_identity": "source_accessor_order_including_seams",
            "indices": "exact_source_triangles_with_corner_1_2_swap",
            "vertex_count": len(expected["positions"]), "triangle_count": len(expected["triangles"]),
            "maximum_coordinate_error": maximum_error, "coordinate_tolerance": "2e-6*max(1,abs(source_coordinate))",
            "tolerance_scope": "engine_binary32_conversion_only_not_physical_accuracy",
            "welding": "not_performed", "asset_promotion": "not_performed",
            "collision_probe_scope": "legacy_workbench_probes_retained_not_box_acceptance",
            "state_admission": "not_performed"}


def validate_payload(operation_id, data, run, parameters, selection):
    if operation_id == PROJECT:
        if canonical(data) != canonical(project(parameters)):
            raise ValueError("glTF projection differs from retained source")
        return
    if operation_id != INSPECT:
        raise ValueError("Unsupported glTF operation")
    keys(data, {"schema", "source_id", "native_record_ref", "observation", "comparison", "logs"})
    record = _source(parameters, INSPECT)
    if (data["schema"] != "ciw.gltf-import-evidence.v1" or data["source_id"] != parameters["source"]["source_id"]
            or data["native_record_ref"] != digest(record)):
        raise ValueError("Godot retained input identity differs")
    if canonical(data["comparison"]) != canonical(_comparison(record, data["observation"], _request(parameters))):
        raise ValueError("Godot retained comparison differs")
    keys(data["logs"], {"stdout", "stderr"})
    for log in data["logs"].values():
        keys(log, {"utf8", "sha256"})
        if (type(log["utf8"]) is not str or len(log["utf8"].encode()) > 16384
                or bytes_ref(log["utf8"].encode()) != log["sha256"] or "ERROR:" in log["utf8"]):
            raise ValueError("Godot log identity or runtime error")


def validate_live_dependencies(operation_id, parameters, sources):
    if operation_id not in OPERATION_IDS:
        return
    _source(parameters, operation_id)
    source = parameters["source"]
    if source["source_id"] not in sources or canonical(sources[source["source_id"]]) != canonical(source):
        raise ValueError("glTF requires a source present unchanged in this Workbench")


def validate_saved_dependencies(results, sources):
    for result in results.values():
        validate_live_dependencies(result["operation_id"], result["parameters"], sources)


class GodotRuntime:
    """Explicit trusted executable; disposable directories are not a sandbox."""
    def __init__(self, executable, *, expected_sha256):
        from .foundry_asset_inspector import GODOT_SCRIPT, PROJECT as GODOT_PROJECT
        from .interactive_simulation import file_sha
        self.executable = Path(executable).expanduser().resolve(strict=True)
        if not self.executable.is_file() or file_sha(self.executable) != expected_sha256:
            raise ValueError("Godot executable digest mismatch")
        self.script, self.project = GODOT_SCRIPT.encode(), GODOT_PROJECT
        self.identity = {"provider": "ciw.foundry_asset_inspector.GODOT_SCRIPT", "execution_mode": "native_gltf_import",
            "executable_sha256": expected_sha256, "script_sha256": bytes_ref(self.script),
            "project_sha256": bytes_ref(self.project), "adapter_sha256": bytes_ref(Path(__file__).read_bytes()),
            "scope": "trusted executable/scripts; shared libraries and external reads not attested"}

    def runtime_identity(self):
        from .interactive_simulation import file_sha
        if file_sha(self.executable) != self.identity["executable_sha256"]:
            raise ValueError("Bound Godot executable changed")
        return deepcopy(self.identity)

    def operation(self):
        return Operation(INSPECT, "backend", lambda run, p: self.execute(p), self.runtime_identity)

    def execute(self, parameters):
        from .interactive_simulation import run_process
        record = _source(parameters, INSPECT)
        request = _request(parameters)
        self.runtime_identity()
        try:
            with tempfile.TemporaryDirectory(prefix="net-gltf-import-") as temporary:
                root = Path(temporary)
                fixed = {"asset.gltf": _unblob(record["gltf"]), "inspect.gd": self.script,
                         "project.godot": self.project, "request.json": canonical(request),
                         **{name: _unblob(data) for name, data in record["buffers"].items()}}
                for name, raw in fixed.items():
                    (root / name).write_bytes(raw)
                output = root / "observations.json"
                run_process([str(self.executable), "--headless", "--path", str(root), "--script", "res://inspect.gd",
                    "--", str(root / "asset.gltf"), str(root / "request.json"), str(output)], root, [output], timeout=30)
                with output.open("rb") as stream:
                    raw = stream.read(256*1024 + 1)
                if len(raw) > 256*1024:
                    raise ValueError("Godot observation exceeds budget")
                observation = _json(raw)
                logs = {}
                for name in ("stdout", "stderr"):
                    with (root / (name + ".log")).open("rb") as stream:
                        raw = stream.read(16385)
                    if len(raw) > 16384:
                        raise ValueError("Godot log exceeds budget")
                    logs[name] = {"utf8": raw.decode(), "sha256": bytes_ref(raw)}
                if any((root / name).read_bytes() != raw for name, raw in fixed.items()):
                    raise ValueError("Godot changed fixed input bytes")
                data = {"schema": "ciw.gltf-import-evidence.v1", "source_id": parameters["source"]["source_id"],
                    "native_record_ref": digest(record), "observation": observation,
                    "comparison": _comparison(record, observation, request), "logs": logs}
                validate_payload(INSPECT, data, {}, parameters, {})
                self.runtime_identity()
                return data
        except (OSError, RuntimeError, ValueError) as exc:
            raise AdapterRefusal("gltf_import_refused", str(exc)[:4096]) from exc
