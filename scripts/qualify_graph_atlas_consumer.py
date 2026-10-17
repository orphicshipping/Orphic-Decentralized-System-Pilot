"""Opt-in Graph Atlas qualification of native Compute projection and F2 topology.

Consumes only the pinned externally acquired atlas, never its Python programs.
Undirected edges become two explicitly labelled directed arcs; node fields keep
isolates and cardinality fields keep the empty graph. This is representation
qualification plus the existing bounded F2 reference, not a graph-learning score,
cryptographic proof, or physical claim.
No recording, acquisition clock, positions, weights or uncertainty is invented.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path

from ciw import data_compute_bridge as bridge, proof_f2
from ciw.data_sources import native_record, retain_record
from ciw.workbench import Workbench

ATLAS_SHA256 = "73fc416df0164923607751cb759f4ae81deb5f6550bf25be59c86de3b747e41d"
ATLAS_URL = "https://networkx.org/documentation/stable/reference/generated/networkx.generators.atlas.graph_atlas_g.html"
MAX_COMPRESSED = 16384
MAX_EXPANDED = 100000
MAX_BATCH = 32
ORDER_COUNTS = {0: 1, 1: 1, 2: 2, 3: 4, 4: 11, 5: 34, 6: 156, 7: 1044}
STATE_SCHEMA = "ciw.graph-atlas-paired-arcs.v1"
POLICY = {
    "edge_mapping": "one_undirected_source_edge_to_two_ordered_directed_arcs",
    "node_mapping": "one_dimensionless_index_field_per_source_node_including_isolates",
    "empty_graph_mapping": "source_node_count_field_retains_zero_node_graph",
    "timestamp": "retention_time_only_not_an_acquisition_clock",
    "units": "1_for_integer_indices_and_counts_not_physical_measurements",
    "coordinates_weights_uncertainty": "not_supplied_not_invented",
    "scope": "native_representation_projection_inverse_mapping_and_F2_reference",
    "physical_qualification": "not_established",
    "topology": "ordinary_F2_homology_of_vertices_and_edges_no_clique_filling",
    "proof_execution": "not_performed",
}


def parse_atlas(raw: bytes) -> list[dict]:
    """Read the bounded data grammar without importing NetworkX or corpus code."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_COMPRESSED:
        raise ValueError("Atlas compressed byte budget exceeded")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            expanded = stream.read(MAX_EXPANDED + 1)
        if len(expanded) > MAX_EXPANDED:
            raise ValueError("Atlas expanded byte budget exceeded")
        lines = expanded.decode("ascii").splitlines()
    except (OSError, EOFError, UnicodeError) as exc:
        raise ValueError("Invalid compressed ASCII atlas") from exc
    graphs = []
    position = 0
    while position < len(lines):
        parts = lines[position].split()
        if parts != ["GRAPH", str(len(graphs))] or position + 1 >= len(lines):
            raise ValueError("Require contiguous ordered Graph Atlas identities")
        count = lines[position + 1].split()
        if len(count) != 2 or count[0] != "NODES" or count[1] not in [str(n) for n in range(8)]:
            raise ValueError("Require source node counts between zero and seven")
        graph = {"id": len(graphs), "node_count": int(count[1]), "edges": []}
        position += 2
        seen = set()
        while position < len(lines) and not lines[position].startswith("GRAPH "):
            edge = lines[position].split()
            if len(edge) != 2 or any(v not in [str(n) for n in range(graph["node_count"])] for v in edge):
                raise ValueError("Atlas edge endpoint is outside source nodes")
            a, b = map(int, edge)
            if a == b or tuple(sorted((a, b))) in seen:
                raise ValueError("Atlas contains a loop or duplicate undirected edge")
            seen.add(tuple(sorted((a, b))))
            graph["edges"].append([a, b])
            position += 1
        graphs.append(graph)
        if len(graphs) > 1253:
            raise ValueError("Atlas graph count exceeds fixed profile")
    if not graphs:
        raise ValueError("Atlas is empty")
    return graphs


