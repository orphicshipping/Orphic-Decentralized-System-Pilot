"""Source/attempt identity, fail-closed summaries and bounded real command checks."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
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
    assert result["nise_qualification"]["status"] == "NOT_REQUESTED"
    assert result["nise_qualification"]["installed_tests"] == []
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
    with pytest.raises(FileNotFoundError): ci.junit(tmp_path / "missing.xml")


def test_node_phase_emits_parseable_test_totals(tmp_path, monkeypatch):
    test_file = tmp_path / "controlled.test.mjs"
    test_file.write_text("import test from 'node:test'; test('controlled test', () => {});\n")
    output = tmp_path / "attempt"; output.mkdir()
    ci.write_new(output / "preflight.json", {"status": "PASS"})
    for earlier in ci.PHASES[:-1]:
        ci.write_new(output / (earlier + ".json"), {"status": "PASS"})
    monkeypatch.setattr(ci, "NODE_TESTS", (str(test_file),))
    monkeypatch.setattr(ci, "source_identity", lambda root: IDENTITY)
    ci.phase("node", output, tmp_path, IDENTITY)
    result = json.loads((output / "node.json").read_text())
    assert result["status"] == "PASS"
    assert result["tests"]["tests"] == result["tests"]["pass"] == 1
    assert result["tests"]["fail"] == result["tests"]["skipped"] == 0


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


def native_environment(tmp_path):
    checkout = tmp_path / "provider"; checkout.mkdir()
    python = tmp_path / "provider-python"
    python.write_text("#!/bin/sh\nexit 0\n"); python.chmod(0o700)
    return {"ODSP_CI_NISE_MODE": "native", "CIW_NISE_CHECKOUT": str(checkout),
            "CIW_NISE_PYTHON": str(python)}


@pytest.mark.parametrize("mode", ["", "automatic", "NATIVE", "true", None, 1, {}])
def test_unknown_native_selector_refuses(mode):
    with pytest.raises(ValueError, match="contracts or native"):
        ci.nise_selection({"ODSP_CI_NISE_MODE": mode})


def test_contract_mode_does_not_bind_or_pass_incidental_provider_environment(tmp_path, monkeypatch):
    for key in ("CIW_NISE_CHECKOUT", "CIW_NISE_PYTHON"):
        monkeypatch.setenv(key, "/unavailable/host-binding")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-not-exported")
    selected = ci.nise_selection({
        "CIW_NISE_CHECKOUT": "/unavailable/checkout", "CIW_NISE_PYTHON": "/unavailable/python"})
    assert selected == {"mode": "contracts", "bindings": {}}
    env = ci.command_env(ROOT, tmp_path, selected)
    assert "CIW_NISE_CHECKOUT" not in env and "CIW_NISE_PYTHON" not in env
    assert "GITHUB_TOKEN" not in env


def test_native_host_bindings_pass_only_when_explicitly_selected(tmp_path, monkeypatch):
    host = native_environment(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-not-exported")
    selected = ci.nise_selection(host)
    env = ci.command_env(ROOT, tmp_path, selected)
    assert {key: env[key] for key in selected["bindings"]} == selected["bindings"]
    assert env["CIW_NISE_CHECKOUT"] == host["CIW_NISE_CHECKOUT"]
    assert env["CIW_NISE_PYTHON"] == host["CIW_NISE_PYTHON"]
    assert "GITHUB_TOKEN" not in env


@pytest.mark.parametrize("missing", ["CIW_NISE_CHECKOUT", "CIW_NISE_PYTHON"])
def test_native_requires_every_host_binding(tmp_path, missing):
    host = native_environment(tmp_path)
    del host[missing]
    with pytest.raises(ValueError, match=missing): ci.nise_selection(host)


def test_missing_native_binding_refuses_preflight_before_attempt_creation(tmp_path, monkeypatch, capsys):
    output = tmp_path / "attempt"; cache = tmp_path / "cache"; cache.mkdir()
    monkeypatch.setattr(sys, "argv", ["run_private_woodpecker.py", "preflight"])
    monkeypatch.setattr(ci, "source_identity", lambda root: IDENTITY)
    for key, value in {**environment(), "ODSP_CI_NISE_MODE": "native",
                       "ODSP_CI_OUTPUT": str(output), "ODSP_CI_CACHE": str(cache)}.items():
        monkeypatch.setenv(key, value)
    for key in ("CIW_NISE_CHECKOUT", "CIW_NISE_PYTHON"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ci, "preflight", lambda *a, **k: pytest.fail("missing binding reached preflight"))
    assert ci.main() == 1
    assert "CIW_NISE_CHECKOUT" in capsys.readouterr().out
    assert not output.exists()


def test_source_and_installed_test_selectors_are_exact():
    contract = (
        "tests/test_private_release.py", "scripts/tests/test_private_release_build.py",
        "scripts/tests/test_private_woodpecker.py", "tests/test_annotations.py",
        "tests/test_annotation_nise.py", "tests/test_control_annotations.py",
        "tests/test_nise_contract.py", "tests/test_nise_handoff.py",
        "tests/test_semantic_capabilities.py",
    )
    assert ci.selected_contract_tests({"mode": "contracts"}) == contract
    assert ci.selected_contract_tests({"mode": "native"}) == contract + (
        "tests/test_nise_profile.py", "tests/test_nise_policy.py")
    assert ci.INSTALLED_NISE_TESTS == (
        "tests/test_nise_contract.py", "tests/test_nise_profile.py", "tests/test_nise_policy.py",
        "tests/test_control_annotations.py")
    assert ci.selected_commands({"mode": "contracts"}) == ci.COMMANDS
    assert ci.selected_commands({"mode": "native"}) == ci.COMMANDS + ("installed-native",)


def test_attempt_refuses_changed_mode_or_binding(tmp_path):
    selected = ci.nise_selection(native_environment(tmp_path))
    asserted = ci.metadata(environment(), IDENTITY)
    ci.write_new(tmp_path / "attempt.json", {
        "source": IDENTITY, "runner_asserted_metadata": asserted,
        "python": str(Path(sys.executable).absolute()), "nise_qualification": selected,
    })
    assert ci.load_attempt(tmp_path, IDENTITY, asserted, selected)["nise_qualification"] == selected
    with pytest.raises(ValueError, match="selector or binding"):
        ci.load_attempt(tmp_path, IDENTITY, asserted, ci.nise_selection({}))
    changed = {"mode": "native", "bindings": {**selected["bindings"], "CIW_NISE_PYTHON": "/other/python"}}
    with pytest.raises(ValueError, match="selector or binding"):
        ci.load_attempt(tmp_path, IDENTITY, asserted, changed)


def stage_native_fixtures(output, monkeypatch):
    observed = {}
    def capture(argv, destination, name, *, timeout, env, cwd=None):
        observed.update(argv=argv, destination=destination, name=name, timeout=timeout, env=env, cwd=cwd)
        (output / "installed-native.xml").write_text(
            '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>')
        return {"status": "PASS"}
    monkeypatch.setattr(ci, "run_checked", capture)
    ci.installed_native(output, output / "installed",
                        {"PYTHONPATH": str(ROOT / "src"), "CIW_NISE_CHECKOUT": "/selected/provider",
                         "CIW_NISE_PYTHON": "/selected/python", "PYTHONDONTWRITEBYTECODE": "1"})
    return observed


def test_installed_native_uses_external_hashed_fixtures_and_isolated_interpreter(tmp_path, monkeypatch):
    observed = stage_native_fixtures(tmp_path, monkeypatch)
    assert observed["name"] == "installed-native" and observed["timeout"] == 180
    assert observed["argv"][:4] == [str(tmp_path / "installed/bin/python"), "-I", "-B", "-c"]
    assert observed["cwd"] == tmp_path / "installed-native-fixtures"
    assert not observed["cwd"].is_relative_to(ROOT)
    assert "PYTHONPATH" not in observed["env"]
    assert observed["env"]["CIW_NISE_CHECKOUT"] == "/selected/provider"
    assert observed["env"]["CIW_NISE_PYTHON"] == "/selected/python"
    manifest = json.loads((tmp_path / "installed-native-fixtures.json").read_text())
    assert set(manifest["files"]) == set(ci.INSTALLED_NISE_TESTS)
    assert {p.name for p in observed["cwd"].iterdir()} == {Path(p).name for p in ci.INSTALLED_NISE_TESTS}
    assert len(ci.installed_native_files(tmp_path)) == 2 + len(ci.INSTALLED_NISE_TESTS)
    # Exercise the actual emitted guard; pytest must not start for a source overlay.
    foreign = "import sys,types; m=types.ModuleType('ciw'); m.__file__='/outside-prefix/ciw/__init__.py'; sys.modules['ciw']=m; "
    result = subprocess.run([sys.executable, "-I", "-B", "-c", foreign + observed["argv"][-1]],
                            cwd=observed["cwd"], text=True, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert "ciw is not from the installed environment" in result.stderr
    assert "installed_ciw_origin" not in result.stdout


@pytest.mark.parametrize("mutation", ["missing-xml", "skip", "changed-copy", "missing-copy", "changed-manifest"])
def test_installed_native_revalidation_refuses_incomplete_or_changed_evidence(tmp_path, monkeypatch, mutation):
    stage_native_fixtures(tmp_path, monkeypatch)
    copied = tmp_path / "installed-native-fixtures" / Path(ci.INSTALLED_NISE_TESTS[0]).name
    if mutation == "missing-xml": (tmp_path / "installed-native.xml").unlink()
    elif mutation == "skip": (tmp_path / "installed-native.xml").write_text('<testsuite tests="1" skipped="1"/>')
    elif mutation == "changed-copy": copied.write_text("changed")
    elif mutation == "missing-copy": copied.unlink()
    else: (tmp_path / "installed-native-fixtures.json").write_text('{"schema":"wrong","files":{}}')
    with pytest.raises((ValueError, OSError)): ci.installed_native_files(tmp_path)


def test_missing_installed_native_command_cannot_pass_summary(tmp_path, monkeypatch):
    completed(tmp_path)
    monkeypatch.setattr(ci, "result_artifacts", lambda *args: {"controlled": "unit-fixture"})
    with pytest.raises(ValueError): ci.summarize(tmp_path, IDENTITY, {"mode": "native", "bindings": {}})
    result = json.loads((tmp_path / "summary.json").read_text())
    assert result["status"] == result["nise_qualification"]["status"] == "FAIL"
    assert result["commands"]["installed-native"]["status"] == "MISSING"


def test_annotation_loop_keeps_exact_provider_in_a_separate_clean_build_environment():
    workflow = yaml.load((ROOT / ".github/workflows/annotation-nise-loop.yml").read_text(),
                         Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["loop"]["steps"]
    checkout = next(step["with"] for step in steps if step.get("with", {}).get("path") == "external/nise")
    assert checkout == {"repository": "orphicsystems/Notations-Inference-Schematics-Engine",
                        "ref": "81908ddbe817018af5d9887a6bdd9cce3e3eafc6",
                        "path": "external/nise", "persist-credentials": "false"}
    install = next(step["run"] for step in steps if step.get("name") ==
                   "Install NISE in an isolated provider environment")
    assert "'archive', 'HEAD'" in install and "tree.extractall(source, filter='data')" in install
    assert "[str(provider), '-m', 'pip', 'install', '--no-deps', str(source)]" in install
    assert "'status', '--porcelain'" in install
    specimen = next(step["run"] for step in steps if step.get("name") ==
                    "Full human-notation to NET-plan specimen")
    assert '"$NISE_PYTHON" -I -B -m nise.cli compile' in specimen
    assert all("token" not in step.get("with", {}) for step in steps)
