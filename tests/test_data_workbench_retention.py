"""Retained-data owner integration; synthetic records do not qualify hardware."""
import base64
from copy import deepcopy
import json

import pytest

from ciw import data_sources as data
from ciw.adapters.protocol import AdapterRefusal
from ciw.control_contracts import artifact
from ciw.core.identities import canonical_json
from ciw.instruments import make_demo_run
from ciw.session import Session
from ciw.workbench import Workbench, _source
from test_data_sources import obs, retain


def call(session, kind, payload=None):
    result = session.handle({"protocol_version": 1, "request_id": "data-owner-test",
                             "type": kind, "payload": {} if payload is None else payload})
    assert result["type"] == "response", result
    return result["payload"]


def payload(value, *, label="synthetic retained data", dependencies=None):
    result = {"kind": "data-record", "label": label,
              "bytes_b64": base64.b64encode(canonical_json(value).encode()).decode()}
    if dependencies is not None:
        result["input_source_ids"] = dependencies
    return result


@pytest.mark.parametrize("schema", ["ciw.field-observation-mapping.v1", "ciw.compute-canonical-state.v1",
    "ciw.hoomd-study.v1", "ciw.hoomd-result.v1", "ciw.product-study.v1",
    "ciw.polymer-manufacturing-report.v1", "ciw.polymer-manufacturing-report.v2",
    "ciw.toolpoint-stream-report.v1"])
def test_unintegrated_extension_owners_are_not_advertised_or_accepted(schema):
    assert schema not in data.SCHEMA_FAMILIES
    with pytest.raises(AdapterRefusal) as error:
        data.retain_record({"schema": schema}, data_family="unintegrated", dataset_id="unintegrated",
                           revision=1, parent_source_id=None, recorded_at="2026-10-07T00:00:00Z")
    assert error.value.code == "data_family_unsupported"


def test_session_data_reads_are_detached_and_do_not_execute_or_admit(tmp_path):
    session = Session(make_demo_run(), tmp_path)
    envelope = retain(obs())
    source = call(session, "source.add", payload(envelope))
    before = deepcopy((session.workbench.serialize(), session.results, session.executions))
    assert call(session, "data.list") == {"sources": [source]}
    loaded = call(session, "data.get", {"source_id": source["source_id"]})
    assert loaded == envelope
    loaded["native_record"]["value"] = 999
    assert call(session, "data.get", {"source_id": source["source_id"]}) == envelope
    assert (session.workbench.serialize(), session.results, session.executions) == before
    assert source["data"]["state_admission"] == "not_performed"
    assert source["data"]["physical_validation"] == "not_performed"
    assert source["data"]["source_authentication"] == "not_established_by_hashes"
    assert not session.results and not session.executions
    with pytest.raises(ValueError, match="no executable operation"):
        session.workbench.bind_workflow("data-record", {})
    for kind, bad in (("data.list", {"allow_operations": ["*"]}),
                      ("data.get", {}), ("data.get", {"source_id": source["source_id"], "execute": True})):
        response = session.handle({"protocol_version": 1, "request_id": "denied", "type": kind, "payload": bad})
        assert response["type"] == "error" and response["payload"]["code"] == "invalid_payload"
    assert (session.workbench.serialize(), session.results, session.executions) == before


def test_same_occurrence_retry_preserves_first_bytes_label_and_bindings():
    workbench = Workbench()
    first_value = retain(obs())
    first = workbench.add_source(payload(first_value))
    before = workbench.serialize()
    retry = payload(first_value, label="changed human label")
    retry["bytes_b64"] = base64.b64encode((json.dumps(first_value, indent=2) + "\n").encode()).decode()
    assert workbench.add_source(retry) == first
    assert workbench.serialize() == before
    with pytest.raises(ValueError, match="input bindings"):
        workbench.add_source(payload(first_value, dependencies=[first["source_id"]]))
    with pytest.raises(ValueError, match="revision is already bound"):
        workbench.add_source(payload(retain(obs(3))))
    assert workbench.serialize() == before


@pytest.mark.parametrize("mode", ["first_has_parent", "parent_missing", "revision_gap", "wrong_dataset", "schema_change"])
def test_invalid_revision_links_refuse_without_partial_catalog(mode):
    workbench = Workbench()
    first_value = retain(obs())
    workbench.add_source(payload(first_value))
    before = workbench.serialize()
    kwargs = {"revision": 2, "parent_source_id": first_value["source_id"]}
    if mode == "first_has_parent":
        kwargs = {"revision": 1, "parent_source_id": first_value["source_id"], "dataset_id": "new"}
    elif mode == "parent_missing": kwargs["parent_source_id"] = None
    elif mode == "revision_gap": kwargs["revision"] = 3
    elif mode == "wrong_dataset": kwargs["dataset_id"] = "other"
    native = artifact(b"synthetic", media_type="application/octet-stream", producer="fixture",
                      experiment_id="fixture") if mode == "schema_change" else obs(3)
    with pytest.raises(ValueError):
        workbench.add_source(payload(retain(native, **kwargs)))
    assert workbench.serialize() == before


