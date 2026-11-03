"""AGPL-3.0-or-later. Shadow observations reuse existing subprocess custody.

The fixed child receives only LANG in its environment. This is not a network or
filesystem sandbox; detached sessions remain outside process-group containment.
"""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import os
from pathlib import Path
import stat
import subprocess
import sys

from .adapters.protocol import AdapterRefusal
from .adapters.subprocess import _bounded_process, RuntimeCleanupUncertain

MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 4096


class ShadowFailure(Exception):
    pass


class ShadowSnapshotUncertain(RuntimeCleanupUncertain):
    """An owned descriptor close was ambiguous; never retry or launch again."""
    def __init__(self, descriptor):
        self.descriptor = descriptor  # Host diagnostic only, never auto-closed.
        super().__init__()


def _close_owned(descriptor):
    try:
        os.close(descriptor)
    except BaseException as exc:
        raise ShadowSnapshotUncertain(descriptor) from exc


@contextmanager
def executable_snapshot(binary: Path, expected_sha256: str):
    """Execute exactly the verified bytes from a sealed, host-created Linux fd.

    No mutable path is re-opened for execution. No fallback to an unsealed path
    is allowed. Closing either owned descriptor is attempted once; ExitStack
    preserves primary/cleanup context and still closes the other descriptor.
    """
    if sys.platform != 'linux' or not hasattr(os, 'memfd_create'):
        raise ShadowFailure('snapshot_unavailable')
    import fcntl
    if (not isinstance(binary, Path) or not binary.is_absolute() or '..' in binary.parts
            or any(p.is_symlink() for p in (binary, *binary.parents))):
        raise ShadowFailure('binary_binding')
    # Stable Linux UAPI from linux/fcntl.h; some Python builds omit these names.
    # A kernel that cannot enforce them refuses below; there is no path fallback.
    add_seals = getattr(fcntl, 'F_ADD_SEALS', 1033)
    get_seals = getattr(fcntl, 'F_GET_SEALS', 1034)
    seals = 0x0008 | 0x0004 | 0x0002 | 0x0001
    close_failures = []
    def close(descriptor):
        try:
            _close_owned(descriptor)
        except BaseException as exc:
            close_failures.append(exc)
            raise
    def preserve_close_failures(exc_type, exc, traceback):
        if exc is not None and close_failures:
            previous, seen = [], {id(exc)}
            for failure in (*close_failures, exc.__cause__, exc.__context__):
                if failure is not None and id(failure) not in seen:
                    seen.add(id(failure))
                    previous.append(failure)
            if previous:
                # Keep every ambiguous descriptor and cancellation even when a
                # second callback replaces ExitStack's cleanup-only context.
                exc.__cause__ = BaseExceptionGroup('Shadow descriptor cleanup diagnostics', previous)
    with ExitStack() as cleanup:
        cleanup.push(preserve_close_failures)
        source_fd = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        cleanup.callback(close, source_fd)
        info = os.fstat(source_fd)
        if (not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111
                or info.st_mode & 0o6000 or info.st_size > MAX_INPUT):
            raise ShadowFailure('binary_binding')
        snapshot_fd = os.memfd_create('odsp-request-boundary', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        cleanup.callback(close, snapshot_fd)
        digest = sha256()
        count = 0
        while block := os.read(source_fd, min(65536, MAX_INPUT + 1 - count)):
            count += len(block)
            if count > MAX_INPUT:
                raise ShadowFailure('file_budget')
            digest.update(block)
            offset = 0
            while offset < len(block):
                written = os.write(snapshot_fd, block[offset:])
                if written <= 0:
                    raise ShadowFailure('snapshot_io')
                offset += written
        if digest.hexdigest() != expected_sha256:
            raise ShadowFailure('binary_binding')
        os.fchmod(snapshot_fd, 0o500)
        fcntl.fcntl(snapshot_fd, add_seals, seals)
        if fcntl.fcntl(snapshot_fd, get_seals) & seals != seals:
            raise ShadowFailure('snapshot_unsealed')
        yield Path('/proc') / str(os.getpid()) / 'fd' / str(snapshot_fd)


def _exchange(binary: Path, mode: str, raw: bytes, timeout: float) -> bytes:
    if (not isinstance(binary, Path) or not binary.is_absolute() or mode != 'boundary-v1'
            or type(raw) is not bytes or len(raw) > MAX_INPUT
            or type(timeout) not in (int, float) or not 0 < timeout <= 2):
        raise ShadowFailure('configuration')
    try:
        code, output = _bounded_process([str(binary), mode], cwd=Path('/'),
            timeout=timeout, limit=MAX_OUTPUT, stdin=raw, environment={'LANG':'C.UTF-8'})
    except RuntimeCleanupUncertain:
        # The persistent RequestBoundary guard records this before any later
        # shadow launch. A diagnostic fallback cannot clear unknown custody.
        raise
    except AdapterRefusal as exc:
        reason = {'TIMEOUT':'timeout', 'OUTPUT_LIMIT':'output_budget',
                  'RUNTIME_UNAVAILABLE':'process_unavailable'}.get(exc.code, 'process_error')
        raise ShadowFailure(reason) from exc
    if code != 0:
        raise ShadowFailure('process_exit')
    return output
