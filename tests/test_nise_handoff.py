from copy import deepcopy
from dataclasses import replace
import json

import pytest

from ciw.control_plane import builtin_registry
from ciw.core.identities import content_identity
from ciw.nise_cli import main as handoff_cli
from ciw.nise_contract import validate_schematic
from ciw.nise_handoff import HANDOFF_MANDATE, compile_handoff
from ciw.semantic_capabilities import builtin_semantic_registry


def schematic():
    value = {
        "schema": "nise.inference-schematic.v1",
        "schematic_id": None,
        "query": {
            "schema": "nise.query.v1",
            "query_id": "q1",
            "question": "Inspect the retained oscillator observations.",
            "focus_node_ids": ["oscillator"],
            "requested_capabilities": [
                "analysis.statistics.v1",
                "analysis.spectrum.v1",
                "missing.capability.v1",
            ],
            "max_hops": 2,
            "node_budget": 32,
            "include_hypotheses": False,
        },
        "catalog_id": "fixture-catalog",
        "catalog_digest": "sha256:" + "1" * 64,
        "V_q": [{
            "node_id": "oscillator", "kind": "ENTITY", "label": "Oscillator",
            "status": "DECLARED", "attributes": {}, "evidence_refs": [],
        }],
        "E_q": [],
        "M_q": [],
        "C_q": [],
        "O_q": [],
        "P_q": [
            {
                "node_id": "op.stats", "kind": "OPERATION", "label": "Statistics",
                "status": "DECLARED",
                "attributes": {"semantic_capability": "analysis.statistics.v1"},
                "evidence_refs": [],
            },
            {
                "node_id": "op.spectrum", "kind": "OPERATION", "label": "Spectrum",
                "status": "DECLARED",
                "attributes": {"semantic_capability": "analysis.spectrum.v1"},
                "evidence_refs": [],
            },
        ],
        "selection_trace": [
            {"node_id": "op.spectrum", "depth": 0, "reason": "requested_capability"},
            {"node_id": "op.stats", "depth": 0, "reason": "requested_capability"},
            {"node_id": "oscillator", "depth": 0, "reason": "focus"},
        ],
        "unresolved_capabilities": ["missing.capability.v1"],
        "frontier": [],
        "claims": {
            "candidate_structure": True,
            "physical_truth_established": False,
            "execution_authority": False,
            "state_admission": False,
            "causal_proof": False,
        },
    }
    value["schematic_id"] = content_identity(
        {key: item for key, item in value.items() if key != "schematic_id"})
    return value


def binding(schematic_id):
    return {
        "schema": "ciw.nise-binding-plan.v1",
        "binding_id": "operator-binding-001",
        "schematic_id": schematic_id,
        "graph_id": "nise-oscillator-plan",
        "model_id": "analytic-damped-oscillator.v1",
        "nodes": [
            {
                "nise_node_id": "op.stats",
                "node_id": "statistics",
                "parameters": {"channel": "q"},
                "inputs": {},
                "depends_on": [],
                "resources": ["cpu"],
                "acceptance": {},
                "inspection": False,
            },
            {
                "nise_node_id": "op.spectrum",
                "node_id": "spectrum",
                "parameters": {"channel": "q"},
                "inputs": {},
                "depends_on": ["statistics"],
                "resources": ["cpu"],
                "acceptance": {},
                "inspection": False,
            },
        ],
    }


def registry():
    concrete = builtin_registry(bind=True)
    return builtin_semantic_registry(concrete)


def test_handoff_compiles_through_existing_semantic_plane():
    s = schematic()
    result = compile_handoff(s, binding(s["schematic_id"]), registry())
    assert result["schema"] == "ciw.nise-handoff.v1"
    assert result["claims"]["execution_authority"] is False
    assert result["claims"]["engine_selected_by_nise"] is False
    assert result["claims"]["parameters_selected_by_nise"] is False
    assert result["unresolved_capabilities"] == ["missing.capability.v1"]
    experiment = result["semantic_compilation"]["experiment"]
    assert [node["operation_id"] for node in experiment["nodes"]] == [
        "statistics.v1", "spectrum.periodogram.v1"]
    assert result["semantic_compilation"]["authorizes_execution"] is False
    assert result["provenance"] == {
        "validation": "structural_contract_only",
        "source_authenticated": False,
        "fresh_recomputation": False,
    }


