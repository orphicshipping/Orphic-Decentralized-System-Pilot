"""Admission ownership across owner-transaction exit, with real POSIX processes.

Faulted workers stay alive while independent processes probe and recover their
jobs. Synthetic operations record actual calls; no native provider is executed.
The same tests also run outside the checkout against an installed wheel.
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
    reason="Admission lock qualification requires POSIX signals and no-follow support",
)
PACKAGE_PARENT = Path(ciw.__file__).resolve().parents[1]
TIMEOUT = 20

CHILD = r'''
import json, os, signal, sys, time
from pathlib import Path
import ciw
from ciw import coordinator, retention, services

signal.signal(signal.SIGINT, signal.default_int_handler)
cfg = json.loads(sys.argv[1])
mode, fault = cfg['mode'], cfg.get('fault')
store = retention.Store('admission-worker')
directory = retention._owner_dir(store.owner)
owner_path = directory / 'sequence.lock'
job_path = directory / coordinator._idem_name('job')
lease_path = coordinator._lease_path(directory, job_path.name)
other_path = retention._owner_dir('unrelated-owner') / 'sequence.lock'
arguments = {'domain': 'sequence', 'example': True}
ready, result_path, stop, proceed = map(Path, (cfg['ready'], cfg['result'], cfg['stop'], cfg['proceed']))
executions = Path(cfg['executions'])

def emit(path, value):
    value['ciw_file'] = ciw.__file__
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)

def wait_for(path):
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('child handshake deadline expired')
        time.sleep(0.01)

def pause(boundary):
    emit(ready, {'boundary': boundary})
    wait_for(proceed)

def same_file(fd, path):
    try:
        left, right = os.fstat(fd), path.stat()
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
    except OSError:
        return False

def available(path):
    lease = coordinator._Lease(path)
    try:
        return lease.try_acquire()
    finally:
        lease.release()

def operation(**kwargs):
    fd = os.open(executions, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, (str(os.getpid()) + '\n').encode())
    finally:
        os.close(fd)
    if mode == 'hold_operation':
        pause('operation')
    return {'artifact_id': None, 'example': True}

services.domain_run = operation
if mode in ('probe', 'recover', 'replay'):
    if cfg.get('gate'):
        emit(ready, {'boundary': 'recovery-gate'})
        wait_for(Path(cfg['gate']))
    if mode == 'probe':
        value = {'job_available': available(lease_path),
                 'owner_available': available(owner_path),
                 'unrelated_available': available(other_path)}
    elif mode == 'recover':
        value = coordinator.recover(store)
    else:
        value = coordinator.submit(store, 'ncp_domain_run', arguments, 'job')
    value['executions_here'] = coordinator._EXECUTIONS
    emit(result_path, value)
else:
    primary = (KeyboardInterrupt('admission exit interrupted')
               if fault in ('unlock_interrupt', 'paired_cleanup') else
               SystemExit('admission exit terminated') if fault == 'unlock_system_exit'
               else OSError('admission exit I/O failure'))
    body_error = ValueError('publication completed before body error')
    cleanup_error = OSError('job lease cleanup error')
    injected = cleanup_injected = False
    replacement = None
    owner_close_count = 0
    original_unlock, original_close = retention._lock_release, os.close
    original_lease_unlock, original_store_job = coordinator._lock_release, coordinator._store_job

    def owner_unlock(fd):
        global injected
        if same_file(fd, owner_path) and not injected:
            if fault in ('signal', 'crash'):
                injected = True
                pause('owner-exit')
            elif fault in ('unlock_error', 'unlock_interrupt', 'unlock_system_exit',
                           'paired_cleanup', 'publish_then_exit_error'):
                injected = True
                raise primary
        return original_unlock(fd)

    def owner_close(fd):
        global injected, replacement, owner_close_count
        matched = same_file(fd, owner_path)
        if matched:
            owner_close_count += 1
        original_close(fd)
        if matched and fault == 'close_after_effect' and not injected:
            injected = True
            replacement = os.open(other_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            if replacement != fd:
                os.dup2(replacement, fd)
                original_close(replacement)
                replacement = fd
            assert retention._try_lock(replacement)
            raise primary

    def lease_unlock(fd):
        global cleanup_injected
        if fault == 'paired_cleanup' and same_file(fd, lease_path) and not cleanup_injected:
            cleanup_injected = True
            raise cleanup_error
        return original_lease_unlock(fd)

    def store_job(holder, name, job):
        if fault == 'publish_before':
            raise primary
        original_store_job(holder, name, job)
        if fault == 'publish_after':
            raise primary
        if fault == 'publish_then_exit_error':
            raise body_error

    retention._lock_release, os.close = owner_unlock, owner_close
    coordinator._lock_release, coordinator._store_job = lease_unlock, store_job
    try:
        try:
            value = coordinator.submit(store, 'ncp_domain_run', arguments, 'job')
            outcome = {'exception': None, 'result': value}
        except BaseException as exc:
            chain = []
            current = exc
            while current is not None:
                chain.append(type(current).__name__)
                current = current.__context__
            outcome = {'exception': type(exc).__name__, 'chain': chain,
                       'same_primary': exc is primary,
                       'same_cleanup': exc is cleanup_error,
                       'primary_is_context': exc.__context__ is primary,
                       'body_is_context': exc.__context__ is body_error}
    finally:
        retention._lock_release, os.close = original_unlock, original_close
        coordinator._lock_release, coordinator._store_job = original_lease_unlock, original_store_job
    outcome.update(injected=injected, cleanup_injected=cleanup_injected,
                   owner_close_count=owner_close_count,
                   saved_job=coordinator._read_json(job_path),
                   executions_here=coordinator._EXECUTIONS,
                   replacement_open=replacement is not None and same_file(replacement, other_path))
    emit(result_path, outcome)
    try:
        wait_for(stop)
    finally:
        if replacement is not None and same_file(replacement, other_path):
            try:
                original_unlock(replacement)
            finally:
                original_close(replacement)
'''


class Workers:
    def __init__(self, directory):
        self.directory = directory
        self.children = []
        self.executions = directory / 'executions'

    def spawn(self, mode, **options):
        prefix = self.directory / str(len(self.children))
        config = dict(mode=mode, ready=str(prefix.with_suffix('.ready')),
                      result=str(prefix.with_suffix('.result')),
                      stop=str(prefix.with_suffix('.stop')),
                      proceed=str(prefix.with_suffix('.proceed')),
                      executions=str(self.executions), **options)
        env = dict(os.environ, NCP_RETAIN_DIR=str(self.directory / 'retain'),
                   PYTHONPATH=str(PACKAGE_PARENT), PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.Popen([sys.executable, '-c', CHILD, json.dumps(config)],
                                cwd=self.directory, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = proc, config
        self.children.append(child)
        return child

    def read(self, child, field='result'):
        proc, config = child
        path = Path(config[field])
        deadline = time.monotonic() + TIMEOUT
        while not path.exists():
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=5)
                pytest.fail(f'Child exited {proc.returncode} before {field}: {out}\n{err}')
            assert time.monotonic() < deadline, f'Child {field} handshake timed out'
            time.sleep(0.01)
        value = json.loads(path.read_text())
        assert Path(value.pop('ciw_file')).resolve() == Path(ciw.__file__).resolve()
        return value

    def finish(self, child):
        proc, config = child
        Path(config['stop']).write_text('stop')
        out, err = proc.communicate(timeout=TIMEOUT)
        assert proc.returncode == 0, out + err

    def query(self, mode):
        child = self.spawn(mode)
        value = self.read(child)
        self.finish(child)
        return value

    def calls(self):
        return len(self.executions.read_text().splitlines()) if self.executions.exists() else 0

    def recover_once(self):
        gate = self.directory / f'recover-{len(self.children)}'
        children = [self.spawn('recover', gate=str(gate)) for _ in range(4)]
        for child in children:
            assert self.read(child, 'ready')['boundary'] == 'recovery-gate'
        gate.write_text('go')
        values = [self.read(child) for child in children]
        for child in children:
            self.finish(child)
        jobs = [job for value in values for job in value['jobs']]
        assert len(jobs) == 1, values
        assert all(value['executions_here'] == 0 for value in values)
        replay = self.query('replay')
        assert replay['status'] == 'failed' and replay['error'] == 'worker_lost'
        assert replay['replayed'] and replay['executions_here'] == 0
        assert replay['artifact_id'] == jobs[0]['artifact_id']
        assert self.query('recover')['jobs'] == []
        assert len(list((self.directory / 'retain').rglob('failure-*.json'))) == 1
        assert self.calls() == 0

    def close(self):
        for proc, config in self.children:
            if proc.poll() is None:
                Path(config['proceed']).write_text('go')
                Path(config['stop']).write_text('stop')
                try:
                    proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=5)


@pytest.fixture
def workers(tmp_path):
    value = Workers(tmp_path)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize('fault,exception', [
    ('unlock_error', 'OSError'), ('unlock_interrupt', 'KeyboardInterrupt'),
    ('unlock_system_exit', 'SystemExit'), ('close_after_effect', 'OSError'),
])
def test_owner_exit_failure_releases_admission_lease_while_worker_lives(workers, fault, exception):
    worker = workers.spawn('fault', fault=fault)
    result = workers.read(worker)
    assert worker[0].poll() is None
    assert result['exception'] == exception and result['same_primary']
    assert result['chain'] == [exception]
    assert result['injected'] and result['executions_here'] == 0
    assert result['owner_close_count'] == 1
    assert result['saved_job']['status'] == 'running'
    probe = workers.query('probe')
    assert probe['owner_available'] and probe['job_available']
    if fault == 'close_after_effect':
        assert result['replacement_open'] and not probe['unrelated_available']
    workers.recover_once()
    workers.finish(worker)
    assert workers.query('probe')['unrelated_available']


def test_admission_and_lease_cleanup_errors_preserve_context_and_release(workers):
    worker = workers.spawn('fault', fault='paired_cleanup')
    result = workers.read(worker)
    assert result['chain'] == ['OSError', 'KeyboardInterrupt']
    assert result['same_cleanup'] and result['primary_is_context']
    assert result['cleanup_injected'] and result['executions_here'] == 0
    assert workers.query('probe')['job_available']
    workers.recover_once()


def test_real_sigint_during_owner_exit_does_not_execute_or_strand_admission(workers):
    worker = workers.spawn('fault', fault='signal')
    assert workers.read(worker, 'ready')['boundary'] == 'owner-exit'
    before = workers.query('probe')
    assert not before['job_available'] and not before['owner_available']
    worker[0].send_signal(signal.SIGINT)
    result = workers.read(worker)
    assert result['chain'] == ['KeyboardInterrupt'] and result['executions_here'] == 0
    assert worker[0].poll() is None
    assert workers.query('probe')['job_available']
    workers.recover_once()


def test_killed_worker_at_owner_exit_recovers_committed_admission_once(workers):
    worker = workers.spawn('fault', fault='crash')
    assert workers.read(worker, 'ready')['boundary'] == 'owner-exit'
    before = workers.query('probe')
    assert not before['job_available'] and not before['owner_available']
    worker[0].kill()
    worker[0].communicate(timeout=TIMEOUT)
    assert worker[0].returncode == -signal.SIGKILL
    after = workers.query('probe')
    assert after['job_available'] and after['owner_available']
    workers.recover_once()


def test_successful_transfer_keeps_live_job_owned_until_operation_returns(workers):
    worker = workers.spawn('hold_operation')
    assert workers.read(worker, 'ready')['boundary'] == 'operation'
    probe = workers.query('probe')
    assert probe['owner_available'] and not probe['job_available']
    assert workers.query('recover')['jobs'] == []
    replay = workers.query('replay')
    assert replay['status'] == 'running' and replay['executions_here'] == 0
    assert workers.calls() == 1
    Path(worker[1]['proceed']).write_text('continue')
    assert workers.read(worker)['result']['status'] == 'completed'
    assert workers.query('probe')['job_available']
    replay = workers.query('replay')
    assert replay['status'] == 'completed' and replay['replayed']
    assert replay['executions_here'] == 0 and workers.calls() == 1


def test_failed_replay_transaction_does_not_release_another_workers_lease(workers):
    holder = workers.spawn('hold_operation')
    assert workers.read(holder, 'ready')['boundary'] == 'operation'
    contender = workers.spawn('fault', fault='unlock_error')
    result = workers.read(contender)
    assert result['same_primary'] and result['executions_here'] == 0
    assert not workers.query('probe')['job_available']
    assert workers.query('recover')['jobs'] == []
    assert workers.calls() == 1
    Path(holder[1]['proceed']).write_text('continue')
    assert workers.read(holder)['result']['status'] == 'completed'
    assert workers.query('replay')['status'] == 'completed'
    assert workers.calls() == 1


@pytest.mark.parametrize('fault', ['publish_before', 'publish_after', 'publish_then_exit_error'])
def test_publication_effects_and_exception_context_are_not_rolled_back(workers, fault):
    worker = workers.spawn('fault', fault=fault)
    result = workers.read(worker)
    assert result['same_primary'] and result['exception'] == 'OSError'
    assert result['executions_here'] == 0 and workers.calls() == 0
    probe = workers.query('probe')
    assert probe['owner_available'] and probe['job_available']
    if fault == 'publish_before':
        assert result['saved_job'] is None
        assert workers.query('recover')['jobs'] == []
        assert workers.query('replay')['status'] == 'completed'
        assert workers.query('replay')['replayed'] and workers.calls() == 1
    else:
        assert result['saved_job']['status'] == 'running'
        if fault == 'publish_then_exit_error':
            assert result['body_is_context'] and result['chain'] == ['OSError', 'ValueError']
        workers.recover_once()
