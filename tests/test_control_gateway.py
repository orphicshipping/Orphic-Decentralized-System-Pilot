"""End-to-end plan admission over the existing execution owner."""
from copy import deepcopy
import threading
import time

import pytest

from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost


@pytest.fixture
def configured(tmp_path):
    profile = demo_config(tmp_path / "demo")
    owner = from_profile(profile)
    host = ControlHost(owner)
    yield profile, owner, host
    host.close()
    owner.close()


def call(host, name, **arguments):
    response = host.call(name, arguments)
    assert response["status"] == "ok", response
    return response["data"]


def plan(host):
    draft = call(host, "ncp_plan_template", baseline="baseline")
    return call(host, "ncp_plan_create", source="source", baseline="baseline", graph=draft["graph"])


def wait(host, run_id):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        value = call(host, "ncp_run_get", run_id=run_id)
        if value["state"] not in {"queued", "running"}:
            return value
        time.sleep(.01)
    pytest.fail("Bounded fixture execution did not finish")


def test_discover_compile_admit_execute_inspect_retry(configured):
    profile, owner, host = configured
    source_bytes = (profile.parent / "source.json").read_bytes()
    capabilities = call(host, "ncp_capabilities_list")
    assert len(capabilities["items"]) == 9
    assert capabilities["authorizes_execution"] is False
    draft = plan(host)
    assert draft["admitted"] is False
    assert owner.capabilities()["execution_budget"]["used"] == 0
    refused = host.call("ncp_run_submit", {"plan_id": draft["plan_id"], "expected_digest": draft["digest"], "attempt": "before-admission"})
    assert refused["status"] == "refused"
    admitted = call(host, "ncp_plan_admit", plan_id=draft["plan_id"], expected_digest=draft["digest"])
    assert admitted["admitted"] is True
    run = call(host, "ncp_run_submit", plan_id=draft["plan_id"], expected_digest=draft["digest"], attempt="first")
    done = wait(host, run["run_id"])
    assert done["state"] == "completed"
    assert len(done["result"]["execution_ids"]) == 1
    again = call(host, "ncp_run_submit", plan_id=draft["plan_id"], expected_digest=draft["digest"], attempt="first")
    assert again["run_id"] == done["run_id"]
    assert owner.capabilities()["execution_budget"]["used"] == 1
    artifact = done["result"]["artifacts"]["graph-run"]["artifact_id"]
    found = call(host, "ncp_artifact_read", artifact=artifact, selector=["nodes", "statistics", "result", "data"])
    assert found["data"]["unit"] == "m"
    assert (profile.parent / "source.json").read_bytes() == source_bytes


@pytest.mark.parametrize("attack", ["operation", "parameters", "model", "acceptance", "source", "digest"])
def test_plan_cannot_widen_host_authority(configured, attack):
    _, owner, host = configured
    graph = call(host, "ncp_plan_template", baseline="baseline")["graph"]
    source = "source"
    if attack == "operation": graph["nodes"][0]["capability"] = "analysis.spectrum.v1"
    elif attack == "parameters": graph["nodes"][0]["parameters"]["atol"] = 1000
    elif attack == "model": graph["model_id"] = "another-model"
    elif attack == "acceptance": graph["nodes"][0]["acceptance"] = {"pass": True}
    elif attack == "source": source = "../../secret"
    if attack == "digest":
        p = plan(host)
        response = host.call("ncp_plan_admit", {"plan_id": p["plan_id"], "expected_digest": "sha256:" + "0" * 64})
    else:
        response = host.call("ncp_plan_create", {"source": source, "baseline": "baseline", "graph": graph})
    assert response["status"] == "refused"
    assert owner.capabilities()["execution_budget"]["used"] == 0


def test_parameter_candidate_and_conflicting_attempt(configured):
    _, owner, host = configured
    first = plan(host)
    call(host, "ncp_plan_admit", plan_id=first["plan_id"], expected_digest=first["digest"])
    run = call(host, "ncp_run_submit", plan_id=first["plan_id"], expected_digest=first["digest"], attempt="same")
    wait(host, run["run_id"])
    graph = call(host, "ncp_plan_template", baseline="baseline")["graph"]
    graph["nodes"][0]["parameters"]["channel"] = "v"
    other = call(host, "ncp_plan_create", source="source", baseline="baseline", graph=graph)
    call(host, "ncp_plan_admit", plan_id=other["plan_id"], expected_digest=other["digest"])
    assert host.call("ncp_run_submit", {"plan_id": other["plan_id"], "expected_digest": other["digest"], "attempt": "same"})["status"] == "refused"
    assert owner.capabilities()["execution_budget"]["used"] == 1


