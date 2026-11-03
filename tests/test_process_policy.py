"""Actual Linux limits and explicit sandbox refusal, never a network-off claim."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from ciw.adapters import subprocess as runtime
from ciw.adapters.protocol import AdapterRefusal
from ciw.process_policy import ProcessPolicy, ProcessPolicyHeld, executable_sha256
from test_subprocess_adapter import checkout

pytestmark = pytest.mark.skipif(sys.platform != "linux" or not Path('/usr/bin/prlimit').is_file(),
                               reason="Actual Linux prlimit qualification requires its installed executable")


def policy(**kwargs):
    return ProcessPolicy(expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'), **kwargs)


def run(tmp_path, code, *, selected=None, stdin=b''):
    return runtime._bounded_process([sys.executable, '-I', '-c', code], cwd=tmp_path,
        timeout=10, limit=32768, stdin=stdin, process_policy=selected or policy())


def test_child_has_only_explicit_environment_and_actual_limits(tmp_path, monkeypatch):
    monkeypatch.setenv('CAMPAIGN_SYNTHETIC_PRIVATE_CANARY', 'must-not-inherit')
    selected = policy(cpu_seconds=2, memory_bytes=256*1024*1024, file_bytes=8192,
                      max_open_files=40, max_processes=512)
    code, raw = run(tmp_path, """import json,os,resource
print(json.dumps({'environment':dict(os.environ),'limits':{
 k:resource.getrlimit(getattr(resource,k)) for k in
 ('RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_NOFILE','RLIMIT_NPROC','RLIMIT_CORE')}}))
""", selected=selected)
    assert code == 0
    result = json.loads(raw)
    assert result['environment'] == dict(selected.environment)
    assert result['limits'] == {'RLIMIT_CPU':[2,2], 'RLIMIT_AS':[268435456,268435456],
        'RLIMIT_FSIZE':[8192,8192], 'RLIMIT_NOFILE':[40,40], 'RLIMIT_NPROC':[512,512], 'RLIMIT_CORE':[0,0]}
    assert selected.qualify(tmp_path) == {'status':'available','mode':'limits',
                                        'network_isolated':False,'filesystem_isolated':False}
    assert 'CAMPAIGN_SYNTHETIC_PRIVATE_CANARY' not in json.dumps(selected.identity())


def test_legacy_default_is_unchanged_and_selected_policy_is_identified(checkout):
    path, revision = checkout
    legacy = runtime.PinnedSubprocessAdapter(path, revision, 'example.endpoint')
    assert 'process_policy' not in legacy.runtime_identity()
    selected = policy(max_output_bytes=65536)
    bounded = runtime.PinnedSubprocessAdapter(path, revision, 'example.endpoint', process_policy=selected)
    assert bounded.invoke('evaluate.v1', {'x':7}) == legacy.invoke('evaluate.v1', {'x':7})
    assert bounded.runtime_identity()['process_policy'] == selected.identity()


def test_policy_is_copied_immutable_and_never_uses_payload_paths(tmp_path):
    env = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
    selected = policy(environment=env)
    env['FROM_MUTATION'] = 'bad'
    assert 'FROM_MUTATION' not in selected.environment
    with pytest.raises(TypeError): selected.environment['x'] = 'y'
    with pytest.raises(FrozenInstanceError): selected.cpu_seconds = 9
    with pytest.raises(ValueError): policy(environment={'PATH':'.:/usr/bin'})
    with pytest.raises(ValueError): policy(environment={'PATH':'/usr/bin','LD_PRELOAD':'bad'})
    for kwargs in ({'cpu_seconds':True},{'wall_seconds':float('nan')},{'max_open_files':5},
                   {'memory_bytes':-1},{'mode':'invented'},{'read_only_roots':('/',)},
                   {'read_only_roots':('/usr/..',)}):
        with pytest.raises(ValueError): policy(**kwargs)
    with pytest.raises(ValueError):
        runtime._bounded_process([sys.executable],cwd=tmp_path,timeout=1,limit=100,process_policy={})
    for broad in ('/','/tmp','/workspace',str(tmp_path/'..')):
        with pytest.raises(ValueError,match='working directory'): selected.qualify(Path(broad))


def test_wrong_launcher_pin_and_input_overflow_do_not_launch(tmp_path, monkeypatch):
    launches = []
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda *a,**k: launches.append(a))
    selected = ProcessPolicy(expected_prlimit_sha256='0'*64)
    with pytest.raises(ProcessPolicyHeld, match='pinned_launcher_changed'):
        run(tmp_path, "raise AssertionError('must not launch')", selected=selected)
    with pytest.raises(AdapterRefusal) as caught:
        run(tmp_path, "raise AssertionError('must not launch')", selected=policy(max_input_bytes=3), stdin=b'xxxx')
    assert caught.value.code == 'INPUT_LIMIT' and launches == []


@pytest.mark.parametrize('stream', ['stdout','stderr'])
def test_live_flood_is_bounded_and_owned_child_is_reaped(tmp_path, monkeypatch, stream):
    launches = []; original = subprocess.Popen
    def spawn(*a,**k):
        child = original(*a,**k); launches.append(child); return child
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    with pytest.raises(AdapterRefusal) as caught:
        run(tmp_path, f"import sys,time;sys.{stream}.write('z'*1000000);sys.{stream}.flush();time.sleep(20)",
            selected=policy(max_output_bytes=4096))
    assert caught.value.code == 'OUTPUT_LIMIT'
    assert len(launches) == 1 and launches[0].returncode == -signal.SIGKILL
    with pytest.raises(ChildProcessError): os.waitpid(launches[0].pid, os.WNOHANG)


def test_wall_timeout_and_descendant_pipe_are_cleaned(tmp_path):
    begin = time.monotonic()
    with pytest.raises(AdapterRefusal) as caught:
        run(tmp_path, 'import time;time.sleep(20)', selected=policy(wall_seconds=.15))
    assert caught.value.code == 'TIMEOUT' and time.monotonic()-begin < 6
    # The parent exits successfully; a descendant keeps the pipe and process
    # group alive. Existing group cleanup must also work through prlimit exec.
    code, raw = run(tmp_path, """import os,sys,time
