"""Qualification must consume the selected wheel, not a later source-path value.

Mechanism tests use explicitly synthetic root packages and real venv/pip. They
stop before scientific tests; their synthetic packages are not NET qualification.
"""
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

from test_link_offline_environment import wheel_bytes

SOURCE = Path(__file__).resolve().parents[1] / 'scripts'


def load(path):
    spec = importlib.util.spec_from_file_location('qualification_artifact_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def candidate(tmp_path):
    project = tmp_path / 'project'
    (project / 'scripts').mkdir(parents=True)
    (project / 'src/ciw').mkdir(parents=True)
    for name in ('check_link_installation.py', 'provision_link_environment.py'):
        shutil.copyfile(SOURCE / name, project / 'scripts' / name)
    packaging = importlib.metadata.version('packaging')
    (project / 'pyproject.toml').write_text(
        '[project]\nname="net-qualification-fixture"\nversion="1.0"\n'
        'requires-python=">=3.11"\n'
        f'dependencies=["packaging=={packaging}"]\n'
        '[project.optional-dependencies]\ndev=[]\nlegibility=[]\n')
    original = b'VALUE = 42\n'
    (project / 'src/ciw/__init__.py').write_bytes(original)
    def wheel(payload=original, extra=None, record_fault=False):
        return wheel_bytes('net-qualification-fixture', requirements=(f'packaging=={packaging}',),
            extras=('dev', 'legibility'), files={'ciw/__init__.py': payload, **(extra or {})},
            record_fault=record_fault)
    name, raw = wheel()
    path = tmp_path / name
    path.write_bytes(raw)
    return load(project/'scripts/check_link_installation.py'), path, raw, wheel


class InstallationObserved(RuntimeError):
    """Stop at the real installed-package boundary before unrelated NET imports."""


def installed_prefix(q, path, destination, monkeypatch, *, mutate=None, extra_args=()):
    real_create = q.venv.EnvBuilder.create
    real_run = q.subprocess.run
    def create(builder, target):
        result = real_create(builder, target)
        if mutate is not None:
            mutate()
        return result
    def run(command, *args, **kwargs):
        if any(isinstance(x, str) and 'import ciw, ciw.link_workflow' in x for x in command):
            raise InstallationObserved('real installation and dependency checks reached')
        return real_run(command, *args, **kwargs)
    monkeypatch.setattr(q.venv.EnvBuilder, 'create', create)
    monkeypatch.setattr(q.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', [str(q.__file__), '--wheel', str(path),
        '--output-dir', str(destination), '--require-pins', *extra_args])
    with pytest.raises(InstallationObserved):
        q.main()
    sites = list((destination/'env/lib').glob('python*/site-packages'))
    assert len(sites) == 1
    assert (sites[0]/'ciw/__init__.py').read_bytes() == b'VALUE = 42\n'
    assert json.loads((destination/'summary.json').read_bytes())['status'] == 'BLOCKED'
    return sites[0]


@pytest.mark.parametrize('mutation', ['replace', 'delete'])
def test_real_pip_consumes_validated_wheel_when_original_path_changes(candidate, tmp_path, monkeypatch, mutation):
    q, path, original, wheel = candidate
    def change():
        if mutation == 'delete':
            path.unlink()
        else:
            path.write_bytes(wheel(b'VALUE = 99\n')[1])
    out = tmp_path/'qualification'
    installed_prefix(q, path, out, monkeypatch, mutate=change)
    selected = out/'inputs'/path.name
    assert selected.read_bytes() == original
    summary = json.loads((out/'summary.json').read_bytes())
    assert summary['wheel_input']['sha256'] == hashlib.sha256(original).hexdigest()
    assert summary['wheel_input']['external_ref_checked'] is False


