"""Synthetic offline intake -> fixed SQL pipeline -> held Domain H analysis.

Creates only new private local files. No fetch, device, hosted resources,
authenticated timestamp, NSL admission or automatic downstream action.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import DataInstrumentLedger, content_ref, version_ref
from ciw.offline_survey import OfflineSurveyFlow
from ciw.principal_policy import PrincipalPolicy
from ciw.survey_analysis_pipeline import MAX_ARTIFACT, MODEL, SurveyAnalysisPipeline, validate_artifact


def source_example():
    path = Path(__file__).resolve().parents[1] / 'offline-survey/run.py'
    spec = importlib.util.spec_from_file_location('domain_h_survey_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_new(root, name, raw):
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(root):
    root.mkdir(mode=0o700, exist_ok=False)
    inputs = root / 'inputs'
    inputs.mkdir(mode=0o700)
    private = root / 'private'
    private.mkdir(mode=0o700)
    example = source_example()
    files = example.synthetic_inputs()
    declaration = parse(files['principal-policy.json'])
    declaration['revision'] = 'synthetic-domain-h-host-only'
    declaration['principals'][0]['scientific_operations'] = ['statistics.v1', 'uncertainty.propagate.v1']
    # This file is inert provenance. Active authority is the fresh callback
    # below, not a policy restored from input or saved output.
    files['principal-policy.json'] = canonical(declaration)
    for name, raw in files.items():
        write_new(inputs, name, raw)
    os.environ['NCP_RETAIN_DIR'] = str(root / 'retained')
    state = {'policy': deepcopy(declaration), 'now': 1000, 'source_allowed': True,
             'pipeline_allowed': True, 'actions': []}
    policy = PrincipalPolicy(lambda: state['policy'])
    def source_admit(action, source, context):
        return state['source_allowed'] and action in {'open', 'register', 'capture', 'read', 'assess', 'verify_chain'}
    ledger = DataInstrumentLedger(private / 'instruments.sqlite', tenant='synthetic-company',
        principal='survey-operator', policy=policy, admit=source_admit,
        clock=lambda: {'value': state['now'], 'frame': example.FRAME})
    source_ref = ledger.register_source(parse(files['source.json']), files['known-good.json'])
    flow = OfflineSurveyFlow(ledger=ledger, source_id=example.SOURCE_ID, source_ref=source_ref,
                             binding=parse(files['binding.json']))
    captured = flow.capture(files['capture.json'], expected_ref=content_ref(files['capture.json']),
        attempt='domain-h-survey-capture-1', observed={'value': 990, 'frame': example.FRAME})
    def admit(action, intent, context):
        state['actions'].append(action)
        return state['pipeline_allowed'] and action != state.get('deny_action')
    def pipeline():
        return SurveyAnalysisPipeline(root / 'pipeline', survey=flow, capture_ref=captured['capture_ref'],
            model=MODEL, expected_model_ref=version_ref(MODEL), admit=admit)
    active = pipeline()
    return SimpleNamespace(root=root, inputs=inputs, files=files, example=example, ledger=ledger,
        survey=flow, policy=policy, state=state, captured=captured, active=active, pipeline=pipeline)


def create(root):
    p = prepare(root)
    first = p.active.run('analysis-1')
    # The same attempt reopens the exact retained artifact. A separate explicit
    # rerun is a fresh occurrence with an equal reproducible value commitment.
    replay = p.active.run('analysis-1')
    second = p.active.run('independent-rerun')
    if (first['artifact_ref'] != replay['artifact_ref']
            or first['artifact_id'] != replay['artifact_id']
            or first['reproducible_value_ref'] != second['reproducible_value_ref']):
        raise ValueError('synthetic_pipeline_reproducibility_failed')
    for name, result in [('analysis-first.json', first), ('analysis-rerun.json', second)]:
        write_new(root, name, canonical(result['artifact']))
    receipt = {'schema': 'odsp.synthetic-survey-analysis-journey.v1', 'status': 'held',
        'origin': 'synthetic', 'capture_ref': p.captured['capture_ref'],
        'dataset_ref': first['artifact']['input']['dataset_ref'], 'plan_ref': p.active.plan_ref,
        'first': {k: first[k] for k in ('job_id', 'artifact_id', 'artifact_ref', 'reproducible_value_ref')},
        'rerun': {k: second[k] for k in ('job_id', 'artifact_id', 'artifact_ref', 'reproducible_value_ref')},
        'same_attempt_reused_exact_artifact': True, 'fresh_attempt_preserved_reproducible_value': True,
        'claims': first['claims']}
    write_new(root, 'journey.json', canonical(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--inspect', type=Path)
    parser.add_argument('--expected-ref')
    args = parser.parse_args()
    if args.inspect:
        if args.output or not args.expected_ref:
            parser.error('--inspect requires --expected-ref and no --output')
        fd = os.open(args.inspect, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(MAX_ARTIFACT + 1)
        if len(raw) > MAX_ARTIFACT or content_ref(raw) != args.expected_ref:
            raise ValueError('historical_artifact_byte_pin_or_budget_refused')
        artifact = validate_artifact(parse(raw), expected_ref=args.expected_ref)
        print(json.dumps({'status': 'held', 'mode': 'historical_artifact_only_current_authority_not_restored',
                          'reproducible_value_ref': artifact['reproducible_value_ref'],
                          'statistics': artifact['analysis']['value']['statistics'], 'claims': artifact['claims']}, sort_keys=True))
    else:
        if args.output is None or args.expected_ref:
            parser.error('--output requires a new private directory')
        print(json.dumps(create(args.output.absolute()), sort_keys=True))


if __name__ == '__main__':
    main()
