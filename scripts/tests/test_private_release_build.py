from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('private_builder', Path(__file__).resolve().parents[1] / 'build_private_release.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_source_binding_is_deterministic_and_record_is_updated(tmp_path):
    paths = [tmp_path / 'one.whl', tmp_path / 'two.whl']
    for path in paths:
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('ciw/__init__.py', b'# package')
            archive.writestr('example.dist-info/RECORD', '')
        builder.bound_wheel(path, {'source_commit':'a' * 40}, 1700000000)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    with zipfile.ZipFile(paths[0]) as archive:
        assert json.loads(archive.read('ciw/_private_release_source.json')) == {'source_commit':'a' * 40}
        record = archive.read('example.dist-info/RECORD').decode()
        assert 'ciw/_private_release_source.json,sha256=' in record


def test_import_pin_mismatch_refuses(monkeypatch):
    modules = [{'role':str(i), 'path':f'instruments/{i}', 'import_revision':'a' * 40, 'import_tree':'b' * 40} for i in range(21)]
    def git(root, *args, binary=False):
        if args[0] == 'show':
            return json.dumps({'modules':modules}).encode()
        return 'c' * 40
    monkeypatch.setattr(builder, 'git', git)
    with pytest.raises(ValueError, match='import tree mismatch'):
        builder.audit_pins(Path('/source'), 'd' * 40)


def test_dirty_checkout_cannot_be_labelled_as_committed_release(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    monkeypatch.setattr(builder, 'git', lambda *args, **kwargs: ' M src/ciw/changed.py')
    with pytest.raises(ValueError, match='clean committed checkout'):
        builder.build(source, tmp_path / 'result', {})
    assert not (tmp_path / 'result').exists()


def test_output_is_create_only_and_outside_checkout(tmp_path):
    output = tmp_path / 'source'
    output.mkdir()
    with pytest.raises(ValueError, match='new release output'):
        builder.build(tmp_path, output, {})
    with pytest.raises(ValueError, match='outside the checkout'):
        builder.build(tmp_path, output / 'new', {})
