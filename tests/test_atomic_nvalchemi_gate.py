"""Provider-free gate behavior; these tests do not claim native qualification."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("atomic_native_gate", ROOT / "scripts/check_atomic_nvalchemi.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_missing_binding_is_retained_not_run(tmp_path):
    assert gate.main(["--output-dir", str(tmp_path)]) == 2
    report = json.loads((tmp_path / "gate.json").read_text())
    assert report["status"] == "NOT_RUN"
    assert report["operation"] == "atomic.lj-energy-forces.v1"
    assert report["may_authorize"] is False


def test_stale_native_evidence_cannot_satisfy_gate(tmp_path):
    old = tmp_path / "gate.json"
    old.write_text('{"status":"PASS"}')
    with pytest.raises(SystemExit): gate.main(["--output-dir", str(tmp_path)])
    assert json.loads(old.read_text())["status"] == "PASS"


def test_malformed_binding_is_failure_not_skip(tmp_path):
    binding = tmp_path / "binding.json"
    binding.write_text("{}")
    output = tmp_path / "evidence"
    assert gate.main(["--binding", str(binding), "--output-dir", str(output)]) == 1
    report = json.loads((output / "gate.json").read_text())
    assert report["status"] == "FAIL" and report["error"]["type"] == "ValueError"


def test_native_fixture_geometry_and_finite_difference_are_declared():
    cases = gate.cases()
    assert len(cases) == 12
    assert len(cases["cluster"]["positions"]) == 4
    assert cases["fd_plus"]["positions"][0][0] == 1e-4
    assert cases["fd_minus"]["positions"][0][0] == -1e-4
