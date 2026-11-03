"""Reproducible synthetic offline survey; no network, device or scientific run.

Run with an installed candidate package or PYTHONPATH=src from the checkout.
Output is create-only. --inspect reopens an existing owned fixture.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
import json
import os
from pathlib import Path
import stat

from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import DataInstrumentLedger, content_ref
from ciw.offline_survey import OfflineSurveyFlow, SURVEY_TABLE
from ciw.principal_policy import PrincipalPolicy


FRAME = {'clock_id': 'synthetic-reference', 'time_scale': 'synthetic-seconds', 'unit': 's'}
SOURCE_ID = 'synthetic-survey'
INPUT_FILES = {'instrument.json', 'calibration-review.json', 'usage-review.json',
               'scope-review.json', 'known-good.json', 'binding.json', 'capture.json',
               'source.json', 'principal-policy.json'}


def synthetic_inputs():
    documents = {
        'instrument.json': {'schema': 'odsp.synthetic-instrument.v1', 'kind': 'offline-survey-generator',
            'physical_device': False, 'note': 'Invented local engineering coordinates; no Earth CRS or sensor.'},
        'calibration-review.json': {'schema': 'odsp.synthetic-control-review.v1',
            'control_height_m': 10, 'absolute_tolerance_m': 0.005,
            'physical_calibration': 'not_established', 'traceable_certificate': None},
        'usage-review.json': {'schema': 'odsp.synthetic-usage-review.v1',
            'scope': 'self-authored fixture only', 'network_collection': False,
            'licence': 'LicenseRef-Synthetic-Fixture', 'real_source_clearance': 'not_established'},
    }
    binding = {'schema': 'odsp.offline-survey.binding.v1', 'origin': 'synthetic',
        'frame_id': 'synthetic-site-grid', 'linear_unit': 'm',
        'axis_order': ['easting', 'northing', 'height'],
        'instrument_ref': content_ref(canonical(documents['instrument.json'])),
        'calibration_ref': content_ref(canonical(documents['calibration-review.json'])),
        'clock_frame': FRAME}
    control = {'point_id': 'CONTROL', 'role': 'control', 'easting_m': 1000,
        'northing_m': 2000, 'height_m': 10, 'height_standard_uncertainty_m': 0.005,
        'frame_id': binding['frame_id'], 'unit': 'm', 'instrument_ref': binding['instrument_ref'],
        'calibration_ref': binding['calibration_ref'], 'sample_time_s': 990,
        'clock_id': FRAME['clock_id'], 'time_scale': FRAME['time_scale']}
    documents['known-good.json'] = [control]
    documents['capture.json'] = [{**control, 'height_m': 10.002},
        {**control, 'point_id': 'ASSET-A', 'role': 'observation', 'easting_m': 1002,
         'northing_m': 2001, 'height_m': 10.125, 'height_standard_uncertainty_m': 0.012},
        {**control, 'point_id': 'ASSET-B', 'role': 'observation', 'easting_m': 1005,
         'northing_m': 2003, 'height_m': 10.25, 'height_standard_uncertainty_m': 0.015}]
    documents['binding.json'] = binding
    documents['scope-review.json'] = {'schema': 'odsp.synthetic-scope-review.v1',
        'kind': 'infrastructure', 'contains_people': False,
        'binding_ref': content_ref(canonical(binding)), 'review': 'synthetic fixture declaration only'}
    usage_ref = content_ref(canonical(documents['usage-review.json']))
    usage = {'status': 'permitted', 'document_ref': usage_ref, 'review_ref': usage_ref}
    documents['source.json'] = {'source_id': SOURCE_ID, 'version': 1, 'kind': 'infrastructure',
        'origin': 'https://example.invalid/offline-survey',
        'license': {**usage, 'expression': 'LicenseRef-Synthetic-Fixture'},
        'terms': usage, 'robots': usage,
        'scope_review_ref': content_ref(canonical(documents['scope-review.json'])),
        'identification': {'product': 'ODSP', 'version': '1.0', 'contact': 'https://example.invalid/contact'},
        'minimum_interval_seconds': 10, 'table': SURVEY_TABLE,
        'calibration': {'known_good_ref': content_ref(canonical(documents['known-good.json'])),
                        'evidence_ref': binding['calibration_ref'], 'absolute_tolerances': {'height_m': 0.005}},
        'freshness': {'frame': FRAME, 'max_age_seconds': 100, 'future_tolerance_seconds': 0}}
    documents['principal-policy.json'] = {'schema': 'ciw.principal-policy.v1',
        'revision': 'synthetic-survey-1', 'principals': [{
            'principal': 'survey-operator', 'enabled': True, 'operations': [],
            'scientific_operations': [], 'resources': [], 'reference_scopes': [], 'handoff_recipients': []}]}
    return {name: canonical(value) for name, value in documents.items()}


def read_owned(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('owned_regular_fixture_required')
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise ValueError('fixture_byte_budget')
    return raw


def write_new(root, name, raw):
    with (root / name).open('xb') as stream:
        stream.write(raw)


def open_flow(root, files):
    os.environ['NCP_RETAIN_DIR'] = str(root / 'retained')
    actions = {'open', 'register', 'capture', 'read', 'assess', 'verify_chain'}
    policy = PrincipalPolicy.from_file(root / 'principal-policy.json')
    ledger = DataInstrumentLedger(root / 'private/instruments.sqlite',
        tenant='synthetic-company', principal='survey-operator', policy=policy,
        admit=lambda action, source, context: action in actions and source in (None, SOURCE_ID),
        clock=lambda: {'value': 1000, 'frame': FRAME})
    source_ref = ledger.register_source(parse(files['source.json']), files['known-good.json'])
    flow = OfflineSurveyFlow(ledger=ledger, source_id=SOURCE_ID, source_ref=source_ref,
                             binding=parse(files['binding.json']))
    return ledger, flow


def create(root):
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    (root / 'private').mkdir(mode=0o700)
    files = synthetic_inputs()
    for name, raw in files.items():
        write_new(root, name, raw)
    ledger, flow = open_flow(root, files)
    result = flow.capture(files['capture.json'], expected_ref=content_ref(files['capture.json']),
        attempt='synthetic-survey-1', observed={'value': 990, 'frame': FRAME})
    view = flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    outputs = {
        'capture-receipt.json': ledger.receipt(SOURCE_ID, result['capture_ref']),
        'assessment-receipt.json': ledger.receipt(SOURCE_ID, view['retained_result']['assessment_ref']),
        'held-result.json': view['retained_result'], 'current-view.json': view,
        'chain.json': ledger.verify_chain()}
    for name, value in outputs.items():
        raw = canonical(value)
        write_new(root, name, raw)
        files[name] = raw
    manifest = {'schema': 'odsp.synthetic-survey.manifest.v1',
        'origin': 'synthetic', 'status': 'held', 'source_ref': flow.source_ref,
        'result': result, 'files': {name: content_ref(raw) for name, raw in files.items()},
        'trust': 'local content identities; no external anchor, authenticated time or NSL admission'}
    write_new(root, 'manifest.json', canonical(manifest))
    return manifest


def inspect(root):
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('owned_private_fixture_required')
    manifest = parse(read_owned(root / 'manifest.json'))
    if manifest.get('schema') != 'odsp.synthetic-survey.manifest.v1':
        raise ValueError('synthetic_fixture_manifest_required')
    files = {name: read_owned(root / name) for name in INPUT_FILES}
    for name, raw in files.items():
        if manifest['files'].get(name) != content_ref(raw):
            raise ValueError('fixture_input_pin_mismatch')
    ledger, flow = open_flow(root, files)
    if flow.source_ref != manifest['source_ref']:
        raise ValueError('fixture_source_pin_mismatch')
    result = manifest['result']
    view = flow.read(result['artifact_id'], capture_ref=result['capture_ref'])
    if (view['retained_result']['result_ref'] != result['result_ref']
            or ledger.snapshot(SOURCE_ID, result['capture_ref']) != files['capture.json']):
        raise ValueError('fixture_result_pin_mismatch')
    ledger.verify_chain()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New private synthetic fixture directory')
    parser.add_argument('--inspect', action='store_true', help='Reopen the existing fixture; never recapture')
    args = parser.parse_args()
    root = args.output.absolute()
    manifest = inspect(root) if args.inspect else create(root)
    print(json.dumps({'output': str(root), 'status': 'held', 'origin': 'synthetic',
                      'source_ref': manifest['source_ref'], **manifest['result']}, sort_keys=True))


if __name__ == '__main__':
    main()
