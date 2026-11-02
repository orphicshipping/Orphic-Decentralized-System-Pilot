"""Surviving-worker interruption after admission returns to submit.

No native provider is run. A trace pauses at a real source line with the caller's
lease assigned. This tests that boundary, not every Python bytecode interruption.
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
    os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'),
    reason='Real lease/signal qualification requires POSIX no-follow support',
)
PACKAGE_PARENT = Path(ciw.__file__).resolve().parents[1]
INSTALLED = Path(ciw.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
TIMEOUT = 15

CHILD = r'''
import inspect, json, os, signal, sys, time
from pathlib import Path
import ciw
from ciw import coordinator as c, retention as r, services
cfg=json.loads(sys.argv[1])
signal.signal(signal.SIGINT,signal.default_int_handler)
store=r.Store('submit-transfer-owner')
arguments={'domain':'sequence','example':True}
key='one-job'
path=c._lease_path(r._owner_dir(store.owner),c._idem_name(key))
def emit(value):
    value['ciw_file']=ciw.__file__
    target=Path(cfg['result']);partial=target.with_suffix('.partial')
    partial.write_text(json.dumps(value));partial.replace(target)
def wait(path):
    end=time.monotonic()+30
    while not Path(path).exists():
        if time.monotonic()>end:raise TimeoutError(path)
        time.sleep(.005)
if cfg['mode']=='query':
    lease=c._Lease(path)
    try:available=lease.try_acquire()
    finally:lease.release()
    recovery=c.recover(store)
    emit({'available':available,'recovery':recovery,
          'job':c.coordinate('recall',store=store,idempotency_key=key)})
elif cfg['mode']=='holder':
    job,lease=c._admit(store,key,c._digest('ncp_domain_run',arguments,None),'ncp_domain_run')
    assert lease is not None and lease.fd is not None
    try:
        emit({'holding':True})
        wait(cfg['stop'])
    finally:lease.release()
else:
    primary=OSError('interruption at submit ownership boundary')
    source,start=inspect.getsourcelines(c.submit)
    target=next(start+i for i,line in enumerate(source) if line.strip()=='if lease is None:')
    held=None;injected=False;operation_calls=0
    def operation(**kwargs):
        global operation_calls
        operation_calls+=1
        return {'artifact_id':None,'example':True}
    services.domain_run=operation
    def trace(frame,event,arg):
        global held,injected
        if not injected and frame.f_code is c.submit.__code__ and event=='line' and frame.f_lineno==target:
            held=frame.f_locals['lease'];injected=True
            assert (held is not None and held.fd is not None)==cfg['expect_owned']
            if cfg['fault']=='OSError':raise primary
            Path(cfg['ready']).write_text('lease boundary reached')
            wait(cfg['resume'])
        return trace
    try:
        sys.settrace(trace)
        try:c.submit(store,'ncp_domain_run',arguments,key)
        except BaseException as exc:
            outcome={'exception':type(exc).__name__,'same_primary':exc is primary,
                     'context':type(exc.__context__).__name__ if exc.__context__ else None}
        else:outcome={'exception':None}
    finally:sys.settrace(None)
    outcome.update(injected=injected,lease_owned=held is not None and held.fd is not None,
                   operation_calls=operation_calls,coordinator_executions=c._EXECUTIONS)
    emit(outcome)
    try:wait(cfg['stop'])
    finally:
        # Harness cleanup happens after independent observations. A defective
        # version is allowed to fail assertions without leaking the fixture.
        if held is not None:held.release()
'''


class Processes:
    def __init__(self, root):
        self.root, self.items = root, []

    def spawn(self, mode, **values):
        index = len(self.items)
        config = {'mode': mode, **values,
                  **{name: str(self.root / f'{index}.{name}')
                     for name in ('result', 'ready', 'stop', 'resume')}}
        env = {**os.environ, 'NCP_RETAIN_DIR': str(self.root / 'retain'),
               'PYTHONPATH': str(PACKAGE_PARENT), 'PYTHONDONTWRITEBYTECODE': '1'}
        # Installed acceptance uses isolated imports; source tests pin the same
        # package via PYTHONPATH. -B also applies to all child script modes.
        flags = ['-I', '-B'] if INSTALLED else ['-B']
        process = subprocess.Popen([sys.executable, *flags, '-c', CHILD, json.dumps(config)],
                                   cwd=self.root, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        item = process, config
        self.items.append(item)
        return item

    def read(self, item, name='result'):
        process, config = item
        target = Path(config[name])
        end = time.monotonic() + TIMEOUT
        while not target.exists():
            if process.poll() is not None:
                out, err = process.communicate(timeout=3)
                pytest.fail(f'Child exited {process.returncode}: {out}\n{err}')
            assert time.monotonic() < end, f'Child timeout at {name}'
            time.sleep(.005)
        if name != 'result':
            return
        value = json.loads(target.read_text())
        assert Path(value.pop('ciw_file')).resolve() == Path(ciw.__file__).resolve()
        return value

    def query(self):
        item = self.spawn('query')
        value = self.read(item)
        out, err = item[0].communicate(timeout=TIMEOUT)
        assert item[0].returncode == 0, (out, err)
        return value

    def close(self):
        for _, config in self.items:
            Path(config['stop']).write_text('stop')
            Path(config['resume']).write_text('go')
        for process, _ in self.items:
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)


@pytest.fixture
def children(tmp_path):
    group = Processes(tmp_path)
    try:
        yield group
    finally:
        group.close()


@pytest.mark.parametrize('fault', ['OSError', 'SIGINT'])
def test_post_admission_caller_interruption_releases_job_before_worker_exit(children, fault):
    worker = children.spawn('worker', fault=fault, expect_owned=True)
    if fault == 'SIGINT':
        children.read(worker, 'ready')
        worker[0].send_signal(signal.SIGINT)
    result = children.read(worker)
    assert worker[0].poll() is None
    assert result['exception'] == ('OSError' if fault == 'OSError' else 'KeyboardInterrupt')
    assert result['same_primary'] == (fault == 'OSError')
    assert result['context'] is None
    assert result['injected'] and result['operation_calls'] == result['coordinator_executions'] == 0
    observed = children.query()
    assert observed['available'], observed
    assert not result['lease_owned'], result
    assert len(observed['recovery']['jobs']) == 1
    assert observed['job']['status'] == 'failed'
    assert observed['job']['error'] == 'worker_lost'
    second = children.query()
    assert second['recovery']['jobs'] == []
    assert second['job']['artifact_id'] == observed['job']['artifact_id']
    assert worker[0].poll() is None


def test_interrupted_replay_does_not_release_another_live_workers_job(children):
    holder = children.spawn('holder')
    assert children.read(holder)['holding']
    replay = children.spawn('worker', fault='OSError', expect_owned=False)
    result = children.read(replay)
    assert result['same_primary'] and result['exception'] == 'OSError'
    assert result['context'] is None and not result['lease_owned']
    assert result['operation_calls'] == result['coordinator_executions'] == 0
    observed = children.query()
    assert not observed['available']
    assert observed['recovery']['jobs'] == []
    assert observed['job']['status'] == 'running'
    assert holder[0].poll() is None and replay[0].poll() is None
