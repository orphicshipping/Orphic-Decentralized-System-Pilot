"""Installed qualification must select its interpreter's launcher on each layout."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "installed_command", Path(__file__).resolve().parents[1] / "scripts/installed_command.py")
commands = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(commands)


@pytest.mark.parametrize("platform,relative,launcher", [
    ("nt", "base/Scripts", "net.exe"),
    ("nt", "venv/Scripts", "net.exe"),
    ("posix", "venv/bin", "net"),
])
def test_launcher_comes_from_active_installation_not_interpreter_parent_or_path(
    tmp_path, monkeypatch, platform, relative, launcher,
):
    scripts = tmp_path / relative
    scripts.mkdir(parents=True)
    expected = scripts / launcher
    expected.write_bytes(b"candidate-launcher")
    foreign = tmp_path / "other-installation"
    foreign.mkdir()
    (foreign / launcher).write_bytes(b"foreign-launcher")
    monkeypatch.setenv("PATH", str(foreign))
    monkeypatch.setattr(commands, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(commands.sysconfig, "get_path", lambda key: str(scripts))
    assert commands.installed_net_command() == expected
    expected.unlink()
    with pytest.raises(ValueError, match="Missing installed native NET command"):
        commands.installed_net_command()
