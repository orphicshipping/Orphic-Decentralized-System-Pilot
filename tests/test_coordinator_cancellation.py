"""Coordinator cancellation and lease cleanup across live POSIX processes.

Faults occur after real kernel acquisition and real metadata I/O. A worker
catches the exception and stays alive while independent processes probe its
leases, recall its job, and retry its idempotency key. SIGKILL remains a lost
worker, not a caught cancellation. No native provider is executed.

This file is self-contained so it can also run outside the checkout against an
installed wheel. Child imports follow the ciw package imported by this test.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import ciw
import pytest


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="Lease and signal qualification requires POSIX with no-follow support",
)
TIMEOUT = 20
PACKAGE_PARENT = Path(ciw.__file__).resolve().parents[1]

CHILD = r'''
import json, os, signal, sys, time
from pathlib import Path
import ciw
from ciw import coordinator, services
from ciw.retention import Store, _owner_dir

signal.signal(signal.SIGINT, signal.default_int_handler)
cfg = json.loads(sys.argv[1])
store = Store("cancellation-worker")
key = cfg.get("key", "job")
arguments = {"domain": "sequence", "example": True}
exclusive = coordinator._exclusive_path("oven")
ready, result_path, stop = map(Path, (cfg["ready"], cfg["result"], cfg["stop"]))
executions = Path(cfg["executions"])
injected = False
fault_fd = None
finish_injected = False

def emit(path, value):
    value["ciw_file"] = ciw.__file__
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)

def wait_for(path):
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("child handshake deadline expired")
        time.sleep(0.01)

def pause(boundary):
    emit(ready, {"boundary": boundary})
    wait_for(stop)

def is_exclusive(fd):
    try:
        descriptor, target = os.fstat(fd), exclusive.stat()
        return (descriptor.st_dev, descriptor.st_ino) == (target.st_dev, target.st_ino)
    except OSError:
        return False

def probe(path):
    lease = coordinator._Lease(path)
    try:
        return lease.try_acquire()
    finally:
        lease.release()

def domain_run(**kwargs):
    # O_APPEND makes each short execution marker a separate retained event.
    fd = os.open(executions, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (str(os.getpid()) + "\n").encode())
    finally:
        os.close(fd)
    if cfg["mode"] == "crash" and cfg["boundary"] == "domain":
        pause("domain")
    return {"artifact_id": None, "example": True}

services.domain_run = domain_run
mode = cfg["mode"]

if mode == "release_after_close":
    lease = coordinator._Lease(exclusive)
    assert lease.try_acquire()
    original_fd = lease.fd
    original_close = coordinator.os.close
    def closed_then_failed(fd):
        original_close(fd)
        if fd == original_fd:
            raise OSError("injected after descriptor was closed")
    coordinator.os.close = closed_then_failed
    try:
        try:
            lease.release()
            first_exception = None
        except OSError:
            first_exception = "OSError"
    finally:
        coordinator.os.close = original_close
    detached = lease.fd is None
    replacement = os.open(Path(cfg["stop"]).with_suffix(".unrelated"),
                          os.O_RDWR | os.O_CREAT, 0o600)
    if replacement != original_fd:
        os.dup2(replacement, original_fd)
        os.close(replacement)
        replacement = original_fd
    try:
        lease.release()
        try:
            os.write(replacement, b"unrelated descriptor still belongs to caller")
            replacement_open = True
        except OSError:
            replacement_open = False
        emit(result_path, {"exception": first_exception, "detached": detached,
                           "replacement_open": replacement_open})
        wait_for(stop)
    finally:
        try:
            os.close(replacement)
        except OSError:
            pass
elif mode == "holder":
    lease = coordinator._hold_exclusive(Store("another-owner"), "oven")
    assert lease is not None
    try:
        pause("holding")
    finally:
        coordinator._release_exclusive(lease)
elif mode == "query":
    if cfg.get("gate"):
        emit(ready, {"boundary": "query-gate"})
        wait_for(Path(cfg["gate"]))
    action = cfg["action"]
    if action == "probe":
        directory = _owner_dir(store.owner)
        name = coordinator._idem_name(key)
        value = {"job_available": probe(coordinator._lease_path(directory, name)),
                 "exclusive_available": probe(exclusive)}
    elif action == "recall":
        value = coordinator.coordinate("recall", store=store, idempotency_key=key)
    elif action == "replay":
        value = coordinator.submit(store, "ncp_domain_run", arguments, key, exclusive="oven")
    else:
        raise AssertionError("unknown query")
    emit(result_path, value)
else:
    error_type = {"KeyboardInterrupt": KeyboardInterrupt, "SystemExit": SystemExit,
                  "OSError": OSError}.get(cfg.get("exception"), KeyboardInterrupt)
    if mode == "wait":
        original_acquire = coordinator._Lease.try_acquire
        def watched_acquire(self):
            acquired = original_acquire(self)
            if self.path == exclusive and not acquired:
                emit(ready, {"boundary": "contended"})
            return acquired
        coordinator._Lease.try_acquire = watched_acquire
    if mode == "kernel_fault" or (mode == "crash" and cfg["boundary"] == "acquired"):
        original_lock = coordinator._try_lock
        def interrupted_lock(fd):
            global injected, fault_fd
            acquired = original_lock(fd)
            if acquired and is_exclusive(fd):
                if mode == "crash":
                    pause("acquired")
                elif not injected:
                    injected, fault_fd = True, fd
                    raise error_type("injected after kernel acquisition")
            return acquired
        coordinator._try_lock = interrupted_lock
    if mode in {"metadata_fault", "release_fault"}:
        operation = cfg.get("operation", "ftruncate")
        original_io = getattr(coordinator.os, operation)
        calls = 0
        def interrupted_io(fd, *args):
            global calls, injected, fault_fd
            matched = is_exclusive(fd)
            returned = original_io(fd, *args)
            if matched:
                calls += 1
                if calls == (2 if mode == "release_fault" else 1):
                    injected, fault_fd = True, fd
                    raise error_type("injected exclusive metadata " + operation)
            return returned
        setattr(coordinator.os, operation, interrupted_io)
    if mode == "crash" and cfg["boundary"] == "completed":
        original_finish = coordinator._finish
        def paused_finish(store, key, record):
            original_finish(store, key, record)
            if record.get("status") == "completed":
                pause("completed")
        coordinator._finish = paused_finish
    if cfg.get("finish_failure"):
        original_finish = coordinator._finish
        def failed_finish(store, key, record):
            global finish_injected
            if record.get("status") == "cancelled" and not finish_injected:
                finish_injected = True
                raise OSError("injected cancellation record persistence failure")
            return original_finish(store, key, record)
        coordinator._finish = failed_finish
    try:
        value = coordinator.submit(store, "ncp_domain_run", arguments, key,
                                   exclusive="oven", exclusive_wait=25 if mode == "wait" else 0)
        outcome = {"exception": None, "result": value}
    except BaseException as exc:
        outcome = {"exception": type(exc).__name__}
    outcome.update(injected=injected, finish_injected=finish_injected, fault_fd_still_owns_resource=(
        fault_fd is not None and is_exclusive(fault_fd)))
    emit(result_path, outcome)
    # Parent probes while this process still lives; process exit cannot hide leaks.
    wait_for(stop)
'''


class Children:
    def __init__(self, directory):
        self.directory = directory
        self.retain = directory / "retain"
        self.executions = directory / "executions"
        self.children = []

    def spawn(self, mode, **options):
        prefix = self.directory / str(len(self.children))
        cfg = dict(mode=mode, ready=str(prefix.with_suffix(".ready")),
                   result=str(prefix.with_suffix(".result")),
                   stop=str(prefix.with_suffix(".stop")),
                   executions=str(self.executions), **options)
        env = dict(os.environ, NCP_RETAIN_DIR=str(self.retain),
                   PYTHONPATH=str(PACKAGE_PARENT), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen([sys.executable, "-c", CHILD, json.dumps(cfg)],
                                cwd=self.directory, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = (proc, cfg)
        self.children.append(child)
        return child

    def read(self, child, field="result"):
        proc, cfg = child
        path = Path(cfg[field])
        deadline = time.monotonic() + TIMEOUT
        while not path.exists():
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=5)
                pytest.fail(f"Child exited {proc.returncode} before {field}: {out}\n{err}")
            assert time.monotonic() < deadline, f"Child {field} handshake timed out"
            time.sleep(0.01)
        value = json.loads(path.read_text())
        assert Path(value.pop("ciw_file")).resolve() == Path(ciw.__file__).resolve()
        return value

    def query(self, action):
        child = self.spawn("query", action=action)
        value = self.read(child)
        self.join(child)
        return value

    def concurrent_queries(self):
        gate = self.directory / f"query-gate-{len(self.children)}"
        actions = ["recall", "replay"] * 3
        children = [self.spawn("query", action=action, gate=str(gate)) for action in actions]
        for child in children:
            assert self.read(child, "ready")["boundary"] == "query-gate"
        gate.write_text("go", encoding="utf-8")
        values = [self.read(child) for child in children]
        for child in children:
            self.join(child)
        for action, value in zip(actions, values):
            if action == "replay":
                assert value["replayed"] is True
        return values

    def join(self, child):
        proc, _ = child
        out, err = proc.communicate(timeout=TIMEOUT)
        assert proc.returncode == 0, f"Child exited {proc.returncode}: {out}\n{err}"

    def kill(self, child):
        proc, _ = child
        proc.send_signal(signal.SIGKILL)
        proc.communicate(timeout=TIMEOUT)
        assert proc.returncode == -signal.SIGKILL

    def count_executions(self):
        return len(self.executions.read_text().splitlines()) if self.executions.exists() else 0

    def close(self):
        for _, cfg in self.children:
            Path(cfg["stop"]).write_text("stop", encoding="utf-8")
        for proc, _ in self.children:
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=5)


@pytest.fixture
def children(tmp_path):
    group = Children(tmp_path)
    try:
        yield group
    finally:
        group.close()


def test_sigint_while_waiting_records_cancellation_before_worker_exit(children):
    holder = children.spawn("holder")
    assert children.read(holder, "ready")["boundary"] == "holding"
    worker = children.spawn("wait")
    assert children.read(worker, "ready")["boundary"] == "contended"
    worker[0].send_signal(signal.SIGINT)
    assert children.read(worker)["exception"] == "KeyboardInterrupt"
    assert worker[0].poll() is None
    assert children.query("probe") == {"job_available": True, "exclusive_available": False}
    values = children.concurrent_queries()
    assert {value["status"] for value in values} == {"cancelled"}
    assert children.count_executions() == 0
    assert holder[0].poll() is None


@pytest.mark.parametrize("exception", ["KeyboardInterrupt", "SystemExit", "OSError"])
def test_fault_after_real_kernel_acquisition_closes_unreturned_descriptor(children, exception):
    worker = children.spawn("kernel_fault", exception=exception)
    result = children.read(worker)
    assert result["exception"] == exception and result["injected"]
    assert worker[0].poll() is None
    assert not result["fault_fd_still_owns_resource"]
    assert children.query("probe") == {"job_available": True, "exclusive_available": True}
    expected = "failed" if exception == "OSError" else "cancelled"
    assert children.query("recall")["status"] == expected
    assert children.query("replay")["status"] == expected
    assert children.count_executions() == 0


@pytest.mark.parametrize("operation", ["ftruncate", "lseek", "write"])
@pytest.mark.parametrize("exception", ["KeyboardInterrupt", "OSError"])
def test_exclusive_metadata_fault_releases_both_leases(children, operation, exception):
    worker = children.spawn("metadata_fault", operation=operation, exception=exception)
    result = children.read(worker)
    assert result["exception"] == exception and result["injected"]
    assert worker[0].poll() is None
    assert children.query("probe") == {"job_available": True, "exclusive_available": True}
    expected = "failed" if exception == "OSError" else "cancelled"
    assert children.query("recall")["status"] == expected
    assert children.query("replay")["status"] == expected
    assert children.count_executions() == 0


@pytest.mark.parametrize("exception", ["OSError", "KeyboardInterrupt"])
def test_release_failure_preserves_completion_and_releases_job_lease(children, exception):
    worker = children.spawn("release_fault", exception=exception)
    result = children.read(worker)
    assert result["exception"] == exception and result["injected"]
    assert worker[0].poll() is None
    assert children.query("probe") == {"job_available": True, "exclusive_available": True}
    assert {value["status"] for value in children.concurrent_queries()} == {"completed"}
    assert children.count_executions() == 1


def test_failed_cancellation_record_releases_leases_and_recovers_as_worker_lost(children):
    worker = children.spawn("metadata_fault", operation="ftruncate", exception="KeyboardInterrupt",
                            finish_failure=True)
    result = children.read(worker)
    assert result["exception"] == "OSError"
    assert result["injected"] and result["finish_injected"]
    assert worker[0].poll() is None
    assert children.query("probe") == {"job_available": True, "exclusive_available": True}
    paths = list(children.retain.rglob("idem-*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["status"] == "running"
    values = children.concurrent_queries()
    assert {value["status"] for value in values} == {"failed"}
    assert {value["error"] for value in values} == {"worker_lost"}
    assert len({value["artifact_id"] for value in values}) == 1
    assert len(list(children.retain.rglob("failure-*.json"))) == 1
    assert children.count_executions() == 0


def test_close_after_effect_failure_does_not_close_a_reused_descriptor(children):
    worker = children.spawn("release_after_close")
    result = children.read(worker)
    assert result == {"exception": "OSError", "detached": True, "replacement_open": True}
    assert worker[0].poll() is None
    assert children.query("probe")["exclusive_available"] is True


@pytest.mark.parametrize("boundary,runs", [("acquired", 0), ("domain", 1), ("completed", 1)])
def test_sigkill_boundaries_recover_once_without_second_execution(children, boundary, runs):
    worker = children.spawn("crash", boundary=boundary)
    assert children.read(worker, "ready")["boundary"] == boundary
    before = "completed" if boundary == "completed" else "running"
    assert children.query("recall")["status"] == before
    assert children.query("replay")["status"] == before
    assert children.count_executions() == runs
    children.kill(worker)
    values = children.concurrent_queries()
    expected = "completed" if boundary == "completed" else "failed"
    assert {value["status"] for value in values} == {expected}
    if expected == "failed":
        assert {value["error"] for value in values} == {"worker_lost"}
        assert len({value["artifact_id"] for value in values}) == 1
        assert len(list(children.retain.rglob("failure-*.json"))) == 1
    else:
        assert list(children.retain.rglob("failure-*.json")) == []
    assert children.query("probe") == {"job_available": True, "exclusive_available": True}
    assert children.count_executions() == runs
