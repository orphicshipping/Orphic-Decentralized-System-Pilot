"""Restart identity, conservative interrupted outcomes and exclusive local ownership."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import ciw.agent_api as agent_api
from ciw.agent_api import AgentHost, encode
from ciw.control_plane import builtin_registry, experiment, ParameterSpace, Choice
from ciw.instruments import make_demo_run


@pytest.fixture
def settings(tmp_path):
    graph = experiment("recovery", model_id="oscillator", nodes=[{
        "node_id": "statistics", "operation_id": "statistics.v1", "parameters": {"channel": "q"},
        "inputs": {}, "depends_on": []}])
    return dict(registry=builtin_registry(bind=True), inputs={
        "source": encode(make_demo_run()), "baseline": encode(graph)},
        output_dir=tmp_path / "output", allow_operations=("statistics.v1",),
        candidate_domains={"baseline": {"statistics": ParameterSpace({"channel": Choice(("q", "v"))}).to_dict()}},
        max_executions=2)


def execute(host, attempt="first", graph="baseline"):
    return host.call("net_execute", {"source": "source", "graph": graph, "attempt": attempt})


def forbid_dispatch(*args, **kwargs):
    pytest.fail("Recovery or a retry must not dispatch a provider")


def test_completed_restart_retains_response_and_offline_inspection(settings, monkeypatch):
    with AgentHost(**settings) as host:
        original = execute(host)
        retained = host.capabilities()["retained_artifacts"]
    monkeypatch.setattr("ciw.agent_api.run_graph", forbid_dispatch)
    with AgentHost(**settings, resume=True) as host:
        assert execute(host) == {**original, "reused_response": True}
        assert host.capabilities()["retained_artifacts"] == retained
        workspace = host.inspect(original["artifacts"]["workspace"]["artifact_id"])["data"]
        assert set(workspace["executions"]) == set(original["execution_ids"])
        assert set(workspace["results"]) == set(original["result_ids"])
        assert host.capabilities()["execution_budget"]["used"] == 1


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_interrupted_attempt_never_automatically_reruns(settings, monkeypatch, after_dispatch):
    host = AgentHost(**settings)
    if after_dispatch:
        checkpoint = host._checkpoint
        def crash_on_completion():
            if host._attempts.get("first", {}).get("state") == "finished":
                raise KeyboardInterrupt("power interruption before completion checkpoint")
            checkpoint()
        monkeypatch.setattr(host, "_checkpoint", crash_on_completion)
    else:
        def interrupted(*args):
            raise KeyboardInterrupt("provider interrupted")
        monkeypatch.setattr("ciw.agent_api.run_graph", interrupted)
    with pytest.raises(KeyboardInterrupt):
        execute(host)
    host.close()
    monkeypatch.setattr("ciw.agent_api.run_graph", forbid_dispatch)
    with AgentHost(**settings, resume=True) as resumed:
        result = execute(resumed)
        assert result["status"] == "incomplete"
        assert result["execution_outcome"] == "unknown" and result["reused_response"]
        assert "execution_ids" not in result
        assert resumed.capabilities()["execution_budget"]["used"] == 1
        assert resumed.capabilities()["attempts"] == [{"attempt": "first", "status": "incomplete", "execution_outcome": "unknown"}]


@pytest.mark.parametrize("change", ["bytes", "alias", "policy"])
def test_resume_refuses_changed_initial_bindings_and_policy(settings, change):
    with AgentHost(**settings):
        pass
    changed = {**settings, "inputs": deepcopy(settings["inputs"])}
    if change == "bytes":
        changed["inputs"]["source"] += b"\n"
    elif change == "alias":
        changed["inputs"]["another"] = changed["inputs"]["source"]
    else:
        changed["max_executions"] += 1
    with pytest.raises(ValueError, match="bindings or host policy"):
        AgentHost(**changed, resume=True)
    # Failed restoration must release its lock, leaving the valid state resumable.
    with AgentHost(**settings, resume=True) as host:
        assert host.capabilities()["execution_budget"]["used"] == 0


@pytest.mark.parametrize("damage", ["checkpoint_missing", "checkpoint_changed", "blob_missing", "blob_changed"])
def test_resume_refuses_missing_or_corrupt_state(settings, damage):
    with AgentHost(**settings):
        pass
    root = settings["output_dir"] / ".agent-state"
    checkpoint = root / "checkpoint.json"
    if damage == "checkpoint_missing":
        checkpoint.unlink()
    elif damage == "checkpoint_changed":
        value = json.loads(checkpoint.read_bytes())
        value["state"]["aliases"] = {}
        checkpoint.write_text(json.dumps(value))
    else:
        blob = next((root / "artifacts").iterdir())
        if damage == "blob_missing":
            blob.unlink()
        else:
            blob.write_bytes(b"{}")
    with pytest.raises(ValueError, match="recovery"):
        AgentHost(**settings, resume=True)


def test_budget_and_attempt_collision_survive_restart(settings):
    settings["max_executions"] = 1
    with AgentHost(**settings) as host:
        original = execute(host)
        candidate = host.candidate("baseline", {"statistics": {"channel": "v"}})["candidate"]["artifact_id"]
    with AgentHost(**settings, resume=True) as host:
        assert execute(host)["execution_ids"] == original["execution_ids"]
        with pytest.raises(ValueError, match="different request"):
            execute(host, graph=candidate)
        with pytest.raises(ValueError, match="budget"):
            execute(host, attempt="second")


def test_candidate_domains_and_explicit_fresh_replay_survive_restart(settings):
    with AgentHost(**settings) as host:
        candidate = host.candidate("baseline", {"statistics": {"channel": "v"}})["candidate"]["artifact_id"]
        original = execute(host, graph=candidate)
    with AgentHost(**settings, resume=True) as host:
        assert candidate in host.capabilities()["candidate_domains"]
        assert host.inspect(candidate)["data"]["nodes"][0]["parameters"]["channel"] == "v"
        second = host.replay("first", "explicitReplay")
        assert second["status"] == "completed"
        assert second["execution_ids"] != original["execution_ids"]
        assert second["result_ids"] != original["result_ids"]


def test_concurrent_process_is_refused_and_close_releases_lock(settings):
    environment = {**os.environ, "PYTHONPATH": str(Path(agent_api.__file__).resolve().parent.parent)}
    script = "from ciw.agent_recovery import RecoveryStore; from pathlib import Path; import ciw.agent_api as api; import sys; assert Path(api.__file__).resolve().parent.parent == Path(sys.argv[2]).resolve(); s=RecoveryStore(Path(sys.argv[1]),resume=True); s.close()"
    def contender():
        return subprocess.run([sys.executable, "-c", script, str(settings["output_dir"]), environment["PYTHONPATH"]],
                              env=environment, text=True, capture_output=True, timeout=30)
    host = AgentHost(**settings)
    try:
        blocked = contender()
        assert blocked.returncode != 0 and "active writer" in blocked.stderr
        with pytest.raises(ValueError, match="active writer"):
            AgentHost(**settings, resume=True)
    finally:
        host.close()
    assert contender().returncode == 0
    with pytest.raises(ValueError, match="closed"):
        execute(host)


def test_resume_requires_existing_state_and_fresh_default_still_refuses(settings):
    with pytest.raises(ValueError, match="Missing"):
        AgentHost(**settings, resume=True)
    with AgentHost(**settings):
        pass
    with pytest.raises(FileExistsError):
        AgentHost(**settings)


def test_killed_provider_process_leaves_unknown_attempt_and_releases_lock(settings, tmp_path, monkeypatch):
    config = tmp_path / "child-inputs.json"
    config.write_text(json.dumps({
        "inputs": {key: raw.decode() for key, raw in settings["inputs"].items()},
        "candidate_domains": settings["candidate_domains"],
        "output_dir": str(settings["output_dir"])}))
    marker = tmp_path / "provider-completed.json"
    script = tmp_path / "interrupted-provider.py"
    script.write_text("""
