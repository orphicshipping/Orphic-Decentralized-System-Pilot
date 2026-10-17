"""Temporal checks over actual retained formats, with labelled synthetic faults."""
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
import time

import pytest

from ciw import link_contract as c
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.evidence_history import inspect_link_journal, inspect_control_checkpoints, main
from ciw.link_profiles import example_manifest
from ciw.link_runtime import DeviceRuntime
from ciw.link_store import Journal


def rule(report, name):
    return next(item for item in report["rules"] if item["rule"] == name)


def make_journal(tmp_path, *, rollback=False, reset=False, new_boot=False, overflow=False, missing=False):
    path = tmp_path / "journal.sqlite"
    device = example_manifest()
    channel = device["channels"][0]
    raw = b"1.200 " + channel["unit"].encode() + b"\r\n"
    with Journal(path, create=True) as journal:
        runtime = DeviceRuntime(device, journal, boot_id="synthetic-boot-a")
        runtime.emit(channel["id"], raw, tick=None if missing else 100)
        if overflow: runtime.event("overflow", "Synthetic overflow injection", tick=110)
        if reset: runtime.event("clock_reset", "Synthetic clock reset injection", tick=0)
        if new_boot: runtime = DeviceRuntime(device, journal, boot_id="synthetic-boot-b")
        runtime.emit(channel["id"], raw, tick=None if missing else 90 if rollback else 120)
    return path


def test_journal_original_unchanged_and_no_physical_history_invented(tmp_path):
    path = make_journal(tmp_path)
    before = path.read_bytes()
    report = inspect_link_journal(path)
    assert path.read_bytes() == before
    assert report["evidence_consistency"] == "NO_VIOLATION_FOUND"
    assert report["observed"]["origins"] == ["synthetic"]
    assert rule(report, "configuration_binding_before_interpretation")["status"] == "CONSISTENT"
    assert rule(report, "clock_reset_requires_new_boot")["status"] == "UNKNOWN"
    assert rule(report, "complete_stream_after_overflow_or_gap")["status"] == "UNKNOWN"
    assert rule(report, "revoked_grant_prevents_dispatch")["status"] == "UNKNOWN"


@pytest.mark.parametrize("fault,rule_name", [("rollback", "tick_rollback_requires_new_boot"), ("reset", "clock_reset_requires_new_boot")])
def test_journal_temporal_fault_has_original_counterexample_ids(tmp_path, fault, rule_name):
    path = make_journal(tmp_path, **{fault: True})
    report = inspect_link_journal(path)
    result = rule(report, rule_name)
    assert result["status"] == "VIOLATED"
    assert len(result["counterexample_event_ids"]) == 2
    assert all(item.startswith(("observation:sha256:", "event:sha256:")) for item in result["counterexample_event_ids"])


def test_reset_and_rollback_allowed_in_identified_new_boot(tmp_path):
    report = inspect_link_journal(make_journal(tmp_path, reset=True, new_boot=True, rollback=True))
    assert report["evidence_consistency"] == "NO_VIOLATION_FOUND"
    assert rule(report, "clock_reset_requires_new_boot")["status"] == "CONSISTENT"
    assert report["observed"]["device_boot_domains"] == 2


def test_missing_ticks_are_unknown(tmp_path):
    report = inspect_link_journal(make_journal(tmp_path, missing=True))
    assert rule(report, "tick_rollback_requires_new_boot")["status"] == "UNKNOWN"
    assert len(report["observed"]["missing_tick_observation_ids"]) == 2


def test_overflow_contradicts_explicit_completeness_only(tmp_path):
    path = make_journal(tmp_path, overflow=True)
    report = inspect_link_journal(path)
    assert report["evidence_consistency"] == "NO_VIOLATION_FOUND"
    result = rule(inspect_link_journal(path, claim_complete_stream=True), "complete_stream_after_overflow_or_gap")
    assert result["status"] == "VIOLATED"
    assert result["counterexample_event_ids"] == report["observed"]["overflow_event_ids"]


