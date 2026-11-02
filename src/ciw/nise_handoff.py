"""Compile a NISE inference schematic into an existing NET semantic work graph.

NISE owns candidate investigation structure. NET owns operation binding and
execution. This bridge requires an operator-authored binding plan for parameters,
inputs, dependencies, resources and acceptance. The schematic supplies semantic
capability meaning; the binding plan cannot replace it with another capability or
select an engine.
"""
from __future__ import annotations

from copy import deepcopy

from .control_contracts import detached, keys, text
from .nise_contract import validate_schematic
from .operations.runner import seal
from .semantic_capabilities import SemanticRegistry, compile_graph

MAX_OPERATIONS = 64
HANDOFF_MANDATE = "Compile the operator-authored NISE binding plan."


def _validate_binding(value: dict) -> dict:
    keys(value, {
        "schema", "binding_id", "schematic_id", "graph_id",
        "model_id", "nodes",
    })
    if value["schema"] != "ciw.nise-binding-plan.v1":
        raise ValueError("Unsupported NISE binding-plan schema")
    for field in ("binding_id", "schematic_id", "graph_id", "model_id"):
        text(value[field])
    nodes = value["nodes"]
    if type(nodes) is not list or not 1 <= len(nodes) <= MAX_OPERATIONS:
        raise ValueError("Binding plan requires 1..64 nodes")
    seen_nise, seen_net = set(), set()
    for node in nodes:
        keys(node, {
            "nise_node_id", "node_id", "parameters", "inputs",
            "depends_on", "resources", "acceptance", "inspection",
        })
        nise_id = text(node["nise_node_id"])
        net_id = text(node["node_id"])
        if nise_id in seen_nise or net_id in seen_net:
            raise ValueError("Binding plan contains duplicate NISE/NET node identities")
        seen_nise.add(nise_id)
        seen_net.add(net_id)
        for field in ("parameters", "inputs", "acceptance"):
            if type(node[field]) is not dict:
                raise ValueError(f"Binding {field} must be an object")
            detached(node[field])
        if type(node["depends_on"]) is not list:
            raise ValueError("Binding depends_on must be an array")
        for dependency in node["depends_on"]:
            text(dependency)
        if type(node["resources"]) is not list:
            raise ValueError("Binding resources must be an array")
        for resource in node["resources"]:
            text(resource)
        if type(node["inspection"]) is not bool:
            raise ValueError("Binding inspection must be boolean")
    return detached(value)


def compile_handoff(schematic: dict, binding: dict, registry: SemanticRegistry) -> dict:
    """Bind selected NISE operation nodes to operator-authored NET work details.

    The NISE operation node supplies the semantic capability. The binding supplies
    only execution-shape details. Engine selection remains in SemanticRegistry.
    """
    schematic = validate_schematic(schematic)
    if len(schematic["P_q"]) > MAX_OPERATIONS:
        raise ValueError("NISE operation partition exceeds NET handoff bound")
    binding = _validate_binding(binding)
    if binding["schematic_id"] != schematic["schematic_id"]:
        raise ValueError("Binding plan targets a different NISE schematic")

    operations = {node["node_id"]: node for node in schematic["P_q"]}
    selected, graph_nodes = [], []

    for row in binding["nodes"]:
        if row["nise_node_id"] not in operations:
            raise ValueError("Binding references an operation absent from NISE P_q")
        operation = operations[row["nise_node_id"]]
        if operation["status"] == "HYPOTHESIZED":
            raise ValueError("Hypothesized NISE operations cannot be execution-bound in V1")
        capability = operation["attributes"]["semantic_capability"]
        graph_nodes.append({
            "node_id": row["node_id"],
            "capability": capability,
            "parameters": deepcopy(row["parameters"]),
            "inputs": deepcopy(row["inputs"]),
            "depends_on": deepcopy(row["depends_on"]),
            "resources": deepcopy(row["resources"]),
            "acceptance": deepcopy(row["acceptance"]),
            "inspection": row["inspection"],
        })
        selected.append({
            "nise_node_id": row["nise_node_id"],
            "net_node_id": row["node_id"],
            "capability": capability,
            "epistemic_status": operation["status"],
            "evidence_refs": deepcopy(operation["evidence_refs"]),
        })

    selected_nise = {row["nise_node_id"] for row in selected}
    unbound = [
        {
            "nise_node_id": node["node_id"],
            "capability": node["attributes"]["semantic_capability"],
            "epistemic_status": node["status"],
        }
        for node in schematic["P_q"]
        if node["node_id"] not in selected_nise
    ]

    graph = {
        "schema": "ciw.semantic-work-graph.v1",
        "graph_id": binding["graph_id"],
        "mandate": HANDOFF_MANDATE,
        "model_id": binding["model_id"],
        "nodes": graph_nodes,
    }
    compilation = compile_graph(graph, registry)

    return seal({
        "schema": "ciw.nise-handoff.v1",
        "schematic_id": schematic["schematic_id"],
        "query_id": schematic["query"]["query_id"],
        "catalog_id": schematic["catalog_id"],
        "binding_id": binding["binding_id"],
        "query_context": {
            "question": schematic["query"]["question"],
            "role": "inert_scoping_context",
            "execution_instruction": False,
        },
        "provenance": {
            "validation": "structural_contract_only",
            "source_authenticated": False,
            "fresh_recomputation": False,
        },
        "selected_operations": selected,
        "unbound_schematic_operations": unbound,
        "unresolved_capabilities": deepcopy(schematic["unresolved_capabilities"]),
        "semantic_compilation": compilation,
        "claims": {
            "candidate_structure_consumed": True,
            "physical_truth_established": False,
            "execution_authority": False,
            "state_admission": False,
            "engine_selected_by_nise": False,
            "parameters_selected_by_nise": False,
        },
    })
