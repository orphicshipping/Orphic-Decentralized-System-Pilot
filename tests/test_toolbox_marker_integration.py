"""Real recipe marker custody and owner-lock release; local synthetic captures."""
import os
import stat
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ciw import toolbox_custody as custody
from ciw.retention import _owner_dir
from test_campaign_audited_host import bound
from test_toolbox_campaign import runner, run


@pytest.mark.parametrize('primary_type', [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('close_fails', [False, True])
def test_marker_sync_fault_never_dispatches_and_releases_owner_lock(tmp_path, monkeypatch, primary_type, close_fails):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    wrapped, profile, _, _ = bound(tmp_path)
    active = runner(wrapped, [('ncp_domain_read_describe', {'domain': 'finance'})])
    error = primary_type('synthetic directory sync fault')
    cleanup = OSError('synthetic post-close error')
    opened, closed, replacement = [], [], []

    def opening(path, flags):
        fd = os.open(path, flags)
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
        opened.append(fd)
        return fd

    def syncing(fd):
        raise error

    def closing(fd):
        closed.append(fd)
        os.close(fd)
        if close_fails:
            new = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
            replacement.append(new)
            assert new == fd, 'fixture could not establish exact descriptor reuse'
            raise cleanup

    view = SimpleNamespace(name=os.name, O_DIRECTORY=os.O_DIRECTORY, O_NOFOLLOW=os.O_NOFOLLOW,
        O_CLOEXEC=os.O_CLOEXEC, O_RDONLY=os.O_RDONLY, open=opening, fsync=syncing, close=closing)
    monkeypatch.setattr(custody, 'os', view)
    try:
        with patch.object(profile, 'call', side_effect=AssertionError('marker failure dispatched')) as calls:
            with pytest.raises(primary_type) as caught:
                run(active)
            assert caught.value is error
            assert len(opened) == 1 and closed == opened
            assert calls.call_count == 0
            directory = _owner_dir(active._store.owner)
            # A separate open-file description must be able to reacquire this
            # actual flock after cancellation; the test process never retries
            # the ambiguous directory close.
            import fcntl
            fd = os.open(directory / 'sequence.lock', os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
            assert len(list(directory.glob('toolbox-attempt-*.json'))) == 1
            assert active._directory_sync.snapshot()['effect_state'] == 'not_started'
            if close_fails:
                for attempt in ('first', 'second'):
                    with pytest.raises(custody.DirectorySyncRefused):
                        run(active, attempt)
                assert len(list(directory.glob('toolbox-attempt-*.json'))) == 1
                assert len(opened) == 1 and calls.call_count == 0
                assert stat.S_ISCHR(os.fstat(replacement[0]).st_mode)
    finally:
        for fd in replacement:
            os.close(fd)  # Test owns this distinct /dev/null descriptor.


def test_receipt_rebind_during_store_read_refuses_old_disclosure(tmp_path, monkeypatch):
    from test_domain_read_profile import capture, host
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    wrapped, _, _, _ = bound(tmp_path)
    active = runner(wrapped, [('ncp_domain_read_describe', {'domain': 'finance'})])
    result = run(active)
    original = active._store.native
    changed = capture()
    changed['records'][0]['data']['price'] = '2.00'

    def native(artifact):
        value = original(artifact)
        with wrapped.facade.policy.lock:
            wrapped.facade.hosts['domain_read'] = host(changed)
        return value

    with patch.object(active._store, 'native', side_effect=native):
        with pytest.raises(ValueError, match='scope_changed'):
            active.receipt(result['artifact_id'])