import json
from pathlib import Path
import sys
import threading
import ciw.agent_api as api
from ciw.control_plane import builtin_registry
config = json.loads(Path(sys.argv[1]).read_text())
assert Path(api.__file__).resolve().parent.parent == Path(sys.argv[3]).resolve()
normal = api.run_graph
def blocked_after_provider(session, graph, registry):
    report = normal(session, graph, registry)
    marker = Path(sys.argv[2])
    pending = marker.with_suffix(".pending")
    pending.write_text(json.dumps({"execution_ids": list(session.executions)}))
    pending.replace(marker)
    threading.Event().wait(60)
    return report
api.run_graph = blocked_after_provider
with api.AgentHost(registry=builtin_registry(bind=True),
        inputs={key: raw.encode() for key, raw in config["inputs"].items()},
        candidate_domains=config["candidate_domains"], output_dir=Path(config["output_dir"]),
        allow_operations=("statistics.v1",), max_executions=2) as host:
    host.execute("source", "baseline", "interrupted")
""")
    environment = {**os.environ, "PYTHONPATH": str(Path(agent_api.__file__).resolve().parent.parent)}
    child = subprocess.Popen([sys.executable, str(script), str(config), str(marker), environment["PYTHONPATH"]],
                             env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "Child did not reach the post-provider interruption point"
        assert json.loads(marker.read_text())["execution_ids"]
        child.terminate()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.communicate()
    monkeypatch.setattr("ciw.agent_api.run_graph", forbid_dispatch)
    with AgentHost(**settings, resume=True) as host:
        repeated = execute(host, attempt="interrupted")
        assert repeated["status"] == "incomplete" and repeated["execution_outcome"] == "unknown"
        assert repeated["reused_response"] and "execution_ids" not in repeated
        assert host.capabilities()["execution_budget"]["used"] == 1
