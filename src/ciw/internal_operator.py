"""Bounded private inspection and recovery guidance; never a recovery executor.

Host-bound APIs read current custody/holds. The CLI only checks an explicitly
pinned saved report and labels it historical. No endpoint or grant is created.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import time

from .campaign_contracts import canonical, closed_object, parse
from .control_contracts import content_ref
from .data_instruments import content_ref as byte_ref
from .distributed_queue import PostgresQueue, _FORMAT
from .offline_survey import OfflineSurveyFlow, CLAIMS as SURVEY_CLAIMS
from .sensor_survey_workbench import inspect_workbench, _survey

MAX_JOBS = 8
MAX_REPORT = 65536
SCHEMA = 'odsp.internal-operator-inspection.v1'
CLAIMS = {'read_only': True, 'recovery_executed': False, 'dispatch_performed': False,
    'equipment_authority': False, 'state_admission': 'not_performed',
    'authenticated_time': False, 'nsl_admission': 'not_verified',
    'physical_qualification': 'not_established', 'hosted_qualification': 'not_established'}
GUIDANCE = {
    'preserve_evidence': 'Preserve exact source bytes, receipts, workspace, audit and content pins. Keep suspect files; compare a separately trusted pin before restoring a copy.',
    'expired_claim': 'Have the operator establish worker quiescence and inspect intent/audit first. Existing queue.recover() can fence expired custody as outcome_unknown; this inspection does not call it or retry the job.',
    'unknown_outcome': 'Reconcile the retained intent and actual effect with an authorized operator. Never requeue, resend or change attempt identity to hide uncertainty. A fresh attempt requires a separate explicit decision.',
    'outbox_pending': 'Inspect authorized NATS stream/consumer delivery separately. Existing reconcile_notifications() reopens only queued wake-ups; an operator may publish them after custody review. Do not reset job state or infer broker health from this database view.',
    'source_hold': 'Preserve the held capture and assess schema, control, freshness and downstream impact. Use the existing verified impact-review path when authorized; a status report cannot clear a hold or make an old report fresh.',
    'journal_hold': 'Establish that the local journal owner has exited before an operator considers existing recover(). Recovery marks unfinished reads unknown, performs no source read and does not resume a plan or send a device command.',
    'projection_review': 'Review exact source/model/runtime pins, frame/time/unit declarations and the full covariance. Keep this synthetic projection held. This tool cannot approve a finding, admit state or authorize equipment.',
    'restore_boundary': 'For the qualified local cold-restore drill, preserve the current checkpoint, quiesce owned workers, stop the source and use an empty destination with exact archive and custody pins. Stale-history restore, surviving remote writers and these file-store/journal backups remain separate gates.',
    'unavailable': 'Resolve current access, dependency or integrity failure before relying on this component. Diagnostics deliberately omit provider exception text and credentials. Do not substitute a cached report for current authority.'}


class InspectionRefusal(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise InspectionRefusal(reason)


@contextmanager
def _read_database(queue):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(queue._dsn, connect_timeout=3, row_factory=dict_row,
                         prepare_threshold=None) as db:
        with db.transaction():
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            db.execute("SET LOCAL statement_timeout=5000; SET LOCAL lock_timeout=2000; "
                       "SET LOCAL idle_in_transaction_session_timeout=5000; SET LOCAL search_path=pg_catalog",
                       prepare=False)
            settings = db.execute("SELECT current_setting('transaction_read_only') AS read_only, "
                "current_setting('transaction_isolation') AS isolation").fetchone()
            require(settings == {'read_only': 'on', 'isolation': 'repeatable read'},
                    'read_only_transaction_required')
            version = db.execute('SELECT digest FROM odsp_distributed_v1.format').fetchone()
            require(version is not None and version['digest'] == _FORMAT, 'queue_format_unavailable')
            yield db


def coordinator_snapshot(queue, job_ids, guard):
    """No constructor, row locks, recovery, notification mutation or dispatch."""
    require(type(queue) is PostgresQueue and type(job_ids) is tuple
            and 1 <= len(job_ids) <= MAX_JOBS and len(set(job_ids)) == len(job_ids)
            and all(type(j) is str and re.fullmatch(r'job-[0-9a-f]{64}', j) for j in job_ids),
            'bounded_explicit_jobs_required')
    guard()
    deadline = time.monotonic() + 10
    with _read_database(queue) as db:
        queue._queue_row(db)  # Nonlocking integrity check of the bound namespace.
        now = db.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        jobs = []
        for job_id in job_ids:
            require(time.monotonic() < deadline, 'inspection_deadline')
            row = queue._job(db, job_id)
            queue._authorize(row)
            history = queue._history(db, row)
            notification = db.execute('SELECT event_id,payload,published FROM odsp_distributed_v1.outbox '
                'WHERE queue_id=%s AND job_id=%s', (queue.queue_id, job_id)).fetchone()
            require(notification is not None, 'outbox_record_unavailable')
            payload = bytes(notification['payload'])
            expected = {'schema': 'ciw.distributed-wakeup.v1', 'queue_id': queue.queue_id,
                        'job_id': job_id, 'request_sha256': row['request_sha256']}
            require(payload == canonical(expected) and sha256(payload).hexdigest() == notification['event_id'],
                    'outbox_integrity_unavailable')
            jobs.append({'job_id': job_id, 'operation': row['decoded']['operation'],
                'request_sha256': row['request_sha256'], 'status': row['status'], 'fence': row['fence'],
                'expired_claim': row['status'] in ('claimed', 'dispatching')
                    and row['lease_until'] is not None and row['lease_until'] <= now,
                'outbox_published': notification['published'],
                'history_states': [e['state'] for e in history],
                'history_head_sha256': sha256(canonical(history[-1])).hexdigest()})
            queue._authorize(row)
            guard()
        queue._queue_row(db)
        guard()
    guard()
    return {'status': 'checked', 'queue_id': queue.queue_id, 'jobs': jobs,
        'scope': 'explicit_selected_jobs_only', 'database_observed_at': now.astimezone(timezone.utc).isoformat(),
        'clock_status': 'database_observation_not_authenticated_time',
        'transaction': 'repeatable_read_read_only', 'broker_health': 'not_inspected',
        'arguments_and_results': 'not_disclosed'}


def survey_snapshot(survey, selected):
    """Inspect drift without appending an assessment or releasing a hold."""
    survey._check_binding()
    snapshot = survey.ledger.inspect_capture(survey.source_id, selected['capture_ref'])
    report = survey.store.native(selected['artifact_id'])
    view = {'status': 'held', 'artifact_id': selected['artifact_id'], 'retained_result': report,
            'current_assessment': snapshot['assessment'], 'claims': deepcopy(SURVEY_CLAIMS)}
    points = [p for p in report['reported_points'] if p['role'] == 'observation']
    require(bool(points), 'retained_survey_observation_unavailable')
    model = {'survey': {**selected, 'result_ref': report['result_ref'], 'point_id': points[0]['point_id']}}
    _survey(view, snapshot['receipt'], snapshot['raw'], model, require_current_ready=False)
    survey._check_binding()
    current = survey.ledger.inspect_capture(survey.source_id, selected['capture_ref'])
    require(canonical(current['receipt']) == canonical(snapshot['receipt'])
            and current['raw'] == snapshot['raw'], 'survey_snapshot_changed')
    view['current_assessment'] = current['assessment']
    return view


class InternalOperatorInspection:
    """Private trusted-launcher binding. No callers choose identities or DSNs."""
    def __init__(self, *, queue, job_ids, survey, survey_record, journal, journal_job_id,
                 workbench, expected_manifest_ref, authorize):
        from atelier_mcp.observation_gateway import ObservationJournal
        require(type(queue) is PostgresQueue and isinstance(survey, OfflineSurveyFlow)
                and type(journal) is ObservationJournal and callable(authorize), 'bound_sources_required')
        require(type(job_ids) in (list, tuple) and 1 <= len(job_ids) <= MAX_JOBS
                and len(set(job_ids)) == len(job_ids), 'bounded_explicit_jobs_required')
        require(queue.identity['principal'] == survey.ledger.principal == journal.principal
                and survey.ledger.tenant == journal.tenant, 'operator_source_scope_mismatch')
        closed_object(survey_record, ('artifact_id', 'capture_ref'))
        content_ref(survey_record['capture_ref']); content_ref(expected_manifest_ref)
        self.queue, self.survey, self.journal = queue, survey, journal
        self._jobs, self._survey_record = tuple(job_ids), parse(canonical(survey_record))
        self._journal_job = journal_job_id
        self._root, self._manifest_ref = Path(workbench).absolute(), expected_manifest_ref
        self._admit, self._queue_authorize = authorize, queue.authorize
        self._objects = (queue, survey, journal, survey.ledger, survey.ledger.policy, journal._authorize)
        self._binding = self._binding_bytes()
        self.scope_ref = byte_ref(self._binding)

    def _binding_bytes(self):
        return canonical({'queue': self.queue.identity, 'queue_id': self.queue.queue_id,
            'operations': sorted(self.queue.operations), 'connection_ref': byte_ref(self.queue._dsn.encode()),
            'tenant': self.survey.ledger.tenant, 'principal': self.survey.ledger.principal,
            'journal_tenant': self.journal.tenant, 'journal_principal': self.journal.principal,
            'source_id': self.survey.source_id, 'source_ref': self.survey.source_ref,
            'journal_binding': self.journal.binding_ref, 'survey_record': self._survey_record,
            'journal_job': self._journal_job, 'jobs': list(self._jobs),
            'workbench_path': str(self._root), 'manifest_ref': self._manifest_ref})

    def _guard(self):
        require((self.queue, self.survey, self.journal, self.survey.ledger, self.survey.ledger.policy,
                 self.journal._authorize) == self._objects and self.queue.authorize is self._queue_authorize
                and self._binding_bytes() == self._binding, 'operator_host_binding_changed')
        try:
            allowed = self._admit('inspect', {'scope_ref': self.scope_ref}) is True
        except Exception:
            allowed = False
        require(allowed, 'operator_inspection_not_admitted')

    def snapshot(self, *, max_bytes=32768):
        require(type(max_bytes) is int and 1024 <= max_bytes <= MAX_REPORT, 'inspection_output_budget')
        self._guard()
        components, holds, evidence = {}, [], {}
        def read(name, call):
            self._guard()
            try:
                value = call()
            except Exception:
                components[name] = {'status': 'unavailable', 'reason': 'access_dependency_or_integrity_unavailable'}
                holds.append(name + '_unavailable')
                return None
            finally:
                self._guard()
            return value
        queue = read('coordinator', lambda: coordinator_snapshot(self.queue, self._jobs, self._guard))
        if queue is not None:
            components['coordinator'] = queue
            for job in queue['jobs']:
                if job['expired_claim']: holds.append('expired_claim')
                if job['status'] == 'outcome_unknown': holds.append('unknown_outcome')
                if job['status'] == 'queued' and not job['outbox_published']: holds.append('outbox_pending')
        survey = read('survey', lambda: survey_snapshot(self.survey, self._survey_record))
        if survey is not None:
            report, current = survey['retained_result'], survey['current_assessment']
            blocked = current['hold_ref'] is not None or current['checks'] != {
                'schema': 'within_contract', 'calibration': 'within_tolerance', 'freshness': 'within_nominal_window'}
            components['survey'] = {'status': 'held', 'current_checks': current['checks'],
                'hold_ref': current['hold_ref'], 'source_ref': report['source_ref'],
                'capture_ref': report['capture_ref'], 'dataset_ref': report['dataset_ref'],
                'result_ref': report['result_ref'], 'timestamp_status': current['timestamp_status'],
                'point_count': len(report['reported_points']), 'current_input_blocked': blocked}
            evidence['survey_result_ref'] = report['result_ref']
            if blocked: holds.append('source_hold')
        journal = read('journal', lambda: self.journal.readiness(self._journal_job))
        if journal is not None:
            components['journal'] = journal
            if journal['ready_for_held_projection'] is not True: holds.append('journal_hold')
        view = read('projection', lambda: inspect_workbench(self._root, expected_manifest_ref=self._manifest_ref))
        if view is not None:
            p = view['projection']
            links = view['source_bindings']
            matched = (all(links['survey'][k] == self._survey_record[k] for k in self._survey_record)
                and links['sensor'] == {'job_id': self._journal_job, 'binding_ref': self.journal.binding_ref}
                and survey is not None and p['lineage']['survey_result_ref'] == survey['retained_result']['result_ref'])
            components['projection'] = {'status': 'held', 'review': p['review'],
                'manifest_ref': self._manifest_ref, 'model_ref': p['model_ref'],
                'execution_id': p['execution_id'], 'result_id': p['result_id'],
                'dependencies_match': matched,
                'current_dependencies_held': not matched or journal is None
                    or journal['ready_for_held_projection'] is not True
                    or survey is None or components['survey']['current_input_blocked'],
                'hold_reasons': p['hold_reasons'], 'historical_integrity': 'checked_not_current_authority'}
            evidence['projection_manifest_ref'] = self._manifest_ref
            if not matched: holds.append('source_binding_mismatch')
        holds.extend(['projection_review', 'synthetic_inputs_only', 'authenticated_time_and_nsl_unverified'])
        keys = {'preserve_evidence', 'projection_review', 'restore_boundary'}
        keys.update(h for h in holds if h in GUIDANCE)
        if any(h.endswith('_unavailable') or h == 'source_binding_mismatch' for h in holds): keys.add('unavailable')
        result = {'schema': SCHEMA, 'status': 'held', 'inspection_scope': 'current_host_bound_local_sources',
            'observed_at': datetime.now(timezone.utc).isoformat(), 'time_status': 'host_wall_clock_only',
            'scope_ref': self.scope_ref, 'components': components, 'holds': sorted(set(holds)),
            'evidence': evidence, 'recovery_guidance': [{'id': k, 'instruction': GUIDANCE[k],
                'execution': 'not_performed'} for k in sorted(keys)], 'claims': deepcopy(CLAIMS)}
        require(len(canonical(result)) <= max_bytes, 'inspection_output_budget')
        self._guard()
        return result


def save_report(path, report):
    """Explicit create-only private operator export; no grants or source payloads."""
    _report(report)
    raw = canonical(report)
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw); output.flush(); os.fsync(output.fileno())
    return byte_ref(raw)


def _report(value):
    closed_object(value, ('schema', 'status', 'inspection_scope', 'observed_at', 'time_status',
        'scope_ref', 'components', 'holds', 'evidence', 'recovery_guidance', 'claims'))
    require(value['schema'] == SCHEMA and value['status'] == 'held'
            and value['inspection_scope'] == 'current_host_bound_local_sources'
            and value['time_status'] == 'host_wall_clock_only' and canonical(value['claims']) == canonical(CLAIMS),
            'held_inspection_report_required')
    content_ref(value['scope_ref'])
    require(type(value['components']) is dict and set(value['components']) ==
            {'coordinator', 'survey', 'journal', 'projection'}, 'inspection_components_required')
    require(type(value['holds']) is list and len(value['holds']) <= 32
            and len(value['holds']) == len(set(value['holds']))
            and all(type(h) is str and re.fullmatch(r'[a-z_]{1,80}', h) for h in value['holds']),
            'bounded_inspection_holds_required')
    require(type(value['recovery_guidance']) is list and len(value['recovery_guidance']) <= len(GUIDANCE),
            'bounded_recovery_guidance_required')
    for step in value['recovery_guidance']:
        closed_object(step, ('id', 'instruction', 'execution'))
        require(step['id'] in GUIDANCE and step['instruction'] == GUIDANCE[step['id']]
                and step['execution'] == 'not_performed', 'data_only_recovery_guidance_required')
    require(len(canonical(value)) <= MAX_REPORT, 'inspection_output_budget')


def historical_report(path, *, expected_ref):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                'owned_private_report_required')
        raw = source.read(MAX_REPORT + 1)
    require(0 < len(raw) <= MAX_REPORT and byte_ref(raw) == content_ref(expected_ref), 'report_pin_mismatch')
    value = parse(raw); _report(value)
    result = {'schema': 'odsp.internal-operator-history.v1', 'status': 'held',
        'inspection_scope': 'retained_report_only_current_sources_not_checked',
        'source_report_ref': expected_ref, 'historical_report': value,
        'current_authority_restored': False, 'claims': deepcopy(CLAIMS)}
    require(len(canonical(result)) <= MAX_REPORT, 'inspection_output_budget')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True, help='Existing private saved report; never a live connection profile')
    parser.add_argument('--expected-ref', required=True, help='Separately trusted SHA-256 pin')
    args = parser.parse_args(argv)
    try:
        result = historical_report(args.report, expected_ref=args.expected_ref)
    except Exception:
        print(json.dumps({'status': 'held', 'reason': 'report_unavailable_or_pin_mismatch'}))
        return 2
    print(canonical(result).decode('ascii'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
