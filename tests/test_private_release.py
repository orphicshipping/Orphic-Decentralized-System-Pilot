from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from ciw import private_release as release


@pytest.fixture
def bundle(tmp_path, monkeypatch, request):
    root = tmp_path / 'context'
    wheel_root = root / 'wheel'
    wheel_root.mkdir(parents=True)
    service = root / 'tools/private-mcp-host/adapter.py'
    service.parent.mkdir(parents=True)
    service.write_bytes(b'# service\n')
    dependencies = {name: '1.0' for name in ('numpy', 'websockets', 'mcp', 'starlette', 'uvicorn', 'jsonschema')}
    closure = {'package_roots': ['ciw', 'net_geomatics'], 'dependencies': dependencies, 'service_files': {'tools/private-mcp-host/adapter.py': release.file_hash(service)}, 'pins_digest': 'c' * 64}
    repository = getattr(request, 'param', 'orphicsystems/Orphic-Decentralized-System-Protocol')
    source = {'schema': release.SOURCE_SCHEMA, 'repository': repository, 'source_commit': 'a' * 40, 'source_tree': 'b' * 40, 'closure_digest': release.digest(closure)}
    entries = {'ciw/__init__.py': b'# package\n', 'ciw/model.json': b'{"example":1}', 'ciw/' + release.SOURCE_MARKER: release.canonical(source), 'net_geomatics/__init__.py': b'# geomatics\n', 'net_geomatics/gis.py': b'# gis\n'}
    wheel = wheel_root / 'computational_instrumentation_workbench-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        archive.writestr('computational_instrumentation_workbench-0.1.0.dist-info/top_level.txt', 'ciw\nnet_geomatics\n')
    site = tmp_path / 'site-packages'
    installed = site / 'ciw'
    for name, content in entries.items():
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {'schema': release.MANIFEST_SCHEMA, 'identity': {}, 'wheel': {'filename': wheel.name, 'size_bytes': wheel.stat().st_size}, 'distribution': {'name': release.DISTRIBUTION, 'version': '0.1.0'}, 'package_files': {name: sha256(content).hexdigest() for name, content in entries.items()}, **closure}
    identity = {'schema': release.IDENTITY_SCHEMA, 'repository': source['repository'], 'source_commit': source['source_commit'], 'source_tree': source['source_tree'], 'wheel_sha256': release.file_hash(wheel), 'package_digest': release.package_digest(manifest)}
    manifest['identity'] = identity
    path = wheel_root / 'release-manifest.json'
    path.write_text(json.dumps(manifest))
    distribution = SimpleNamespace(version='0.1.0', locate_file=lambda name: site / name, read_text=lambda name: None)
    monkeypatch.setattr(release.metadata, 'distribution', lambda name: distribution)
    monkeypatch.setattr(release.metadata, 'version', lambda name: dependencies[name])
    spec = lambda name: SimpleNamespace(origin=str(site / name / '__init__.py'), submodule_search_locations=[str(site / name)])
    original_find = release.importlib.util.find_spec
    original_path_find = release.importlib.machinery.PathFinder.find_spec
    monkeypatch.setattr(release.importlib.util, 'find_spec',
                        lambda name, *args: spec(name) if name in closure['package_roots'] else original_find(name, *args))
    monkeypatch.setattr(release.importlib.machinery.PathFinder, 'find_spec',
                        lambda name, *args: spec(name) if name in closure['package_roots'] else original_path_find(name, *args))
    monkeypatch.setattr(release, 'sys', SimpleNamespace(path=[], modules={}))
    return SimpleNamespace(path=path, manifest=manifest, identity=identity, root=root, service=service, wheel=wheel, installed=installed, site=site, distribution=distribution)


def rewrite(bundle, manifest):
    bundle.path.write_text(json.dumps(manifest))


def test_verifies_real_package_and_service_bytes(bundle):
    assert release.verify_release(bundle.path) == bundle.identity
    assert release.verify_manifest(bundle.path)['identity'] == bundle.identity


