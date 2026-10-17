"""Bounded glTF 2.0 reference ingress; original bytes remain authoritative.

One indexed triangle primitive, POSITION and optional NORMAL, one mesh instance,
local .bin buffers, affine scene transforms. No fetching, welding, resampling,
asset replacement, material conversion, animation, skinning or extension support.
The unchanged native mesh solver receives an explicitly selected whole indexed
component: coincident seam coordinates never invent adjacency across components.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import math
import re
import struct

from .adapters.subprocess import _json
from .control_contracts import bytes_ref, keys, text
from .telemetry import canonical, digest

SCHEMA = "ciw.gltf-reference.v1"
MAX_BYTES = 48 * 1024  # Base64 stays within the retained native string contract.
AUTHORITY = {"native_gltf": "authoritative", "welding": "not_performed",
             "physical_validation": "not_established", "state_admission": "not_performed"}


def _int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("glTF integer is outside the bounded profile")
    return value


def _fields(value, required, optional=()):
    if type(value) is not dict or not set(required) <= set(value) <= set(required) | set(optional):
        raise ValueError("Unsupported glTF fields, extension or resource")


def _vector(value, size):
    if type(value) is not list or len(value) != size or any(
            type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e6 for x in value):
        raise ValueError("glTF coordinates require finite bounded numbers")
    return value


def buffer_name(value):
    # Deliberately narrower than URI syntax: no schemes, slash, escapes, query,
    # fragment, dot segments, data URIs, drive paths or platform separators.
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.bin", value):
        raise ValueError("glTF requires a plain local .bin basename; no external URI or traversal")
    return value


def _blob(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("glTF bytes exceed the bounded profile")
    return {"bytes_b64": base64.b64encode(raw).decode(), "sha256": bytes_ref(raw), "byte_count": len(raw)}


def _unblob(value):
    keys(value, {"bytes_b64", "sha256", "byte_count"})
    if type(value["bytes_b64"]) is not str or len(value["bytes_b64"]) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("glTF retained bytes exceed budget")
    try:
        raw = base64.b64decode(value["bytes_b64"], validate=True)
    except ValueError as exc:
        raise ValueError("Malformed glTF retained bytes") from exc
    if _blob(raw) != value or type(value["byte_count"]) is not int:
        raise ValueError("glTF retained bytes identity mismatch")
    return raw


def _table(document, key, maximum):
    value = document[key]
    if type(value) is not list or not 1 <= len(value) <= maximum:
        raise ValueError("glTF table exceeds bounded profile: " + key)
    return value


def _at(values, index):
    return values[_int(index, 0, len(values) - 1)]


def _multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _transform(node):
    identity = [[float(i == j) for j in range(4)] for i in range(4)]
    if "matrix" in node:
        if any(k in node for k in ("translation", "rotation", "scale")):
            raise ValueError("glTF matrix and TRS cannot coexist")
        m = _vector(node["matrix"], 16)
        matrix = [[m[4*j+i] for j in range(4)] for i in range(4)]
        if matrix[3] != [0, 0, 0, 1]:
            raise ValueError("Only affine glTF transforms are supported")
        return matrix
    t = _vector(node.get("translation", [0, 0, 0]), 3)
    s = _vector(node.get("scale", [1, 1, 1]), 3)
    x, y, z, w = _vector(node.get("rotation", [0, 0, 0, 1]), 4)
    if abs(x*x + y*y + z*z + w*w - 1) > 1e-7:
        raise ValueError("glTF quaternion must be normalized; no repair")
    r = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
         [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
         [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
    for i in range(3):
        for j in range(3):
            identity[i][j] = r[i][j] * s[j]
        identity[i][3] = t[i]
    return identity


def decode(raw, buffers):
    """Decode detached in-memory bytes only. Unsupported inputs fail closed."""
    try:
        return _decode(raw, buffers)
    except (KeyError, IndexError, TypeError, OverflowError, RecursionError, struct.error) as exc:
        raise ValueError("Malformed bounded glTF reference") from exc


def _decode(raw, buffers):
    _blob(raw)
    document = _json(raw)
    _fields(document, {"asset", "scenes", "nodes", "meshes", "accessors", "bufferViews", "buffers"}, {"scene", "materials"})
    _fields(document["asset"], {"version"}, {"generator", "copyright"})
    if document["asset"]["version"] != "2.0":
        raise ValueError("Only glTF 2.0 is supported")
    declarations = _table(document, "buffers", 4)
    if type(buffers) is not dict or len(buffers) != len(declarations):
        raise ValueError("Supply exactly the declared local buffers")
    binary, names = [], []
    for declaration in declarations:
        _fields(declaration, {"byteLength", "uri"}, {"name"})
        name = buffer_name(declaration["uri"])
        if name in names or name not in buffers:
            raise ValueError("Missing or repeated glTF buffer")
        names.append(name)
        data = buffers[name]
        _blob(data)
        if len(data) != _int(declaration["byteLength"], 1, MAX_BYTES):
            raise ValueError("glTF buffer byteLength mismatch")
        binary.append(data)
    if sum(map(len, binary)) + len(raw) > MAX_BYTES:
        raise ValueError("Combined glTF input exceeds budget")
    views = _table(document, "bufferViews", 16)
    for view in views:
        _fields(view, {"buffer", "byteLength"}, {"byteOffset", "byteStride", "target", "name"})
        data = _at(binary, view["buffer"])
        offset = _int(view.get("byteOffset", 0), 0, len(data))
        length = _int(view["byteLength"], 1, len(data))
        if offset + length > len(data):
            raise ValueError("glTF bufferView out of bounds")
        if "byteStride" in view and (_int(view["byteStride"], 4, 252) % 4):
            raise ValueError("glTF byteStride must be a multiple of four")
        if "target" in view and view["target"] not in (34962, 34963):
            raise ValueError("Unsupported glTF buffer target")
    accessors = _table(document, "accessors", 3)
    used = set()

    def accessor(index, position=False, normal=False):
        item = _at(accessors, index)
        used.add(index)
        _fields(item, {"bufferView", "componentType", "count", "type"}, {"byteOffset", "min", "max", "normalized", "name"})
        if "normalized" in item and item["normalized"] is not False:
            raise ValueError("Normalized glTF accessor is unsupported")
        vector = position or normal
        width, fmt = (3, "f") if vector else (1, {5121: "B", 5123: "H", 5125: "I"}.get(item["componentType"]))
        if (vector and item["componentType"] != 5126) or fmt is None or item["type"] != ("VEC3" if vector else "SCALAR"):
            raise ValueError("Unsupported glTF accessor component/type")
        size = struct.calcsize("<" + fmt)
        count = _int(item["count"], 3, 256 if vector else 1536)
        view = _at(views, item["bufferView"])
        offset = _int(item.get("byteOffset", 0), 0, view["byteLength"])
        stride = view.get("byteStride", size * width)
        if not vector and "byteStride" in view:
            raise ValueError("glTF index bufferView cannot have byteStride")
        start = view.get("byteOffset", 0) + offset
        if offset % size or start % size or stride < size*width or (vector and (start % 4 or stride % 4)):
            raise ValueError("Misaligned glTF accessor or stride")
        if offset + (count - 1) * stride + size * width > view["byteLength"]:
            raise ValueError("glTF accessor out of bounds")
        data = _at(binary, view["buffer"])
        values = [list(struct.unpack_from("<" + fmt*width, data, start + i*stride)) for i in range(count)]
        for row in values:
            _vector(row, width)
        for key, function in (("min", min), ("max", max)):
            if key in item and _vector(item[key], width) != [function(row[i] for row in values) for i in range(width)]:
                raise ValueError("glTF accessor bounds contradict data")
        if position and not {"min", "max"} <= set(item):
            raise ValueError("POSITION accessor requires exact bounds")
        return values

    meshes = _table(document, "meshes", 1)
    _fields(meshes[0], {"primitives"}, {"name"})
    if type(meshes[0]["primitives"]) is not list or len(meshes[0]["primitives"]) != 1:
        raise ValueError("Select one indexed triangle primitive")
    primitive = meshes[0]["primitives"][0]
    _fields(primitive, {"attributes", "indices"}, {"mode", "material"})
    if type(primitive.get("mode", 4)) is not int or primitive.get("mode", 4) != 4:
        raise ValueError("Only glTF TRIANGLES mode is supported")
    _fields(primitive["attributes"], {"POSITION"}, {"NORMAL"})
    positions = accessor(primitive["attributes"]["POSITION"], position=True)
    normals = accessor(primitive["attributes"]["NORMAL"], normal=True) if "NORMAL" in primitive["attributes"] else None
    if normals is not None and len(normals) != len(positions):
        raise ValueError("glTF NORMAL count differs from POSITION")
    indices = [row[0] for row in accessor(primitive["indices"])]
    if len(indices) % 3 or any(i >= len(positions) for i in indices):
        raise ValueError("glTF triangle indices out of bounds")
    triangles = [indices[i:i+3] for i in range(0, len(indices), 3)]
    if len(used) != len(accessors) or any(len(set(face)) != 3 for face in triangles):
        raise ValueError("Unused accessors or degenerate index triples unsupported")
    if "materials" in document:
        materials = _table(document, "materials", 4)
        for material in materials:
            _fields(material, set(), {"name", "pbrMetallicRoughness", "doubleSided", "alphaMode", "alphaCutoff", "emissiveFactor"})
            if "pbrMetallicRoughness" in material:
                pbr = material["pbrMetallicRoughness"]
                _fields(pbr, set(), {"baseColorFactor", "metallicFactor", "roughnessFactor"})
                for name, width in (("baseColorFactor", 4), ("metallicFactor", 1), ("roughnessFactor", 1)):
                    if name in pbr:
                        _vector(pbr[name] if width > 1 else [pbr[name]], width)
        if "material" in primitive:
            _at(materials, primitive["material"])
    elif "material" in primitive:
        raise ValueError("glTF references absent material")
    nodes = _table(document, "nodes", 32)
    scenes = _table(document, "scenes", 1)
    _int(document.get("scene", 0), 0, 0)
    _fields(scenes[0], {"nodes"}, {"name"})
    reached, instances = set(), []

    def visit(index, parent):
        node = _at(nodes, index)
        if index in reached:
            raise ValueError("glTF node cycle or repeated scene instance")
        reached.add(index)
        _fields(node, set(), {"mesh", "children", "matrix", "translation", "rotation", "scale", "name"})
        matrix = _multiply(parent, _transform(node))
        if "mesh" in node:
            _int(node["mesh"], 0, 0)
            instances.append((index, matrix))
        children = node.get("children", [])
        if type(children) is not list or len(children) > 32:
            raise ValueError("Invalid glTF node children")
        for child in children:
            visit(child, matrix)
    roots = scenes[0]["nodes"]
    if type(roots) is not list or not 1 <= len(roots) <= 32:
        raise ValueError("Invalid glTF scene roots")
    for root in roots:
        visit(root, [[float(i == j) for j in range(4)] for i in range(4)])
    if len(instances) != 1 or len(reached) != len(nodes):
        raise ValueError("Require exactly one mesh instance and no hidden nodes")
    node_index, matrix = instances[0]
    world = [[sum(matrix[i][j] * p[j] for j in range(3)) + matrix[i][3] for i in range(3)] for p in positions]
    for p in world:
        _vector(p, 3)
    adjacency = [set() for _ in positions]
    for face in triangles:
        for i in face:
            adjacency[i].update(set(face) - {i})
    if any(not neighbors for neighbors in adjacency):
        raise ValueError("Isolated glTF vertex unsupported")
    components, pending_vertices = [], set(range(len(positions)))
    while pending_vertices:
        reached_component, pending = set(), [min(pending_vertices)]
        while pending:
            vertex = pending.pop()
            if vertex not in reached_component:
                reached_component.add(vertex)
                pending.extend(adjacency[vertex] - reached_component)
        components.append(sorted(reached_component))
        pending_vertices -= reached_component
    return {"positions": positions, "normals": normals, "indices": indices, "triangles": triangles,
            "world_positions": world, "world_matrix_rows": matrix, "mesh_node": node_index,
            "components": components, "coincident_vertex_count": len(world) - len({tuple(p) for p in world}),
            "index_winding": "source_order_unchanged", "seam_identity": "original_accessor_indices"}


def make_record(raw, buffers, *, provenance):
    value = {"schema": SCHEMA, "gltf": _blob(raw), "buffers": {name: _blob(data) for name, data in buffers.items()},
             "provenance": deepcopy(provenance), "decoded": decode(raw, buffers), "authority": deepcopy(AUTHORITY)}
    return validate_record(value)


def validate_record(value):
    keys(value, {"schema", "gltf", "buffers", "provenance", "decoded", "authority"})
    if value["schema"] != SCHEMA or value["authority"] != AUTHORITY or len(canonical(value)) > 4 * MAX_BYTES:
        raise ValueError("Unsupported glTF record schema, authority or budget")
    keys(value["provenance"], {"source", "revision", "license", "attribution"})
    for item in value["provenance"].values():
        text(item)
    if type(value["buffers"]) is not dict or not 1 <= len(value["buffers"]) <= 4:
        raise ValueError("Invalid retained glTF buffers")
    raw = _unblob(value["gltf"])
    buffers = {buffer_name(k): _unblob(v) for k, v in value["buffers"].items()}
    if canonical(value["decoded"]) != canonical(decode(raw, buffers)):
        raise ValueError("glTF decoded geometry contradicts authoritative bytes")
    return deepcopy(value)


def mesh_projection(value, *, source_vertex, target_vertex):
    """Whole indexed component only; no duplicated-coordinate merge is possible."""
    from .geometry_mesh_contract import BUDGET, validate_request
    from .geometry_research import POLICIES
    value = validate_record(value)
    decoded = value["decoded"]
    n = len(decoded["positions"])
    _int(source_vertex, 0, n - 1); _int(target_vertex, 0, n - 1)
    selected = next(c for c in decoded["components"] if source_vertex in c)
    if target_vertex not in selected:
        raise ValueError("Cross-component seam path refused; welding is not performed")
    face_ids = [i for i, face in enumerate(decoded["triangles"]) if face[0] in selected]
    indices = {old: new for new, old in enumerate(selected)}
    mesh = {"vertices": [decoded["world_positions"][i] for i in selected],
            "triangles": [[indices[i] for i in decoded["triangles"][f]] for f in face_ids],
            "units": "m", "coordinate_frame": "glTF 2.0 scene world axes, metres",
            "provenance": {"source": "retained glTF reference " + digest(value), "preprocessing": [
                "glTF accessor decoding and declared scene transform; source winding preserved",
                "Whole index-connected component; index-map receipt=" + digest(selected),
                "No welding, repair, or cross-seam adjacency"]}}
    mesh["mesh_digest"] = digest(mesh)
    request = {"schema": "isgt.edge-geodesic-request.v1", "mesh": mesh,
               "source_vertex": indices[source_vertex], "target_vertex": indices[target_vertex],
               "settings": {"algorithm": "edge_dijkstra", "minimum_triangle_quality": 0,
                            "distance_absolute_tolerance": 1e-12}, "tolerance_budget": deepcopy(BUDGET)}
    validate_request(request)
    mapping = {"schema": "ciw.gltf-mesh-projection.v1", "native_record_ref": digest(value),
               "source_vertex": source_vertex, "target_vertex": target_vertex,
               "local_to_source_vertices": selected, "source_triangle_indices": face_ids,
               "unselected_vertex_count": n - len(selected), "welding": "not_performed",
               "source": {"schema": "ciw.mesh-path-source.v1", "experiment_id": "gltf-component-" + digest(request)[7:23],
                          "configuration": deepcopy(POLICIES["mesh-path"]), "request": request}}
    return mapping


def read_local(path, *, provenance):
    """Read an operator-selected local .gltf and declared sibling .bin files.

    The URI check runs before file access. Symlinks/escapes are refused, and no
    URL, path from a saved workspace, shell, importer or network client is used.
    """
    try:
        return _read_local(path, provenance=provenance)
    except (KeyError, IndexError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError("Malformed bounded local glTF reference") from exc


def _read_local(path, *, provenance):
    from pathlib import Path
    path = Path(path)
    if path.suffix != ".gltf" or path.is_symlink() or not path.is_file():
        raise ValueError("Select a regular local .gltf file")
    root = path.parent.resolve(strict=True)
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    _blob(raw)
    document = _json(raw)
    declarations = _table(document, "buffers", 4)
    buffers = {}
    for item in declarations:
        name = buffer_name(item.get("uri"))
        location = root / name
        if location.is_symlink() or not location.is_file() or location.resolve(strict=True).parent != root:
            raise ValueError("glTF buffer must be a regular sibling file")
        with location.open("rb") as stream:
            buffers[name] = stream.read(MAX_BYTES + 1)
    return make_record(raw, buffers, provenance=provenance)
