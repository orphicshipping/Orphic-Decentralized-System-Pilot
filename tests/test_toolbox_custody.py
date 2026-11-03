"""Real directory-descriptor faults; no provider or scientific execution."""
import errno
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from ciw import toolbox_custody as custody


pytestmark = pytest.mark.skipif(
    os.name != 'posix' or any(not hasattr(os, name) for name in
                            ('O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC')),
    reason='Actual POSIX no-follow directory descriptor custody is required')


def causes(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


@pytest.fixture
def observed(tmp_path, monkeypatch):
    directory = tmp_path / 'owned-directory'
    directory.mkdir()
    original = SimpleNamespace(open=os.open, close=os.close, fsync=os.fsync)
    seen = SimpleNamespace(directory=directory, opened=[], closed=[], synced=[], original=original)

    def opening(path, flags, *args, **kwargs):
        fd = original.open(path, flags, *args, **kwargs)
        if Path(path) == directory:
            seen.opened.append(fd)
        return fd

    def closing(fd):
        seen.closed.append(fd)
        return original.close(fd)

    def syncing(fd):
        seen.synced.append(fd)
        return original.fsync(fd)

    # Replace only this helper's os view; never intercept pytest/other workers.
    view = SimpleNamespace(name=os.name, O_DIRECTORY=os.O_DIRECTORY,
        O_NOFOLLOW=os.O_NOFOLLOW, O_CLOEXEC=os.O_CLOEXEC, O_RDONLY=os.O_RDONLY,
        open=opening, close=closing, fsync=syncing)
    monkeypatch.setattr(custody, 'os', view)
    seen.view = view
    return seen


def assert_closed(fd):
    with pytest.raises(OSError) as caught:
        os.fstat(fd)
    assert caught.value.errno == errno.EBADF


def assert_held(owner, observed, expected):
    snapshot = owner.snapshot()
    assert snapshot['held'] is True
    assert snapshot['effect_state'] == 'not_started'
    assert snapshot['phase'] == 'close'
    assert snapshot['close_attempted'] is True and snapshot['close_resolved'] is False
    assert snapshot['descriptor'] == expected
    assert len(json.dumps(snapshot).encode()) < 1024
    snapshot['exceptions'].clear()
    assert owner.snapshot()['exceptions']
    calls = (list(observed.opened), list(observed.closed), list(observed.synced))
    for _ in range(2):
        with pytest.raises(custody.DirectorySyncRefused, match='custody_unresolved'):
            owner.check()
        with pytest.raises(custody.DirectorySyncRefused, match='custody_unresolved'):
            owner.sync(observed.directory)
    assert calls == (observed.opened, observed.closed, observed.synced)


def test_check_does_not_open_or_mutate_unheld_custody(observed):
    owner = custody.DirectorySyncOwner()
    before = owner.snapshot()
    assert owner.check() is None
    assert owner.snapshot() == before
    assert observed.opened == observed.closed == observed.synced == []


def test_success_closes_real_directory_and_next_sync_is_allowed(observed):
    owner = custody.DirectorySyncOwner()
    assert owner.sync(observed.directory) is None
    fd, = observed.opened
    assert observed.synced == observed.closed == [fd]
    assert_closed(fd)
    assert owner.snapshot()['held'] is False
    assert owner.snapshot()['close_resolved'] is True
    assert owner.snapshot()['descriptor'] is None
    assert owner.sync(observed.directory) is None
    assert len(observed.opened) == len(observed.closed) == 2


@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt, SystemExit])
def test_fsync_failure_preserves_original_and_closes_real_descriptor(observed, error_type):
    owner = custody.DirectorySyncOwner()
    error = error_type('private synthetic fsync message')

    def failing(fd):
        observed.synced.append(fd)
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
        raise error

    observed.view.fsync = failing
    with pytest.raises(error_type) as caught:
        owner.sync(observed.directory)
    assert caught.value is error
    fd, = observed.opened
    assert observed.closed == [fd]
    assert_closed(fd)
    snapshot = owner.snapshot()
    assert snapshot['held'] is False and snapshot['close_resolved'] is True
    assert snapshot['exceptions'] == [{'phase': 'fsync', 'type': error_type.__name__}]
    assert 'private synthetic' not in json.dumps(snapshot)
    observed.view.fsync = observed.original.fsync
    owner.sync(observed.directory)


