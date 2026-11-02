"""Owned-process cleanup with real children and a surviving SIGINT receiver.

POSIX process/session qualification only. Faults after real signal/wait/thread
operations preserve their diagnostics; they do not prove provider rollback.
The test imports the active ciw package and also runs outside a source checkout.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import ciw
import pytest
from ciw.adapters import subprocess as runtime

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Real SIGINT/session cleanup requires POSIX")
CLOSED = "import os,time; os.close(1); os.close(2); time.sleep(20)"


def invoke(tmp_path, program=CLOSED):
    return runtime._bounded_process([sys.executable, "-I", "-c", program],
                                    cwd=tmp_path, timeout=20, limit=4096)


def causes(error):
    seen, pending = set(), [error]
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        yield value
        if value.__context__ is not None:
            pending.append(value.__context__)
        if value.__cause__ is not None:
            pending.append(value.__cause__)
        if isinstance(value, BaseExceptionGroup):
            pending.extend(value.exceptions)


@contextmanager
def owned_children(monkeypatch):
    state = SimpleNamespace(processes=[], readers=[], waits={}, stops=[],
                            popen=subprocess.Popen, thread=threading.Thread,
                            stop=runtime._stop)

    def popen(*args, **kwargs):
        process = state.popen(*args, **kwargs)
        state.processes.append(process)
        state.waits[process] = process.wait
        return process

    def thread(*args, **kwargs):
        reader = state.thread(*args, **kwargs)
        state.readers.append(reader)
        return reader

    def stop(process):
        assert process in state.processes  # No signal to another caller's child.
        state.stops.append(process.pid)
        state.stop(process)

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime.threading, "Thread", thread)
    monkeypatch.setattr(runtime, "_stop", stop)
    try:
        yield state
    finally:
        # Teardown owns cleanup of deliberately uncertain fault cases too.
        for process in state.processes:
            if process.poll() is None:
                state.stop(process)
            state.waits[process](timeout=5)
        for reader in state.readers:
            if reader.ident is not None:
                state.thread.join(reader, timeout=5)
        for process in state.processes:
            process.stdout.close()
            process.stderr.close()


@pytest.fixture
def ownership(monkeypatch):
    with owned_children(monkeypatch) as state:
        yield state


def assert_reaped(ownership):
    assert len(ownership.processes) == 1
    process = ownership.processes[0]
    assert process.returncode == -signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    assert process.stdout.closed and process.stderr.closed
    assert not any(reader.is_alive() for reader in ownership.readers)


@pytest.mark.parametrize("stage,index,after_effect", [
    ("construct", 0, False), ("construct", 1, False),
    ("start", 0, False), ("start", 1, False), ("start", 0, True),
])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, OSError])
def test_reader_setup_failure_stops_and_reaps(tmp_path, monkeypatch, ownership, stage, index, after_effect, error_type):
    primary = error_type("reader setup fault")
    constructor = runtime.threading.Thread
    count = 0

    def thread(*args, **kwargs):
        nonlocal count
        selected = count == index
        count += 1
        if selected and stage == "construct":
            raise primary
        reader = constructor(*args, **kwargs)
        if selected and stage == "start":
            original = reader.start
            def start():
                if after_effect:
                    original()
                raise primary
            reader.start = start
        return reader

    monkeypatch.setattr(runtime.threading, "Thread", thread)
    with pytest.raises(error_type) as caught:
        invoke(tmp_path)
    assert caught.value is primary
    assert_reaped(ownership)
    assert ownership.stops == [ownership.processes[0].pid]


def fail_wait(monkeypatch, ownership, primary, cleanup_error=None):
    original = runtime.subprocess.Popen

    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        wait = process.wait
        calls = 0
        def failing_wait(timeout=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                # The owned child is alive, but both readers have observed EOF.
                for reader in ownership.readers:
                    ownership.thread.join(reader, timeout=5)
                    assert not reader.is_alive()
                raise primary
            result = wait(timeout=timeout)
            if cleanup_error is not None:
                raise cleanup_error
            return result
        process.wait = failing_wait
        return process
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, OSError])
def test_wait_failure_after_eof_preserves_primary(tmp_path, monkeypatch, ownership, error_type):
    primary = error_type("wait fault")
    fail_wait(monkeypatch, ownership, primary)
    with pytest.raises(error_type) as caught:
        invoke(tmp_path)
    assert caught.value is primary
    assert_reaped(ownership)


@pytest.mark.parametrize("cleanup_stage", ["stop", "wait", "join", "close"])
def test_cleanup_after_effect_failure_retains_primary_and_finishes_other_cleanup(tmp_path, monkeypatch, ownership, cleanup_stage):
    primary = KeyboardInterrupt("owned wait cancelled")
    cleanup = OSError("cleanup failed after effect: " + cleanup_stage)
    fail_wait(monkeypatch, ownership, primary, cleanup if cleanup_stage == "wait" else None)
    if cleanup_stage == "stop":
        original = runtime._stop
        def stop(process):
            original(process)
            raise cleanup
        monkeypatch.setattr(runtime, "_stop", stop)
    elif cleanup_stage == "join":
        original = runtime.threading.Thread
        def thread(*args, **kwargs):
            reader = original(*args, **kwargs)
            join = reader.join
            if len(ownership.readers) == 1:
                def joined(timeout=None):
                    join(timeout=timeout)
                    raise cleanup
                reader.join = joined
            return reader
        monkeypatch.setattr(runtime.threading, "Thread", thread)
    elif cleanup_stage == "close":
        original = runtime.subprocess.Popen
        def popen(*args, **kwargs):
            process = original(*args, **kwargs)
            close = process.stdout.close
            def closed():
                close()
                raise cleanup
            process.stdout.close = closed
            return process
        monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    with pytest.raises(OSError) as caught:
        invoke(tmp_path)
    assert caught.value is cleanup
    assert primary in list(causes(caught.value))
    assert_reaped(ownership)
    assert ownership.stops == [ownership.processes[0].pid]
    if cleanup_stage == "close":
        # The failed close already took effect; teardown need not repeat it.
        ownership.processes[0].stdout.close = lambda: None


def test_success_and_error_after_reap_never_resignal_ended_ownership(tmp_path, monkeypatch, ownership):
    # A successful wait releases the PID; a later join fault must not signal it.
    original = runtime.threading.Thread
    failure = OSError("join fault after child reaped")
    def thread(*args, **kwargs):
        reader = original(*args, **kwargs)
        join = reader.join
        injected = False
        def joined(timeout=None):
            nonlocal injected
            join(timeout=timeout)
            if not injected:
                injected = True
                raise failure
        reader.join = joined
        return reader
    monkeypatch.setattr(runtime.threading, "Thread", thread)
    with pytest.raises(OSError) as caught:
        invoke(tmp_path, "print('finished')")
    assert caught.value is failure
    assert ownership.stops == []
    assert ownership.processes[0].returncode == 0
    assert all(pipe.closed for pipe in (ownership.processes[0].stdout, ownership.processes[0].stderr))
    assert not any(reader.is_alive() for reader in ownership.readers)


def test_success_preserves_output_without_signaling_reaped_child(tmp_path, ownership):
    assert invoke(tmp_path, "import sys; print('retained'); print('diagnostic',file=sys.stderr)") == (0, b"retained\n")
    assert ownership.stops == []
    assert ownership.processes[0].stdout.closed and ownership.processes[0].stderr.closed
    assert not any(reader.is_alive() for reader in ownership.readers)


def test_reader_io_failure_is_reported_after_stop_and_reap(tmp_path, monkeypatch, ownership):
    failure = OSError("reader I/O failed")
    original = runtime.subprocess.Popen
    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        def read1(size):
            raise failure
        process.stdout.read1 = read1
        return process
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    with pytest.raises(OSError) as caught:
        invoke(tmp_path)
    assert caught.value is failure
    assert_reaped(ownership)
    assert ownership.stops == [ownership.processes[0].pid]


def test_failed_stop_and_unconfirmed_reap_remain_uncertain_without_retry(tmp_path, monkeypatch, ownership):
    primary = KeyboardInterrupt("cancelled before stop failure")
    failure = OSError("stop failed before effect")
    fail_wait(monkeypatch, ownership, primary)
    attempts = []
    def stop(process):
        assert process in ownership.processes
        attempts.append(process.pid)
        raise failure
    monkeypatch.setattr(runtime, "_stop", stop)
    unrelated = ownership.popen([sys.executable, "-I", "-c", "import time; time.sleep(20)"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    try:
        # Keep the production five-second cleanup deadline. This path cannot
        # claim cancellation completed: signal failed and the child is alive.
        with pytest.raises(runtime.RuntimeCleanupUncertain) as caught:
            invoke(tmp_path)
        assert caught.value.code == "RUNTIME_CLEANUP_UNCERTAIN"
        assert primary in list(causes(caught.value))
        assert failure in list(causes(caught.value))
        assert any(isinstance(exc, subprocess.TimeoutExpired) for exc in causes(caught.value))
        process = ownership.processes[0]
        assert caught.value.process is process
        assert process.poll() is None and unrelated.poll() is None
        assert attempts == [process.pid]
        assert process.stdout.closed and process.stderr.closed
        assert not any(reader.is_alive() for reader in ownership.readers)
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)


def bound_fixture(tmp_path, adapter_type=runtime.PinnedSubprocessAdapter):
    root = tmp_path / "bound-runtime"
    (root / "src/example").mkdir(parents=True)
    (root / "src/example/__init__.py").write_text("")
    (root / "src/example/endpoint.py").write_text("print('fixture')\n")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=CIW Test", "-c", "user.email=ciw@example.test", "commit", "-qm", "fixture")
    return adapter_type(root, git("rev-parse", "HEAD"), "example.endpoint")


@pytest.mark.parametrize("route,outer_cleanup", [("run", None), ("source", None),
                                                  ("run", "TemporaryFile"), ("run", "TemporaryDirectory")])
def test_bound_adapter_holds_uncertain_ownership_before_any_followup_child(tmp_path, monkeypatch, route, outer_cleanup):
    class VirtualGit(runtime.PinnedSubprocessAdapter):
        fail_source = False
        def _git(self, *args):
            if self.fail_source:
                return invoke(self.repository_root)
            return super()._git(*args)
    adapter = bound_fixture(tmp_path, VirtualGit if route == "source" else runtime.PinnedSubprocessAdapter)
    adapter.fail_source = route == "source"
    primary = KeyboardInterrupt("cancelled bound operation")
    stop_failure = OSError("stop denied before effect")
    outer_failure = OSError("enclosing cleanup failed after effect")
    with monkeypatch.context() as patch, owned_children(patch) as ownership:
        fail_wait(patch, ownership, primary)
        attempts = []
        def stop(process):
            attempts.append(process.pid)
            raise stop_failure
        patch.setattr(runtime, "_stop", stop)
        if outer_cleanup is not None:
            original = getattr(runtime.tempfile, outer_cleanup)
            class FailedExit:
                def __init__(self, *args, **kwargs):
                    self.manager = original(*args, **kwargs)
                def __enter__(self):
                    return self.manager.__enter__()
                def __exit__(self, *args):
                    self.manager.__exit__(*args)
                    raise outer_failure
            patch.setattr(runtime.tempfile, outer_cleanup, FailedExit)
        expected = OSError if outer_cleanup else runtime.RuntimeCleanupUncertain
        with pytest.raises(expected) as caught:
            if route == "source":
                adapter.runtime_identity()
            else:
                adapter._run(CLOSED, [])
        chain = list(causes(caught.value))
        held = adapter._cleanup_uncertain
        assert isinstance(held, runtime.RuntimeCleanupUncertain)
        assert held in chain and primary in chain and stop_failure in chain
        if outer_cleanup:
            assert caught.value is outer_failure
        process = ownership.processes[0]
        assert held.process is process and process.poll() is None
        assert attempts == [process.pid]
        def forbidden(*args, **kwargs):
            raise AssertionError("uncertain binding must not launch another child")
        patch.setattr(runtime.subprocess, "Popen", forbidden)
        calls = [adapter.runtime_identity, adapter._verify_source, adapter._probe,
                 lambda: adapter._run("print('forbidden')", []),
                 lambda: adapter.invoke("evaluate.v1", {}),
                 lambda: runtime.PinnedSubprocessAdapter._git(adapter, "rev-parse", "HEAD"),
                 lambda: runtime.PinnedSubprocessAdapter.__init__(adapter, adapter.repository_root,
                     adapter.revision, adapter.module)]
        for call in calls:
            with pytest.raises(runtime.RuntimeCleanupUncertain) as refused:
                call()
            assert refused.value.__cause__ is held
            assert refused.value.to_dict() == {
                "code": "RUNTIME_CLEANUP_UNCERTAIN", "message": "Bound runtime cleanup could not be confirmed"}
        assert process.poll() is None and len(ownership.processes) == 1
        # Even explicit test/operator cleanup does not silently clear the hold.
        ownership.stop(process)
        ownership.waits[process](timeout=5)
        with pytest.raises(runtime.RuntimeCleanupUncertain):
            adapter.runtime_identity()
        assert adapter._cleanup_uncertain is held


def test_confirmed_reap_with_cleanup_error_does_not_install_false_hold(tmp_path, monkeypatch):
    adapter = bound_fixture(tmp_path)
    primary = KeyboardInterrupt("cancelled bound operation")
    failure = OSError("signal raised after effect")
    with monkeypatch.context() as patch, owned_children(patch) as ownership:
        fail_wait(patch, ownership, primary)
        original = runtime._stop
        def stop(process):
            original(process)
            raise failure
        patch.setattr(runtime, "_stop", stop)
        with pytest.raises(OSError) as caught:
            adapter._run(CLOSED, [])
        assert caught.value is failure and primary in list(causes(failure))
        assert adapter._cleanup_uncertain is None
        assert_reaped(ownership)
    assert adapter._run("print('followup')", []) == (0, b"followup\n")


def test_caller_uncertainty_context_does_not_hold_completed_source_probe(tmp_path):
    adapter = bound_fixture(tmp_path)
    foreign = runtime.RuntimeCleanupUncertain()
    try:
        raise foreign
    except runtime.RuntimeCleanupUncertain:
        with pytest.raises(runtime.AdapterRefusal) as caught:
            adapter._git("definitely-not-a-git-command")
    assert caught.value.code == "SOURCE_PIN_MISMATCH"
    assert foreign in list(causes(caught.value))
    assert adapter._cleanup_uncertain is None
    assert adapter._run("print('followup')", []) == (0, b"followup\n")


def test_caller_uncertainty_context_does_not_hold_confirmed_reap(tmp_path, monkeypatch):
    adapter = bound_fixture(tmp_path)
    foreign = runtime.RuntimeCleanupUncertain()
    primary = KeyboardInterrupt("this operation cancelled")
    cleanup = OSError("this signal failed after effect")
    with monkeypatch.context() as patch, owned_children(patch) as ownership:
        fail_wait(patch, ownership, primary)
        original = runtime._stop
        def stop(process):
            original(process)
            raise cleanup
        patch.setattr(runtime, "_stop", stop)
        try:
            raise foreign
        except runtime.RuntimeCleanupUncertain:
            with pytest.raises(OSError) as caught:
                adapter._run(CLOSED, [])
        assert caught.value is cleanup
        assert primary in list(causes(cleanup)) and foreign in list(causes(cleanup))
        assert_reaped(ownership)
        assert adapter._cleanup_uncertain is None
    assert adapter._run("print('followup')", []) == (0, b"followup\n")


@pytest.mark.parametrize("probe", ["_verify_source", "_probe"])
def test_binding_initializes_hold_before_virtual_probes(tmp_path, monkeypatch, probe):
    failure = runtime.RuntimeCleanupUncertain()
    def uncertain(self):
        assert self._cleanup_uncertain is None
        raise failure
    class Binding(runtime.PinnedSubprocessAdapter):
        def _verify_source(self):
            return "fixture-tree"
        def _executable_digest(self):
            return "fixture-executable"
    monkeypatch.setattr(Binding, probe, uncertain)
    (tmp_path / "src").mkdir()
    adapter = object.__new__(Binding)
    with pytest.raises(runtime.RuntimeCleanupUncertain) as caught:
        adapter.__init__(tmp_path, "a" * 40, "example.endpoint")
    assert caught.value is failure and adapter._cleanup_uncertain is failure
    with pytest.raises(runtime.RuntimeCleanupUncertain):
        adapter.runtime_identity()


DRIVER = r'''
import json, os, signal, subprocess, sys, threading, time
from pathlib import Path
from unittest.mock import patch
from ciw.adapters import subprocess as runtime
signal.signal(signal.SIGINT, signal.default_int_handler)
root = Path(sys.argv[1])
processes, readers, waits = [], [], []
original_popen, original_thread = subprocess.Popen, threading.Thread

def popen(*args, **kwargs):
    process = original_popen(*args, **kwargs)
    processes.append(process)
    wait = process.wait
    waits.append(wait)
    def waiting(timeout=None):
        if timeout == 20:
            for reader in readers:
                reader.join(timeout=5)
                assert not reader.is_alive()
            (root/'ready.json').write_text(json.dumps({'pid':process.pid, 'readers_eof':True}))
        return wait(timeout=timeout)
    process.wait = waiting
    return process

def thread(*args, **kwargs):
    reader = original_thread(*args, **kwargs)
    readers.append(reader)
    return reader

try:
    with patch.object(runtime.subprocess, 'Popen', popen), patch.object(runtime.threading, 'Thread', thread):
        try:
            runtime._bounded_process([sys.executable,'-I','-c',
                'import os,time; os.close(1); os.close(2); time.sleep(20)'],
                cwd=root,timeout=20,limit=4096)
        except KeyboardInterrupt:
            cancelled = True
        else:
            cancelled = False
    process = processes[0]
    try:
        observed = os.waitpid(process.pid, os.WNOHANG)
        reaped = False
    except ChildProcessError:
        reaped = True
    followup = runtime._bounded_process([sys.executable,'-I','-c',"print('next')"],
        cwd=root,timeout=5,limit=4096)
    report = {'cancelled':cancelled,'returncode':process.returncode,'reaped_before_followup':reaped,
              'readers_stopped':not any(t.is_alive() for t in readers),
              'pipes_closed':process.stdout.closed and process.stderr.closed,
              'followup':[followup[0],followup[1].decode()]}
    (root/'result.json').write_text(json.dumps(report))
    deadline=time.monotonic()+15
    while not (root/'release').exists():
        if time.monotonic()>deadline: raise TimeoutError('test parent did not release driver')
        time.sleep(.01)
finally:
    for process,wait in zip(processes,waits):
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL)
        wait(timeout=5)
        process.stdout.close();process.stderr.close()
'''


def test_real_sigint_after_closed_pipes_reaps_before_alive_parent_followup(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(Path(ciw.__file__).resolve().parents[1])}
    driver = subprocess.Popen([sys.executable, "-c", DRIVER, str(tmp_path)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def read(name):
        path = tmp_path / name
        deadline = time.monotonic() + 15
        while not path.exists():
            assert driver.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        return json.loads(path.read_text())
    try:
        assert read("ready.json")["readers_eof"] is True
        driver.send_signal(signal.SIGINT)
        result = read("result.json")
        assert driver.poll() is None  # Owner remains alive; process exit is not cleanup.
        assert result == {"cancelled": True, "returncode": -signal.SIGKILL,
                          "reaped_before_followup": True, "readers_stopped": True,
                          "pipes_closed": True, "followup": [0, "next\n"]}
    finally:
        (tmp_path / "release").touch()
        try:
            out, err = driver.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            driver.kill()
            out, err = driver.communicate(timeout=5)
        assert driver.returncode == 0, out + err
