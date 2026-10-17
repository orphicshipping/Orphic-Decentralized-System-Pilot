"""Create-only corpus publication under bounded Windows sharing interference."""
from hashlib import sha256
import os
from pathlib import Path

import pytest

from ciw import proto_corpus as corpus
from test_proto_corpus import make_inputs


def fingerprint(root):
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def sharing_error(code):
    error = PermissionError('controlled Windows publication conflict')
    error.winerror = code
    return error


@pytest.mark.parametrize('code', [5, 32, 33])
def test_catalog_retries_only_publication_and_preserves_exact_inputs(tmp_path, monkeypatch, code):
    inputs = make_inputs(tmp_path)
    before = fingerprint(tmp_path)
    rename = Path.rename
    calls, staged, sleeps = [], [], []
    monkeypatch.setattr(corpus, '_WINDOWS_PUBLICATION', True, raising=False)
    monkeypatch.setattr(corpus, '_publication_pause', sleeps.append, raising=False)

    def transient(path, target):
        calls.append((path, target))
        staged.append(fingerprint(path))
        if len(calls) == 1:
            raise sharing_error(code)
        return rename(path, target)

    monkeypatch.setattr(Path, 'rename', transient)
    pin = corpus.compile_bundle(**inputs)
    assert corpus.ProtoCorpus.open(inputs['output'], pin).ref == pin
    assert len(calls) == 2 and sleeps == [0.01]
    assert calls[0] == calls[1] and staged[0] == staged[1]
    assert all((tmp_path / name).read_bytes() for name in before)
    assert {name: fingerprint(tmp_path)[name] for name in before} == before


def staged_directory(tmp_path):
    stage, target = tmp_path / 'stage', tmp_path / 'published'
    stage.mkdir()
    (stage / 'source.bin').write_bytes(b'exact retained bytes\0\xff')
    return stage, target


def test_permanent_sharing_failure_is_bounded_and_preserves_error(tmp_path, monkeypatch):
    stage, target = staged_directory(tmp_path)
    before = fingerprint(stage)
    error = sharing_error(32)
    calls, sleeps = [], []
    monkeypatch.setattr(corpus, '_WINDOWS_PUBLICATION', True)
    monkeypatch.setattr(corpus, '_publication_pause', sleeps.append)

    def blocked(path, destination):
        calls.append((path, destination))
        raise error

    monkeypatch.setattr(Path, 'rename', blocked)
    with pytest.raises(PermissionError) as caught:
        corpus._publish_directory(stage, target)
    assert caught.value is error
    assert len(calls) == 8 and sleeps == [0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64]
    assert sum(sleeps) == pytest.approx(1.27)
    assert not target.exists() and fingerprint(stage) == before


@pytest.mark.parametrize('windows,code', [(False, 5), (False, 32), (False, 33),
                                         (True, 2), (True, 3), (True, 87)])
def test_other_platforms_and_errors_never_retry(tmp_path, monkeypatch, windows, code):
    stage, target = staged_directory(tmp_path)
    error = sharing_error(code)
    calls, sleeps = [], []
    monkeypatch.setattr(corpus, '_WINDOWS_PUBLICATION', windows)
    monkeypatch.setattr(corpus, '_publication_pause', sleeps.append)

    def fail(path, destination):
        calls.append((path, destination))
        raise error

    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(PermissionError) as caught:
        corpus._publish_directory(stage, target)
    assert caught.value is error and len(calls) == 1 and not sleeps
    assert not target.exists()


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_destination_created_during_pause_is_never_replaced(tmp_path, monkeypatch, kind):
    stage, target = staged_directory(tmp_path)
    before = fingerprint(stage)
    calls = []
    monkeypatch.setattr(corpus, '_WINDOWS_PUBLICATION', True)

    def pause(delay):
        if kind == 'file':
            target.write_bytes(b'other writer')
        else:
            target.mkdir()
            (target / 'other.bin').write_bytes(b'other writer')

    def fail(path, destination):
        calls.append((path, destination))
        raise sharing_error(5)

    monkeypatch.setattr(corpus, '_publication_pause', pause)
    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(corpus.ProtoCorpusError, match='output appeared'):
        corpus._publish_directory(stage, target)
    assert len(calls) == 1 and fingerprint(stage) == before
    assert (target if kind == 'file' else target / 'other.bin').read_bytes() == b'other writer'


