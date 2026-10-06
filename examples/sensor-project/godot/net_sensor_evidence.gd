class_name NETSensorEvidence
extends RefCounted
## Read-only binding component for an existing scene; not a physics/runtime owner.
## The host explicitly calls this on an approved NET-exported view and scene root.
var _projection: Dictionary = {}
var _bindings: Dictionary = {}

func load_view(path: String, expected_sha256: String, asset_sha256: String, scene_root: Node) -> Dictionary:
    _projection.clear()
    _bindings.clear()
    if not expected_sha256.begins_with("sha256:") or not asset_sha256.begins_with("sha256:"):
        return {"status": "REFUSED", "reason": "Require operator-bound view and asset hashes"}
    var file := FileAccess.open(path, FileAccess.READ)
    if file == null or file.get_length() > 8388608:
        return {"status": "REFUSED", "reason": "View missing or out of budget"}
    var raw := file.get_buffer(file.get_length())
    file.close()
    var hash := HashingContext.new()
    hash.start(HashingContext.HASH_SHA256)
    hash.update(raw)
    if "sha256:" + hash.finish().hex_encode() != expected_sha256:
        return {"status": "REFUSED", "reason": "View bytes differ from operator anchor"}
    var parsed: Variant = JSON.parse_string(raw.get_string_from_utf8())
    if not (parsed is Dictionary) or parsed.get("schema") != "ciw.sensor-view.v1":
        return {"status": "REFUSED", "reason": "Wrong view schema"}
    if parsed.get("mode") not in ["REPLAY", "SIMULATION", "COUNTERFACTUAL"]:
        return {"status": "REFUSED", "reason": "Retained input cannot be presented as LIVE"}
    if parsed.get("claims", {}).get("equipment_control", true) != false:
        return {"status": "REFUSED", "reason": "No machine authority in this component"}
    if scene_root == null:
        return {"status": "REFUSED", "reason": "Require existing scene root"}
    var staged: Dictionary = {}
    var missing: Array = []
    for item: Dictionary in parsed.get("bindings", []):
        if item.get("asset_ref") != asset_sha256:
            continue
        var selector: String = item.get("binding", {}).get("node", "")
        if selector.is_empty() or selector.begins_with("/") or ".." in selector.split("/") or ":" in selector:
            return {"status": "REFUSED", "reason": "Invalid relative scene selector"}
        var node := scene_root.get_node_or_null(NodePath(selector))
        if node == null:
            missing.append(selector)
        else:
            if staged.has(node.get_instance_id()):
                return {"status": "REFUSED", "reason": "Ambiguous scene node"}
            staged[node.get_instance_id()] = item.duplicate(true)
    _bindings = staged
    _projection = parsed.duplicate(true)
    return {"status": "RETAINED_VIEW", "mode": parsed.mode, "bound_nodes": _bindings.size(), "missing_nodes": missing,
            "asset_binding": "caller_bound_not_geometry_verification", "equipment_control": false}

func inspect_node(node: Node) -> Dictionary:
    if not _bindings.has(node.get_instance_id()):
        return {"status": "REFUSED", "reason": "Node has no retained NET binding"}
    var binding: Dictionary = _bindings[node.get_instance_id()]
    var rows: Array = []
    for row: Dictionary in _projection.get("observations", []):
        if row.get("observation", {}).get("record_digest") in binding.get("observation_refs", []):
            rows.append(row.duplicate(true))
    return {"status": "RETAINED_VIEW", "mode": _projection.mode, "source_snapshot_ref": _projection.source_snapshot_ref,
            "binding": binding.duplicate(true), "observations": rows, "claims": _projection.claims.duplicate(true)}
