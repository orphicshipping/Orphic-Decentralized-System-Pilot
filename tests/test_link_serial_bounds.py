"""Modeled OS seam, real Link journal; no physical or Linux qualification claim."""
from collections import deque
from types import SimpleNamespace
import sys

import pytest

from ciw import link_contract as c, link_serial as serial
from ciw.link_gateway import capture, validate_capture
from ciw.link_profiles import example_manifest
from ciw.link_runtime import Authority
from ciw.link_store import Journal


class TerminalError(Exception):
    """CPython termios.error has its own Exception base, not OSError.

    https://github.com/python/cpython/blob/3.12/Modules/termios.c
    """


class Terminal:
    def __init__(self, monkeypatch, chunks):
        self.pending = deque(chunks)
        self.now = 100.0
        self.wall = 1000
        self.reads = []
        self.closed = []
        self.configurations = []
        self.controls = []
        self.after_wait = lambda: None
        self.old = [0, 0, 0, 0, 9600, 9600, [0, 0]]
        node = SimpleNamespace(st_mode=0o020000, st_dev=1, st_ino=2, st_rdev=3)
        class VirtualPath:
            def __init__(self, value): self.value = str(value)
            def is_absolute(self): return True
            def __str__(self): return self.value
        monkeypatch.setattr(serial, 'Path', VirtualPath)
        monkeypatch.setattr(serial, 'safe_path', lambda p: p)
        monkeypatch.setattr(serial, 'sys', SimpleNamespace(platform='linux'))
        monkeypatch.setattr(serial, 'time', SimpleNamespace(
            monotonic=lambda: self.now, monotonic_ns=lambda: int(self.now * 1e9),
            time_ns=lambda: self.wall))
        def opened(path, flags):
            assert str(path) == '/dev/pts/42' and flags == 7
            return 42
        monkeypatch.setattr(serial, 'os', SimpleNamespace(
            O_RDONLY=0, O_NONBLOCK=1, O_NOCTTY=2, O_NOFOLLOW=4,
            stat=lambda *a, **k: node, fstat=lambda fd: node,
            isatty=lambda fd: True, open=opened, read=self.read,
            close=self.closed.append))  # No write API exists at this seam.
        monkeypatch.setattr(serial, 'select', SimpleNamespace(select=self.select))
        monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(
            ioctl=lambda fd, action: self.controls.append(action)))
        monkeypatch.setitem(sys.modules, 'termios', SimpleNamespace(
            TIOCEXCL=1, TIOCNXCL=2, CS8=8, CREAD=16, CLOCAL=32,
            B9600=9600, VMIN=0, VTIME=1, TCSANOW=0, error=TerminalError,
            tcgetattr=lambda fd: self.old,
            tcsetattr=lambda fd, action, attrs: self.configurations.append(attrs)))

    def select(self, readers, writers, errors, timeout):
        assert readers == [42] and not writers and not errors
        self.now += min(timeout, .01) if self.pending else timeout
        self.after_wait()
        return ([42] if self.pending else [], [], [])

    def read(self, fd, size):
        assert fd == 42 and size > 0
        self.reads.append(size)
        raw = self.pending.popleft()
        if isinstance(raw, BaseException): raise raw
        if len(raw) > size: self.pending.appendleft(raw[size:])
        return raw[:size]


def receive(tmp_path, terminal, **bounds):
    device = example_manifest('GaugeLink')
    device['channels'][0].update(quantity='force', unit='N', encoding='opaque.v1')
    device['clock'].update(id='link.host.monotonic_ns', tick_hz=1_000_000_000)
    with Journal(tmp_path / 'journal', create=True) as journal:
        receipt = serial.capture_serial('/dev/pts/42', device=device, journal=journal,
            authority=Authority('operator', device['device_id'], frozenset({'READ'}), 2000),
            channel_id='CH0', allow_pty=True, **bounds)
        retained = validate_capture(capture(journal, expected_manifest=device,
                                           boot_id=receipt['boot_id']))
    raw = b''.join(c.unpack(row['raw'], c.MAX_RAW) for row in retained['observations'])
    assert receipt['origin'] == 'synthetic' and receipt['bytes_transmitted'] == 0
    assert receipt['completeness'] == 'not_established'
    assert terminal.closed == [42] and terminal.controls == [1, 2]
    assert terminal.configurations[-1] == terminal.old
    return receipt, raw


@pytest.mark.parametrize('limit', [1, 2, 3, 4])
@pytest.mark.parametrize('stride', [1, 2, 5, 256])
def test_record_budget_matches_independent_prefix_model(tmp_path, monkeypatch, limit, stride):
    raw = b'1 N\r\n2 N\r\n3 N\r\n4 N\r\n5 N\r\n'
    terminal = Terminal(monkeypatch, [raw[i:i + stride] for i in range(0, len(raw), stride)])
    receipt, retained = receive(tmp_path, terminal, max_records=limit)
    expected = b''.join(raw.splitlines(keepends=True)[:limit])
    assert receipt['status'] == 'RECORD_BOUND_REACHED'
    assert receipt['acquisition']['records'] == limit
    assert retained == expected
    assert retained + b''.join(terminal.pending) == raw


@pytest.mark.parametrize('ending, expected', [([], 'IDLE_TIMEOUT'),
    ([b''], 'DISCONNECTED_OR_EOF'), ([OSError(5, 'modeled fault')], 'DISCONNECTED_OR_READ_ERROR'),
    ([KeyboardInterrupt()], 'CANCELLED')])
def test_fault_outcomes_retain_incomplete_prefix(tmp_path, monkeypatch, ending, expected):
    terminal = Terminal(monkeypatch, [b'1 N\r\n2.'] + ending)
    receipt, retained = receive(tmp_path, terminal, idle_timeout_s=.2, duration_s=1)
    assert receipt['status'] == expected and retained == b'1 N\r\n2.'
    assert receipt['acquisition']['records'] == 1


def test_cancel_callback_retains_committed_bytes(tmp_path, monkeypatch):
    terminal = Terminal(monkeypatch, [b'1 N\r\n', b'2 N\r\n'])
    cancel = iter([False, True])
    receipt, retained = receive(tmp_path, terminal, cancelled=lambda: next(cancel))
    assert receipt['status'] == 'CANCELLED' and retained == b'1 N\r\n'
    assert b''.join(terminal.pending) == b'2 N\r\n'


def test_expired_grant_after_readiness_never_reads(tmp_path, monkeypatch):
    terminal = Terminal(monkeypatch, [b'1 N\r\n'])
    terminal.after_wait = lambda: setattr(terminal, 'wall', 2000)
    with pytest.raises(PermissionError): receive(tmp_path, terminal, max_records=1)
    assert terminal.reads == [] and terminal.closed == [42]


@pytest.mark.parametrize('bounds', [{'max_records': True}, {'max_records': 0},
    {'max_records': 33}, {'duration_s': float('nan')}, {'duration_s': True},
    {'idle_timeout_s': 0}, {'idle_timeout_s': float('inf')}, {'cancelled': 'device-text'},
    {'max_bytes': 8193}, {'max_chunks': 0}])
def test_invalid_host_bounds_refuse(bounds):
    with pytest.raises(ValueError): serial.acquisition_limits(**bounds)
