"""Native Windows sharing behavior; selected by the Windows-only CI step."""
import ctypes
import os
import threading

import pytest

from ciw import proto_corpus as corpus
from test_corpus_publication import staged_directory, fingerprint

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows directory sharing")


def test_actual_windows_share_delete_conflict_succeeds_after_handle_release(tmp_path):
    from ctypes import wintypes
    stage, target = staged_directory(tmp_path)
    before = fingerprint(stage)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(stage), 0x80000000, 0x1 | 0x2, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value
    closed = threading.Event()

    def release():
        assert kernel.CloseHandle(handle)
        closed.set()

    timer = threading.Timer(0.05, release)
    try:
        with pytest.raises(PermissionError) as caught:
            stage.rename(target)
        assert caught.value.winerror in {5, 32, 33}
        timer.start()
        corpus._publish_directory(stage, target)
        assert closed.is_set() and not stage.exists()
        assert fingerprint(target) == before
    finally:
        if timer.ident is not None:
            timer.join(timeout=2)
        if not closed.is_set():
            kernel.CloseHandle(handle)