def test_external_wheel_anchor_is_checked_and_retained(candidate, tmp_path, monkeypatch):
    q, path, original, _ = candidate
    digest = 'sha256:' + hashlib.sha256(original).hexdigest()
    out = tmp_path/'anchored'
    installed_prefix(q, path, out, monkeypatch, extra_args=('--wheel-ref', digest))
    summary = json.loads((out/'summary.json').read_bytes())
    assert summary['wheel_input']['external_ref_checked'] is True
    assert (out/summary['wheel_input']['snapshot']).read_bytes() == original
    assert (out/'inputs'/path.name).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('bad', ['sha256:'+'0'*64, 'not-a-hash'])
def test_wrong_wheel_anchor_refuses_before_environment(candidate, tmp_path, monkeypatch, bad):
    q, path, _, _ = candidate
    monkeypatch.setattr(q.venv.EnvBuilder, 'create', lambda *a, **k: pytest.fail('created environment'))
    monkeypatch.setattr(sys, 'argv', [str(q.__file__), '--wheel', str(path), '--wheel-ref', bad,
        '--output-dir', str(tmp_path/'blocked')])
    with pytest.raises(ValueError, match='SHA-256'):
        q.main()
    assert not (tmp_path/'blocked/env').exists()


@pytest.mark.parametrize('bad', ['startup', 'traversal', 'record'])
def test_qualifier_reuses_full_wheel_policy_before_any_install(candidate, tmp_path, monkeypatch, bad):
    q, path, _, wheel = candidate
    extra = {'sitecustomize/__init__.py': b'# inert synthetic fixture\n'} if bad == 'startup' else (
        {'../escape.py': b'# inert\n'} if bad == 'traversal' else None)
    path.write_bytes(wheel(extra=extra, record_fault=bad == 'record')[1])
    # Keep source equality satisfied in the RECORD case, exposing the omitted
    # archive-integrity check rather than an already-covered CIW mismatch.
    if bad == 'record':
        (Path(q.__file__).parents[1]/'src/ciw/__init__.py').write_bytes(
            b'VALUE = 42\n# changed after RECORD\n')
    monkeypatch.setattr(q.venv.EnvBuilder, 'create', lambda *a, **k: pytest.fail('created environment'))
    monkeypatch.setattr(sys, 'argv', [str(q.__file__), '--wheel', str(path),
        '--output-dir', str(tmp_path/'blocked')])
    with pytest.raises(ValueError):
        q.main()
    assert not (tmp_path/'blocked/env').exists()


def test_snapshot_drift_after_install_is_not_accepted(candidate, tmp_path, monkeypatch):
    q, path, original, _ = candidate
    real_run = q.subprocess.run
    out = tmp_path/'drift'
    def run(command, *a, **kw):
        result = real_run(command, *a, **kw)
        if 'install' in command:
            (out/'inputs'/path.name).write_bytes(b'changed private input')
        return result
    monkeypatch.setattr(q.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', [str(q.__file__), '--wheel', str(path), '--output-dir', str(out)])
    with pytest.raises(ValueError, match='snapshot'):
        q.main()
    assert path.read_bytes() == original
    assert json.loads((out/'summary.json').read_bytes())['status'] == 'BLOCKED'


def test_recorded_wheel_ref_is_independent_of_source_path_after_review(candidate, tmp_path):
    q, path, original, _ = candidate
    data, metadata = q.review_qualification_wheel(path)
    path.unlink()
    assert data == original
    assert metadata['sha256'] == 'sha256:' + hashlib.sha256(original).hexdigest()
    assert metadata['name'] == 'net-qualification-fixture'


@pytest.mark.parametrize('injection', ['environment', 'config'])
def test_host_pip_settings_cannot_redirect_qualification_install(candidate, tmp_path, monkeypatch, injection):
    q, path, _, _ = candidate
    outside = tmp_path/'unselected-install-target'
    if injection == 'environment':
        monkeypatch.setenv('PIP_TARGET', str(outside))
    else:
        config = tmp_path/'pip.conf'
        config.write_text('[global]\ntarget = '+str(outside)+'\n')
        monkeypatch.setenv('PIP_CONFIG_FILE', str(config))
    installed_prefix(q, path, tmp_path/'qualification', monkeypatch)
    assert not outside.exists()