pid=os.fork()
if pid==0: time.sleep(20)
else: print(pid,flush=True)
""")
    assert code == 0
    child = int(raw)
    state = Path(f'/proc/{child}/stat')
    # A non-child orphan can remain a zombie under the container's init, but
    # cannot remain executing or holding the output descriptors.
    assert not state.exists() or state.read_text().split()[2] == 'Z'


def test_cpu_memory_and_individual_file_limits_are_enforced(tmp_path):
    code, _ = run(tmp_path, 'while True: pass', selected=policy(cpu_seconds=1,wall_seconds=5))
    assert code == -signal.SIGKILL
    code, raw = run(tmp_path, """try:
 x=bytearray(300*1024*1024)
except MemoryError: print('bounded')
else: raise AssertionError('memory limit absent')
""", selected=policy(memory_bytes=96*1024*1024))
    assert (code,raw) == (0,b'bounded\n')
    code, _ = run(tmp_path, "open('bounded.bin','wb').write(b'x'*16384)", selected=policy(file_bytes=4096))
    assert code != 0 and (tmp_path/'bounded.bin').stat().st_size <= 4096


def test_limits_mode_does_not_claim_escaped_descendant_containment(tmp_path):
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        pytest.skip('Owned negative-containment probe requires Linux pidfd cleanup')
    selected=policy()
    code,raw=run(tmp_path,"""import os,time
child=os.fork()
if child==0:
 os.setsid();os.close(1);os.close(2);time.sleep(20)
else: print(child,flush=True)
""",selected=selected)
    assert code==0
    child=int(raw);handle=os.pidfd_open(child)
    try:
        status=Path(f'/proc/{child}/stat').read_text().split()
        assert status[2]!='Z'
        assert 'escaped sessions are not contained' in selected.identity()['descendant_cleanup']
        assert selected.qualify(tmp_path)['network_isolated'] is False
    finally:
        # This test explicitly owns its synthetic escaped child; production
        # limits mode cannot make this stronger containment assertion.
        signal.pidfd_send_signal(handle,signal.SIGKILL)
        os.close(handle)


def strict_policy(tmp_path):
    if not Path('/usr/bin/bwrap').is_file():
        pytest.skip('Bubblewrap executable is not installed; strict mode prerequisite is open')
    roots = ('/usr', str(Path(sys.executable).resolve().parents[1]), str(Path(sys.prefix).resolve()))
    return policy(mode='bubblewrap', read_only_roots=tuple(sorted(set(roots))),
                  expected_bwrap_sha256=executable_sha256('/usr/bin/bwrap'))


def test_strict_mode_refuses_unavailable_namespace_before_target(tmp_path):
    selected = strict_policy(tmp_path)
    marker = tmp_path/'target-ran'
    try:
        selected.qualify(tmp_path)
    except ProcessPolicyHeld as expected:
        assert expected.reason == 'namespace_probe_refused'
        with pytest.raises(ProcessPolicyHeld):
            run(tmp_path, "open('target-ran','w').write('unsafe')", selected=selected)
        assert not marker.exists()
    else:
        assert run(tmp_path, 'print(1)',selected=selected) == (0,b'1\n')


def test_strict_mount_order_never_overrides_readonly_source_with_workdir(tmp_path):
    selected=strict_policy(tmp_path)
    command=selected._sandbox(['/usr/bin/true'],tmp_path)
    assert command.index('--bind') < command.index('--ro-bind')


def test_strict_namespace_and_filesystem_positive_only_if_available(tmp_path):
    selected = strict_policy(tmp_path)
    try:
        selected.qualify(tmp_path)
    except ProcessPolicyHeld as exc:
        pytest.skip(f'Positive sandbox qualification blocked: {exc.reason}; no namespace settings changed')
    external = tmp_path.parent/'outside-allowed-mount.txt'
    external.write_text('synthetic outside fixture')
    code, raw = run(tmp_path, f"""import json,os
print(json.dumps({{'net':os.readlink('/proc/self/ns/net'),
 'outside':os.path.exists({str(external)!r}), 'passwd':os.path.exists('/etc/passwd')}}))
""", selected=selected)
    assert code == 0
    result = json.loads(raw)
    assert result['net'] != os.readlink('/proc/self/ns/net')
    assert result['outside'] is result['passwd'] is False
