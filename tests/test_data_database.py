"""Real local PostgreSQL/PgBouncer construction and inspection qualification.

All DDL and data below belong to freshly created synthetic fixtures. These
tests never use a user-supplied or hosted DSN. Keep runtime logs on failure.
"""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from threading import Event, Thread
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from ciw import data_database as module
from ciw.campaign_contracts import canonical
from ciw.data_database import (BackupArtifact, DataDatabaseHost, DatabaseRefusal,
                               DatabaseVocabulary, ScratchDatabase, capture_schema)
from test_distributed_coordinator import runtime


SOURCE = 'sha256:' + 'a' * 64


def ref(value):
    return 'sha256:' + sha256(value if type(value) is bytes else canonical(value)).hexdigest()


def connect(dsn, **kwargs):
    import psycopg
    return psycopg.connect(dsn, connect_timeout=3, prepare_threshold=None, **kwargs)


def vocabulary(namespace):
    return DatabaseVocabulary({'namespace': namespace, 'tables': {
        'assets': {'name': 'assets', 'columns': {
            'id': {'name': 'id', 'type': 'integer', 'nullable': False},
            'label': {'name': 'label', 'type': 'string', 'nullable': False},
            'value': {'name': 'measurement', 'type': 'number', 'nullable': True},
            'active': {'name': 'active', 'type': 'boolean', 'nullable': True}}, 'primary_key': ['id']}},
        'indexes': {'by_label': {'name': 'assets_by_label', 'table': 'assets', 'columns': ['label'], 'unique': False}},
        'changes': {'note': {'table': 'assets', 'column': {'name': 'note', 'type': 'string', 'nullable': True}}}})


@pytest.fixture(scope='module')
def scratch_runtime(runtime):
    import psycopg.sql
    from psycopg.conninfo import make_conninfo
    assert runtime.pooler is not None, 'database qualification requires the pinned transaction pooler'
    assert os.statvfs('/tmp').f_bavail * os.statvfs('/tmp').f_frsize > 1024 ** 3
    assert os.statvfs('/workspace').f_bavail * os.statvfs('/workspace').f_frsize > 512 * 1024 ** 2
    name = 'odsp_scratch_' + uuid4().hex[:16]
    with connect(runtime.direct_dsn, autocommit=True) as db:
        db.execute(psycopg.sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(psycopg.sql.Identifier(name)))
    dsn = make_conninfo(runtime.direct_dsn, dbname=name)
    try:
        yield SimpleNamespace(runtime=runtime, dsn=dsn, scratch=ScratchDatabase(dsn, root=runtime.root))
    finally:
        with connect(runtime.direct_dsn, autocommit=True) as db:
            db.execute(psycopg.sql.SQL('DROP DATABASE {}').format(psycopg.sql.Identifier(name)))


@pytest.fixture
def configured(runtime, scratch_runtime):
    import psycopg.sql
    namespace = 'odsp_data_' + uuid4().hex[:12]
    vocab = vocabulary(namespace)
    with connect(runtime.direct_dsn) as db:
        for statement in vocab.statements('schema', ['assets']):
            db.execute(statement)
        db.execute(psycopg.sql.SQL('INSERT INTO {}.assets(id,label,measurement,active) VALUES(%s,%s,%s,%s)').format(
            psycopg.sql.Identifier(namespace)), (1, 'private-fixture-row', 1.25, True))
    baseline = capture_schema(runtime.dsn, namespace)
    authority = {'principal': 'alice', 'tenant': 'local-test', 'source_ref': SOURCE,
                 'revision': 'one', 'enabled': True, 'operations': list(module.TOOLS)}
    def make(**overrides):
        return DataDatabaseHost(**{
            'dsn': runtime.dsn, 'vocabulary': vocab, 'baseline': baseline,
            'principal': 'alice', 'tenant': 'local-test', 'source_ref': SOURCE,
            'read_authority': lambda: deepcopy(authority), 'scratch': scratch_runtime.scratch, **overrides})
    value = SimpleNamespace(runtime=runtime, scratch=scratch_runtime, vocabulary=vocab, namespace=namespace,
                            baseline=baseline, authority=authority, make=make)
    value.host = make()
    try:
        yield value
    finally:
        with connect(runtime.direct_dsn) as db:
            db.execute(psycopg.sql.SQL('DROP SCHEMA {} CASCADE').format(psycopg.sql.Identifier(namespace)))


