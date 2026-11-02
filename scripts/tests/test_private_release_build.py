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


def test_private_policy_is_additive_to_unchanged_baseline_requirements():
    root = Path(__file__).resolve().parents[2]
    baseline = json.loads((root / 'release/qualification-policy.json').read_text())
    private = json.loads((root / 'release/private-qualification-policy.json').read_text())
    assert baseline['repository'] == private['repository'] == builder.REPOSITORY
    assert len(baseline['required_workflows']) == 14
    assert sum(len(row['jobs']) for row in baseline['required_workflows']) == 56
    assert sum(len(row['artifacts']) for row in baseline['required_workflows']) == 45
    assert private['required_workflows'] == [{
        'path': '.github/workflows/odrp-private-host-candidate.yml',
        'jobs': ['isolated-container'], 'artifacts': ['odsp-private-host-evidence'],
        'allowed_skipped_jobs': [], 'excluded_jobs': {},
    }]


def test_current_repository_bindings_and_project_urls_are_exact():
    import tomllib
    root = Path(__file__).resolve().parents[2]
    repository = 'orphicsystems/Orphic-Decentralized-System-Protocol'
    assert builder.REPOSITORY == repository
    for name in ('qualification-policy.json', 'private-qualification-policy.json', 'private-host-contract.json'):
        assert json.loads((root / 'release' / name).read_text())['repository'] == repository
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    base = 'https://github.com/' + repository
    assert project['urls'] == {'Repository': base, 'Documentation': base + '/blob/main/docs/quickstart.md',
                               'Issues': base + '/issues'}
    assert f'The current repository is `{repository}`.' in (root / 'docs/QUALIFIED_RELEASE.md').read_text()
    assert '--env CI_REPO=' + repository + '`' in (root / 'docs/WOODPECKER_PRIVATE_CI.md').read_text()


@pytest.mark.parametrize('repository,sha', [
    ('orphicsystems/Orphic-Decentralized-Systems-Protocol', 'a' * 40),
    ('other/Orphic-Decentralized-System-Protocol', 'a' * 40),
    ('orphicsystems/Orphic-Decentralized-System-Protocol', 'b' * 40),
])
def test_github_build_refuses_noncanonical_repository_or_wrong_sha_before_export(tmp_path, monkeypatch, repository, sha):
    root, output = tmp_path / 'source', tmp_path / 'release'
    root.mkdir()
    def git(source, *args, binary=False):
        if args[0] == 'status': return ''
        if args[0] == 'rev-parse': return ('a' if args[1] == 'HEAD' else 'c') * 40
        if args[-1].endswith(':release/qualification-policy.json'):
            return json.dumps({'repository': builder.REPOSITORY}).encode()
        return '1700000000'
    monkeypatch.setattr(builder, 'git', git)
    monkeypatch.setattr(builder, 'audit_pins', lambda *args: {})
    monkeypatch.setattr(builder.metadata, 'version', lambda name: 'fixture-version')
    def forbidden(*args): raise AssertionError('failed runner identity launched a build')
    monkeypatch.setattr(builder, 'export_source', forbidden)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_REPOSITORY', repository)
    monkeypatch.setenv('GITHUB_SHA', sha)
    with pytest.raises(ValueError, match='CI runner source/repository identity mismatch'):
        builder.build(root, output, {})
    receipt = json.loads((output / 'build-receipt.json').read_text())
    assert receipt['status'] == 'failed'
    assert receipt['repository'] == builder.REPOSITORY
    assert receipt['runner_asserted_identity']['GITHUB_REPOSITORY'] == repository
    assert not (output / 'wheel').exists()


def test_private_workflow_selects_every_regular_package_root():
    import re
    root = Path(__file__).resolve().parents[2]
    workflow = (root / '.github/workflows/odrp-private-host-candidate.yml').read_text()
    pull_request = workflow.partition('  pull_request:\n')[2].partition('  workflow_dispatch:\n')[0]
    paths = set(re.findall(r"^      - ['\"]([^'\"]+)['\"]$", pull_request, re.MULTILINE))
    package_roots = {path.parent.name for path in (root / 'src').glob('*/__init__.py')}
    assert {'ciw', 'net_geomatics'} <= package_roots
    # Accept the complete src subtree or an explicit recursive filter for
    # every root. Testing derived roots catches newly added owned packages too.
    for package in package_roots:
        assert {'src/**', f'src/{package}/**'} & paths, f'private qualification omits {package}'
    assert not any(pattern.startswith('!src/') for pattern in paths)


