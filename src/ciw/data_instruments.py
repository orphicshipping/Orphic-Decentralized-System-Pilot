"""Local Domain H source instruments, immutable observations and sticky drift holds.

SPDX-License-Identifier: AGPL-3.0-or-later
Launcher-only API. No network, R2, SQL, source code, migration, or NSL admission
is executed here. A replayed TBRT calculation is not an authenticated timestamp.
The existing principal policy and a fresh host admission decision guard every
operation; this module does not register tools or grant permissions.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit

from .campaign_contracts import (ContractRefusal, Limits, canonical, closed_object,
                                 integer, parse, text)
from .persistent_cache import PrivateSQLite
from .principal_policy import PrincipalFacade, PrincipalPolicy


KINDS = frozenset({'places', 'assets', 'infrastructure', 'imagery'})
TABLE_LIMITS = Limits(max_bytes=262144, max_array=1024, max_object=32,
                      max_nodes=65536, max_string=8192)
RECORD_LIMITS = Limits(max_bytes=262144)
SOURCE_LIMITS = Limits(max_bytes=65536)
IMPLEMENTATION_REF = 'sha256:' + sha256(Path(__file__).read_bytes()).hexdigest()
_IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,79}\Z')
_REF = re.compile(r'sha256:[0-9a-f]{64}\Z')


class InstrumentRefusal(ValueError):
    """Bounded refusal code; no credentials, paths or record contents."""


class InstrumentOutcomeUnknown(InstrumentRefusal):
    """A local mutation committed, but current authority forbids disclosure.

    Inspect its durable record after authority is restored; never infer rollback
    or automatically retry a review/reservation with a new identity.
    """


def content_ref(raw):
    if type(raw) is not bytes:
        raise InstrumentRefusal('exact_bytes_required')
    return 'sha256:' + sha256(raw).hexdigest()


def version_ref(value):
    """Existing CIW canonical JSON, explicitly not RFC 8785/JCS."""
    return content_ref(canonical(value, limits=RECORD_LIMITS))


def _ref(value):
    if type(value) is not str or _REF.fullmatch(value) is None:
        raise InstrumentRefusal('content_reference_required')
    return value


def _identifier(value):
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise InstrumentRefusal('identifier_refused')
    return value


def _number(value, *, minimum=None, maximum=1e100):
    if type(value) not in (int, float):
        raise InstrumentRefusal('number_required')
    canonical(value)
    if abs(value) > maximum or (minimum is not None and value < minimum):
        raise InstrumentRefusal('number_outside_bounds')
    return value


def _frame(value):
    closed_object(value, ('clock_id', 'time_scale', 'unit'))
    text(value['clock_id'], 256)
    text(value['time_scale'], 128)
    if value['unit'] != 's':
        raise InstrumentRefusal('explicit_seconds_frame_required')
    return value


def _instant(value):
    closed_object(value, ('value', 'frame'))
    _number(value['value'])
    _frame(value['frame'])
    return value


def _https(value):
    text(value, 1024)
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment
            or any(ord(c) <= 32 for c in value)):
        raise InstrumentRefusal('public_https_identification_required')
    try:
        url.port
    except ValueError:
        raise InstrumentRefusal('public_https_identification_required') from None


def _table(contract):
    closed_object(contract, ('key', 'columns', 'nullable'))
    _identifier(contract['key'])
    columns, nullable = contract['columns'], contract['nullable']
    if (type(columns) is not dict or not 1 <= len(columns) <= 32
            or type(nullable) is not list or len(nullable) > 32
            or any(type(n) is not str for n in nullable)
            or len(nullable) != len(set(nullable)) or set(nullable) - columns.keys()
            or columns.get(contract['key']) not in ('string', 'integer')
            or contract['key'] in nullable):
        raise InstrumentRefusal('table_contract_refused')
    for name, kind in columns.items():
        _identifier(name)
        if kind not in ('string', 'integer', 'number', 'boolean'):
            raise InstrumentRefusal('column_type_refused')


def validate_table(raw, contract):
    """Closed primitive table with a unique typed key. Never coerces values."""
    _table(contract)
    rows = parse(raw, limits=TABLE_LIMITS)
    if type(rows) is not list or len(rows) > 1024:
        raise InstrumentRefusal('table_rows_required')
    indexed = {}
    for row in rows:
        if type(row) is not dict or set(row) != set(contract['columns']):
            raise InstrumentRefusal('column_set_drift')
        for name, kind in contract['columns'].items():
            value = row[name]
            if value is None and name in contract['nullable']:
                continue
            accepted = {'string': (str,), 'integer': (int,),
                        'number': (int, float), 'boolean': (bool,)}[kind]
            if type(value) not in accepted:
                raise InstrumentRefusal('column_type_drift')
        key = canonical(row[contract['key']])
        if key in indexed:
            raise InstrumentRefusal('duplicate_record_key')
        indexed[key] = row
    return indexed


def _source(document, known_good):
    raw = canonical(document, limits=SOURCE_LIMITS)
    value = parse(raw, limits=SOURCE_LIMITS)
    closed_object(value, ('source_id', 'version', 'kind', 'origin', 'license', 'terms',
                         'robots', 'identification', 'scope_review_ref',
                         'minimum_interval_seconds', 'table', 'calibration', 'freshness'))
    _identifier(value['source_id'])
    integer(value['version'], 1, 4096)
    if type(value['kind']) is not str or value['kind'] not in KINDS:
        raise InstrumentRefusal('source_kind_excluded')
    _https(value['origin'])
    _ref(value['scope_review_ref'])
    for name in ('license', 'terms', 'robots'):
        evidence = value[name]
        closed_object(evidence, ('status', 'document_ref', 'review_ref'),
                      ('expression',) if name == 'license' else ())
        if evidence['status'] not in ('permitted', 'held', 'unknown'):
            raise InstrumentRefusal('source_usage_status_refused')
        _ref(evidence['document_ref'])
        _ref(evidence['review_ref'])
        if name == 'license':
            text(evidence.get('expression'), 256)
    identity = value['identification']
    closed_object(identity, ('product', 'version', 'contact'))
    if identity['product'] != 'ODSP':
        raise InstrumentRefusal('honest_product_identification_required')
    if type(identity['version']) is not str or re.fullmatch(r'[0-9][A-Za-z0-9.+-]{0,31}', identity['version']) is None:
        raise InstrumentRefusal('product_version_required')
    _https(identity['contact'])
    _number(value['minimum_interval_seconds'], minimum=1, maximum=86400)
    _table(value['table'])
    calibration = value['calibration']
    closed_object(calibration, ('known_good_ref', 'evidence_ref', 'absolute_tolerances'))
    _ref(calibration['evidence_ref'])
    if _ref(calibration['known_good_ref']) != content_ref(known_good):
        raise InstrumentRefusal('known_good_content_mismatch')
    samples = validate_table(known_good, value['table'])
    if not 1 <= len(samples) <= 64:
        raise InstrumentRefusal('known_good_sample_budget')
    tolerances = calibration['absolute_tolerances']
    if type(tolerances) is not dict or len(tolerances) > 32:
        raise InstrumentRefusal('tolerance_contract_refused')
    for name, tolerance in tolerances.items():
        if name == value['table']['key'] or value['table']['columns'].get(name) not in ('integer', 'number'):
            raise InstrumentRefusal('tolerance_column_refused')
        _number(tolerance, minimum=0)
    freshness = value['freshness']
    closed_object(freshness, ('frame', 'max_age_seconds', 'future_tolerance_seconds'))
    _frame(freshness['frame'])
    _number(freshness['max_age_seconds'], minimum=0, maximum=31536000)
    _number(freshness['future_tolerance_seconds'], minimum=0, maximum=86400)
    return value, raw


def _checks(raw, source, baseline):
    try:
        rows = validate_table(raw, source['table'])
    except (ContractRefusal, InstrumentRefusal):
        return {'schema': 'out_of_tolerance', 'calibration': 'not_evaluated'}
    known = validate_table(baseline, source['table'])
    tolerances = source['calibration']['absolute_tolerances']
    passed = True
    for key, sample in known.items():
        if key not in rows:
            passed = False
            continue
        for name, expected in sample.items():
            observed = rows[key][name]
            if name in tolerances and observed is not None and expected is not None:
                equal = abs(Fraction(observed) - Fraction(expected)) <= Fraction(tolerances[name])
            else:
                equal = canonical(observed) == canonical(expected)
            passed = passed and equal
    return {'schema': 'within_contract',
            'calibration': 'within_tolerance' if passed else 'out_of_tolerance'}


def _freshness(source, now, mapped):
    if now['frame'] != source['freshness']['frame']:
        raise InstrumentRefusal('reference_clock_mismatch')
    age = None if mapped is None else Fraction(now['value']) - mapped
    limits = source['freshness']
    status = ('mapping_required' if age is None else
              'within_nominal_window' if -Fraction(limits['future_tolerance_seconds']) <= age <= Fraction(limits['max_age_seconds']) else
              'out_of_tolerance')
    return status, None if age is None else float(age)


class DataInstrumentLedger:
    """Durable local host API, not a server/MCP authorization facade.

    The host supplies an existing PrincipalPolicy, fixed tenant/principal and
    admit(action, source_id, principal_context) callback. Exact True is required
    every time; context is rechecked after host code and database I/O. The
    callback must enforce current operation/source scope. No callback, identity,
    file path, policy, review authority or clock is a model request argument.

    clock() returns an explicit host reference frame and value; it is never
    described as TBRT. SQL transactions give one-machine create-only records
    and atomic hold updates. Hash chains detect accidental corruption, not a
    same-uid administrator rewriting the whole database without an anchor.
    """
    def __init__(self, path, *, tenant, principal, policy, admit, clock, verify_impact_review=None,
                 max_records=1024, max_bytes=16 * 1024 * 1024):
        self.tenant, self.principal = _identifier(tenant), _identifier(principal)
        if not isinstance(policy, PrincipalPolicy) or not callable(admit) or not callable(clock):
            raise TypeError('existing_policy_and_host_callbacks_required')
        integer(max_records, 16, 4096)
        integer(max_bytes, 65536, 32 * 1024 * 1024)
        self.policy, self._admit, self._clock = policy, admit, clock
        if verify_impact_review is not None and not callable(verify_impact_review):
            raise TypeError('host_review_verifier_required')
        self._verify_review = verify_impact_review
        self._identity = (tenant, principal)
        self._limits = (max_records, max_bytes)
        identity = self._authorize('open', None)
        self.db = PrivateSQLite(path)
        with self.db.transaction() as db:
            statements = (
                'CREATE TABLE IF NOT EXISTS instrument_config (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value BLOB NOT NULL)',
                'CREATE TABLE IF NOT EXISTS instrument_sources (ref TEXT PRIMARY KEY, source_id TEXT NOT NULL, version INTEGER NOT NULL, value BLOB NOT NULL, UNIQUE(source_id,version))',
                'CREATE TABLE IF NOT EXISTS instrument_heads (source_id TEXT PRIMARY KEY, source_ref TEXT NOT NULL, hold_ref TEXT)',
                'CREATE TABLE IF NOT EXISTS instrument_blobs (ref TEXT PRIMARY KEY, value BLOB NOT NULL)',
                'CREATE TABLE IF NOT EXISTS instrument_events (seq INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, source_id TEXT NOT NULL, kind TEXT NOT NULL, value BLOB NOT NULL)',
                'CREATE TABLE IF NOT EXISTS instrument_attempts (source_id TEXT NOT NULL, attempt TEXT NOT NULL, request_ref TEXT NOT NULL, receipt_ref TEXT NOT NULL, PRIMARY KEY(source_id,attempt))',
                'CREATE TABLE IF NOT EXISTS instrument_clocks (capture_ref TEXT PRIMARY KEY, event_ref TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS instrument_rate (source_id TEXT PRIMARY KEY, instant BLOB NOT NULL)',
            )
            for sql in statements:
                db.execute(sql)
            config = canonical({'tenant': tenant, 'principal': principal, 'limits': list(self._limits),
                                'schema': 'odsp.data-instruments.store.v1'})
            db.execute('INSERT OR IGNORE INTO instrument_config VALUES (1,?)', (config,))
            if db.execute('SELECT value FROM instrument_config WHERE singleton=1').fetchone()[0] != config:
                raise InstrumentRefusal('store_binding_changed')
            self._current(identity, 'open', None)

    def _authorize(self, action, source_id):
        if (self.tenant, self.principal) != self._identity:
            raise InstrumentRefusal('identity_changed')
        context = self.policy.context(self.principal)
        if self._admit(action, source_id, deepcopy(context)) is not True:
            raise InstrumentRefusal('host_admission_required')
        self.policy.validate_context(context)
        return context

    def _current(self, identity, action, source_id):
        if self._authorize(action, source_id) != identity:
            raise InstrumentRefusal('principal_context_changed')

    @contextmanager
    def _transaction(self, action, source_id):
        identity = self._authorize(action, source_id)
        with self.db.transaction() as db:
            self._current(identity, action, source_id)
            yield db
            self._budget(db)
            self._current(identity, action, source_id)
        try:
            self._current(identity, action, source_id)
        except Exception:
            if action not in ('read', 'verify_chain'):
                raise InstrumentOutcomeUnknown('local_commit_completed_disclosure_withheld') from None
            raise

    def _budget(self, db):
        count = db.execute('SELECT COUNT(*) FROM instrument_events').fetchone()[0]
        size = sum(db.execute('SELECT COALESCE(SUM(length(value)),0) FROM ' + table).fetchone()[0]
                   for table in ('instrument_sources', 'instrument_blobs', 'instrument_events'))
        if count > self._limits[0] or size > self._limits[1]:
            raise InstrumentRefusal('store_budget_reached')

    def _event(self, db, source_id, kind, body):
        previous = db.execute('SELECT seq,ref FROM instrument_events ORDER BY seq DESC LIMIT 1').fetchone()
        record = {'schema': 'odsp.data-instrument.receipt.v1', 'kind': kind,
                  'sequence': 1 if previous is None else previous[0] + 1,
                  'previous_ref': None if previous is None else previous[1],
                  'tenant': self.tenant, 'principal': self.principal,
                  'source_id': source_id, 'implementation_ref': IMPLEMENTATION_REF,
                  'body': body, 'authorizes_execution': False, 'authorizes_state_admission': False}
        raw = canonical(record, limits=RECORD_LIMITS)
        ref = content_ref(raw)
        db.execute('INSERT INTO instrument_events VALUES (?,?,?,?,?)',
                   (record['sequence'], ref, source_id, kind, raw))
        return ref

    @staticmethod
    def _blob(db, raw):
        ref = content_ref(raw)
        db.execute('INSERT OR IGNORE INTO instrument_blobs VALUES (?,?)', (ref, raw))
        if db.execute('SELECT value FROM instrument_blobs WHERE ref=?', (ref,)).fetchone()[0] != raw:
            raise InstrumentRefusal('content_collision_or_corruption')
        return ref

    @staticmethod
    def _read_blob(db, ref):
        row = db.execute('SELECT value FROM instrument_blobs WHERE ref=?', (_ref(ref),)).fetchone()
        if row is None or content_ref(row[0]) != ref:
            raise InstrumentRefusal('snapshot_missing_or_corrupt')
        return row[0]

    @staticmethod
    def _read_event(db, ref, *, source_id=None, kind=None):
        row = db.execute('SELECT source_id,kind,value FROM instrument_events WHERE ref=?', (_ref(ref),)).fetchone()
        if (row is None or content_ref(row[2]) != ref
                or source_id is not None and row[0] != source_id or kind is not None and row[1] != kind):
            raise InstrumentRefusal('receipt_missing_or_wrong_scope')
        record = parse(row[2], limits=RECORD_LIMITS)
        if record['source_id'] != row[0] or record['kind'] != row[1]:
            raise InstrumentRefusal('receipt_corrupt')
        return record

    @staticmethod
    def _read_source(db, source_ref, *, current=True):
        row = db.execute('SELECT source_id,value FROM instrument_sources WHERE ref=?', (_ref(source_ref),)).fetchone()
        if row is None or content_ref(row[1]) != source_ref:
            raise InstrumentRefusal('source_missing_or_corrupt')
        head = db.execute('SELECT source_ref,hold_ref FROM instrument_heads WHERE source_id=?', (row[0],)).fetchone()
        if current and head[0] != source_ref:
            raise InstrumentRefusal('source_version_superseded')
        return parse(row[1], limits=SOURCE_LIMITS), head[1]

    @staticmethod
    def _usable(source):
        if any(source[k]['status'] != 'permitted' for k in ('license', 'terms', 'robots')):
            raise InstrumentRefusal('source_usage_held')

    def _hold(self, db, source_id, evidence_ref, reasons):
        ref = self._event(db, source_id, 'drift_hold', {'evidence_ref': evidence_ref,
                          'reasons': reasons, 'release_requires': 'recorded_host_impact_review'})
        db.execute('UPDATE instrument_heads SET hold_ref=? WHERE source_id=?', (ref, source_id))
        return ref

    def register_source(self, document, known_good):
        """Host registration preserves rights evidence; it does not prove rights."""
        source, raw = _source(document, known_good)
        source_id, ref = source['source_id'], content_ref(raw)
        with self._transaction('register', source_id) as db:
            existing = db.execute('SELECT value FROM instrument_sources WHERE ref=?', (ref,)).fetchone()
            if existing is not None:
                if existing[0] != raw:
                    raise InstrumentRefusal('source_corrupt')
                return ref
            previous = db.execute('SELECT source_ref FROM instrument_heads WHERE source_id=?', (source_id,)).fetchone()
            version = 0 if previous is None else self._read_source(db, previous[0])[0]['version']
            if source['version'] != version + 1:
                raise InstrumentRefusal('source_version_sequence')
            self._blob(db, known_good)
            db.execute('INSERT INTO instrument_sources VALUES (?,?,?,?)', (ref, source_id, source['version'], raw))
            db.execute('INSERT INTO instrument_heads VALUES (?,?,NULL) ON CONFLICT(source_id) '
                       'DO UPDATE SET source_ref=excluded.source_ref', (source_id, ref))
            event = self._event(db, source_id, 'source_registered', {'source_ref': ref,
                                'previous_source_ref': None if previous is None else previous[0],
                                'rights_verification': 'host_review_evidence_only'})
            if previous is not None:
                self._hold(db, source_id, event, ['source_contract_changed'])
        return ref

    def reserve_collection(self, source_id, source_ref):
        """Atomic rate reservation only. It neither fetches nor authorizes a fetch."""
        _identifier(source_id)
        with self._transaction('reserve', source_id) as db:
            source, _ = self._read_source(db, source_ref)
            if source['source_id'] != source_id:
                raise InstrumentRefusal('source_binding_mismatch')
            self._usable(source)
            now = parse(canonical(_instant(self._clock())))
            if now['frame'] != source['freshness']['frame']:
                raise InstrumentRefusal('reference_clock_mismatch')
            row = db.execute('SELECT instant FROM instrument_rate WHERE source_id=?', (source_id,)).fetchone()
            if row is not None:
                previous = parse(row[0])
                if now['frame'] != previous['frame'] or Fraction(now['value']) - Fraction(previous['value']) < Fraction(source['minimum_interval_seconds']):
                    raise InstrumentRefusal('rate_limited_or_clock_regressed')
            db.execute('INSERT INTO instrument_rate VALUES (?,?) ON CONFLICT(source_id) '
                       'DO UPDATE SET instant=excluded.instant', (source_id, canonical(now)))
            ref = self._event(db, source_id, 'collection_reservation', {'source_ref': source_ref,
                              'host_instant': now, 'identification': source['identification'],
                              'network_request_performed': False, 'authorizes_network': False})
        return ref

    def capture(self, source_id, source_ref, raw, *, attempt, observed):
        """Retain exact bytes, even drifted data; a failed check sets a sticky hold."""
        _identifier(source_id)
        _identifier(attempt)
        if type(raw) is not bytes or not 0 < len(raw) <= TABLE_LIMITS.max_bytes:
            raise InstrumentRefusal('snapshot_byte_budget')
        observed = parse(canonical(_instant(observed)))
        request_ref = version_ref({'source_ref': _ref(source_ref), 'raw_ref': content_ref(raw), 'observed': observed})
        with self._transaction('capture', source_id) as db:
            source, _ = self._read_source(db, source_ref)
            if source['source_id'] != source_id:
                raise InstrumentRefusal('source_binding_mismatch')
            self._usable(source)
            prior = db.execute('SELECT request_ref,receipt_ref FROM instrument_attempts '
                               'WHERE source_id=? AND attempt=?', (source_id, attempt)).fetchone()
            if prior:
                if prior[0] != request_ref:
                    raise InstrumentRefusal('attempt_conflict')
                self._read_event(db, prior[1], source_id=source_id, kind='capture')
                return prior[1]
            baseline = self._read_blob(db, source['calibration']['known_good_ref'])
            checks = _checks(raw, source, baseline)
            now = parse(canonical(_instant(self._clock())))
            mapped = Fraction(observed['value']) if observed['frame'] == now['frame'] else None
            checks['freshness'], age = _freshness(source, now, mapped)
            raw_ref = self._blob(db, raw)
            dataset = {'schema': 'odsp.dataset-version.v1', 'source_ref': source_ref,
                       'content_ref': raw_ref, 'table_contract_ref': version_ref(source['table'])}
            ref = self._event(db, source_id, 'capture', {'source_ref': source_ref, 'attempt': attempt,
                              'dataset': dataset, 'dataset_ref': version_ref(dataset), 'observed': observed,
                              'checks': checks, 'host_instant': now, 'nominal_age_seconds': age,
                              'nsl_admission': 'not_verified',
                              'timestamp_status': 'host_observation_only'})
            db.execute('INSERT INTO instrument_attempts VALUES (?,?,?,?)', (source_id, attempt, request_ref, ref))
            if 'out_of_tolerance' in checks.values():
                self._hold(db, source_id, ref, [k for k, v in checks.items() if v == 'out_of_tolerance'])
        return ref

    def link_clocksync(self, source_id, capture_ref, receipt, *, host):
        """Replay the real existing pinned service; persist only verified linkage.

        The receipt's evidence_id must be the exact raw dataset content ref.
        Successful numerical replay preserves the provider's false source
        authentication/traceability flags. No NSL or downstream grant is made.
        """
        from .audited_host import AuditedPrincipalHost
        from .clocksync_profile import ClockSyncHost
        from .services import invoke
        facade = host._check_identity() if type(host) is AuditedPrincipalHost else host
        if (type(facade) is not PrincipalFacade or facade.principal != self.principal
                or facade.policy is not self.policy or type(facade.hosts.get('clocksync')) is not ClockSyncHost):
            raise InstrumentRefusal('principal_clocksync_binding_required')
        receipt = parse(canonical(receipt))
        identity = self._authorize('clock_link', _identifier(source_id))
        with self.db.transaction() as db:
            capture = self._read_event(db, capture_ref, source_id=source_id, kind='capture')['body']
            source, _ = self._read_source(db, capture['source_ref'])
        self._current(identity, 'clock_link', source_id)
        if type(receipt) is not dict or type(receipt.get('request')) is not dict or type(receipt['request'].get('observation')) is not dict:
            raise InstrumentRefusal('timestamp_dataset_binding_mismatch')
        observation = receipt['request']['observation']
        if (canonical(observation.get('device_time')) != canonical(capture['observed']['value'])
                or observation.get('frame') != capture['observed']['frame']
                or observation.get('evidence_id') != capture['dataset']['content_ref']
                or receipt.get('request', {}).get('expected_reference') != source['freshness']['frame']):
            raise InstrumentRefusal('timestamp_dataset_binding_mismatch')
        replay = invoke('ncp_clocksync_replay', {'receipt': receipt}, host=host)
        if (replay.get('outcome') != 'verified'
                or any(replay.get(k) is not True for k in ('canonical_receipt_match', 'request_digest_match',
                                                         'numerical_digest_match', 'receipt_digest_match'))
                or replay.get('replayed_receipt_digest') != receipt.get('receipt_digest')):
            raise InstrumentRefusal('clocksync_replay_not_verified')
        self._current(identity, 'clock_link', source_id)
        with self._transaction('clock_link', source_id) as db:
            self._current(identity, 'clock_link', source_id)
            self._read_source(db, capture['source_ref'])
            old = db.execute('SELECT event_ref FROM instrument_clocks WHERE capture_ref=?', (capture_ref,)).fetchone()
            if old is not None:
                raise InstrumentRefusal('timestamp_link_already_recorded')
            event = self._event(db, source_id, 'clocksync_link', {'capture_ref': capture_ref,
                                'receipt': receipt, 'replay': replay,
                                'timestamp_status': 'numerical_replay_verified_source_unauthenticated'})
            db.execute('INSERT INTO instrument_clocks VALUES (?,?)', (capture_ref, event))
        return event

    def _assessment(self, db, source_id, capture_ref):
        capture = self._read_event(db, capture_ref, source_id=source_id, kind='capture')['body']
        source, hold = self._read_source(db, capture['source_ref'])
        self._usable(source)
        raw = self._read_blob(db, capture['dataset']['content_ref'])
        checks = _checks(raw, source, self._read_blob(db, source['calibration']['known_good_ref']))
        now = parse(canonical(_instant(self._clock())))
        if now['frame'] != source['freshness']['frame']:
            raise InstrumentRefusal('reference_clock_mismatch')
        clock = db.execute('SELECT event_ref FROM instrument_clocks WHERE capture_ref=?', (capture_ref,)).fetchone()
        mapped, uncertainty, timestamp = None, None, 'host_observation_only'
        if clock:
            link = self._read_event(db, clock[0], source_id=source_id, kind='clocksync_link')['body']
            value = link['receipt']['numerical_result']
            mapped = Fraction(value['reference_origin']) + Fraction(value['event_time_delta'])
            uncertainty = {'standard_uncertainty': value['standard_uncertainty'], 'variance': value['variance'],
                           'propagation': value['propagation'], 'interpretation': 'declared_model_not_authenticated_time'}
            timestamp = 'retained_numerical_replay_source_unauthenticated'
        elif capture['observed']['frame'] == now['frame']:
            mapped = Fraction(capture['observed']['value'])
        checks['freshness'], age = _freshness(source, now, mapped)
        return {'capture_ref': capture_ref, 'source_ref': capture['source_ref'],
                'dataset_ref': capture['dataset_ref'], 'checks': checks, 'hold_ref': hold,
                'host_instant': now, 'nominal_age_seconds': age,
                'clock_link_ref': None if clock is None else clock[0], 'timestamp_status': timestamp,
                'time_uncertainty': uncertainty, 'source_authenticated': False,
                'nsl_admission': 'not_verified', 'trusted_for_downstream': False,
                'trust_gaps': ['authenticated_source_time', 'verified_nsl_admission']}

    def assess(self, source_id, capture_ref):
        """Recheck bytes/contracts and current nominal age; never clears a hold."""
        with self._transaction('assess', _identifier(source_id)) as db:
            result = self._assessment(db, source_id, capture_ref)
            event = self._event(db, source_id, 'assessment', result)
            failures = [k for k, v in result['checks'].items() if v == 'out_of_tolerance']
            if failures:
                result['hold_ref'] = self._hold(db, source_id, event, failures)
            result['assessment_ref'] = event
        return result

    def review_hold(self, source_id, *, expected_hold_ref, capture_ref, review):
        """Launcher-only recorded impact review; clears local drift only.

        The review document is supplied by a host approval workflow, never by
        an MCP/model operation. An exact active hold and a freshly checked
        recovery capture are required. Downstream authentication/admission is
        still missing and cannot be asserted by this review.
        """
        review = parse(canonical(review, limits=SOURCE_LIMITS), limits=SOURCE_LIMITS)
        closed_object(review, ('approval_ref', 'reviewer', 'decision', 'impact'))
        _ref(review['approval_ref'])
        _identifier(review['reviewer'])
        if review['decision'] != 'release_local_drift_hold':
            raise InstrumentRefusal('impact_review_decision_refused')
        if type(review['impact']) is not list or len(review['impact']) > 64:
            raise InstrumentRefusal('impact_review_budget')
        for item in review['impact']:
            closed_object(item, ('artifact_ref', 'disposition', 'evidence_ref'))
            _ref(item['artifact_ref']); _ref(item['evidence_ref'])
            if item['disposition'] not in ('hold', 'invalidate', 'recompute'):
                raise InstrumentRefusal('impact_disposition_required')
        intent = {'source_id': _identifier(source_id), 'hold_ref': _ref(expected_hold_ref),
                  'capture_ref': _ref(capture_ref), 'review': review}
        identity = self._authorize('impact_review', source_id)
        if self._verify_review is None or self._verify_review(deepcopy(intent), deepcopy(identity)) is not True:
            raise InstrumentRefusal('verified_host_impact_review_required')
        self._current(identity, 'impact_review', source_id)
        with self._transaction('impact_review', _identifier(source_id)) as db:
            self._current(identity, 'impact_review', source_id)
            result = self._assessment(db, source_id, capture_ref)
            if result['hold_ref'] != _ref(expected_hold_ref):
                raise InstrumentRefusal('active_hold_changed')
            if result['checks'] != {'schema': 'within_contract', 'calibration': 'within_tolerance',
                                    'freshness': 'within_nominal_window'}:
                raise InstrumentRefusal('recovery_capture_out_of_tolerance')
            # The reviewer may be revoked while another process holds the DB.
            if self._verify_review(deepcopy(intent), deepcopy(identity)) is not True:
                raise InstrumentRefusal('verified_host_impact_review_required')
            self._current(identity, 'impact_review', source_id)
            # Approval verification may block; sample freshness again afterwards.
            result = self._assessment(db, source_id, capture_ref)
            if result['checks'] != {'schema': 'within_contract', 'calibration': 'within_tolerance',
                                    'freshness': 'within_nominal_window'}:
                raise InstrumentRefusal('recovery_capture_out_of_tolerance')
            # Retain both the recovery measurement and exact review, even when
            # impact is empty because no downstream artifact was admitted.
            event = self._event(db, source_id, 'impact_review', {'hold_ref': expected_hold_ref,
                                'recovery': result, 'review': review, 'trusted_for_downstream': False})
            db.execute('UPDATE instrument_heads SET hold_ref=NULL WHERE source_id=? AND hold_ref=?',
                       (source_id, expected_hold_ref))
        return event

    def receipt(self, source_id, ref):
        with self._transaction('read', _identifier(source_id)) as db:
            result = self._read_event(db, ref, source_id=source_id)
        return result

    def snapshot(self, source_id, capture_ref):
        """Inspection bytes only, including held data; not a trusted dataset grant."""
        with self._transaction('read', _identifier(source_id)) as db:
            capture = self._read_event(db, capture_ref, source_id=source_id, kind='capture')['body']
            raw = self._read_blob(db, capture['dataset']['content_ref'])
        return raw

    def inspect_capture(self, source_id, capture_ref):
        """Atomic launcher-only inspection of an exact current-source capture.

        No assessment event is appended, so callers may repeat this guard after
        each blocking operation. A failing check is not permission to continue:
        callers must stop and use assess() to persist the resulting drift hold.
        This read never establishes authenticated time or NSL admission.
        """
        with self._transaction('read', _identifier(source_id)) as db:
            receipt = self._read_event(db, capture_ref, source_id=source_id, kind='capture')
            raw = self._read_blob(db, receipt['body']['dataset']['content_ref'])
            assessment = self._assessment(db, source_id, capture_ref)
            result = {'receipt': receipt, 'raw': raw, 'assessment': assessment}
        return result

    def verify_chain(self):
        with self._transaction('verify_chain', None) as db:
            rows = db.execute('SELECT seq,ref,source_id,kind,value FROM instrument_events ORDER BY seq').fetchall()
            previous = None
            for sequence, row in enumerate(rows, 1):
                seq, ref, source, kind, raw = row
                value = parse(raw, limits=RECORD_LIMITS)
                if (seq != sequence or content_ref(raw) != ref or value['previous_ref'] != previous
                        or value['sequence'] != sequence or value['source_id'] != source or value['kind'] != kind
                        or value['tenant'] != self.tenant or value['principal'] != self.principal):
                    raise InstrumentRefusal('receipt_chain_corrupt')
                previous = ref
            result = {'records': len(rows), 'head_ref': previous, 'local_chain_consistent': True,
                      'independently_anchored': False, 'nsl_admission_verified': False}
        return result
