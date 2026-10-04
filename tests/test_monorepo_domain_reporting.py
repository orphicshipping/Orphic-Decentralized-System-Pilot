"""A failed startup cannot leave a previous successful gate report in place."""
from pathlib import Path
import importlib.util
import json
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["web", "flowstate", "operations"])
def test_git_startup_failure_replaces_previous_success(name, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("reporting_" + name,
        ROOT / "scripts" / ("check_monorepo_" + name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"status": "passed", "verification_id": "old-success"}))

    def unavailable(*args):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr(module, "_git", unavailable)
    assert module.main(["--output-dir", str(tmp_path)]) == 1
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["verification_id"] != "old-success"
    assert report["terminal_revision"] is None
    assert report["error"]["type"] == "CalledProcessError"
    assert "128" in report["error"]["message"]


@pytest.mark.parametrize("captured", [b"completed test before timeout\n", "completed test before timeout\n", None])
def test_flowstate_timeout_retains_partial_output_and_original_failure(monkeypatch, tmp_path, captured):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("flowstate_timeout_reporting",
        ROOT / "scripts/check_monorepo_flowstate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    failure = subprocess.TimeoutExpired(["fixture", "run"], 7, output=captured)
    log = tmp_path / "commands.log"

    def expired(command, **kwargs):
        # Operators can see the active command before it completes.
        assert b"$ fixture run" in log.read_bytes()
        raise failure

    monkeypatch.setattr(module.subprocess, "run", expired)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        module._run(["fixture", "run"], cwd=tmp_path, log=log, timeout=7)
    assert caught.value is failure
    expected = captured.encode("utf-8") if isinstance(captured, str) else captured
    assert log.read_bytes() == b"\n$ fixture run\n" + (expected or b"")


@pytest.fixture
def flowstate_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("flowstate_budget_reporting",
        ROOT / "scripts/check_monorepo_flowstate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", ["0", "-1", "7201", "1.5", "invalid"])
def test_flowstate_cli_refuses_invalid_suite_timeout(flowstate_gate, tmp_path, value):
    with pytest.raises(SystemExit) as caught:
        flowstate_gate.main(["--suite-timeout", value, "--output-dir", str(tmp_path)])
    assert caught.value.code == 2
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize("arguments,expected", [([], 2700), (["--suite-timeout", "5400"], 5400),
                                             (["--suite-timeout", "1"], 1), (["--suite-timeout", "7200"], 7200)])
def test_flowstate_selected_suite_budget_is_forwarded_and_recorded(flowstate_gate, monkeypatch, tmp_path, arguments, expected):
    module = flowstate_gate
    monkeypatch.setattr(module, "_git", lambda *args: "a" * 40)
    monkeypatch.setattr(module, "verify_terminal_source", lambda **kwargs:
                        {"revision": "a" * 40, "source_tree": "b" * 40})
    observed = []

    def qualify(args, report, output, log):
        observed.append(args.suite_timeout)
        report["source_tests"] = {"passed": 1, "skipped": 3}

    monkeypatch.setattr(module, "_qualify", qualify)
    assert module.main([*arguments, "--output-dir", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["suite_timeout_seconds"] == expected
    assert observed == [expected]


def test_flowstate_budget_reaches_original_default_suite_without_changing_exclusions(flowstate_gate, monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET
    module = flowstate_gate
    observed = []
    python = tmp_path / "python"
    source = tmp_path / "source"
    log = tmp_path / "commands.log"
    junit = tmp_path / "flowstate-default-tests.xml"

    def execute(command, **kwargs):
        observed.append((command, kwargs))
        suite = ET.Element("testsuite", tests="4", failures="0", errors="0", skipped="3")
        ET.SubElement(suite, "testcase", classname="test_unit", name="test_unit")
        for name, test in sorted(module.DEFAULT_DAF_SKIPS):
            case = ET.SubElement(suite, "testcase", classname=name, name=test)
            ET.SubElement(case, "skipped", message=module.DAF_SKIP_REASON)
        ET.ElementTree(suite).write(junit, encoding="utf-8")
        return "fixture result"

    monkeypatch.setattr(module, "_run", execute)
    result = module._default_suite(python, source, tmp_path, log, timeout=5400)
    assert observed == [([python, "-I", "-m", "pytest", "-q", "-rs", "--junitxml", junit],
                         {"cwd": source, "log": log, "timeout": 5400})]
    assert result["passed"] == 1 and result["skipped"] == 3
