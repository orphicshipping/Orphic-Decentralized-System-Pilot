"""Reproduce published experiment reductions using a pinned installed NET wheel.

Run with --root EXTERNAL_CORPUS --wheel PATH --wheel-sha256 SHA --output NEW_REPORT.
No network, fitting, source modification, MCP admission or physical clocks.
Python 3.11+, explicitly provisioned inputs and an installed NET wheel are required.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
from xml.etree import ElementTree as ET
import zipfile


HERE = Path(__file__).resolve().parent
REFERENCE_KERNEL_REVISION = '0091492b9dc237b05c33cac77d2307115bd803b2'
LEGACY_WHEEL_PATH = 'software/computational_instrumentation_workbench-0.1.0-py3-none-any.whl'
FIXTURE_HASHES = {
    'kriss-kernel-oracles.json': '06fb6c7e1777ef3095137705cdc4480bdd52b3ebc9fb193fb237d6215116f413',
    'helium-table12-rss.json': 'fe53d6fb1fb0ee0d153cd64788cecd797acdee9ac58a29ced843f70c293004d9',
}
SOURCE_HASHES = {
    'kriss-kernel-oracles.json': '1f5c80832cac048ff9e9ed68a1c987d8d6d6934759b62dd29302498066c7e8fe',
    'helium-table12-rss.json': '0a3a752a0b71bfaaace96b8cdb20560526e7f51d5283a6c2eb597b0738c6bcf1',
}
MODULE_HASHES = {
    'temperature_processing': 'f4dea340ebf3250ee8fca7c4a7d5f9b148f216c8f9dda58302ab8709bec73dea',
    'calibrated_window': '49ff8049eda6ae470df7d0fb374e6e96e51dd3709439a11fbc6cdc8eaa502350',
}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError('Fixture exceeds 2 MB limit')
    return json.loads(path.read_text(encoding='utf-8-sig'),
                      parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))


def checked_path(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('Unsafe relative path')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Path escapes corpus')
    return path


def preflight(root):
    manifest = read_json(root / 'qualification' / 'inputs.json')
    required = set()
    for name, expected in SOURCE_HASHES.items():
        fixture_path = root / 'qualification' / name
        if digest(fixture_path) != FIXTURE_HASHES[name]:
            raise ValueError('Published-reference fixture changed; explicit review and new release required')
        fixture = read_json(fixture_path)
        asset = checked_path(root, fixture['source']['asset_path'])
        if digest(asset) != expected or fixture['source']['sha256'] != expected:
            raise ValueError('Published source bytes differ from pinned reference')
        required.update({'qualification/' + name, fixture['source']['asset_path']})
    listed = [item['path'] for item in manifest['files']]
    # Corpus 0.2 also lists its historical wheel. The current wheel is supplied
    # and verified separately; source verification never uses that old artifact.
    if len(listed) != len(set(listed)) or set(listed) not in (required, required | {LEGACY_WHEEL_PATH}):
        raise ValueError('Input manifest must cover all fixtures and original sources exactly')
    manifest = {'files': [item for item in manifest['files'] if item['path'] != LEGACY_WHEEL_PATH]}
    for item in manifest['files']:
        path = checked_path(root, item['path'])
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('Retained input changed: ' + item['path'])
    return manifest


def verify_install(wheel_path, expected_sha256, modules):
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_sha256):
        raise ValueError('Expected wheel SHA256 must be 64 lowercase hexadecimal characters')
    if digest(wheel_path) != expected_sha256:
        raise ValueError('Wheel differs from explicitly supplied SHA256')
    dist = importlib.metadata.distribution('computational-instrumentation-workbench')
    def package_file(name):
        return (name.startswith('ciw/') and not name.endswith('/')
                and '__pycache__' not in name.split('/') and not name.endswith('.pyc'))

    if dist.files is None:
        raise ValueError('Installed distribution has no package file inventory')
    installed_names = {item.as_posix() for item in dist.files if package_file(item.as_posix())}
    checked = 0
    with zipfile.ZipFile(wheel_path) as wheel:
        # Every archive member participates; only installed runtime caches
        # are excluded from the metadata inventory above.
        names = [name for name in wheel.namelist() if name.startswith('ciw/') and not name.endswith('/')]
        if len(names) != len(set(names)):
            raise ValueError('Wheel package inventory contains duplicate files')
        if not installed_names or set(names) != installed_names:
            missing = sorted(installed_names - set(names))
            extra = sorted(set(names) - installed_names)
            raise ValueError(f'Wheel package inventory differs from installed distribution: missing={missing}, extra={extra}')
        for name in names:
            installed = Path(dist.locate_file(name)).resolve()
            if digest(installed) != hashlib.sha256(wheel.read(name)).hexdigest():
                raise ValueError('Installed package differs from wheel: ' + name)
            checked += 1
    if checked == 0:
        raise ValueError('Wheel contains no ciw package files')
    for name, module in modules.items():
        if Path(module.__file__).resolve() != Path(dist.locate_file('ciw/' + name + '.py')).resolve():
            raise ValueError('Module import origin does not match installed distribution')
    return checked


def workbook_cells(path):
    """Read cached numeric cells, without running Excel formulas or external links."""
    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 200 or sum(i.file_size for i in infos) > 20_000_000:
            raise ValueError('Workbook expansion exceeds limits')
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in rels}
        result = {}
        for sheet in wb.find('x:sheets', ns):
            rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = targets[rid]
            name = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            tree = ET.fromstring(z.read(name))
            cells = {}
            for cell in tree.findall('.//x:sheetData/x:row/x:c', ns):
                val = cell.find('x:v', ns)
                if val is not None and cell.attrib.get('t', 'n') == 'n':
                    cells[cell.attrib['r']] = float(val.text)
            result[sheet.attrib['name']] = cells
    return result


def check_value(actual, expected, absolute, relative=0.0):
    if not all(math.isfinite(x) for x in (actual, expected, absolute, relative)):
        raise ValueError('Nonfinite result or tolerance')
    if absolute < 0 or relative < 0:
        raise ValueError('Negative tolerance')
    error = abs(actual - expected)
    tolerance = max(absolute, relative * abs(expected))
    return {'actual': actual, 'expected': expected, 'absolute_error': error,
            'acceptance_tolerance': tolerance, 'passed': error <= tolerance}


def qualify(root, wheel_path, expected_sha256):
    manifest = preflight(root)
    from ciw import calibrated_window, temperature_processing
    modules = {'temperature_processing': temperature_processing,
               'calibrated_window': calibrated_window}
    installed_files_checked = verify_install(wheel_path, expected_sha256, modules)
    observed = {name: digest(Path(module.__file__)) for name, module in modules.items()}
    if observed != MODULE_HASHES:
        raise ValueError('NET module bytes differ from qualified release; review and repin explicitly')
    version = importlib.metadata.version('computational-instrumentation-workbench')
    if version != '0.1.0':
        raise ValueError('Unexpected NET distribution version')
    k = read_json(root / 'qualification' / 'kriss-kernel-oracles.json')
    b = read_json(root / 'qualification' / 'helium-table12-rss.json')
    cells = workbook_cells(checked_path(root, k['source']['asset_path']))
    cases = []
    if len(k['affine_cases']) != 136 or len(k['rss_uncertainty_cases']) != 6 or len(b['rows']) != 5:
        raise ValueError('Published fixture coverage changed')
    for row in k['affine_cases']:
        source = cells[row['sheet']]
        for field, key in (('input', 'value'), ('reference_emf', 'value'),
                           ('sensitivity', 'value'), ('reference_temperature_C', 'value'),
                           ('expected', 'cached_value')):
            if source[row[field]['cell']] != row[field][key]:
                raise ValueError('Workbook/fixture mismatch')
        gain = 1000.0 / row['sensitivity']['value']
        # Static linearized indicated temperature, in absolute kelvin.
        offset = (row['reference_temperature_C']['value'] + 273.15
                  - gain * row['reference_emf']['value'])
        request = {'raw_observations': {'samples': [
            {'sample_id': row['id'], 'time_s': 0.0, 'value': row['input']['value']}]},
            'calibration': {'gain': gain, 'offset_K': offset},
            'joint_covariance': {'matrix': [[0.0] * 3 for _ in range(3)]},
            'contributions': []}
        result = temperature_processing._sensor_output(request, [[]])
        actual = result['samples'][0]['value_K']
        expected = row['reference_temperature_C']['value'] + row['expected']['cached_value'] + 273.15
        # Tolerance is on deviation reproduction, not inflated by absolute temperature.
        tolerance = row['numerical_tolerance']
        check = check_value(actual, expected, max(tolerance['absolute'],
                            tolerance['relative'] * abs(row['expected']['cached_value'])))
        cases.append({'id': row['id'], 'family': 'KRISS thermocouple affine reduction',
                      'kernel': 'ciw.temperature_processing._sensor_output',
                      'source_sheet': row['sheet'], 'source_expected_cell': row['expected']['cell'],
                      'nominal_temperature_C': row['nominal_temperature_C'],
                      'unit': 'K', **check})
    for row in k['rss_uncertainty_cases']:
        source = cells[row['sheet']]
        values = []
        for item in row['input_components']:
            if source[item['cell']] != item['standard_relative_uncertainty']:
                raise ValueError('Workbook input mismatch')
            values.append(item['standard_relative_uncertainty'])
        expected_field = row['expected_standard_relative_uncertainty']
        if source[expected_field['cell']] != expected_field['cached_value']:
            raise ValueError('Workbook output mismatch')
        covariance = [[u*u if i == j else 0.0 for j in range(len(values))]
                      for i, u in enumerate(values)]
        variance = calibrated_window._project([[1.0] * len(values)], covariance)[0][0]
        tolerance = row['numerical_tolerance']
        cases.append({'id': row['id'], 'family': 'KRISS GHP uncertainty combination',
                      'kernel': 'ciw.calibrated_window._project',
                      'source_sheet': row['sheet'], 'source_expected_cell': expected_field['cell'],
                      'unit': 'relative standard uncertainty',
                      **check_value(math.sqrt(variance), expected_field['cached_value'],
                                    tolerance['absolute'], tolerance['relative'])})
    for row in b['rows']:
        covariance = [[row['uA'] ** 2, 0.0], [0.0, row['uB'] ** 2]]
        variance = calibrated_window._project([[1.0, 1.0]], covariance)[0][0]
        cases.append({'id': 'helium-L2-' + row['laboratory'],
                      'family': 'BIPM helium leak uncertainty combination',
                      'kernel': 'ciw.calibrated_window._project',
                      'source_table': 12, 'source_pdf_page': 24, 'unit': 'mol/s',
                      **check_value(math.sqrt(variance), row['expected_combined_u'],
                                    row['source_rounding_absolute_tolerance'])})
    passed = sum(c['passed'] for c in cases)
    return {
        'schema': 'net.published-experiment-software-qualification.v1',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'passed_bounded_kernel_reproduction' if passed == len(cases) else 'failed',
        'scope': 'Version-specific software reproduction of published calibrated-experiment data reductions.',
        'tested_artifact': {'distribution': 'computational-instrumentation-workbench',
                           'version': version, 'reference_kernel_git_revision': REFERENCE_KERNEL_REVISION,
                           'wheel_sha256': expected_sha256,
                           'module_sha256': observed, 'installed_package_files_matched_to_wheel': installed_files_checked, 'python': platform.python_version(),
                           'platform': platform.platform()},
        'summary': {'cases': len(cases), 'passed': passed, 'failed': len(cases) - passed,
                    'thermocouple_nominal_range_C': [200, 1000], 'GHP_budget_range_C': [200, 700]},
        'source_inputs': manifest,
        'runner_sha256': digest(Path(__file__)),
        'inputs_manifest_sha256': digest(root / 'qualification' / 'inputs.json'),
        'reference_kind': 'Author-published workbook caches and printed table values; no outputs fitted to NET results.',
        'harness_disclosures': [
            'Affine checks are static linearized reference temperature plus published thermocouple deviation, converted to kelvin.',
            'time_s=0 and zero covariance are harness-only placeholders required by private _sensor_output; their output is discarded. They do not declare actual time or zero measurement uncertainty.',
            'Only nominal temperature is checked by affine cases; temperature uncertainty is not qualified.',
            'The reference kernel revision identifies pinned module bytes; the runner does not independently attest the full wheel build provenance.',
            'RSS uses the published diagonal-covariance reduction, not a physical independence claim.',
            'Tolerances describe numerical reproduction/printed precision, not instrument accuracy or measurement uncertainty.'],
        'not_qualified': [
            'Full MCP admission, acquisition, retained execution and result-to-ledger delivery with these sources',
            'NET thermal inverse model, core-temperature prediction, or leakage inventory assessment',
            'Correlated uncertainty propagation against experimental covariance data',
            'Independent held-out physical accuracy, repeatability or drift claims',
            'Thermocouple reference serial/certificate chain missing from KRISS workbook',
            'Use outside the exact tested source rows and numerical conditions'],
        'correction_status': 'Source snapshots pinned; external corrections not continuously monitored. Source, fixture or module changes require rerun and review.',
        'cases': cases,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='External corpus with original sources and pinned derived fixtures')
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--wheel-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True, help='New report path; existing evidence is never replaced')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve a fresh report before processing data, so an occupied or
        # unwritable destination fails immediately without rerunning the cases.
        with output.open('x', encoding='utf-8') as stream:
            try:
                report = qualify(args.root.resolve(), args.wheel.resolve(), args.wheel_sha256)
            except KeyboardInterrupt:
                report = {'schema': 'net.published-experiment-software-qualification.v1',
                          'status': 'interrupted', 'error_type': 'KeyboardInterrupt',
                          'error': 'Interrupted by operator; no complete qualification result is retained.'}
            except Exception as exc:
                report = {'schema': 'net.published-experiment-software-qualification.v1',
                          'status': 'blocked_input_or_environment',
                          'error_type': type(exc).__name__, 'error': str(exc)}
            stream.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    except OSError as exc:
        report = {'status': 'blocked_output', 'error_type': type(exc).__name__,
                  'error': f'Cannot create new report at {output}: {exc}. Choose a new writable output path.'}
    summary = {k: report[k] for k in ('status', 'summary', 'error_type', 'error') if k in report}
    summary['report'] = str(output) if report['status'] != 'blocked_output' else None
    print(json.dumps(summary))
    return 130 if report['status'] == 'interrupted' else (0 if report['status'] == 'passed_bounded_kernel_reproduction' else 1)


if __name__ == '__main__':
    sys.exit(main())