def test_no_links_guard_is_rechecked_before_retry(tmp_path, monkeypatch):
    stage, target = staged_directory(tmp_path)
    checked, calls = [], []
    monkeypatch.setattr(corpus, '_WINDOWS_PUBLICATION', True)
    monkeypatch.setattr(corpus, '_publication_pause', lambda delay: None)
    original_guard = corpus._no_links

    def guard(path):
        checked.append(path)
        if len(checked) == 4:
            raise corpus.ProtoCorpusError('Symlink path refused')
        return original_guard(path)

    def fail(path, destination):
        calls.append((path, destination))
        raise sharing_error(5)

    monkeypatch.setattr(corpus, '_no_links', guard)
    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(corpus.ProtoCorpusError, match='Symlink'):
        corpus._publish_directory(stage, target)
    assert len(calls) == 1 and len(checked) == 4 and not target.exists()


def test_staging_changed_to_symlink_during_pause_is_never_published(tmp_path, monkeypatch):
    stage, target = staged_directory(tmp_path)
    original = tmp_path / 'original-stage'
    outside = tmp_path / 'other-source'
    outside.mkdir()
    (outside / 'private.bin').write_bytes(b'not the staged source')
    before, other = fingerprint(stage), fingerprint(outside)
    calls = []
    monkeypatch.setattr(corpus, '_WINDOWS_PUBLICATION', True)

    def pause(delay):
        os.rename(stage, original)
        stage.symlink_to(outside, target_is_directory=True)

    def fail(path, destination):
        calls.append((path, destination))
        raise sharing_error(32)

    monkeypatch.setattr(corpus, '_publication_pause', pause)
    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(corpus.ProtoCorpusError, match='symlink'):
        corpus._publish_directory(stage, target)
    assert len(calls) == 1 and stage.is_symlink() and not target.exists()
    assert fingerprint(original) == before and fingerprint(outside) == other


@pytest.mark.parametrize('builder', ['catalog', 'evidence'])
def test_permanent_publication_failure_cleans_only_staging(tmp_path, monkeypatch, builder):
    inputs = make_inputs(tmp_path / 'catalog-inputs')
    if builder == 'catalog':
        action = lambda: corpus.compile_bundle(**inputs)
        output = inputs['output']
    else:
        import json
        from ciw import evidence_corpus as evidence
        from test_evidence_corpus import make_catalog
        catalog, record_id, source = make_catalog(tmp_path / 'evidence-inputs', b'Exact source')
        selection = tmp_path / 'selection.json'
        selection.write_text(json.dumps({'schema': 'ciw.evidence-selection.v1',
            'disclosure_ref': 'fixture:approved',
            'sources': [{'record_id': record_id, 'path': str(source)}]}))
        output = tmp_path / 'evidence'
        action = lambda: evidence.compile_evidence(catalog=catalog, selection=selection, output=output)
    before = fingerprint(tmp_path)
    monkeypatch.setattr(corpus, '_WINDOWS_PUBLICATION', True)
    monkeypatch.setattr(corpus, '_publication_pause', lambda delay: None)
    error = sharing_error(5)

    def fail(path, destination):
        raise error

    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(PermissionError) as caught:
        action()
    assert caught.value is error and not output.exists()
    assert fingerprint(tmp_path) == before
    assert not any(p.name.startswith(('.proto-corpus-', '.evidence-build-')) for p in tmp_path.rglob('*'))