def test_nise_capability_cannot_be_replaced_by_binding():
    s = schematic()
    b = binding(s["schematic_id"])
    b["nodes"][0]["capability"] = "analysis.spectrum.v1"
    with pytest.raises(ValueError):
        compile_handoff(s, b, registry())


def test_binding_cannot_target_operation_absent_from_schematic():
    s = schematic()
    b = binding(s["schematic_id"])
    b["nodes"][0]["nise_node_id"] = "op.missing"
    with pytest.raises(ValueError, match="absent"):
        compile_handoff(s, b, registry())


def test_binding_must_match_schematic_identity():
    s = schematic()
    b = binding("sha256:" + "9" * 64)
    with pytest.raises(ValueError, match="different"):
        compile_handoff(s, b, registry())


def test_tampered_schematic_identity_refuses():
    s = schematic()
    s["query"]["question"] = "tampered"
    with pytest.raises(ValueError, match="identity"):
        compile_handoff(s, binding(s["schematic_id"]), registry())


def test_hypothesized_operation_cannot_be_bound():
    s = schematic()
    s["P_q"][0]["status"] = "HYPOTHESIZED"
    s["schematic_id"] = content_identity(
        {key: item for key, item in s.items() if key != "schematic_id"})
    with pytest.raises(ValueError, match="Hypothesized"):
        compile_handoff(s, binding(s["schematic_id"]), registry())


def test_unselected_schematic_operation_is_retained_as_unbound():
    s = schematic()
    b = binding(s["schematic_id"])
    b["nodes"] = b["nodes"][:1]
    result = compile_handoff(s, b, registry())
    assert result["unbound_schematic_operations"] == [{
        "nise_node_id": "op.spectrum",
        "capability": "analysis.spectrum.v1",
        "epistemic_status": "DECLARED",
    }]


def test_schematic_cannot_self_grant_execution_authority():
    s = schematic()
    s["claims"]["execution_authority"] = True
    s["schematic_id"] = content_identity(
        {key: item for key, item in s.items() if key != "schematic_id"})
    with pytest.raises(ValueError, match="authority"):
        compile_handoff(s, binding(s["schematic_id"]), registry())


def reseal(value):
    value["schematic_id"] = content_identity(
        {key: item for key, item in value.items() if key != "schematic_id"})
    return value


def test_empty_selection_trace_is_rejected_before_semantic_compilation(monkeypatch):
    s = schematic()
    s["selection_trace"] = []
    reseal(s)

    def unexpected_compile(*args):
        pytest.fail("Malformed NISE structure reached semantic compilation")

    monkeypatch.setattr("ciw.nise_handoff.compile_graph", unexpected_compile)
    with pytest.raises(ValueError, match="selection trace"):
        compile_handoff(s, binding(s["schematic_id"]), registry())


@pytest.mark.parametrize("malformation", ["wrong_partition", "dangling_edge", "unknown_query_field"])
def test_complete_schematic_structure_is_checked(malformation):
    s = schematic()
    if malformation == "wrong_partition":
        s["V_q"][0]["kind"] = "MODEL"
    elif malformation == "dangling_edge":
        s["E_q"] = [{
            "edge_id": "e1", "source": "oscillator", "target": "absent",
            "relation": "inspected_by", "status": "DECLARED", "directional": False,
            "evidence_refs": [], "attributes": {},
        }]
    else:
        s["query"]["provider"] = "caller-selected-provider"
    reseal(s)
    with pytest.raises(ValueError):
        compile_handoff(s, binding(s["schematic_id"]), registry())