def ok(host, name, args=None):
    result = host.call(name, {} if args is None else args)
    assert result['status'] == 'ok', result
    body = result['data']
    assert body['receipt_ref'] == ref({k: v for k, v in body.items() if k != 'receipt_ref'})
    assert body['authorizes_execution'] is False
    assert body['operational_writes_performed'] is False
    assert body['nsl_admission'] == 'not_established'
    assert body['tbrt_evidence'] == 'not_bound'
    assert len(canonical(host.mcp_result(name, result))) <= 262144
    return body['data']


def refused(host, name, args=None, code=None):
    result = host.call(name, {} if args is None else args)
    assert result['status'] == 'refused' and result['data'] is None, result
    if code:
        assert result['error']['code'] == code, result
    assert 'password' not in json.dumps(result)
    return result


def plan_args(host, kind='indexes', items=None):
    return {'kind': kind, 'items': items or ['by_label'],
            'vocabulary_ref': host.vocabulary.ref, 'schema_ref': host.schema_ref}


def assert_scratch_empty(context):
    with connect(context.dsn) as db:
        assert db.execute("SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND left(nspname,3)<>'pg_'").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('information_schema') AND left(n.nspname,3)<>'pg_'").fetchone() == (0,)


def test_real_pool_health_schema_and_no_row_disclosure(configured):
    c = configured
    health = ok(c.host, 'ncp_dbops_health')
    assert health['server_version'] == 170011
    assert health['inspection_transaction_read_only'] is True
    schema = ok(c.host, 'ncp_dbops_schema')
    assert schema == {'snapshot': c.baseline, 'schema_ref': c.host.schema_ref}
    assert 'private-fixture-row' not in json.dumps(schema)
    assert ok(c.host, 'ncp_dbops_drift', {'schema_ref': c.host.schema_ref})['drift'] is False
    advice = ok(c.host, 'ncp_dbops_advice')
    assert advice['query_text_disclosed'] is False
    assert advice['query_statistics'] == 'host_query_ids_unbound'
    assert {'kind': 'review_index_candidate', 'index_id': 'by_label',
            'reason': 'host_reviewed_template_absent', 'benefit_established': False} in advice['suggestions']


