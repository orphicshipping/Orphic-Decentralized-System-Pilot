"""Managed checkout timeouts retain bounded queries and exact-path cleanup."""
import importlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def operators(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("monorepo"), importlib.import_module("module_update")


@pytest.mark.parametrize("name", ["monorepo", "module_update"])
@pytest.mark.parametrize("arguments,expected", [
    (("worktree", "add", "--detach", "temporary", "HEAD"), 300),
    (("worktree", "remove", "--force", "temporary"), 300),
    (("worktree", "list", "--porcelain", "-z"), 30),
    (("rev-parse", "HEAD"), 30),
    (("merge-base", "--is-ancestor", "old", "HEAD"), 30),
])
def test_worktree_mutations_have_a_longer_bounded_timeout(operators, monkeypatch, tmp_path, name, arguments, expected):
    module = operators[0 if name == "monorepo" else 1]
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=b"observed")

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run))
    runner = module.git if name == "monorepo" else module._git
    assert runner(tmp_path, *arguments) == b"observed"
    assert calls[0][1]["timeout"] == expected


@pytest.fixture
def repository(operators, tmp_path):
    monorepo, _ = operators
    root = tmp_path / "repository"
    root.mkdir()
    for arguments in (("init", "--quiet"), ("config", "user.name", "Lifecycle Fixture"),
                      ("config", "user.email", "lifecycle@example.invalid")):
        monorepo.git(root, *arguments)
    (root / "source.txt").write_bytes(b"retained source\n")
    monorepo.git(root, "add", "source.txt")
    monorepo.git(root, "commit", "--quiet", "-m", "Retained source")
    revision = monorepo.git(root, "rev-parse", "HEAD").decode().strip()
    return root, revision


@pytest.mark.parametrize("kind", ["provider", "module"])
@pytest.mark.parametrize("registered", [False, True])
def test_failed_add_removes_only_its_own_registration(operators, repository, monkeypatch, tmp_path, kind, registered):
    monorepo, module_update = operators
    root, revision = repository
    unrelated = tmp_path / "unrelated"
    monorepo.git(root, "worktree", "add", "--detach", str(unrelated), revision)
    selected = monorepo if kind == "provider" else module_update
    name = "git" if kind == "provider" else "_git"
    original = getattr(selected, name)
    created = []

    def fail_add(directory, *arguments, **kwargs):
        if arguments[:2] == ("worktree", "add"):
            path = Path(arguments[-2])
            created.append(path)
            if registered:
                original(directory, *arguments, **kwargs)
                # Git can retain this exact initialization lock when an add
                # times out after registering its checkout.
                original(directory, "worktree", "lock", "--reason", "initializing", str(path))
            raise subprocess.TimeoutExpired(["git", *arguments], 300)
        return original(directory, *arguments, **kwargs)

    monkeypatch.setattr(selected, name, fail_add)
    monkeypatch.setattr(monorepo, "verify_imports", lambda root: {})
    monkeypatch.setattr(monorepo, "load_manifest", lambda root: {"modules": [
        {"role": "mcur", "runtime_revision": revision, "import_revision": revision}]})
    context = (monorepo.provider_worktrees(root, roles=["mcur"]) if kind == "provider"
               else module_update._detached(root, revision))
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            with context:
                pytest.fail("A failed checkout cannot execute")
        assert len(created) == 1 and not created[0].exists()
        records = monorepo.git(root, "worktree", "list", "--porcelain").decode()
        assert records.count("worktree ") == 2
        assert unrelated.exists()
        assert created[0].as_posix() not in records
    finally:
        monorepo.git(root, "worktree", "remove", "--force", str(unrelated))
