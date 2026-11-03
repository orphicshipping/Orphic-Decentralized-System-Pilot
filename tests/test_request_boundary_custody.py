"""Real shadow process/descriptor custody using test-only Python executables.

These probes qualify transport cleanup, not the Rust implementation, sandboxing,
network isolation or provider rollback. Failed owners stay alive during checks.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

import ciw
from ciw import request_boundary as boundary
from ciw import request_boundary_transport as transport
from ciw.adapters import subprocess as runtime
import test_subprocess_cancellation as existing

pytestmark = pytest.mark.skipif(sys.platform != 'linux' or not hasattr(os,'memfd_create'),
                               reason='Actual sealed memfd/SIGINT custody qualification requires Linux')


def fixture_binary(tmp_path, program, name='synthetic-shadow.py'):
    path=tmp_path/name
    # The fixture interpreter is explicitly host-selected; no request controls
    # this executable. Production native Rust qualification is a separate gate.
    raw=(f'#!{sys.executable} -I\n'+program+'\n').encode()
    path.write_bytes(raw);path.chmod(0o700)
    return path,sha256(raw).hexdigest()


def bound(tmp_path, program=existing.CLOSED, *, timeout=.2):
    path,digest=fixture_binary(tmp_path,program)
    return boundary.RequestBoundary(enabled=True,binary=path,binary_sha256=digest,timeout=timeout)


def assert_held_no_launch(owner, monkeypatch, held):
    def forbidden(*args,**kwargs):
        raise AssertionError('uncertain shadow custody must prevent a second launch')
    with monkeypatch.context() as patch:
        patch.setattr(runtime.subprocess,'Popen',forbidden)
        with pytest.raises(runtime.RuntimeCleanupUncertain) as caught: owner._run(b'next')
    assert owner._cleanup_uncertain is held and caught.value.__cause__ is held


def test_combined_stdout_stderr_flood_refuses_and_reaps_actual_shadow(tmp_path,monkeypatch):
    owner=bound(tmp_path,"import os,time;os.write(1,b'x'*2200);os.write(2,b'y'*2200);time.sleep(20)",timeout=2)
    with existing.owned_children(monkeypatch) as owned:
        with pytest.raises(transport.ShadowFailure,match='output_budget') as caught: owner._run(b'input')
        assert any(isinstance(error,runtime.AdapterRefusal) and error.code=='OUTPUT_LIMIT'
                   for error in existing.causes(caught.value))
        existing.assert_reaped(owned)
        assert owned.stops==[owned.processes[0].pid] and owner._cleanup_uncertain is None


def test_normal_shadow_timeout_reaps_without_claiming_rollback(tmp_path,monkeypatch):
    owner=bound(tmp_path,timeout=.1)
    with existing.owned_children(monkeypatch) as owned:
        started=time.monotonic()
        with pytest.raises(transport.ShadowFailure,match='timeout'): owner._run(b'input')
        assert time.monotonic()-started<6
        existing.assert_reaped(owned)
        assert owner._cleanup_uncertain is None


def test_ordinary_descendant_held_pipes_are_stopped_while_owner_stays_alive(tmp_path,monkeypatch):
    owner=bound(tmp_path,"""import os,time
child=os.fork()
if child==0: time.sleep(20)
else: print(child,flush=True)
""",timeout=2)
    with existing.owned_children(monkeypatch) as owned:
        child=int(owner._run(b'input'))
        process=owned.processes[0]
        assert process.returncode==0 and owned.stops==[process.pid]
        with pytest.raises(ChildProcessError):os.waitpid(process.pid,os.WNOHANG)
        assert process.stdout.closed and process.stderr.closed
        assert not any(reader.is_alive() for reader in owned.readers)
        state=Path(f'/proc/{child}/stat')
        # The caller cannot waitpid a grandchild. An orphan zombie waiting for
        # container init is not executing and no longer owns the pipes.
        assert not state.exists() or state.read_text().split()[2]=='Z'


def test_descendant_pipe_stop_interrupt_remains_diagnostic_and_held(tmp_path,monkeypatch):
    child_record=tmp_path/'owned-descendant.pid'
    owner=bound(tmp_path,f"""import os,time
child=os.fork()
if child==0:time.sleep(20)
else:
 with open({str(child_record)!r},'w') as record:record.write(str(child))
