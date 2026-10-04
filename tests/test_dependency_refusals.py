"""Refused requests retain history without inventing computational dependencies."""
from copy import deepcopy

import pytest

from ciw.session import Session
from test_correction_session import (
    _accept, _call, _claim_payload, _execute, _sources,
)


@pytest.mark.parametrize("operation,parameters", [
    ("system.verify.v1", {"candidate": {"result_id": "result-missing"}}),
    ("system.simulate.v1", {"plan": {"result_id": "result-missing"}}),
    ("system.compare.v1", {"left": {"result_id": "result-missing"},
                           "right": {"result_id": "result-also-missing"}}),
    ("statistics.v1", {"source_result_id": "result-missing"}),
    ("statistics.v1", {"source_result_id": []}),
    ("statistics.v1", {"source_result_id": {"invalid": "reference"}}),
])
def test_refused_dependencies_preserve_staleness_and_offline_reopen(
        tmp_path, monkeypatch, operation, parameters):
    session, old, new = _sources(tmp_path / "original")
    original = _execute(session, old)
    step = original["steps"][0]
    _accept(session, old, new)
    before = _call(session, "dependency.inspect")
    refused = _call(session, "operation.execute", {
        "operation_id": operation, "parameters": parameters,
    })
    assert refused["status"] == "refused" and refused["result"] is None
    execution = refused["execution"]
    execution_before = deepcopy(execution)
    execution_path = session.output_dir / (execution["execution_id"] + ".json")
    execution_bytes = execution_path.read_bytes()
    graph = _call(session, "dependency.inspect")
    node = graph["nodes"][execution["execution_id"]]
    assert node["dependencies"] == [session.run["evidence_id"]]
    assert node["outcome"] == "refused"
    assert graph["artifact_status"][execution["execution_id"]]["status"] == "current"
    for identity, status in before["artifact_status"].items():
        assert graph["artifact_status"][identity] == status
    assert graph["artifact_status"][step["result_id"]]["status"] == "stale"
    # The refusal cannot prevent a later journal action from checkpointing.
    claim = _call(session, "claim.add", _claim_payload([step["result_id"]]))
    expected = _call(session, "dependency.inspect")
    assert expected["artifact_status"][claim["claim_id"]]["status"] == "stale"
    saved = session.save_workspace(tmp_path / "workspace.json")
    saved_bytes = saved.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Read-only correction restore executed an operation")

    monkeypatch.setattr("ciw.session.execute_operation", forbidden)
    monkeypatch.setattr("ciw.machine_workflow.MachineManifestWorkflow.create_session", forbidden)
    restored = Session.from_workspace(saved, tmp_path / "restored")
    assert _call(restored, "dependency.inspect") == expected
    assert restored.executions[execution["execution_id"]] == execution_before
    assert _call(restored, "bundle.get", {"bundle_id": original["bundle_digest"]}) == original
    assert execution_path.read_bytes() == execution_bytes
    assert saved.read_bytes() == saved_bytes


def test_refused_compile_does_not_claim_use_of_a_known_withdrawn_source(tmp_path):
    session, old, new = _sources(tmp_path)
    _accept(session, old, new)
    # This is an encoder source, not a valid system compilation declaration.
    refused = _call(session, "operation.execute", {
        "operation_id": "system.compile.v1", "parameters": {"source_id": old["source_id"]},
    })
    assert refused["status"] == "refused" and refused["result"] is None
    execution = refused["execution"]
    assert execution["parameters"]["source_id"] == old["source_id"]
    graph = _call(session, "dependency.inspect")
    assert graph["artifact_status"][old["source_id"]]["status"] == "stale"
    assert graph["nodes"][execution["execution_id"]]["dependencies"] == [session.run["evidence_id"]]
    assert graph["artifact_status"][execution["execution_id"]]["status"] == "current"


def test_completed_recording_dependencies_still_propagate():
    from ciw.correction_journal import CorrectionJournal
    from ciw.dependency_graph import artifact_graph

    class RetainedWorkbench:
        def dependency_artifacts(self):
            return {
                "sources": [
                    {"source_id": "source:" + label, "evidence_id": "evidence:" + label,
                     "kind": "machine-manifest", "label": label}
                    for label in ("old", "new")
                ],
                "bundles": [], "executions": [],
            }

    # Retained descriptors exercise graph projection without running providers.
    declarations = [
        ("plan", "system.compile.v1", {"source_id": "source:old"}),
        ("left", "system.simulate.v1", {"plan": {"result_id": "result:plan"},
                                       "source_result_id": "result:plan"}),
        ("right", "statistics.v1", {}),
        ("comparison", "system.compare.v1", {"left": {"result_id": "result:left"},
                                             "right": {"result_id": "result:right"}}),
    ]
    executions, results = {}, {}
    for label, operation, parameters in declarations:
        execution_id, result_id = "execution:" + label, "result:" + label
        common = {"execution_id": execution_id, "operation_id": operation,
                  "evidence_id": "evidence:run", "parameters": parameters}
        executions[execution_id] = {**common, "status": "completed"}
        results[result_id] = {**common, "result_id": result_id}
    graph = artifact_graph({"run_id": "run:fixture", "evidence_id": "evidence:run"},
                           results, executions, RetainedWorkbench())
    assert graph["execution:plan"]["dependencies"] == ["evidence:run", "source:old"]
    assert graph["execution:left"]["dependencies"] == ["evidence:run", "result:plan"]
    assert graph["execution:comparison"]["dependencies"] == [
        "evidence:run", "result:left", "result:right",
    ]
    journal = CorrectionJournal()
    claim = journal.add_claim(_claim_payload(["result:comparison"]), graph)
    correction = journal.propose({
        "old_source_id": "source:old", "new_source_id": "source:new",
        "kind": "calibration_assumptions", "reason": "Revise the fixture source.",
    }, graph)
    journal.review({
        "correction_id": correction["correction_id"], "decision": "accept",
        "expected_revision": journal.revision, "reviewer": "Declared fixture reviewer",
        "reason": "Use the replacement fixture source.",
    }, graph)
    statuses = journal.status(graph)["artifact_status"]
    expected_stale = {"source:old", claim["claim_id"]}
    expected_stale.update(prefix + label for prefix in ("execution:", "result:")
                          for label in ("plan", "left", "comparison"))
    assert {identity for identity, value in statuses.items()
            if value["status"] == "stale"} == expected_stale
    assert statuses["execution:right"]["status"] == statuses["result:right"]["status"] == "current"
    assert statuses["source:new"]["status"] == statuses["evidence:old"]["status"] == "current"
    restored = CorrectionJournal.restore(journal.serialize(), graph)
    assert restored.status(graph) == journal.status(graph)
