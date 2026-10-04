"""Check the installed hardware workflow outside its source checkout.

All observations are synthetic. This checks package and retained-workflow
behaviour; it establishes no physical qualification or release authority.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile


PROVENANCE = """
import importlib.metadata as metadata, importlib.util, json, sys
package = metadata.distribution('computational-instrumentation-workbench')
direct = package.read_text('direct_url.json')
print(json.dumps({'distribution': package.metadata['Name'], 'version': package.version,
    'python': sys.version, 'interpreter': sys.executable, 'isolated': bool(sys.flags.isolated),
    'ciw_origin': importlib.util.find_spec('ciw').origin,
    'package_root': str(package.locate_file('ciw')),
    'direct_url': json.loads(direct) if direct else None}))
"""


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _snapshot(directory: Path) -> dict:
    return {path.relative_to(directory).as_posix(): _hash(path)
            for path in sorted(directory.rglob('*')) if path.is_file()}


class Check:
    def __init__(self, python: Path, console: Path, root: Path, timeout: float):
        self.python, self.console, self.root, self.timeout = python, console, root, timeout
        self.commands: list[dict] = []
        self.checks: list[dict] = []
        (root / 'commands').mkdir()

    def require(self, name: str, condition: bool) -> None:
        self.checks.append({'check': name, 'status': 'PASS' if condition else 'FAIL'})
        if not condition:
            raise ValueError(name)

    def execute(self, name: str, command: list[str], expected: int = 0) -> dict:
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT',
                   'TEMP', 'TMP', 'TMPDIR', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        stem = f'{len(self.commands) + 1:02d}-{name}'
        stdout, stderr = (self.root / 'commands' / (stem + suffix)
                          for suffix in ('.stdout.json', '.stderr.txt'))
        receipt = {'name': name, 'argv': command, 'cwd': str(self.root),
                   'expected_returncode': expected, 'status': 'FAIL',
                   'stdout': stdout.relative_to(self.root).as_posix(),
                   'stderr': stderr.relative_to(self.root).as_posix()}
        self.commands.append(receipt)
        try:
            result = subprocess.run(command, cwd=self.root, env=environment, timeout=self.timeout,
                                    capture_output=True, check=False)
        except subprocess.TimeoutExpired as exc:
            stdout.write_bytes(exc.stdout or b'')
            stderr.write_bytes(exc.stderr or b'')
            receipt.update(returncode=None, status='TIMEOUT')
            raise ValueError(f'{name} exceeded {self.timeout:g} seconds') from exc
        stdout.write_bytes(result.stdout)
        stderr.write_bytes(result.stderr)
        receipt.update(returncode=result.returncode, stdout_sha256=_hash(stdout),
                       stderr_sha256=_hash(stderr))
        if result.returncode != expected:
            raise ValueError(f'{name} returned {result.returncode}, expected {expected}; see {stderr}')
        receipt['status'] = 'PASS'
        for raw in (result.stdout, result.stderr):
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    return value
            except (ValueError, UnicodeDecodeError):
                pass
        raise ValueError(f'{name} did not return a JSON object')

    def net(self, name: str, *arguments: str, expected: int = 0) -> dict:
        return self.execute(name, [str(self.console), 'hardware', *arguments], expected)


def _wheel_proof(check: Check, wheel: Path, package_root: Path) -> dict:
    files = {}
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            if item.is_dir() or not item.filename.startswith('ciw/'):
                continue
            relative = Path(item.filename).relative_to('ciw')
            if '..' in relative.parts or relative.is_absolute() or item.filename in files:
                raise ValueError('Invalid or duplicate package path in wheel')
            expected = sha256(archive.read(item)).hexdigest()
            installed = package_root / relative
            check.require('wheel-byte:' + item.filename,
                          installed.is_file() and _hash(installed) == expected)
            files[item.filename] = expected
    check.require('wheel-contains-hardware-module', 'ciw/hardware_workflow.py' in files)
    return {'wheel': str(wheel), 'wheel_sha256': _hash(wheel), 'matched_files': files}


def _lifecycle(check: Check) -> dict:
    root = check.root
    created = check.net('example', 'example', '--output', 'request.json')
    check.require('example-is-explicitly-synthetic',
                  created['status'] == 'created' and created['source_kind'] == 'synthetic')
    request = _read(root / 'request.json')
    _write(root / 'configuration.json', request['configuration'])
    identity = check.net('configuration', 'configuration', 'configuration.json', '--output', 'identity.json')
    check.require('configuration-reference-matches-observations',
                  identity['configuration_ref'] == request['observations']['configuration_ref']
                  and identity == _read(root / 'identity.json'))
    original = check.net('run', 'run', 'request.json', '--output-dir', 'run')
    check.require('synthetic-run-passes-declared-checks',
                  original['status'] == 'PASS' and original['source_kind'] == 'synthetic'
                  and all(row['status'] == 'PASS' for row in original['assessment']['checks']))
    before = _snapshot(root / 'run')
    inspected = check.net('inspect', 'inspect', 'run')
    module = check.execute('isolated-module-inspect',
                           [str(check.python), '-I', '-m', 'ciw.net', 'hardware', 'inspect', 'run'])
    check.require('inspection-preserves-identities-without-provider-execution',
                  inspected == original == module and inspected['provider_executed_by_inspection'] is False)
    replay = check.net('replay', 'replay', 'run', '--output-dir', 'replay')
    check.require('replay-retains-evidence-and-creates-fresh-occurrences',
                  replay['status'] == 'PASS' and replay['evidence_id'] == original['evidence_id']
                  and replay['assessment'] == original['assessment']
                  and all(replay[key] != original[key] for key in ('execution_id', 'result_id')))
    raw = (root / 'request.json').read_bytes()
    check.require('original-bytes-and-reference-retained',
                  (root / 'run/request.json').read_bytes() == raw
                  and (root / 'replay/request.json').read_bytes() == raw
                  and original['input_bytes_ref'] == replay['input_bytes_ref'] == 'sha256:' + sha256(raw).hexdigest())
    report = check.net('report', 'report', 'run', '--output', 'hardware.html')
    html = (root / 'hardware.html').read_text(encoding='utf-8')
    check.require('customer-report-retains-synthetic-and-authority-labels',
                  report['status'] == 'PASS' and 'SYNTHETIC EXAMPLE' in html
                  and 'no equipment tested' in html and 'not_verified' in html
                  and 'does not certify' in html and '<script' not in html.lower())
    for name, args, target in (
        ('example', ('example', '--output', 'request.json'), root / 'request.json'),
        ('configuration', ('configuration', 'configuration.json', '--output', 'identity.json'), root / 'identity.json'),
        ('run', ('run', 'request.json', '--output-dir', 'run'), root / 'run'),
        ('replay', ('replay', 'run', '--output-dir', 'replay'), root / 'replay'),
        ('report', ('report', 'run', '--output', 'hardware.html'), root / 'hardware.html'),
    ):
        retained = _snapshot(target) if target.is_dir() else _hash(target)
        refused = check.net('overwrite-' + name, *args, expected=1)
        after = _snapshot(target) if target.is_dir() else _hash(target)
        check.require(name + '-overwrite-refused-with-original-preserved',
                      refused['status'] == 'REFUSED' and after == retained)
    for name, status, exit_code in (('failed', 'FAIL', 2), ('missing', 'INDETERMINATE', 3)):
        variant = deepcopy(request)
        if status == 'FAIL':
            variant['observations']['records'][0]['value'] = 1
        else:
            variant['observations']['records'].pop(0)
        _write(root / (name + '.json'), variant)
        result = check.net(name + '-run', 'run', name + '.json', '--output-dir', name, expected=exit_code)
        inspection = check.net(name + '-inspect', 'inspect', name, expected=exit_code)
        exported = check.net(name + '-report', 'report', name, '--output', name + '.html', expected=exit_code)
        check.require(name + '-outcome-retained-and-exported',
                      result['status'] == inspection['status'] == exported['status'] == status
                      and ('Declared criteria: ' + status) in (root / (name + '.html')).read_text(encoding='utf-8'))
    mismatch = deepcopy(request)
    mismatch['configuration']['revision'] = 'different'
    _write(root / 'mismatch.json', mismatch)
    refused = check.net('configuration-mismatch', 'run', 'mismatch.json', '--output-dir', 'mismatch', expected=1)
    check.require('mismatched-configuration-refused-before-publication',
                  refused['status'] == 'REFUSED' and not (root / 'mismatch').exists())
    check.require('all-inspections-replays-and-exports-preserve-original-run', _snapshot(root / 'run') == before)
    check.require('no-physical-or-release-authority',
                  original['verification_status'] == 'not_verified'
                  and original['assessment']['authority']['physical_validation'] == 'not_established'
                  and original['assessment']['authority']['release_authorization'] == 'not_granted')
    return {key: original[key] for key in ('status', 'configuration_ref', 'evidence_id', 'execution_id', 'result_id')}


def qualify(python: Path, console: Path, wheel: Path, destination: Path, timeout: float) -> dict:
    python, console, wheel = (path.absolute() for path in (python, console, wheel))
    if not all(path.is_file() for path in (python, console, wheel)):
        raise ValueError('Require existing installed Python, net launcher and wheel files')
    checkout = Path(__file__).resolve().parents[1]
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Choose a new output directory')
    if destination.resolve().is_relative_to(checkout):
        raise ValueError('Output directory must be outside the source checkout')
    destination.mkdir(parents=True, exist_ok=False)
    check = Check(python, console, destination.resolve(), timeout)
    report = {'schema': 'ciw.hardware-installed-qualification.v1', 'status': 'FAIL',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'installed synthetic hardware assessment lifecycle',
              'physical_validation': 'not_established', 'release_authorization': 'not_granted',
              'commands': check.commands, 'checks': check.checks}
    try:
        provenance = check.execute('installed-provenance', [str(python), '-I', '-c', PROVENANCE])
        report['installed_package'] = provenance
        package_root = Path(provenance['package_root']).resolve(strict=True)
        editable = (provenance.get('direct_url') or {}).get('dir_info', {}).get('editable')
        check.require('isolated-installed-package-without-source-fallback',
                      provenance['isolated'] is True and editable is not True
                      and Path(provenance['ciw_origin']).resolve() == package_root / '__init__.py'
                      and not package_root.is_relative_to(checkout))
        report['wheel_byte_proof'] = _wheel_proof(check, wheel, package_root)
        report['investigation'] = _lifecycle(check)
        report['status'] = 'PASS'
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        report['failure'] = {'type': type(exc).__name__, 'reason': str(exc)}
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    _write(check.root / 'qualification.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--net', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error('timeout must be between 1 and 300 seconds')
    try:
        report = qualify(args.python, args.net, args.wheel, args.output_dir, args.timeout)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'REFUSED', 'reason': str(exc)}))
        return 2
    print(json.dumps({'status': report['status'], 'qualification': str(args.output_dir.absolute() / 'qualification.json'),
                      'commands': len(report['commands']), 'checks': len(report['checks']),
                      'failure': report.get('failure')}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
