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
