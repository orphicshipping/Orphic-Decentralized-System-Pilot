"""Real built-in examples and fail-closed evidence-runner regressions."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import ciw
import pytest


SPEC = importlib.util.spec_from_file_location(
    "domain_examples", Path(__file__).resolve().parents[1] / "scripts/check_domain_examples.py")
examples = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(examples)


@pytest.mark.parametrize("name", examples.EXAMPLES)
def test_public_builtin_example_really_runs(name, tmp_path):
    if name == "measured-thermal":
        shutil.copyfile(examples.ROOT / "examples/measured-thermal/source.json", tmp_path / "source.json")
    result = examples.run_example(name, tmp_path, Path(ciw.__file__).resolve().parent)
    assert result["scope"]
    assert any(tmp_path.iterdir())


def test_existing_output_directory_is_never_overwritten(tmp_path):
    marker = tmp_path / "report.json"
    marker.write_text("keep existing evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        examples.qualify(tmp_path / "unused.whl", tmp_path, "a" * 40)
    assert marker.read_text(encoding="utf-8") == "keep existing evidence"


def test_binding_failure_retains_failed_report(tmp_path, monkeypatch):
    def missing(_wheel):
        raise ValueError("Candidate wheel was not installed")
    monkeypatch.setattr(examples, "installed_binding", missing)
    report = examples.qualify(tmp_path / "unused.whl", tmp_path / "out", "a" * 40)
    assert report["status"] == "FAIL"
    assert report["message"] == "Candidate wheel was not installed"
    assert all(row["status"] == "NOT_RUN" for row in report["examples"])
    assert json.loads((tmp_path / "out/report.json").read_text()) == report


def test_timeout_fails_each_example_without_silently_skipping(tmp_path, monkeypatch):
    monkeypatch.setattr(examples, "installed_binding", lambda _: {"installed_package": str(tmp_path)})
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(examples.subprocess, "run", timeout)
    report = examples.qualify(tmp_path / "unused.whl", tmp_path / "out", "a" * 40, timeout_s=1)
    assert report["status"] == "FAIL"
    assert [row["name"] for row in report["examples"]] == list(examples.EXAMPLES)
    assert all(row["status"] == "FAIL" and row["error_type"] == "TimeoutExpired" for row in report["examples"])
    assert all(row["elapsed_s"] >= 0 for row in report["examples"])


@pytest.mark.parametrize("revision, timeout", [("short-sha", 60), ("a" * 40, 0), ("a" * 40, 121)])
def test_invalid_provenance_or_budget_refused_before_output(tmp_path, revision, timeout):
    destination = tmp_path / "out"
    with pytest.raises(ValueError):
        examples.qualify(tmp_path / "unused.whl", destination, revision, timeout)
    assert not destination.exists()


def test_total_budget_exhaustion_is_retained_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(examples, "installed_binding", lambda _: {"installed_package": str(tmp_path)})
    ticks = iter([0.0] + [600.0] * 100)
    monkeypatch.setattr(examples.time, "perf_counter", lambda: next(ticks))
    def denied(*args, **kwargs):
        raise AssertionError("No process may start after the suite deadline")
    monkeypatch.setattr(examples.subprocess, "run", denied)
    report = examples.qualify(tmp_path / "unused.whl", tmp_path / "out", "a" * 40)
    assert report["status"] == "FAIL"
    assert all(row["status"] == "FAIL" and "suite budget was exhausted" in row["message"]
               for row in report["examples"])
