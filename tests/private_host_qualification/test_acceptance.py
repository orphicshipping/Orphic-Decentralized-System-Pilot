"""A qualification gate cannot pass absent, stale, skipped, or unknown evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("private_host_qualification", ROOT / "scripts/check_private_host_qualification.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def evidence(tmp_path, phase="native_clients"):
    (tmp_path / "observed.json").write_text('{"observation":"actual-fixture"}\n')
    reference = {"path": "observed.json", "sha256": gate.sha(tmp_path / "observed.json")}
    return {"schema": "odsp.private-fixture-phase.v1", "phase": phase, "status": "PASS", "skipped": 0,
            "checks": [{"id": check, "status": "PASS", "evidence": [deepcopy(reference)]}
                       for check in sorted(gate.REQUIRED_CHECKS[phase])]}


@pytest.mark.parametrize("phase", gate.PHASES)
def test_complete_local_evidence_is_validated(tmp_path, phase):
    receipt = evidence(tmp_path, phase)
    assert gate.validate_phase(phase, receipt, tmp_path) == receipt


@pytest.mark.parametrize("status", ["UNKNOWN", "RUNNING", "SKIP", None, "FAIL", "passed"])
def test_unknown_or_nonpassing_phase_outcome_is_never_a_pass(tmp_path, status):
    receipt = evidence(tmp_path)
    receipt["status"] = status
    with pytest.raises(ValueError, match="unknown phase outcome"):
        gate.validate_phase("native_clients", receipt, tmp_path)


def test_missing_required_journey_is_never_a_pass(tmp_path):
    receipt = evidence(tmp_path)
    receipt["checks"].pop()
    with pytest.raises(ValueError, match="Missing required fixture"):
        gate.validate_phase("native_clients", receipt, tmp_path)


@pytest.mark.parametrize("change", ["missing", "tampered", "symlink", "no_reference", "escape", "unknown_check", "duplicate", "skipped"])
def test_unusable_check_evidence_fails(tmp_path, change):
    receipt = evidence(tmp_path)
    path = tmp_path / "observed.json"
    if change == "missing":
        path.unlink()
    elif change == "tampered":
        path.write_text("replaced after qualification")
    elif change == "symlink":
        copy = tmp_path / "copy.json"
        path.rename(copy)
        path.symlink_to(copy)
    elif change == "no_reference":
        receipt["checks"][0]["evidence"] = []
    elif change == "escape":
        receipt["checks"][0]["evidence"][0]["path"] = "../observed.json"
    elif change == "unknown_check":
        receipt["checks"][0]["status"] = "unknown"
    elif change == "duplicate":
        receipt["checks"].append(deepcopy(receipt["checks"][0]))
    elif change == "skipped":
        receipt["skipped"] = 1
    with pytest.raises(ValueError):
        gate.validate_phase("native_clients", receipt, tmp_path)


def test_missing_installed_runtime_emits_failure_receipt_and_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "source_identity", lambda root: {"commit": "a" * 40, "tree": "b" * 40, "clean": True})
    args = SimpleNamespace(candidate_root=tmp_path, output_dir=tmp_path / "receipt",
                           python=tmp_path / "missing-python", wheel=tmp_path / "candidate.whl",
                           manifest=tmp_path / "manifest.json", node="node")
    assert gate.qualify(args) == 1
    receipt = json.loads((args.output_dir / "report.json").read_text())
    assert receipt["status"] == "FAIL" and "runtime is missing" in receipt["error"]
    assert receipt["phases"] == {}


def test_successful_command_without_required_result_does_not_pass_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "source_identity", lambda root: {"commit": "a" * 40, "tree": "b" * 40, "clean": True})
    helper = tmp_path / "tests/private_host_qualification/journeys.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# no receipt\n")
    wheel, manifest = tmp_path / "candidate.whl", tmp_path / "manifest.json"
    wheel.write_bytes(b"wheel fixture")
    manifest.write_text("{}")
    args = SimpleNamespace(candidate_root=tmp_path, output_dir=tmp_path / "receipt", python=Path(sys.executable),
                           wheel=wheel, manifest=manifest, node="node")
    assert gate.qualify(args) == 1
    receipt = json.loads((args.output_dir / "report.json").read_text())
    assert receipt["status"] == "FAIL" and receipt["phases"]["runtime"]["command"]["status"] == "PASS"
    assert receipt["phases"]["runtime"]["status"] == "FAIL"


def test_command_failure_retains_duration_log_and_resource_observation(tmp_path):
    receipt = gate.run_command([sys.executable, "-c", "raise SystemExit(7)"], tmp_path, tmp_path / "command", timeout=2)
    assert receipt["status"] == "FAIL" and receipt["returncode"] == 7
    assert receipt["duration_seconds"] > 0
    assert receipt["resource_samples"] > 0 and receipt["peak_processes"] >= 1
    assert (tmp_path / "command/command.json").is_file()


def test_command_deadline_stops_process_and_records_failure(tmp_path):
    receipt = gate.run_command([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, tmp_path / "command", timeout=0.1)
    assert receipt["status"] == "FAIL" and "deadline" in receipt["error"]
    assert receipt["duration_seconds"] < 2


def test_process_memory_ceiling_is_enforced(tmp_path, monkeypatch):
    real_tree = gate.process_tree
    def over_budget(pid):
        return {child: (row[0], row[1], gate.TARGETS["aggregate_process_rss_limit_bytes"] + 1)
                for child, row in real_tree(pid).items()}
    monkeypatch.setattr(gate, "process_tree", over_budget)
    receipt = gate.run_command([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, tmp_path / "command", timeout=2)
    assert receipt["status"] == "FAIL" and "memory ceiling" in receipt["error"]


@pytest.mark.parametrize("summary", ["", "# tests 0\n# pass 0\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n",
    "# tests 2\n# pass 1\n# fail 0\n# cancelled 0\n# skipped 1\n# todo 0\n"])
def test_missing_or_skipped_node_test_evidence_fails(summary):
    with pytest.raises(ValueError):
        gate.parse_tap(summary)


def test_node_summary_requires_all_checks_to_run():
    summary = "# tests 2\n# pass 2\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n"
    assert gate.parse_tap(summary)["tests"] == 2


def test_authorization_text_is_redacted_from_receipts():
    original = 'Authorization: Bearer fixture-private-credential\n{"access_token":"secret-value"}'
    redacted = gate.redact(original)
    assert "fixture-private-credential" not in redacted and "secret-value" not in redacted


def test_soak_targets_are_explicit_and_distinct_from_smoke():
    assert gate.TARGETS["soak_seconds"] == 60
    assert gate.TARGETS["soak_minimum_completed_calls"] == 100
    assert gate.TARGETS["soak_command_timeout_seconds"] == 90
    assert gate.TARGETS["clients"] == 3
