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


@pytest.mark.parametrize("name", ["check_monorepo", "check_monorepo_math"])
def test_test_hashes_match_declared_paths_and_detect_byte_mutation(monkeypatch, tmp_path, name):
    from hashlib import sha256

    gate = _gate(monkeypatch, name)
    test_file = tmp_path / "tests" / "nested" / "test_example.py"
    test_file.parent.mkdir(parents=True)
    original = b"def test_example():\n    assert True\n"
    test_file.write_bytes(original)
    expected = {"tests/nested/test_example.py": sha256(original).hexdigest()}
    assert gate._test_hashes(tmp_path) == expected
    test_file.write_bytes(original + b"# changed after execution\n")
    assert set(gate._test_hashes(tmp_path)) == set(expected)
    assert gate._test_hashes(tmp_path) != expected


@pytest.mark.parametrize("captured", ["completed ρ test\n".encode("utf-8"), "completed ρ test\n", None])
def test_surface_timeout_preserves_partial_output_and_original_failure(monkeypatch, tmp_path, captured):
    from subprocess import TimeoutExpired

    gate = _gate(monkeypatch, "check_monorepo_surface")
    failure = TimeoutExpired(["fixture", "run"], 7, output=captured)

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 7
        raise failure

    monkeypatch.setattr(gate.subprocess, "run", timeout)
    log = tmp_path / "commands.log"
    with pytest.raises(TimeoutExpired) as caught:
        gate._run(["fixture", "run"], cwd=tmp_path, log=log, timeout=7)
    assert caught.value is failure
    expected = captured.encode("utf-8") if isinstance(captured, str) else captured
    assert log.read_bytes() == b"\n$ fixture run\n" + (expected or b"")


@pytest.mark.parametrize("value", ["0", "-1", "7201", "1.5", "true", "false", "nan", "inf"])
def test_surface_cli_refuses_invalid_suite_timeout(monkeypatch, tmp_path, value):
    gate = _gate(monkeypatch, "check_monorepo_surface")
    with pytest.raises(SystemExit) as caught:
        gate.main(["--suite-timeout", value, "--output-dir", str(tmp_path)])
    assert caught.value.code == 2
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize("arguments,expected", [([], 3600), (["--suite-timeout", "1"], 1),
                                             (["--suite-timeout", "7200"], 7200)])
def test_surface_records_selected_suite_budget(monkeypatch, tmp_path, arguments, expected):
    gate = _gate(monkeypatch, "check_monorepo_surface")
    revision = "a" * 40
    monkeypatch.setattr(gate, "_git", lambda *args: revision)
    monkeypatch.setattr(gate, "verify_terminal_source", lambda **kwargs: {"revision": revision})
    observed = []

    def qualify(args, report, output, log):
        observed.append(args.suite_timeout)

    monkeypatch.setattr(gate, "qualify", qualify)
    assert gate.main(["--output-dir", str(tmp_path), *arguments]) == 0
    assert observed == [expected]
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["suite_timeout_seconds"] == expected


def test_surface_full_suite_uses_budget_without_changing_selection(monkeypatch, tmp_path):
    from subprocess import TimeoutExpired

    gate = _gate(monkeypatch, "check_monorepo_surface")
    failure = TimeoutExpired(["fixture"], 7200)

    def timeout(command, *, cwd, log, timeout):
        assert command == [sys.executable, "-I", "-m", "pytest", "-q", "-rs", "-o", "pythonpath=",
                           "--import-mode=importlib", "--junitxml", tmp_path / "surface-full-tests.xml", "tests"]
        assert timeout == 7200
        raise failure

    monkeypatch.setattr(gate, "_run", timeout)
    with pytest.raises(TimeoutExpired) as caught:
        gate._full_scientific_suite(sys.executable, tmp_path, tmp_path, tmp_path,
                                    tmp_path / "commands.log", [], timeout=7200)
    assert caught.value is failure


def test_surface_completed_science_retains_its_actual_budget(monkeypatch, tmp_path):
    gate = _gate(monkeypatch, "check_monorepo_surface")
    monkeypatch.setattr(gate, "_run", lambda *args, **kwargs: "")
    monkeypatch.setattr(gate, "_surface_junit", lambda *args: {"tests": 799, "passed": 777, "skipped": 22})
    result = gate._full_scientific_suite(sys.executable, tmp_path, tmp_path, tmp_path,
                                         tmp_path / "commands.log", [], timeout=7200)
    assert result["timeout_seconds"] == 7200
    assert (result["tests"], result["passed"], result["skipped"]) == (799, 777, 22)