def test_completed_jobs_survive_resume_without_execution(tmp_path):
    profile = demo_config(tmp_path / "demo")
    owner = from_profile(profile)
    host = ControlHost(owner)
    p = plan(host)
    call(host, "ncp_plan_admit", plan_id=p["plan_id"], expected_digest=p["digest"])
    run = call(host, "ncp_run_submit", plan_id=p["plan_id"], expected_digest=p["digest"], attempt="retained")
    done = wait(host, run["run_id"])
    host.close(); owner.close()
    reopened = from_profile(profile, resume=True)
    with ControlHost(reopened, resume=True) as resumed:
        saved = call(resumed, "ncp_run_get", run_id=done["run_id"])
        assert saved == done
        assert reopened.capabilities()["execution_budget"]["used"] == 1
    reopened.close()


def test_progress_and_cancellation_remain_responsive(configured, monkeypatch):
    _, owner, host = configured
    entered, release = threading.Event(), threading.Event()
    original = owner.execute
    def slow(**kw):
        entered.set()
        assert release.wait(10)
        return original(**kw)
    monkeypatch.setattr(owner, "execute", slow)
    p = plan(host)
    call(host, "ncp_plan_admit", plan_id=p["plan_id"], expected_digest=p["digest"])
    run = call(host, "ncp_run_submit", plan_id=p["plan_id"], expected_digest=p["digest"], attempt="slow")
    try:
        assert entered.wait(5)
        assert call(host, "ncp_capabilities_list")["items"]
        assert call(host, "ncp_plan_template", baseline="baseline")["graph"]
        active = call(host, "ncp_run_get", run_id=run["run_id"])
        assert active["state"] == "running"
        cancelled = call(host, "ncp_run_cancel", run_id=run["run_id"])
        assert cancelled["state"] == "running" and cancelled["cancel_requested"] is True
    finally:
        release.set()
    done = wait(host, run["run_id"])
    assert done["state"] == "completed" and done["cancel_requested"] is True


def test_typed_tool_envelopes_and_resources(configured):
    _, _, host = configured
    import jsonschema
    for tool in host.descriptions():
        assert tool["outputSchema"]["required"] == ["schema", "status", "data", "error"]
    value = host.call("ncp_capabilities_list", {})
    spec = next(t for t in host.descriptions() if t["name"] == "ncp_capabilities_list")
    jsonschema.validate(value, spec["outputSchema"])
    p = plan(host)
    assert host.read_resource(p["uri"])["plan_id"] == p["plan_id"]
    with pytest.raises(ValueError): host.read_resource("file:///etc/passwd")


def test_wider_catalog_is_navigation_not_execution(configured):
    _, owner, host = configured
    first = call(host, "ncp_workflows_list", limit=7)
    second = call(host, "ncp_workflows_list", offset=first["next_offset"], limit=32)
    rows = first["items"] + second["items"]
    assert len(rows) == first["total"] == 29
    assert len({r["workflow"] for r in rows}) == 29
    assert all(r["authorizes_execution"] is False for r in rows)
    assert owner.capabilities()["execution_budget"]["used"] == 0


def test_index_pages_every_capability_without_authorizing(configured):
    import json
    from pathlib import Path
    from ciw.capabilities import catalog
    from ciw.numerical_contracts import OPERATIONS
    _, owner, host = configured
    used = owner.capabilities()["execution_budget"]["used"]
    first = call(host, "ncp_index_list", limit=32)
    assert first["next_offset"] == 32
    second = call(host, "ncp_index_list", offset=first["next_offset"], limit=32)
    rows = first["items"] + second["items"]
    assert second["next_offset"] is None
    assert len(rows) == first["total"] == first["index_total"] + first["ledger_total"]
    assert first["authorizes_execution"] is False
    assert all(row["authorizes_execution"] is False for row in rows)
    ids = [row["capability_id"] for row in rows]
    assert len(ids) == len(set(ids))
    index_ids = [item["capability_id"] for item in catalog()["capabilities"]]
    ledger = json.loads((Path(__file__).resolve().parents[1] / "release" / "capability-ledger.json").read_text())
    ledger_ids = [item["id"] for item in ledger["capabilities"]]
    assert first["index_total"] == len(index_ids)
    assert first["ledger_total"] == len(ledger_ids)
    assert set(index_ids + ledger_ids) == set(ids)
    by_id = {row["capability_id"]: row for row in rows}
    assert by_id["statistics.v1"]["mcp_profiles"] == ["analysis", "builtin"]
    assert by_id["statistics.v1"]["mcp_profile"] == "analysis"
    assert by_id["spectrum.periodogram.v1"]["mcp_profile"] == "analysis"
    for name in OPERATIONS:
        assert by_id[name]["mcp_profile"] == "numerical" and by_id[name]["callable"] is True
    for name in ("system.compile.v1", "legibility.compile.v1", "data.compare-observations.v1",
                 "learning.oscillator-rms", "doctor.preflight", "linear-response.library",
                 "synthetic-oscillator-demo", "bounded_fluid_models", "proved_heat"):
        assert by_id[name]["mcp_profile"] is None and by_id[name]["callable"] is False
    granted = [item["operation_id"] for item in call(host, "ncp_capabilities_list")["items"] if item["authorized"]]
    assert granted == ["statistics.v1"]
    assert owner.capabilities()["execution_budget"]["used"] == used
    assert "ncp_index_list" in {tool["name"] for tool in host.descriptions()}


