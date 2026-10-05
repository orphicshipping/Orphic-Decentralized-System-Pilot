"""Worker protocol and local source-integrity tests without third-party simulation."""
import base64
from hashlib import sha256
import importlib.metadata as metadata
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ciw import nvalchemi_contract as c
from ciw import nvalchemi_worker as worker


def test_unsupported_operation_refuses_in_real_worker_subprocess():
    root = str(Path(__file__).resolve().parents[1] / "src")
    program = "import sys,runpy;sys.path.insert(0,sys.argv[1]);runpy.run_module('ciw.nvalchemi_worker',run_name='__main__')"
    p = subprocess.run([sys.executable, "-I", "-B", "-c", program, root],
                       input=b'{"schema":"ciw.adapter-request.v1","operation_id":"training.start.v1","inputs":{}}',
                       capture_output=True, timeout=10, check=True)
    result = c.loads(p.stdout)
    assert result["status"] == "refused"
    assert "Unsupported" in result["refusal"]["message"]
    assert "data" not in result


def test_worker_validates_configuration_before_environment_or_gpu(monkeypatch):
    def prohibited():
        raise AssertionError("Must not inspect/load providers for invalid inputs")
    monkeypatch.setattr(worker, "probe_environment", prohibited)
    state = c.example_input()
    state["pbc"] = [True, False, False]
    with pytest.raises(ValueError):
        worker.evaluate({"configuration": state, "device": "cpu", "environment_sha256": c.digest("x")})


def test_missing_library_does_not_substitute_the_scalar_reference(monkeypatch):
    def missing():
        raise metadata.PackageNotFoundError("nvalchemi-toolkit")
    monkeypatch.setattr(worker, "probe_environment", missing)
    with pytest.raises(metadata.PackageNotFoundError):
        worker.evaluate({"configuration": c.example_input(), "device": "cpu", "environment_sha256": c.digest("x")})


def test_wrong_environment_refuses_before_loading_torch(monkeypatch):
    monkeypatch.setattr(worker, "probe_environment", lambda: {"environment": "different"})
    with pytest.raises(ValueError, match="fingerprint"):
        worker.evaluate({"configuration": c.example_input(), "device": "cpu", "environment_sha256": c.digest("x")})


def fake_distribution(tmp_path):
    path = tmp_path / "nvalchemi" / "example.py"
    path.parent.mkdir()
    raw = b'VALUE = 1\n'
    path.write_bytes(raw)
    class Entry:
        parts = ("nvalchemi", "example.py")
        hash = SimpleNamespace(mode="sha256", value=base64.urlsafe_b64encode(sha256(raw).digest()).decode().rstrip("="))
        def is_absolute(self): return False
        def as_posix(self): return "/".join(self.parts)
    return path, SimpleNamespace(files=[Entry()], locate_file=lambda _: tmp_path)


@pytest.mark.parametrize("mutation", ["none", "changed", "missing", "injected", "symlink"])
def test_installed_toolkit_record_and_untracked_files(tmp_path, monkeypatch, mutation):
    path, dist = fake_distribution(tmp_path)
    monkeypatch.setattr(worker.metadata, "distribution", lambda _: dist)
    if mutation == "changed": path.write_bytes(b'VALUE = 2\n')
    elif mutation == "missing": path.unlink()
    elif mutation == "injected": path.with_name("injected.py").write_bytes(b'bad')
    elif mutation == "symlink":
        target = tmp_path / "other.py"
        target.write_bytes(path.read_bytes())
        path.unlink()
        try: path.symlink_to(target)
        except OSError: pytest.skip("Host does not permit source symlink creation")
    if mutation == "none":
        assert worker._package_files("nvalchemi-toolkit").startswith("sha256:")
    else:
        with pytest.raises(ValueError):
            worker._package_files("nvalchemi-toolkit")


def test_no_editable_or_unpinned_upstream_source(monkeypatch):
    monkeypatch.setattr(worker.metadata, "version", lambda _: "fixture")
    monkeypatch.setattr(worker.metadata, "distribution", lambda _: SimpleNamespace(read_text=lambda _: '{}'))
    with pytest.raises(ValueError, match="exact non-editable"):
        worker.probe_environment()