def read_atlas(path: Path) -> tuple[bytes, list[dict]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Require a regular acquired atlas file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_COMPRESSED + 1)
    if sha256(raw).hexdigest() != ATLAS_SHA256:
        raise ValueError("Atlas source SHA256 differs from the reviewed acquisition")
    graphs = parse_atlas(raw)
    if Counter(g["node_count"] for g in graphs) != ORDER_COUNTS:
        raise ValueError("Complete atlas order counts differ from reviewed reference")
    return raw, graphs


def native_state(graphs: list[dict]) -> dict:
    if not 1 <= len(graphs) <= MAX_BATCH or len({g["id"] for g in graphs}) != len(graphs):
        raise ValueError("Require one to 32 distinct source graphs")
    fields, edges = {}, []
    for graph in graphs:
        prefix = f"atlas/{graph['id']}"
        count_id = prefix + "/node_count"
        fields[count_id] = {"id": count_id, "type": "scalar", "value": graph["node_count"], "unit": "1"}
        for node in range(graph["node_count"]):
            node_id = f"{prefix}/node/{node}"
            fields[node_id] = {"id": node_id, "type": "scalar", "value": node, "unit": "1"}
        for edge_index, (a, b) in enumerate(graph["edges"]):
            for suffix, start, stop in (("forward", a, b), ("reverse", b, a)):
                edges.append({"id": f"{prefix}/edge/{edge_index}/{suffix}",
                    "from": f"{prefix}/node/{start}", "to": f"{prefix}/node/{stop}",
                    "type": "atlas-undirected-paired-arc",
                    "attributes": {"source_graph": graph["id"], "source_edge_index": edge_index,
                                   "source_undirected": True}})
    return {"schema_version": STATE_SCHEMA, "fields": fields, "edges": edges}


