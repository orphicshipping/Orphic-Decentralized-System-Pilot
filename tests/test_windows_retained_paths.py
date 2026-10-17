"""Real Windows filesystem regression; synthetic software evidence only."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from ciw.filesystem_paths import filesystem_path, _windows_extended_path
from ciw import session as session_module
from ciw.session import Session, read_json, write_json
from ciw.instruments import make_demo_run

WINDOWS = pytest.mark.skipif(os.name != 'nt', reason='Native Windows extended-path filesystem')


def long_path(root, length, name='recording-' + 'a' * 64 + '.json'):
    parent = root
    while length - len(str(parent)) - len(name) - 2 > 120:
        parent /= 'p' * 80
    padding = length - len(str(parent)) - len(name) - 2
    assert padding > 0
    target = parent / ('p' * padding) / name
    assert len(str(target)) == length
    return target


def snapshot(root):
    root = filesystem_path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize(('name', 'expected'), [
    (r'C:\retained\sub\..\record.json', r'\\?\C:\retained\record.json'),
    ('C:/retained/record.json', r'\\?\C:\retained\record.json'),
    (r'\\server\share\retained\record.json', r'\\?\UNC\server\share\retained\record.json'),
    (r'\\?\C:\retained\record.json', r'\\?\C:\retained\record.json'),
    (r'\\?\UNC\server\share\record.json', r'\\?\UNC\server\share\record.json'),
])
def test_windows_path_spelling_model(name, expected):
    assert _windows_extended_path(name) == expected


@pytest.mark.parametrize('name', [
    r'\\.\PhysicalDrive0', r'\\?\GLOBALROOT\Device\Harddisk0', r'\\?\Volume{abc}\record.json',
    r'\\?\UNC\.\pipe\record', r'\\?\UNC\?\share\record',
    r'C:\retained\NUL.json', r'C:\retained\con.txt', r'C:\retained\COM1.json',
    'C:\\retained\\LPT\u00b2.json', r'C:\retained\CONOUT$',
    r'C:\retained\record.json:stream', 'C:\\retained\\trailing \\record.json',
    r'C:\retained\trailing.\record.json', r'C:\retained\wild*.json',
])
def test_extended_paths_do_not_enable_devices_or_ambiguous_names(name):
    with pytest.raises(ValueError):
        _windows_extended_path(name)


def test_posix_branch_passes_paths_through_without_windows_normalization(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ciw import filesystem_paths
    # Explicit branch model; no global os.name mutation or claim of native Linux IO.
    monkeypatch.setattr(filesystem_paths, 'os', SimpleNamespace(name='posix'))
    path = tmp_path / 'literal:part' / 'record.json'
    assert filesystem_path(path) == path


@WINDOWS
@pytest.mark.parametrize('length', [259, 260, 261, 266, 350])
def test_full_recording_name_roundtrips_beyond_max_path(tmp_path, length):
    target = long_path(tmp_path, length)
    data = {'synthetic': True, 'value': [1, 2, 3]}
    assert write_json(target, data) == target
    assert read_json(target) == data
    assert filesystem_path(target).name == target.name
    assert filesystem_path(target).read_text() == json.dumps(data, indent=2) + '\n'
    assert not list(filesystem_path(target.parent).glob('.ciw-*.tmp'))


@WINDOWS
def test_relative_unicode_path_uses_utf16_length_without_changing_return_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    relative = Path('unicode-' + '\U0001f9ea' * 75) / ('recording-' + 'b' * 64 + '.json')
    assert len(str(tmp_path / relative).encode('utf-16-le')) // 2 > 260
    assert write_json(relative, {'value': 7}) == relative
    assert read_json(relative) == {'value': 7}


@WINDOWS
@pytest.mark.parametrize('failure', ['fsync', 'replace'])
def test_failed_long_path_replace_preserves_previous_file_and_cleans_staging(tmp_path, monkeypatch, failure):
    target = long_path(tmp_path, 350)
    write_json(target, {'previous': True})
    before = filesystem_path(target).read_bytes()
    error = OSError('injected atomic publication failure')
    def fail(*args, **kwargs):
        raise error
    with monkeypatch.context() as m:
        m.setattr(session_module.os, failure, fail)
        with pytest.raises(OSError) as raised:
            write_json(target, {'new': True})
        assert raised.value is error
    assert filesystem_path(target).read_bytes() == before
    assert not list(filesystem_path(target.parent).glob('.ciw-*.tmp'))


@WINDOWS
def test_serialization_failure_does_not_create_long_path_output(tmp_path):
    target = long_path(tmp_path, 350)
    with pytest.raises(ValueError):
        write_json(target, {'invalid': float('nan')})
    assert not filesystem_path(target.parent).exists()


@WINDOWS
@pytest.mark.parametrize(('kind', 'after_write'), [('result', False), ('result', True), ('execution', False), ('execution', True)])
def test_long_session_rollback_removes_only_new_pair_and_reopens_exactly(tmp_path, monkeypatch, kind, after_write):
    root = long_path(tmp_path, 355).parent
    session = Session(make_demo_run(), root)
    def execute():
        return session.handle({'protocol_version': 1, 'request_id': 'long-path',
            'type': 'operation.execute', 'payload': {'operation_id': 'statistics.v1'}})
    assert execute()['payload']['status'] == 'completed'
    prior = snapshot(root)
    results, executions = deepcopy(session.results), deepcopy(session.executions)
    def interrupted(path, value):
        if Path(path).name.startswith(kind + '-'):
            if after_write:
                write_json(path, value)
            raise OSError('injected pair publication failure')
        return write_json(path, value)
    with monkeypatch.context() as m:
        m.setattr(session_module, 'write_json', interrupted)
        assert execute()['payload']['code'] == 'storage_error'
    assert snapshot(root) == prior
    assert session.results == results and session.executions == executions
    assert session._pending_operations == 0
    saved = session.save_workspace(root / 'workspace.json')
    assert saved == root / 'workspace.json' and session.output_dir == root
    restored = Session.from_workspace(saved, root / 'restored')
    assert restored.results == results and restored.executions == executions
    assert restored.recording_file == session.recording_file
    assert len(session.recording_file) == len('recording-') + 64 + len('.json')


@WINDOWS
def test_engineering_long_path_roundtrip_retains_all_bytes_and_no_authority(tmp_path, monkeypatch):
    from ciw import engineering_thread as e, engineering_thread_io as io, link_contract as c
    from ciw.link_study_cli import example
    root = long_path(tmp_path, 350).parent
    raw = (json.dumps(example(), indent=2, allow_nan=False) + '\n').encode()
    intent = e.example_intent('scalar', c.bytes_ref(raw))
    packet = io.compile_packet(json.dumps(intent).encode(), raw, root/'compiled', host_profile='scalar')
    view = io.run_packet(root/'compiled', root/'complete', expected_compiled_ref=packet['compiled_ref'],
                         host_profile='scalar', allow_compute=True)
    before = snapshot(root/'complete')
    manifest = json.loads(before['thread.json'])
    assert any(p.startswith('run/session/recording-') for p in before)
    assert len(before) == len(manifest['files']) + 1
    assert all(not entry['path'].startswith(('\\', '/')) for entry in manifest['files'])
    def refuse(*args, **kwargs):
        pytest.fail('Historical review or restore executed an operation')
    with monkeypatch.context() as m:
        m.setattr(Session, 'handle', refuse)
        assert io.inspect_thread(root/'complete', expected_thread_ref=view['thread_ref'], host_profile='scalar') == view
        restored = io.restore_thread(root/'complete', root/'restored', expected_thread_ref=view['thread_ref'], host_profile='scalar')
    assert snapshot(root/'complete') == snapshot(root/'restored') == before
    assert restored['execution_ids'] == view['execution_ids']
    assert restored['new_scientific_executions'] == 0 and restored['active_authority_restored'] is False
    assert view['scope']['physical_qualification'] == 'HELD'


@WINDOWS
def test_engineering_guard_checks_before_and_after_windows_normalization(tmp_path, monkeypatch):
    from ciw import engineering_thread_io as io
    observed = []
    original = io._link_safe_path
    def record(path):
        observed.append(Path(path))
        return original(path)
    monkeypatch.setattr(io, '_link_safe_path', record)
    path = tmp_path/'parent'/'..'/'record.json'
    io.safe_path(path)
    assert observed[0] == path
    assert str(observed[1]).startswith('\\\\?\\')
    assert '..' not in observed[1].parts


@WINDOWS
def test_engineering_report_return_uses_original_logical_path(tmp_path):
    from ciw import engineering_thread_io as io
    result = io.demo(tmp_path/'demo', host_profile='scalar')
    assert result['report']['path'] == str((tmp_path/'demo'/'engineering.html').absolute())
    assert not result['report']['path'].startswith('\\\\?\\')


@WINDOWS
@pytest.mark.parametrize('parent_traversal', [False, True])
def test_engineering_refuses_long_symlink_even_before_parent_normalization(tmp_path, parent_traversal):
    from ciw import engineering_thread_io as io
    root = long_path(tmp_path, 355).parent
    physical = filesystem_path(root)
    physical.mkdir(parents=True)
    target = physical/'target'; target.mkdir()
    link = physical/'linked'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows account lacks symbolic-link privilege')
        raise
    path = root/'linked'
    if parent_traversal:
        path /= '..'
    path /= 'record.json'
    with pytest.raises(ValueError, match='symlinks'):
        io.safe_path(path)
    assert not (physical/'record.json').exists()


@pytest.mark.parametrize('name', [
    r'\\server\share?\record.json', r'\\server\share:stream\record.json',
    r'\\server\share.\record.json', '\\\\server\\share \\record.json',
    r'\\?\UNC\\.\pipe\record', r'\\server\\share\record.json',
    r'\\..\share\record.json', r'\\server.\share\record.json',
])
def test_windows_unc_anchor_is_not_exempt_from_name_checks(name):
    with pytest.raises(ValueError):
        _windows_extended_path(name)


@pytest.mark.parametrize('name', [r'1:\record.json', r'!:\record.json', '\u00e9:\\record.json'])
def test_windows_drive_anchor_requires_ascii_letter(name):
    with pytest.raises(ValueError):
        _windows_extended_path(name)
