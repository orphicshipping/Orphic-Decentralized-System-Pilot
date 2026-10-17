"""Qualify installed Link/provider profiles against exact local wheels and pins.

Run with an isolated installed interpreter. This gate never downloads providers,
installs dependencies, grants runtime authority, or accepts skipped tests. Use
provision_pinned_profiles.py to prepare the declared local provider checkouts.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    'test_calibrated_observable', 'test_identified_design',
    'test_workbench_session', 'test_workbench_transport',
    'test_link_measurement_workbench', 'test_data_sources',
    'test_data_workbench_retention',
)
MINIMUM_CASES = {'test_link_measurement_workbench': 8}


def validate_report(path: Path, expected: tuple[str, ...]) -> dict:
    """Require actual cases from every selected module, without skips or failures."""
    cases = list(ET.parse(path).getroot().iter('testcase'))
    if not cases:
        raise ValueError('No test cases were executed')
    observed = {}
    for case in cases:
        for name in expected:
            if name in case.get('classname', '').split('.'):
                observed[name] = observed.get(name, 0) + 1
        if any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')):
            raise ValueError('A required profile case did not pass: ' + case.get('name', 'unknown'))
    if set(observed) != set(expected):
        raise ValueError('Missing profile modules: ' + ', '.join(sorted(set(expected) - set(observed))))
    for name, count in observed.items():
        if count < MINIMUM_CASES.get(name, 1):
            raise ValueError('Incomplete required profile case set: ' + name)
    for suite in ET.parse(path).getroot().iter('testsuite'):
        if any(int(suite.get(field, '0')) for field in ('failures', 'errors', 'skipped')):
            raise ValueError('The test suite reports a non-passing outcome')
    return {'passed': len(cases), 'skipped': 0, 'by_module': observed,
            'junit_sha256': sha256(path.read_bytes()).hexdigest()}


def _wheel_inventory(wheel: Path, source: Path) -> dict:
    # Reuse the existing bounded wheel/RECORD validator; no archive is extracted.
    spec = importlib.util.spec_from_file_location('link_wheel_audit', ROOT / 'scripts/provision_link_environment.py')
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    reviewed = validator.inspect_wheel(wheel.name, validator._read(wheel, validator.MAX_WHEEL))
    if reviewed['name'] != 'computational-instrumentation-workbench':
        raise ValueError('Require the selected NCP wheel')
    import ciw
    installed = Path(ciw.__file__).resolve().parent
    if not installed.is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError('NCP must be installed in the selected interpreter environment')
    files = {name: digest for name, digest in reviewed['file_hashes'].items() if name.startswith('ciw/')}
    actual = {'ciw/' + p.relative_to(installed).as_posix() for p in installed.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'}}
    if not files or actual != set(files):
        raise ValueError('Installed NCP file inventory differs from the selected wheel')
    source_package = source / 'src/ciw'
    source_files = {'ciw/' + p.relative_to(source_package).as_posix() for p in source_package.rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'}}
    if source_files != set(files):
        raise ValueError('Source NCP file inventory differs from the selected wheel')
    for name, expected in files.items():
        for path in (installed.parent / name, source / 'src' / name):
            if path.is_symlink() or 'sha256:' + sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Wheel, installed package and selected source differ: ' + name)
    return {'wheel_sha256': reviewed['sha256'], 'runtime_members': len(files),
            'runtime_hashes': files, 'installed_root': str(installed)}


def _providers(root: Path, manifests: tuple[str, ...]) -> dict:
    from importlib import resources
    from ciw.provider_checkouts import validate_checkout
    pins = {}
    for name in manifests:
        declarations = json.loads(resources.files('ciw').joinpath(name).read_bytes())
        if pins.keys() & declarations.keys():
            raise ValueError('Provider manifests overlap instead of extending the selected stack')
        pins.update(declarations)
    result = {}
    for role, pin in sorted(pins.items()):
        checkout = validate_checkout(root / role, pin['revision'])
        result[role] = {'revision': pin['revision'], 'checkout': str(checkout)}
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--net-wheel', required=True, type=Path)
    parser.add_argument('--stack-root', required=True, type=Path,
                        help='Exact eleven-role calibrated/identified-design checkouts')
    parser.add_argument('--window-stack-root', type=Path,
                        help='Also require the distinct five-role calibrated-window profile')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--timeout-seconds', type=int, default=5400,
                        help='Bounded full provider replay deadline (default: 5400 seconds)')
    args = parser.parse_args(argv)
    if not 60 <= args.timeout_seconds <= 7200:
        parser.error('--timeout-seconds must be between 60 and 7200')
    source, wheel = ROOT.resolve(), args.net_wheel.resolve()
    stack = args.stack_root.resolve()
    window = args.window_stack_root.resolve() if args.window_stack_root else None
    output = args.output_dir.resolve()
    for retained in (source, stack, window, Path(sys.prefix).resolve()):
        if retained is not None and (output.is_relative_to(retained) or retained.is_relative_to(output)):
            raise ValueError('Qualification output must be outside source, providers and installed environment')
    if output.exists():
        raise FileExistsError('Use a new qualification output directory')
    before = _wheel_inventory(wheel, source)
    # Require the actual owner APIs before running the positive native profile.
    from ciw.data_sources import native_record, retain_record
    if not callable(native_record) or not callable(retain_record):
        raise ValueError('The native typed-data owner is unavailable')
    manifests = ('calibrated-observable-runtimes.json', 'identified-design-runtimes.json')
    provider_before = _providers(stack, manifests)
    window_before = _providers(window, ('calibrated-window-runtimes.json',)) if window else None
    tests = TESTS + (('test_calibrated_window',) if window else ())
    if any(not (source / 'tests' / (name + '.py')).is_file() for name in tests):
        raise ValueError('A required test module is absent from the selected source')
    output.mkdir(parents=True, exist_ok=False)
    work = output / 'installed-check'
    work.mkdir()
    shutil.copytree(source / 'tests', work / 'tests', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(source / 'examples', work / 'examples', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    config = work / 'pytest.ini'
    config.write_text('[pytest]\n', encoding='utf-8')
    environment = {key: value for key, value in os.environ.items()
                   if key not in {'PYTHONPATH', 'PYTHONHOME', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS',
                                  'GITHUB_TOKEN', 'CIW_PROVIDER_READ_TOKEN', 'SCR_READ_TOKEN',
                                  'SSH_ASKPASS', 'GCM_INTERACTIVE'}
                   and not key.startswith(('GIT_', 'GH_'))
                   and not key.startswith('CIW_') and key != 'NET_MEASUREMENT_WORKBENCH'}
    environment.update(PYTHONNOUSERSITE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                       CIW_CALIBRATED_STACK_ROOT=str(stack), CIW_IDENTIFIED_DESIGN_STACK_ROOT=str(stack))
    if window:
        environment['CIW_CALIBRATED_WINDOW_STACK_ROOT'] = str(window)
        environment['CIW_WINDOW_FIXTURE_DIR'] = str(output / 'window-evidence')
    report = {'status': 'RUNNING', 'scope': 'installed software profile qualification',
              'python': sys.version, 'executable': sys.executable, 'platform': sys.platform,
              'package': before, 'providers': provider_before, 'window_providers': window_before,
              'selected_modules': tests, 'timeout_seconds': args.timeout_seconds, 'physical_qualification': 'NOT_ESTABLISHED'}
    xml = output / 'profiles.xml'
    command = [sys.executable, '-I', '-B', '-m', 'pytest', '-q', '-c', str(config),
               '-o', 'pythonpath=', '-o', 'addopts=', '-p', 'no:cacheprovider',
               '--junitxml=' + str(xml), '--basetemp=' + str(output / 'tmp')]
    command.extend(str(work / 'tests' / (name + '.py')) for name in tests)
    report['command'] = command
    started = time.monotonic()
    status = 1
    try:
        with (output / 'profiles.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=work, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=args.timeout_seconds, check=False)
        report['returncode'] = result.returncode
        if result.returncode:
            raise RuntimeError('Installed profile tests failed; retained profiles.log and profiles.xml')
        report['tests'] = validate_report(xml, tests)
        report['status'] = 'PASS_INSTALLED_PROVIDER_AND_TYPED_DATA_PROFILES'
        status = 0
    except Exception as exc:
        report.update(status='FAILED', error=f'{type(exc).__name__}: {exc}')
    finally:
        try:
            if _wheel_inventory(wheel, source) != before or _providers(stack, manifests) != provider_before:
                raise ValueError('A retained package or provider changed during qualification')
            if window and _providers(window, ('calibrated-window-runtimes.json',)) != window_before:
                raise ValueError('A calibrated-window provider changed during qualification')
            report['retained_inputs_unchanged'] = True
        except Exception as exc:
            report.update(status='FAILED', integrity_error=f'{type(exc).__name__}: {exc}')
            status = 1
        report['seconds'] = time.monotonic() - started
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'report': str(output / 'report.json')}))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
