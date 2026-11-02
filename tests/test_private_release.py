from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from ciw import private_release as release


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / 'context'
    wheel_root = root / 'wheel'
    wheel_root.mkdir(parents=True)
    service = root / 'tools/private-mcp-host/adapter.py'
    service.parent.mkdir(parents=True)
    service.write_bytes(b'# service\n')
    dependencies = {name: '1.0' for name in ('numpy', 'websockets', 'mcp', 'starlette', 'uvicorn', 'jsonschema')}
    closure = {'dependencies': dependencies, 'service_files': {'tools/private-mcp-host/adapter.py': release.file_hash(service)}, 'pins_digest': 'c' * 64}
    source = {'schema': release.SOURCE_SCHEMA, 'repository': 'orphicsystems/Orphic-Decentralized-Systems-Protocol', 'source_commit': 'a' * 40, 'source_tree': 'b' * 40, 'closure_digest': release.digest(closure)}
    entries = {'__init__.py': b'# package\n', 'model.json': b'{"example":1}', release.SOURCE_MARKER: release.canonical(source)}
    wheel = wheel_root / 'computational_instrumentation_workbench-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, content in entries.items():
            archive.writestr('ciw/' + name, content)
    installed = tmp_path / 'site-packages/ciw'
    installed.mkdir(parents=True)
    for name, content in entries.items():
        (installed / name).write_bytes(content)
    manifest = {'schema': release.MANIFEST_SCHEMA, 'identity': {}, 'wheel': {'filename': wheel.name, 'size_bytes': wheel.stat().st_size}, 'distribution': {'name': release.DISTRIBUTION, 'version': '0.1.0'}, 'package_files': {name: sha256(content).hexdigest() for name, content in entries.items()}, **closure}
    identity = {'schema': release.IDENTITY_SCHEMA, 'repository': source['repository'], 'source_commit': source['source_commit'], 'source_tree': source['source_tree'], 'wheel_sha256': release.file_hash(wheel), 'package_digest': release.package_digest(manifest)}
    manifest['identity'] = identity
    path = wheel_root / 'release-manifest.json'
    path.write_text(json.dumps(manifest))
    distribution = SimpleNamespace(version='0.1.0', locate_file=lambda name: installed, read_text=lambda name: None)
    monkeypatch.setattr(release.metadata, 'distribution', lambda name: distribution)
    monkeypatch.setattr(release.metadata, 'version', lambda name: dependencies[name])
    monkeypatch.setattr(release.importlib.util, 'find_spec', lambda name: SimpleNamespace(origin=str(installed / '__init__.py')))
    return SimpleNamespace(path=path, manifest=manifest, identity=identity, root=root, service=service, wheel=wheel, installed=installed, distribution=distribution)


def rewrite(bundle, manifest):
    bundle.path.write_text(json.dumps(manifest))


def test_verifies_real_package_and_service_bytes(bundle):
    assert release.verify_release(bundle.path) == bundle.identity
    assert release.verify_manifest(bundle.path)['identity'] == bundle.identity


@pytest.mark.parametrize('key,value', [
    ('source_commit', 'd' * 40), ('source_tree', 'd' * 40), ('repository', 'other/repository'),
    ('wheel_sha256', 'd' * 64), ('package_digest', 'd' * 64), ('source_commit', 'short'),
    ('source_tree', None), ('schema', 'historical-release'),
])
def test_source_or_release_identity_mismatch_refuses(bundle, key, value):
    manifest = deepcopy(bundle.manifest)
    manifest['identity'][key] = value
    rewrite(bundle, manifest)
    with pytest.raises(ValueError):
        release.verify_release(bundle.path)


@pytest.mark.parametrize('mutation', ['missing-manifest', 'missing-wheel', 'altered-wheel', 'missing-package', 'altered-package', 'extra-package', 'cache', 'service', 'extra-service', 'dependency', 'editable', 'wrong-origin', 'symlink'])
def test_missing_or_drifted_installation_refuses(bundle, monkeypatch, mutation):
    if mutation == 'missing-manifest':
        bundle.path.unlink()
    elif mutation == 'missing-wheel':
        bundle.wheel.unlink()
    elif mutation == 'altered-wheel':
        bundle.wheel.write_bytes(bundle.wheel.read_bytes() + b'altered')
    elif mutation == 'missing-package':
        (bundle.installed / 'model.json').unlink()
    elif mutation == 'altered-package':
        (bundle.installed / 'model.json').write_bytes(b'altered')
    elif mutation == 'extra-package':
        (bundle.installed / 'extra.py').write_bytes(b'# extra')
    elif mutation == 'cache':
        (bundle.installed / '__pycache__').mkdir()
        (bundle.installed / '__pycache__/__init__.pyc').write_bytes(b'code')
    elif mutation == 'service':
        bundle.service.write_bytes(b'# altered')
    elif mutation == 'extra-service':
        bundle.service.with_name('extra.py').write_bytes(b'# extra')
    elif mutation == 'dependency':
        monkeypatch.setattr(release.metadata, 'version', lambda name: 'unknown')
    elif mutation == 'editable':
        bundle.distribution.read_text = lambda name: '{"dir_info":{"editable":true}}'
    elif mutation == 'wrong-origin':
        monkeypatch.setattr(release.importlib.util, 'find_spec', lambda name: SimpleNamespace(origin='/checkout/ciw/__init__.py'))
    elif mutation == 'symlink':
        target = bundle.installed / 'model.json'
        target.unlink()
        target.symlink_to(bundle.service)
    with pytest.raises(ValueError):
        release.verify_release(bundle.path)


@pytest.mark.parametrize('field', ['dependencies', 'service_files', 'pins_digest'])
def test_resealed_manifest_cannot_rebind_wheel_closure(bundle, field):
    value = deepcopy(bundle.manifest)
    if field == 'dependencies':
        value[field]['mcp'] = 'other'
    elif field == 'service_files':
        value[field]['tools/private-mcp-host/adapter.py'] = 'd' * 64
    else:
        value[field] = 'd' * 64
    value['identity']['package_digest'] = release.package_digest(value)
    rewrite(bundle, value)
    with pytest.raises(ValueError, match='source/closure binding'):
        release.verify_release(bundle.path)


@pytest.mark.parametrize('raw', ['{}', '[]', '{"schema":1,"schema":2}', '{"value":NaN}'])
def test_strict_manifest_parsing(bundle, raw):
    bundle.path.write_text(raw)
    with pytest.raises(ValueError):
        release.verify_release(bundle.path)


@pytest.mark.parametrize('name', ['ciw/../escape', 'ciw/CON', 'ciw\\bad.py', 'ciw/__pycache__/source.pyc', 'CIW/shadow.py'])
def test_malformed_archive_paths_refuse(bundle, name):
    with zipfile.ZipFile(bundle.wheel, 'a') as archive:
        archive.writestr(name, b'malformed')
    with pytest.raises(ValueError):
        release.verify_release(bundle.path)


def test_manifest_is_explicit_and_absolute(bundle):
    with pytest.raises(ValueError):
        release.verify_release('release-manifest.json')
    with pytest.raises(ValueError):
        release.verify_release(None)
