"""Coordinator guarantees exercised across real OS processes.

Each scenario runs separate Python interpreters against one retain directory:
crash (SIGKILL), timeout of a hung worker, cancellation, duplicate delivery,
reconnect, concurrent admission, concurrent handoff claims, concurrent release
and conflicting idempotency-key reuse. Every wait is bounded.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / "tests" / "support" / "coordinator_child.py"
LIMIT = 60

pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals and fork-free spawning are exercised on POSIX")


def env_for(retain: Path, **extra) -> dict:
    env = dict(os.environ)
    env["NCP_RETAIN_DIR"] = str(retain)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env.update({k: str(v) for k, v in extra.items()})
    return env


def spawn(retain, *args, **extra):
    return subprocess.Popen([sys.executable, str(CHILD), *map(str, args)], env=env_for(retain, **extra),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(proc) -> dict:
    out, err = proc.communicate(timeout=LIMIT)
    assert proc.returncode == 0, err
    return json.loads(out.strip().splitlines()[-1])


def run(retain, *args, **extra) -> dict:
    return finish(spawn(retain, *args, **extra))


def wait_for(path: Path, proc=None) -> None:
    deadline = time.monotonic() + LIMIT
    while not path.exists():
        if proc is not None and proc.poll() is not None:
            raise AssertionError(proc.stderr.read())
        assert time.monotonic() < deadline, "child never became ready"
        time.sleep(0.01)


def kill(proc) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGKILL)
    proc.communicate(timeout=LIMIT)


def test_concurrent_admission_executes_once_across_processes(tmp_path):
    retain, go = tmp_path / "retain", tmp_path / "go"
    procs = [spawn(retain, "submit", "owner-a", "job-many", go) for _ in range(6)]
    time.sleep(0.5)
    go.write_text("go")
    results = [finish(p) for p in procs]
    assert sum(r["executed_here"] for r in results) == 1
    completed = [r for r in results if r["replayed"] is False]
    assert len(completed) == 1 and completed[0]["status"] == "completed"
    for r in results:
        assert r["status"] in {"running", "completed"}
    again = run(retain, "submit", "owner-a", "job-many")
    assert again["replayed"] is True and again["status"] == "completed" and again["executed_here"] == 0
    assert again["artifact_id"] == completed[0]["artifact_id"]


def test_conflicting_idempotency_key_reuse_is_refused_from_another_process(tmp_path):
    retain = tmp_path / "retain"
    first = run(retain, "submit", "owner-a", "job-conflict")
    assert first["status"] == "completed"
    clash = run(retain, "submit-example", "owner-a", "job-conflict")
    assert clash["error"] == "idempotency_key_conflict" and clash["executed_here"] == 0


def test_crashed_worker_is_lost_and_recovered_once(tmp_path):
    retain, marker = tmp_path / "retain", tmp_path / "ready"
    worker = spawn(retain, "hang", "owner-a", "job-crash", marker)
    wait_for(marker, worker)
    # While the worker lives its lease is held: a reconnecting client sees running.
    alive = run(retain, "recall", "owner-a", "job-crash")
    assert alive["status"] == "running"
    duplicate = run(retain, "submit", "owner-a", "job-crash")
    assert duplicate["status"] == "running" and duplicate["executed_here"] == 0
    kill(worker)
    recoverers = [spawn(retain, "recover", "owner-a", "-") for _ in range(4)]
    reports = [finish(p) for p in recoverers]
    jobs = [j for r in reports for j in r["jobs"]]
    assert len(jobs) == 1, reports
    recalled = run(retain, "recall", "owner-a", "job-crash")
    assert recalled["status"] == "failed" and recalled["error"] == "worker_lost"
    assert recalled["artifact_id"] == jobs[0]["artifact_id"]


def test_hung_worker_times_out_then_reconnect_sees_the_loss(tmp_path):
    retain, marker = tmp_path / "retain", tmp_path / "ready"
    worker = spawn(retain, "hang", "owner-a", "job-timeout", marker)
    wait_for(marker, worker)
    budget = time.monotonic() + 1.0
    while time.monotonic() < budget:
        assert run(retain, "recall", "owner-a", "job-timeout")["status"] == "running"
    # The supervisor's deadline passed: it terminates the worker.
    worker.terminate()
    worker.communicate(timeout=LIMIT)
    reconnect = run(retain, "recall", "owner-a", "job-timeout")
    assert reconnect["status"] == "failed" and reconnect["error"] == "worker_lost"
    replay = run(retain, "submit", "owner-a", "job-timeout")
    assert replay["replayed"] is True and replay["executed_here"] == 0


def test_cancellation_is_recorded_while_the_process_stays_alive(tmp_path):
    retain, marker = tmp_path / "retain", tmp_path / "ready"
    worker = spawn(retain, "hang-cancel", "owner-a", "job-cancel", marker, CHILD_LINGER=30)
    line = worker.stdout.readline()
    assert json.loads(line)["cancelled"] is True
    try:
        # Same PID is still alive; PID liveness alone would leave this "running" forever.
        recalled = run(retain, "recall", "owner-a", "job-cancel")
        assert recalled["status"] == "cancelled"
    finally:
        kill(worker)


def test_reused_pid_does_not_keep_a_dead_job_alive(tmp_path):
    retain = tmp_path / "retain"
    sys.path.insert(0, str(ROOT / "src"))
    os.environ["NCP_RETAIN_DIR"] = str(retain)
    try:
        from ciw.coordinator import _digest, _idem_name, _store_job
        from ciw.retention import Store
        from ciw.sequence_kinetics import example_request
        digest = _digest("ncp_domain_run", {"domain": "sequence", "request": example_request()}, None)
        # The record names this test's own live PID, as a recycled PID would.
        _store_job(Store("owner-a"), _idem_name("job-pid"), {
            "schema": "ncp.coordinator-job.v1", "operation": "ncp_domain_run", "executions": 1,
            "status": "running", "pid": os.getpid(), "request_digest": digest, "authorizes_execution": False})
    finally:
        os.environ.pop("NCP_RETAIN_DIR", None)
    recalled = run(retain, "recall", "owner-a", "job-pid")
    assert recalled["status"] == "failed" and recalled["error"] == "worker_lost"


def test_exclusive_hold_is_not_stolen_and_frees_on_crash(tmp_path):
    retain, marker = tmp_path / "retain", tmp_path / "ready"
    holder = spawn(retain, "hold", "owner-a", "oven", marker)
    wait_for(marker, holder)
    contenders = [spawn(retain, "exclusive", "owner-a", f"job-oven-{i}", "oven") for i in range(4)]
    for result in map(finish, contenders):
        assert result["error"] == "exclusive_resource_busy"
    timed = run(retain, "exclusive", "owner-a", "job-oven-wait", "oven", EXCLUSIVE_WAIT=0.3)
    assert timed["error"] == "exclusive_resource_busy"
    kill(holder)
    report = run(retain, "recover", "owner-a", "-")
    assert report["released"] == ["oven"]
    after = run(retain, "exclusive", "owner-a", "job-oven-after", "oven")
    assert after["status"] == "completed"


def test_concurrent_handoff_claims_produce_one_copy(tmp_path):
    retain, go = tmp_path / "retain", tmp_path / "go"
    first = run(retain, "submit", "owner-a", "job-handoff")
    sys.path.insert(0, str(ROOT / "src"))
    os.environ["NCP_RETAIN_DIR"] = str(retain)
    try:
        from ciw.coordinator import handoff
        from ciw.retention import Store
        grant = handoff(Store("owner-a"), first["artifact_id"], "owner-b")["grant_id"]
    finally:
        os.environ.pop("NCP_RETAIN_DIR", None)
    claimers = [spawn(retain, "claim", "owner-b", grant, go) for _ in range(6)]
    intruder = spawn(retain, "claim", "owner-c", grant, go)
    time.sleep(0.5)
    go.write_text("go")
    results = [finish(p) for p in claimers]
    assert "handoff_refused" in finish(intruder)["error"]
    assert len({r["artifact_id"] for r in results}) == 1
    assert sum(r["replayed"] is False for r in results) == 1
    copies = [p for p in (retain).rglob("retained-*.json") if json.loads(p.read_text()).get("kind") == "handoff"]
    assert len(copies) == 1
    # A duplicate delivery after reconnect returns the same copy.
    assert run(retain, "claim", "owner-b", grant)["artifact_id"] == results[0]["artifact_id"]
    os.environ["NCP_RETAIN_DIR"] = str(retain)
    try:
        assert handoff(Store("owner-a"), first["artifact_id"], "owner-b")["already_claimed"] is True
    finally:
        os.environ.pop("NCP_RETAIN_DIR", None)


def test_concurrent_release_unlinks_once(tmp_path):
    retain, go = tmp_path / "retain", tmp_path / "go"
    first = run(retain, "submit", "owner-a", "job-release")
    releasers = [spawn(retain, "release", "owner-a", first["artifact_id"], go) for _ in range(5)]
    time.sleep(0.5)
    go.write_text("go")
    results = [finish(p) for p in releasers]
    assert sum(r.get("released") is True for r in results) == 1
    assert all("Unknown retained artifact" in r["error"] for r in results if "error" in r)
