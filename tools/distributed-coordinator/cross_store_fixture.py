"""Bounded cold recovery set for owned, quiesced synthetic local fixtures only.

This is qualification infrastructure, not a backup service or activation API.
The trusted test host supplies the participants, current authority and expected
pins. No callback, DSN, executable, broker credential or active grant is loaded
from the recovery set. Same-uid adversarial writers and remote failover are
outside this single-host drill's contract.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace
from hashlib import sha256
import fcntl
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import sys


def _native():
    name = 'odsp_cross_store_native_restore'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name('restore_fixture.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class RecoveryHeld(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise RecoveryHeld(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                      allow_nan=False).encode()


def ref(raw):
    return 'sha256:' + sha256(raw).hexdigest()


TABLES = {
    'survey': ('instrument_config', 'instrument_sources', 'instrument_heads', 'instrument_blobs',
               'instrument_events', 'instrument_attempts', 'instrument_clocks', 'instrument_rate'),
    'journal': ('config', 'plans', 'jobs', 'events', 'observations', 'hold', 'clock_state'),
}
MAX_MEMBER = 16 * 1024 * 1024
MAX_SET = 32 * 1024 * 1024
CLAIMS = {'host_authorization_restored': False, 'broker_state_restored': False,
          'automatic_resend': False, 'hosted_qualified': False, 'physical_qualified': False,
          'power_loss_qualified': False, 'atomic_distributed_snapshot': False}


def private(path):
    path = Path(path)
    info = path.lstat()
    require(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and not info.st_mode & 0o077, 'private_owned_directory_required')
    return path


def read(path, maximum=MAX_MEMBER):
    path = Path(path)
    require(path.parent.resolve() == path.parent, 'member_parent_link_refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and 0 <= info.st_size <= maximum, 'owned_bounded_regular_member_required')
        raw = stream.read(maximum + 1)
    require(len(raw) == info.st_size, 'member_changed_or_exceeded_budget')
    return raw


def write(path, raw):
    require(len(raw) <= MAX_MEMBER, 'member_budget')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def sqlite_state(path, kind):
    read(path)
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=2) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'sqlite_integrity_failed')
        schema = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        require({row[0] for row in schema} == set(TABLES[kind]), 'sqlite_schema_not_expected')
        def normal(row):
            return [{'bytes': v.hex()} if isinstance(v, bytes) else v for v in row]
        rows = {table: sorted((normal(row) for row in db.execute('SELECT * FROM ' + table)), key=canonical)
                for table in TABLES[kind]}
    return {'schema': schema, 'rows': rows}


def sqlite_backup(source, destination, kind):
    # Native backup includes committed WAL content. The caller holds both
    # participant write locks; no raw live database/WAL copy is used.
    write(destination, b'')
    with sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True) as before:
        with sqlite3.connect(destination) as after:
            before.backup(after, pages=128, sleep=.01)
            require(after.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete',
                    'backup_journal_normalization_failed')
    require(canonical(sqlite_state(source, kind)) == canonical(sqlite_state(destination, kind)),
            'sqlite_native_backup_mismatch')


def safe_name(name):
    require(type(name) is str and len(name) <= 240, 'unsafe_member_name')
    parts = PurePosixPath(name).parts
    require(name == str(PurePosixPath(name)) and not name.startswith('/')
            and '\\' not in name and 1 <= len(parts) <= 4
            and all(p not in ('', '.', '..') for p in parts), 'unsafe_member_name')
    require(name in {'survey.sqlite', 'journal.sqlite', 'coordinator.dump', 'coordinator.json'}
            or parts[0] in {'survey', 'workbench', 'retained'} and len(parts) >= 2
            and (name.endswith('.json') or name.endswith('.html') or parts[-1] == 'sequence'),
            'member_not_in_fixture_contract')
    return name


@dataclass(frozen=True)
class Participants:
    ledger: object
    journal: object
    retention_directory: Path
    files: dict
    identities: dict


@contextmanager
def frozen(participants, *, quiesced):
    require(quiesced is True, 'explicit_owned_worker_quiescence_required')
    private(participants.retention_directory)
    with ExitStack() as stack:
        stack.enter_context(participants.journal._owner())
        stack.enter_context(participants.journal._transaction())
        stack.enter_context(participants.ledger.db.transaction())
        lock = os.open(participants.retention_directory / 'sequence.lock', os.O_RDWR | os.O_NOFOLLOW)
        stack.callback(os.close, lock)
        info = os.fstat(lock)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid(),
                'retention_lock_refused')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def state(participants):
    require(1 <= len(participants.files) <= 60, 'fixture_file_count')
    return {'sqlite': {kind: ref(canonical(sqlite_state(path, kind))) for kind, path in (
                ('survey', participants.ledger.db.path), ('journal', participants.journal.path))},
            'files': {safe_name(name): ref(read(path)) for name, path in sorted(participants.files.items())}}


def check_links(root, identities):
    """Check linked custody before any services stop; no source or authority loads."""
    from ciw.sensor_survey_workbench import inspect_workbench
    projection = inspect_workbench(root / 'workbench',
        expected_manifest_ref=identities['projection_manifest_ref'])['projection']
    model = json.loads(read(root / 'workbench/model.json'))
    require(projection['execution_id'] == identities['execution_id']
            and projection['result_id'] == identities['result_id']
            and model['sensor']['job_id'] == identities['journal_job_id']
            and model['survey']['capture_ref'] == identities['survey_capture_ref']
            and projection['lineage']['survey_result_ref'] == identities['survey_result_ref']
            and projection['lineage']['sensor_binding_ref'] == identities['journal_binding_ref'],
            'cross_store_projection_identity_mismatch')
    survey = sqlite_state(root / 'survey.sqlite', 'survey')['rows']
    journal = sqlite_state(root / 'journal.sqlite', 'journal')['rows']
    config = json.loads(bytes.fromhex(journal['config'][0][1]['bytes']))
    require(config['tenant'] == identities['tenant'] and config['principal'] == identities['principal']
            and ref(canonical(config['binding'])) == identities['journal_binding_ref'], 'cross_store_owner_mismatch')
    exported = json.loads(read(root / 'workbench/journal-export.json'))
    event_rows = sorted((row for row in journal['events'] if row[0] == identities['journal_job_id']), key=lambda row: row[1])
    require([json.loads(bytes.fromhex(row[3]['bytes'])) for row in event_rows] == exported['events'],
            'cross_store_journal_events_mismatch')
    require(any(row[0] == identities['survey_capture_ref'] for row in survey['instrument_events'])
            or any(row[1] == identities['survey_capture_ref'] for row in survey['instrument_events']),
            'cross_store_survey_capture_missing')
    owner = sha256(identities['retention_owner'].encode()).hexdigest()[:32]
    retained = json.loads(read(root / 'retained' / owner / (model['survey']['artifact_id'] + '.json')))
    # Store envelopes retain the native payload, not authority.
    payload = retained['native']
    require(retained['owner'] == identities['retention_owner']
            and payload['result_ref'] == identities['survey_result_ref']
            and payload['capture_ref'] == identities['survey_capture_ref']
            and payload['source_ref'] == identities['source_ref'], 'cross_store_retained_result_missing')
    return projection


def capture_set(source, participants, root, *, quiesced):
    root = Path(root)
    root.mkdir(mode=0o700, exist_ok=False)
    with frozen(participants, quiesced=quiesced):
        before = state(participants)
        checkpoint = _native().capture(source, source.root / (root.name + '.dump'))
        for name, path in sorted(participants.files.items()):
            write(root / safe_name(name), read(path))
        sqlite_backup(participants.ledger.db.path, root / 'survey.sqlite', 'survey')
        sqlite_backup(participants.journal.path, root / 'journal.sqlite', 'journal')
        write(root / 'coordinator.dump', read(checkpoint.archive))
        write(root / 'coordinator.json', checkpoint.snapshot_json.encode())
        require(state(participants) == before and _native().snapshot(source) == checkpoint.snapshot_json,
                'participants_advanced_during_capture')
        check_links(root, participants.identities)
        members = {str(path.relative_to(root)): {'ref': ref(read(path)), 'bytes': len(read(path))}
                   for path in sorted(root.rglob('*')) if path.is_file()}
        require(sum(m['bytes'] for m in members.values()) <= MAX_SET, 'recovery_set_budget')
        manifest = {'schema': 'odsp.local-synthetic-cross-store-recovery.v1', 'status': 'held',
            'identities': participants.identities, 'source_state': before, 'members': members,
            'coordinator_snapshot_ref': ref(checkpoint.snapshot_json.encode()),
            'claims': CLAIMS}
        raw = canonical(manifest)
        write(root / 'recovery-set.json', raw)
    return checkpoint, ref(raw)


def validate_set(root, *, expected_ref, expected_identities, expected_members):
    root = private(root)
    raw = read(root / 'recovery-set.json', 65536)
    require(ref(raw) == expected_ref, 'external_recovery_pin_mismatch')
    manifest = json.loads(raw)
    require(set(manifest) == {'schema', 'status', 'identities', 'source_state', 'members',
                             'coordinator_snapshot_ref', 'claims'}
            and manifest['schema'] == 'odsp.local-synthetic-cross-store-recovery.v1'
            and manifest['status'] == 'held' and manifest['identities'] == expected_identities
            and manifest['claims'] == CLAIMS,
            'recovery_set_contract_or_owner_mismatch')
    require(set(manifest['members']) == set(expected_members) | {
        'survey.sqlite', 'journal.sqlite', 'coordinator.dump', 'coordinator.json'}, 'recovery_member_set_changed')
    require(len(manifest['members']) <= 64, 'fixture_file_count')
    seen, total = set(), 0
    for path in root.rglob('*'):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), 'recovery_links_refused')
        if stat.S_ISDIR(info.st_mode):
            private(path)
        else:
            seen.add(str(path.relative_to(root)))
    require(seen == set(manifest['members']) | {'recovery-set.json'}, 'unlisted_or_missing_recovery_member')
    for name, record in manifest['members'].items():
        data = read(root / safe_name(name))
        require(record == {'ref': ref(data), 'bytes': len(data)}, 'recovery_member_pin_mismatch')
        total += len(data)
    require(total <= MAX_SET, 'recovery_set_budget')
    require(ref(read(root / 'coordinator.json')) == manifest['coordinator_snapshot_ref'], 'custody_pin_mismatch')
    for kind in TABLES:
        require(ref(canonical(sqlite_state(root / (kind + '.sqlite'), kind)))
                == manifest['source_state']['sqlite'][kind], 'sqlite_logical_state_mismatch')
    require({name: manifest['members'][name]['ref'] for name in expected_members}
            == manifest['source_state']['files'], 'file_custody_mismatch')
    check_links(root, expected_identities)
    return manifest


def cutover(source, destination, participants, checkpoint, root, staged, *, expected_ref, quiesced):
    """Verify all participants, stage create-only copies, then native cold cutover.

    Any exception is a held recovery, leaving all source/partial evidence intact.
    Caller must separately supply fresh authority and explicitly inspect holds.
    """
    manifest = validate_set(root, expected_ref=expected_ref,
        expected_identities=participants.identities, expected_members=participants.files)
    _native().check_runtime(destination, running=False)
    staged = Path(staged)
    require(not staged.exists() and not staged.is_symlink() and staged.is_absolute()
            and staged.parent.resolve() == staged.parent
            and not staged.is_relative_to(source.root) and not Path(root).is_relative_to(staged),
            'new_separate_participant_destination_required')
    with frozen(participants, quiesced=quiesced):
        require(state(participants) == manifest['source_state'], 'participant_advanced_new_checkpoint_required')
        require(_native().snapshot(source) == checkpoint.snapshot_json,
                'source_advanced_new_checkpoint_required')
        require(ref(checkpoint.snapshot_json.encode()) == manifest['coordinator_snapshot_ref']
                and ref(read(Path(root) / 'coordinator.dump')) == 'sha256:' + checkpoint.archive_sha256,
                'native_checkpoint_set_mismatch')
        staged.mkdir(mode=0o700)
        for name in manifest['members']:
            write(staged / name, read(Path(root) / name))
        write(staged / 'recovery-set.json', read(Path(root) / 'recovery-set.json'))
        validate_set(staged, expected_ref=expected_ref, expected_identities=participants.identities,
                     expected_members=participants.files)
        report = _native().restore_current(source, destination,
            replace(checkpoint, archive=staged / 'coordinator.dump'),
            expected_archive_sha256=checkpoint.archive_sha256,
            expected_snapshot_sha256=checkpoint.snapshot_sha256)
    return {**report, 'recovery_set_ref': expected_ref, 'all_participants_restored': True,
            'participant_source_state_equal_before_reopen': True, 'status': 'held',
            'activation': 'not_performed', 'fresh_authority_required': True,
            'source_files_preserved': True}