def decode_projection(state: dict) -> list[dict]:
    """Independently recover the atlas records, rejecting missing/reordered arcs."""
    if set(state) != {"schema_version", "fields", "edges"} or state["schema_version"] != STATE_SCHEMA:
        raise ValueError("Wrong atlas native state schema")
    counts = {}
    nodes = {}
    for key, field in state["fields"].items():
        pieces = key.split("/")
        if (len(pieces) not in (3, 4) or pieces[0] != "atlas" or not pieces[1].isdigit()
                or field != {"id": key, "type": "scalar", "value": field.get("value"), "unit": "1"}
                or type(field["value"]) is not int):
            raise ValueError("Unexpected native atlas field")
        graph = int(pieces[1])
        if len(pieces) == 3 and pieces[2] == "node_count" and 0 <= field["value"] <= 7:
            counts[graph] = field["value"]
        elif len(pieces) == 4 and pieces[2] == "node" and pieces[3] == str(field["value"]):
            nodes.setdefault(graph, []).append(field["value"])
        else:
            raise ValueError("Unexpected native atlas field identity")
    if set(nodes) - set(counts):
        raise ValueError("Missing graph cardinality")
    result = {graph: {"id": graph, "node_count": count, "edges": []} for graph, count in sorted(counts.items())}
    if not result or len(result) > MAX_BATCH:
        raise ValueError("Wrong native graph batch size")
    for graph, count in counts.items():
        if sorted(nodes.get(graph, [])) != list(range(count)):
            raise ValueError("Source nodes or isolates changed")
    arcs = state["edges"]
    if len(arcs) % 2:
        raise ValueError("Unpaired directed arc")
    last_graph = -1
    for i in range(0, len(arcs), 2):
        forward, reverse = arcs[i:i + 2]
        attributes = forward["attributes"]
        graph, index = attributes.get("source_graph"), attributes.get("source_edge_index")
        if (type(graph) is not int or graph not in result or graph < last_graph
                or type(index) is not int or index != len(result[graph]["edges"])):
            raise ValueError("Source graph or edge order changed")
        prefix = f"atlas/{graph}"
        if (attributes != {"source_graph": graph, "source_edge_index": index, "source_undirected": True}
                or forward["type"] != "atlas-undirected-paired-arc"
                or forward["id"] != f"{prefix}/edge/{index}/forward"):
            raise ValueError("Source undirected edge semantics changed")
        expected_reverse = {**forward, "id": f"{prefix}/edge/{index}/reverse",
                            "from": forward["to"], "to": forward["from"]}
        if reverse != expected_reverse:
            raise ValueError("Inverse directed arc differs")
        endpoint_prefix = prefix + "/node/"
        ends = [forward["from"], forward["to"]]
        if any(end not in state["fields"] or not end.startswith(endpoint_prefix) for end in ends):
            raise ValueError("Edge left its source graph or node set")
        edge = [int(end[len(endpoint_prefix):]) for end in ends]
        if edge[0] == edge[1] or any(set(edge) == set(old) for old in result[graph]["edges"]):
            raise ValueError("Source loop or duplicate edge")
        result[graph]["edges"].append(edge)
        last_graph = graph
    return list(result.values())


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def topology_reference(graph: dict) -> dict:
    """Native boundary elimination versus independent combinatorial graph ranks.

    The graph itself is a one-dimensional complex, including all isolates. We do
    not add triangle faces, fill cliques, posit a mesh, or invoke a proof engine.
    """
    n = graph["node_count"]
    if n == 0:
        try:
            proof_f2.reference({"schema": proof_f2.SCHEMA, "profile": proof_f2.PROFILE,
                "coefficient_field": "F2", "homology": "ordinary", "vertex_count": 0,
                "simplices": [], "candidate_betti": []})
        except ValueError as error:
            return {"graph_id": graph["id"], "status": "EXPECTED_REFUSAL", "reason": str(error),
                    "scope": "native_contract_requires_at_least_one_vertex"}
        raise ValueError("Native F2 unexpectedly accepted an empty complex")
    adjacency = [set() for _ in range(n)]
    for a, b in graph["edges"]:
        adjacency[a].add(b)
        adjacency[b].add(a)
    unseen = set(range(n))
    components = 0
    while unseen:
        pending = [unseen.pop()]
        components += 1
        while pending:
            reached = adjacency[pending.pop()] & unseen
            unseen.difference_update(reached)
            pending.extend(reached)
    edge_count = len(graph["edges"])
    expected = [components] + ([edge_count - n + components] if edge_count else [])
    request = {"schema": proof_f2.SCHEMA, "profile": proof_f2.PROFILE,
        "coefficient_field": "F2", "homology": "ordinary", "vertex_count": n,
        "simplices": [[v] for v in range(n)] + sorted(sorted(edge) for edge in graph["edges"]),
        "candidate_betti": expected}
    wire = proof_f2.encode(request)
    if proof_f2.decode(wire) != request:
        raise ValueError("Native F2 binary interchange changed the graph complex")
    result = proof_f2.reference(request)
    expected_result = {"counts": [n] + ([edge_count] if edge_count else []),
        "ranks": [0] + ([n - components] if edge_count else []),
        "betti": expected, "candidate_matches": True}
    if result != expected_result:
        raise ValueError("Native F2 elimination disagrees with graph component/cycle oracle")
    # Verify that candidate mismatch stays a mismatch rather than being repaired.
    incorrect = {**request, "candidate_betti": [expected[0] + 1, *expected[1:]]}
    if proof_f2.reference(incorrect)["candidate_matches"] is not False:
        raise ValueError("Native F2 accepted an incorrect Betti candidate")
    return {"graph_id": graph["id"], "status": "PASS", "request": request,
        "wire_sha256": sha256(wire).hexdigest(), "result": result,
        "independent_oracle": "graph_traversal_components_and_E_minus_V_plus_components",
        "incorrect_candidate": "mismatch_preserved", "native_reference_executions": 2}