@pytest.mark.parametrize('after_effect', [False, True])
@pytest.mark.parametrize('primary_type', [None, OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('cleanup_type', [OSError, KeyboardInterrupt, SystemExit])
def test_close_ambiguity_is_held_once_and_never_closes_reused_fd(observed, after_effect, primary_type, cleanup_type):
    owner = custody.DirectorySyncOwner()
    primary = None if primary_type is None else primary_type('original fsync failure')
    cleanup = cleanup_type('private ambiguous close diagnostic')
    replaced, test_owned = [], []
    if primary is not None:
        def fsync(fd):
            observed.synced.append(fd)
            raise primary
        observed.view.fsync = fsync

    def close(fd):
        observed.closed.append(fd)
        if after_effect:
            observed.original.close(fd)
            # Reuse this exact number for a separately owned descriptor. Never
            # infer the original directory's ownership from the reused number.
            replacement = observed.original.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
            test_owned.append(replacement)
            # Refuse an unexpected allocation rather than dup2 over a descriptor
            # that a concurrent unrelated thread might have acquired.
            assert replacement == fd, 'fixture could not establish exact fd reuse'
            replaced.append(fd)
        else:
            test_owned.append(fd)
        raise cleanup

    observed.view.close = close
    try:
        expected = primary if primary is not None else cleanup
        if primary is not None and isinstance(primary, Exception) and not isinstance(cleanup, Exception):
            expected = cleanup
        with pytest.raises(type(expected)) as caught:
            owner.sync(observed.directory)
        assert caught.value is expected
        assert cleanup in list(causes(caught.value))
        if primary is not None:
            assert primary in list(causes(caught.value))
        fd, = observed.opened
        assert observed.closed == [fd]
        assert_held(owner, observed, fd)
        info = os.fstat(fd)
        assert stat.S_ISCHR(info.st_mode) if after_effect else stat.S_ISDIR(info.st_mode)
    finally:
        # Explicit test-only cleanup of a descriptor whose ownership this test
        # knows from its fault injector. The production helper never retries.
        for fd in test_owned:
            observed.original.close(fd)
    assert_held(owner, observed, observed.opened[0])
    assert replaced == ([observed.opened[0]] if after_effect else [])


@pytest.mark.parametrize('cancel_type', [KeyboardInterrupt, SystemExit])
def test_cancellation_in_close_remains_primary_with_original_sync_error(observed, cancel_type):
    owner = custody.DirectorySyncOwner()
    primary = OSError('fsync error')
    cancel = cancel_type('interrupted close')

    def fsync(fd):
        raise primary

    def close(fd):
        observed.closed.append(fd)
        raise cancel

    observed.view.fsync, observed.view.close = fsync, close
    try:
        with pytest.raises(cancel_type) as caught:
            owner.sync(observed.directory)
        assert caught.value is cancel
        assert primary in list(causes(cancel))
        assert_held(owner, observed, observed.opened[0])
    finally:
        observed.original.close(observed.opened[0])


def test_ordinary_primary_keeps_existing_cause_with_cleanup_error(observed):
    owner = custody.DirectorySyncOwner()
    predecessor = ValueError('private predecessor')
    primary = OSError('fsync failed')
    primary.__cause__ = predecessor
    cleanup = OSError('close failed')

    def fsync(fd):
        raise primary

    def close(fd):
        observed.closed.append(fd)
        raise cleanup

    observed.view.fsync, observed.view.close = fsync, close
    try:
        with pytest.raises(OSError) as caught:
            owner.sync(observed.directory)
        assert caught.value is primary
        assert {id(e) for e in causes(primary)} >= {id(primary), id(cleanup), id(predecessor)}
        assert_held(owner, observed, observed.opened[0])
    finally:
        observed.original.close(observed.opened[0])


@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt, SystemExit])
def test_reused_exception_object_is_preserved_without_empty_exception_group(observed, error_type):
    owner = custody.DirectorySyncOwner()
    error = error_type('same original error')

    def fsync(fd):
        raise error

    def close(fd):
        observed.closed.append(fd)
        raise error

    observed.view.fsync, observed.view.close = fsync, close
    try:
        with pytest.raises(error_type) as caught:
            owner.sync(observed.directory)
        assert caught.value is error
        assert_held(owner, observed, observed.opened[0])
    finally:
        observed.original.close(observed.opened[0])


def test_symlink_directory_refused_without_following_or_descriptor(observed, tmp_path):
    owner = custody.DirectorySyncOwner()
    link = tmp_path / 'link'
    link.symlink_to(observed.directory, target_is_directory=True)
    with pytest.raises(OSError):
        owner.sync(link)
    assert observed.opened == observed.synced == observed.closed == []
    assert not owner.snapshot()['held']
    assert owner.snapshot()['exceptions'][0]['phase'] == 'open'

