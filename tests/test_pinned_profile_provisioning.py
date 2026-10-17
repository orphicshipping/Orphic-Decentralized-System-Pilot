"""Exact local source preparation never substitutes dirty files or missing blobs."""
import importlib
import json
from pathlib import Path
import subprocess

import pytest


def git(path, *args):
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(path), *args],
        capture_output=True, check=True, text=True,
    ).stdout.strip()


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    helper = importlib.import_module("provision_pinned_profiles")
    root = tmp_path / "project"
    manifests = root / "src" / "ciw"
    manifests.mkdir(parents=True)
    source = tmp_path / "donor"
    source.mkdir()
    git(source, "init", "--quiet")
    (source / "provider.py").write_bytes(b"value = 1\n")
    git(source, "add", "provider.py")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Original provider")
    pin = git(source, "rev-parse", "HEAD")
    (manifests / "pins.json").write_text(json.dumps({"engine": {
        "revision": pin, "module": "provider", "source_root": "."}}))
    monkeypatch.setattr(helper, "ROOT", root)
    monkeypatch.setattr(helper, "PROFILES", {"profile": ("pins.json",)})
    return helper, source, pin, tmp_path / "new-stack"


def test_materializes_original_commit_objects_without_importing_dirty_donor(preparation):
    helper, source, pin, output = preparation
    (source / "provider.py").write_bytes(b"raise RuntimeError('unapproved working bytes')\n")
    (source / "shadow.py").write_bytes(b"raise RuntimeError('untracked')\n")
    watched = [source / "provider.py", source / "shadow.py",
               *(source / ".git" / name for name in ("HEAD", "index", "config"))]
    before = {p: p.read_bytes() for p in watched}
    receipt = helper.provision(source, output, ["profile"])
    checkout = output / "profile" / "engine"
    assert receipt["status"] == "PINNED_SOURCES_PREPARED"
    assert git(checkout, "rev-parse", "HEAD") == pin
    assert (checkout / "provider.py").read_bytes() == b"value = 1\n"
    assert not (checkout / "shadow.py").exists()
    assert not (checkout / ".git" / "objects" / "info" / "alternates").exists()
    assert helper.validate_checkout(checkout, pin) == checkout.resolve()
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert json.loads((output / "provisioning.json").read_bytes())["status"] == receipt["status"]


def test_existing_output_is_never_overwritten(preparation):
    helper, source, _, output = preparation
    output.mkdir()
    marker = output / "other-writer.txt"
    marker.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        helper.provision(source, output, ["profile"])
    assert marker.read_bytes() == b"preserve"
    assert list(output.iterdir()) == [marker]


def test_missing_required_blob_refuses_before_creating_any_checkout(preparation):
    helper, source, _, output = preparation
    blob = git(source, "rev-parse", "HEAD:provider.py")
    # Remove one loose object in this disposable test repository only; the
    # matching working file cannot silently substitute for missing Git evidence.
    loose = source / ".git" / "objects" / blob[:2] / blob[2:]
    loose.chmod(0o600)  # Git marks loose objects read-only on Windows.
    loose.unlink()
    with pytest.raises(ValueError, match="missing required"):
        helper.provision(source, output, ["profile"])
    assert not output.exists()
    assert (source / "provider.py").read_bytes() == b"value = 1\n"


def test_duplicate_profile_refuses_before_source_materialization(preparation):
    helper, source, _, output = preparation
    with pytest.raises(ValueError, match="distinct"):
        helper.provision(source, output, ["profile", "profile"])
    assert not output.exists()


def test_output_cannot_write_inside_donor_repository(preparation):
    helper, source, _, _ = preparation
    output = source / "nested-stack"
    with pytest.raises(ValueError, match="outside the donor"):
        helper.provision(source, output, ["profile"])
    assert not output.exists()
