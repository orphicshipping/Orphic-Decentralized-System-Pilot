"""Installer metadata variations never replace candidate-wheel byte verification."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location(
    "wheel_binding_examples", Path(__file__).resolve().parents[1] / "scripts/check_domain_examples.py")
examples = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(examples)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setattr(examples, "ROOT", checkout)
    monkeypatch.setattr(examples, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=1)))
    package_root = tmp_path / "site-packages"
    (package_root / "ciw").mkdir(parents=True)
    wheel = tmp_path / "candidate.whl"
    files = {"ciw/__init__.py": b"# candidate package\n", "ciw/kernel.py": b"VALUE = 17\n"}
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
            (package_root / name).write_bytes(raw)
    direct = {"url": wheel.as_uri(), "archive_info": {}}
    state = {"wheel": wheel, "hash": hashlib.sha256(wheel.read_bytes()).hexdigest(),
             "direct": direct, "package_root": package_root, "checkout": checkout}
    distribution = SimpleNamespace(
        locate_file=lambda name: state["package_root"] / name,
        read_text=lambda name: json.dumps(direct) if name == "direct_url.json" else None)
    monkeypatch.setattr(examples.importlib.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(examples.importlib.metadata, "version", lambda name: "test-version")
    return state


def test_uv_style_missing_archive_hash_still_compares_every_installed_file(installation):
    result = examples.installed_binding(installation["wheel"])
    assert result["wheel_sha256"] == installation["hash"]
    assert result["installer_sha256"] is None
    assert result["hash_provenance"] == "computed_wheel_and_exact_installed_bytes"
    assert result["checked_package_files"] == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_correct_installer_sha_is_retained(installation, legacy):
    metadata = installation["direct"]["archive_info"]
    if legacy:
        metadata["hash"] = "sha256=" + installation["hash"]
    else:
        metadata["hashes"] = {"sha256": installation["hash"]}
    result = examples.installed_binding(installation["wheel"])
    assert result["installer_sha256"] == installation["hash"]
    assert result["checked_package_files"] == 2


@pytest.mark.parametrize("value", ["0" * 64, None, "", 13])
def test_any_present_wrong_or_invalid_installer_sha_refuses(installation, value):
    installation["direct"]["archive_info"]["hashes"] = {"sha256": value}
    with pytest.raises(ValueError, match="Installer-declared SHA256"):
        examples.installed_binding(installation["wheel"])


def test_conflicting_legacy_sha_refuses_even_with_correct_modern_sha(installation):
    installation["direct"]["archive_info"].update(
        hashes={"sha256": installation["hash"]}, hash="sha256=" + "0" * 64)
    with pytest.raises(ValueError, match="Installer-declared SHA256"):
        examples.installed_binding(installation["wheel"])


@pytest.mark.parametrize("declared", [False, True])
def test_installed_byte_tampering_refuses_with_or_without_installer_sha(installation, declared):
    if declared:
        installation["direct"]["archive_info"]["hashes"] = {"sha256": installation["hash"]}
    (installation["package_root"] / "ciw/kernel.py").write_bytes(b"VALUE = 99\n")
    with pytest.raises(ValueError, match="Installed package differs from wheel: ciw/kernel.py"):
        examples.installed_binding(installation["wheel"])


def test_matching_bytes_do_not_allow_another_installation_url(installation):
    installation["direct"]["url"] = (installation["wheel"].parent / "other.whl").as_uri()
    with pytest.raises(ValueError, match="must come from --wheel"):
        examples.installed_binding(installation["wheel"])


def test_nonisolated_python_refuses(installation, monkeypatch):
    monkeypatch.setattr(examples, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=0)))
    with pytest.raises(ValueError, match="python -I"):
        examples.installed_binding(installation["wheel"])


def test_checkout_working_directory_refuses(installation, monkeypatch):
    monkeypatch.chdir(installation["checkout"])
    with pytest.raises(ValueError, match="outside the checkout"):
        examples.installed_binding(installation["wheel"])


def test_source_package_location_refuses(installation):
    source = installation["checkout"] / "src"
    (source / "ciw").mkdir(parents=True)
    installation["package_root"] = source
    with pytest.raises(ValueError, match="Install the candidate wheel outside"):
        examples.installed_binding(installation["wheel"])
