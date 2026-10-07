"""Host profile pinning, revocation, and setup without provider execution."""
import json
from pathlib import Path

import pytest

from ciw.board_refit_cli import demo, main, open_host, read_profile
from ciw.control_contracts import load
from ciw.operations.runner import seal


def close(host):
    if hasattr(host, "close"):
        host.close()


def test_demo_is_configuration_only(tmp_path, monkeypatch):
    import ciw.control_plane as control
    import ciw.needle as needle
    def forbidden(*args, **kwargs):
        raise AssertionError("Configuration must not execute providers")
    monkeypatch.setattr(control, "run_graph", forbidden)
    monkeypatch.setattr(needle, "execute_needle", forbidden)
    result = demo(tmp_path / "demo")
    assert result["providers_executed"] is False
    assert result["activated"] is False
    profile_path, profile, board, source, grant = read_profile(result["profile"])
    assert grant["board_ref"] == board["record_digest"]
    assert grant["source_evidence_id"] == source["evidence_id"]
    assert profile["investigation_ref"] == "demo:oscillator-board-refit"
    config = load(profile_path.parent / "mcp-config.json")["mcpServers"]["ncp-board-refit"]
    assert config["args"][-1] == str(profile_path)
    host = open_host(profile_path)
    try:
        assert len(host.descriptions()) == 7
    finally:
        close(host)


@pytest.mark.parametrize("name", ["board.json", "source.json"])
def test_changed_input_bytes_refused_before_open(tmp_path, name):
    result = demo(tmp_path / "demo")
    path = Path(result["profile"]).parent / name
    # Semantically equal JSON still changes the pinned byte identity.
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA256"):
        open_host(result["profile"])


def test_removed_grant_refused_after_start_and_on_reconnect(tmp_path):
    result = demo(tmp_path / "demo")
    host = open_host(result["profile"])
    grant_path = Path(result["profile"]).parent / "grant.json"
    grant_path.unlink()
    try:
        with pytest.raises((ValueError, OSError)):
            host.descriptions()
    finally:
        close(host)
    with pytest.raises((ValueError, OSError)):
        open_host(result["profile"])


def test_changed_grant_cannot_expand_live_or_resumed_authority(tmp_path):
    result = demo(tmp_path / "demo")
    host = open_host(result["profile"])
    grant_path = Path(result["profile"]).parent / "grant.json"
    grant = load(grant_path)
    grant["max_candidate_runs"] += 1
    grant_path.write_text(json.dumps(grant), encoding="utf-8")
    try:
        with pytest.raises((ValueError, PermissionError)):
            host.descriptions()
    finally:
        close(host)
    with pytest.raises(ValueError):
        open_host(result["profile"])


def test_profile_seal_cannot_hide_changed_investigation_on_resume(tmp_path):
    result = demo(tmp_path / "demo")
    path = Path(result["profile"])
    profile = load(path)
    profile["investigation_ref"] = "different-investigation"
    path.write_text(json.dumps(seal(profile)), encoding="utf-8")
    with pytest.raises(ValueError):
        open_host(path)


def test_configure_uses_an_existing_grant_without_regranting(tmp_path, capsys):
    original = demo(tmp_path / "original")
    base = Path(original["profile"]).parent
    before = (base / "grant.json").read_bytes()
    assert main(["configure", "--board", str(base / "board.json"), "--source", str(base / "source.json"),
                 "--grant", str(base / "grant.json"), "--output-dir", str(tmp_path / "configured"),
                 "--investigation-ref", "investigation:existing-grant"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["providers_executed"] is False
    assert (base / "grant.json").read_bytes() == before
    profile = load(tmp_path / "configured" / "profile.json")
    assert profile["grant_path"] == str(base / "grant.json")


def test_setup_is_create_only(tmp_path):
    demo(tmp_path / "demo")
    with pytest.raises(FileExistsError):
        demo(tmp_path / "demo")


def test_installed_client_launcher_is_isolated(tmp_path, monkeypatch):
    from ciw import board_refit_cli as launcher
    prefix = tmp_path / 'environment'
    monkeypatch.setattr(launcher.sys, 'prefix', str(prefix))
    monkeypatch.setattr(launcher, '__file__', str(prefix / 'Lib' / 'site-packages' / 'ciw' / 'board_refit_cli.py'))
    config = launcher._client_config(tmp_path/'profile.json')['mcpServers']['ncp-board-refit']
    assert config['args'][:2] == ['-I', '-m']
    assert 'PYTHONPATH' not in config.get('env', {})
