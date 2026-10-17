"""Qualification scope uses real installed-metadata fixtures, never fake vendor binaries."""
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_link_installation.py'
spec = importlib.util.spec_from_file_location('link_qualifier', SCRIPT)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)

PROJECT = b'''[project]
name = "net-qualification-fixture"
version = "1.0"
requires-python = ">=3.11"
dependencies = ["net-base==1.0"]
[project.optional-dependencies]
dev = ["net-test==1.0", "net-yaml==1.0"]
legibility = ["net-crypto==1.0"]
unused = ["unselected @ https://invalid.example/never-fetch.whl"]
'''


@pytest.fixture
def installed(tmp_path):
    def put(name, version='1.0', requirements=(), extras=(), python=None):
        info = tmp_path / (name.replace('-', '_')+'-'+version+'.dist-info')
        info.mkdir(exist_ok=True)
        text = 'Metadata-Version: 2.3\nName: '+name+'\nVersion: '+version+'\n'
        if python is not None: text += 'Requires-Python: '+python+'\n'
        text += ''.join('Requires-Dist: '+r+'\n' for r in requirements)
        text += ''.join('Provides-Extra: '+e+'\n' for e in extras)
        (info/'METADATA').write_text(text+'\n')
        return metadata.PathDistribution(info)
    items = {n:put(n) for n in ('net-base','net-test','net-yaml','net-crypto')}
    def lookup(name):
        if name not in items: raise metadata.PackageNotFoundError(name)
        return items[name]
    return put, items, lookup


def select(installed, raw=PROJECT):
    return q.dependency_selection(raw, lookup=installed[2])


def test_all_project_direct_pins_including_yaml_are_selected(installed):
    report, distributions = select(installed)
    assert report['required_versions'] == {n:'1.0' for n in installed[1]}
    assert set(distributions) == set(installed[1])
    assert report['pinned_dependencies_match'] is True
    assert report['pin_deviations'] == []
    assert report['selected_extras'] == ['dev','legibility']
    assert report['project_ref'].startswith('sha256:')


def test_transitive_closure_is_derived_not_a_fixed_list(installed):
    put, items, _ = installed
    items['net-base'] = put('net-base', requirements=('net-leaf>=2,<3',))
    items['net-leaf'] = put('net-leaf', version='2.2')
    report, selected = select(installed)
    assert set(selected) == set(items)
    assert report['versions']['net-leaf'] == '2.2'
    assert 'net-leaf' not in report['required_versions']


def test_conditional_extras_expand_until_fixed_point(installed):
    put, items, _ = installed
    items['net-base'] = put('net-base', requirements=('net-shared==1.0',))
    items['net-test'] = put('net-test', requirements=('net-shared[details]==1.0',))
    items['net-shared'] = put('net-shared', extras=('details',), requirements=('net-leaf==1.0; extra == "details"',))
    items['net-leaf'] = put('net-leaf')
    report, selected = select(installed)
    assert 'net-leaf' in selected
    assert report['distributions']['net-shared']['extras'] == ['details']


def test_cycles_terminate_without_duplicate_distributions(installed):
    put, items, _ = installed
    items['net-base'] = put('net-base', requirements=('net-test==1.0',))
    items['net-test'] = put('net-test', requirements=('net-base==1.0',))
    report, selected = select(installed)
    assert len(selected) == 4


def test_inactive_marker_and_unrequested_extra_are_not_copied(installed):
    put, items, _ = installed
    items['net-base'] = put('net-base', requirements=(
        'no-platform==1.0; python_version < "2"',
        'unselected @ https://invalid.example/never-fetch.whl ; extra == "other"'), extras=('other',))
    report, selected = select(installed)
    assert set(selected) == set(items)


@pytest.mark.parametrize('missing',['net-yaml','net-base','net-leaf'])
def test_missing_direct_or_transitive_dependency_is_explicit(installed,missing):
    put, items, _ = installed
    if missing == 'net-leaf': items['net-base']=put('net-base',requirements=('net-leaf==1.0',))
    else: del items[missing]
    with pytest.raises(ValueError,match='Missing qualification dependency: '+missing): select(installed)


def test_direct_pin_deviation_is_reported_not_renamed_a_pin_match(installed):
    put, items, _ = installed
    items['net-base'] = put('net-base',version='1.1')
    report, selected = select(installed)
    assert report['pinned_dependencies_match'] is False
    assert report['pin_deviations']==[{'name':'net-base','required':'1.0','installed':'1.1'}]
    assert selected['net-base'].version == '1.1'


def test_transitive_conflict_is_not_covered_by_development_pin_deviation(installed):
    put, items, _ = installed
    items['net-base']=put('net-base',version='1.1',requirements=('net-leaf>=2',))
    items['net-leaf']=put('net-leaf',version='1.0')
    with pytest.raises(ValueError,match='Incompatible dependency'): select(installed)


@pytest.mark.parametrize('bad', ['net-base>=1.0','net-base==1.*','net-base @ https://invalid.example/a.whl'])
def test_direct_dependencies_must_remain_exact_offline_pins(installed,bad):
    raw=PROJECT.replace(b'net-base==1.0',bad.encode())
    with pytest.raises(ValueError): select(installed,raw)


def test_same_named_direct_requirements_cannot_have_conflicting_pins(installed):
    raw=PROJECT.replace(b'"net-yaml==1.0"',b'"net-yaml==1.0", "net-base==2.0"')
    with pytest.raises(ValueError,match='Conflicting direct pins'): select(installed,raw)


