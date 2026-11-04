"""Independent stdlib-only verification of this synthetic retained pilot.

No ODSP imports, execution provider, authorization callback, network or source
acquisition. This checks offline custody and declared arithmetic, not authority.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
import base64
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()


def ref(raw):
    return 'sha256:' + sha256(raw).hexdigest()


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def read(path, limit=524288):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    check(len(raw) <= limit, 'independent_byte_budget')
    return raw


def artifact(path, expected_ref):
    raw = read(path, 131072)
    check(ref(raw) == expected_ref, 'independent_artifact_pin')
    v = json.loads(raw)
    source, transform = v['input'], v['transform']
    original = base64.b64decode(source['raw_b64'], validate=True)
    check(ref(original) == source['raw_ref'], 'independent_original_bytes')
    rows = json.loads(original)
    selected = sorted((r for r in rows if r['role'] == 'observation'), key=lambda r: r['point_id'])
    check([r['point_id'] for r in selected] == ['ASSET-A', 'ASSET-B'] and transform['rows'] == selected,
          'independent_sql_selection')
    check(ref(encode(selected)) == transform['content_ref']
          and ref(encode(transform['version'])) == transform['dataset_ref'], 'independent_output_dataset')
    check(ref(encode(source['capture_receipt'])) == source['capture_ref']
          and source['capture_receipt']['body']['dataset']['content_ref'] == source['raw_ref']
          and source['capture_receipt']['body']['dataset_ref'] == source['dataset_ref']
          and source['capture_receipt']['body']['source_ref'] == source['source_ref'], 'independent_capture_lineage')
    plan = {'definition': v['pipeline']['definition'], 'implementation': v['pipeline']['implementation'],
        'binding_ref': source['analysis_binding_ref'], 'survey_binding_ref': source['survey_binding_ref'],
        'tenant': source['tenant'], 'principal': source['principal']}
    check(ref(encode(plan)) == v['pipeline']['plan_ref']
          and ref(encode(source['analysis_binding'])) == source['analysis_binding_ref']
          and ref(encode(source['survey_binding'])) == source['survey_binding_ref'], 'independent_plan_bindings')
    check(source['schema_ref'] == transform['schema_ref'] ==
          ref(encode(v['pipeline']['definition']['input_schema'])), 'independent_schema_identity')
    for r in selected:
        check(r['unit'] == 'm' and r['frame_id'] == 'synthetic-site-grid'
              and r['clock_id'] == 'synthetic-reference' and r['time_scale'] == 'synthetic-seconds'
              and r['sample_time_s'] == 990, 'independent_unit_frame_time')
    a = v['analysis']
    check(ref(encode({k: x for k, x in a.items() if k not in ('receipt_ref', 'retention')})) == a['receipt_ref'],
          'independent_analysis_receipt')
    check(a['arguments']['keys'] == [r['point_id'] for r in selected]
          and a['value']['row_keys'] == a['arguments']['keys']
          and a['arguments']['dataset_ref'] == source['dataset_ref'], 'independent_analysis_selection')
    with localcontext() as ctx:
        ctx.prec = 50
        values = [Decimal(str(r['height_m'])) for r in selected]
        n = Decimal(len(values))
        mean = sum(values) / n
        variance = sum(Decimal(str(r['height_standard_uncertainty_m']))**2 for r in selected) / n**2
        sigma = variance.sqrt()
        rms = (sum(x*x for x in values) / n).sqrt()
    result = a['value']
    for name, expected in [('mean', mean), ('minimum', min(values)), ('maximum', max(values)), ('rms', rms)]:
        check(math.isclose(result['statistics'][name], float(expected), rel_tol=1e-13), 'independent_statistics')
    check(math.isclose(result['uncertainty']['mean']['standard_uncertainty'], float(sigma), rel_tol=1e-13)
          and math.isclose(result['propagation']['result']['covariance']['matrix'][0][0], float(variance), rel_tol=1e-13),
          'independent_declared_uncertainty')
    check(all(result['uncertainty'][name]['status'] == 'not_qualified' for name in ('minimum', 'maximum', 'rms')),
          'independent_unsupported_uncertainty')
    check(v['status'] == 'held' and v['review']['status'] == 'pending_human_review'
          and v['claims']['authenticated_time'] is False and v['claims']['nsl_admission'] == 'not_verified'
          and v['claims']['authorizes_state_admission'] is False
          and v['claims']['hosted_qualified'] is False, 'independent_claim_limits')
    check(ref(encode(v['reproducible_value'])) == v['reproducible_value_ref']
          and v['reproducible_value']['analysis_value'] == result
          and v['reproducible_value']['transform'] == transform, 'independent_value_commitment')
    return v, {'mean_m': str(mean), 'variance_m2': str(variance), 'standard_uncertainty_m': str(sigma)}


def verify(root, expected_first, expected_rerun, code_root=None):
    first, reference = artifact(root / 'analysis-first.json', expected_first)
    second, repeated = artifact(root / 'analysis-rerun.json', expected_rerun)
    check(first['reproducible_value_ref'] == second['reproducible_value_ref']
          and expected_first != expected_rerun and reference == repeated, 'independent_reproducibility')
    queue = json.loads(read(root / 'pipeline/queue/queue.json'))
    check(len(queue['jobs']) == 2 and all(j['status'] == 'completed' for j in queue['jobs']),
          'independent_exact_attempt_count')
    for v, expected in [(first, expected_first), (second, expected_rerun)]:
        job = next(j for j in queue['jobs'] if j['job_id'] == v['attempt']['job_id'])
        request = {'identity': queue['identity'], 'operation': job['operation'],
                   'arguments': job['arguments'], 'attempt': job['attempt']}
        check(sha256(encode(request)).hexdigest() == job['request_sha256'] == v['attempt']['request_sha256']
              and job['result']['artifact_ref'] == expected, 'independent_attempt_custody')
        owner = 'domain-h-pipeline:' + v['pipeline']['plan_ref'][7:]
        path = root / 'retained' / sha256(owner.encode()).hexdigest()[:32] / (job['result']['artifact_id'] + '.json')
        retained = json.loads(read(path))
        check(retained['owner'] == owner and retained['native'] == v, 'independent_owner_retention')
    with sqlite3.connect((root / 'private/instruments.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        check(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'independent_ledger_integrity')
        previous = None
        events = db.execute('SELECT seq,ref,value FROM instrument_events ORDER BY seq').fetchall()
        capture = None
        for sequence, pin, raw in events:
            v = json.loads(raw)
            check(ref(raw) == pin and v['previous_ref'] == previous and v['sequence'] == sequence,
                  'independent_receipt_chain')
            previous = pin
            if pin == first['input']['capture_ref']:
                capture = v
        check(capture == first['input']['capture_receipt'], 'independent_original_capture')
        original = db.execute('SELECT value FROM instrument_blobs WHERE ref=?', (first['input']['raw_ref'],)).fetchone()[0]
        check(ref(original) == first['input']['raw_ref'], 'independent_retained_raw')
    verified_code = 0
    if code_root is not None:
        pins = first['pipeline']['implementation']['source_refs']
        pins = {**pins, **{name: 'sha256:' + pin for name, pin in
                          first['pipeline']['implementation']['numerical']['source_files_sha256'].items()}}
        for name, pin in pins.items():
            check(not Path(name).is_absolute() and '..' not in Path(name).parts, 'independent_source_path')
            check(ref(read(code_root / name, 1048576)) == pin, 'independent_current_code_commitment')
            verified_code += 1
    return {'schema': 'odsp.independent-survey-pipeline-verification.v1', 'status': 'verified_held_offline',
        'first_artifact_ref': expected_first, 'rerun_artifact_ref': expected_rerun,
        'reproducible_value_ref': first['reproducible_value_ref'], 'declared_reference': reference,
        'retained_attempts': 2, 'independent_native_receipts': len(events), 'code_files_verified': verified_code,
        'source_acquisition': False, 'current_authority_restored': False,
        'authenticated_time': False, 'nsl_admission': 'not_verified', 'hosted_qualified': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pilot-root', required=True, type=Path)
    p.add_argument('--expected-first-ref', required=True)
    p.add_argument('--expected-rerun-ref', required=True)
    p.add_argument('--code-root', type=Path)
    a = p.parse_args()
    print(json.dumps(verify(a.pilot_root.absolute(), a.expected_first_ref, a.expected_rerun_ref, a.code_root), sort_keys=True))
