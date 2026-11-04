"""Create/reopen a held, synthetic sensor + local-grid survey workbench.

Requires the separately preserved Atelier observation-journal candidate.
No network, source downloads, device access, actuation or admission.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from atelier_mcp.observation_gateway import ObservationJournal, RecordedSensor
from atelier_mcp.observation_gateway.contracts import digest, encode, raw_ref
from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import content_ref
from ciw.core.covariance import create_covariance_artifact
from ciw.sensor_survey_workbench import SensorSurveyWorkbench, inspect_workbench


def survey_example():
    path = Path(__file__).resolve().parents[1] / 'offline-survey/run.py'
    spec = importlib.util.spec_from_file_location('synthetic_offline_survey', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declarations():
    common = {'unit': 'm', 'frame': 'synthetic-site-grid',
              'status': 'declared_unverified'}
    shared = {**common, 'schema': 'odsp.shared-height-component.v1',
              'standard_uncertainty_m': 0.004}
    uncertainty = create_covariance_artifact(quantity_ids=['height'], units=['m'],
        frame=common['frame'], reference_values=[10.129], matrix=[[0.0001]],
        method='synthetic_declared_marginal.v1', basis={'kind': 'observation', 'id': 'synthetic-height'},
        provenance={'provider': 'synthetic-height-fixture',
            'source_evidence_ids': [content_ref(canonical(shared))], 'source_covariance_ids': [],
            'metadata': {'status': 'declared_unverified'}},
        assumptions=['Synthetic marginal includes a shared additive component; no physical traceability.'])
    bound = {'schema': 'atelier.observation-binding.v1', 'device_id': 'synthetic-height-recorder',
        'model_id': 'synthetic-height-v1', 'firmware_ref': raw_ref(b'no physical firmware'),
        'adapter_ref': raw_ref(b'finite synthetic height recorder'),
        'configuration': {'point_id': 'ASSET-A', 'easting_m': 1002, 'northing_m': 2001},
        'origin': 'synthetic_recording', 'effects': 'observe_only',
        'channels': [{'channel_id': 'height', 'quantity': 'height', 'unit': 'm', 'frame': common['frame']}],
        'source_clock': {'clock_id': 'synthetic-reference', 'scale': 'synthetic-seconds', 'unit': 's'},
        'receipt_clock': {'clock_id': 'synthetic-reference', 'scale': 'synthetic-seconds', 'unit': 's'},
        'calibration': {'status': 'declared_unverified', 'ref': raw_ref(b'synthetic height calibration only'),
            'source_clock': 'synthetic-reference', 'valid_from_s': 900, 'valid_until_s': 1100},
        'uncertainty': {'status': 'declared_unverified', 'covariance_ref': uncertainty['covariance_id'],
                        'dependency_refs': [content_ref(canonical(shared))]}}
    sample = {'sample_id': 'height-boot-a:height:0', 'session_id': 'height-boot-a', 'sequence': 0,
        'channel_id': 'height', 'value': 10.129, 'missing_reason': None, 'quality': 'reported_good',
        'unit': 'm', 'frame': common['frame'],
        'source_stamp': {'clock_id': 'synthetic-reference', 'seconds': 990},
        'acquired_at': {'clock_id': 'synthetic-reference', 'seconds': 990},
        'received_at': {'clock_id': 'synthetic-reference', 'seconds': 992}}
    return uncertainty, shared, bound, sample


def prepare(root):
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    example = survey_example()
    survey_manifest = example.create(root / 'survey')
    files = example.synthetic_inputs()
    ledger, survey = example.open_flow(root / 'survey', files)
    uncertainty, shared, bound, sample = declarations()
    private = root / 'sensor'
    private.mkdir(mode=0o700)
    def authority(action, request):
        return {'tenant': ledger.tenant, 'principal': ledger.principal,
            'binding_ref': digest(bound), 'issuer': 'synthetic-host-only-policy',
            'policy_ref': raw_ref(b'synthetic workbench observation scope'), 'epoch': 'policy-1',
            'clock_epoch': 'synthetic-host-boot', 'expires_at_s': 1100,
            'actions': ['open', 'prepare', 'acquire', 'read', 'stop', 'recover']}
    journal = ObservationJournal(private / 'journal.sqlite', device=bound,
        tenant=ledger.tenant, principal=ledger.principal, authorize=authority,
        clock=lambda: {'clock_id': 'synthetic-reference', 'epoch': 'synthetic-host-boot', 'seconds': 1000})
    duplicate = deepcopy(sample)
    duplicate['received_at']['seconds'] = 995
    source = RecordedSensor(bound, (encode([sample]), encode([duplicate])))
    plan = journal.prepare(source, {'max_arrivals': 4, 'window_s': 2})
    history = journal.acquire(plan['plan_ref'], key='synthetic-height-1', source=source)
    exported = journal.export(history['job_id'])
    model = {'schema': 'odsp.sensor-survey-model.v1', 'model_id': 'coincident-height-discrepancy.v1',
        'survey': {k: survey_manifest['result'][k] for k in ('artifact_id', 'capture_ref', 'result_ref')},
        'sensor': {'job_id': history['job_id'], 'export_ref': raw_ref(exported),
                   'sample_ref': history['summary']['samples'][0]['sample_ref']},
        'sensor_uncertainty': uncertainty, 'shared_component': shared,
        'assumptions': ['Self-authored synthetic local-grid observations; no Earth CRS or physical calibration.',
            'Exactly coincident declared source clock/time and static point support; no clock alignment or coordinate conversion.',
            'Marginal standard uncertainties include one shared additive component with sensitivity +1 for each input.',
            'The shared component and remaining mutually independent components are declarations, not measured traceability.',
            'The residual is sensor minus survey; it does not estimate or admit a new physical state.']}
    model['survey']['point_id'] = 'ASSET-A'
    state = {'allowed': True, 'actions': []}
    def admit(action, request):
        state['actions'].append(action)
        return state['allowed'] and action in {'read', 'compute', 'retain', 'disclose'}
    active = SensorSurveyWorkbench(survey=survey, journal=journal, model=model,
        expected_model_ref=content_ref(canonical(model)), authorize=admit)
    return SimpleNamespace(active=active, survey=survey, ledger=ledger, journal=journal,
        model=model, source=source, state=state, history=history, root=root)


def create(root):
    prepared = prepare(root)
    active, source, history = prepared.active, prepared.source, prepared.history
    result = active.execute(root / 'workbench')
    with (root / 'journey.json').open('xb') as output:
        output.write(canonical({**result, 'origin': 'synthetic',
            'sensor_source_read_calls': source.read_calls,
            'arrivals': history['summary']['arrivals'],
            'unique_measurements': history['summary']['unique_samples'],
            'duplicate_arrivals': history['summary']['duplicate_arrivals']}))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--expected-manifest-ref')
    parser.add_argument('--playback-output', type=Path)
    args = parser.parse_args()
    root = args.output.absolute()
    if args.inspect:
        if not args.expected_manifest_ref or not args.playback_output:
            parser.error('--inspect requires --expected-manifest-ref and --playback-output')
        view = inspect_workbench(root / 'workbench', expected_manifest_ref=args.expected_manifest_ref,
                                 playback_output=args.playback_output)
        print(json.dumps({'status': view['status'], 'mode': 'retained_history_only',
                          'projection': view['projection']}, sort_keys=True))
    else:
        print(json.dumps(create(root), sort_keys=True))


if __name__ == '__main__':
    main()
