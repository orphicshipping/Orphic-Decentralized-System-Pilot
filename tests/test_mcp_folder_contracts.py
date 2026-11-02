"""Navigation must not change retained source or exact-input capsules."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def generator(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("folder_contract_generator", ROOT / "scripts/write_mcp_folder_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def snapshot(root):
    return {path.relative_to(root).as_posix(): (stat.S_IMODE(path.stat().st_mode), path.read_bytes())
            for path in root.rglob("*") if path.is_file() and ".git" not in path.relative_to(root).parts}


def test_protected_roots_follow_the_validated_import_manifest_and_exact_capsule(generator):
    modules = json.loads((ROOT / "instruments/manifest.json").read_text(encoding="utf-8"))["modules"]
    assert set(generator.protected_roots()) == {row["path"] for row in modules} | {"examples/workcells/godot-prop"}
    from ciw.workcell_recipe import PROP
    capsule = ROOT / "examples/workcells/godot-prop"
    assert set(snapshot(capsule)) == set(PROP.source_paths)


def test_external_navigation_covers_ignored_tracked_folders_without_writing_source(generator, tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp/project-map.json").write_text('{"folders": []}\n')
    source = tmp_path / "instruments/retained"
    (source / "ignored").mkdir(parents=True)
    (source / ".gitignore").write_text("ignored/\n")
    (source / "README.md").write_bytes(b"Upstream README\r\nwithout final newline")
    program = source / "ignored/program.py"
    program.write_bytes(b"# retained source\n")
    program.chmod(0o755)
    capsule = tmp_path / "examples/workcells/godot-prop"
    capsule.mkdir(parents=True)
    for name in ("compiler.py", "motion.gd", "spec.json"):
        (capsule / name).write_text(name)
    git(tmp_path, "add", "-f", ".")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    for name, relative in (("MAP_PATH", "project-map.json"), ("INDEX_PATH", "folder-index.json"), ("OVERLAY_PATH", "source-contracts.md")):
        monkeypatch.setattr(generator, name, tmp_path / "mcp" / relative)
    monkeypatch.setattr(generator, "load_manifest", lambda root: {"modules": [{"path": "instruments/retained"}]})
    original_source, original_capsule = snapshot(source), snapshot(capsule)
    generator.main()
    first = snapshot(tmp_path)
    generator.main()
    assert snapshot(tmp_path) == first
    assert snapshot(source) == original_source
    assert snapshot(capsule) == original_capsule
    index = json.loads(generator.INDEX_PATH.read_text())
    rows = {row["path"]: row for row in index["folders"]}
    assert {"instruments/retained", "instruments/retained/ignored", "examples/workcells/godot-prop"} <= rows.keys()
    overlay = generator.OVERLAY_PATH.read_text()
    for relative in ("instruments/retained", "instruments/retained/ignored", "examples/workcells/godot-prop"):
        assert rows[relative]["contract"] == "mcp/source-contracts.md#" + relative
        assert f'<a id="{relative}"></a>' in overlay
    assert "[project map](../mcp/project-map.json)" in overlay
    assert "[folder index](../mcp/folder-index.json)" in overlay


@pytest.mark.parametrize("manifest", [None, {"schema": "notations.monorepo-imports.v1", "modules": []}])
def test_missing_or_incomplete_manifest_refuses_before_any_navigation_write(generator, tmp_path, monkeypatch, manifest):
    (tmp_path / "instruments").mkdir()
    if manifest is not None:
        (tmp_path / "instruments/manifest.json").write_text(json.dumps(manifest))
    before = snapshot(tmp_path)
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    with pytest.raises((FileNotFoundError, ValueError)):
        generator.main()
    assert snapshot(tmp_path) == before


def test_two_generator_runs_preserve_all_import_trees_and_committed_navigation(generator, tmp_path):
    # Like the import qualification tests, this checks a committed snapshot,
    # including index/working bytes, rather than accepting staged replacements.
    from monorepo import verify_imports
    clone = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", "--shared", "--config", "core.autocrlf=false",
                    "--config", "core.longpaths=true", str(ROOT), str(clone)], check=True, timeout=300)
    modules = json.loads((clone / "instruments/manifest.json").read_text(encoding="utf-8"))["modules"]
    expected = {module["role"]: module["import_tree"] for module in modules}
    assert len(expected) == 21
    assert verify_imports(clone) == expected
    for _ in range(2):
        subprocess.run([sys.executable, "scripts/write_mcp_folder_contracts.py"], cwd=clone,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, check=True, timeout=120)
        assert verify_imports(clone) == expected
        assert git(clone, "status", "--porcelain", "--untracked-files=all") == b""