@pytest.mark.parametrize("dependencies", [[], ["source:unknown"], ["source:unknown", "source:unknown"]])
def test_missing_or_ambiguous_dependency_refuses_without_partial_catalog(dependencies):
    workbench = Workbench()
    before = workbench.serialize()
    with pytest.raises(ValueError):
        workbench.add_source(payload(retain(obs()), dependencies=dependencies))
    assert workbench.serialize() == before


def test_resealed_saved_source_cannot_hide_missing_input():
    workbench = Workbench()
    source = workbench.add_source(payload(retain(obs())))
    saved = workbench.serialize()
    forged = _source(payload(retain(obs()), dependencies=["source:missing"]))
    saved["sources"] = [forged]
    with pytest.raises(ValueError, match="missing dependency"):
        Workbench.restore(saved)
    assert workbench.list_sources() == [source]


def test_revision_history_is_not_a_dependency_and_correction_is_transitive(tmp_path):
    session = Session(make_demo_run(), tmp_path)
    original = retain(obs())
    old = call(session, "source.add", payload(original))
    derived = call(session, "source.add", payload(retain(obs(4), dataset_id="derived"), dependencies=[old["source_id"]]))
    leaf = call(session, "source.add", payload(retain(obs(5), dataset_id="leaf"), dependencies=[derived["source_id"]]))
    new_value = retain(obs(3), revision=2, parent_source_id=original["source_id"])
    new = call(session, "source.add", payload(new_value))
    before = session.workbench.serialize()
    graph = session.dependency_status()["nodes"]
    assert old["source_id"] in graph[derived["source_id"]]["dependencies"]
    assert derived["source_id"] in graph[leaf["source_id"]]["dependencies"]
    assert old["source_id"] not in graph[new["source_id"]]["dependencies"]
    proposal = call(session, "correction.propose", {"old_source_id": old["source_id"],
        "new_source_id": new["source_id"], "kind": "calibration", "reason": "synthetic correction lifecycle"})
    assert all(v["status"] == "current" for v in session.dependency_status()["artifact_status"].values())
    call(session, "correction.review", {"correction_id": proposal["correction_id"], "decision": "accept",
        "expected_revision": session.correction_journal.revision, "reviewer": "synthetic test reviewer",
        "reason": "explicit test-only review"})
    state = session.dependency_status()
    for value in (old, derived, leaf):
        assert state["artifact_status"][value["source_id"]]["status"] == "stale"
    assert state["artifact_status"][new["source_id"]]["status"] == "current"
    assert state["state_admission"] == state["hardware_actuation"] == "not_performed"
    assert session.workbench.serialize() == before
    workspace = tmp_path / "retained.json"
    session.save_workspace(workspace)
    reopened = Session.from_workspace(workspace, tmp_path / "reopened")
    assert reopened.workbench.serialize() == before
    assert reopened.dependency_status() == state
    assert not reopened.executions and not reopened.results


def test_atomic_source_batch_rolls_back_all_catalog_mutations():
    workbench = Workbench()
    workbench.add_source(payload(retain(obs())))
    before = workbench.serialize()
    revision, used = workbench.revision, workbench._used_bytes
    invalid = payload(retain(obs(5), dataset_id="batch/invalid"), dependencies=["source:missing"])
    with pytest.raises(ValueError, match="missing dependency"):
        workbench.add_sources([payload(retain(obs(4), dataset_id="batch/first")), invalid])
    assert workbench.serialize() == before
    assert workbench.revision == revision and workbench._used_bytes == used
    # Rollback must also remove source identity claims, allowing this valid retry.
    committed = workbench.add_sources([payload(retain(obs(4), dataset_id="batch/first"))])
    assert len(committed) == 1 and workbench.revision == revision + 1


def test_atomic_source_batch_preserves_live_reservations_and_budget(monkeypatch):
    from ciw import workbench as owner
    workbench = Workbench()
    with workbench._lock:
        _, reserved = workbench._reserve("machine-manifest")
    first = payload(retain(obs(), dataset_id="batch/first"))
    second = payload(retain(obs(4), dataset_id="batch/second"))
    size = len(owner._canonical(owner._source(first)))
    monkeypatch.setattr(owner, "MAX_BYTES", reserved + size + owner._OVERHEAD)
    before = workbench.serialize()
    bindings = workbench._bindings
    with pytest.raises(AdapterRefusal) as exc:
        workbench.add_sources([first, second])
    assert exc.value.code == "workbench_capacity"
    assert workbench.serialize() == before
    assert workbench._bindings is bindings
    assert workbench.pending_operations == 1 and workbench._reserved_bytes == reserved
    committed = workbench.add_sources([first])
    assert len(committed) == 1
    assert workbench.pending_operations == 1 and workbench._reserved_bytes == reserved


def test_atomic_source_batch_rolls_back_interruption(monkeypatch):
    workbench = Workbench()
    before = workbench.serialize()
    add_source = workbench.add_source
    calls = 0
    def interrupt_second(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("synthetic cancellation after first insertion")
        return add_source(value)
    monkeypatch.setattr(workbench, "add_source", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        workbench.add_sources([payload(retain(obs(), dataset_id="batch/first")),
                               payload(retain(obs(4), dataset_id="batch/second"))])
    assert workbench.serialize() == before
    assert workbench.revision == 0 and workbench._used_bytes == 0
