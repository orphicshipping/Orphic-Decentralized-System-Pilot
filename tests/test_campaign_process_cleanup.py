"""Real policy-bound cancellation and after-effect cleanup regression probes."""
import os
from pathlib import Path
import sys

import pytest

from ciw.adapters import subprocess as runtime
from ciw.process_policy import ProcessPolicy, executable_sha256
import test_subprocess_cancellation as existing

pytestmark = pytest.mark.skipif(sys.platform != 'linux' or not Path('/usr/bin/prlimit').is_file(),
                               reason='Policy-bound process cleanup requires Linux prlimit')


class PolicyAdapter(runtime.PinnedSubprocessAdapter):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,process_policy=ProcessPolicy(
            expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit')),**kwargs)


def test_actual_sigint_reaps_policy_child_before_surviving_owner_continues(tmp_path,monkeypatch):
    driver=existing.DRIVER.replace('from ciw.adapters import subprocess as runtime',
        "from ciw.adapters import subprocess as runtime\nfrom ciw.process_policy import ProcessPolicy,executable_sha256\nselected=ProcessPolicy(expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'))")
    driver=driver.replace('cwd=root,timeout=20,limit=4096)',
                          'cwd=root,timeout=20,limit=4096,process_policy=selected)')
    driver=driver.replace('cwd=root,timeout=5,limit=4096)',
                          'cwd=root,timeout=5,limit=4096,process_policy=selected)')
    assert driver.count('process_policy=selected')==2
    monkeypatch.setattr(existing,'DRIVER',driver)
    existing.test_real_sigint_after_closed_pipes_reaps_before_alive_parent_followup(tmp_path)


def test_policy_child_signal_after_effect_error_preserves_cancellation_and_reap(tmp_path,monkeypatch):
    adapter=existing.bound_fixture(tmp_path,PolicyAdapter)
    primary=KeyboardInterrupt('synthetic cancellation'); after=OSError('signal after effect')
    with monkeypatch.context() as patch, existing.owned_children(patch) as owned:
        existing.fail_wait(patch,owned,primary)
        original=runtime._stop
        def stop(process):
            original(process)
            raise after
        patch.setattr(runtime,'_stop',stop)
        with pytest.raises(OSError) as caught: adapter._run(existing.CLOSED,[])
        assert caught.value is after and primary in list(existing.causes(after))
        existing.assert_reaped(owned)
        assert owned.stops==[owned.processes[0].pid] and adapter._cleanup_uncertain is None
    assert adapter._run("print('recovered')",[])==(0,b'recovered\n')


def test_policy_child_failed_cleanup_holds_all_future_launches(tmp_path,monkeypatch):
    adapter=existing.bound_fixture(tmp_path,PolicyAdapter)
    primary=KeyboardInterrupt('cancelled'); failure=OSError('signal before effect')
    with monkeypatch.context() as patch, existing.owned_children(patch) as owned:
        existing.fail_wait(patch,owned,primary)
        attempts=[]
        def stop(process):
            attempts.append(process.pid)
            raise failure
        patch.setattr(runtime,'_stop',stop)
        with pytest.raises(runtime.RuntimeCleanupUncertain) as caught: adapter._run(existing.CLOSED,[])
        assert primary in list(existing.causes(caught.value)) and failure in list(existing.causes(caught.value))
        assert caught.value.process is owned.processes[0]
        assert owned.processes[0].poll() is None and attempts==[owned.processes[0].pid]
        def forbidden(*a,**k): raise AssertionError('uncertain policy binding must not launch')
        patch.setattr(runtime.subprocess,'Popen',forbidden)
        with pytest.raises(runtime.RuntimeCleanupUncertain): adapter._run('print(1)',[])
        with pytest.raises(runtime.RuntimeCleanupUncertain): adapter.runtime_identity()
        assert len(owned.processes)==1
