"""Independent persistence-fault and concurrent-retry checks for Board refits."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from ciw.board_refit import BoardRefitRefusal
from ciw.board_refit_cli import demo, open_host
from ciw.visual_board_server import BoardWorkbench


def _profile(tmp_path):
    return demo(tmp_path / "profile")["profile"]


def _preview(host):
    status = host.call("ncp_board_status", {})
    return host.call("ncp_board_preview", {
        "base_board_ref": status["board_ref"], "node_id": "statistics",
        "parameter": "channel", "replacement": "v"})


def _candidate_arguments(host):
    preview = _preview(host)
    validation = host.call("ncp_board_validate", {"preview_ref": preview["preview_ref"]})
    baseline = host.call("ncp_board_baseline", {"attempt_id": "baseline"})
    return {"preview_ref": preview["preview_ref"],
            "validation_ref": validation["validation_ref"],
            "baseline_ref": baseline["baseline_ref"], "attempt_id": "candidate"}


def test_budget_failure_cannot_expose_an_uncommitted_preview(tmp_path, monkeypatch):
    import ciw.board_refit as domain
    profile = _profile(tmp_path)
    host = open_host(profile)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(domain, "MAX_TOTAL_BYTES", 1)
            with pytest.raises(BoardRefitRefusal, match="budget_exhausted"):
                _preview(host)
            # Failure must close or roll back; never expose the in-memory-only preview.
            with pytest.raises(ValueError):
                host.descriptions()
    finally:
        host.close()
    with open_host(profile) as resumed:
        status = resumed.call("ncp_board_status", {})
        assert status["budgets"]["previews_used"] == 0
        assert status["attempts"] == []
        assert _preview(resumed)["provider_execution"] is False


@pytest.mark.parametrize("kind", ["baseline", "candidate"])
def test_failed_completion_checkpoint_never_replays_or_publishes_memory_only_success(
        tmp_path, monkeypatch, kind):
    profile = _profile(tmp_path)
    host = open_host(profile)
    if kind == "candidate":
        arguments = _candidate_arguments(host)
        tool = "ncp_board_recompute"
        previous_baseline = arguments["baseline_ref"]
    else:
        arguments = {"attempt_id": "baseline"}
        tool = "ncp_board_baseline"
        previous_baseline = None
    method = "run_candidate" if kind == "candidate" else "run_baseline"
    original_run = getattr(BoardWorkbench, method)
    calls = []
    def counted_run(self, *args, **kwargs):
        calls.append(1)
        return original_run(self, *args, **kwargs)
    monkeypatch.setattr(BoardWorkbench, method, counted_run)
    original_save = host._store.save
    def fail_final_checkpoint(state, artifacts):
        attempt = state["attempts"].get(arguments["attempt_id"])
        if attempt and attempt["status"] == "completed":
            raise OSError("injected checkpoint failure")
        return original_save(state, artifacts)
    monkeypatch.setattr(host._store, "save", fail_final_checkpoint)
    try:
        with pytest.raises(OSError, match="injected checkpoint failure"):
            host.call(tool, arguments)
        assert len(calls) == 1
        with pytest.raises(ValueError):
            host.call(tool, arguments)
        assert len(calls) == 1
    finally:
        host.close()
    with open_host(profile) as resumed:
        status = resumed.call("ncp_board_status", {})
        assert status["baseline_ref"] == previous_baseline
        assert status["candidate_refs"] == []
        attempt = next(a for a in status["attempts"] if a["attempt_id"] == arguments["attempt_id"])
        assert attempt["status"] == "outcome_unknown"
        assert attempt["outcome_ref"] is None
        with pytest.raises(BoardRefitRefusal, match="attempt_outcome_unknown"):
            resumed.call(tool, arguments)
        assert len(calls) == 1


def test_concurrent_retry_computes_candidate_once(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    with open_host(profile) as host:
        arguments = _candidate_arguments(host)
        original = BoardWorkbench.run_candidate
        invocations = []
        def counted(self, *args, **kwargs):
            invocations.append(1)
            return original(self, *args, **kwargs)
        monkeypatch.setattr(BoardWorkbench, "run_candidate", counted)
        barrier = Barrier(2)
        def call():
            barrier.wait(timeout=10)
            return host.call("ncp_board_recompute", arguments)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        assert results[0] == results[1]
        assert len(invocations) == 1
        assert results[0]["review_status"] == "unreviewed"
        assert results[0]["accepted"] is False
        status = host.call("ncp_board_status", {})
        assert status["budgets"]["candidate_runs_used"] == 1
        assert status["baseline_ref"] == arguments["baseline_ref"]


def test_one_active_writer_per_profile(tmp_path):
    profile = _profile(tmp_path)
    with open_host(profile):
        with pytest.raises(ValueError, match="active writer"):
            open_host(profile)
    with open_host(profile) as resumed:
        assert resumed.call("ncp_board_status", {})["attempts"] == []
