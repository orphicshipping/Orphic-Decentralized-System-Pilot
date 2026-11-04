"""Actual PostgreSQL custody plus retained synthetic sources; no recovery action."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch
from uuid import uuid4

import pytest

from ciw.campaign_contracts import canonical, parse
from ciw.distributed_queue import PostgresQueue, install_schema
from ciw.internal_operator import (InternalOperatorInspection, InspectionRefusal,
    coordinator_snapshot, historical_report, save_report, main)
from ciw.sensor_survey_workbench import inspect_workbench
from ciw.session import Session


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def runtime():
    import psycopg
    assert psycopg.__version__ == '3.3.7'
    bins = os.environ.get('ODSP_DISTRIBUTED_PG_BIN')
    if not bins:
        pytest.fail('explicit pinned local PostgreSQL runtime required; no mock or skipped coordinator check')
    pins = json.loads(os.environ['ODSP_INSPECTION_PG_PINS'])
    assert set(pins) == {'initdb', 'postgres', 'pg_ctl'}
    for name, pin in pins.items():
        assert sha256((Path(bins) / name).read_bytes()).hexdigest() == pin
    assert shutil.disk_usage('/tmp').free >= 1258291200 + 32 * 1024 * 1024
    base = Path(os.environ['ODSP_INTERNAL_OPERATOR_EVIDENCE'])
    helper = Path(__file__).resolve().parents[1] / 'tools/distributed-coordinator/local_runtime.py'
    local = load('odsp_operator_private_runtime', helper)
    owned = local.LocalRuntime(base / 'postgres-only', bins, '/usr/bin/false')
    share = Path(bins).parent.parent.parent.parent / 'share/postgresql' / Path(bins).parent.name
    command = [str(Path(bins) / 'initdb'), '-D', str(owned.data), '-L', str(share),
        '--auth-local=trust', '--auth-host=reject', '--no-locale', '--encoding=UTF8', '--wal-segsize=1']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    (owned.root / 'initdb.log').write_bytes(result.stdout)
    assert result.returncode == 0
    # Capacity-only settings for this new private synthetic cluster. Existing
    # databases, permissions, security settings and retention remain untouched.
    with (owned.data / 'postgresql.conf').open('a') as output:
        output.write('\nmin_wal_size=2MB\nmax_wal_size=32MB\n')
    try:
        owned.start_postgres()  # TCP disabled, socket 0700, fsync/full_page_writes on.
        assert shutil.disk_usage('/tmp').free >= 1258291200
        install_schema(owned.direct_dsn)
        yield owned
    finally:
        owned.close()


@pytest.fixture
def prepared(tmp_path, runtime):
    example = load('internal_operator_synthetic_journey', Path(__file__).resolve().parents[1] /
        'examples/sensor-survey-workbench/run.py')
    inputs = example.prepare(tmp_path / 'synthetic')
    result = inputs.active.execute(inputs.root / 'workbench')
    allowed = {'queue': True, 'operator': True}
    def authorize(principal, operation, arguments):
        if not allowed['queue']:
            raise ValueError('DO-NOT-DISCLOSE-CONNECTION-OR-ARGUMENTS')
        return True
    queue = PostgresQueue(runtime.direct_dsn, owner='synthetic-internal-operator',
        principal=inputs.ledger.principal, generation='inspection-' + uuid4().hex,
        operations=['ncp_capabilities_list'], authorize=authorize, lease_seconds=.2)
    job = queue.enqueue('ncp_capabilities_list', {'private': 'SECRET-ARGUMENT-CONTENT'}, 'private-attempt-name')
    selected = {k: inputs.model['survey'][k] for k in ('artifact_id', 'capture_ref')}
    reader = InternalOperatorInspection(queue=queue, job_ids=[job['job_id']],
        survey=inputs.survey, survey_record=selected, journal=inputs.journal,
        journal_job_id=inputs.history['job_id'], workbench=inputs.root / 'workbench',
        expected_manifest_ref=result['manifest_ref'], authorize=lambda action, request: allowed['operator'])
    return inputs, queue, job, reader, allowed, result


def database_state(runtime):
    import psycopg
    with psycopg.connect(runtime.direct_dsn) as db:
        return [db.execute('SELECT * FROM odsp_distributed_v1.' + table + ' ORDER BY 1,2').fetchall()
                for table in ['queues', 'jobs', 'events', 'outbox']]


def test_current_inspection_retains_holds_hides_payloads_and_never_mutates_custody(prepared, runtime):
    inputs, queue, job, reader, _, result = prepared
    before = database_state(runtime)
    event_count = len(inputs.journal.history(inputs.history['job_id'])['events'])
    with patch.object(queue, 'recover', side_effect=AssertionError('recovery called')), \
         patch.object(queue, 'claim_next', side_effect=AssertionError('claim called')), \
         patch.object(queue, 'reconcile_notifications', side_effect=AssertionError('outbox changed')), \
         patch('ciw.numerical_workflow.execute', side_effect=AssertionError('calculation called')):
        report = reader.snapshot()
    assert database_state(runtime) == before
    assert len(inputs.journal.history(inputs.history['job_id'])['events']) == event_count
    assert report['status'] == 'held' and report['claims']['recovery_executed'] is False
    component = report['components']['coordinator']
    assert component['transaction'] == 'repeatable_read_read_only' and component['broker_health'] == 'not_inspected'
    assert component['jobs'][0]['history_states'] == ['queued']
    assert {'outbox_pending', 'projection_review', 'synthetic_inputs_only'} <= set(report['holds'])
    assert report['components']['projection']['dependencies_match'] is True
    assert report['components']['projection']['current_dependencies_held'] is False
    assert report['components']['projection']['manifest_ref'] == result['manifest_ref']
    text = canonical(report).decode()
    assert 'SECRET-ARGUMENT-CONTENT' not in text and 'private-attempt-name' not in text
    assert runtime.direct_dsn not in text and 'host=' not in text and 'claim_id' not in text
    evidence = os.environ.get('ODSP_OPERATOR_SAVE_EVIDENCE')
    if evidence:
        root = Path(evidence)
        root.mkdir(mode=0o700, exist_ok=False)
        pin = save_report(root / 'operator-report.json', report)
        (root / 'receipt.json').write_bytes(canonical({'report_ref': pin,
            'projection_manifest_ref': result['manifest_ref'], 'queue_id': queue.queue_id,
            'custody_unchanged': database_state(runtime) == before,
            'source_read_calls': inputs.source.read_calls, 'observation_event_count': event_count,
            'inspection': 'actual private PostgreSQL and retained synthetic sources; no recovery execution'}))


def test_expired_claim_produces_guidance_without_recovering_or_resetting_job(prepared, runtime):
    _, queue, job, reader, _, _ = prepared
    claim = queue.claim_next('operator-fixture-worker')
    time.sleep(.24)
    before = database_state(runtime)
    report = reader.snapshot()
    assert database_state(runtime) == before
    assert report['components']['coordinator']['jobs'][0]['status'] == 'claimed'
    assert report['components']['coordinator']['jobs'][0]['fence'] == claim.fence.number
    assert 'expired_claim' in report['holds']
    assert all(r['execution'] == 'not_performed' for r in report['recovery_guidance'])
    assert any(r['id'] == 'expired_claim' for r in report['recovery_guidance'])


def test_unknown_outcome_is_not_automatically_retried(prepared, runtime):
    _, queue, _, reader, _, _ = prepared
    claim = queue.claim_next('unknown-fixture-worker')
    queue.begin_dispatch(claim)
    queue.abandon(claim)
    before = database_state(runtime)
    report = reader.snapshot()
    assert database_state(runtime) == before
    assert 'unknown_outcome' in report['holds']
    assert report['components']['coordinator']['jobs'][0]['status'] == 'outcome_unknown'


def test_coordinator_revocation_or_corruption_is_redacted_and_other_holds_remain(prepared):
    _, _, _, reader, allowed, _ = prepared
    allowed['queue'] = False
    report = reader.snapshot()
    assert report['components']['coordinator']['status'] == 'unavailable'
    assert report['components']['projection']['status'] == 'held'
    assert 'DO-NOT-DISCLOSE' not in canonical(report).decode()


def test_operator_revocation_or_truthy_nonboolean_denies_entire_report(prepared):
    _, _, _, reader, allowed, _ = prepared
    for denied in (False, 1, {'cached_grant': True}):
        allowed['operator'] = denied
        with pytest.raises(InspectionRefusal, match='not_admitted'):
            reader.snapshot()


def test_new_freshness_failure_is_visible_without_appending_assessment_or_clearing_hold(prepared):
    inputs, _, _, reader, _, _ = prepared
    inputs.ledger._clock = lambda: {'value': 1100, 'frame': {'clock_id': 'synthetic-reference',
        'time_scale': 'synthetic-seconds', 'unit': 's'}}
    before = inputs.ledger.verify_chain()
    with patch.object(inputs.ledger, 'assess', side_effect=AssertionError('inspection appended assessment')):
        report = reader.snapshot()
    assert inputs.ledger.verify_chain() == before
    assert 'source_hold' in report['holds']
    assert report['components']['survey']['current_input_blocked'] is True
    assert report['components']['projection']['current_dependencies_held'] is True


def test_corrupt_projection_never_recomputes_or_hides_integrity_hold(prepared):
    inputs, _, _, reader, _, _ = prepared
    file = inputs.root / 'workbench/projection.json'
    file.write_bytes(file.read_bytes() + b' ')
    with patch('ciw.numerical_workflow.execute', side_effect=AssertionError('corrupt projection recomputed')):
        report = reader.snapshot()
    assert report['components']['projection']['status'] == 'unavailable'
    assert 'projection_unavailable' in report['holds']
    assert any(r['id'] == 'preserve_evidence' for r in report['recovery_guidance'])


def test_live_database_connection_cannot_be_rebound_after_host_selection(prepared):
    _, queue, _, reader, _, _ = prepared
    queue._dsn = 'host=unselected.invalid password=DO-NOT-DISCLOSE'
    with pytest.raises(InspectionRefusal, match='binding_changed'):
        reader.snapshot()


def test_job_and_output_bounds_refuse_without_mutation(prepared, runtime):
    _, queue, job, reader, _, _ = prepared
    before = database_state(runtime)
    for jobs in ((), (job['job_id'],) * 2, ('not-a-job',), tuple('job-' + str(i)*64 for i in range(9))):
        with pytest.raises(InspectionRefusal): coordinator_snapshot(queue, jobs, lambda: None)
    for size in (True, 0, 1023, 65537):
        with pytest.raises(InspectionRefusal): reader.snapshot(max_bytes=size)
    with pytest.raises(InspectionRefusal, match='budget'): reader.snapshot(max_bytes=1024)
    assert database_state(runtime) == before


def test_private_create_only_export_and_pinned_historical_cli_never_restore_authority(prepared, capsys):
    inputs, _, _, reader, allowed, _ = prepared
    report = reader.snapshot()
    path = inputs.root / 'operator-report.json'
    pin = save_report(path, report)
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError): save_report(path, report)
    allowed['operator'] = False
    with patch('ciw.internal_operator._read_database', side_effect=AssertionError('history connected')):
        assert main(['--report', str(path), '--expected-ref', pin]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved['current_authority_restored'] is False
    assert saved['inspection_scope'] == 'retained_report_only_current_sources_not_checked'
    path.write_bytes(path.read_bytes() + b' ')
    assert main(['--report', str(path), '--expected-ref', pin]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'held'


def test_pure_projection_and_workspace_inspection_write_no_files_or_new_occurrences(prepared):
    inputs, _, _, _, _, result = prepared
    before = {p: p.read_bytes() for p in inputs.root.rglob('*') if p.is_file() and p.suffix != '.sqlite'}
    with patch('ciw.session.write_json', side_effect=AssertionError('inspection wrote a file')), \
         patch('ciw.numerical_workflow.execute', side_effect=AssertionError('inspection computed')):
        view = inspect_workbench(inputs.root / 'workbench', expected_manifest_ref=result['manifest_ref'])
        saved = Session.inspect_workspace(inputs.root / 'workbench/workspace.json')
    assert view['projection']['execution_id'] in saved['executions']
    assert {p: p.read_bytes() for p in before} == before


def test_saved_bridge_implementation_identity_is_preserved_during_new_reader_playback(prepared):
    inputs, _, _, _, _, result = prepared
    import ciw.sensor_survey_workbench as bridge
    original = parse((inputs.root / 'workbench/projection.json').read_bytes())
    with patch.object(bridge, 'IMPLEMENTATION_REF', 'sha256:' + '0' * 64):
        view = inspect_workbench(inputs.root / 'workbench', expected_manifest_ref=result['manifest_ref'])
    assert view['projection']['lineage']['bridge_implementation_ref'] == original['lineage']['bridge_implementation_ref']