def test_preserves_baseline_global_context(tmp_path):
    import json
    from ciw.control_plane import experiment
    profile = demo_config(tmp_path / "demo")
    path = profile.parent / "experiment.json"
    original = json.loads(path.read_text())
    original = experiment(original["experiment_id"], model_id=original["model_id"],
        nodes=original["nodes"], parameters={"context": "host-owned"},
        parent_checkpoint="sha256:" + "a" * 64)
    path.write_text(json.dumps(original))
    with from_profile(profile) as owner, ControlHost(owner) as host:
        concrete = plan(host)["compilation"]["experiment"]
        assert concrete["parameters"] == original["parameters"]
        assert concrete["parent_checkpoint"] == original["parent_checkpoint"]


def test_queued_cancellation_never_dispatches(configured, monkeypatch):
    _, owner, host = configured
    monkeypatch.setattr(host._executor, "submit", lambda *args: None)
    p = plan(host)
    call(host, "ncp_plan_admit", plan_id=p["plan_id"], expected_digest=p["digest"])
    run = call(host, "ncp_run_submit", plan_id=p["plan_id"], expected_digest=p["digest"], attempt="queued")
    cancelled = call(host, "ncp_run_cancel", run_id=run["run_id"])
    assert cancelled["state"] == "cancelled"
    host._work(run["run_id"])
    assert owner.capabilities()["execution_budget"]["used"] == 0


@pytest.mark.parametrize("state", ["queued", "running"])
def test_interrupted_control_intent_recovers_unknown_without_dispatch(tmp_path, monkeypatch, state):
    profile = demo_config(tmp_path / "demo")
    with from_profile(profile) as owner, ControlHost(owner) as host:
        monkeypatch.setattr(host._executor, "submit", lambda *args: None)
        p = plan(host)
        call(host, "ncp_plan_admit", plan_id=p["plan_id"], expected_digest=p["digest"])
        run = call(host, "ncp_run_submit", plan_id=p["plan_id"], expected_digest=p["digest"], attempt="interrupted")
        host._jobs[run["run_id"]]["state"] = state
        host._save()
    with from_profile(profile, resume=True) as owner, ControlHost(owner, resume=True) as host:
        saved = call(host, "ncp_run_get", run_id=run["run_id"])
        assert saved["state"] == "unknown"
        assert call(host, "ncp_run_submit", plan_id=p["plan_id"], expected_digest=p["digest"], attempt="interrupted") == saved
        assert owner.capabilities()["execution_budget"]["used"] == 0


@pytest.mark.parametrize("damage", ["plan", "outcome", "cancelled", "engine_id", "unfinished_result"])
def test_recovery_rejects_even_rehashed_inconsistent_state(tmp_path, damage):
    profile = demo_config(tmp_path / "demo")
    with from_profile(profile) as owner, ControlHost(owner) as host:
        p = plan(host)
        call(host, "ncp_plan_admit", plan_id=p["plan_id"], expected_digest=p["digest"])
        run = call(host, "ncp_run_submit", plan_id=p["plan_id"], expected_digest=p["digest"], attempt="retained")
        wait(host, run["run_id"])
        job = host._jobs[run["run_id"]]
        if damage == "plan": host._plans[p["plan_id"]]["compilation"]["experiment"]["nodes"][0]["parameters"]["channel"] = "v"
        elif damage == "outcome": job["state"] = "failed"
        elif damage == "cancelled": job["state"] = "cancelled"
        elif damage == "engine_id": job["engine_attempt"] = "other"
        elif damage == "unfinished_result": job["state"] = "running"
        host._save()
    with from_profile(profile, resume=True) as owner:
        with pytest.raises(ValueError): ControlHost(owner, resume=True)


def test_dispatch_queue_failure_is_durable_unknown(configured, monkeypatch):
    _, owner, host = configured
    def failure(*args): raise RuntimeError("executor stopped")
    monkeypatch.setattr(host._executor, "submit", failure)
    p = plan(host)
    call(host, "ncp_plan_admit", plan_id=p["plan_id"], expected_digest=p["digest"])
    run = call(host, "ncp_run_submit", plan_id=p["plan_id"], expected_digest=p["digest"], attempt="queue-failed")
    assert run["state"] == "unknown"
    assert owner.capabilities()["execution_budget"]["used"] == 0


def test_closed_resource_cannot_bypass_tool_lifecycle(configured):
    _, _, host = configured
    p = plan(host)
    host.close()
    with pytest.raises(ValueError): host.read_resource(p["uri"])
    assert host.call("ncp_plan_get", {"plan_id": p["plan_id"]})["status"] == "refused"
