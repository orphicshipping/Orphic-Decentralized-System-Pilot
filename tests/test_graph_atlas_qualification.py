"""Mapping tests use small declared fixtures; acquired corpus stays opt-in/local."""
from copy import deepcopy
import gzip
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("atlas_qualification", ROOT / "scripts/qualify_graph_atlas_consumer.py")
atlas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(atlas)
STAMP = "2026-10-05T22:00:00Z"


def graphs():
    return [{"id": 0, "node_count": 0, "edges": []},
            {"id": 1, "node_count": 3, "edges": [[2, 0]]},
            {"id": 2, "node_count": 2, "edges": []}]


def test_parser_and_inverse_preserve_empty_isolated_and_source_endpoint_order():
    raw = gzip.compress(b"GRAPH 0\nNODES 0\nGRAPH 1\nNODES 3\n2 0\nGRAPH 2\nNODES 2\n")
    assert atlas.parse_atlas(raw) == graphs()
    state = atlas.native_state(graphs())
    assert atlas.decode_projection(state) == graphs()
    assert len(state["fields"]) == 8
    assert len(state["edges"]) == 2


@pytest.mark.parametrize("raw", [b"", b"GRAPH 1\nNODES 0\n", b"GRAPH 0\nNODES 8\n",
    b"GRAPH 0\nNODES 2\n0 2\n", b"GRAPH 0\nNODES 2\n0 0\n",
    b"GRAPH 0\nNODES 2\n0 1\n1 0\n", b"GRAPH 0\nNODES 2\n0.0 1\n",
    b"GRAPH 0\nNODES 2\n\n"])
def test_malformed_source_refuses(raw):
    with pytest.raises(ValueError):
        atlas.parse_atlas(gzip.compress(raw))


def test_expansion_budget_and_unpinned_file_refuse(tmp_path):
    with pytest.raises(ValueError, match="expanded"):
        atlas.parse_atlas(gzip.compress(b"a" * (atlas.MAX_EXPANDED + 1)))
    path = tmp_path / "atlas.gz"
    path.write_bytes(gzip.compress(b"GRAPH 0\nNODES 0\n"))
    with pytest.raises(ValueError, match="SHA256"):
        atlas.read_atlas(path)


@pytest.mark.parametrize("mutation", ["lost_isolate", "unpaired", "reversed", "changed_direction", "cross_graph", "physical_unit"])
def test_projection_mutations_refuse(mutation):
    state = atlas.native_state(graphs())
    if mutation == "lost_isolate":
        del state["fields"]["atlas/1/node/1"]
    elif mutation == "unpaired":
        state["edges"].pop()
    elif mutation == "reversed":
        state["edges"].reverse()
    elif mutation == "changed_direction":
        state["edges"][1]["from"] = "atlas/1/node/2"
    elif mutation == "cross_graph":
        state["edges"][0]["to"] = "atlas/2/node/0"
        state["edges"][1]["from"] = "atlas/2/node/0"
    elif mutation == "physical_unit":
        state["fields"]["atlas/1/node/1"]["unit"] = "m"
    with pytest.raises(ValueError):
        atlas.decode_projection(state)


def test_real_native_compute_projection_restore_and_replay(tmp_path):
    runtime = atlas.bridge.ComputeRuntime(ROOT / "instruments/execution/compute-runtime")
    result = atlas.run_batch(graphs(), runtime, tmp_path / "batch", STAMP)
    assert result["status"] == "PASS"
    assert result["native_executions"] == 2
    assert result["source_nodes"] == 5
    assert result["source_edges"] == 1
    assert result["native_directed_arcs"] == 2
    assert result["execution_path"].endswith("not_Session_recording")


def test_offline_restore_does_not_invoke_native_provider(monkeypatch):
    state = atlas.native_state(graphs())
    record = atlas.bridge.canonical_record(state, timestamp=STAMP, provenance={
        "author": "test", "transaction_id": "fixture", "source": "declared", "timestamp": None})
    envelope = atlas.retain_record(record, dataset_id="fixture", revision=1, parent_source_id=None,
                                  recorded_at=STAMP, data_family="compute-canonical-state")
    workbench = atlas.Workbench()
    workbench.add_source({"kind": "data-record", "label": "fixture",
        "bytes_b64": atlas.base64.b64encode(atlas.json.dumps(envelope).encode()).decode()})
    monkeypatch.setattr(atlas.bridge, "_bounded_process", lambda *a, **kw: pytest.fail("Offline execution"))
    saved = workbench.serialize()
    assert atlas.Workbench.restore(deepcopy(saved)).serialize() == saved


def test_native_f2_uses_graph_complex_not_clique_filling():
    triangle = {"id": 7, "node_count": 3, "edges": [[0, 1], [0, 2], [1, 2]]}
    report = atlas.topology_reference(triangle)
    assert report["result"] == {"counts": [3, 3], "ranks": [0, 2], "betti": [1, 1], "candidate_matches": True}
    assert report["incorrect_candidate"] == "mismatch_preserved"
    assert [0, 1, 2] not in report["request"]["simplices"]


def test_native_f2_preserves_isolates_and_explicit_empty_refusal():
    assert atlas.topology_reference(graphs()[0])["status"] == "EXPECTED_REFUSAL"
    assert atlas.topology_reference(graphs()[1])["result"]["betti"] == [2, 0]
    assert atlas.topology_reference(graphs()[2])["result"]["betti"] == [2]
