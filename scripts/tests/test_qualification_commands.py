"""Subprocess-lifecycle tests; these do not qualify an installed NET package."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SPEC = importlib.util.spec_from_file_location(
    "qualification_commands", Path(__file__).resolve().parents[1] / "check_qualification_commands.py")
checks = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(checks)


@pytest.fixture
def campaign(tmp_path):
    (tmp_path / "commands").mkdir()
    return tmp_path, {"commands": []}


def launch_script(monkeypatch, script, observed):
    real_launch = asyncio.create_subprocess_exec

    async def launch(*arguments, **kwargs):
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        process = await real_launch(sys.executable, "-I", "-B", "-u", "-c", script, **kwargs)
        observed["process"] = process
        return process

    monkeypatch.setattr(checks.asyncio, "create_subprocess_exec", launch)


def test_completed_command_keeps_exact_isolated_invocation_and_logs(campaign, monkeypatch):
    destination, report = campaign
    observed = {}
    launch_script(monkeypatch, 'import sys; print(\'{"ok": true}\'); print("diagnostic", file=sys.stderr)', observed)
    result = asyncio.run(checks.run_command(destination, report, "success", ["reference-compare", "verify", "report.json"]))
    assert result == {"ok": True}
    receipt = report["commands"][0]
    assert receipt["status"] == "completed" and receipt["exit"] == 0
    assert observed["arguments"] == (sys.executable, "-I", "-B", "-u", "-m", "ciw.net",
                                      "reference-compare", "verify", "report.json")
    assert receipt["command"] == list(observed["arguments"])
    assert observed["kwargs"]["cwd"] == destination
    assert json.loads((destination / receipt["stdout_file"]).read_text()) == result
    assert (destination / receipt["stderr_file"]).read_text().strip() == "diagnostic"


def test_spawn_failure_retains_attempt_before_launch_and_empty_logs(campaign, monkeypatch):
    destination, report = campaign

    async def failed_spawn(*arguments, **kwargs):
        assert len(report["commands"]) == 1
        assert report["commands"][0]["status"] == "attempted"
        assert (destination / "commands/spawn.stdout").exists()
        assert (destination / "commands/spawn.stderr").exists()
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(checks.asyncio, "create_subprocess_exec", failed_spawn)
    with pytest.raises(OSError, match="synthetic launch failure"):
        asyncio.run(checks.run_command(destination, report, "spawn", ["timing", "inspect", "report.json"]))
    receipt = report["commands"][0]
    assert receipt["status"] == "failed" and receipt["exit"] is None
    assert receipt["failure"] == {"phase": "spawn", "type": "OSError", "detail": "synthetic launch failure"}
    assert (destination / receipt["stdout_file"]).read_bytes() == b""
    assert (destination / receipt["stderr_file"]).read_bytes() == b""


def test_timeout_keeps_partial_output_and_kills_and_reaps_child(campaign, monkeypatch):
    destination, report = campaign
    observed = {}
    launch_script(monkeypatch,
                  'import sys,time; print("started", flush=True); print("partial error", file=sys.stderr, flush=True); time.sleep(60)',
                  observed)
    with pytest.raises(TimeoutError, match="timeout: exceeded the 3-second command deadline"):
        asyncio.run(checks.run_command(destination, report, "timeout", ["timing", "run"], timeout_s=3))
    receipt = report["commands"][0]
    assert receipt["status"] == "timeout" and receipt["timeout_s"] == 3
    assert receipt["failure"]["phase"] == "wait"
    assert observed["process"].returncode is not None
    assert receipt["exit"] == observed["process"].returncode
    assert (destination / receipt["stdout_file"]).read_text().strip() == "started"
    assert (destination / receipt["stderr_file"]).read_text().strip() == "partial error"


@pytest.mark.parametrize("script,phase,exit_code,error", [
    ('import sys; print("before failure"); print("reason", file=sys.stderr); sys.exit(7)', "check_exit", 7, ValueError),
    ('print("not JSON")', "decode_output", 0, json.JSONDecodeError),
])
def test_exit_and_output_failures_keep_receipts_and_logs(campaign, monkeypatch, script, phase, exit_code, error):
    destination, report = campaign
    launch_script(monkeypatch, script, {})
    with pytest.raises(error):
        asyncio.run(checks.run_command(destination, report, "failed", ["reference-compare", "demo"]))
    receipt = report["commands"][0]
    assert receipt["status"] == "failed" and receipt["exit"] == exit_code
    assert receipt["failure"]["phase"] == phase
    assert (destination / receipt["stdout_file"]).read_bytes()
    assert (destination / receipt["stderr_file"]).exists()


def test_summary_text_is_retained_without_json_parsing(campaign, monkeypatch):
    destination, report = campaign
    launch_script(monkeypatch, 'print("INDETERMINATE: conditional comparison")', {})
    text = asyncio.run(checks.run_command(destination, report, "summary", ["reference-compare", "verify"], summary=True))
    assert text.startswith("INDETERMINATE:")
    assert report["commands"][0]["status"] == "completed"


def test_existing_log_is_not_replaced_and_command_is_not_launched(campaign, monkeypatch):
    destination, report = campaign
    path = destination / "commands/existing.stdout"
    path.write_bytes(b"preserve")

    async def forbidden(*arguments, **kwargs):
        pytest.fail("Existing logs must refuse before subprocess creation")

    monkeypatch.setattr(checks.asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(FileExistsError):
        asyncio.run(checks.run_command(destination, report, "existing", ["timing", "run"]))
    assert path.read_bytes() == b"preserve"
    assert report["commands"][0]["failure"]["phase"] == "open_logs"
