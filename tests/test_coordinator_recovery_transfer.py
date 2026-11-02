"""Recovery owns its lease before an interruptible transfer boundary.

No lock helper is replaced. Tracing pauses at the first source line after real
acquisition, before processing resumes; a parent delivers actual SIGINT. Failed
workers remain alive while independent processes probe and recover the records.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import ciw
from ciw import coordinator, retention
import pytest

pytestmark = pytest.mark.skipif(
    os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'),
    reason='Recovery transfer qualification requires POSIX signals, flock and no-follow support',
)
PACKAGE_PARENT = Path(ciw.__file__).resolve().parents[1]
TIMEOUT = 20

CHILD = r'''
import json, os, signal, sys, time
from pathlib import Path
import ciw
from ciw import coordinator as c, retention as r

cfg = json.loads(sys.argv[1])
store = r.Store('recovery-owner')
signal.signal(signal.SIGINT, signal.default_int_handler)
held = None

def emit(path, value):
    value['ciw_file'] = ciw.__file__
    path = Path(path)
    part = path.with_suffix('.partial')
    part.write_text(json.dumps(value))
    part.replace(path)

def wait(path):
    until = time.monotonic() + 30
    while not Path(path).exists():
        if time.monotonic() >= until: raise TimeoutError('child handshake timed out')
        time.sleep(0.005)

if cfg['mode'] == 'probe':
    lease = c._Lease(Path(cfg['lease']))
    try: available = lease.try_acquire()
    finally: lease.release()
    emit(cfg['result'], {'available': available, 'recovery': c.recover(store),
                         'executions': c._EXECUTIONS})
elif cfg['mode'] == 'holder':
    held = c._Lease(Path(cfg['lease']))
    assert held.try_acquire()
    emit(cfg['ready'], {'holding': True})
    try: wait(cfg['stop'])
    finally: held.release()
else:
    function = c._settle_locked if cfg['boundary'] == 'job' else c.recover
    injected = False
    primary = OSError('injected recovery ownership transfer')
    def trace(frame, event, arg):
        global held, injected
        if not injected and frame.f_code is function.__code__ and event == 'line':
            candidate = frame.f_locals.get('lease')
            if candidate is not None and candidate.fd is not None:
                held, injected = candidate, True
                if cfg['fault'] == 'OSError': raise primary
                emit(cfg['ready'], {'owned': True, 'line': frame.f_lineno})
                wait(cfg['resume'])
        return trace
    try:
        sys.settrace(trace)
        try:
            c.recover(store)
        except BaseException as exc:
            outcome = {'exception': type(exc).__name__, 'same_primary': exc is primary,
                       'context': type(exc.__context__).__name__ if exc.__context__ else None}
        else:
            outcome = {'exception': None}
    finally:
        sys.settrace(None)
    outcome.update(injected=injected, owns_fd=held is not None and held.fd is not None,
                   executions=c._EXECUTIONS)
    emit(cfg['result'], outcome)
    try:
        wait(cfg['stop'])
    finally:
        # Test-only teardown for red baseline runs. Assertions/probes happen
        # while the worker is still alive, before this cleanup can mask a leak.
        if held is not None: held.release()
'''


class Processes:
    def __init__(self, base, boundary):
        self.base, self.boundary = base, boundary
        self.children = []
        store = retention.Store('recovery-owner')
        if boundary == 'job':
            directory = retention._owner_dir(store.owner)
            name = coordinator._idem_name('unknown-job')
            with retention._transaction(directory):
                coordinator._store_job(store, name, {
                    'schema': 'ncp.coordinator-job.v1', 'operation': 'ncp_domain_run',
                    'status': 'running', 'pid': 0, 'request_digest': 'synthetic-unknown',
                    'authorizes_execution': False,
                })
            self.lease = coordinator._lease_path(directory, name)
        else:
            self.lease = coordinator._exclusive_path('oven')
            self.lease.write_text(json.dumps({'owner': store.owner, 'pid': 0, 'liveness': 'file-lease'}))

    def spawn(self, mode, **extra):
        prefix = self.base / str(len(self.children))
        cfg = dict(mode=mode, boundary=self.boundary, lease=str(self.lease),
                   ready=str(prefix.with_suffix('.ready')), result=str(prefix.with_suffix('.result')),
                   stop=str(prefix.with_suffix('.stop')), resume=str(prefix.with_suffix('.resume')), **extra)
        env = dict(os.environ, PYTHONPATH=str(PACKAGE_PARENT), PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.Popen([sys.executable, '-c', CHILD, json.dumps(cfg)],
                                cwd=self.base, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child = proc, cfg
        self.children.append(child)
        return child

    def read(self, child, field='result'):
        proc, cfg = child
        path = Path(cfg[field])
        until = time.monotonic() + TIMEOUT
        while not path.exists():
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=5)
                pytest.fail(f'Child exited {proc.returncode}: {out}\n{err}')
            assert time.monotonic() < until, 'child handshake timed out'
            time.sleep(0.005)
        value = json.loads(path.read_text())
        assert Path(value.pop('ciw_file')).resolve() == Path(ciw.__file__).resolve()
        return value

    def query(self):
        child = self.spawn('probe')
        result = self.read(child)
        out, err = child[0].communicate(timeout=TIMEOUT)
        assert child[0].returncode == 0, out + err
        return result

    def close(self):
        for proc, cfg in self.children:
            Path(cfg['resume']).write_text('go')
            Path(cfg['stop']).write_text('stop')
            try: proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=5)


@pytest.mark.parametrize('boundary', ['job', 'exclusive'])
@pytest.mark.parametrize('fault', ['OSError', 'SIGINT'])
def test_recovery_transfer_failure_releases_owned_lease_before_worker_exit(tmp_path, monkeypatch, boundary, fault):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retain'))
    group = Processes(tmp_path, boundary)
    try:
        worker = group.spawn('fault', fault=fault)
        if fault == 'SIGINT':
            assert group.read(worker, 'ready')['owned']
            worker[0].send_signal(signal.SIGINT)
        outcome = group.read(worker)
        assert worker[0].poll() is None
        assert outcome['injected'] and outcome['executions'] == 0
        assert outcome['context'] is None
        assert outcome['exception'] == ('KeyboardInterrupt' if fault == 'SIGINT' else 'OSError')
        if fault == 'OSError': assert outcome['same_primary']
        observed = group.query()
        assert not outcome['owns_fd'] and observed['available'], (outcome, observed)
        assert observed['executions'] == 0
        settled = observed['recovery']['jobs' if boundary == 'job' else 'released']
        assert len(settled) == 1
        assert group.query()['recovery'] == {
            'schema': 'ncp.coordinator-recovery.v1', 'jobs': [], 'released': [], 'authorizes_execution': False,
        }
        assert worker[0].poll() is None
    finally:
        group.close()


@pytest.mark.parametrize('boundary', ['job', 'exclusive'])
def test_recovery_does_not_release_a_different_live_holder(tmp_path, monkeypatch, boundary):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retain'))
    group = Processes(tmp_path, boundary)
    try:
        holder = group.spawn('holder')
        assert group.read(holder, 'ready')['holding']
        for _ in range(2):
            observed = group.query()
            assert not observed['available']
            assert observed['executions'] == 0
            assert observed['recovery']['jobs'] == [] and observed['recovery']['released'] == []
            assert holder[0].poll() is None
        Path(holder[1]['stop']).write_text('stop')
        out, err = holder[0].communicate(timeout=TIMEOUT)
        assert holder[0].returncode == 0, out + err
        observed = group.query()
        assert observed['available']
        assert len(observed['recovery']['jobs' if boundary == 'job' else 'released']) == 1
    finally:
        group.close()
