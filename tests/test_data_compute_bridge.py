"""Real native package projection and polymer execution through retained data."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from ciw.adapters.protocol import AdapterRefusal
from ciw import data_compute_bridge as bridge
from ciw.data_sources import native_record, retain_record
from ciw.instruments import make_demo_run
from ciw.operations.registry import default_registry
from ciw.polymer_contract import example_request
from ciw.session import Session
from ciw.workbench import Workbench

ROOT = Path(__file__).resolve().parents[1]
COMPUTE = ROOT / "instruments/execution/compute-runtime"
STAMP = "2026-10-05T19:20:00Z"


def compute_record():
    # Intentionally not lexical field order; native hash sorts mappings while
    # preserving native edge order and vector component order.
    return bridge.canonical_record({"schema_version": "bounded.specimen.v1", "fields": {
        "specimen.position": {"id": "specimen.position", "type": "vector3", "value": [3.0, -1.0, 2.0], "unit": "m"},
        "specimen.mass": {"id": "specimen.mass", "type": "scalar", "value": 2.5, "unit": "kg"},
        "specimen.orientation": {"id": "specimen.orientation", "type": "quaternion", "value": [0, 0, 0, 1], "unit": None},
        "specimen.label": {"id": "specimen.label", "type": "string", "value": "résine α", "unit": None},
        "specimen.ready": {"id": "specimen.ready", "type": "bool", "value": False, "unit": None},
    }, "edges": [
        {"id": "z-flow", "from": "specimen", "to": "mold", "type": "contact",
         "attributes": {"normal": [1.0, 0.0, 0.0], "conductance": 5.0, "unit": "W/K"}},
        {"id": "a-flow", "from": "mold", "to": "sink", "type": "thermal",
         "attributes": {"active": True, "conductance": 7.0}},
    ]}, timestamp=STAMP, provenance={"author": "test", "transaction_id": "sample-1",
                                    "source": "declared", "timestamp": None})


def envelope(record=None, dataset="canonical"):
    record = compute_record() if record is None else record
    return retain_record(record, dataset_id=dataset, revision=1, parent_source_id=None,
                         recorded_at=STAMP, data_family="compute-canonical-state" if record["schema"] == bridge.SCHEMA
                         else "polymer-cycle-request")


def add(workbench, record=None, dataset="canonical"):
    # Preserve raw, visibly noncanonical input bytes independently of native
    # content identity. Ingestion cannot reconstruct them from the parsed object.
    raw = json.dumps(envelope(record, dataset), ensure_ascii=False, indent=2).encode()
    return workbench.add_source({"kind": "data-record", "label": dataset,
                                 "bytes_b64": base64.b64encode(raw).decode()})


def session(tmp_path, compute=True):
    registry = default_registry()
    if compute:
        registry.register(bridge.ComputeRuntime(COMPUTE).operation())
    # Existing registries may explicitly opt in rather than advertise polymer.
    if bridge.ASSESS_POLYMER not in {x["operation_id"] for x in registry.describe()}:
        registry.register(bridge.polymer_operation())
    return Session(make_demo_run(), tmp_path, operations=registry)


def call(session, operation, source):
    answer = session.handle({"protocol_version": 1, "request_id": "bridge-test", "type": "operation.execute",
                            "payload": {"operation_id": operation,
                                        "parameters": bridge.source_parameters(session.workbench, source["source_id"])}})
    assert answer["type"] == "response", answer
    return answer["payload"]


def test_actual_native_compute_projection_preserves_graph_and_fields(tmp_path):
    s = session(tmp_path)
    original = compute_record()
    source = add(s.workbench, original)
    result = call(s, bridge.PROJECT, source)
    assert result["status"] == "completed", result
    data = result["result"]["data"]
    assert data["projection"]["state"] == original["version"]["state"]
    assert data["projection"]["source_version"] == original["version"]["id"]
    assert data["conservation"]["field_count"] == 5
    assert data["conservation"]["edge_ids"] == ["z-flow", "a-flow"]
    assert data["conservation"]["discarded_fields"] == data["conservation"]["discarded_edges"] == []
    assert data["authority"]["state_admission"] == "not_performed"
    assert result["result"]["verification_status"] == "not_verified"
    assert result["execution"]["runtime"]["provider"] == "compute-runtime.core.projection.project_state"
    assert set(result["execution"]["runtime"]["source_files_sha256"]) == set(bridge._FILES)
    assert native_record(s.workbench.get_source(source["source_id"])) == original


def test_compute_save_reopen_is_provider_free_and_replay_needs_explicit_binding(tmp_path, monkeypatch):
    s = session(tmp_path / "live")
    source = add(s.workbench)
    first = call(s, bridge.PROJECT, source)
    assert first["status"] == "completed", first
    path = s.save_workspace(tmp_path / "workspace.json")
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "_bounded_process", lambda *a, **kw: pytest.fail("Offline restore invoked compute package"))
        restored = Session.from_workspace(path, output_dir=tmp_path / "offline")
        assert restored.results == s.results
        assert native_record(restored.workbench.get_source(source["source_id"])) == compute_record()
        refused = call(restored, bridge.PROJECT, source)
        assert refused["status"] == "refused"
        assert refused["execution"]["refusal"]["code"] == "operation_unavailable"
    registry = default_registry()
    registry.register(bridge.ComputeRuntime(COMPUTE).operation())
    rebound = Session.from_workspace(path, output_dir=tmp_path / "rebound")
    rebound.operations = registry
    replay = call(rebound, bridge.PROJECT, source)
    assert replay["status"] == "completed", replay
    assert replay["result"]["data"] == first["result"]["data"]
    assert replay["execution"]["execution_id"] != first["execution"]["execution_id"]


def test_actual_polymer_assessment_consumes_complete_native_cycle(tmp_path, monkeypatch):
    from ciw import polymer_workflow, polymer_models
    s = session(tmp_path / "live", compute=False)
    request = example_request()
    source = add(s.workbench, request, "cycle")
    result = call(s, bridge.ASSESS_POLYMER, source)
    assert result["status"] == "completed", result
    data = result["result"]["data"]
    expected = polymer_workflow._assess(polymer_workflow.make_source(request), {})
    assert data["assessment"] == expected
    assert data["conservation"]["sample_counts"] == {v["sensor_id"]: len(v["samples"]) for v in request["sensors"]}
    assert data["conservation"]["resampling"] == "not_performed"
    assert data["assessment"]["authority"]["hardware_actuation"] == "not_performed"
    assert native_record(s.workbench.get_source(source["source_id"])) == request
    path = s.save_workspace(tmp_path / "polymer.json")
    with monkeypatch.context() as patch:
        patch.setattr(polymer_workflow, "_assess", lambda *a, **kw: pytest.fail("Offline restore dispatched assessment"))
        patch.setattr(polymer_models, "engineering_estimate", lambda *a, **kw: pytest.fail("Offline restore ran model"))
        restored = Session.from_workspace(path, output_dir=tmp_path / "offline")
        assert restored.results == s.results
    replay = call(restored, bridge.ASSESS_POLYMER, source)
    assert replay["status"] == "completed", replay
    assert replay["result"]["data"] == data


@pytest.mark.parametrize("kind,value", [("tensor", [[1, 0], [0, 1]]), ("vector3", [1, 2]),
                                        ("quaternion", [0, 0, 1]), ("scalar", True),
                                        ("scalar", None), ("bool", 1), ("vector3", [1, 2, float("nan")])])
def test_unsupported_or_malformed_native_types_are_refused(kind, value):
    record = compute_record()
    state = record["version"]["state"]
    state["fields"]["specimen.mass"].update(type=kind, value=value)
    with pytest.raises(ValueError):
        bridge.canonical_record(state, timestamp=STAMP, provenance=record["version"]["provenance"])


def test_compute_native_identity_separates_content_from_provenance():
    record = compute_record()
    provenance = {**record["version"]["provenance"], "author": "another-author"}
    other = bridge.canonical_record(record["version"]["state"], timestamp="2026-10-05T19:30:00Z", provenance=provenance,
                                    parent_version_id="a" * 64)
    assert other["version"]["id"] == record["version"]["id"]
    assert envelope(other)["source_id"] != envelope(record)["source_id"]
    changed = deepcopy(record)
    changed["version"]["state"]["fields"]["specimen.mass"]["unit"] = "g"
    with pytest.raises(ValueError, match="identity"):
        bridge.validate_record(changed)


def test_key_identity_duplicate_edge_and_resealed_output_corruption_refused(tmp_path):
    record = compute_record()
    record["version"]["state"]["fields"]["specimen.mass"]["id"] = "new-mass"
    with pytest.raises(ValueError, match="identity"):
        envelope(record)
    record = compute_record()
    record["version"]["state"]["edges"][1]["id"] = "z-flow"
    with pytest.raises(ValueError, match="Duplicate"):
        envelope(record)
    w = Workbench()
    source = add(w)
    parameters = bridge.source_parameters(w, source["source_id"])
    data = bridge.ComputeRuntime(COMPUTE).execute(parameters)
    data["projection"]["state"]["edges"].reverse()
    with pytest.raises(ValueError, match="differs"):
        bridge.validate_payload(bridge.PROJECT, data, {}, parameters, {})


def test_unretained_compute_or_polymer_source_refused_before_dispatch(tmp_path, monkeypatch):
    s = session(tmp_path)
    other = Workbench()
    for operation, record, name in [(bridge.PROJECT, compute_record(), "compute"),
                                    (bridge.ASSESS_POLYMER, example_request(), "polymer")]:
        source = add(other, record, name)
        parameters = bridge.source_parameters(other, source["source_id"])
        answer = s.handle({"protocol_version": 1, "request_id": "missing", "type": "operation.execute",
                          "payload": {"operation_id": operation, "parameters": parameters}})["payload"]
        assert answer["status"] == "refused", answer
        assert answer["result"] is None
        assert answer["execution"]["runtime"] is None
    assert not s.results


def test_runtime_binding_refuses_missing_source_and_changed_source_bytes(tmp_path):
    with pytest.raises(AdapterRefusal, match="Missing"):
        bridge.ComputeRuntime(tmp_path / "missing")
    root = tmp_path / "runtime"
    for name in bridge._FILES:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(COMPUTE / name, dest)
    runtime = bridge.ComputeRuntime(root)
    (root / "core/projection/project.py").write_text("raise RuntimeError('tampered')")
    with pytest.raises(AdapterRefusal, match="changed"):
        runtime.runtime_identity()


def test_saved_missing_source_dependency_is_refused(tmp_path):
    s = session(tmp_path / "live")
    source = add(s.workbench)
    assert call(s, bridge.PROJECT, source)["status"] == "completed"
    saved = s._workspace()
    saved["workbench"]["sources"] = []
    saved["workbench"]["revision"] = 0
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="source present unchanged"):
        Session.from_workspace(path, output_dir=tmp_path / "bad")


def test_projection_ignores_untrusted_bytecode_and_root_module_shadowing(tmp_path):
    # Source hashes bind executable .py bytes, so stale/malicious cache files
    # must not become a second code authority. Likewise the package root is
    # never added to sys.path where it could shadow a standard library module.
    import py_compile
    root = tmp_path / "runtime"
    for name in bridge._FILES:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(COMPUTE / name, dest)
    rogue = tmp_path / "rogue.py"
    rogue.write_text("raise RuntimeError('untrusted bytecode')\n")
    cached = root / "core/projection/__pycache__"
    cached.mkdir()
    tag = __import__("sys").implementation.cache_tag
    py_compile.compile(str(rogue), cfile=str(cached / f"project.{tag}.pyc"),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    (root / "hashlib.py").write_text("raise RuntimeError('root module shadowing')\n")
    w = Workbench()
    source = add(w)
    result = bridge.ComputeRuntime(root).execute(bridge.source_parameters(w, source["source_id"]))
    assert result["projection"]["state"] == compute_record()["version"]["state"]


def test_runtime_output_corruption_is_rejected_even_with_valid_json(tmp_path, monkeypatch):
    w = Workbench()
    source = add(w)
    record = compute_record()
    fake = {"source_version": record["version"]["id"], "state": record["version"]["state"]}
    fake["state"]["fields"]["specimen.mass"]["value"] = 999.0
    monkeypatch.setattr(bridge, "_bounded_process", lambda *a, **kw: (0, json.dumps(fake).encode()))
    with pytest.raises(ValueError, match="changed canonical"):
        bridge.ComputeRuntime(COMPUTE).execute(bridge.source_parameters(w, source["source_id"]))


def test_bridge_correction_invalidates_actual_consuming_execution(tmp_path):
    s = session(tmp_path / "live")
    record = compute_record()
    first_envelope = envelope(record)
    old = add(s.workbench, record)
    first = call(s, bridge.PROJECT, old)["result"]
    revised_state = deepcopy(record["version"]["state"])
    revised_state["fields"]["specimen.mass"]["value"] = 4.0
    revised_record = bridge.canonical_record(revised_state, timestamp=STAMP,
        provenance=record["version"]["provenance"], parent_version_id=record["version"]["id"])
    revised_envelope = retain_record(revised_record, dataset_id="canonical", revision=2,
        parent_source_id=first_envelope["source_id"], recorded_at=STAMP, data_family="compute-canonical-state")
    revised = s.workbench.add_source({"kind": "data-record", "label": "corrected canonical",
                                    "bytes_b64": base64.b64encode(json.dumps(revised_envelope).encode()).decode()})

    def command(kind, payload):
        response = s.handle({"protocol_version": 1, "request_id": kind, "type": kind, "payload": payload})
        assert response["type"] == "response", response
        return response["payload"]

    proposal = command("correction.propose", {"old_source_id": old["source_id"], "new_source_id": revised["source_id"],
                        "kind": "source_data", "reason": "retained mass correction"})
    command("correction.review", {"correction_id": proposal["correction_id"], "decision": "accept",
            "expected_revision": s.correction_journal.revision, "reviewer": "operator", "reason": "checked"})
    status = s.dependency_status()["artifact_status"]
    for identity in (old["source_id"], first["execution_id"], first["result_id"]):
        assert status[identity]["status"] == "stale"
    assert s.results[first["result_id"]] == first
    fresh = call(s, bridge.PROJECT, revised)
    assert fresh["status"] == "completed", fresh
    assert fresh["result"]["data"]["projection"]["state"] == revised_state
    assert s.dependency_status()["artifact_status"][fresh["result"]["result_id"]]["status"] == "current"


@pytest.mark.parametrize('field', ['value', 'standard_uncertainty', 'time_s'])
def test_polymer_copied_measurement_cannot_alias_boolean_and_number(tmp_path, field):
    request = example_request()
    # A zero uncertainty is valid evidence; changing its JSON type is not.
    request['sensors'][0]['samples'][-1]['standard_uncertainty'] = 0.0
    s = session(tmp_path, compute=False)
    source = add(s.workbench, request, 'cycle')
    completed = call(s, bridge.ASSESS_POLYMER, source)
    assert completed['status'] == 'completed'
    data = deepcopy(completed['result']['data'])
    row = data['assessment']['metrology']['measurements'][0]
    if field == 'standard_uncertainty':
        row[field] = False
    elif type(row[field]) is int:
        row[field] = float(row[field])
    else:
        assert row[field] == int(row[field])
        row[field] = int(row[field])
    with pytest.raises(ValueError, match='exact selected source sample'):
        bridge.validate_payload(bridge.ASSESS_POLYMER, data, s.run,
            bridge.source_parameters(s.workbench, source['source_id']), s.selection)
