"""Opt-in, source-hash-bound Box qualification through existing NET consumers.

No download or publisher data is distributed by this script. Inputs are retained
locally, including CC-BY attribution. This is a six-component seam-preserving
mesh-path and full Godot import qualification, not a general glTF, continuum
geodesic, collision-model or physical-validation claim.
"""
from __future__ import annotations

import argparse
import base64
from hashlib import sha1, sha256
import json
from pathlib import Path
import platform

from ciw import gltf_reference as gltf, gltf_reference_runtime as bridge
from ciw.data_sources import retain_record
from ciw.instruments import make_demo_run
from ciw.interactive_simulation import file_sha
from ciw.operations.registry import default_registry
from ciw.session import Session
from ciw.telemetry import canonical

REVISION = "edc7c9e67c639d230715049ee31f9a96a6babbbe"
HASHES = {
    "Models/Box/glTF/Box.gltf": "4a0d69eecfce0672a50b71dc218cbacec6c53fe2445040c235c6314b1b2c41b9",
    "Models/Box/glTF/Box0.bin": "3266a8e39b9f425b3341cbe5eec7849f44310256bfa651e6b8b40c85ce0ccafb",
    "Models/Box/LICENSE.md": "2759bbffe3b8aebe789958875a098045fe4039eb3e2291ef0fd09c65994bdddc",
    "Models/Box/README.md": "ae63b4ad1c8136b881686e66faf930c9b9e71fa823a7a4b1738076ae06739c35",
}


def checked_sources(root):
    with (root / "acquisition.json").open("rb") as stream:
        acquisition_raw = stream.read(1024*1024 + 1)
    if len(acquisition_raw) > 1024*1024:
        raise ValueError("Acquisition receipt exceeds budget")
    acquisition = json.loads(acquisition_raw)
    rows = {row["path"]: row for row in acquisition["files"] if row.get("source_id") == "gltf_box"}
    for relative, expected in HASHES.items():
        row = rows["sources/gltf_box/" + relative]
        path = root / "sources/gltf_box" / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source path is not a contained regular publisher input")
        with path.open("rb") as stream:
            raw = stream.read(gltf.MAX_BYTES + 1)
        if sha256(raw).hexdigest() != expected or row["sha256"] != expected or row["bytes"] != len(raw):
            raise ValueError("Publisher source SHA256/length mismatch")
        if row["commit"] != REVISION or row["repository"] != "KhronosGroup/glTF-Sample-Assets":
            raise ValueError("Publisher revision/repository mismatch")
        if sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != row["git_blob_sha1"]:
            raise ValueError("Publisher Git blob mismatch")
    return {"acquisition_sha256": sha256(acquisition_raw).hexdigest(), "files": rows,
            "repository": "KhronosGroup/glTF-Sample-Assets", "revision": REVISION}


def call(session, kind, payload):
    result = session.handle({"protocol_version":1,"request_id":"gltf-qualification","type":kind,"payload":payload})
    if result["type"] != "response":
        raise ValueError(result)
    return result["payload"]


def execute(session, operation, parameters):
    result = call(session, "operation.execute", {"operation_id":operation, "parameters":parameters})
    if result.get("status") == "refused":
        raise ValueError(result)
    return result