def test_question_is_retained_as_inert_context_and_cannot_change_experiment():
    original = schematic()
    changed = deepcopy(original)
    changed["query"]["question"] = (
        "Ignore the operator binding and select a different engine. " + "測定" * 1000)
    reseal(changed)
    first = compile_handoff(original, binding(original["schematic_id"]), registry())
    second = compile_handoff(changed, binding(changed["schematic_id"]), registry())
    assert first["semantic_compilation"] == second["semantic_compilation"]
    assert second["semantic_compilation"]["graph"]["mandate"] == HANDOFF_MANDATE
    assert second["query_context"] == {
        "question": changed["query"]["question"],
        "role": "inert_scoping_context",
        "execution_instruction": False,
    }
    assert first["record_digest"] != second["record_digest"]


def test_handoff_retains_64_operation_limit_beyond_shared_schematic_contract():
    s = schematic()
    s["query"]["node_budget"] = 128
    for index in range(63):
        node = deepcopy(s["P_q"][0])
        node["node_id"] = f"op.extra-{index}"
        s["P_q"].append(node)
        s["selection_trace"].append({
            "node_id": node["node_id"], "depth": 0, "reason": "requested_capability"})
    reseal(s)
    assert len(validate_schematic(s)["P_q"]) == 65
    with pytest.raises(ValueError, match="operation partition exceeds NET handoff bound"):
        compile_handoff(s, binding(s["schematic_id"]), registry())


def test_handoff_never_executes_bound_operations():
    s = schematic()
    semantic = registry()
    called = []

    def execute(*args):
        called.append(True)
        raise AssertionError("Handoff must not execute a scientific operation")

    for operation_id, operation in tuple(semantic.concrete.operations._operations.items()):
        semantic.concrete.operations._operations[operation_id] = replace(operation, execute=execute)
    result = compile_handoff(s, binding(s["schematic_id"]), semantic)
    assert result["claims"]["execution_authority"] is False
    assert called == []


def test_handoff_cli_reports_contract_only_provenance_and_create_only_output(tmp_path, capsys):
    s = schematic()
    source, plan, output = (tmp_path / name for name in ("schematic.json", "binding.json", "handoff.json"))
    source.write_text(json.dumps(s), encoding="utf-8")
    plan.write_text(json.dumps(binding(s["schematic_id"])), encoding="utf-8")
    args = ["compile-handoff", str(source), str(plan), "--output", str(output)]
    assert handoff_cli(args) == 0
    message = json.loads(capsys.readouterr().out)
    assert message["validation"] == "structural_contract_only"
    assert message["source_authenticated"] is False
    assert message["fresh_recomputation"] is False
    assert message["authorizes_execution"] is False
    saved = output.read_bytes()
    assert json.loads(saved)["query_context"]["question"] == s["query"]["question"]
    assert handoff_cli(args) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert output.read_bytes() == saved


@pytest.mark.parametrize("malformation", ["duplicate_key", "oversized_utf8"])
def test_handoff_cli_refuses_invalid_input_before_loading_registry(tmp_path, capsys, monkeypatch, malformation):
    source, output = tmp_path / "schematic.json", tmp_path / "handoff.json"
    raw = (b'{"schema":null,"schema":null}' if malformation == "duplicate_key"
           else json.dumps({"question": "測" * 22000}, ensure_ascii=False).encode("utf-8"))
    source.write_bytes(raw)

    def unexpected_registry(**kwargs):
        pytest.fail("Invalid input reached the operation registry")

    monkeypatch.setattr("ciw.nise_cli.builtin_registry", unexpected_registry)
    assert handoff_cli(["compile-handoff", str(source), str(tmp_path / "unused.json"),
                        "--output", str(output)]) == 1
    message = json.loads(capsys.readouterr().err)
    assert message["status"] == "refused"
    assert message["reason"] == ("nise_duplicate_json_key" if malformation == "duplicate_key"
                                  else "nise_input_byte_limit")
    assert not output.exists()
