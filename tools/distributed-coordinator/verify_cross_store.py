"""Independent raw custody and arithmetic checks for the synthetic recovery drill.

Only stdlib plus the PostgreSQL driver; no candidate package validation, source
reader, queue, Session or numerical execution imports. Emits held evidence.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import UUID


def encoded(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()


def ref(raw):
    return 'sha256:' + sha256(raw).hexdigest()


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def sql_rows(path, table):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        check(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'sqlite_integrity')
        return db.execute('SELECT * FROM ' + table).fetchall()


def normal(value):
    if isinstance(value, (bytes, memoryview, bytearray)):
        return {'bytea': bytes(value).hex()}
    if isinstance(value, datetime):
        return {'timestamp': value.astimezone(timezone.utc).isoformat()}
    if isinstance(value, UUID):
        return {'uuid': str(value)}
    if isinstance(value, dict):
        return {k: normal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normal(v) for v in value]
    return value


def verify(bundle, restored, expected, dsn):
    manifest_raw = (bundle / 'recovery-set.json').read_bytes()
    check(ref(manifest_raw) == expected, 'independent_external_set_pin')
    m = json.loads(manifest_raw)
    ids = m['identities']
    for name, pin in m['members'].items():
        raw = (bundle / name).read_bytes()
        check(len(raw) == pin['bytes'] and ref(raw) == pin['ref'], 'independent_backup_member_pin')
        if name not in ('journal.sqlite', 'survey.sqlite'):
            check((restored / name).read_bytes() == raw, 'independent_restored_member_bytes')
    # Preserve immutable ledger rows even if fresh host inspection adds a newer
    # assessment. Journal high-water, hold and complete/unknown histories match.
    for table in ('instrument_config', 'instrument_sources', 'instrument_events', 'instrument_blobs',
                  'instrument_attempts', 'instrument_clocks'):
        original = sql_rows(bundle / 'survey.sqlite', table)
        current = sql_rows(restored / 'survey.sqlite', table)
        check(all(row in current for row in original), 'independent_survey_rows')
    for table in ('config', 'plans', 'jobs', 'events', 'observations', 'hold', 'clock_state'):
        check(sorted(sql_rows(bundle / 'journal.sqlite', table)) ==
              sorted(sql_rows(restored / 'journal.sqlite', table)), 'independent_journal_rows')
    holds = sql_rows(restored / 'journal.sqlite', 'hold')
    check(holds == [(1, ids['journal_unknown_job_id'])], 'independent_sticky_hold')
    config = json.loads(sql_rows(restored / 'journal.sqlite', 'config')[0][1])
    check(config['tenant'] == ids['tenant'] and config['principal'] == ids['principal']
          and ref(encoded(config['binding'])) == ids['journal_binding_ref'], 'independent_owner_binding')
    events = sql_rows(restored / 'journal.sqlite', 'events')
    for job_id in (ids['journal_job_id'], ids['journal_unknown_job_id']):
        previous = None
        for ordinal, pin, raw in sorted((r[1], r[2], r[3]) for r in events if r[0] == job_id):
            v = json.loads(raw)
            check(ref(raw) == pin and v['previous_ref'] == previous and v['ordinal'] == ordinal,
                  'independent_journal_chain')
            previous = pin
    wb = restored / 'workbench'
    wb_raw = (wb / 'manifest.json').read_bytes()
    check(ref(wb_raw) == ids['projection_manifest_ref'], 'independent_projection_pin')
    for name, pin in json.loads(wb_raw)['files'].items():
        check(ref((wb / name).read_bytes()) == pin, 'independent_workbench_file_pin')
    projection = json.loads((wb / 'projection.json').read_bytes())
    model = json.loads((wb / 'model.json').read_bytes())
    export_raw = (wb / 'journal-export.json').read_bytes()
    exported = json.loads(export_raw)
    journal_events = [json.loads(r[3]) for r in sorted(events, key=lambda r: r[1])
                      if r[0] == ids['journal_job_id']]
    check(exported['events'] == journal_events and exported['binding_ref'] == ids['journal_binding_ref']
          and exported['job_id'] == ids['journal_job_id']
          and model['sensor']['export_ref'] == ref(export_raw), 'independent_export_link')
    check(exported['summary']['unique_samples'] == 1 and exported['summary']['duplicate_arrivals'] == 1,
          'independent_no_new_measurements')
    owner = sha256(ids['retention_owner'].encode()).hexdigest()[:32]
    envelope = json.loads((restored / 'retained' / owner / (model['survey']['artifact_id'] + '.json')).read_bytes())
    check(envelope['owner'] == ids['retention_owner'], 'independent_retention_owner')
    payload = envelope['native']['retained_result'] if 'retained_result' in envelope['native'] else envelope['native']
    check(payload['result_ref'] == ids['survey_result_ref'] and payload['capture_ref'] == ids['survey_capture_ref'],
          'independent_retained_survey_identity')
    context = json.loads((wb / 'survey-context.json').read_bytes())
    capture = context['capture_receipt']
    check(capture['body']['dataset']['content_ref'] == ref((wb / 'survey-raw.json').read_bytes())
          and capture['body']['source_ref'] == ids['source_ref']
          and projection['lineage']['survey_capture_ref'] == ids['survey_capture_ref'], 'independent_raw_source_link')
    with localcontext() as ctx:
        ctx.prec = 50
        residual = Decimal('10.129') - Decimal('10.125')
        variance = Decimal('.010')**2 + Decimal('.012')**2 - 2 * Decimal('.004')**2
        sigma = variance.sqrt()
    check(abs(projection['sensor_minus_survey_m'] - float(residual)) < 1e-14
          and projection['residual_covariance']['matrix'] == [[float(variance)]]
          and abs(projection['residual_standard_uncertainty_m'] - float(sigma)) < 1e-15,
          'independent_decimal_covariance')
    point = projection['point']
    check(point['frame_id'] == 'synthetic-site-grid' and point['unit'] == 'm'
          and point['clock_id'] == 'synthetic-reference' and point['time_scale'] == 'synthetic-seconds'
          and point['sample_time_s'] == 990, 'independent_frame_unit_time')
    check(projection['execution_id'] == ids['execution_id'] and projection['result_id'] == ids['result_id']
          and projection['status'] == 'held' and projection['review']['status'] == 'pending_human_review'
          and projection['claims']['authenticated_time'] is False
          and projection['claims']['state_admission'] == 'not_performed', 'independent_original_held_occurrence')
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        jobs = db.execute('SELECT * FROM odsp_distributed_v1.jobs ORDER BY job_id').fetchall()
        effects = db.execute('SELECT * FROM public.qualification_effects ORDER BY job_id').fetchall()
        custody = json.loads((bundle / 'coordinator.json').read_bytes())
        prior = {row['job_id']: row for row in custody['jobs']}
        by_id = {row['job_id']: row for row in jobs}
        check(set(prior) == set(by_id) == set(ids['coordinator_jobs'].values()), 'independent_job_identities')
        for name, job_id in ids['coordinator_jobs'].items():
            row = by_id[job_id]
            for key in ('queue_id', 'attempt', 'request', 'request_sha256', 'reservation'):
                check(normal(row[key]) == prior[job_id][key], 'independent_request_custody')
            check(sha256(bytes(row['request'])).hexdigest() == row['request_sha256'], 'independent_request_hash')
            expected_status = {'done': 'completed', 'unknown': 'outcome_unknown', 'expired': 'outcome_unknown',
                               'queued': 'queued', 'later_queued': 'queued'}[name]
            check(row['status'] == expected_status, 'independent_no_requeue_or_resend')
            if name == 'expired':
                check(row['fence'] > prior[job_id]['fence'], 'independent_recovery_fence')
            else:
                check(normal(row) == prior[job_id], 'independent_no_terminal_or_queued_mutation')
        check(normal(effects) == custody['effects'] and len(effects) == 2
              and {r['job_id'] for r in effects} == {ids['coordinator_jobs'][n] for n in ('done', 'unknown')},
              'independent_no_repeated_effect')
        current_events = normal(db.execute('SELECT * FROM odsp_distributed_v1.events ORDER BY queue_id,job_id,ordinal').fetchall())
        check(all(row in current_events for row in custody['events']), 'independent_coordinator_history_prefix')
        for row in current_events:
            raw = bytes.fromhex(row['event']['bytea'])
            check(sha256(raw).hexdigest() == row['event_sha256'], 'independent_coordinator_event_hash')
    return {'schema': 'odsp.independent-cross-store-verification.v1', 'status': 'verified_held',
        'recovery_set_ref': expected, 'members_verified': len(m['members']), 'effect_rows': len(effects),
        'job_count': len(jobs), 'journal_hold_preserved': True, 'decimal_variance_m2': str(variance),
        'execution_id': projection['execution_id'], 'result_id': projection['result_id'],
        'source_access_or_execution': False, 'hosted_qualified': False, 'physical_qualified': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--set', type=Path, required=True)
    p.add_argument('--restored', type=Path, required=True)
    p.add_argument('--expected-ref', required=True)
    p.add_argument('--dsn', required=True)  # Host-only private Unix socket, never emitted.
    a = p.parse_args()
    print(json.dumps(verify(a.set, a.restored, a.expected_ref, a.dsn), sort_keys=True))