def test_source_to_wheel_check_covers_non_ciw_package(tmp_path):
    source = tmp_path / 'source'
    wheel = tmp_path / 'computational_instrumentation_workbench-0.1.0-py3-none-any.whl'
    entries = {'ciw/__init__.py': b'# ciw\n', 'net_geomatics/__init__.py': b'# geomatics\n', 'net_geomatics/gis.py': b'# provider\n'}
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)
            target = source / 'src' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        archive.writestr('computational_instrumentation_workbench-0.1.0.dist-info/top_level.txt', 'ciw\nnet_geomatics\n')
    assert set(builder.verify_source_bytes(wheel, source)) == set(entries)
    (source / 'src/net_geomatics/gis.py').write_bytes(b'# drifted provider\n')
    with pytest.raises(ValueError, match='source-to-wheel byte mismatch: net_geomatics/gis.py'):
        builder.verify_source_bytes(wheel, source)


@pytest.fixture(scope='module')
def installed_release(tmp_path_factory):
    """Install the actual source wheel outside the checkout, without downloads.

    This focuses on the release verifier, not an MCP/scientific qualification.
    The six required runtime distributions are copied from the interpreter that
    runs this gate; their versions are recorded, not claimed as byte attestation.
    No import hooks, editable install, source overlay or verifier mock is used.
    """
    import importlib.metadata as metadata
    import os
    import shutil
    import subprocess
    import sys
    from types import SimpleNamespace
    import venv

    root = tmp_path_factory.mktemp('private-release-installed')
    source, context, environment = root / 'source', root / 'context', root / 'environment'
    source.mkdir()
    shutil.copytree(builder.ROOT / 'src', source / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    for name in ('pyproject.toml', 'LICENSE'):
        shutil.copyfile(builder.ROOT / name, source / name)
    wheels = context / 'wheel'
    wheels.mkdir(parents=True)
    result = subprocess.run([sys.executable, '-B', '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation',
                             '--wheel-dir', str(wheels), str(source)], cwd=root,
                            env={**os.environ, 'PIP_NO_INDEX':'1', 'PIP_DISABLE_PIP_VERSION_CHECK':'1',
                                 'SOURCE_DATE_EPOCH':'1700000000', 'PYTHONDONTWRITEBYTECODE':'1'},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    (root / 'wheel-build.log').write_text(result.stdout + result.stderr)
    wheel, = wheels.glob('*.whl')
    files = builder.verify_source_bytes(wheel, source)
    assert builder.contract.package_roots(files) == ['ciw', 'net_geomatics']
    assert len([name for name in files if name.startswith('net_geomatics/')]) == 7
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    probe = subprocess.run([str(python), '-I', '-B', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
                           cwd=root, capture_output=True, text=True, check=True)
    site = Path(probe.stdout.strip())
    dependencies = {}
    for name in ('numpy', 'websockets', 'mcp', 'starlette', 'uvicorn', 'jsonschema'):
        distribution = metadata.distribution(name)
        dependencies[name] = distribution.version
        for owned in distribution.files or ():
            # Console entry points outside site-packages are not needed by this
            # verifier fixture. Copy only the existing distribution's files.
            if '..' in owned.parts or owned.is_absolute():
                continue
            origin = Path(distribution.locate_file(owned))
            if not origin.is_file():
                continue
            target = site / owned
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    service = context / 'tools/private-mcp-host/adapter.py'
    service.parent.mkdir(parents=True)
    service.write_text('# Synthetic service byte fixture; never executed.\n')
    closure = {'package_roots':builder.contract.package_roots(files), 'dependencies':dependencies,
               'service_files':{'tools/private-mcp-host/adapter.py':builder.contract.file_hash(service)}, 'pins_digest':'c' * 64}
    binding = {'schema':builder.contract.SOURCE_SCHEMA, 'repository':builder.REPOSITORY, 'source_commit':'a' * 40,
               'source_tree':'b' * 40, 'closure_digest':builder.contract.digest(closure)}
    builder.bound_wheel(wheel, binding, 1700000000)
    files = builder.contract.wheel_files(wheel)
    manifest = {'schema':builder.contract.MANIFEST_SCHEMA, 'identity':{},
                'wheel':{'filename':wheel.name,'size_bytes':wheel.stat().st_size},
                'distribution':{'name':builder.contract.DISTRIBUTION,'version':'0.1.0'}, 'package_files':files, **closure}
    identity = {'schema':builder.contract.IDENTITY_SCHEMA, 'repository':builder.REPOSITORY, 'source_commit':'a' * 40,
                'source_tree':'b' * 40, 'wheel_sha256':builder.contract.file_hash(wheel),
                'package_digest':builder.contract.package_digest(manifest)}
    manifest['identity'] = identity
    path = wheels / 'release-manifest.json'
    path.write_text(json.dumps(manifest))
    result = subprocess.run([sys.executable, '-B', '-m', 'pip', '--python', str(python), 'install', '--no-index', '--no-deps',
                             '--no-compile', str(wheel)], cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    (root / 'wheel-install.log').write_text(result.stdout + result.stderr)
    fixture = SimpleNamespace(root=root, site=site, python=python, manifest=manifest, path=path,
                              identity=identity, wheel=wheel, calls=[])
    yield fixture
    (root / 'installed-verifier-calls.json').write_text(json.dumps(fixture.calls, sort_keys=True, indent=2) + '\n')


def run_installed(fixture, program):
    import subprocess
    import textwrap
    result = subprocess.run([str(fixture.python), '-I', '-B', '-c', textwrap.dedent(program)],
                            cwd=fixture.root, capture_output=True, text=True, timeout=30)
    fixture.calls.append({'argv':result.args, 'returncode':result.returncode, 'stdout':result.stdout, 'stderr':result.stderr})
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_real_installed_wheel_verifies_every_owned_root(installed_release):
    item = installed_release
    result = run_installed(item, f'''
        import ciw, ciw.operator_examples, net_geomatics.gis, json, pathlib, sys
        from ciw.private_release import verify_release
        assert pathlib.Path(ciw.__file__).is_relative_to(pathlib.Path(sys.prefix))
        assert pathlib.Path(net_geomatics.gis.__file__).is_relative_to(pathlib.Path(sys.prefix))
        assert pathlib.Path.cwd() != pathlib.Path({str(builder.ROOT)!r})
        print(json.dumps(verify_release({str(item.path)!r})))
    ''')
    assert result == item.identity


@pytest.mark.parametrize('mutation', ['changed', 'missing', 'unexpected', 'cache'])
def test_real_installed_geomatics_byte_drift_refuses(installed_release, mutation):
    item = installed_release
    target = item.site / 'net_geomatics/gis.py'
    original = target.read_bytes()
    extra = item.site / ('net_geomatics/unexpected.py' if mutation == 'unexpected' else 'net_geomatics/gis.pyc')
    try:
        if mutation == 'changed':
            target.write_bytes(original + b'\n# changed installed provider\n')
        elif mutation == 'missing':
            target.unlink()
        else:
            extra.write_bytes(b'unknown executable bytes')
        result = run_installed(item, f'''
            import json
            from ciw.private_release import verify_release
            try:
                verify_release({str(item.path)!r})
            except ValueError as error:
                print(json.dumps({{'status':'refused','reason':str(error)}}))
            else:
                raise AssertionError('modified net_geomatics installation was accepted')
        ''')
        assert result['status'] == 'refused'
    finally:
        target.write_bytes(original)
        if extra.exists():
            extra.unlink()


@pytest.mark.parametrize('loaded_first', [False, True])
def test_real_installed_root_shadow_refuses_without_importing_shadow(installed_release, loaded_first):
    item = installed_release
    shadow = item.root / ('shadow-loaded' if loaded_first else 'shadow-fresh')
    package = shadow / 'net_geomatics'
    package.mkdir(parents=True)
    marker = shadow / 'executed.txt'
    (package / '__init__.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n')
    result = run_installed(item, f'''
        import json, pathlib, sys
        from ciw.private_release import verify_release
        if {loaded_first!r}:
            import net_geomatics.gis
        sys.path.insert(0, {str(shadow)!r})
        try:
            verify_release({str(item.path)!r})
        except ValueError as error:
            assert not pathlib.Path({str(marker)!r}).exists(), 'verifier imported a shadow package'
            print(json.dumps({{'status':'refused','reason':str(error)}}))
        else:
            raise AssertionError('shadowed package root was accepted')
    ''')
    assert 'import origin mismatch: net_geomatics' in result['reason']


def test_real_installed_loaded_child_origin_refuses(installed_release):
    item = installed_release
    foreign = item.root / 'foreign-gis.py'
    foreign.write_text('VALUE = "foreign module fixture"\n')
    result = run_installed(item, f'''
        import importlib.util, json, net_geomatics, sys
        from ciw.private_release import verify_release
        spec = importlib.util.spec_from_file_location('net_geomatics.gis', {str(foreign)!r})
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules['net_geomatics.gis'] = module
        try:
            verify_release({str(item.path)!r})
        except ValueError as error:
            print(json.dumps({{'status':'refused','reason':str(error)}}))
        else:
            raise AssertionError('foreign loaded provider module was accepted')
    ''')
    assert 'loaded release module origin mismatch: net_geomatics.gis' in result['reason']


def test_real_installed_loaded_root_search_path_refuses(installed_release):
    item = installed_release
    result = run_installed(item, f'''
        import json, net_geomatics
        from ciw.private_release import verify_release
        net_geomatics.__path__.append({str(item.root)!r})
        try:
            verify_release({str(item.path)!r})
        except ValueError as error:
            print(json.dumps({{'status':'refused','reason':str(error)}}))
        else:
            raise AssertionError('modified loaded package search path was accepted')
    ''')
    assert result['status'] == 'refused'
