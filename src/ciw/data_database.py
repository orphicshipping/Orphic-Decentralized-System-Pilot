"""Host-bound PostgreSQL inspection and rollback-only construction validation.

No operational DDL/DML, model-supplied SQL, credentials, approval or apply API.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from threading import RLock
import time

from .agent_tools import _shape
from .campaign_contracts import (ContractRefusal, Limits, REQUEST_LIMITS, canonical,
                                 closed_object, integer, mcp_envelope, parse, text)

PROFILE = 'ciw.data-database.v1'
_SQL_NAME = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,63}\Z')
_REF = re.compile(r'sha256:[0-9a-f]{64}\Z')
_TYPES = {'string': 'text', 'integer': 'bigint', 'number': 'double precision', 'boolean': 'boolean'}
_S = {'type': 'string', 'minLength': 1, 'maxLength': 64}
_R = {'type': 'string', 'minLength': 71, 'maxLength': 71}
_ROWS = 512
_SNAPSHOT_LIMITS = Limits(max_bytes=131072, max_array=1024)
_AUTHORITY = {'authorizes_execution': False, 'authorizes_state_admission': False,
              'human_approval_required_for_operational_writes': True,
              'operational_writes_performed': False, 'source_license_verified': False,
              'nsl_admission': 'not_established', 'tbrt_evidence': 'not_bound'}


class DatabaseRefusal(ContractRefusal):
    """Fixed diagnostic; driver SQL, DSNs and exception text are never disclosed."""


def _ref(value):
    return 'sha256:' + sha256(value if type(value) is bytes else canonical(value)).hexdigest()


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise DatabaseRefusal('database_identifier_refused')
    return value


def _reference(value):
    if type(value) is not str or not _REF.fullmatch(value):
        raise DatabaseRefusal('database_reference_refused')
    return value


def _sql_name(value):
    if type(value) is not str or not _SQL_NAME.fullmatch(value):
        raise DatabaseRefusal('host_sql_identifier_refused')
    return value


def _quote(value):
    return '"' + _sql_name(value) + '"'


def _spec(description, properties=None, required=(), *, read=True):
    return {'description': description,
            'inputSchema': {'type': 'object', 'properties': properties or {},
                            'required': list(required), 'additionalProperties': False},
            'outputSchema': {'type': 'object'},
            'annotations': {'readOnlyHint': read, 'destructiveHint': False,
                            'idempotentHint': read, 'openWorldHint': True}}


_PLAN_ARGS = {'kind': {'type': 'string', 'enum': ['schema', 'indexes', 'add_columns']},
              'items': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': _S},
              'vocabulary_ref': _R, 'schema_ref': _R}
TOOLS = {
    'ncp_db_plan': _spec('Propose exact SQL from host-reviewed templates. No operational write or approval.',
                         _PLAN_ARGS, tuple(_PLAN_ARGS)),
    'ncp_db_dry_run': _spec('Validate an exact proposal on an isolated private local scratch database; always roll back. No operational data validation or apply.',
                            {**_PLAN_ARGS, 'plan_ref': _R}, (*_PLAN_ARGS, 'plan_ref'), read=False),
    'ncp_dbops_health': _spec('Read bounded PostgreSQL identity and settings; never modify configuration.'),
    'ncp_dbops_schema': _spec('Read bounded structural metadata from the host-bound namespace; expression contents are hashed.'),
    'ncp_dbops_drift': _spec('Compare current structural metadata with the exact host-reviewed snapshot.',
                            {'schema_ref': _R}, ('schema_ref',)),
    'ncp_dbops_advice': _spec('Read index/table statistics and explicitly bound active-query IDs. Return review suggestions, never SQL text, literals or apply actions.'),
    'ncp_dbops_backup_verify': _spec('Check exact host-supplied immutable backup bytes against pinned metadata. Does not claim restore success.',
                                    {'backup_id': _S}, ('backup_id',)),
}


def _binding(dsn):
    if type(dsn) is not str or not 1 <= len(dsn) <= 8192 or '\x00' in dsn:
        raise DatabaseRefusal('host_database_binding_required')


@contextmanager
def _connection(dsn, *, read_only=True):
    """Same portable transaction pattern as the existing PostgreSQL adapters."""
    _binding(dsn)
    try:
        import psycopg
    except ImportError:
        raise DatabaseRefusal('postgres_driver_unavailable') from None
    try:
        with psycopg.connect(dsn, connect_timeout=3, prepare_threshold=None,
                             application_name='odsp-data-database-v1') as db:
            with db.transaction():
                db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY' if read_only
                           else 'SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE')
                db.execute('SET LOCAL statement_timeout=1000; SET LOCAL lock_timeout=500; '
                           'SET LOCAL idle_in_transaction_session_timeout=2000; '
                           'SET LOCAL synchronous_commit=on; SET LOCAL search_path=pg_catalog', prepare=False)
                expected = ('1s', '500ms', '2s', 'on', 'pg_catalog',
                            'repeatable read' if read_only else 'read committed', 'on' if read_only else 'off')
                actual = db.execute("SELECT current_setting('statement_timeout'),current_setting('lock_timeout'),"
                                    "current_setting('idle_in_transaction_session_timeout'),current_setting('synchronous_commit'),"
                                    "current_setting('search_path'),current_setting('transaction_isolation'),"
                                    "current_setting('transaction_read_only')").fetchone()
                if actual != expected:
                    raise DatabaseRefusal('database_transaction_settings_refused')
                yield db
    except psycopg.errors.InsufficientPrivilege:
        raise DatabaseRefusal('database_inspection_privilege_unavailable') from None
    except psycopg.Error:
        raise DatabaseRefusal('database_storage_unavailable') from None


def _rows(db, query, params=(), maximum=_ROWS):
    rows = db.execute(query, params).fetchmany(maximum + 1)
    if len(rows) > maximum:
        raise DatabaseRefusal('database_metadata_row_limit')
    return [list(row) for row in rows]


def _identity(db):
    row = db.execute('SELECT s.system_identifier::text,d.oid::text,d.datname '
                     'FROM pg_control_system() s CROSS JOIN pg_database d '
                     'WHERE d.datname=current_database()').fetchone()
    if row is None or db.info.server_version < 140000:
        raise DatabaseRefusal('database_identity_unavailable')
    return {'system': row[0], 'database_oid': row[1], 'database_name': row[2]}


def _schema(db, namespace):
    """No table rows or raw default/check/index expressions are selected."""
    tables = _rows(db, 'SELECT c.relname,c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity '
        'FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
        "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname LIMIT 17", (namespace,), 16)
    columns = _rows(db, 'SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,'
        'a.attnum,a.attidentity,a.attgenerated,CASE WHEN d.oid IS NULL THEN NULL ELSE '
        "encode(sha256(convert_to(pg_get_expr(d.adbin,d.adrelid),'UTF8')),'hex') END "
        'FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
        'JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum '
        "WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped "
        'ORDER BY c.relname,a.attnum LIMIT 513', (namespace,))
    constraints = _rows(db, 'SELECT c.relname,k.conname,k.contype,k.convalidated,'
        "encode(sha256(convert_to(pg_get_constraintdef(k.oid,true),'UTF8')),'hex') "
        'FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace '
        'WHERE n.nspname=%s ORDER BY c.relname,k.conname LIMIT 129', (namespace,), 128)
    indexes = _rows(db, 'SELECT c.relname,x.relname,i.indisunique,i.indisvalid,i.indisprimary,m.amname,'
        'i.indexprs IS NOT NULL,i.indpred IS NOT NULL,'
        'ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,ord) '
        'LEFT JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum ORDER BY k.ord),'
        "encode(sha256(convert_to(pg_get_indexdef(i.indexrelid),'UTF8')),'hex') "
        'FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_class x ON x.oid=i.indexrelid '
        'JOIN pg_am m ON m.oid=x.relam JOIN pg_namespace n ON n.oid=c.relnamespace '
        'WHERE n.nspname=%s ORDER BY c.relname,x.relname LIMIT 65', (namespace,), 64)
    exists = db.execute('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)', (namespace,)).fetchone()[0]
    result = {'schema': 'ciw.database-schema.v1', 'namespace': namespace, 'namespace_exists': exists,
              'tables': tables, 'columns': columns, 'constraints': constraints, 'indexes': indexes}
    canonical(result, limits=_SNAPSHOT_LIMITS)
    return result


def capture_schema(dsn, namespace):
    """Trusted launcher read for review/pinning; not a model-callable constructor."""
    _sql_name(namespace)
    with _connection(dsn) as db:
        return _schema(db, namespace)


class DatabaseVocabulary:
    """Detached closed host templates; physical identifiers are never request inputs."""
    def __init__(self, value):
        value = parse(canonical(value, limits=REQUEST_LIMITS))
        closed_object(value, {'namespace', 'tables', 'indexes', 'changes'})
        _sql_name(value['namespace'])
        if value['namespace'] in ('public', 'information_schema') or value['namespace'].startswith('pg_'):
            raise DatabaseRefusal('private_reviewed_namespace_required')
        tables = value['tables']
        if type(tables) is not dict or not 1 <= len(tables) <= 16:
            raise DatabaseRefusal('database_vocabulary_limit')
        names = set()
        for name, table in tables.items():
            _id(name); closed_object(table, {'name', 'columns', 'primary_key'})
            physical = _sql_name(table['name'])
            if physical in names:
                raise DatabaseRefusal('duplicate_database_identifier')
            names.add(physical)
            if type(table['columns']) is not dict or not 1 <= len(table['columns']) <= 32:
                raise DatabaseRefusal('database_vocabulary_limit')
            colnames = set()
            for column_id, column in table['columns'].items():
                _id(column_id); self._column(column)
                if column['name'] in colnames:
                    raise DatabaseRefusal('duplicate_database_identifier')
                colnames.add(column['name'])
            keys = table['primary_key']
            if (type(keys) is not list or not 1 <= len(keys) <= 4 or any(type(k) is not str for k in keys)
                    or len(keys) != len(set(keys)) or any(k not in table['columns'] or
                        table['columns'][k]['nullable'] or table['columns'][k]['type'] not in ('string', 'integer') for k in keys)):
                raise DatabaseRefusal('invalid_database_primary_key')
        for kind in ('indexes', 'changes'):
            if type(value[kind]) is not dict or len(value[kind]) > 32:
                raise DatabaseRefusal('database_vocabulary_limit')
        for name, index in value['indexes'].items():
            _id(name); closed_object(index, {'name', 'table', 'columns', 'unique'})
            _sql_name(index['name'])
            if type(index['table']) is not str or index['table'] not in tables or type(index['unique']) is not bool or index['name'] in names:
                raise DatabaseRefusal('invalid_database_index')
            names.add(index['name'])
            keys = index['columns']
            if (type(keys) is not list or not 1 <= len(keys) <= 4 or any(type(k) is not str for k in keys) or len(keys) != len(set(keys))
                    or any(k not in tables[index['table']]['columns'] for k in keys)):
                raise DatabaseRefusal('invalid_database_index')
        changes = set()
        for name, change in value['changes'].items():
            _id(name); closed_object(change, {'table', 'column'})
            if type(change['table']) is not str or change['table'] not in tables:
                raise DatabaseRefusal('invalid_database_change')
            self._column(change['column'])
            key = (change['table'], change['column']['name'])
            if (key in changes or not change['column']['nullable'] or
                    change['column']['name'] in {v['name'] for v in tables[change['table']]['columns'].values()}):
                raise DatabaseRefusal('invalid_database_change')
            changes.add(key)
        self.raw = canonical(value, limits=REQUEST_LIMITS)
        self.ref = _ref(self.raw)

    @staticmethod
    def _column(value):
        closed_object(value, {'name', 'type', 'nullable'})
        _sql_name(value['name'])
        if type(value['type']) is not str or value['type'] not in _TYPES or type(value['nullable']) is not bool:
            raise DatabaseRefusal('invalid_database_column')

    def value(self):
        if _ref(self.raw) != self.ref:
            raise DatabaseRefusal('database_vocabulary_changed')
        return parse(self.raw)

    def statements(self, kind, items):
        value = self.value()
        category = {'schema': 'tables', 'indexes': 'indexes', 'add_columns': 'changes'}.get(kind)
        if (category is None or type(items) is not list or not 1 <= len(items) <= 16
                or any(type(i) is not str or i not in value[category] for i in items)
                or len(set(items)) != len(items)):
            raise DatabaseRefusal('database_template_refused')
        namespace = _quote(value['namespace'])
        result = ['CREATE SCHEMA ' + namespace] if kind == 'schema' else []
        for name in sorted(items):
            item = value[category][name]
            if kind == 'schema':
                columns = [f'{_quote(c["name"])} {_TYPES[c["type"]]}' + ('' if c['nullable'] else ' NOT NULL')
                           for _, c in sorted(item['columns'].items())]
                columns.append('PRIMARY KEY (' + ', '.join(_quote(item['columns'][k]['name']) for k in item['primary_key']) + ')')
                result.append('CREATE TABLE ' + namespace + '.' + _quote(item['name']) + ' (' + ', '.join(columns) + ')')
            elif kind == 'indexes':
                table = value['tables'][item['table']]
                result.append('CREATE ' + ('UNIQUE ' if item['unique'] else '') + 'INDEX ' + _quote(item['name']) +
                    ' ON ' + namespace + '.' + _quote(table['name']) + ' USING btree (' +
                    ', '.join(_quote(table['columns'][c]['name']) for c in item['columns']) + ')')
            else:
                column = item['column']
                result.append('ALTER TABLE ' + namespace + '.' + _quote(value['tables'][item['table']]['name']) +
                              ' ADD COLUMN ' + _quote(column['name']) + ' ' + _TYPES[column['type']])
        return result


def _private_dir(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise DatabaseRefusal('private_scratch_path_required')
    details = path.stat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o700:
        raise DatabaseRefusal('private_scratch_path_required')
    return details.st_dev, details.st_ino


class ScratchDatabase:
    """Bind an existing empty local DB. This class never provisions a database."""
    def __init__(self, dsn, *, root):
        from psycopg.conninfo import conninfo_to_dict
        _binding(dsn)
        try:
            fields = conninfo_to_dict(dsn)
        except Exception:
            raise DatabaseRefusal('local_scratch_binding_required') from None
        if (set(fields) - {'host', 'port', 'dbname', 'user'} or
                not {'host', 'port', 'dbname', 'user'} <= set(fields) or
                not re.fullmatch(r'odsp_scratch_[a-z0-9_]{1,40}', fields['dbname'])):
            raise DatabaseRefusal('local_scratch_binding_required')
        self.root, self.socket = Path(root), Path(fields['host'])
        self._root_identity, self._socket_identity = _private_dir(self.root), _private_dir(self.socket)
        if not self.socket.is_relative_to(self.root):
            raise DatabaseRefusal('local_scratch_binding_required')
        self._dsn, self._held, self._lock = dsn, False, RLock()
        with _connection(dsn) as db:
            self._identity = self._verify(db)
            self._empty(db)

    def _verify(self, db):
        if (_private_dir(self.root) != self._root_identity or
                _private_dir(self.socket) != self._socket_identity):
            raise DatabaseRefusal('scratch_directory_changed')
        row = db.execute("SELECT current_setting('data_directory'),current_setting('listen_addresses'),"
                         'inet_server_addr(),current_database()').fetchone()
        data = Path(row[0])
        if not data.is_relative_to(self.root) or data.resolve() != data or row[1] != '' or row[2] is not None:
            raise DatabaseRefusal('local_scratch_identity_refused')
        _private_dir(data)
        identity = _identity(db)
        if not re.fullmatch(r'odsp_scratch_[a-z0-9_]{1,40}', identity['database_name']):
            raise DatabaseRefusal('local_scratch_identity_refused')
        if hasattr(self, '_identity') and identity != self._identity:
            raise DatabaseRefusal('scratch_database_changed')
        return identity

    @staticmethod
    def _empty(db):
        checks = (
            "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND left(nspname,3)<>'pg_'",
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('information_schema') AND left(n.nspname,3)<>'pg_'",
            "SELECT count(*) FROM pg_extension WHERE extname<>'plpgsql'",
            'SELECT count(*) FROM pg_event_trigger',
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('information_schema') AND left(n.nspname,3)<>'pg_'",
            "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname NOT IN ('information_schema') AND left(n.nspname,3)<>'pg_'",
            'SELECT count(*) FROM pg_foreign_server',
            'SELECT count(*) FROM pg_publication',
            'SELECT count(*) FROM pg_subscription',
        )
        if any(db.execute(query).fetchone() != (0,) for query in checks):
            raise DatabaseRefusal('scratch_database_not_empty')

    def validate(self, vocabulary, kind, items, operational_identity, guard):
        with self._lock:
            if self._held:
                raise DatabaseRefusal('scratch_cleanup_unverified')
            started, ddl_started = time.monotonic(), False
            try:
                with _connection(self._dsn, read_only=False) as db:
                    identity = self._verify(db)
                    if identity == operational_identity:
                        raise DatabaseRefusal('operational_database_is_not_scratch')
                    self._empty(db)
                    guard()
                    statements = vocabulary.statements(kind, items)
                    baseline = [] if kind == 'schema' else vocabulary.statements('schema', sorted(vocabulary.value()['tables']))
                    with db.transaction(force_rollback=True):
                        for query in baseline + statements:
                            if time.monotonic() - started > 8:
                                raise DatabaseRefusal('scratch_deadline')
                            guard()
                            ddl_started = True
                            db.execute(query, prepare=False)
                        observed = _schema(db, vocabulary.value()['namespace'])
                        guard()
                    self._empty(db)
                # Independently read after rollback/connection release, never assume cleanup.
                with _connection(self._dsn) as check:
                    self._verify(check); self._empty(check)
                guard()
                return {'validated': True, 'rollback_verified': True, 'scratch_database_ref': _ref(identity),
                        'result_schema_ref': _ref(observed), 'result_schema': observed,
                        'validation_scope': 'empty_synthetic_baseline_ddl_only',
                        'operational_data_compatibility': 'not_established'}
            except Exception:
                if ddl_started:
                    try:
                        with _connection(self._dsn) as check:
                            self._verify(check); self._empty(check)
                    except Exception:
                        self._held = True
                        raise DatabaseRefusal('scratch_cleanup_unverified') from None
                raise


class BackupArtifact:
    """Host-supplied immutable bytes plus an independently pinned manifest hash."""
    def __init__(self, raw, metadata, *, manifest_ref):
        if type(raw) is not bytes or not 5 <= len(raw) <= 8 * 1024 * 1024:
            raise DatabaseRefusal('backup_byte_limit')
        metadata = parse(canonical(metadata, limits=REQUEST_LIMITS))
        closed_object(metadata, {'schema', 'format', 'source_ref', 'schema_ref', 'bytes_ref', 'bytes', 'captured_at'})
        if metadata['schema'] != 'ciw.database-backup.v1' or metadata['format'] != 'postgres-custom':
            raise DatabaseRefusal('backup_format_refused')
        for key in ('source_ref', 'schema_ref', 'bytes_ref'):
            _reference(metadata[key])
        integer(metadata['bytes'], 5, 8 * 1024 * 1024); text(metadata['captured_at'], 64)
        if _ref(metadata) != _reference(manifest_ref):
            raise DatabaseRefusal('backup_manifest_mismatch')
        if not raw.startswith(b'PGDMP') or len(raw) != metadata['bytes'] or _ref(raw) != metadata['bytes_ref']:
            raise DatabaseRefusal('backup_bytes_mismatch')
        self.raw, self.metadata, self.manifest_ref = raw, canonical(metadata), manifest_ref

    def verify(self, source_ref, schema_ref):
        metadata = parse(self.metadata)
        if (_ref(metadata) != self.manifest_ref or _ref(self.raw) != metadata['bytes_ref']
                or len(self.raw) != metadata['bytes'] or not self.raw.startswith(b'PGDMP')
                or metadata['source_ref'] != source_ref or metadata['schema_ref'] != schema_ref):
            raise DatabaseRefusal('backup_binding_changed')
        return {'manifest_ref': self.manifest_ref, 'metadata': metadata, 'bytes_verified': True,
                'verification_scope': 'exact_host_pinned_manifest_bytes_and_custom_format_magic',
                'archive_semantics_verified': False, 'restore_drill': 'not_performed'}


class DataDatabaseHost:
    """All authority, database/vocabulary selection and backups are launcher-only."""
    def __init__(self, *, dsn, vocabulary, baseline, principal, tenant, source_ref,
                 read_authority, scratch=None, backups=None, query_ids=None):
        from .principal_policy import owner_for
        _binding(dsn); owner_for(principal); _id(tenant); _reference(source_ref)
        if type(vocabulary) is not DatabaseVocabulary or not callable(read_authority):
            raise DatabaseRefusal('host_database_bindings_required')
        baseline = parse(canonical(baseline, limits=_SNAPSHOT_LIMITS), limits=_SNAPSHOT_LIMITS)
        closed_object(baseline, {'schema', 'namespace', 'namespace_exists', 'tables', 'columns', 'constraints', 'indexes'})
        if (baseline['schema'] != 'ciw.database-schema.v1' or baseline['namespace'] != vocabulary.value()['namespace']
                or type(baseline['namespace_exists']) is not bool):
            raise DatabaseRefusal('database_baseline_mismatch')
        if scratch is not None and type(scratch) is not ScratchDatabase:
            raise DatabaseRefusal('local_scratch_binding_required')
        self._backups = dict(backups or {})
        if len(self._backups) > 8 or any(type(k) is not str or not _ID.fullmatch(k) or type(v) is not BackupArtifact for k, v in self._backups.items()):
            raise DatabaseRefusal('backup_binding_refused')
        self._queries = dict(query_ids or {})
        if len(self._queries) > 32:
            raise DatabaseRefusal('query_binding_limit')
        for key, value in self._queries.items():
            _id(key)
            if type(value) is not str or not re.fullmatch(r'-?[0-9]{1,19}', value) or not -(2**63) <= int(value) < 2**63:
                raise DatabaseRefusal('query_binding_refused')
        if len(set(self._queries.values())) != len(self._queries):
            raise DatabaseRefusal('query_binding_refused')
        self._dsn, self.vocabulary, self.baseline = dsn, vocabulary, canonical(baseline, limits=_SNAPSHOT_LIMITS)
        self._bound_principal, self._bound_tenant, self.source_ref = principal, tenant, source_ref
        self.schema_ref, self._read_authority, self.scratch = _ref(self.baseline), read_authority, scratch
        self.code_ref = _ref(Path(__file__).read_bytes())
        self.interface_ref = _ref(TOOLS)
        self._binding_ref = self._binding_identity()

    def _binding_identity(self):
        return _ref([self._dsn, self.vocabulary.ref, _ref(self.vocabulary.raw), _ref(self.baseline),
                     self._bound_principal, self._bound_tenant, self.source_ref, self._queries,
                     sorted([k, v.manifest_ref] for k, v in self._backups.items()),
                     None if self.scratch is None else _ref(self.scratch._identity)])

    def _authority(self, name):
        try:
            value = parse(canonical(self._read_authority(), limits=REQUEST_LIMITS))
            closed_object(value, {'principal', 'tenant', 'source_ref', 'revision', 'enabled', 'operations'})
            if (value['principal'] != self._bound_principal or value['tenant'] != self._bound_tenant
                    or value['source_ref'] != self.source_ref or value['enabled'] is not True
                    or type(value['operations']) is not list or len(value['operations']) > len(TOOLS)
                    or any(type(n) is not str or n not in TOOLS for n in value['operations'])
                    or len(set(value['operations'])) != len(value['operations']) or name not in value['operations']):
                raise ValueError()
            text(value['revision'], 160)
        except Exception:
            raise DatabaseRefusal('database_current_authority_refused') from None
        return _ref(value)

    def descriptions(self):
        return [{'name': name, **deepcopy(spec)} for name, spec in TOOLS.items()]

    def request_scope_guard(self, name, arguments):
        if type(name) is not str or name not in TOOLS:
            raise DatabaseRefusal('unknown_database_operation')
        arguments = parse(canonical(arguments, limits=REQUEST_LIMITS))
        _shape(arguments, TOOLS[name]['inputSchema'])
        authority = self._authority(name)
        reader, scratch = self._read_authority, self.scratch

        def check():
            if (self._read_authority is not reader or self.scratch is not scratch or
                    self._binding_identity() != self._binding_ref or _ref(Path(__file__).read_bytes()) != self.code_ref
                    or _ref(TOOLS) != self.interface_ref
                    or self._authority(name) != authority):
                raise DatabaseRefusal('database_request_scope_changed')
            if name == 'ncp_dbops_backup_verify':
                artifact = self._backups.get(arguments['backup_id'])
                if artifact is None:
                    raise DatabaseRefusal('backup_unavailable')
                artifact.verify(self.source_ref, self.schema_ref)
            return True
        check()
        return check

    def _inspection(self, guard, *, advice=False):
        started = time.monotonic()
        namespace = self.vocabulary.value()['namespace']
        with _connection(self._dsn) as db:
            guard()
            identity = _identity(db)
            snapshot = _schema(db, namespace)
            health = {'database_ref': _ref(identity), 'server_version': db.info.server_version,
                      'in_recovery': db.execute('SELECT pg_is_in_recovery()').fetchone()[0],
                      'inspection_transaction_read_only': True}
            extra = {}
            if advice:
                stats = _rows(db, 'SELECT relname,seq_scan,idx_scan,n_live_tup,n_dead_tup '
                    'FROM pg_stat_user_tables WHERE schemaname=%s ORDER BY relname LIMIT 17', (namespace,), 16)
                active = []
                if self._queries:
                    active = _rows(db, 'SELECT query_id::text,'
                        'EXTRACT(EPOCH FROM clock_timestamp()-query_start)::double precision,COALESCE(wait_event_type,\'none\') '
                        'FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) '
                        'AND usesysid=(SELECT oid FROM pg_roles WHERE rolname=current_user) '
                        'AND query_id=ANY(%s::bigint[]) AND pid<>pg_backend_pid() AND state=\'active\' '
                        'ORDER BY query_start LIMIT 33', (list(self._queries.values()),), 32)
                extra = {'table_statistics': stats, 'active_queries': [
                    {'query_id': key, 'elapsed_seconds': row[1], 'wait_kind': row[2]}
                    for row in active for key, value in self._queries.items() if row[0] == value],
                    'query_statistics': 'current_role_host_bound_ids_only' if self._queries else 'host_query_ids_unbound',
                    'query_text_disclosed': False, 'statistics_are_estimates': True}
            if time.monotonic() - started > 8:
                raise DatabaseRefusal('database_inspection_deadline')
            guard()
        guard()
        return identity, snapshot, health, extra

    def _plan(self, arguments, snapshot, identity):
        if arguments['vocabulary_ref'] != self.vocabulary.ref or arguments['schema_ref'] != self.schema_ref:
            raise DatabaseRefusal('database_plan_reference_mismatch')
        if _ref(snapshot) != self.schema_ref:
            raise DatabaseRefusal('database_schema_drift_requires_review')
        if arguments['kind'] == 'schema' and snapshot['namespace_exists']:
            raise DatabaseRefusal('database_namespace_already_exists')
        statements = self.vocabulary.statements(arguments['kind'], arguments['items'])
        body = {'schema': 'ciw.database-plan.v1', 'source_ref': self.source_ref, 'schema_ref': self.schema_ref,
                'database_ref': _ref(identity),
                'vocabulary_ref': self.vocabulary.ref, 'code_ref': self.code_ref, 'interface_ref': self.interface_ref,
                'kind': arguments['kind'], 'items': sorted(arguments['items']), 'statements': statements,
                'approval_scope': 'exact_plan_ref_target_schema_and_sql_requires_human_review',
                'apply_available': False, 'operational_data_compatibility': 'not_established', **_AUTHORITY}
        return {**body, 'plan_ref': _ref(body)}

    def call(self, name, arguments):
        try:
            arguments = parse(canonical(arguments, limits=REQUEST_LIMITS))
            guard = self.request_scope_guard(name, arguments)
            if name == 'ncp_dbops_backup_verify':
                artifact = self._backups.get(arguments['backup_id'])
                if artifact is None:
                    raise DatabaseRefusal('backup_unavailable')
                data = artifact.verify(self.source_ref, self.schema_ref)
            else:
                identity, snapshot, health, extra = self._inspection(guard, advice=name == 'ncp_dbops_advice')
                current_ref = _ref(snapshot)
                if name == 'ncp_dbops_health':
                    data = health
                elif name == 'ncp_dbops_schema':
                    data = {'snapshot': snapshot, 'schema_ref': current_ref}
                elif name == 'ncp_dbops_drift':
                    if arguments['schema_ref'] != self.schema_ref:
                        raise DatabaseRefusal('database_baseline_reference_mismatch')
                    baseline = parse(self.baseline, limits=_SNAPSHOT_LIMITS)
                    changed = [key for key in ('namespace_exists', 'tables', 'columns', 'constraints', 'indexes') if baseline[key] != snapshot[key]]
                    data = {'baseline_ref': self.schema_ref, 'current_ref': current_ref,
                            'drift': bool(changed), 'changed_sections': changed,
                            'comparison_scope': 'bounded_structural_metadata_not_data_or_function_bodies'}
                elif name == 'ncp_dbops_advice':
                    suggestions = []
                    present = {row[1] for row in snapshot['indexes']}
                    for key, index in self.vocabulary.value()['indexes'].items():
                        if index['name'] not in present:
                            suggestions.append({'kind': 'review_index_candidate', 'index_id': key,
                                                'reason': 'host_reviewed_template_absent', 'benefit_established': False})
                    for row in snapshot['indexes']:
                        if not row[3]:
                            suggestions.append({'kind': 'review_invalid_index', 'index': row[1]})
                    for row in extra['active_queries']:
                        if row['elapsed_seconds'] >= 1:
                            suggestions.append({'kind': 'review_active_query', 'query_id': row['query_id'],
                                                'reason': 'observed_active_over_one_second'})
                    data = {'schema_ref': current_ref, **extra, 'suggestions': suggestions,
                            'index_removal_or_configuration_change': 'not_performed'}
                else:
                    data = self._plan(arguments, snapshot, identity)
                    if name == 'ncp_db_dry_run':
                        if arguments['plan_ref'] != data['plan_ref']:
                            raise DatabaseRefusal('database_exact_plan_required')
                        if self.scratch is None:
                            raise DatabaseRefusal('isolated_scratch_unavailable')
                        data = {'plan': data, **self.scratch.validate(self.vocabulary, arguments['kind'],
                                                                    arguments['items'], identity, guard)}
            guard()
            body = {'profile': PROFILE, 'source_ref': self.source_ref, 'code_ref': self.code_ref,
                    'interface_ref': self.interface_ref,
                    'vocabulary_ref': self.vocabulary.ref, 'baseline_ref': self.schema_ref,
                    'input_ref': _ref({'name': name, 'arguments': arguments}), 'data': data, **_AUTHORITY}
            result = {'schema': 'ncp.control-response.v1', 'status': 'ok',
                      'data': {**body, 'receipt_ref': _ref(body)}, 'error': None}
            mcp_envelope(result)
            guard()
            return result
        except DatabaseRefusal as exc:
            if str(exc) == 'scratch_cleanup_unverified':
                return {'schema': 'ncp.control-response.v1', 'status': 'refused', 'data': None,
                        'error': {'code': 'outcome_unknown', 'reason': 'scratch_cleanup_unverified'},
                        'effect_state': 'unknown', 'authorizes_execution': False}
            return {'schema': 'ncp.control-response.v1', 'status': 'refused', 'data': None,
                    'error': {'code': str(exc)}, 'authorizes_execution': False}
        except (ContractRefusal, ValueError, TypeError, KeyError, OSError):
            return {'schema': 'ncp.control-response.v1', 'status': 'refused', 'data': None,
                    'error': {'code': 'invalid_database_request'}, 'authorizes_execution': False}

    @staticmethod
    def mcp_result(name, value):
        return mcp_envelope(value, is_error=value.get('status') != 'ok')


def profile_manifest():
    return {'profile_id': PROFILE, 'host_factory': 'ciw.data_database.DataDatabaseHost',
            'qualification': 'host_bound_read_only_postgres_and_isolated_local_rollback',
            'tools': [{'name': name, **deepcopy(spec)} for name, spec in TOOLS.items()], **_AUTHORITY}