def run_batch(graphs, runtime, destination: Path, stamp: str) -> dict:
    state = native_state(graphs)
    record = bridge.canonical_record(state, timestamp=stamp, provenance={
        "author": "NET bounded academic qualification", "transaction_id": f"atlas-{graphs[0]['id']}-{graphs[-1]['id']}",
        "source": f"sha256:{ATLAS_SHA256}", "timestamp": None})
    envelope = retain_record(record, dataset_id=f"graph-atlas/{graphs[0]['id']}-{graphs[-1]['id']}",
        revision=1, parent_source_id=None, recorded_at=stamp, data_family="compute-canonical-state")
    workbench = Workbench()
    source = workbench.add_source({"kind": "data-record", "label": "Graph Atlas paired-arc projection",
        "bytes_b64": base64.b64encode(json.dumps(envelope).encode()).decode()})
    parameters = bridge.source_parameters(workbench, source["source_id"])
    result = runtime.execute(parameters)
    bridge.validate_payload(bridge.PROJECT, result, {}, parameters, {})
    decoded = decode_projection(result["projection"]["state"])
    if decoded != graphs:
        raise ValueError("Native projection did not reproduce original atlas records")
    saved = workbench.serialize()
    restored = Workbench.restore(saved)
    if restored.serialize() != saved or native_record(restored.get_source(source["source_id"])) != record:
        raise ValueError("Offline restoration changed source data")
    replay = runtime.execute(bridge.source_parameters(restored, source["source_id"]))
    if replay != result or decode_projection(replay["projection"]["state"]) != graphs:
        raise ValueError("Fresh native replay differs from retained projection")
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / "workbench.json", saved)
    write_json(destination / "projection.json", result)
    write_json(destination / "source-graphs.json", graphs)
    return {"status": "PASS", "first_graph": graphs[0]["id"], "last_graph": graphs[-1]["id"],
        "graph_count": len(graphs), "source_id": source["source_id"], "native_version_id": record["version"]["id"],
        "native_executions": 2, "operation_id": bridge.PROJECT,
        "execution_path": "existing_ComputeRuntime.execute_API_not_Session_recording",
        "source_nodes": sum(g["node_count"] for g in graphs), "source_edges": sum(len(g["edges"]) for g in graphs),
        "native_fields": len(state["fields"]), "native_directed_arcs": len(state["edges"]),
        "source_inverse_mapping": "exact_match", "offline_restore": "exact_match", "fresh_replay": "exact_match"}


def qualify(atlas: Path, compute_root: Path, destination: Path) -> dict:
    raw, graphs = read_atlas(atlas)
    runtime = bridge.ComputeRuntime(compute_root)
    identity = runtime.runtime_identity()
    stamp = datetime.now(timezone.utc).isoformat()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "atlas.dat.gz").write_bytes(raw)
    runs = [run_batch(graphs[start:start + MAX_BATCH], runtime, destination / f"batch-{start:04d}", stamp)
            for start in range(0, len(graphs), MAX_BATCH)]
    topology = [topology_reference(graph) for graph in graphs]
    write_json(destination / "f2-topology.json", topology)
    if runtime.runtime_identity() != identity:
        raise ValueError("Runtime identity changed during qualification")
    report = {"schema": "ciw.academic-graph-atlas-qualification.v1", "status": "PASS", "recorded_at": stamp,
        "source_sha256": ATLAS_SHA256, "source_bytes": len(raw), "source_url": ATLAS_URL,
        "runtime": identity, "policy": POLICY, "graphs": len(graphs),
        "mapping_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "f2_runtime_sha256": sha256(Path(proof_f2.__file__).read_bytes()).hexdigest(),
        "f2_nonempty_graphs_passed": sum(row["status"] == "PASS" for row in topology),
        "f2_empty_graph_expected_refusals": sum(row["status"] == "EXPECTED_REFUSAL" for row in topology),
        "f2_reference_executions": sum(row.get("native_reference_executions", 0) for row in topology),
        "order_counts": dict(Counter(g["node_count"] for g in graphs)), "batches": runs,
        "source_nodes": sum(r["source_nodes"] for r in runs), "source_undirected_edges": sum(r["source_edges"] for r in runs),
        "native_executions": sum(r["native_executions"] for r in runs),
        "evidence_scope": "complete_pinned_atlas_projection_inverse_mapping_restore_replay_and_ordinary_F2_reference",
        "synthesized_recording": False, "physical_qualification": "not_established"}
    write_json(destination / "qualification.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, required=True)
    parser.add_argument("--compute-root", type=Path, required=True, help="Explicit trusted existing Compute Runtime binding")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = qualify(args.atlas, args.compute_root, args.output_dir)
    print(json.dumps({k: report[k] for k in ("status", "graphs", "source_nodes", "source_undirected_edges",
        "native_executions", "f2_nonempty_graphs_passed", "f2_empty_graph_expected_refusals", "f2_reference_executions")}))


if __name__ == "__main__":
    main()