""",timeout=2)
    interrupt=KeyboardInterrupt('interrupt while stopping descendant-held pipes')
    with existing.owned_children(monkeypatch) as owned:
        attempts=[]
        def stop(process):attempts.append(process.pid);raise interrupt
        monkeypatch.setattr(runtime,'_stop',stop)
        try:
            with pytest.raises(runtime.RuntimeCleanupUncertain) as caught:owner._run(b'input')
            assert interrupt in list(existing.causes(caught.value))
            assert owned.processes[0].returncode==0  # Direct child was already reaped.
            assert attempts==[owned.processes[0].pid]
            assert any(reader.is_alive() for reader in owned.readers)
            held=owner._cleanup_uncertain
            assert held is caught.value
            assert_held_no_launch(owner,monkeypatch,held)
        finally:
            # Test owns this ordinary child group even though its leader exited.
            # Resolve it explicitly before helper teardown closes blocked pipes.
            if child_record.exists() and owned.processes:
                child=int(child_record.read_text())
                try:
                    if os.getpgid(child)==owned.processes[0].pid:owned.stop(owned.processes[0])
                except ProcessLookupError:pass
                for reader in owned.readers:
                    if reader.ident is not None:owned.thread.join(reader,timeout=5)
        assert_held_no_launch(owner,monkeypatch,held)


def test_keyboard_interrupt_before_cleanup_preserves_primary_and_reaps(tmp_path,monkeypatch):
    owner=bound(tmp_path,timeout=2);primary=KeyboardInterrupt('synthetic owned cancellation')
    with existing.owned_children(monkeypatch) as owned:
        existing.fail_wait(monkeypatch,owned,primary)
        with pytest.raises(KeyboardInterrupt) as caught:owner._run(b'input')
        assert caught.value is primary
        existing.assert_reaped(owned)
        assert owner._cleanup_uncertain is None


@pytest.mark.parametrize('after_effect',[False,True])
def test_keyboard_interrupt_during_stop_preserves_actual_custody(tmp_path,monkeypatch,after_effect):
    owner=bound(tmp_path,timeout=.1);interrupt=KeyboardInterrupt('interrupt during owned stop')
    with existing.owned_children(monkeypatch) as owned:
        original=runtime._stop;attempts=[]
        def stop(process):
            attempts.append(process.pid)
            if after_effect:original(process)
            raise interrupt
        monkeypatch.setattr(runtime,'_stop',stop)
        if after_effect:
            with pytest.raises(KeyboardInterrupt) as caught:owner._run(b'input')
            assert caught.value is interrupt and owner._cleanup_uncertain is None
            existing.assert_reaped(owned)
        else:
            with pytest.raises(runtime.RuntimeCleanupUncertain) as caught:owner._run(b'input')
            assert interrupt in list(existing.causes(caught.value))
            assert caught.value.process.poll() is None
            assert_held_no_launch(owner,monkeypatch,owner._cleanup_uncertain)
        assert attempts==[owned.processes[0].pid]


@pytest.mark.parametrize('stage',['stop','wait','pipe_close','snapshot_exit'])
def test_after_effect_cleanup_failure_preserves_cancellation_and_completes_other_cleanup(tmp_path,monkeypatch,stage):
    owner=bound(tmp_path,timeout=2)
    primary=KeyboardInterrupt('synthetic owned cancellation');cleanup=OSError('synthetic after-effect '+stage)
    with existing.owned_children(monkeypatch) as owned:
        existing.fail_wait(monkeypatch,owned,primary,cleanup if stage=='wait' else None)
        if stage=='stop':
            original=runtime._stop
            def stop(process):original(process);raise cleanup
            monkeypatch.setattr(runtime,'_stop',stop)
        elif stage=='pipe_close':
            original=runtime.subprocess.Popen
            def spawn(*args,**kwargs):
                process=original(*args,**kwargs);close=process.stdout.close
                def closed():close();raise cleanup
                process.stdout.close=closed
                return process
            monkeypatch.setattr(runtime.subprocess,'Popen',spawn)
        elif stage=='snapshot_exit':
            original=boundary.executable_snapshot
            @contextmanager
            def snapshot(*args,**kwargs):
                try:
                    with original(*args,**kwargs) as executable:yield executable
                finally:raise cleanup
            monkeypatch.setattr(boundary,'executable_snapshot',snapshot)
        with pytest.raises(OSError) as caught:owner._run(b'input')
        assert caught.value is cleanup and primary in list(existing.causes(cleanup))
        existing.assert_reaped(owned)
        assert owner._cleanup_uncertain is None
        if stage=='pipe_close':owned.processes[0].stdout.close=lambda:None


def test_cleanup_only_stop_interrupt_survives_later_pipe_close_error(tmp_path,monkeypatch):
    owner=bound(tmp_path,timeout=.1)
    interrupt=KeyboardInterrupt('interrupt after stop effect')
    cleanup=OSError('pipe close after effect')
    with existing.owned_children(monkeypatch) as owned:
        original_stop=runtime._stop;original_spawn=runtime.subprocess.Popen
        def stop(process):original_stop(process);raise interrupt
        def spawn(*args,**kwargs):
            process=original_spawn(*args,**kwargs);original_close=process.stdout.close
            def close():original_close();raise cleanup
            process.stdout.close=close
            return process
        monkeypatch.setattr(runtime,'_stop',stop);monkeypatch.setattr(runtime.subprocess,'Popen',spawn)
        try:
            with pytest.raises(OSError) as caught:owner._run(b'input')
            assert caught.value is cleanup
            assert interrupt in list(existing.causes(caught.value))
            existing.assert_reaped(owned)
            assert owner._cleanup_uncertain is None
            assert owned.stops==[owned.processes[0].pid]
        finally:
            if owned.processes:owned.processes[0].stdout.close=lambda:None


@pytest.mark.parametrize('outer_exit_failure',[False,True])
def test_failed_stop_keeps_unknown_hold_and_unrelated_child_untouched(tmp_path,monkeypatch,outer_exit_failure):
    owner=bound(tmp_path,timeout=2)
    primary=KeyboardInterrupt('synthetic owned cancellation');stop_error=OSError('stop before effect')
    outer=OSError('outer snapshot exit after effect')
    with existing.owned_children(monkeypatch) as owned:
        unrelated=owned.popen([sys.executable,'-I','-c','import time;time.sleep(30)'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            existing.fail_wait(monkeypatch,owned,primary)
            attempts=[]
            def stop(process):
                assert process in owned.processes
                attempts.append(process.pid);raise stop_error
            monkeypatch.setattr(runtime,'_stop',stop)
            if outer_exit_failure:
                original=boundary.executable_snapshot
                @contextmanager
                def snapshot(*args,**kwargs):
                    try:
                        with original(*args,**kwargs) as executable:yield executable
                    finally:raise outer
                monkeypatch.setattr(boundary,'executable_snapshot',snapshot)
            with pytest.raises(runtime.RuntimeCleanupUncertain) as caught:owner._run(b'input')
            failures=list(existing.causes(caught.value))
            assert primary in failures and stop_error in failures
            if outer_exit_failure:assert outer in failures and caught.value.__cause__ is outer
            held=owner._cleanup_uncertain
            assert held is caught.value and held.process is owned.processes[0]
            assert attempts==[owned.processes[0].pid]
            assert held.process.poll() is None and unrelated.poll() is None
            assert_held_no_launch(owner,monkeypatch,held)
            # Explicit test custody cleanup does not silently clear production
            # uncertainty, and must never signal the unrelated process.
            owned.stop(held.process);owned.waits[held.process](timeout=5)
            assert unrelated.poll() is None
            assert_held_no_launch(owner,monkeypatch,held)
        finally:
            unrelated.kill();unrelated.wait(timeout=5)


def fd_tracking(monkeypatch,binary):
    original_open=os.open;original_memfd=os.memfd_create;owned={}
    def opened(path,*args,**kwargs):
        fd=original_open(path,*args,**kwargs)
        if Path(path)==binary:owned['source']=fd
        return fd
    def created(*args,**kwargs):
        fd=original_memfd(*args,**kwargs);owned['snapshot']=fd;return fd
    monkeypatch.setattr(os,'open',opened);monkeypatch.setattr(os,'memfd_create',created)
    return owned


@pytest.mark.parametrize('stage',['memfd','read','write','seal','digest'])
def test_snapshot_failure_closes_every_acquired_fd_and_never_launches(tmp_path,monkeypatch,stage):
    owner=bound(tmp_path,'print(1)');original_read=os.read;original_write=os.write
    import fcntl
    original_fcntl=fcntl.fcntl
    owned=fd_tracking(monkeypatch,owner.binary)
    def failed(*args,**kwargs):raise OSError('synthetic '+stage+' failure')
    if stage=='memfd':monkeypatch.setattr(os,'memfd_create',failed)
    elif stage=='read':
        def read(fd,count):return failed() if fd==owned.get('source') else original_read(fd,count)
        monkeypatch.setattr(os,'read',read)
    elif stage=='write':
        def write(fd,data):return failed() if fd==owned.get('snapshot') else original_write(fd,data)
        monkeypatch.setattr(os,'write',write)
    elif stage=='seal':
        def control(fd,operation,*args):
            if fd==owned.get('snapshot') and operation==getattr(fcntl,'F_ADD_SEALS',1033):return failed()
            return original_fcntl(fd,operation,*args)
        monkeypatch.setattr(fcntl,'fcntl',control)
    else:owner.binary_sha256='0'*64
    def forbidden(*args,**kwargs):raise AssertionError('snapshot failed before child launch')
    monkeypatch.setattr(runtime.subprocess,'Popen',forbidden)
    with pytest.raises((OSError,transport.ShadowFailure)):owner._run(b'input')
    assert owned
    for fd in owned.values():
        with pytest.raises(OSError):os.fstat(fd)
    assert owner._cleanup_uncertain is None


@pytest.mark.parametrize('target',['source','snapshot'])
@pytest.mark.parametrize('after_effect',[False,True])
@pytest.mark.parametrize('error_type',[OSError,KeyboardInterrupt])
def test_ambiguous_snapshot_close_holds_binding_without_reclosing_reused_fd(tmp_path,monkeypatch,target,after_effect,error_type):
    owner=bound(tmp_path,"print('done')",timeout=2)
    original_close=os.close;original_open=os.open;reused=[];injected=[]
    with monkeypatch.context() as patch:
        owned=fd_tracking(patch,owner.binary)
        fault=error_type('ambiguous '+target+' close')
        def close(fd):
            if fd==owned.get(target) and not injected:
                injected.append(fd)
                if after_effect:
                    original_close(fd)
                    reused.append(original_open('/dev/null',os.O_RDONLY))
                    assert reused[-1]==fd
                raise fault
            return original_close(fd)
        patch.setattr(os,'close',close)
        with pytest.raises(runtime.RuntimeCleanupUncertain) as caught:owner._run(b'input')
        assert fault in list(existing.causes(caught.value))
        held=owner._cleanup_uncertain
        assert held is caught.value and len(injected)==1
        assert_held_no_launch(owner,patch,held)
    for kind,fd in owned.items():
        if kind==target:
            os.fstat(fd)  # Original leaked fd or unrelated reused fd remains ours to diagnose.
        else:
            with pytest.raises(OSError):os.fstat(fd)
    original_close(owned[target])
    assert_held_no_launch(owner,monkeypatch,held)


def test_snapshot_is_sealed_and_execution_does_not_reopen_replaced_source(tmp_path):
    binary,digest=fixture_binary(tmp_path,"print('original')")
    with transport.executable_snapshot(binary,digest) as executable:
        assert sha256(executable.read_bytes()).hexdigest()==digest
        with pytest.raises(OSError):
            with executable.open('wb') as stream:stream.write(b'forged')
        binary.write_text(f'#!{sys.executable} -I\nprint("replacement")\n')
        assert transport._exchange(executable,'boundary-v1',b'input',2)==b'original\n'


REQUEST=b'{"jsonrpc":"2.0","id":7,"method":"tools/list","params":{}}'


def assert_current_bindings():
    for module in (boundary.agent_api,boundary.agent_mcp,boundary.control_contracts,boundary.session,
                   boundary.request_boundary_contracts,transport,runtime):
        assert sha256(Path(module.__file__).read_bytes()).hexdigest()==boundary.SOURCE_SHA256[module.__name__.rsplit('.',1)[-1]]


@pytest.mark.parametrize('stage',['primary','stop_after_effect','stop_before_effect'])
def test_observer_never_swallows_cancellation_or_reports_stale_match(tmp_path,monkeypatch,stage):
    assert_current_bindings()
    owner=bound(tmp_path,timeout=2);primary=KeyboardInterrupt('observer cancellation')
    cleanup=OSError('observer stop fault')
    owner.last_observation={'status':'matched','execution_authorized':False}
    with existing.owned_children(monkeypatch) as owned:
        existing.fail_wait(monkeypatch,owned,primary)
        if stage!='primary':
            original=runtime._stop
            def stop(process):
                if stage=='stop_after_effect':original(process)
                raise cleanup
            monkeypatch.setattr(runtime,'_stop',stop)
        if stage=='primary':
            with pytest.raises(KeyboardInterrupt) as caught:owner.prefix(REQUEST)
            assert caught.value is primary
            assert owner.last_observation=={'status':'interrupted','reason':'observation_interrupted','execution_authorized':False}
            existing.assert_reaped(owned)
        else:
            with pytest.raises(BaseExceptionGroup) as caught:owner.prefix(REQUEST)
            failures=list(existing.causes(caught.value))
            assert primary in failures and cleanup in failures
            assert owner.last_observation['execution_authorized'] is False
            if stage=='stop_after_effect':
                assert owner.last_observation['status']=='fallback'
                existing.assert_reaped(owned)
                assert owner._cleanup_uncertain is None
            else:
                assert owner.last_observation=={'status':'held','reason':'cleanup_uncertain','execution_authorized':False}
                held=owner._cleanup_uncertain
                assert held in failures and held.process.poll() is None
                def forbidden(*args,**kwargs):raise AssertionError('held observer must not launch')
                with monkeypatch.context() as patch:
                    patch.setattr(runtime.subprocess,'Popen',forbidden)
                    assert owner.prefix(REQUEST)==boundary.classify(REQUEST,frozenset())[0]
                assert owner.last_observation['status']=='held'


@pytest.mark.parametrize('after_effect',[False,True])
def test_prelaunch_digest_close_uncertainty_holds_before_any_further_file_work(tmp_path,monkeypatch,after_effect):
    assert_current_bindings()
    owner=bound(tmp_path,"print('not launched')",timeout=2)
    target=Path(boundary.agent_api.__file__)
    original_open=os.open;original_close=os.close;opened=[];owned=[];injected=[]
    cleanup=OSError('prelaunch digest close ambiguous')
    try:
        with monkeypatch.context() as patch:
            def open_file(path,*args,**kwargs):
                fd=original_open(path,*args,**kwargs);opened.append(Path(path))
                if Path(path)==target:owned.append(fd)
                return fd
            def close(fd):
                if owned and fd==owned[0] and not injected:
                    injected.append(fd)
                    if after_effect:
                        original_close(fd)
                        assert original_open('/dev/null',os.O_RDONLY)==fd
                    raise cleanup
                return original_close(fd)
            def forbidden(*args,**kwargs):raise AssertionError('prelaunch uncertainty must prevent child')
            patch.setattr(os,'open',open_file);patch.setattr(os,'close',close)
            patch.setattr(runtime.subprocess,'Popen',forbidden)
            assert owner.prefix(REQUEST)==boundary.classify(REQUEST,frozenset())[0]
            held=owner._cleanup_uncertain
            assert isinstance(held,runtime.RuntimeCleanupUncertain)
            assert cleanup in list(existing.causes(held))
            assert owner.last_observation=={'status':'held','reason':'cleanup_uncertain','execution_authorized':False}
            assert opened==[target] and len(injected)==1
            patch.setattr(os,'open',forbidden)
            assert owner.prefix(REQUEST)==boundary.classify(REQUEST,frozenset())[0]
            assert owner.last_observation['status']=='held'
        os.fstat(owned[0])
    finally:
        for fd in owned:
            try:original_close(fd)
            except OSError:pass  # Test teardown only; production never retries ambiguous close.
    assert_held_no_launch(owner,monkeypatch,held)


@pytest.mark.parametrize('after_effect',[False,True])
def test_paired_snapshot_close_failures_preserve_both_descriptors_and_cancellation(tmp_path,monkeypatch,after_effect):
    assert_current_bindings()
    owner=bound(tmp_path,"print('completed child')",timeout=2)
    original_close=os.close;original_open=os.open;injected=[];owned={}
    interruption=KeyboardInterrupt('snapshot close interrupted')
    source_error=OSError('source close failed')
    faults={'snapshot':interruption,'source':source_error}
    try:
        with monkeypatch.context() as patch:
            owned=fd_tracking(patch,owner.binary)
            def close(fd):
                # _observe first digests the binary through its own descriptor.
                # Fault only once both executable-snapshot descriptors exist.
                targets=[name for name in ('snapshot','source')
                         if 'snapshot' in owned and owned[name]==fd and name not in injected]
                if targets:
                    name=targets[0];injected.append(name)
                    if after_effect:
                        original_close(fd)
                        assert original_open('/dev/null',os.O_RDONLY)==fd
                    raise faults[name]
                return original_close(fd)
            patch.setattr(os,'close',close)
            with pytest.raises(BaseExceptionGroup) as caught:owner.prefix(REQUEST)
            failures=list(existing.causes(caught.value))
            assert interruption in failures and source_error in failures
            held=owner._cleanup_uncertain
            assert isinstance(held,runtime.RuntimeCleanupUncertain)
            descriptors={error.descriptor for error in failures if isinstance(error,transport.ShadowSnapshotUncertain)}
            assert descriptors >= set(owned.values())
            assert injected==['snapshot','source']
            assert owner.last_observation=={'status':'held','reason':'cleanup_uncertain','execution_authorized':False}
            def forbidden(*args,**kwargs):raise AssertionError('paired descriptor hold must prevent file work or launch')
            patch.setattr(os,'open',forbidden);patch.setattr(runtime.subprocess,'Popen',forbidden)
            assert owner.prefix(REQUEST)==boundary.classify(REQUEST,frozenset())[0]
        for fd in owned.values():os.fstat(fd)  # Neither possibly reused fd was retried/closed.
    finally:
        for fd in set(owned.values()):
            try:original_close(fd)
            except OSError:pass  # Explicit test-only cleanup of known synthetic descriptors.
    assert_held_no_launch(owner,monkeypatch,held)


DRIVER = r'''
import json,os,signal,subprocess,sys,threading,time
from pathlib import Path
from unittest.mock import patch
from ciw import request_boundary as boundary
from ciw.adapters import subprocess as runtime
root=Path(sys.argv[1]);binary=Path(sys.argv[2]);digest=sys.argv[3]
signal.signal(signal.SIGINT,signal.default_int_handler)
owner=boundary.RequestBoundary(enabled=True,binary=binary,binary_sha256=digest,timeout=2)
processes=[];readers=[];waits=[]
original_popen=subprocess.Popen;original_thread=threading.Thread
def popen(*args,**kwargs):
 process=original_popen(*args,**kwargs);processes.append(process)
 original=process.wait;waits.append(original);first=True
 def wait(timeout=None):
  nonlocal first
  if first:
   first=False
   for reader in readers:
    reader.join(timeout=5)
    assert not reader.is_alive()
   (root/'ready.json').write_text(json.dumps({'pid':process.pid,'readers_eof':True}))
  return original(timeout=timeout)
 process.wait=wait
 return process
def thread(*args,**kwargs):
 reader=original_thread(*args,**kwargs);readers.append(reader);return reader
try:
 with patch.object(runtime.subprocess,'Popen',popen),patch.object(runtime.threading,'Thread',thread):
  try:owner._run(b'block')
  except KeyboardInterrupt:cancelled=True
  else:cancelled=False
 process=processes[0]
 try:os.waitpid(process.pid,os.WNOHANG);reaped=False
 except ChildProcessError:reaped=True
 followup=owner._run(b'followup')
 report={'cancelled':cancelled,'returncode':process.returncode,'reaped':reaped,
  'readers_stopped':not any(r.is_alive() for r in readers),
  'pipes_closed':process.stdout.closed and process.stderr.closed,
  'held':owner._cleanup_uncertain is not None,'followup':followup.decode()}
 (root/'result.json').write_text(json.dumps(report))
 deadline=time.monotonic()+15
 while not (root/'release').exists():
  if time.monotonic()>deadline:raise RuntimeError('test parent did not release owner')
  time.sleep(.01)
finally:
 for process,wait in zip(processes,waits):
  if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
  wait(timeout=5)
  process.stdout.close();process.stderr.close()
'''


def test_actual_sigint_keeps_shadow_owner_alive_through_reap_and_followup(tmp_path):
    binary,digest=fixture_binary(tmp_path,"""import os,sys,time
if sys.stdin.buffer.read()==b'followup':print('recovered')
else:os.close(1);os.close(2);time.sleep(20)
""")
    env={**os.environ,'PYTHONPATH':str(Path(ciw.__file__).resolve().parents[1])}
    driver=subprocess.Popen([sys.executable,'-c',DRIVER,str(tmp_path),str(binary),digest],
        env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def read(name):
        path=tmp_path/name;deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            assert driver.poll() is None
            try:return json.loads(path.read_text())
            except (FileNotFoundError,json.JSONDecodeError):time.sleep(.01)
        raise AssertionError('test driver did not reach '+name)
    try:
        assert read('ready.json')['readers_eof'] is True
        driver.send_signal(signal.SIGINT)
        assert read('result.json')=={'cancelled':True,'returncode':-signal.SIGKILL,'reaped':True,
            'readers_stopped':True,'pipes_closed':True,'held':False,'followup':'recovered\n'}
        assert driver.poll() is None
    finally:
        (tmp_path/'release').touch()
        try:out,err=driver.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            driver.kill();out,err=driver.communicate(timeout=5)
        assert driver.returncode==0,out+err
