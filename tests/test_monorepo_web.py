"""The web gate must launch the same Node/npm pair without a command shell."""
from pathlib import Path
import importlib.util
import os
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def web(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("web_gate_test", ROOT / "scripts/check_monorepo_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def node_installation(tmp_path):
    installation = tmp_path / "Node installation"
    installation.mkdir()
    for name in ("node.exe", "npm.cmd"):
        path = installation / name
        path.write_text("fixture")
        path.chmod(0o755)
    entry = installation / "node_modules/npm/bin/npm-cli.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fixture")
    return installation


def test_windows_npm_uses_selected_node_and_literal_arguments(web, monkeypatch, tmp_path, node_installation):
    installation = node_installation
    entry = installation / "node_modules/npm/bin/npm-cli.js"
    node = installation / "node.exe"
    monkeypatch.setattr(web, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("PATH", str(installation))
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"10.9.4\n")

    monkeypatch.setattr(web.subprocess, "run", execute)
    output_argument = str(tmp_path / "literal & output" / "report.json")
    assert web._run(["npm", "run", "test", "--", "--outputFile=" + output_argument],
                    cwd=tmp_path, temporary=tmp_path, log=tmp_path / "commands.log") == "10.9.4\n"
    command, kwargs = calls[0]
    assert command == [str(node), str(entry), "run", "test", "--", "--outputFile=" + output_argument]
    assert kwargs.get("shell", False) is False
    assert kwargs["env"]["npm_config_userconfig"] == str(tmp_path / "empty-npmrc")


@pytest.mark.parametrize("missing", ["node.exe", "npm.cmd", "node_modules/npm/bin/npm-cli.js"])
def test_windows_npm_missing_installation_is_actionable(web, monkeypatch, tmp_path, node_installation, missing):
    (node_installation / missing).unlink()
    monkeypatch.setattr(web, "sys", SimpleNamespace(platform="win32"))
    with pytest.raises((FileNotFoundError, RuntimeError), match="requires Node.js and npm|Cannot find npm"):
        web._command(["npm", "--version"], cwd=tmp_path, environment={"PATH": str(node_installation)})


def test_windows_tool_selection_ignores_caller_and_child_current_directories(web, monkeypatch, tmp_path, node_installation):
    caller, child = tmp_path / "caller", tmp_path / "child"
    for directory in (caller, child):
        directory.mkdir()
        for name in ("node.exe", "npm.cmd"):
            path = directory / name
            path.write_text("wrong current-directory tool")
            path.chmod(0o755)
    monkeypatch.chdir(caller)
    monkeypatch.setattr(web, "sys", SimpleNamespace(platform="win32"))
    environment = {"PATH": str(node_installation)}
    node = str(node_installation / "node.exe")
    assert web._command(["node", "--version"], cwd=child, environment=environment) == [node, "--version"]
    command = web._command(["npm", "--version"], cwd=child, environment=environment)
    assert command[:2] == [node, str(node_installation / "node_modules/npm/bin/npm-cli.js")]


@pytest.mark.parametrize("prefix", ["", "relative-tools"])
def test_windows_tool_selection_refuses_cwd_dependent_path(web, monkeypatch, tmp_path, node_installation, prefix):
    monkeypatch.setattr(web, "sys", SimpleNamespace(platform="win32"))
    environment = {"PATH": prefix + os.pathsep + str(node_installation)}
    with pytest.raises(RuntimeError, match="absolute, nonempty PATH"):
        web._command(["node", "--version"], cwd=tmp_path, environment=environment)


def test_posix_launch_preserves_original_contract(web, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "sys", SimpleNamespace(platform="linux"))
    assert web._command(["npm", "--version"], cwd=tmp_path, environment={}) == ["npm", "--version"]


@pytest.mark.parametrize("captured", ["completed ρ test\n".encode("utf-8"), "completed ρ test\n", None])
def test_web_timeout_preserves_partial_output_and_original_failure(web, monkeypatch, tmp_path, captured):
    from subprocess import TimeoutExpired

    failure = TimeoutExpired(["fixture", "run"], 7, output=captured)
    log = tmp_path / "commands.log"
    previous = b"previous completed command\n"
    log.write_bytes(previous)

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 7
        assert log.read_bytes() == previous + b"\n$ fixture run\n"
        raise failure

    monkeypatch.setattr(web.subprocess, "run", timeout)
    with pytest.raises(TimeoutExpired) as caught:
        web._run(["fixture", "run"], cwd=tmp_path, temporary=tmp_path, log=log, timeout=7)
    assert caught.value is failure
    expected = captured.encode("utf-8") if isinstance(captured, str) else captured
    assert log.read_bytes() == previous + b"\n$ fixture run\n" + (expected or b"")
