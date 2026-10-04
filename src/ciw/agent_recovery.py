"""Local agent checkpoints and a lifetime writer lock; never resumes provider work.

Copyright (c) 2026 Giason Pooni. SPDX-License-Identifier: AGPL-3.0-or-later
Hashes detect damaged retained bytes, not an authenticated operator history.
"""
from __future__ import annotations

from hashlib import sha256
import errno
import json
import os
from pathlib import Path
import re
import uuid

from .session import loads_json

MAX_CHECKPOINT = 8 * 1024 * 1024


def _directory_sync(path: Path) -> None:
    # Windows does not expose directory fsync through the Python file API.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or unsafe agent recovery file")


def _stream_from_native_handle(handle, adopt, release):
    """Transfer native ownership to a descriptor, then to its file object."""
    try:
        descriptor = adopt(handle)
    except BaseException:
        release(handle)
        raise
    try:
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError as exc:
            # A failed file-object constructor may already have closed it.
            if exc.errno != errno.EBADF:
                raise
        raise


def _open_lock(path: Path):
    if path.is_symlink():
        raise ValueError("Unsafe agent recovery lock")
    if os.name == "nt":
        # Share deletion so existing TemporaryDirectory users can clean up even
        # while a host object is alive. check_current detects removed/replaced locks.
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        release = kernel.CloseHandle
        release.argtypes = [wintypes.HANDLE]
        release.restype = wintypes.BOOL
        handle = create(str(path), 0xC0000000, 7, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        stream = _stream_from_native_handle(handle,
            lambda value: msvcrt.open_osfhandle(value, os.O_RDWR | os.O_BINARY), release)
    else:
        stream = path.open("a+b", buffering=0)
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise ValueError("Agent output already has an active writer") from exc
    return stream


class RecoveryStore:
    """One writer, immutable blobs, and one atomically replaced bounded checkpoint."""

    def __init__(self, root: Path, *, resume: bool):
        self.root = root / ".agent-state"
        self._stream = None
        if resume:
            if self.root.is_symlink() or not self.root.is_dir():
                raise ValueError("Missing agent recovery state; cannot resume")
        else:
            self.root.mkdir(exist_ok=False)
        try:
            self._stream = _open_lock(self.root / "lock")
            self._identity = self._stat_identity(os.fstat(self._stream.fileno()))
            self.blobs = self.root / "artifacts"
            if resume:
                if self.blobs.is_symlink() or not self.blobs.is_dir():
                    raise ValueError("Missing agent recovery artifacts")
            else:
                self.blobs.mkdir()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _stat_identity(value):
        return value.st_dev, value.st_ino

    def check_current(self):
        if self._stream is None:
            raise ValueError("Agent host is closed")
        if self.root.is_symlink() or self.blobs.is_symlink():
            raise ValueError("Agent recovery directory changed")
        path = self.root / "lock"
        _regular(path)
        if self._stat_identity(path.stat()) != self._identity:
            raise ValueError("Agent recovery writer lock changed")

    def _atomic(self, path: Path, raw: bytes):
        temporary = path.parent / (".pending-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _directory_sync(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, state: dict, artifacts: dict[str, bytes]):
        self.check_current()
        for key, raw in artifacts.items():
            path = self.blobs / (key + ".json")
            if path.exists():
                _regular(path)
                with path.open("rb") as stream:
                    retained = stream.read(len(raw) + 1)
                if retained != raw:
                    raise ValueError("Retained agent artifact was changed")
            else:
                self._atomic(path, raw)
        payload = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        sealed = json.dumps({"state": state, "sha256": sha256(payload).hexdigest()},
                            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(sealed) > MAX_CHECKPOINT:
            raise ValueError("Agent recovery checkpoint exceeds budget")
        self._atomic(self.root / "checkpoint.json", sealed)

    def load(self) -> tuple[dict, dict[str, bytes]]:
        self.check_current()
        path = self.root / "checkpoint.json"
        _regular(path)
        with path.open("rb") as stream:
            raw = stream.read(MAX_CHECKPOINT + 1)
        if len(raw) > MAX_CHECKPOINT:
            raise ValueError("Agent recovery checkpoint exceeds budget")
        try:
            sealed = loads_json(raw.decode())
            if type(sealed) is not dict or set(sealed) != {"state", "sha256"}:
                raise ValueError("Invalid agent recovery checkpoint")
            state = sealed["state"]
            payload = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            if sha256(payload).hexdigest() != sealed["sha256"]:
                raise ValueError("Agent recovery checkpoint digest mismatch")
            if type(state) is not dict or type(state.get("artifacts")) is not dict:
                raise ValueError("Invalid agent recovery artifact index")
            index = state["artifacts"]
            if len(index) > 256:
                raise ValueError("Agent recovery artifact budget exceeded")
            artifacts, total = {}, 0
            for key, size in index.items():
                if (type(key) is not str or re.fullmatch(r"a-[0-9a-f]{64}", key) is None
                        or type(size) is not int or not 0 < size <= 8 * 1024 * 1024):
                    raise ValueError("Invalid agent recovery artifact binding")
                total += size
                if total > 64 * 1024 * 1024:
                    raise ValueError("Agent recovery artifact budget exceeded")
                blob = self.blobs / (key + ".json")
                _regular(blob)
                with blob.open("rb") as stream:
                    content = stream.read(size + 1)
                if len(content) != size or "a-" + sha256(content).hexdigest() != key:
                    raise ValueError("Agent recovery artifact digest mismatch")
                artifacts[key] = content
            return state, artifacts
        except (UnicodeError, TypeError, KeyError, RecursionError) as exc:
            raise ValueError("Malformed agent recovery checkpoint") from exc

    def close(self):
        if self._stream is not None:
            self._stream.close()  # The OS releases the lock, including on process death.
            self._stream = None

    def __del__(self):
        self.close()
