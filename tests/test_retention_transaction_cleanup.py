"""Real owner locks, caught interruptions, and recovery in separate processes.

Every faulted worker remains alive for independent descriptor/lock probes.
Each injection is bounded and occurs once; paired failures explicitly test
normal nested-finally exception precedence. No Windows qualification is implied.
Children import the same installed/source ciw as this self-contained test file.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import ciw
import pytest


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="Transaction lock qualification requires POSIX signals and no-follow support",
)
PACKAGE_PARENT = Path(ciw.__file__).resolve().parents[1]
TIMEOUT = 20

CHILD = r'''
import json, os, signal, sys, time
from pathlib import Path
import ciw
from ciw import retention, coordinator

signal.signal(signal.SIGINT, signal.default_int_handler)
cfg = json.loads(sys.argv[1])
owner = cfg.get("owner", "transaction-worker")
directory = retention._owner_dir(owner)
lock_path = directory / "sequence.lock"
ready, result_path, stop = map(Path, (cfg["ready"], cfg["result"], cfg["stop"]))
mode = cfg["mode"]
errors = {"KeyboardInterrupt": KeyboardInterrupt, "SystemExit": SystemExit,
          "OSError": OSError, "RuntimeError": RuntimeError}

def emit(path, value):
    value["ciw_file"] = ciw.__file__
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)

def wait_stop():
    deadline = time.monotonic() + 30
    while not stop.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("child stop deadline expired")
        time.sleep(0.01)

def inode(path):
    info = path.stat()
    return [info.st_dev, info.st_ino]

def same_file(fd, path):
    try:
        return [os.fstat(fd).st_dev, os.fstat(fd).st_ino] == inode(path)
    except OSError:
        return False

def exception_chain(exc):
    chain = []
    while exc is not None:
        chain.append(type(exc).__name__)
        exc = exc.__context__
    return chain

if mode == "holder":
    guard = retention._transaction(directory)
    with guard:
        os.write(guard.fd, b"stable owner lock")
        emit(ready, {"inode": inode(lock_path)})
        wait_stop()
elif mode == "probe":
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        acquired = retention._try_lock(fd)
        if acquired:
            retention._lock_release(fd)
        emit(result_path, {"available": acquired, "inode": inode(lock_path)})
    finally:
        os.close(fd)
elif mode == "recovery":
    store = retention.Store(owner)
    before = inode(lock_path)
    source = store.keep({"recovered": True}, kind="fixture", catalog={})
    assert store.native(source) == {"recovered": True}
    recipient = retention.Store("recovery-recipient")
    grant = coordinator.handoff(store, source, recipient.owner)["grant_id"]
    copied = coordinator.claim(recipient, grant)["artifact_id"]
    assert recipient.native(copied) == {"recovered": True}
    assert coordinator.claim(recipient, grant)["artifact_id"] == copied
    assert store.release(source)["released"]
    assert recipient.release(copied)["released"]
    name = coordinator._idem_name("lost-after-transaction-fault")
    with retention._transaction(directory):
        coordinator._store_job(store, name, {
            "schema": "ncp.coordinator-job.v1", "status": "running", "pid": 0,
            "operation": "ncp_domain_run", "request_digest": "synthetic-recovery",
            "authorizes_execution": False})
    recovered = coordinator.recover(store)
    assert len(recovered["jobs"]) == 1
    failure = recovered["jobs"][0]["artifact_id"]
    assert store.native(failure)["error"] == "worker_lost"
    assert coordinator.recover(store)["jobs"] == []
    assert store.release(failure)["released"]
    emit(result_path, {"keep_read_release_claim_recover": True,
                       "stable_inode": before == inode(lock_path),
                       "inode": inode(lock_path),
                       "stale_partial_exists": (directory / "stale.partial").exists()})
elif mode == "reentry":
    guard = retention._transaction(directory)
    guard.__enter__()
    original_fd = guard.fd
    def deadline(*_):
        raise TimeoutError("active reentry blocked")
    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(2)
    try:
        try:
            guard.__enter__()
            caught = None
        except BaseException as exc:
            caught = type(exc).__name__
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    emit(result_path, {"exception": caught, "same_descriptor": guard.fd == original_fd,
                       "inode": inode(lock_path)})
    try:
        wait_stop()
    finally:
        guard.__exit__(None, None, None)
elif mode == "reused_descriptor":
    guard = retention._transaction(directory)
    guard.__enter__()
    first_fd = guard.fd
    original_close = retention.os.close
    close_calls = 0
    def closed_then_failed(fd):
        global close_calls
        if fd == first_fd:
            close_calls += 1
        original_close(fd)
        if fd == first_fd:
            raise OSError("injected after successful close")
    retention.os.close = closed_then_failed
    try:
        try:
            guard.__exit__(None, None, None)
            caught = None
        except BaseException as exc:
            caught = type(exc).__name__
    finally:
        retention.os.close = original_close
    detached = getattr(guard, "fd", None) is None
    other_path = retention._owner_dir("other-owner") / "sequence.lock"
    replacement = os.open(other_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    if replacement != first_fd:
        os.dup2(replacement, first_fd)
        os.close(replacement)
        replacement = first_fd
    retention._lock_exclusive(replacement)
    try:
        try:
            guard.__exit__(None, None, None)
            repeated_error = None
        except BaseException as exc:
            repeated_error = type(exc).__name__
        emit(result_path, {"exception": caught, "close_calls": close_calls,
                           "detached": detached, "repeat_exception": repeated_error,
                           "replacement_open": same_file(replacement, other_path),
                           "inode": inode(lock_path)})
        wait_stop()
    finally:
        if same_file(replacement, other_path):
            try:
                retention._lock_release(replacement)
            finally:
                os.close(replacement)
elif mode == "symlink":
    target = retention._owner_dir("other-owner") / "sequence.lock"
    lock_path.symlink_to(target)
    reached_lock = False
    def refuse_followed_descriptor(fd):
        global reached_lock
        reached_lock = True
        # Bound the old unsafe implementation instead of blocking on the holder.
        raise AssertionError("symlink reached owner-lock acquisition")
    retention._lock_exclusive = refuse_followed_descriptor
    try:
        with retention._transaction(directory):
            raise AssertionError("symlink entered transaction body")
    except BaseException as exc:
        emit(result_path, {"exception": type(exc).__name__, "reached_lock": reached_lock,
                           "link_preserved": lock_path.is_symlink(),
                           "target_inode": inode(target), "target_bytes": target.read_text()})
    wait_stop()
else:
    fault = cfg.get("fault")
    failure_type = errors.get(cfg.get("exception"), OSError)
    transaction_fd = None
    unlocks = 0
    closes = 0
    injected = False
    close_injected = False
    entered = False
    original_lock, original_unlock = retention._lock_exclusive, retention._lock_release
    original_close, original_unlink = retention.os.close, Path.unlink
    partial = directory / "stale.partial"
    if fault == "partial":
        partial.write_text("stale publication", encoding="utf-8")
    def watched_lock(fd):
        global transaction_fd, injected
        transaction_fd = fd
        if mode == "wait":
            emit(ready, {"boundary": "waiting", "inode": inode(lock_path)})
        if fault == "before_lock":
            injected = True
            raise failure_type("injected before kernel acquisition")
        result = original_lock(fd)
        if fault == "after_lock":
            injected = True
            raise failure_type("injected after kernel acquisition")
        return result
    def watched_unlock(fd):
        global unlocks, injected
        unlocks += 1
        if fault == "unlock" and not injected:
            injected = True
            raise failure_type("injected before kernel unlock")
        return original_unlock(fd)
    def watched_close(fd):
        global closes, close_injected
        if fd == transaction_fd:
            closes += 1
        original_close(fd)
        if fd == transaction_fd and cfg.get("close_error") and not close_injected:
            close_injected = True
            raise errors[cfg["close_error"]]("injected after descriptor close")
    def watched_unlink(self, *args, **kwargs):
        global injected
        if self == partial and not injected:
            injected = True
            raise failure_type("injected stale partial cleanup")
        return original_unlink(self, *args, **kwargs)
    retention._lock_exclusive, retention._lock_release = watched_lock, watched_unlock
    retention.os.close = watched_close
    if fault == "partial":
        Path.unlink = watched_unlink
    guard = retention._transaction(directory)
    try:
        with guard:
            entered = True
            if cfg.get("body_error"):
                raise errors[cfg["body_error"]]("transaction body failure")
        chain = []
    except BaseException as exc:
        chain = exception_chain(exc)
    emit(result_path, {"chain": chain, "entered": entered, "injected": injected,
                       "close_injected": close_injected, "unlocks": unlocks, "closes": closes,
                       "descriptor_open": transaction_fd is not None and same_file(transaction_fd, lock_path),
                       "inode": inode(lock_path)})
    wait_stop()
'''


class Workers:
    def __init__(self, directory):
        self.directory = directory
        self.children = []

    def spawn(self, mode, **options):
        prefix = self.directory / str(len(self.children))
        config = dict(mode=mode, ready=str(prefix.with_suffix(".ready")),
                      result=str(prefix.with_suffix(".result")), stop=str(prefix.with_suffix(".stop")),
                      **options)
        env = dict(os.environ, NCP_RETAIN_DIR=str(self.directory / "retain"),
                   PYTHONPATH=str(PACKAGE_PARENT), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen([sys.executable, "-c", CHILD, json.dumps(config)],
                                cwd=self.directory, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = proc, config
        self.children.append(child)
        return child

    def read(self, child, field="result"):
        proc, config = child
        path = Path(config[field])
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

    def query(self, mode, **options):
        child = self.spawn(mode, **options)
        value = self.read(child)
        self.join(child)
        return value

    def join(self, child):
        proc, _ = child
        out, err = proc.communicate(timeout=TIMEOUT)
        assert proc.returncode == 0, f"Child exited {proc.returncode}: {out}\n{err}"

    def stop(self, child):
        Path(child[1]["stop"]).write_text("stop", encoding="utf-8")
        self.join(child)

    def assert_recovery(self, child, inode):
        assert child[0].poll() is None
        # Fail here before attempting any potentially blocking recovery work.
        assert self.query("probe") == {"available": True, "inode": inode}
        assert self.query("recovery") == {
            "keep_read_release_claim_recover": True, "stable_inode": True,
            "inode": inode, "stale_partial_exists": False}
        assert child[0].poll() is None

    def close(self):
        for _, config in self.children:
            Path(config["stop"]).write_text("stop", encoding="utf-8")
        for proc, _ in self.children:
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=5)


@pytest.fixture
def workers(tmp_path):
    group = Workers(tmp_path)
    try:
        yield group
    finally:
        group.close()


@pytest.mark.parametrize("exception", ["KeyboardInterrupt", "SystemExit", "OSError"])
def test_post_kernel_acquisition_exception_closes_without_unlocking(workers, exception):
    child = workers.spawn("fault", fault="after_lock", exception=exception)
    result = workers.read(child)
    assert result["chain"] == [exception] and result["injected"]
    assert not result["entered"] and not result["descriptor_open"]
    assert result["unlocks"] == 0 and result["closes"] == 1
    workers.assert_recovery(child, result["inode"])


def test_unacquired_lock_failure_closes_attempt_without_unlock(workers):
    child = workers.spawn("fault", fault="before_lock", exception="OSError")
    result = workers.read(child)
    assert result["chain"] == ["OSError"] and result["injected"]
    assert not result["entered"] and not result["descriptor_open"]
    assert result["unlocks"] == 0 and result["closes"] == 1
    workers.assert_recovery(child, result["inode"])


def test_sigint_waiter_closes_attempt_and_preserves_other_holder(workers):
    import signal
    holder = workers.spawn("holder")
    before = workers.read(holder, "ready")
    child = workers.spawn("wait")
    assert workers.read(child, "ready")["boundary"] == "waiting"
    assert workers.query("probe") == {"available": False, "inode": before["inode"]}
    child[0].send_signal(signal.SIGINT)
    result = workers.read(child)
    assert result["chain"] == ["KeyboardInterrupt"]
    assert not result["entered"] and not result["descriptor_open"]
    assert result["unlocks"] == 0 and result["closes"] == 1
    assert workers.query("probe") == {"available": False, "inode": before["inode"]}
    assert holder[0].poll() is None and child[0].poll() is None
    workers.stop(holder)
    workers.assert_recovery(child, result["inode"])


@pytest.mark.parametrize("exception", ["KeyboardInterrupt", "OSError"])
def test_interrupted_stale_partial_cleanup_releases_owner_lock(workers, exception):
    child = workers.spawn("fault", fault="partial", exception=exception)
    result = workers.read(child)
    assert result["chain"] == [exception] and result["injected"]
    assert not result["entered"] and not result["descriptor_open"]
    assert result["unlocks"] == 0 and result["closes"] == 1
    workers.assert_recovery(child, result["inode"])


@pytest.mark.parametrize("exception", ["OSError", "KeyboardInterrupt"])
def test_unlock_failure_before_effect_still_closes_and_recovers(workers, exception):
    child = workers.spawn("fault", fault="unlock", exception=exception)
    result = workers.read(child)
    assert result["chain"] == [exception] and result["injected"] and result["entered"]
    assert not result["descriptor_open"]
    assert result["unlocks"] == 1 and result["closes"] == 1
    workers.assert_recovery(child, result["inode"])


@pytest.mark.parametrize("body", ["RuntimeError", "KeyboardInterrupt"])
def test_body_exception_propagates_when_cleanup_succeeds(workers, body):
    child = workers.spawn("fault", body_error=body)
    result = workers.read(child)
    assert result["chain"] == [body] and result["entered"]
    assert not result["descriptor_open"]
    workers.assert_recovery(child, result["inode"])


@pytest.mark.parametrize("body,cleanup", [("KeyboardInterrupt", "OSError"), ("RuntimeError", "KeyboardInterrupt")])
def test_cleanup_exception_wins_with_body_in_context(workers, body, cleanup):
    child = workers.spawn("fault", body_error=body, fault="unlock", exception=cleanup)
    result = workers.read(child)
    assert result["chain"] == [cleanup, body]
    assert result["injected"] and not result["descriptor_open"]
    workers.assert_recovery(child, result["inode"])


@pytest.mark.parametrize("fault", ["after_lock", "unlock"])
def test_close_error_wins_with_prior_cancellation_in_context_without_retry(workers, fault):
    child = workers.spawn("fault", fault=fault, exception="KeyboardInterrupt", close_error="OSError")
    result = workers.read(child)
    assert result["chain"] == ["OSError", "KeyboardInterrupt"]
    assert result["injected"] and result["close_injected"]
    assert result["closes"] == 1 and not result["descriptor_open"]
    workers.assert_recovery(child, result["inode"])


def test_repeated_exit_cannot_unlock_or_close_reused_descriptor_of_other_owner(workers):
    child = workers.spawn("reused_descriptor")
    result = workers.read(child)
    assert result["exception"] == "OSError" and result["close_calls"] == 1
    assert result["detached"] and result["replacement_open"]
    assert result["repeat_exception"] is None
    assert workers.query("probe", owner="other-owner")["available"] is False
    workers.assert_recovery(child, result["inode"])
    workers.stop(child)
    assert workers.query("probe", owner="other-owner")["available"] is True


def test_active_reentry_refuses_without_losing_original_lock(workers):
    child = workers.spawn("reentry")
    result = workers.read(child)
    assert result["exception"] == "RuntimeError" and result["same_descriptor"]
    assert workers.query("probe") == {"available": False, "inode": result["inode"]}
    workers.stop(child)
    assert workers.query("probe") == {"available": True, "inode": result["inode"]}
    assert workers.query("recovery")["keep_read_release_claim_recover"]


def test_symlink_lock_refuses_before_acquisition_and_preserves_target_holder(workers):
    holder = workers.spawn("holder", owner="other-owner")
    before = workers.read(holder, "ready")
    child = workers.spawn("symlink")
    result = workers.read(child)
    assert result["exception"] == "OSError"
    assert not result["reached_lock"] and result["link_preserved"]
    assert result["target_inode"] == before["inode"]
    assert result["target_bytes"] == "stable owner lock"
    assert workers.query("probe", owner="other-owner") == {"available": False, "inode": before["inode"]}
    assert holder[0].poll() is None
