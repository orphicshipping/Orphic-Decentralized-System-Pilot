"""Failure evidence and text portability for scientific qualification gates."""
from importlib import util
import json
from pathlib import Path
from subprocess import CalledProcessError
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _gate(monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = util.spec_from_file_location("scientific_gate_test_" + name,
                                       ROOT / "scripts" / (name + ".py"))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["check_monorepo", "check_monorepo_inference",
                                 "check_monorepo_math", "check_monorepo_surface"])
def test_initial_revision_failure_replaces_stale_success(monkeypatch, tmp_path, name):
    gate = _gate(monkeypatch, name)
    previous = {"status": "passed", "verification_id": "verification:" + "1" * 32,
                "terminal_revision": "2" * 40}
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(previous), encoding="utf-8")

    def unavailable(*args):
        raise CalledProcessError(128, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr(gate, "_git", unavailable)
    assert gate.main(["--output-dir", str(tmp_path)]) == 1
    current = json.loads(report_path.read_text(encoding="utf-8"))
    assert current["status"] == "failed"
    assert current["verification_id"] != previous["verification_id"]
    assert current["terminal_revision"] is None
    assert current["error"]["type"] == "CalledProcessError"


@pytest.mark.parametrize("name", ["check_monorepo", "check_monorepo_inference",
                                 "check_monorepo_math", "check_monorepo_surface",
                                 "check_monorepo_flowstate", "check_monorepo_operations"])
@pytest.mark.parametrize("isolated", [False, True])
def test_scientific_python_reads_and_emits_utf8(monkeypatch, tmp_path, name, isolated):
    gate = _gate(monkeypatch, name)
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    document = tmp_path / "document.txt"
    content = "\u03c1 \u2014 \u03ba"
    document.write_bytes(content.encode("utf-8"))
    command = [sys.executable, *(["-I"] if isolated else []), "-c",
               "import pathlib, sys; assert sys.flags.utf8_mode == 1; "
               "print(pathlib.Path(sys.argv[1]).read_text())", document]
    runner = getattr(gate, "_run_bound", gate._run)
    actual = runner(command, cwd=tmp_path, log=tmp_path / "commands.log")
    assert actual.strip() == content


def test_measurement_and_inference_provision_windows_timezone_data(monkeypatch):
    from packaging.requirements import Requirement

    measurement = _gate(monkeypatch, "check_monorepo")
    inference = _gate(monkeypatch, "check_monorepo_inference")
    for gate in (measurement, inference):
        timezone, = [Requirement(item) for item in gate.DEPENDENCIES
                     if Requirement(item).name == "tzdata"]
        assert str(timezone.specifier) == "==2026.5"
        assert timezone.marker.evaluate({"sys_platform": "win32"})
        assert not timezone.marker.evaluate({"sys_platform": "linux"})


@pytest.mark.parametrize("command", [["clang", "-I", "include"],
                                     ["python", "-c", "print('ok')", "-I"],
                                     ["git", "-c", "core.fsmonitor=false", "status"]])
def test_utf8_selection_leaves_other_commands_and_caller_arguments_unchanged(monkeypatch, command):
    gate = _gate(monkeypatch, "check_monorepo")
    before = list(command)
    assert gate._utf8_python_command(command) == before
    assert command == before