def test_corrupt_journal_uses_existing_original_wire_validator(tmp_path):
    path = make_journal(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE messages SET wire_ref=? WHERE ordinal=2", ("sha256:" + "0" * 64,))
    report = inspect_link_journal(path)
    assert report["evidence_consistency"] == "FAIL"
    assert rule(report, "retained_wire_and_manifest_binding")["status"] == "VIOLATED"


@pytest.fixture
def completed_checkpoint(tmp_path):
    profile = demo_config(tmp_path / "control")
    owner = from_profile(profile)
    host = ControlHost(owner)
    try:
        graph = host.template("baseline")["graph"]
        plan = host.create("source", "baseline", graph)
        host.admit(plan["plan_id"], plan["digest"])
        job = host.submit(plan["plan_id"], plan["digest"], "synthetic-history-attempt")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = host.get_run(job["run_id"])
            if job["state"] not in {"queued", "running"}: break
            time.sleep(.01)
        assert job["state"] == "completed"
        raw = (host._store.root / "checkpoint.json").read_bytes()
        yield raw
    finally:
        host.close()
        owner.close()


def changed(raw, mutate):
    value = json.loads(raw)
    mutate(value["state"])
    payload = json.dumps(value["state"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    value["sha256"] = sha256(payload).hexdigest()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def test_actual_control_artifact_checked_without_resuming(completed_checkpoint):
    report = inspect_control_checkpoints([completed_checkpoint])
    assert report["evidence_consistency"] == "NO_VIOLATION_FOUND"
    assert report["automatic_replay"] is False
    assert rule(report, "plan_admission_before_job")["status"] == "CONSISTENT"
    assert rule(report, "terminal_or_unknown_work_is_not_retried")["status"] == "UNKNOWN"
    assert rule(report, "revoked_grant_prevents_dispatch")["status"] == "UNKNOWN"


def test_corrupted_control_hash_refuses(completed_checkpoint):
    report = inspect_control_checkpoints([completed_checkpoint.replace(b'"schema":"ncp.control-state.v1"', b'"schema":"ncp.control-state.v9"')])
    assert report["evidence_consistency"] == "FAIL"
    assert rule(report, "checkpoint_byte_integrity")["counterexample_event_ids"] == ["snapshot:0"]


@pytest.mark.parametrize("fault", ["admission", "attempt", "outcome", "unknown_retry"])
def test_resealed_faults_are_semantically_checked(completed_checkpoint, fault):
    def corrupt(state):
        job = next(iter(state["jobs"].values()))
        if fault == "admission": state["plans"][job["plan_id"]]["admitted"] = False
        elif fault == "attempt": job["attempt"] = "altered-attempt"
        elif fault == "outcome": job["result"] = None
        elif fault == "unknown_retry": job["state"], job["result"] = "running", None
    snapshots = [completed_checkpoint, changed(completed_checkpoint, corrupt)]
    report = inspect_control_checkpoints(snapshots)
    assert report["evidence_consistency"] == "FAIL"
    assert any(item["counterexample_event_ids"] for item in report["rules"] if item["status"] == "VIOLATED")


def test_running_to_unknown_is_retained_without_auto_retry(completed_checkpoint):
    def running(state):
        job = next(iter(state["jobs"].values()))
        job["state"], job["result"] = "running", None
    first = changed(completed_checkpoint, running)
    def unknown(state): next(iter(state["jobs"].values()))["state"] = "unknown"
    second = changed(first, unknown)
    report = inspect_control_checkpoints([first, second])
    assert report["evidence_consistency"] == "NO_VIOLATION_FOUND"
    assert report["observed"]["unknown_occurrences"]
    assert rule(report, "interrupted_work_is_unknown")["status"] == "UNKNOWN"
    assert report["automatic_replay"] is False
    # A subsequent transition back to running is prohibited even when rehashed.
    failed = inspect_control_checkpoints([second, first])
    assert rule(failed, "terminal_or_unknown_work_is_not_retried")["status"] == "VIOLATED"


def test_cli_retains_report_without_source_mutation(tmp_path, capsys):
    path = make_journal(tmp_path)
    raw = path.read_bytes()
    report = tmp_path / "history.json"
    assert main(["link-journal", str(path), "--output", str(report)]) == 0
    assert path.read_bytes() == raw
    assert json.loads(report.read_text(encoding="utf-8"))["rule_version"] == "ncp.retained-history-rules.v1"
    with pytest.raises(FileExistsError):
        main(["link-journal", str(path), "--output", str(report)])


@pytest.mark.parametrize("fault", ["stale_plan_digest", "stale_compilation_seal", "stale_experiment_seal", "policy", "plan_identity"])
def test_plan_content_and_nested_seals_checked_after_outer_reseal(completed_checkpoint, fault):
    from ciw.agent_api import encode
    from ciw.operations.runner import seal
    def corrupt(state):
        key, plan = next(iter(state["plans"].items()))
        if fault in {"stale_plan_digest", "stale_compilation_seal", "stale_experiment_seal"}:
            plan["compilation"]["experiment"]["parameters"]["invented"] = 42
        if fault == "stale_compilation_seal": seal(plan["compilation"]["experiment"])
        if fault == "stale_experiment_seal": seal(plan["compilation"])
        if fault == "policy": state["policy"] = "sha256:" + "1" * 64
        if fault == "plan_identity": plan["uri"] = "ncp://plans/different"
        if fault in {"stale_compilation_seal", "stale_experiment_seal"}:
            body = {k: v for k, v in plan.items() if k not in {"plan_id", "digest", "admitted", "uri"}}
            plan["digest"] = "sha256:" + sha256(encode(body)).hexdigest()
            plan["plan_id"] = "p-" + plan["digest"][7:]
            plan["uri"] = "ncp://plans/" + plan["plan_id"]
            del state["plans"][key]
            state["plans"][plan["plan_id"]] = plan
            for job in state["jobs"].values():
                job["plan_id"], job["plan_digest"] = plan["plan_id"], plan["digest"]
    report = inspect_control_checkpoints([changed(completed_checkpoint, corrupt)])
    assert report["evidence_consistency"] == "FAIL"
    assert rule(report, "retained_plan_content_integrity")["status"] == "VIOLATED"
    assert rule(report, "retained_plan_content_integrity")["counterexample_event_ids"]


def test_history_total_byte_budget_precedes_json_parsing(monkeypatch):
    import ciw.evidence_history as history
    monkeypatch.setattr(history, "MAX_HISTORY_BYTES", 10)
    def unexpected(_): pytest.fail("Oversized history must be rejected before parsing")
    monkeypatch.setattr(history, "_checkpoint", unexpected)
    with pytest.raises(ValueError, match="total input budget"):
        history.inspect_control_checkpoints([b"123456", b"123456"])
