"""Real local processes qualify queue leases while failed workers remain alive.

Only synthetic host dispatchers run. The queue deliberately does not migrate or
alter the existing coordinator and retention formats. No hosted/storage/network
qualification is implied by these POSIX filesystem tests.
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

from ciw.store_queue import QueueLimits, QueueRefusal, StoreQueue


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="Queue lifecycle qualification requires POSIX signals and no-follow kernel leases",
)
PACKAGE_PARENT = Path(ciw.__file__).resolve().parents[1]

CHILD = r'''
import json, os, signal, sys, time
from pathlib import Path
from ciw import coordinator, retention, store_queue
from ciw.store_queue import StoreQueue
signal.signal(signal.SIGINT, signal.default_int_handler)
cfg = json.loads(sys.argv[1])
root = Path(cfg['root'])
directory = root / 'queue'
mode, fault = cfg['mode'], cfg.get('fault')
ready, result, stop, proceed = map(Path, (cfg['ready'], cfg['result'], cfg['stop'], cfg['proceed']))
queue_path = directory / 'queue.lock'
job_path = directory / (cfg.get('job_id', 'unused') + '.lease')
other_path = root / 'unrelated.lock'
executions = root / 'executions'

def emit(path, value):
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)

def wait(path):
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('synthetic handshake expired')
        time.sleep(0.005)

def same(fd, path):
    try:
        a, b = os.fstat(fd), path.stat()
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    except OSError:
        return False

def available(path):
    lease = coordinator._Lease(path)
    try:
        return lease.try_acquire()
    finally:
        lease.release()

if mode == 'probe':
    value = {'queue': available(queue_path)}
    if not cfg.get('only_queue'):
        value.update(job=available(job_path), unrelated=available(other_path))
    emit(result, value)
    sys.exit(0)

def create_queue():
    return StoreQueue(directory, owner='host-a', principal='principal-a', generation='generation-a',
                      operations=['operation.one'], authorize=lambda *args: None)

q = None if mode == 'init_fault' else create_queue()
if fault == 'partial_cleanup':
    (directory / 'queue.json.partial').write_bytes(b'incomplete publication')
if cfg.get('gate'):
    emit(ready, {'boundary': 'gate'})
    wait(Path(cfg['gate']))
if mode == 'recover':
    emit(result, q.recover())
    sys.exit(0)
if mode == 'enqueue':
    emit(result, q.enqueue('operation.one', {'value': 1}, 'attempt'))
    sys.exit(0)
if mode == 'replay':
    emit(result, q.enqueue('operation.one', {'value': 1}, 'attempt'))
    sys.exit(0)

injected = armed = False
replacement = None
queue_close_count = 0
original_publish, original_unlock = store_queue._publish, coordinator._lock_release
original_close, original_try_lock, original_unlink = os.close, coordinator._try_lock, Path.unlink
phase = cfg.get('phase', 'running')
primary = KeyboardInterrupt('synthetic cancellation') if fault in (
    'unlock_interrupt', 'publish_interrupt_after', 'paired_cleanup',
    'acquire_interrupt', 'queue_acquire_interrupt') else OSError('synthetic I/O failure')

def publish(directory, name, raw):
    global injected, armed
    state = json.loads(raw)
    matched = not state['jobs'] if phase == 'init' else any(job['status'] == phase for job in state['jobs'])
    if matched:
        armed = True
    if matched and fault == 'publish_before' and not injected:
        injected = True
        raise primary
    original_publish(directory, name, raw)
    if matched and fault in ('publish_after', 'publish_interrupt_after', 'paired_cleanup') and not injected:
        injected = True
        raise primary

def unlock(fd):
    global injected
    if same(fd, queue_path) and armed:
        if fault == 'unlock_and_close_before_effect':
            raise OSError('synthetic unlock failed before close')
        if fault == 'paired_cleanup':
            raise OSError('synthetic enclosing cleanup failure')
        if not injected and fault in ('unlock_error', 'unlock_after', 'unlock_interrupt'):
            injected = True
            if fault == 'unlock_after':
                original_unlock(fd)
            raise primary
    if same(fd, job_path) and fault == 'job_unlock_error' and not injected and armed:
        injected = True
        raise primary
    return original_unlock(fd)

def close(fd):
    global injected, replacement, queue_close_count
    matched = same(fd, queue_path)
    if matched:
        queue_close_count += 1
    if matched and armed and fault == 'unlock_and_close_before_effect' and not injected:
        injected = True
        raise primary
    original_close(fd)
    if matched and armed and fault == 'close_after_effect' and not injected:
        injected = True
        replacement = os.open(other_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        if replacement != fd:
            os.dup2(replacement, fd)
            original_close(replacement)
            replacement = fd
        assert original_try_lock(replacement)
        raise primary

def acquire(fd):
    global injected
    got = original_try_lock(fd)
    target = queue_path if fault == 'queue_acquire_interrupt' else job_path
    if got and fault in ('queue_acquire_interrupt', 'acquire_interrupt') and not injected and same(fd, target):
        injected = True
        raise primary
    return got

def unlink(path, *args, **kwargs):
    global injected
    if fault == 'partial_cleanup' and path == directory / 'queue.json.partial' and not injected:
        injected = True
        raise OSError('synthetic partial cleanup failure')
    return original_unlink(path, *args, **kwargs)

store_queue._publish = publish
coordinator._lock_release = unlock
coordinator._try_lock = acquire
os.close = close
Path.unlink = unlink

def dispatch(operation, arguments, fence):
    fd = os.open(executions, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, (str(os.getpid()) + '\n').encode())
    finally:
        os.close(fd)
    if mode == 'live_execute':
        emit(ready, {'boundary': 'dispatch', 'fence': fence})
        wait(proceed)
    return {'synthetic': True, 'fence': fence}

try:
    if mode == 'init_fault':
        q = create_queue()
    if mode == 'claim':
        claim = q.claim_next('worker-' + str(os.getpid()))
        emit(result, {'claimed': claim is not None, 'fence': claim.fence if claim else None})
        if claim is not None:
            wait(stop)
            q.abandon(claim)
        sys.exit(0)
    if mode == 'init_fault':
        answer = {'initialized': True}
    elif mode == 'live_claim':
        claim = q.claim_next('worker-' + str(os.getpid()))
        emit(ready, {'boundary': 'claim', 'fence': claim.fence})
        wait(proceed)
        answer = q.complete(claim, {'synthetic': True})
    elif mode == 'fault_enqueue':
        answer = q.enqueue('operation.one', {'value': 2}, 'new-attempt')
    elif mode == 'fault_recover':
        answer = q.recover()
    else:
        answer = q.execute_one('worker-' + str(os.getpid()), dispatch)
    emit(result, {'ok': answer, 'claims': len(q._claims), 'queue_close_count': queue_close_count})
except BaseException as exc:
    if mode == 'claim' and isinstance(exc, SystemExit) and exc.code == 0:
        raise
    chain, current = [], exc
    while current is not None and len(chain) < 8:
        chain.append({'type': type(current).__name__, 'message': str(current)})
        current = current.__context__
    emit(result, {'error': type(exc).__name__, 'chain': chain, 'claims': len(q._claims) if q else 0,
                  'replacement': replacement, 'queue_close_count': queue_close_count})
finally:
    store_queue._publish, coordinator._lock_release = original_publish, original_unlock
    coordinator._try_lock, os.close, Path.unlink = original_try_lock, original_close, original_unlink
# Remain alive: process termination must not be what makes probe locks available.
wait(stop)
if replacement is not None:
    original_close(replacement)
'''


class Processes:
    def __init__(self, root):
        self.root, self.workers, self.counter = root, [], 0

    def start(self, mode, **options):
        self.counter += 1
        prefix = self.root / str(self.counter)
        cfg = {"root": str(self.root), "mode": mode,
               **{name: str(prefix.with_suffix("." + name)) for name in ("ready", "result", "stop", "proceed")},
               **options}
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(PACKAGE_PARENT))
        process = subprocess.Popen([sys.executable, "-c", CHILD, json.dumps(cfg)],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.workers.append((process, cfg))
        return process, cfg

    @staticmethod
    def read(worker, field="result"):
        process, cfg = worker
        path = Path(cfg[field])
        deadline = time.monotonic() + 20
        while not path.exists():
            if process.poll() is not None:
                out, err = process.communicate(timeout=2)
                pytest.fail(f"child exited {process.returncode}: {out}\n{err}")
            assert time.monotonic() < deadline, "synthetic child handshake timeout"
            time.sleep(0.005)
        return json.loads(path.read_bytes())

    def once(self, mode, **options):
        worker = self.start(mode, **options)
        result = self.read(worker)
        out, err = worker[0].communicate(timeout=20)
        assert worker[0].returncode == 0, out + err
        return result

    def cleanup(self):
        for process, cfg in self.workers:
            Path(cfg["stop"]).touch()
            Path(cfg["proceed"]).touch()
        for process, _cfg in self.workers:
            if process.poll() is None:
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)


@pytest.fixture
def processes(tmp_path):
    manager = Processes(tmp_path)
    yield manager
    manager.cleanup()


def setup_queue(tmp_path):
    queue = StoreQueue(tmp_path / "queue", owner="host-a", principal="principal-a", generation="generation-a",
                       operations=["operation.one"], authorize=lambda *args: None)
    job = queue.enqueue("operation.one", {"value": 1}, "attempt")
    return queue, job


def executions(tmp_path):
    path = tmp_path / "executions"
    return len(path.read_text().splitlines()) if path.exists() else 0


@pytest.mark.parametrize("fault,committed", [
    ("publish_before", False), ("publish_after", True), ("publish_interrupt_after", True),
    ("unlock_error", True), ("unlock_after", True), ("unlock_interrupt", True),
    ("close_after_effect", True), ("paired_cleanup", True),
    ("acquire_interrupt", False), ("queue_acquire_interrupt", False), ("partial_cleanup", False),
])
def test_claim_failure_releases_only_owned_leases_while_worker_is_alive(tmp_path, processes, fault, committed):
    q, job = setup_queue(tmp_path)
    worker = processes.start("fault", fault=fault, job_id=job["job_id"])
    result = processes.read(worker)
    assert result["error"] in {"OSError", "KeyboardInterrupt"}
    assert result["claims"] == 0 and worker[0].poll() is None
    probe = processes.once("probe", job_id=job["job_id"])
    assert probe == {"queue": True, "job": True, "unrelated": fault != "close_after_effect"}
    assert executions(tmp_path) == 0
    if fault == "paired_cleanup":
        assert [error["type"] for error in result["chain"]] == ["OSError", "KeyboardInterrupt"]
    if fault == "close_after_effect":
        assert result["replacement"] is not None and result["queue_close_count"] == 1
    before = q.inspect(job["job_id"])
    assert before["status"] == ("running" if committed else "queued")
    recovered = processes.once("recover")["jobs"]
    assert len(recovered) == int(committed)
    if committed:
        assert recovered[0]["status"] == "outcome_unknown"
        assert recovered[0]["fence"] > before["fence"]
        assert processes.once("recover")["jobs"] == []
        assert q.execute_one("new-worker", lambda *args: pytest.fail("uncertain attempt retried")) is None
    replay = processes.once("replay")
    assert replay["replayed"] is True
    assert replay["status"] == ("outcome_unknown" if committed else "queued")
    assert executions(tmp_path) == 0 and worker[0].poll() is None


@pytest.mark.parametrize("fault,completed", [
    ("publish_before", False), ("publish_after", True), ("publish_interrupt_after", True),
    ("unlock_error", True), ("unlock_after", True), ("unlock_interrupt", True),
    ("close_after_effect", True), ("job_unlock_error", True), ("paired_cleanup", True),
])
def test_terminal_failure_preserves_committed_effects_and_never_reruns(tmp_path, processes, fault, completed):
    q, job = setup_queue(tmp_path)
    worker = processes.start("fault", fault=fault, phase="completed", job_id=job["job_id"])
    result = processes.read(worker)
    assert result["error"] in {"OSError", "KeyboardInterrupt"} and result["claims"] == 0
    assert worker[0].poll() is None and executions(tmp_path) == 1
    probe = processes.once("probe", job_id=job["job_id"])
    assert probe == {"queue": True, "job": True, "unrelated": fault != "close_after_effect"}
    assert q.inspect(job["job_id"])["status"] == ("completed" if completed else "running")
    recovered = processes.once("recover")["jobs"]
    assert len(recovered) == int(not completed)
    replay = processes.once("replay")
    assert replay["status"] == ("completed" if completed else "outcome_unknown")
    assert q.execute_one("new-worker", lambda *args: pytest.fail("terminal attempt rerun")) is None
    assert executions(tmp_path) == 1 and worker[0].poll() is None


def test_successful_admission_holds_lease_until_execution_and_settlement(tmp_path, processes):
    q, job = setup_queue(tmp_path)
    worker = processes.start("live_execute", job_id=job["job_id"])
    ready = processes.read(worker, "ready")
    assert ready["boundary"] == "dispatch"
    assert processes.once("probe", job_id=job["job_id"]) == {"queue": True, "job": False, "unrelated": True}
    assert processes.once("recover")["jobs"] == []
    assert q.execute_one("contender", lambda *args: pytest.fail("live worker stolen")) is None
    assert executions(tmp_path) == 1
    Path(worker[1]["proceed"]).touch()
    result = processes.read(worker)
    assert result["ok"]["status"] == "completed" and worker[0].poll() is None
    assert processes.once("probe", job_id=job["job_id"])["job"] is True
    assert q.inspect(job["job_id"])["result"]["fence"] == ready["fence"]


def test_real_sigint_records_unknown_and_releases_without_worker_exit(tmp_path, processes):
    q, job = setup_queue(tmp_path)
    worker = processes.start("live_execute", job_id=job["job_id"])
    processes.read(worker, "ready")
    worker[0].send_signal(signal.SIGINT)
    result = processes.read(worker)
    assert result["error"] == "KeyboardInterrupt" and worker[0].poll() is None
    assert q.inspect(job["job_id"])["status"] == "outcome_unknown"
    assert processes.once("probe", job_id=job["job_id"])["job"] is True
    assert processes.once("recover")["jobs"] == []
    assert q.execute_one("contender", lambda *args: pytest.fail("cancelled attempt rerun")) is None
    assert executions(tmp_path) == 1


def test_process_loss_recovers_exactly_once_under_concurrency(tmp_path, processes):
    q, job = setup_queue(tmp_path)
    worker = processes.start("live_claim", job_id=job["job_id"])
    original = processes.read(worker, "ready")
    assert processes.once("recover")["jobs"] == []
    worker[0].kill(); worker[0].communicate(timeout=5)
    gate = tmp_path / "recover-go"
    contenders = [processes.start("recover", gate=str(gate)) for _ in range(6)]
    for contender in contenders:
        processes.read(contender, "ready")
    gate.touch()
    results = [processes.read(contender) for contender in contenders]
    jobs = [row for result in results for row in result["jobs"]]
    assert len(jobs) == 1 and jobs[0]["status"] == "outcome_unknown"
    assert jobs[0]["fence"] > original["fence"]
    assert q.execute_one("contender", lambda *args: pytest.fail("lost attempt rerun")) is None
    q.enqueue("operation.one", {"value": 2}, "new-attempt")
    claim = q.claim_next("new-worker")
    assert claim.fence > jobs[0]["fence"]
    q.complete(claim, {"synthetic": True})
    assert executions(tmp_path) == 0


def test_concurrent_enqueue_and_claim_have_one_identity(tmp_path, processes):
    q, job = setup_queue(tmp_path)
    gate = tmp_path / "enqueue-go"
    enqueuers = [processes.start("enqueue", gate=str(gate)) for _ in range(6)]
    for child in enqueuers:
        processes.read(child, "ready")
    gate.touch()
    values = [processes.read(child) for child in enqueuers]
    assert {value["job_id"] for value in values} == {job["job_id"]}
    assert all(value["replayed"] for value in values)
    gate = tmp_path / "claim-go"
    claimers = [processes.start("claim", gate=str(gate)) for _ in range(6)]
    for child in claimers:
        processes.read(child, "ready")
    gate.touch()
    values = [processes.read(child) for child in claimers]
    assert sum(value["claimed"] for value in values) == 1
    assert q.recover()["jobs"] == []
    winner = claimers[next(index for index, value in enumerate(values) if value["claimed"])]
    assert winner[0].poll() is None
    Path(winner[1]["stop"]).touch()
    winner[0].communicate(timeout=5)
    assert q.inspect(job["job_id"])["status"] == "outcome_unknown"


def test_lock_wait_has_host_bound_without_stealing_live_lock(tmp_path, processes):
    from ciw.coordinator import _Lease
    q, job = setup_queue(tmp_path)
    held = _Lease(q.directory / "queue.lock")
    assert held.try_acquire()
    try:
        before = time.monotonic()
        with pytest.raises(QueueRefusal, match="busy"):
            StoreQueue(q.directory, owner="host-a", principal="principal-a", generation="generation-a",
                       operations=["operation.one"], authorize=lambda *args: None,
                       limits=QueueLimits(lock_timeout=0.05))
        assert 0.04 <= time.monotonic() - before < 1
        assert processes.once("probe", job_id=job["job_id"])["queue"] is False
    finally:
        held.release()
    assert q.inspect(job["job_id"])["status"] == "queued"


@pytest.mark.parametrize("fault,published", [("publish_before", False), ("publish_after", True),
                                           ("unlock_error", True), ("unlock_interrupt", True)])
def test_initialization_failure_closes_lock_and_reopens_without_replacing_history(tmp_path, processes, fault, published):
    worker = processes.start("init_fault", fault=fault, phase="init")
    result = processes.read(worker)
    assert result["error"] in {"OSError", "KeyboardInterrupt"} and worker[0].poll() is None
    assert (tmp_path / "queue/queue.json").exists() is published
    assert processes.once("probe", only_queue=True) == {"queue": True}
    queue = StoreQueue(tmp_path / "queue", owner="host-a", principal="principal-a", generation="generation-a",
                       operations=["operation.one"], authorize=lambda *args: None)
    assert queue.recover()["jobs"] == []
    assert queue.execute_one("worker", lambda *args: pytest.fail("empty queue dispatched")) is None


@pytest.mark.parametrize("fault,published", [("publish_before", False), ("publish_after", True),
                                           ("unlock_error", True), ("unlock_interrupt", True)])
def test_enqueue_failure_readback_preserves_committed_request_without_dispatch(tmp_path, processes, fault, published):
    q, job = setup_queue(tmp_path)
    worker = processes.start("fault_enqueue", fault=fault, phase="queued", job_id=job["job_id"])
    result = processes.read(worker)
    assert result["error"] in {"OSError", "KeyboardInterrupt"} and worker[0].poll() is None
    state = json.loads((q.directory / "queue.json").read_bytes())
    assert len(state["jobs"]) == 1 + int(published)
    assert all(row["status"] == "queued" for row in state["jobs"])
    assert processes.once("probe", job_id=job["job_id"])["queue"] is True
    replay = q.enqueue("operation.one", {"value": 2}, "new-attempt")
    assert replay["replayed"] is published and replay["status"] == "queued"
    assert executions(tmp_path) == 0


@pytest.mark.parametrize("fault,published", [("publish_before", False), ("publish_after", True),
                                           ("unlock_error", True), ("unlock_interrupt", True)])
def test_recovery_failure_persists_unknown_once_and_closes_both_leases(tmp_path, processes, fault, published):
    q, job = setup_queue(tmp_path)
    lost = processes.start("live_claim", job_id=job["job_id"])
    before = processes.read(lost, "ready")
    lost[0].kill(); lost[0].communicate(timeout=5)
    worker = processes.start("fault_recover", fault=fault, phase="outcome_unknown", job_id=job["job_id"])
    result = processes.read(worker)
    assert result["error"] in {"OSError", "KeyboardInterrupt"} and worker[0].poll() is None
    probe = processes.once("probe", job_id=job["job_id"])
    assert probe == {"queue": True, "job": True, "unrelated": True}
    record = q.inspect(job["job_id"])
    assert record["status"] == ("outcome_unknown" if published else "running")
    recovered = processes.once("recover")["jobs"]
    assert len(recovered) == int(not published)
    final = q.inspect(job["job_id"])
    assert final["status"] == "outcome_unknown" and final["fence"] > before["fence"]
    assert processes.once("recover")["jobs"] == []
    assert q.inspect(job["job_id"])["fence"] == final["fence"]
    assert executions(tmp_path) == 0


@pytest.mark.parametrize("phase", ["running", "completed"])
def test_ambiguous_close_before_effect_blocks_until_owner_process_exits(tmp_path, processes, phase):
    q, job = setup_queue(tmp_path)
    worker = processes.start("fault", fault="unlock_and_close_before_effect", phase=phase, job_id=job["job_id"])
    result = processes.read(worker)
    assert result["error"] == "OSError" and result["claims"] == 0
    assert worker[0].poll() is None
    assert processes.once("probe", job_id=job["job_id"]) == {"queue": False, "job": True, "unrelated": True}
    q.limits = QueueLimits(lock_timeout=0.05)
    with pytest.raises(QueueRefusal, match="busy"):
        q.execute_one("contender", lambda *args: pytest.fail("uncertain close authorized dispatch"))
    assert result["queue_close_count"] == (1 if phase == "running" else 2)
    assert executions(tmp_path) == int(phase == "completed")
    # No close retry is safe: the same error could instead follow descriptor reuse.
    # Only this test-owned process exit releases the intentionally leaked fd.
    Path(worker[1]["stop"]).touch()
    worker[0].communicate(timeout=5)
    recovered = q.recover()["jobs"]
    assert len(recovered) == int(phase == "running")
    assert q.inspect(job["job_id"])["status"] == ("outcome_unknown" if phase == "running" else "completed")
    assert q.execute_one("contender", lambda *args: pytest.fail("old attempt retried")) is None
