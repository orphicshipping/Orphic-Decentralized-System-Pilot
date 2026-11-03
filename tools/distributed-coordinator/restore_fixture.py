"""Native checkpoint/restore drill for owned local synthetic qualification services.

This is test infrastructure, not a deployment or disaster-recovery API. The
caller owns and quiesces every worker. Cutover stops the source services before
starting an empty destination; it cannot fence a surviving remote writer.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from uuid import UUID

MAX_BACKUP = 16 * 1024 * 1024
TABLES = {
    'format': 'SELECT * FROM odsp_distributed_v1.format ORDER BY singleton',
    'queues': 'SELECT * FROM odsp_distributed_v1.queues ORDER BY queue_id',
    'jobs': 'SELECT * FROM odsp_distributed_v1.jobs ORDER BY queue_id, job_id',
    'events': 'SELECT * FROM odsp_distributed_v1.events ORDER BY queue_id, job_id, ordinal',
    'outbox': 'SELECT * FROM odsp_distributed_v1.outbox ORDER BY queue_id, job_id',
    'effects': 'SELECT * FROM public.qualification_effects ORDER BY job_id',
}


class RestoreHeld(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise RestoreHeld(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False)


def normal(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'bytea': bytes(value).hex()}
    if isinstance(value, datetime):
        return {'timestamp': value.astimezone(timezone.utc).isoformat()}
    if isinstance(value, UUID):
        return {'uuid': str(value)}
    if type(value) is dict:
        return {k: normal(v) for k, v in value.items()}
    if type(value) in (tuple, list):
        return [normal(v) for v in value]
    return value


def snapshot_connection(db):
    return canonical({name: normal(db.execute(query).fetchall())
                      for name, query in TABLES.items()})


def snapshot(runtime):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(runtime.direct_dsn, connect_timeout=3,
                         row_factory=dict_row) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        return snapshot_connection(db)


def check_runtime(runtime, *, running):
    from psycopg.conninfo import conninfo_to_dict
    root = runtime.root
    require(root.is_absolute() and root.resolve() == root and root.is_dir()
            and root.stat().st_uid == os.getuid() and not root.stat().st_mode & 0o077,
            'owned_private_local_fixture_required')
    require(runtime.data == root / 'postgres-data' and runtime.socket == root / 'socket',
            'local_fixture_paths_changed')
    info = conninfo_to_dict(runtime.direct_dsn)
    require(info['host'] == str(runtime.socket) and info['port'] == str(runtime.pg_port)
            and info['dbname'] == 'postgres' and not info.get('password'),
            'private_unix_socket_fixture_required')
    if running:
        process = runtime.pg
        require(process is not None and process.poll() is None
                and str(runtime.data) in process.args and 'listen_addresses=' in process.args,
                'owned_running_fixture_required')
    else:
        require(runtime.pg is None and runtime.nats is None and runtime.pooler is None
                and not runtime.data.exists(), 'empty_unstarted_destination_required')


@dataclass(frozen=True)
class Checkpoint:
    source_root: str
    archive: Path
    archive_sha256: str
    snapshot_json: str
    snapshot_sha256: str
    captured_at: str


def capture(source, archive):
    """Use one exported PostgreSQL snapshot for custody and a native custom dump."""
    import psycopg
    from psycopg.rows import dict_row
    check_runtime(source, running=True)
    require(archive.parent.resolve() == source.root and not archive.exists()
            and not archive.is_symlink(), 'new_owned_backup_file_required')
    with psycopg.connect(source.direct_dsn, connect_timeout=3, autocommit=True) as db:
        db.execute('CHECKPOINT')
    log = source.root / (archive.stem + '-pg-dump.log')
    with psycopg.connect(source.direct_dsn, connect_timeout=3,
                         row_factory=dict_row) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        exported = db.execute('SELECT pg_export_snapshot() AS identity').fetchone()['identity']
        saved = snapshot_connection(db)
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output, log.open('xb') as errors:
            result = subprocess.run([str(source.pg_bin / 'pg_dump'), '--format=custom',
                '--no-owner', '--no-privileges', '--snapshot=' + exported,
                '--dbname=' + source.direct_dsn], stdout=output, stderr=errors,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=30)
            output.flush()
            os.fsync(output.fileno())
        require(result.returncode == 0, 'native_backup_failed')
    raw = archive.read_bytes()
    require(0 < len(raw) <= MAX_BACKUP and raw.startswith(b'PGDMP'), 'backup_bounds_or_format')
    return Checkpoint(str(source.root), archive, sha256(raw).hexdigest(), saved,
                      sha256(saved.encode()).hexdigest(), datetime.now(timezone.utc).isoformat())


def restore_current(source, destination, checkpoint, *, expected_archive_sha256,
                    expected_snapshot_sha256):
    """Cold cutover of the exact still-current local fixture checkpoint only.

    Independent expected identities are supplied by this drill's host, not by
    the archive. A historical or corrupt dump refuses before any service stops.
    No authorization callback, credential, grant or broker state is restored.
    """
    require(type(checkpoint) is Checkpoint, 'checkpoint_descriptor_required')
    check_runtime(source, running=True)
    check_runtime(destination, running=False)
    require(source.root != destination.root and not source.root.is_relative_to(destination.root)
            and not destination.root.is_relative_to(source.root), 'separate_destination_required')
    require(checkpoint.source_root == str(source.root), 'checkpoint_source_binding_changed')
    require(checkpoint.archive_sha256 == expected_archive_sha256
            and checkpoint.snapshot_sha256 == expected_snapshot_sha256
            and sha256(checkpoint.snapshot_json.encode()).hexdigest() == expected_snapshot_sha256,
            'checkpoint_expected_identity_changed')
    fd = os.open(checkpoint.archive, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_BACKUP,
                'backup_regular_file_required')
        raw = stream.read(MAX_BACKUP + 1)
    require(len(raw) == info.st_size and sha256(raw).hexdigest() == expected_archive_sha256
            and raw.startswith(b'PGDMP'), 'backup_content_changed')
    require(snapshot(source) == checkpoint.snapshot_json, 'source_advanced_new_checkpoint_required')
    started = time.monotonic()
    source.close()
    require(source.pg is None and source.nats is None, 'source_stop_unconfirmed')
    destination.start()
    log = destination.root / 'pg-restore.log'
    with log.open('xb') as output:
        result = subprocess.run([str(destination.pg_bin / 'pg_restore'), '--single-transaction',
            '--exit-on-error', '--no-owner', '--no-privileges',
            '--dbname=' + destination.direct_dsn], input=raw, stdout=output,
            stderr=subprocess.STDOUT, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=30)
    require(result.returncode == 0, 'native_restore_failed')
    require(snapshot(destination) == checkpoint.snapshot_json, 'restored_custody_mismatch')
    return {'schema': 'odsp.local-current-checkpoint-restore.v1',
            'archive_sha256': expected_archive_sha256, 'snapshot_sha256': expected_snapshot_sha256,
            'restore_seconds': time.monotonic() - started, 'custody_snapshot_equal': True,
            'source_services_stopped': True, 'broker_state_restored': False,
            'host_authorization_restored': False, 'historical_disaster_recovery_qualified': False,
            'hosted_qualified': False, 'physical_qualified': False}