@pytest.mark.parametrize(('kind', 'items'), [('indexes', ['by_label']), ('add_columns', ['note'])])
def test_real_index_and_migration_validation_rolls_back(configured, kind, items):
    c = configured
    args = plan_args(c.host, kind, items)
    plan = ok(c.host, 'ncp_db_plan', args)
    assert plan['apply_available'] is False and plan['database_ref']
    before = capture_schema(c.runtime.dsn, c.namespace)
    data = ok(c.host, 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']})
    assert data['validated'] and data['rollback_verified']
    assert data['validation_scope'] == 'empty_synthetic_baseline_ddl_only'
    assert data['operational_data_compatibility'] == 'not_established'
    assert data['result_schema']['namespace_exists'] is True
    if kind == 'indexes':
        assert 'assets_by_label' in {row[1] for row in data['result_schema']['indexes']}
    else:
        assert 'note' in {row[1] for row in data['result_schema']['columns']}
    assert capture_schema(c.runtime.dsn, c.namespace) == before
    assert_scratch_empty(c.scratch)
    with connect(c.runtime.direct_dsn) as db:
        assert db.execute(f'SELECT id,label,measurement,active FROM "{c.namespace}".assets').fetchall() == [(1, 'private-fixture-row', 1.25, True)]


def test_real_construction_for_absent_namespace_and_existing_refusal(configured):
    c = configured
    fresh = vocabulary('odsp_new_' + uuid4().hex[:12])
    baseline = capture_schema(c.runtime.dsn, fresh.value()['namespace'])
    host = c.make(vocabulary=fresh, baseline=baseline)
    args = plan_args(host, 'schema', ['assets'])
    plan = ok(host, 'ncp_db_plan', args)
    data = ok(host, 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']})
    assert data['validated'] and data['rollback_verified']
    assert capture_schema(c.runtime.dsn, fresh.value()['namespace'])['namespace_exists'] is False
    assert_scratch_empty(c.scratch)
    refused(c.host, 'ncp_db_plan', plan_args(c.host, 'schema', ['assets']), 'database_namespace_already_exists')


def test_exact_plan_reference_current_target_and_no_unbound_apply(configured):
    c = configured
    args = plan_args(c.host)
    plan = ok(c.host, 'ncp_db_plan', args)
    with patch.object(c.scratch.scratch, 'validate', side_effect=AssertionError('invalid plan reached scratch')):
        refused(c.host, 'ncp_db_dry_run', {**args, 'plan_ref': 'sha256:' + '0' * 64}, 'database_exact_plan_required')
        refused(c.host, 'ncp_db_dry_run', {**args, 'items': ['note'], 'plan_ref': plan['plan_ref']})
        refused(c.host, 'ncp_db_dry_run', {**args, 'schema_ref': 'sha256:' + '0' * 64, 'plan_ref': plan['plan_ref']})
    for name in ('ncp_db_apply', 'ncp_db_approve', 'ncp_dbops_sql'):
        refused(c.host, name)
    refused(c.make(scratch=None), 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']}, 'isolated_scratch_unavailable')
    assert_scratch_empty(c.scratch)


def test_model_sql_paths_identity_and_extra_fields_refuse_before_connect(configured):
    c = configured
    with patch.object(module, '_connection', side_effect=AssertionError('invalid request connected')):
        for key in ('dsn', 'sql', 'namespace', 'principal', 'tenant', 'path'):
            refused(c.host, 'ncp_dbops_health', {key: 'arbitrary'})
        refused(c.host, 'ncp_db_dry_run', {**plan_args(c.host), 'plan_ref': c.host.schema_ref, 'sql': 'DROP DATABASE postgres'})
        refused(c.host, 'ncp_dbops_backup_verify', {'backup_id': []})
    refused(c.host, 'ncp_db_plan', plan_args(c.host, items=['assets; DROP SCHEMA public']))
    refused(c.host, 'ncp_db_plan', plan_args(c.host, items=['by_label', 'by_label']))


def test_actual_schema_drift_and_expression_literal_redaction(configured):
    from psycopg import sql
    c = configured
    secret = 'sensitive-default-and-check-literal'
    with connect(c.runtime.direct_dsn) as db:
        db.execute(sql.SQL('ALTER TABLE {}.assets ADD COLUMN tag text DEFAULT {}').format(
            sql.Identifier(c.namespace), sql.Literal(secret)))
    drift = ok(c.host, 'ncp_dbops_drift', {'schema_ref': c.host.schema_ref})
    assert drift['drift'] and 'columns' in drift['changed_sections']
    snapshot = ok(c.host, 'ncp_dbops_schema')['snapshot']
    assert secret not in json.dumps(snapshot)
    assert next(row for row in snapshot['columns'] if row[1] == 'tag')[-1] is not None
    refused(c.host, 'ncp_db_plan', plan_args(c.host), 'database_schema_drift_requires_review')


def test_same_actual_operational_database_refused_before_ddl(configured):
    c = configured
    vocab = vocabulary('odsp_same_' + uuid4().hex[:12])
    baseline = capture_schema(c.scratch.dsn, vocab.value()['namespace'])
    host = c.make(dsn=c.scratch.dsn, vocabulary=vocab, baseline=baseline)
    args = plan_args(host, 'schema', ['assets'])
    plan = ok(host, 'ncp_db_plan', args)
    refused(host, 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']}, 'operational_database_is_not_scratch')
    assert_scratch_empty(c.scratch)


def test_scratch_refuses_tcp_nonprivate_socket_symlink_and_dirty_database(configured, tmp_path):
    c = configured
    with pytest.raises(DatabaseRefusal):
        ScratchDatabase('host=127.0.0.1 port=5432 dbname=odsp_scratch_test user=agent', root=c.runtime.root)
    with pytest.raises(DatabaseRefusal):
        ScratchDatabase(c.runtime.direct_dsn, root=c.runtime.root)
    link = tmp_path / 'alias'; link.symlink_to(c.runtime.root)
    with pytest.raises(DatabaseRefusal):
        ScratchDatabase(c.scratch.dsn, root=link)
    c.runtime.socket.chmod(0o755)
    try:
        with pytest.raises(DatabaseRefusal):
            ScratchDatabase(c.scratch.dsn, root=c.runtime.root)
    finally:
        c.runtime.socket.chmod(0o700)
    with connect(c.scratch.dsn) as db:
        db.execute('CREATE TABLE public.unreviewed(value integer)')
    try:
        with pytest.raises(DatabaseRefusal, match='not_empty'):
            ScratchDatabase(c.scratch.dsn, root=c.runtime.root)
    finally:
        with connect(c.scratch.dsn) as db:
            db.execute('DROP TABLE public.unreviewed')
    assert_scratch_empty(c.scratch)


def test_user_defined_scratch_function_or_type_is_not_empty(configured):
    c = configured
    commands = [
        ("CREATE FUNCTION public.unreviewed() RETURNS integer LANGUAGE SQL AS 'SELECT 1'", 'DROP FUNCTION public.unreviewed()'),
        ("CREATE TYPE public.unreviewed AS ENUM ('private')", 'DROP TYPE public.unreviewed'),
    ]
    for create, drop in commands:
        with connect(c.scratch.dsn) as db:
            db.execute(create)
        try:
            with pytest.raises(DatabaseRefusal, match='not_empty'):
                ScratchDatabase(c.scratch.dsn, root=c.runtime.root)
        finally:
            with connect(c.scratch.dsn) as db:
                db.execute(drop)
    assert_scratch_empty(c.scratch)


def test_pg_lookalike_user_namespace_is_not_ignored(configured):
    c = configured
    with connect(c.scratch.dsn) as db:
        db.execute('CREATE SCHEMA pgx_unreviewed')
        db.execute('CREATE TABLE pgx_unreviewed.user_rows(value integer)')
        db.execute('INSERT INTO pgx_unreviewed.user_rows VALUES (1)')
    try:
        with pytest.raises(DatabaseRefusal, match='not_empty'):
            ScratchDatabase(c.scratch.dsn, root=c.runtime.root)
    finally:
        with connect(c.scratch.dsn) as db:
            db.execute('DROP SCHEMA pgx_unreviewed CASCADE')
    assert_scratch_empty(c.scratch)


def test_scope_revocation_during_scratch_ddl_rolls_back(configured):
    c = configured
    args = plan_args(c.host)
    plan = ok(c.host, 'ncp_db_plan', args)
    original = module._schema
    def revoke_after_read(db, namespace):
        result = original(db, namespace)
        if db.info.dbname.startswith('odsp_scratch_'):
            c.authority['enabled'] = False
        return result
    with patch.object(module, '_schema', side_effect=revoke_after_read):
        refused(c.host, 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']}, 'database_current_authority_refused')
    assert_scratch_empty(c.scratch)
    assert capture_schema(c.runtime.dsn, c.namespace) == c.baseline


def test_actual_backend_error_rolls_back_and_does_not_replay_ddl(configured):
    c = configured
    original = module._schema
    calls = []
    def fail_after_ddl(db, namespace):
        if db.info.dbname.startswith('odsp_scratch_'):
            calls.append('scratch-read')
            db.execute('SELECT 1/0 /* private-database-error-literal */')
        return original(db, namespace)
    args = plan_args(c.host)
    plan = ok(c.host, 'ncp_db_plan', args)
    with patch.object(module, '_schema', side_effect=fail_after_ddl):
        result = refused(c.host, 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']}, 'database_storage_unavailable')
    assert calls == ['scratch-read']
    assert 'private-database-error-literal' not in json.dumps(result)
    assert_scratch_empty(c.scratch)


def test_unverified_cleanup_is_unknown_and_scratch_binding_stays_held(configured):
    from ciw.audit_chain import standard_result_outcome
    c = configured
    scratch = ScratchDatabase(c.scratch.dsn, root=c.runtime.root)
    host = c.make(scratch=scratch)
    original_schema, original_verify = module._schema, scratch._verify
    ddl_failed = [False]
    def fail_after_real_ddl(db, namespace):
        if db.info.dbname.startswith('odsp_scratch_'):
            ddl_failed[0] = True
            db.execute('SELECT 1/0')
        return original_schema(db, namespace)
    def unavailable_cleanup(db):
        if ddl_failed[0]:
            raise DatabaseRefusal('database_identity_unavailable')
        return original_verify(db)
    args = plan_args(host)
    plan = ok(host, 'ncp_db_plan', args)
    with patch.object(module, '_schema', side_effect=fail_after_real_ddl), patch.object(scratch, '_verify', side_effect=unavailable_cleanup):
        result = refused(host, 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']}, 'outcome_unknown')
    assert result['error']['reason'] == 'scratch_cleanup_unverified'
    assert standard_result_outcome(result) == {'phase': 'unknown', 'effect_state': 'unknown'}
    # Independent verification after recovery does not reset the binding's hold.
    assert_scratch_empty(c.scratch)
    with patch.object(scratch, '_verify', side_effect=AssertionError('held scratch attempted access')):
        refused(host, 'ncp_db_dry_run', {**args, 'plan_ref': plan['plan_ref']}, 'outcome_unknown')


def test_authority_revoked_during_real_inspection_withholds_snapshot(configured):
    c = configured
    original = module._schema
    def inspect_then_revoke(db, namespace):
        value = original(db, namespace)
        c.authority['operations'] = []
        return value
    with patch.object(module, '_schema', side_effect=inspect_then_revoke):
        refused(c.host, 'ncp_dbops_schema', code='database_current_authority_refused')


def test_post_io_scope_guard_refuses_revoked_source_and_changed_binding(configured):
    c = configured
    guard = c.host.request_scope_guard('ncp_dbops_schema', {})
    assert guard() is True
    ok(c.host, 'ncp_dbops_schema')
    c.authority['revision'] = 'revoked-after-audit'
    with pytest.raises(DatabaseRefusal, match='scope_changed'):
        guard()
    c.authority['revision'] = 'one'
    c.host._dsn += ' application_name=changed'
    with pytest.raises(DatabaseRefusal, match='scope_changed'):
        guard()


def test_read_only_transaction_enforced_real_timeout_and_no_prepare(configured):
    c = configured
    with module._connection(c.runtime.dsn) as db:
        for n in range(12):
            assert db.execute('SELECT %s::int', (n,)).fetchone() == (n,)
        assert db.execute('SELECT count(*) FROM pg_prepared_statements').fetchone() == (0,)
    with pytest.raises(DatabaseRefusal, match='database_storage_unavailable'):
        with module._connection(c.runtime.dsn) as db:
            db.execute(f'INSERT INTO "{c.namespace}".assets(id,label) VALUES (2,\'not-allowed\')')
    start = time.monotonic()
    with pytest.raises(DatabaseRefusal, match='database_storage_unavailable') as error:
        with module._connection(c.runtime.dsn) as db:
            db.execute('SELECT pg_sleep(5) /* secret-marker */')
    assert 0.8 <= time.monotonic() - start < 3
    assert error.value.__cause__ is None and error.value.__suppress_context__
    with connect(c.runtime.direct_dsn) as db:
        assert db.execute(f'SELECT count(*) FROM "{c.namespace}".assets').fetchone() == (1,)


def test_transaction_pool_session_settings_cannot_weaken_read_only_limits(configured):
    c = configured
    keys = ('statement_timeout', 'lock_timeout', 'idle_in_transaction_session_timeout',
            'synchronous_commit', 'search_path', 'default_transaction_isolation', 'default_transaction_read_only')
    poisoned = ('37s', '11s', '42s', 'off', 'public', 'serializable', 'off')
    with connect(c.runtime.dsn) as db:
        before = tuple(db.execute('SELECT current_setting(%s)', (key,)).fetchone()[0] for key in keys)
        pid = db.execute('SELECT pg_backend_pid()').fetchone()[0]
        for key, value in zip(keys, poisoned):
            db.execute('SELECT set_config(%s,%s,false)', (key, value))
    try:
        with module._connection(c.runtime.dsn) as db:
            assert db.execute('SELECT pg_backend_pid()').fetchone()[0] == pid
            values = tuple(db.execute('SELECT current_setting(%s)', (key,)).fetchone()[0] for key in (
                *keys[:5], 'transaction_isolation', 'transaction_read_only'))
            assert values == ('1s', '500ms', '2s', 'on', 'pg_catalog', 'repeatable read', 'on')
        with connect(c.runtime.dsn) as db:
            after = tuple(db.execute('SELECT current_setting(%s)', (key,)).fetchone()[0] for key in keys)
            assert after == poisoned
    finally:
        with connect(c.runtime.dsn) as db:
            for key, value in zip(keys, before):
                db.execute('SELECT set_config(%s,%s,false)', (key, value))


def test_missing_real_catalog_privilege_fails_closed_and_redacted(configured):
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    c = configured
    role = 'odsp_inspect_' + uuid4().hex[:12]
    with connect(c.runtime.direct_dsn) as db:
        db.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)))
        assert db.execute("SELECT has_function_privilege(%s,'pg_catalog.pg_control_system()','EXECUTE')", (role,)).fetchone() == (True,)
        # This freshly created private fixture exposes control identity by
        # default; explicitly remove that privilege to qualify the refusal.
        db.execute('REVOKE EXECUTE ON FUNCTION pg_catalog.pg_control_system() FROM PUBLIC')
    try:
        host = c.make(dsn=make_conninfo(c.runtime.direct_dsn, user=role))
        result = refused(host, 'ncp_dbops_health', code='database_inspection_privilege_unavailable')
        assert c.runtime.direct_dsn not in json.dumps(result) and role not in json.dumps(result)
    finally:
        with connect(c.runtime.direct_dsn) as db:
            db.execute('GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO PUBLIC')
            db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_real_pg_dump_bytes_verified_but_restore_not_claimed(configured, tmp_path):
    c = configured
    archive = tmp_path / 'fixture.dump'
    command = [str(c.runtime.pg_bin / 'pg_dump'), '--format=c', '--no-owner', '--no-acl',
               '--schema=' + c.namespace, '--file=' + str(archive), '--dbname=' + c.runtime.direct_dsn]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
    raw = archive.read_bytes()
    metadata = {'schema': 'ciw.database-backup.v1', 'format': 'postgres-custom', 'source_ref': SOURCE,
                'schema_ref': c.host.schema_ref, 'bytes_ref': ref(raw), 'bytes': len(raw),
                'captured_at': '2026-11-03T00:00:00Z'}
    artifact = BackupArtifact(raw, metadata, manifest_ref=ref(metadata))
    host = c.make(backups={'snapshot': artifact})
    checked = ok(host, 'ncp_dbops_backup_verify', {'backup_id': 'snapshot'})
    assert checked['bytes_verified'] and checked['archive_semantics_verified'] is False
    assert checked['restore_drill'] == 'not_performed'
    guard = host.request_scope_guard('ncp_dbops_backup_verify', {'backup_id': 'snapshot'})
    artifact.raw = raw[:-1] + bytes([raw[-1] ^ 1])
    with pytest.raises(DatabaseRefusal, match='backup_binding_changed'):
        guard()
    refused(host, 'ncp_dbops_backup_verify', {'backup_id': 'snapshot'}, 'backup_binding_changed')
    with pytest.raises(DatabaseRefusal, match='backup_bytes_mismatch'):
        BackupArtifact(artifact.raw, metadata, manifest_ref=ref(metadata))
    with pytest.raises(DatabaseRefusal, match='backup_manifest_mismatch'):
        BackupArtifact(raw, {**metadata, 'captured_at': 'changed'}, manifest_ref=ref(metadata))


def test_actual_active_query_advice_never_discloses_literals(configured):
    c = configured
    ready, failures = Event(), []
    state = {}
    def work():
        try:
            with connect(c.runtime.direct_dsn) as db:
                db.execute('SET compute_query_id=on')
                state['pid'] = db.execute('SELECT pg_backend_pid()').fetchone()[0]
                ready.set()
                db.execute("SELECT pg_sleep(4), 'private-active-query-literal'")
        except Exception as exc:
            failures.append(type(exc).__name__)
    child = Thread(target=work); child.start()
    try:
        assert ready.wait(3)
        deadline = time.monotonic() + 2
        query_id = None
        while time.monotonic() < deadline:
            with connect(c.runtime.direct_dsn) as db:
                row = db.execute("SELECT query_id::text,EXTRACT(EPOCH FROM clock_timestamp()-query_start) "
                    "FROM pg_stat_activity WHERE pid=%s AND state='active'", (state['pid'],)).fetchone()
            if row and row[0] is not None and row[1] >= 1:
                query_id = row[0]; break
            time.sleep(0.05)
        assert query_id is not None
        host = c.make(query_ids={'approved_query': query_id})
        data = ok(host, 'ncp_dbops_advice')
        assert data['active_queries'] and data['active_queries'][0]['query_id'] == 'approved_query'
        assert 'private-active-query-literal' not in json.dumps(data)
        assert 'query_text' not in data and 'SELECT' not in json.dumps(data)
        assert any(row['kind'] == 'review_active_query' for row in data['suggestions'])
    finally:
        child.join(6)
    assert not child.is_alive() and not failures


def test_actual_metadata_row_cap_refuses_partial_snapshot(configured):
    c = configured
    with connect(c.runtime.direct_dsn) as db:
        for n in range(16):
            db.execute(f'CREATE TABLE "{c.namespace}".extra_{n}(value integer)')
    refused(c.host, 'ncp_dbops_schema', code='database_metadata_row_limit')


def test_vocabulary_is_closed_detached_and_typed():
    raw = vocabulary('odsp_vocabulary').value()
    variants = []
    for key, value in [('namespace', 'public'), ('namespace', 'sql;DROP'), ('sql', 'SELECT 1')]:
        altered = deepcopy(raw); altered[key] = value; variants.append(altered)
    altered = deepcopy(raw); altered['tables']['assets']['columns']['id']['type'] = 'number'; variants.append(altered)
    altered = deepcopy(raw); altered['changes']['note']['column']['nullable'] = False; variants.append(altered)
    altered = deepcopy(raw); altered['indexes']['by_label']['columns'] = [['label']]; variants.append(altered)
    for value in variants:
        with pytest.raises(ValueError):
            DatabaseVocabulary(value)
    value = DatabaseVocabulary(raw)
    raw['namespace'] = 'altered'
    assert value.value()['namespace'] == 'odsp_vocabulary'
    value.raw = value.raw.replace(b'odsp_vocabulary', b'odsp_corrupted')
    with pytest.raises(DatabaseRefusal, match='changed'):
        value.statements('schema', ['assets'])


def test_tool_contract_has_no_apply_or_arbitrary_connection_inputs():
    manifest = module.profile_manifest()
    assert set(row['name'] for row in manifest['tools']) == set(module.TOOLS)
    assert len(manifest['tools']) == 7
    for row in manifest['tools']:
        assert row['inputSchema']['additionalProperties'] is False
        assert not {'sql', 'dsn', 'principal', 'tenant', 'namespace', 'path'} & row['inputSchema']['properties'].keys()
        assert row['annotations']['readOnlyHint'] is (row['name'] != 'ncp_db_dry_run')
    assert manifest['authorizes_state_admission'] is False