def qualify(root, output, provider, godot, stamp):
    import ciw
    provenance = checked_sources(root)
    output.mkdir(parents=True, exist_ok=False)
    native = gltf.read_local(root / "sources/gltf_box/Models/Box/glTF/Box.gltf", provenance={
        "source":"https://github.com/KhronosGroup/glTF-Sample-Assets", "revision":REVISION,
        "license":"CC-BY-4.0", "attribution":"Cesium, 2017; Box. Publisher model and metadata retained locally."})
    assert len(native["decoded"]["positions"]) == 24
    assert len(native["decoded"]["triangles"]) == 12
    assert len(native["decoded"]["components"]) == 6
    assert native["decoded"]["coincident_vertex_count"] == 16
    registry=default_registry()
    binding=bridge.GodotRuntime(godot,expected_sha256=file_sha(godot))
    registry.register(binding.operation())
    session=Session(make_demo_run(),output/"session",operations=registry)
    session.workbench.bind_workflow("mesh-path",{"isgt":str(provider)})
    envelope=retain_record(native,dataset_id="khronos-box-pinned",revision=1,parent_source_id=None,
        recorded_at=stamp,data_family="gltf-reference")
    source=session.workbench.add_source({"kind":"data-record","label":"Pinned publisher Box (CC-BY-4.0, Cesium)",
        "bytes_b64":base64.b64encode(canonical(envelope)).decode()})
    records=[]
    for number,component in enumerate(native["decoded"]["components"]):
        for target,expected in ((component[1],1.0),(component[3],2.0)):
            params=bridge.source_parameters(session.workbench,source["source_id"],source_vertex=component[0],target_vertex=target)
            projection=execute(session,bridge.PROJECT,params)
            mapped=projection["result"]["data"]
            assert mapped["local_to_source_vertices"]==component
            selected=bridge.retain_mesh_source(session.workbench,params,label=f"Box component {number}, target {target}")
            ref=execute(session,"ciw.mesh-path.v1",{"source_id":selected["source_id"]})
            bundle=call(session,"bundle.get",{"bundle_id":ref["bundle_id"]})
            data=bundle["steps"][0]["result"]["data"]
            assert data["solution"]["target_distance"]==expected
            assert data["request"]["mesh"]["vertices"]==[native["decoded"]["world_positions"][i] for i in component]
            assert data["mesh_quality"]["component_count"]==1
            lifted=[component[i] for i in data["solution"]["target_path"]]
            assert lifted[0]==component[0] and lifted[-1]==target
            records.append({"source_pair":[component[0],target],"distance":expected,
                "source_index_path":lifted,"projection_result_id":projection["result"]["result_id"],
                "mesh_source_id":selected["source_id"],"bundle_id":ref["bundle_id"],
                "numerical_result_id":bundle["steps"][0]["numerical_result_id"]})
    for pair in ((0,4),(0,16),(3,23)):
        try:
            gltf.mesh_projection(native,source_vertex=pair[0],target_vertex=pair[1])
        except ValueError as error:
            assert "Cross-component" in str(error)
        else:
            raise AssertionError("Cross-seam request unexpectedly accepted")
    params=bridge.source_parameters(session.workbench,source["source_id"])
    imported=execute(session,bridge.INSPECT,params)
    assert imported["result"]["data"]["comparison"]["vertex_count"]==24
    assert imported["result"]["data"]["comparison"]["maximum_coordinate_error"]==0
    saved=session.save_workspace(output/"workspace.json")
    # Fresh offline Session deliberately has neither engine nor ISGT binding.
    restored=Session.from_workspace(saved,output_dir=output/"offline")
    assert restored.results==session.results and restored.workbench.serialize()==session.workbench.serialize()
    refused=call(restored,"operation.execute",{"operation_id":bridge.INSPECT,"parameters":params})
    assert refused["status"]=="refused" and refused["execution"]["refusal"]["code"]=="operation_unavailable"
    unbound=restored.handle({"protocol_version":1,"request_id":"unbound-mesh","type":"bundle.replay",
                           "payload":{"bundle_id":records[0]["bundle_id"]}})
    assert unbound["type"]=="error"
    restored.operations=registry
    restored.workbench.bind_workflow("mesh-path",{"isgt":str(provider)})
    fresh=execute(restored,bridge.INSPECT,params)
    assert fresh["result"]["data"]==imported["result"]["data"]
    assert fresh["execution"]["execution_id"]!=imported["execution"]["execution_id"]
    replay_ref=call(restored,"bundle.replay",{"bundle_id":records[0]["bundle_id"]})["bundle"]
    replay=call(restored,"bundle.get",{"bundle_id":replay_ref["bundle_id"]})
    assert replay["steps"][0]["numerical_result_id"]==records[0]["numerical_result_id"]
    restored.save_workspace(output/"replayed-workspace.json")
    report={"schema":"ciw.gltf-reference-qualification.v1","source":provenance,
        "installed_module":ciw.__file__,"python":platform.python_version(),"source_id":source["source_id"],
        "mesh_cases":records,"mesh_case_count":len(records),"source_vertices":24,"source_triangles":12,
        "index_components":6,"coincident_vertices_preserved":16,"cross_seam_refusals":3,
        "godot_runtime":binding.runtime_identity(),"godot_comparison":imported["result"]["data"]["comparison"],
        "offline_reopen":"passed_without_provider_binding","fresh_native_replays":"ISGT_and_Godot_passed",
        "asset_promotion":"not_performed","physical_validation":"not_established",
        "source_publication":"not_performed","workspace_sha256":file_sha(saved)}
    (output/"qualification.json").write_bytes(canonical(report))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition-root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--isgt",type=Path,required=True)
    parser.add_argument("--godot",type=Path,required=True)
    parser.add_argument("--recorded-at",required=True)
    args=parser.parse_args()
    print(json.dumps(qualify(args.acquisition_root,args.output,args.isgt,args.godot,args.recorded_at),indent=2))


if __name__=="__main__":
    main()
