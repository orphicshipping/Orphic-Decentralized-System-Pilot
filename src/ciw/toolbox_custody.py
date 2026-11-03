"""Host-owned custody for the skill attempt marker's directory fsync.

SPDX-License-Identifier: AGPL-3.0-or-later
This helper admits no operation. Its effect state concerns scientific dispatch,
not rollback of the marker which the caller may already have published.
"""
from copy import deepcopy
import os
from threading import RLock


class DirectorySyncRefused(RuntimeError):
    """Directory synchronization is unavailable or its prior close is unresolved."""


def _paired_failure(primary, cleanup):
    """Preserve cancellation as the top exception, ordinary primary otherwise."""
    if primary is None:
        return cleanup
    chosen = primary
    if isinstance(primary, Exception) and not isinstance(cleanup, Exception):
        chosen = cleanup
    other = cleanup if chosen is primary else primary
    diagnostics, seen = [], {id(chosen)}
    for error in (other, chosen.__cause__, chosen.__context__):
        if error is not None and id(error) not in seen:
            diagnostics.append(error)
            seen.add(id(error))
    # Retain the original objects, including a primary's existing cause. A
    # BaseExceptionGroup also preserves a second cancellation in cleanup.
    if diagnostics:
        chosen.__cause__ = BaseExceptionGroup('Directory synchronization cleanup diagnostics', diagnostics)
    return chosen


class DirectorySyncOwner:
    """Keep this object for the lifetime of its host-bound SkillRunner.

    sync() attempts one directory open/fsync/close. An observed close error is
    ambiguous even if a fault happened after the kernel closed the descriptor.
    The descriptor number is diagnostic only: never retry it, inspect a reused
    descriptor to infer ownership, or close it from a destructor. A held owner
    refuses every later sync before filesystem I/O. There is no reset API.

    snapshot() returns bounded, detached host diagnostics without paths, error
    messages, or arguments. Original exceptions remain in the private chain.
    This is POSIX directory synchronization, not a power-loss durability proof.
    """

    def __init__(self):
        self._lock = RLock()
        self._held_failure = None
        self._diagnostic = self._fresh('idle')

    @staticmethod
    def _fresh(phase):
        return {'schema': 'ciw.toolbox-directory-custody.v1',
                'effect_state': 'not_started', 'phase': phase,
                'descriptor': None, 'close_attempted': False,
                'close_resolved': None, 'held': False, 'exceptions': []}

    def snapshot(self):
        with self._lock:
            return deepcopy(self._diagnostic)

    def check(self):
        """Refuse held custody before the caller publishes another marker."""
        with self._lock:
            if self._held_failure is not None:
                raise DirectorySyncRefused('directory_sync_custody_unresolved') from self._held_failure

    def _record(self, phase, error):
        # There are at most two owned failures per sync: open/fsync and close.
        self._diagnostic['exceptions'].append({
            'phase': phase, 'type': type(error).__name__[:80]})

    def sync(self, directory):
        with self._lock:
            self.check()
            self._diagnostic = self._fresh('open')
            descriptor = None
            primary = None
            try:
                try:
                    if os.name != 'posix' or any(not hasattr(os, name) for name in
                                                 ('O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC')):
                        raise DirectorySyncRefused('directory_sync_platform_unavailable')
                    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                                         os.O_NOFOLLOW | os.O_CLOEXEC)
                    self._diagnostic.update(phase='fsync', descriptor=descriptor)
                    os.fsync(descriptor)
                except BaseException as error:
                    primary = error
                    self._record(self._diagnostic['phase'], error)
            finally:
                if descriptor is not None:
                    owned, descriptor = descriptor, None
                    self._diagnostic.update(phase='close', close_attempted=True)
                    try:
                        os.close(owned)
                    except BaseException as cleanup:
                        # Record the hold before propagating any primary/cancellation.
                        self._held_failure = cleanup
                        self._diagnostic.update(held=True, close_resolved=False)
                        self._record('close', cleanup)
                        failure = _paired_failure(primary, cleanup)
                        self._held_failure = failure
                        raise failure.with_traceback(failure.__traceback__)
                    self._diagnostic.update(phase='closed', descriptor=None, close_resolved=True)
            if primary is not None:
                raise primary.with_traceback(primary.__traceback__)