def test_active_url_and_malformed_requirements_refuse_without_echoing_secrets(installed):
    put, items, _ = installed
    for req in ('leaf @ https://user:synthetic-secret@invalid.example/a.whl', 'leaf===; synthetic-secret'):
        items['net-base']=put('net-base',requirements=(req,))
        with pytest.raises(ValueError) as result: select(installed)
        assert 'synthetic-secret' not in str(result.value)


def test_optional_requires_python_is_not_invented(installed):
    report, _ = select(installed)
    assert report['distributions']['net-base']['requires_python'] is None


def test_unsatisfied_python_constraint_is_rejected(installed):
    put, items, _ = installed
    items['net-base']=put('net-base',python='<2')
    with pytest.raises(ValueError,match='Python requirement'): select(installed)


def test_requested_but_undeclared_extra_is_rejected(installed):
    put,items,_=installed
    items['net-base']=put('net-base',requirements=('net-test[missing]==1.0',))
    with pytest.raises(ValueError,match='Undeclared dependency extra'): select(installed)


def test_metadata_identity_must_agree_with_requested_distribution(installed):
    put,items,_=installed
    items['net-base']=put('other-name')
    with pytest.raises(ValueError,match='identity'): select(installed)


def test_whole_closure_is_bounded(installed,monkeypatch):
    monkeypatch.setattr(q,'MAX_DEPENDENCIES',3,raising=False)
    with pytest.raises(ValueError,match='bound'): select(installed)


def test_metadata_snapshot_is_retained_and_stable(installed):
    report,_=select(installed)
    repeated,_=select(installed)
    assert report == repeated
    assert len(report['distributions']['net-base']['metadata_ref']) == 71
    assert report['publisher_authenticated'] is False
    assert report['vulnerabilities_checked'] is False


def test_pin_failure_and_missing_dependency_leave_blocked_receipt(tmp_path,monkeypatch):
    def missing(*args,**kwargs): raise ValueError('Missing qualification dependency: net-yaml')
    monkeypatch.setattr(q,'dependency_selection',missing,raising=False)
    target=tmp_path/'qualification'
    from test_link_offline_environment import wheel_bytes
    name, raw = wheel_bytes('net-qualification-fixture')
    selected = tmp_path/name
    selected.write_bytes(raw)
    monkeypatch.setattr(sys,'argv',[str(SCRIPT),'--wheel',str(selected),'--output-dir',str(target),'--require-pins'])
    with pytest.raises(ValueError,match='Missing qualification dependency'): q.main()
    record=json.loads((target/'summary.json').read_bytes())
    assert record['status']=='BLOCKED'
    assert record['pinned_dependencies_match'] is None
    assert record['steps']==[] and not (target/'env').exists()


def root_metadata():
    return ('Metadata-Version: 2.3\nName: net-qualification-fixture\nVersion: 1.0\n'
        'Requires-Python: >=3.11\nProvides-Extra: dev\nProvides-Extra: legibility\n'
        'Requires-Dist: net-base==1.0\n'
        'Requires-Dist: net-test==1.0; extra == "dev"\n'
        'Requires-Dist: net-yaml==1.0; extra == "dev"\n'
        'Requires-Dist: net-crypto==1.0; extra == "legibility"\n')


def test_root_wheel_dependencies_are_bound_to_selected_project(installed):
    report, _ = q.dependency_selection(PROJECT, lookup=installed[2], root_metadata=root_metadata())
    assert report['root_metadata_matches_project'] is True
    assert len(report['root_metadata_ref']) == 71


@pytest.mark.parametrize('old,new', [('net-yaml==1.0','net-yaml==2.0'),
    ('net-base==1.0','different==1.0'), ('Version: 1.0','Version: 2.0'),
    ('Requires-Python: >=3.11','Requires-Python: >=3.12')])
def test_root_wheel_metadata_drift_cannot_become_a_development_exception(installed,old,new):
    with pytest.raises(ValueError, match='Root wheel'):
        q.dependency_selection(PROJECT, lookup=installed[2], root_metadata=root_metadata().replace(old,new))


def test_real_yaml_dependency_is_present_and_importable():
    import yaml
    assert yaml.__version__ == metadata.version('PyYAML')
    assert yaml.safe_load('physical_qualification: false')['physical_qualification'] is False


@pytest.mark.parametrize('fault', ['changed','missing','symlink'])
def test_dependency_byte_drift_is_not_hidden_by_unchanged_metadata(tmp_path,fault):
    import hashlib
    path=tmp_path/'library.py'; original=b'VALUE=42\n'; path.write_bytes(original)
    records=[{'name':'net-library-fixture','files':{'library.py':hashlib.sha256(original).hexdigest()}}]
    assert q.verify_dependency_files(tmp_path,records)==1
    if fault=='changed': path.write_bytes(b'VALUE=99\n')
    elif fault=='missing': path.unlink()
    else:
        path.unlink(); (tmp_path/'target.py').write_bytes(original); path.symlink_to(tmp_path/'target.py')
    with pytest.raises((ValueError,OSError)): q.verify_dependency_files(tmp_path,records)


def test_dependency_file_receipt_cannot_name_an_external_path(tmp_path):
    records=[{'name':'synthetic','files':{'../outside':'0'*64}}]
    with pytest.raises(ValueError,match='path'):q.verify_dependency_files(tmp_path,records)
