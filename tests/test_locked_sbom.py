"""Real archive/manifest closure checks without registry/network dependencies."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from ciw import private_release as release

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('write_locked_sbom', ROOT / 'scripts/write_locked_sbom.py')
sbom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sbom)


def write_json(path, value):
    path.write_bytes(release.canonical(value))


def wheel(path, name, version='1.0', metadata_name=None):
    with zipfile.ZipFile(path, 'w') as archive:
        # A native dependency is inventoried as archive bytes, never imported.
        archive.writestr(name + '/native.so', b'not executed synthetic native bytes')
        archive.writestr(name + '-' + version + '.dist-info/METADATA',
            'Metadata-Version: 2.1\nName: ' + (metadata_name or name) + '\nVersion: ' + version + '\n')


@pytest.fixture
def artifacts(tmp_path):
    context = tmp_path / 'context'
    wheel_root, wheelhouse = context / 'wheel', tmp_path / 'wheelhouse'
    wheel_root.mkdir(parents=True); wheelhouse.mkdir()
    service = context / 'tools/private-mcp-host/adapter.py'
    service.parent.mkdir(parents=True)
    service.write_bytes(b'# inert fixture service\n')
    dependencies = dict.fromkeys(('numpy','websockets','mcp','starlette','uvicorn','jsonschema'), '1.0')
    for name, version in dependencies.items():
        wheel(wheelhouse / (name + '-' + version + '-py3-none-any.whl'), name, version)
    lock = tmp_path / 'runtime.lock'
    lock.write_text(''.join(name + '==' + version + ' --hash=sha256:' +
        release.file_hash(wheelhouse / (name + '-' + version + '-py3-none-any.whl')) + '\n'
        for name,version in sorted(dependencies.items())), encoding='ascii')
    pins = {'schema':'odsp.private-preserved-sources.v1', 'manifest_sha256':'a'*64,
            'runtime_pin_files':{'src/ciw/fixture-runtime.json':'b'*64},
            'godot_prop':{'path':'examples/workcells/godot-prop','tree':'c'*40},
            'imports':[{'role':f'role{i}', 'path':f'instruments/fixture{i}',
                        'import_revision':f'{i+1:040x}', 'import_tree':f'{i+50:040x}',
                        'runtime_revision':None if i == 20 else f'{i+100:040x}'} for i in range(21)]}
    pins_path = context / 'pins.json'; write_json(pins_path, pins)
    closure = {'package_roots':['ciw','net_geomatics'], 'dependencies':dependencies,
               'service_files':{'tools/private-mcp-host/adapter.py':release.file_hash(service)},
               'pins_digest':release.digest(pins)}
    source = {'schema':release.SOURCE_SCHEMA,
              'repository':'orphicsystems/Orphic-Decentralized-System-Protocol',
              'source_commit':'d'*40,'source_tree':'e'*40,'closure_digest':release.digest(closure)}
    entries = {'ciw/__init__.py':b'# ciw fixture\n',
               'ciw/' + release.SOURCE_MARKER:release.canonical(source),
               'net_geomatics/__init__.py':b'# geomatics fixture\n',
               'net_geomatics/geodesy.py':b'# owned package content\n'}
    owned = wheel_root / 'computational_instrumentation_workbench-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(owned, 'w') as archive:
        for name, content in entries.items(): archive.writestr(name, content)
        archive.writestr('computational_instrumentation_workbench-0.1.0.dist-info/top_level.txt', 'ciw\nnet_geomatics\n')
    manifest = {'schema':release.MANIFEST_SCHEMA,
        'wheel':{'filename':owned.name,'size_bytes':owned.stat().st_size},
        'distribution':{'name':release.DISTRIBUTION,'version':'0.1.0'},
        'package_files':{name:sha256(raw).hexdigest() for name,raw in entries.items()}, **closure}
    identity = {'schema':release.IDENTITY_SCHEMA,'repository':source['repository'],
        'source_commit':source['source_commit'],'source_tree':source['source_tree'],
        'wheel_sha256':release.file_hash(owned),'package_digest':release.package_digest(manifest)}
    manifest['identity'] = identity
    manifest_path, identity_path = wheel_root / 'release-manifest.json', context / 'identity.json'
    write_json(manifest_path, manifest); write_json(identity_path, identity)
    return SimpleNamespace(context=context, owned=owned, service=service, wheelhouse=wheelhouse,
        lock=lock, pins=pins, pins_path=pins_path, manifest=manifest, manifest_path=manifest_path,
        identity=identity, identity_path=identity_path, lock_hash=release.file_hash(lock))


def build(a, **kwargs):
    return sbom.build_sbom(**{'manifest_path':a.manifest_path,'identity_path':a.identity_path,
        'lock_path':a.lock,'expected_lock_sha256':a.lock_hash,'wheelhouse':a.wheelhouse,
        'pins_path':a.pins_path, **kwargs})


def test_deterministic_inventory_binds_archives_all_owned_roots_source_and_limits(artifacts):
    a = artifacts
    value = build(a)
    assert value == build(a)
    assert value['bomFormat'] == 'CycloneDX' and value['specVersion'] == '1.6'
    props = {p['name']:p['value'] for p in value['metadata']['properties']}
    assert props['odsp:lock-sha256'] == a.lock_hash
    assert props['odsp:installed-dependency-bytes'] == 'not_verified'
    assert props['odsp:source-authentication'] == 'host_selected_unverified'
    assert props['odsp:authorizes-execution'] == 'false'
    assert json.loads(props['odsp:preserved-source-inventory']) == a.pins
    components = {item['bom-ref']:item for item in value['components']}
    owned_files = {entry['name']:entry['hashes'][0]['content'] for root in ('ciw','net_geomatics')
                   for entry in components['odsp:owned-package:' + root]['components']}
    assert owned_files == a.manifest['package_files']
    dependencies = [item for item in value['components'] if item.get('purl')]
    assert len(dependencies) == len(a.manifest['dependencies'])
    for item in dependencies:
        actual = a.wheelhouse / (item['name'] + '-1.0-py3-none-any.whl')
        assert item['hashes'][0]['content'] == release.file_hash(actual)


@pytest.mark.parametrize('mutation', ['altered','missing','extra','duplicate','symlink','directory'])
def test_dependency_archive_closure_refuses_drift(artifacts, mutation):
    a = artifacts
    target = a.wheelhouse / 'numpy-1.0-py3-none-any.whl'
    if mutation == 'altered': target.write_bytes(target.read_bytes() + b'altered')
    elif mutation == 'missing': target.unlink()
    elif mutation == 'extra': wheel(a.wheelhouse / 'unlocked-1.0-py3-none-any.whl', 'unlocked')
    elif mutation == 'duplicate': (a.wheelhouse / 'numpy-1.0-1-py3-none-any.whl').write_bytes(target.read_bytes())
    elif mutation == 'symlink':
        original = a.context / 'retained-numpy.whl'; target.rename(original); target.symlink_to(original)
    else: target.unlink(); target.mkdir()
    with pytest.raises(ValueError): build(a)


@pytest.mark.parametrize('raw', [b'numpy==1.0\n', b'numpy>=1.0 --hash=sha256:' + b'a'*64,
    b'numpy[extra]==1.0 --hash=sha256:' + b'a'*64,
    b'numpy @ https://example.invalid/code.whl', b'--extra-index-url=https://example.invalid',
    b'numpy==1.0;python_version>"3" --hash=sha256:' + b'a'*64,
    b'numpy==1.0 --hash=sha256:' + b'a'*64 + b' --hash=sha256:' + b'b'*64,
    (b'numpy==1.0 --hash=sha256:' + b'a'*64 + b'\n') * 2])
def test_ambiguous_unhashed_or_executable_lock_grammar_is_refused(raw):
    with pytest.raises(ValueError): sbom.locked(raw)


def test_independently_selected_lock_identity_cannot_be_replaced(artifacts):
    a = artifacts
    a.lock.write_bytes(a.lock.read_bytes().replace(b'numpy==1.0', b'numpy==2.0'))
    with pytest.raises(ValueError, match='sbom_expected_lock_mismatch'): build(a)
    with pytest.raises(ValueError, match='closure_mismatch'):
        build(a, expected_lock_sha256=release.file_hash(a.lock))


def test_hashed_archive_metadata_must_match_locked_distribution(artifacts):
    a = artifacts
    target = a.wheelhouse / 'numpy-1.0-py3-none-any.whl'
    previous = release.file_hash(target)
    wheel(target, 'numpy', metadata_name='another-package')
    a.lock.write_text(a.lock.read_text().replace(previous, release.file_hash(target)))
    with pytest.raises(ValueError, match='metadata_identity_mismatch'):
        build(a, expected_lock_sha256=release.file_hash(a.lock))


def test_metadata_read_cap_remains_enforced_after_whole_archive_hash_matches(artifacts):
    a = artifacts
    target = a.wheelhouse / 'numpy-1.0-py3-none-any.whl'
    previous = release.file_hash(target)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('numpy-1.0.dist-info/METADATA',
                        b'Name: numpy\nVersion: 1.0\n\n' + b'x' * sbom.MAX_METADATA)
    a.lock.write_text(a.lock.read_text().replace(previous, release.file_hash(target)))
    with pytest.raises(ValueError, match='sbom_metadata_budget'):
        build(a, expected_lock_sha256=release.file_hash(a.lock))


@pytest.mark.parametrize('mutation', ['source','pins','service','extra-service','owned-package','unexpected-owned-file'])
def test_source_package_and_service_integrity_not_just_dependency_versions(artifacts, mutation):
    a = artifacts
    if mutation == 'source':
        a.identity['source_commit'] = '0'*40; write_json(a.identity_path, a.identity)
    elif mutation == 'pins':
        a.pins['imports'][0]['import_tree'] = '0'*40; write_json(a.pins_path, a.pins)
    elif mutation == 'service': a.service.write_bytes(b'# changed')
    elif mutation == 'extra-service': a.service.with_name('unexpected.py').write_bytes(b'# extra')
    else:
        with zipfile.ZipFile(a.owned) as archive:
            entries = {info.filename:archive.read(info) for info in archive.infolist()}
        if mutation == 'owned-package': entries['net_geomatics/geodesy.py'] = b'# tampered'
        else: entries['net_geomatics/unexpected.py'] = b'# extra'
        with zipfile.ZipFile(a.owned, 'w') as archive:
            for name, raw in entries.items(): archive.writestr(name, raw)
    with pytest.raises(ValueError): build(a)


def test_cli_works_outside_checkout_and_never_overwrites_existing_evidence(artifacts, tmp_path):
    a = artifacts
    output = tmp_path / 'bom.cdx.json'
    command = [sys.executable, '-B', str(ROOT/'scripts/write_locked_sbom.py'),
        '--manifest', str(a.manifest_path), '--identity', str(a.identity_path),
        '--lock', str(a.lock), '--expected-lock-sha256', a.lock_hash,
        '--wheelhouse', str(a.wheelhouse), '--pins', str(a.pins_path), '--output', str(output)]
    result = subprocess.run(command, cwd=tmp_path, env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    reported = json.loads(result.stdout)
    assert reported['sha256'] == release.file_hash(output)
    assert reported['source_commit'] == a.identity['source_commit']
    assert json.loads(output.read_bytes()) == build(a)
    original = output.read_bytes()
    again = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert again.returncode != 0 and output.read_bytes() == original


def test_symlink_parent_is_not_a_host_artifact_directory(artifacts, tmp_path):
    alias = tmp_path / 'linked-context'; alias.symlink_to(artifacts.context, target_is_directory=True)
    with pytest.raises(ValueError, match='sbom_symlink_refused'):
        build(artifacts, manifest_path=alias/'wheel/release-manifest.json')
