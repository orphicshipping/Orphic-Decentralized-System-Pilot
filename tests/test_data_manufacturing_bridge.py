"""Execute typed QC evidence through the real Workbench, Session and native reducer."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from ciw import data_manufacturing_bridge as bridge
from ciw.core.identities import canonical_json, content_identity
from ciw.data_sources import retain_record, native_record
from ciw.instruments import make_demo_run
from ciw.manufacturing_example import example_request
from ciw.manufacturing_runtime import evaluate as manufacture
from ciw.polymer_quality_example import manufacturing_request, source
from ciw.session import Session
from ciw.toolpoint_stream_example import demo
from ciw.toolpoint_stream_containment import project
from ciw.toolpoint_stream_store import StreamStore, initialize

STAMP = "2026-10-05T21:00:00Z"


def report(v2=True):
    request = manufacturing_request() if v2 else example_request()
    if v2:
        request["events"].append({"event_id": "synthetic.data-postpack-hold", "at_s": 11,
            "actor_id": "synthetic.inspector", "authorization_ref": source("inspector"),
            "kind": "quality_hold", "data": {"serial": "p001", "reason": "Synthetic post-pack concern",
            "evidence_ref": source("concern")}})
    return manufacture(request)


def add(session, native, dataset, family, *, revision=1, parent=None):
    wrapped = retain_record(native, dataset_id=dataset, revision=revision,
        parent_source_id=parent, recorded_at=STAMP, data_family=family)
    raw = json.dumps(wrapped, indent=2).encode()
    descriptor = session.workbench.add_source({"kind": "data-record", "label": dataset,
        "bytes_b64": base64.b64encode(raw).decode()})
    return wrapped, session.workbench.get_source(descriptor["source_id"])


def command(session, kind, payload):
    response = session.handle({"protocol_version": 1, "request_id": kind,
                              "type": kind, "payload": payload})
    assert response["type"] == "response", response
    return response["payload"]


def execute(session, parameters):
    return command(session, "operation.execute", {"operation_id": bridge.REVIEW, "parameters": parameters})


@pytest.fixture
def retained(tmp_path):
    session = Session(make_demo_run(), tmp_path / "session")
    manufacturing = report()
    _, msource = add(session, manufacturing, "manufacturing", "manufacturing-report")
    db = tmp_path / "native.sqlite3"
    initialize(db)
    reports, wrapped, descriptors = [], [], []
    for i in range(2):
        journal = demo(case="health-recovery" if i == 0 else "nominal", cycle_index=i)
        spec, head = journal["spec_ref"], journal["receipts"][-1]["receipt_ref"]
        StreamStore(db).import_report(str(i), journal, expected_spec_ref=spec, expected_head_ref=head)
        exported = StreamStore(db).export(str(i), expected_spec_ref=spec, expected_head_ref=head)
        envelope, descriptor = add(session, exported, "cycle-" + str(i), "toolpoint-stream-report")
        reports.append(exported); wrapped.append(envelope); descriptors.append(descriptor)
    parameters = bridge.review_parameters(session.workbench, manufacturing_source_id=msource["source_id"],
        stream_source_ids=[s["source_id"] for s in descriptors], assessed_at_s=12)
    return session, manufacturing, msource, reports, wrapped, descriptors, parameters, db


@pytest.mark.parametrize("v2", [False, True])
def test_native_manufacturing_versions_retain_exact_source(v2, tmp_path):
    session = Session(make_demo_run(), tmp_path)
    native = report(v2)
    wrapped, descriptor = add(session, native, "manufacturing", "manufacturing-report")
    assert native_record(descriptor) == native
    assert json.loads(base64.b64decode(wrapped["source_bytes"]["bytes_b64"])) == native
    assert wrapped["native_schema"] == native["schema"]


def test_real_sqlite_data_session_native_containment_and_replay(retained):
    session, manufacturing, ms, reports, _, sources, params, db = retained
    before = db.read_bytes()
    answer = execute(session, params)
    assert answer["status"] == "completed", answer
    result = answer["result"]
    expected = project({"schema": "ciw.toolpoint-stream-containment-request.v1",
        "manufacturing_report": manufacturing, "streams": reports, "assessed_at_s": 12})
    assert result["data"]["containment"] == expected
    assert result["data"]["containment_result_ref"] == content_identity(expected)
    assert result["data"]["authority"]["shipment_release"] == "not_authorized"
    assert result["data"]["authority"]["action_authorized"] is False
    assert result["verification_status"] == "not_verified"
    assert answer["dependency_status"]["status"] == "current"
    quality = expected["quality"]
    assert quality["p001"]["state"] == quality["p002"]["state"] == quality["a001"]["state"] == "HELD"
    assert quality["p004"]["state"] == "SCRAPPED"
    assert expected["packages"]["synthetic.package"]["state"] == "HELD"
    assert result["data"]["inputs"]["manufacturing"]["evaluation_id"] == manufacturing["evaluation_id"]
    for preserved, journal in zip(result["data"]["inputs"]["streams"], reports, strict=True):
        assert preserved["execution_id"] == journal["execution_id"]
        assert preserved["head_ref"] == journal["receipts"][-1]["receipt_ref"]
        assert preserved["native_report_ref"] == content_identity(journal)
    from ciw.dependency_graph import artifact_graph
    graph = artifact_graph(session.run, session.results, session.executions, session.workbench)
    for key in (result["result_id"], result["execution_id"]):
        assert set(bridge.source_ids(params)) <= set(graph[key]["dependencies"])
    replay = execute(session, params)["result"]
    assert replay["data"] == result["data"]
    assert replay["execution_id"] != result["execution_id"]
    assert replay["result_id"] != result["result_id"]
    assert db.read_bytes() == before


def test_provider_free_reopen_preserves_all_occurrences(retained, tmp_path, monkeypatch):
    session, _, _, _, _, _, params, _ = retained
    first = execute(session, params)["result"]
    saved = session.save_workspace(tmp_path / "workspace.json")
    def fail(*a, **kw):
        pytest.fail("Offline QC review touched a provider, process or SQLite store")
    import ciw.adapters.subprocess as provider
    import ciw.toolpoint_stream_store as store
    import ciw.polymer_models as models
    with monkeypatch.context() as m:
        m.setattr(provider, "_bounded_process", fail)
        m.setattr(store, "_connect", fail)
        m.setattr(models, "engineering_estimate", fail)
        m.setattr(subprocess, "run", fail)
        reopened = Session.from_workspace(saved, output_dir=tmp_path / "offline")
        assert reopened.results == session.results
        assert reopened.executions == session.executions
        assert reopened.workbench.retained_sources() == session.workbench.retained_sources()
        assert reopened.results[first["result_id"]]["runtime"] == first["runtime"]
        again = execute(reopened, params)
        assert again["result"]["data"] == first["data"]


@pytest.mark.parametrize("mutation", ["missing", "altered_descriptor", "foreign_cycle", "duplicate_cycle", "wrong_clock", "wrong_configuration", "wrong_serial", "duplicate", "future", "bool_time", "negative_time", "many", "wrong_family"])
def test_invalid_dependencies_or_native_bindings_refuse_and_retain_attempt(retained, mutation):
    session, _, _, _, _, sources, params, _ = retained
    changed = deepcopy(params)
    if mutation == "missing":
        other = Session(make_demo_run(), session.output_dir / "other")
        session = other
    elif mutation == "altered_descriptor":
        changed["manufacturing_source"]["label"] = "forged label"
    elif mutation == "duplicate_cycle":
        native = demo(case="nominal", cycle_index=1)
        # A valid report for cycle 1 cannot replace cycle 0 while cycle 1 is also present.
        _, d = add(session, native, "other-occurrence", "toolpoint-stream-report")
        changed["stream_sources"][0] = d
    elif mutation in {"foreign_cycle", "wrong_clock", "wrong_configuration", "wrong_serial"}:
        from ciw.toolpoint_stream_example import fixture
        from ciw.toolpoint_stream import CaptureJournal, make_spec
        from ciw.polymer_toolpoint_qc import signal_ref
        plan, observation = fixture(case="nominal")
        if mutation == "foreign_cycle": observation["cycle_id"] = "foreign.cycle"
        elif mutation == "wrong_clock":
            plan["clock_id"] = observation["clock_id"] = "foreign.clock"
            for channel in observation["signals"]:
                channel["clock_id"] = "foreign.clock"
                channel["source_ref"] = signal_ref(channel)
        elif mutation == "wrong_configuration":
            plan["configuration_ref"] = observation["configuration_ref"] = content_identity("foreign.configuration")
        else: observation["part_mapping"]["items"][0]["serial"] = "foreign.serial"
        journal = CaptureJournal(make_spec(plan, observation))
        journal.append(journal.update(observation, received_at_s=.81, final=True))
        _, d = add(session, journal.report(), "valid-foreign-report", "toolpoint-stream-report")
        changed["stream_sources"][0] = d
    elif mutation == "duplicate": changed["stream_sources"].append(deepcopy(sources[0]))
    elif mutation == "future": changed["assessed_at_s"] = 0
    elif mutation == "bool_time": changed["assessed_at_s"] = True
    elif mutation == "negative_time": changed["assessed_at_s"] = -1
    elif mutation == "many": changed["stream_sources"] *= 33
    elif mutation == "wrong_family":
        from ciw.polymer_contract import example_request as cycle
        _, d = add(session, cycle(), "wrong-schema", "polymer-cycle-request")
        changed["manufacturing_source"] = d
    result = execute(session, changed)
    assert result["status"] == "refused", result
    assert result["result"] is None
    assert result["execution"]["status"] == "refused"
    assert result["execution"]["execution_id"] in session.executions
    assert not session.results


def test_missing_streams_preserve_native_cohort_holds(retained):
    session, _, _, _, _, _, params, _ = retained
    params["stream_sources"] = []
    answer = execute(session, params)
    assert answer["status"] == "completed"
    quality = answer["result"]["data"]["containment"]["quality"]
    assert all(v["state"] in {"HELD", "SCRAPPED"} for v in quality.values())


@pytest.mark.parametrize("kind", ["manufacturing", "stream", "review", "authority", "copied_identity"])
def test_resealed_hold_and_identity_tampering_is_rejected(retained, kind):
    session, manufacturing, _, reports, _, _, params, _ = retained
    if kind in {"manufacturing", "stream"}:
        native = deepcopy(manufacturing if kind == "manufacturing" else reports[0])
        if kind == "manufacturing":
            native["result"]["instances"]["p001"]["quality_holds"] = []
        else:
            native["result"]["latched_findings"] = []
            native["result"]["status"] = "NO_PROCESS_HOLD_IDENTIFIED"
        native["result_ref"] = content_identity(native["result"])
        with pytest.raises(ValueError):
            add(session, native, "forged", "manufacturing-report" if kind == "manufacturing" else "toolpoint-stream-report")
    else:
        result = execute(session, params)["result"]
        forged = deepcopy(result["data"])
        if kind == "review":
            forged["containment"]["quality"]["a001"]["state"] = "ELIGIBLE_FOR_REVIEW"
            forged["containment_result_ref"] = content_identity(forged["containment"])
        elif kind == "authority": forged["authority"]["action_authorized"] = True
        else: forged["inputs"]["streams"][0]["execution_id"] = reports[1]["execution_id"]
        with pytest.raises(ValueError):
            bridge.validate_payload(bridge.REVIEW, forged, session.run, params, session.selection)


def test_saved_missing_dependency_is_rejected(retained, tmp_path):
    session, _, ms, _, _, _, params, _ = retained
    execute(session, params)
    saved = session.save_workspace(tmp_path / "workspace.json")
    value = json.loads(saved.read_text())
    # Workbench owns exact retained bytes; the saved result's embedded descriptor
    # cannot act as a substitute for a missing Workbench source.
    def remove(node):
        if isinstance(node, dict):
            for key, child in list(node.items()):
                if key == "sources" and isinstance(child, list):
                    node[key] = [v for v in child if v.get("source_id") != ms["source_id"]]
                else: remove(child)
        elif isinstance(node, list):
            for child in node: remove(child)
    remove(value)
    saved.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        Session.from_workspace(saved, output_dir=tmp_path / "missing")


def test_correction_stales_only_dependent_review_and_never_rewrites_native_store(retained, tmp_path):
    session, _, _, reports, wrapped, sources, params, db = retained
    first = execute(session, params)["result"]
    # A separate retained cohort does not depend on this stream's exact source.
    other = deepcopy(params)
    replacement = demo(case="nominal", cycle_index=0)
    _, separate = add(session, replacement, "separate-cohort-source", "toolpoint-stream-report")
    other["stream_sources"][0] = separate
    independent_response = execute(session, other)
    assert independent_response["status"] == "completed", independent_response
    independent = independent_response["result"]
    before = db.read_bytes()
    _, revised = add(session, replacement, "cycle-0", "toolpoint-stream-report", revision=2,
                     parent=wrapped[0]["source_id"])
    proposal = command(session, "correction.propose", {"old_source_id": sources[0]["source_id"],
        "new_source_id": revised["source_id"], "kind": "source_data", "reason": "Declared source revision for review"})
    command(session, "correction.review", {"correction_id": proposal["correction_id"], "decision": "accept",
        "expected_revision": session.correction_journal.revision, "reviewer": "operator", "reason": "checked declaration"})
    status = session.dependency_status()["artifact_status"]
    assert status[first["result_id"]]["status"] == status[first["execution_id"]]["status"] == "stale"
    assert status[independent["result_id"]]["status"] == "current"
    assert session.results[first["result_id"]] == first
    assert db.read_bytes() == before
    assert StreamStore(db).export("0")["result"] == reports[0]["result"]
    fresh_params = deepcopy(params); fresh_params["stream_sources"][0] = revised
    fresh = execute(session, fresh_params)
    assert fresh["dependency_status"]["status"] == "current"
    assert fresh["result"]["data"]["containment"]["quality"]["a001"]["state"] == "HELD"
    assert fresh["result"]["data"]["authority"]["shipment_release"] == "not_authorized"
    old_again = execute(session, params)
    assert old_again["dependency_status"]["status"] == "stale"
    saved = session.save_workspace(tmp_path / "corrected.json")
    reopened = Session.from_workspace(saved, output_dir=tmp_path / "reopened")
    assert reopened.dependency_status()["artifact_status"] == session.dependency_status()["artifact_status"]


def test_actual_native_data_cli_retain_add_review_reopen_and_create_only(tmp_path):
    session = Session(make_demo_run(), tmp_path / "start")
    workspace = session.save_workspace(tmp_path / "empty.json")
    env = os.environ.copy()
    from ciw import data_manufacturing_bridge
    package = Path(data_manufacturing_bridge.__file__).resolve().parent.parent
    if package.name == "src": env["PYTHONPATH"] = str(package)
    else: env.pop("PYTHONPATH", None)
    def cli(*args, expected=0):
        completed = subprocess.run([sys.executable, "-m", "ciw.net", "data", *map(str,args)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert completed.returncode == expected, completed.stderr + completed.stdout
        return json.loads(completed.stdout)
    ids = []
    for i, (native, family) in enumerate([(report(), "manufacturing-report"),
        (demo(case="health-recovery"), "toolpoint-stream-report"),
        (demo(case="nominal", cycle_index=1), "toolpoint-stream-report")]):
        raw=tmp_path/f"native-{i}.json"; raw.write_text(json.dumps(native, indent=2))
        wrapped=tmp_path/f"source-{i}.json"; new_workspace=tmp_path/f"workspace-{i}.json"
        cli("retain",raw,"--family",family,"--dataset-id",f"demo-{i}","--revision",1,
            "--recorded-at",STAMP,"--output",wrapped)
        added=cli("add",wrapped,"--label",f"demo-{i}","--workspace",workspace,"--output",new_workspace)
        ids.append(added["source_id"]);workspace=new_workspace
    destination=tmp_path/"reviewed.json"
    args=["review-toolpoint","--manufacturing-source-id",ids[0],"--stream-source-id",ids[1],
        "--stream-source-id",ids[2],"--assessed-at-s",12,"--workspace",workspace,"--output",destination]
    reviewed=cli(*args)
    assert reviewed["status"]=="completed"
    assert reviewed["result"]["data"]["containment"]["quality"]["a001"]["state"]=="HELD"
    before=destination.read_bytes();cli(*args,expected=2);assert destination.read_bytes()==before
    reopened=Session.from_workspace(destination,output_dir=tmp_path/"restored")
    assert reopened.results[reviewed["result"]["result_id"]]==reviewed["result"]
    assert reopened.results[reviewed["result"]["result_id"]]["data"]["authority"]["action_authorized"] is False


def test_review_parameter_budget_fails_closed(retained, monkeypatch):
    session, _, _, _, _, _, params, _ = retained
    monkeypatch.setattr(bridge, "MAX_PARAMETERS_BYTES", 1)
    answer = execute(session, params)
    assert answer["status"] == "refused" and answer["result"] is None
    assert "budget" in answer["execution"]["refusal"]["message"]


def test_unqualified_snapshot_profile_cannot_enter_native_data(retained):
    session, _, _, reports, _, _, _, _ = retained
    altered = deepcopy(reports[0])
    altered["spec"]["plan"]["schema"] = "ciw.toolpoint-control-plan.v1"
    with pytest.raises(ValueError, match="qualified native"):
        add(session, altered, "snapshot-profile", "toolpoint-stream-report")


def test_mismatched_typed_family_refuses(retained):
    session, _, _, reports, _, _, _, _ = retained
    with pytest.raises(ValueError, match="family"):
        add(session, reports[0], "wrong-family", "manufacturing-report")


def test_native_review_cli_marks_stale_occurrence_and_keeps_old_bytes(retained, tmp_path, capsys):
    from ciw.data_cli import main
    session, _, ms, _, wrapped, sources, params, db = retained
    # CLI explicitly declares a float time. Preserve that exact numeric identity.
    params["assessed_at_s"] = 12.0
    first = execute(session, params)["result"]
    _, revised = add(session, demo(case="nominal"), "cycle-0", "toolpoint-stream-report", revision=2,
                     parent=wrapped[0]["source_id"])
    proposal = command(session, "correction.propose", {"old_source_id": sources[0]["source_id"],
        "new_source_id": revised["source_id"], "kind": "source_data", "reason": "Declared correction"})
    command(session, "correction.review", {"correction_id": proposal["correction_id"], "decision": "accept",
        "expected_revision": session.correction_journal.revision, "reviewer": "operator", "reason": "checked"})
    saved = session.save_workspace(tmp_path / "corrected-before.json")
    output = tmp_path / "stale-review.json"
    before = db.read_bytes()
    assert main(["review-toolpoint", "--manufacturing-source-id", ms["source_id"],
        "--stream-source-id", sources[0]["source_id"], "--stream-source-id", sources[1]["source_id"],
        "--assessed-at-s", "12", "--workspace", str(saved), "--output", str(output)]) == 2
    displayed = json.loads(capsys.readouterr().out)
    assert displayed["status"] == "completed"
    assert displayed["dependency_status"]["status"] == "stale"
    assert displayed["result"]["data"] == first["data"]
    assert displayed["result"]["result_id"] != first["result_id"]
    assert db.read_bytes() == before


def test_declared_time_numeric_representation_is_not_silently_coerced(retained):
    session, _, _, _, _, _, params, _ = retained
    integer = execute(session, params)["result"]["data"]
    params["assessed_at_s"] = 12.0
    floating = execute(session, params)["result"]["data"]
    assert integer["native_request_ref"] != floating["native_request_ref"]
    assert integer["containment_result_ref"] == floating["containment_result_ref"]
    assert integer["inputs"] == floating["inputs"]
