"""The corpus installed gate must never accidentally exercise the source checkout."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("corpus_mcp_checker", Path(__file__).parents[1] / "scripts/check_corpus_refresh_mcp.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
RECEIPT = {"catalog_ref": "sha256:" + "a" * 64, "evidence_ref": "sha256:" + "b" * 64}


def test_source_launch_preserves_explicit_checkout_binding(tmp_path):
    config, runtime = checker.server_configuration(tmp_path, RECEIPT)
    assert config["args"][:2] == ["-m", "ciw.knowledge_cli"]
    assert Path(config["env"]["PYTHONPATH"]) == Path(checker.__file__).resolve().parents[1] / "src"
    assert runtime["mode"] == "source"


def test_installed_gate_refuses_nonisolated_python(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "sys", SimpleNamespace(executable="python", flags=SimpleNamespace(isolated=0)))
    with pytest.raises(ValueError, match="python -I"):
        checker.server_configuration(tmp_path, RECEIPT, installed=True)


@pytest.mark.parametrize("contaminate", [False, True])
def test_installed_launch_uses_isolated_probe_and_no_pythonpath(tmp_path, monkeypatch, contaminate):
    names = ("ciw", "ciw.proto_corpus", "ciw.evidence_corpus", "ciw.knowledge_cli", "ciw.knowledge_mcp")
    paths = {}
    for name in names:
        path = tmp_path / (name + ".py")
        path.write_text("# synthetic module path only\n")
        paths[name] = str(path)
    monkeypatch.setattr(checker, "ciw", SimpleNamespace(__file__=paths["ciw"]))
    monkeypatch.setattr(checker, "sys", SimpleNamespace(executable="isolated-python", flags=SimpleNamespace(isolated=1)))
    def probe(command, **kwargs):
        assert command[:3] == ["isolated-python", "-I", "-c"]
        assert kwargs["env"] == {} and kwargs["check"] is True and kwargs["timeout"] == 20
        if contaminate:
            paths["ciw.knowledge_mcp"] = str(Path(checker.__file__).resolve().parents[1] / "src/ciw/knowledge_mcp.py")
        return SimpleNamespace(stdout=json.dumps(paths))
    monkeypatch.setattr(checker.subprocess, "run", probe)
    if contaminate:
        with pytest.raises(ValueError, match="checkout code"):
            checker.server_configuration(tmp_path, RECEIPT, installed=True)
    else:
        config, runtime = checker.server_configuration(tmp_path, RECEIPT, installed=True)
        assert config["args"][:3] == ["-I", "-m", "ciw.knowledge_cli"]
        assert config["env"] == {}
        assert runtime["isolated_imports"] == paths
        assert runtime["isolated_import_probe_processes"] == 1


def test_installed_client_source_import_refused_before_probe(tmp_path, monkeypatch):
    path = Path(checker.__file__).resolve().parents[1] / "src/ciw/__init__.py"
    monkeypatch.setattr(checker, "ciw", SimpleNamespace(__file__=str(path)))
    monkeypatch.setattr(checker, "sys", SimpleNamespace(executable="python", flags=SimpleNamespace(isolated=1)))
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **k: pytest.fail("source import reached probe"))
    with pytest.raises(ValueError, match="checkout code"):
        checker.server_configuration(tmp_path, RECEIPT, installed=True)