@pytest.mark.parametrize('bundle', ['orphicsystems/Orphic-Decentralized-Systems-Protocol'], indirect=True)
def test_historical_source_bound_identity_is_preserved_not_aliased(bundle):
    before = {path: path.read_bytes() for path in (bundle.path, bundle.wheel, bundle.installed / release.SOURCE_MARKER)}
    assert release.verify_release(bundle.path) == bundle.identity
    assert all(path.read_bytes() == raw for path, raw in before.items())
    renamed = deepcopy(bundle.manifest)
    renamed['identity']['repository'] = 'orphicsystems/Orphic-Decentralized-System-Protocol'
    rewrite(bundle, renamed)
    with pytest.raises(ValueError, match='source/closure binding'):
        release.verify_release(bundle.path)


@pytest.mark.parametrize('key,value', [
    ('source_commit', 'd' * 40), ('source_tree', 'd' * 40), ('repository', 'other/repository'),
    ('repository', 'orphicsystems/Orphic-Decentralized-Systems-Protocol'),
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


def test_owned_inventory_is_wheel_relative_and_includes_geomatics(bundle):
    files = release.wheel_files(bundle.wheel)
    assert set(files) == set(bundle.manifest['package_files'])
    assert release.package_roots(files) == ['ciw', 'net_geomatics']
    assert 'net_geomatics/gis.py' in files


@pytest.mark.parametrize('mutation', ['changed', 'missing', 'unexpected', 'cache', 'symlink'])
def test_every_owned_package_has_exact_installed_file_closure(bundle, mutation):
    root = bundle.site / 'net_geomatics'
    target = root / 'gis.py'
    if mutation == 'changed':
        target.write_bytes(b'# changed')
    elif mutation == 'missing':
        target.unlink()
    elif mutation == 'unexpected':
        (root / 'unexpected.py').write_bytes(b'# extra code')
    elif mutation == 'cache':
        (root / 'gis.pyc').write_bytes(b'executable cache')
    else:
        target.unlink()
        target.symlink_to(bundle.installed / '__init__.py')
    with pytest.raises(ValueError):
        release.verify_release(bundle.path)


def test_old_incomplete_manifest_requires_rebuild(bundle):
    old = deepcopy(bundle.manifest)
    old['schema'] = 'odsp.private-release-manifest.v1'
    old.pop('package_roots')
    old['package_files'] = {name[4:]: checksum for name, checksum in old['package_files'].items() if name.startswith('ciw/')}
    rewrite(bundle, old)
    with pytest.raises(ValueError, match='v1 package closure is incomplete and requires a rebuild'):
        release.verify_release(bundle.path)


def test_relabelled_partial_inventory_cannot_drop_owned_root(bundle):
    partial = deepcopy(bundle.manifest)
    partial['package_roots'] = ['ciw']
    partial['package_files'] = {name: checksum for name, checksum in partial['package_files'].items() if name.startswith('ciw/')}
    partial['identity']['package_digest'] = release.package_digest(partial)
    rewrite(bundle, partial)
    with pytest.raises(ValueError, match='package file manifest mismatch'):
        release.verify_release(bundle.path)


@pytest.mark.parametrize('entry', ['unowned.py', 'boot.pth', 'namespace/code.py', 'third_party/native.so', 'extra.dist-info/METADATA'])
def test_unknown_wheel_install_layout_is_refused(bundle, entry):
    with zipfile.ZipFile(bundle.wheel, 'a') as archive:
        archive.writestr(entry, b'unsupported')
    with pytest.raises(ValueError):
        release.wheel_files(bundle.wheel)


@pytest.mark.parametrize('declaration', ['ciw\n', 'ciw\nnet_geomatics\nnet_geomatics\n', 'ciw\nnet_geomatics\nabsent\n'])
def test_root_ownership_metadata_must_match_complete_wheel(bundle, declaration):
    with zipfile.ZipFile(bundle.wheel) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries['computational_instrumentation_workbench-0.1.0.dist-info/top_level.txt'] = declaration.encode()
    with zipfile.ZipFile(bundle.wheel, 'w') as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)
    with pytest.raises(ValueError, match='ownership metadata'):
        release.wheel_files(bundle.wheel)
