"""Source/attempt identity, fail-closed summaries and bounded real command checks."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("private_woodpecker", ROOT / "scripts/run_private_woodpecker.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
IDENTITY = {"commit": "a" * 40, "tree": "b" * 40, "clean": True}


def environment():
    return {"CI_REPO": ci.REPOSITORY, "CI_COMMIT_SHA": IDENTITY["commit"], "CI_PIPELINE_EVENT": "manual"}


@pytest.mark.parametrize("key,value", [("CI_REPO", "other/repo"), ("CI_COMMIT_SHA", "c" * 40),
                                     ("CI_PIPELINE_EVENT", "pull_request"), ("CI_PIPELINE_EVENT", "deployment")])
def test_identity_and_event_refuse(key, value):
    env = environment(); env[key] = value
    with pytest.raises(ValueError): ci.metadata(env, IDENTITY)


def test_metadata_remains_asserted_and_does_not_collect_secrets():
    env = {**environment(), "WOODPECKER_AGENT_SECRET": "synthetic-never-export", "GITHUB_TOKEN": "synthetic"}
    value = ci.metadata(env, IDENTITY)
    assert "SECRET" not in json.dumps(value) and "TOKEN" not in json.dumps(value)
    assert value["CI_COMMIT_SHA"] == IDENTITY["commit"]


def test_output_and_cache_must_be_distinct_absolute_paths_outside_source(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    cache = tmp_path / "cache"; cache.mkdir()
    valid = {"ODSP_CI_OUTPUT": str(tmp_path / "attempt"), "ODSP_CI_CACHE": str(cache)}
    assert ci.settings(valid, root) == (tmp_path / "attempt", cache)
    for output in ("relative", str(root / "output"), str(cache / "output"), str(tmp_path)):
        with pytest.raises(ValueError): ci.settings({**valid, "ODSP_CI_OUTPUT": output}, root)
    link = tmp_path / "link"; link.symlink_to(cache, target_is_directory=True)
    with pytest.raises(ValueError): ci.settings({**valid, "ODSP_CI_CACHE": str(link)}, root)


def completed(output):
    for name in ("preflight", *ci.PHASES):
        ci.write_new(output / (name + ".json"), {"status": "PASS", "source": IDENTITY,
                                                "artifacts": {"controlled": "unit-fixture"}})
    for name in ci.COMMANDS:
        path = output / "commands" / name; path.mkdir(parents=True)
        (path / "command.log").write_bytes(b"synthetic complete command\n")
        ci.write_new(path / "command.json", {"status": "PASS", "returncode": 0,
            "log_sha256": hashlib.sha256((path / "command.log").read_bytes()).hexdigest()})


def test_complete_local_summary_keeps_external_gates_open(tmp_path, monkeypatch):
    # Isolate summary aggregation; full result validation is exercised below and
    # by the actual installed Woodpecker pipeline, not by invented wheel bytes.
    monkeypatch.setattr(ci, "result_artifacts", lambda *args: {"controlled": "unit-fixture"})
    completed(tmp_path)
    result = ci.summarize(tmp_path, IDENTITY)
    assert result["status"] == "LOCAL_PIPELINE_PASS"
    assert result["docker_gate"].startswith("NOT_EXECUTED")
    assert result["hosted_woodpecker"] == "NOT_CONFIGURED"
    assert result["github_release_collector"] == "NOT_SATISFIED"
    assert result["deployment"] == "not_performed"
    with pytest.raises(FileExistsError): ci.summarize(tmp_path, IDENTITY)


@pytest.mark.parametrize("mutation", ["missing-phase", "failed-phase", "wrong-source", "missing-command", "bad-log"])
def test_incomplete_or_changed_evidence_cannot_pass(tmp_path, mutation):
    completed(tmp_path)
    target = tmp_path / "qualify.json"
    if mutation == "missing-phase": target.unlink()
    elif mutation == "failed-phase": target.write_text(json.dumps({"status": "FAIL", "source": IDENTITY}))
    elif mutation == "wrong-source": target.write_text(json.dumps({"status": "PASS", "source": {**IDENTITY, "commit": "c" * 40}}))
    elif mutation == "missing-command": (tmp_path / "commands/qualify/command.json").unlink()
    else: (tmp_path / "commands/qualify/command.log").write_bytes(b"changed")
    with pytest.raises(ValueError): ci.summarize(tmp_path, IDENTITY)
    assert json.loads((tmp_path / "summary.json").read_text())["status"] == "FAIL"


def test_success_markers_without_nested_results_cannot_pass(tmp_path):
    completed(tmp_path)
    with pytest.raises(ValueError): ci.summarize(tmp_path, IDENTITY)
    result = json.loads((tmp_path / "summary.json").read_text())
    assert result["status"] == "FAIL" and set(result["result_errors"]) == set(ci.PHASES)


def test_contract_result_revalidation_rejects_later_skip(tmp_path):
    path = tmp_path / "contracts.xml"
    path.write_text('<testsuites><testsuite tests="1"/></testsuites>')
    initial = ci.result_artifacts("contracts", tmp_path, IDENTITY)
    assert "contracts.xml" in initial
    path.write_text('<testsuites><testsuite tests="1" skipped="1"/></testsuites>')
    with pytest.raises(ValueError): ci.result_artifacts("contracts", tmp_path, IDENTITY)


def test_real_failing_command_retains_nonzero_receipt(tmp_path):
    with pytest.raises(ValueError, match="command failed"):
        ci.run_checked([sys.executable, "-B", "-c", "print('controlled failure'); raise SystemExit(7)"],
                       tmp_path, "failure", timeout=10, env=ci.command_env(ROOT, tmp_path))
    receipt = json.loads((tmp_path / "commands/failure/command.json").read_text())
    assert receipt["status"] == "FAIL" and receipt["returncode"] == 7
    assert "controlled failure" in (tmp_path / "commands/failure/command.log").read_text()
    with pytest.raises(ValueError, match="already exists"):
        ci.run_checked([sys.executable, "-c", "raise SystemExit(0)"], tmp_path, "failure", timeout=10, env={})


def test_junit_skip_and_failure_remain_failures(tmp_path):
    path = tmp_path / "tests.xml"
    for attrs in ('tests="0"', 'tests="1" skipped="1"', 'tests="1" failures="1"', 'tests="1" errors="1"'):
        path.write_text('<testsuites><testsuite ' + attrs + '/></testsuites>')
        with pytest.raises(ValueError): ci.junit(path)


def test_checked_workflow_is_manual_sequential_and_has_no_privileged_inputs():
    workflow = yaml.safe_load((ROOT / ".woodpecker/private-qualification.yaml").read_text())
    assert workflow["when"] == [{"event": "manual"}]
    assert workflow["labels"] == {"backend": "local", "profile": "odsp-private-disposable"}
    steps = workflow["steps"]
    assert len(steps) == 7
    for step, name in zip(steps, ("preflight", *ci.PHASES, "summarize"), strict=True):
        assert set(step) <= {"name", "image", "commands", "when"}
        assert step["image"] == "bash"
        assert step["commands"] == ["python3 -B scripts/run_private_woodpecker.py " + name]
    assert steps[-1]["when"] == [{"status": ["success", "failure"]}]
    assert all("when" not in step for step in steps[:-1])
