"""Source-bound collection and conditional R2 quarantine storage adapters.

SPDX-License-Identifier: AGPL-3.0-or-later
Host-only bindings; no credentials are created or discovered here. A collector
retains unadmitted observations, never grants downstream trust. HTTP destinations
and storage namespaces are fixed by the launcher, not request arguments.
"""
from copy import deepcopy
from hashlib import md5
import base64
import http.client
import io
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

from .campaign_contracts import canonical, parse, closed_object, integer
from .data_instruments import (DataInstrumentLedger, InstrumentRefusal, _identifier,
                               _instant, _ref, content_ref, version_ref)


class CollectionRefusal(InstrumentRefusal):
    pass


class CollectionOutcomeUnknown(CollectionRefusal):
    """A request may have occurred; inspect retained intent and never auto-retry."""


def robots_permission(raw, path, *, interval):
    """Conservative literal-prefix robots subset; unsupported rules refuse.

    Retained document, not an automatic robots fetch. Wildcards/end anchors and
    unrecognized directives are held for review instead of silently ignored.
    Exact ODSP groups take precedence over '*'; matching Allow wins equal-length
    ties. A matching group is required even if the source declares permission.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= 16384:
        raise CollectionRefusal('robots_byte_budget')
    try:
        lines = raw.decode('utf-8', 'strict').splitlines()
    except UnicodeError:
        raise CollectionRefusal('robots_encoding_refused') from None
    groups, agents, rules, delays = [], [], [], []
    def finish():
        if agents:
            groups.append((list(agents), list(rules), list(delays)))
    for line in lines:
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if ':' not in line:
            raise CollectionRefusal('robots_syntax_refused')
        name, value = (x.strip() for x in line.split(':', 1))
        name = name.lower()
        if name == 'user-agent':
            if rules or delays:
                finish(); agents, rules, delays = [], [], []
            if not value or len(value) > 128:
                raise CollectionRefusal('robots_agent_refused')
            agents.append(value.lower())
        elif name in ('allow', 'disallow'):
            if not agents or (value and not value.startswith('/')) or any(c in value for c in ('*', '$', '%', '\\')):
                raise CollectionRefusal('robots_rule_requires_review')
            rules.append((name, value))
        elif name == 'crawl-delay':
            if not agents or re.fullmatch(r'[0-9]{1,5}(?:\.[0-9]{1,3})?', value) is None:
                raise CollectionRefusal('robots_delay_refused')
            delays.append(float(value))
        elif name != 'sitemap':
            raise CollectionRefusal('robots_directive_requires_review')
    finish()
    selected = [g for g in groups if 'odsp' in g[0]]
    if not selected:
        selected = [g for g in groups if '*' in g[0]]
    if not selected:
        raise CollectionRefusal('robots_matching_group_required')
    matching = []
    for _, rules, delays in selected:
        if delays and max(delays) > interval:
            raise CollectionRefusal('registered_interval_below_robots_delay')
        matching.extend((len(prefix), name == 'allow') for name, prefix in rules
                        if prefix and path.startswith(prefix))
    if matching and not max(matching)[1]:
        raise CollectionRefusal('robots_disallowed')
    return True


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.bound_socket = None

    def connect(self):
        # No ambient proxy and no hostname re-resolution between review and TCP.
        deadline = time.monotonic() + self.timeout
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            if ipaddress.ip_address(raw.getpeername()[0]) != ipaddress.ip_address(self.address):
                raise CollectionRefusal('http_peer_changed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectionRefusal('http_deadline')
            raw.settimeout(remaining)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            self.bound_socket = self.sock
        except BaseException:
            raw.close()
            raise


class _DeadlineReader(io.RawIOBase):
    """Every socket read shares one deadline, including a trickled header line."""
    def __init__(self, sock, deadline):
        self._socket, self._deadline = sock, deadline
        self._headers = bytearray()
        self._headers_done = False

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise CollectionRefusal('http_deadline')
        self._socket.settimeout(remaining)
        target = buffer if self._headers_done else memoryview(buffer)[:8193 - len(self._headers)]
        count = self._socket.recv_into(target)
        if not self._headers_done:
            self._headers.extend(target[:count])
            end = self._headers.find(b'\r\n\r\n')
            if end >= 0 and end + 4 <= 8192:
                self._headers_done = True
                self._headers.clear()
            elif len(self._headers) > 8192:
                raise CollectionRefusal('http_header_budget')
        return count


class _ResponseSocket:
    """Read-only HTTP parser view; the connection retains socket ownership."""
    def __init__(self, sock, deadline):
        self._socket, self._deadline = sock, deadline

    def makefile(self, mode):
        if mode != 'rb':
            raise CollectionRefusal('http_read_mode_refused')
        return io.BufferedReader(_DeadlineReader(self._socket, self._deadline), buffer_size=8192)


class SourceHTTPS:
    """One fixed public HTTPS URL/IP and honest identity; no redirects or retries.

    approved_address is a launcher-reviewed literal public address. TLS verifies
    the original hostname using the default trust store. A changed endpoint needs
    a new reviewed binding. Addresses, URLs, headers and TLS choices are not call
    arguments. Local fixture transports are separate test implementations.
    """
    def __init__(self, source, *, approved_address, timeout=5, max_bytes=262144):
        self.source_ref = version_ref(source)
        url = urlsplit(source['origin'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise CollectionRefusal('fixed_https_source_required')
        address = ipaddress.ip_address(approved_address)
        if not address.is_global:
            raise CollectionRefusal('public_literal_address_required')
        if type(timeout) not in (int, float) or not 0 < timeout <= 10:
            raise CollectionRefusal('http_timeout_refused')
        integer(max_bytes, 1, 262144)
        identity = source['identification']
        if identity['product'] != 'ODSP':
            raise CollectionRefusal('honest_identification_required')
        self._host, self._port, self._path = url.hostname, url.port or 443, url.path or '/'
        # Percent-encoded paths need an explicit normalization/robots review.
        if any(c in self._path for c in ('%', '\\')) or any(not 32 < ord(c) < 127 for c in source['origin']):
            raise CollectionRefusal('path_requires_review')
        self._address, self._timeout, self._max = str(address), timeout, max_bytes
        self._agent = 'ODSP/' + identity['version'] + ' (contact=' + identity['contact'] + ')'
        if any(ord(c) < 32 or ord(c) > 126 for c in self._agent):
            raise CollectionRefusal('http_identification_refused')

    @staticmethod
    def _read_response(response, *, max_bytes, deadline, set_timeout):
        headers = response.getheaders()
        if len(headers) > 32 or sum(len(k) + len(v) for k, v in headers) > 8192:
            raise CollectionRefusal('http_header_budget')
        lowered = {}
        for k, v in headers:
            k = k.lower()
            if k in lowered:
                raise CollectionRefusal('http_duplicate_header')
            lowered[k] = v
        if response.status != 200:
            raise CollectionRefusal('http_status_refused')
        if lowered.get('content-encoding', 'identity').lower() != 'identity':
            raise CollectionRefusal('http_encoding_refused')
        if lowered.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise CollectionRefusal('http_content_type_refused')
        length = lowered.get('content-length')
        if length is None or re.fullmatch(r'[0-9]{1,7}', length) is None or not 0 < int(length) <= max_bytes:
            raise CollectionRefusal('http_length_required')
        if 'transfer-encoding' in lowered:
            raise CollectionRefusal('http_transfer_encoding_refused')
        chunks, total = [], 0
        while total < int(length):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectionRefusal('http_deadline')
            set_timeout(remaining)
            chunk = response.read1(min(8192, int(length) - total))
            if not chunk:
                raise CollectionRefusal('http_truncated_body')
            total += len(chunk)
            if total > int(length) or total > max_bytes:
                raise CollectionRefusal('http_body_budget')
            chunks.append(chunk)
        if time.monotonic() > deadline:
            raise CollectionRefusal('http_deadline')
        return b''.join(chunks)

    def fetch(self):
        deadline = time.monotonic() + self._timeout
        connection = _PinnedHTTPS(self._host, self._port, self._address, self._timeout)
        try:
            connection.connect()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectionRefusal('http_deadline')
            connection.bound_socket.settimeout(remaining)
            connection.request('GET', self._path, headers={'User-Agent': self._agent,
                                'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Connection': 'close'})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectionRefusal('http_deadline')
            connection.bound_socket.settimeout(remaining)
            connection.response_class = lambda sock, **kw: http.client.HTTPResponse(
                _ResponseSocket(sock, deadline), **kw)
            response = connection.getresponse()
            raw = self._read_response(response, max_bytes=self._max, deadline=deadline,
                                      set_timeout=connection.bound_socket.settimeout)
            return {'raw': raw, 'transport': 'https_public_pinned_address',
                    'source_ref': self.source_ref, 'http_status': 200}
        finally:
            connection.close()


class R2QuarantineStore:
    """Conditional create-only R2 S3 calls with full SHA-256 readback.

    Host supplies an already configured client and fresh authorization callback.
    No buckets, credentials, policies, ACLs or lifecycle rules are created. The
    client must bind the exact R2 account endpoint and bounded SDK timeouts with
    at most one attempt. All stored content remains explicitly unadmitted.
    """
    def __init__(self, client, *, account_id, bucket, tenant, principal, authorize):
        if type(account_id) is not str or re.fullmatch(r'[0-9a-f]{32}', account_id) is None:
            raise CollectionRefusal('r2_account_binding_required')
        if type(bucket) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket) is None:
            raise CollectionRefusal('bucket_binding_required')
        if not callable(authorize):
            raise TypeError('storage_authorization_required')
        endpoint = 'https://' + account_id + '.r2.cloudflarestorage.com'
        meta = getattr(client, 'meta', None)
        config = getattr(meta, 'config', None)
        retries = getattr(config, 'retries', {}) or {}
        if (getattr(meta, 'endpoint_url', None) != endpoint
                or getattr(config, 'signature_version', None) != 's3v4'
                or type(getattr(config, 'connect_timeout', None)) not in (int, float)
                or not 0 < config.connect_timeout <= 5
                or type(getattr(config, 'read_timeout', None)) not in (int, float)
                or not 0 < config.read_timeout <= 5
                or type(retries) is not dict or type(retries.get('total_max_attempts')) is not int
                or retries['total_max_attempts'] != 1):
            raise CollectionRefusal('bounded_exact_r2_client_required')
        self._client, self._bucket, self._authorize = client, bucket, authorize
        self._scope = version_ref({'tenant': _identifier(tenant), 'principal': _identifier(principal),
                                   'endpoint': endpoint, 'bucket': bucket})

    def _key(self, source_id, raw_ref):
        return 'odsp-data/v1/' + self._scope[7:] + '/' + _identifier(source_id) + '/' + _ref(raw_ref)[7:]

    def _allowed(self, intent):
        if self._authorize(deepcopy(intent)) is not True:
            raise CollectionRefusal('storage_authorization_required')

    def _readback(self, key, expected):
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response.get('Body')
        try:
            if response.get('ContentLength') != len(expected) or body is None:
                raise CollectionRefusal('storage_length_mismatch')
            raw = body.read(len(expected) + 1)
            if type(raw) is not bytes or raw != expected or content_ref(raw) != content_ref(expected):
                raise CollectionRefusal('storage_content_mismatch')
        finally:
            if body is not None:
                body.close()

    def retain(self, source_id, raw):
        if type(raw) is not bytes or not 0 < len(raw) <= 262144:
            raise CollectionRefusal('storage_byte_budget')
        ref = content_ref(raw)
        key = self._key(source_id, ref)
        intent = {'scope_ref': self._scope, 'source_id': source_id, 'content_ref': ref,
                  'purpose': 'quarantine_unadmitted_snapshot', 'bytes': len(raw)}
        self._allowed(intent)
        existed = False
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=raw, IfNoneMatch='*',
                                    ContentType='application/json',
                                    ContentMD5=base64.b64encode(md5(raw, usedforsecurity=False).digest()).decode('ascii'),
                                    Metadata={'sha256': ref[7:], 'odsp-trust': 'quarantined'})
        except Exception as exc:
            response = getattr(exc, 'response', {})
            metadata = response.get('ResponseMetadata') if type(response) is dict else None
            if type(metadata) is dict and type(metadata.get('HTTPStatusCode')) is int and metadata['HTTPStatusCode'] == 412:
                existed = True
            else:
                raise CollectionOutcomeUnknown('storage_put_outcome_unknown_no_retry') from None
        # The write may already have committed; revocation cannot imply rollback.
        try:
            self._allowed(intent)
            self._readback(key, raw)
            self._allowed(intent)
        except Exception:
            raise CollectionOutcomeUnknown('storage_readback_or_authority_unconfirmed') from None
        return {'scope_ref': self._scope, 'content_ref': ref, 'key': key,
                'existing_identical_object': existed, 'readback_sha256_verified': True,
                'retention': 'quarantine', 'nsl_admission': 'not_verified',
                'trusted_for_downstream': False, 'hosted_qualification': 'not_established_by_adapter'}


class SourceCollector:
    """Host-bound single-attempt collector with durable intent and no auto-retry.

    fetcher and optional quarantine store are host dependencies, not model
    arguments. Usage documents must match registered hashes; a current host
    review verifier must accept their exact references and expiry before any
    fetch. Unknown/held source use, stale reviews and robots refusal stop first.
    Failed calibration/freshness retains local evidence and blocks object writes.
    Neither local nor cloud retention produces TBRT authentication/NSL admission.
    """
    def __init__(self, ledger, *, source_id, source_ref, usage_documents, review,
                 verify_review, fetcher, observe, quarantine_store=None):
        if type(ledger) is not DataInstrumentLedger or not callable(verify_review) or not callable(observe):
            raise TypeError('host_ledger_review_and_observation_bindings_required')
        self.ledger, self.source_id, self.source_ref = ledger, _identifier(source_id), _ref(source_ref)
        self._review = parse(canonical(review))
        closed_object(self._review, ('source_ref', 'review_ref', 'checked_at', 'expires_at'))
        _ref(self._review['review_ref'])
        _instant(self._review['checked_at']); _instant(self._review['expires_at'])
        if self._review['source_ref'] != self.source_ref:
            raise CollectionRefusal('source_review_binding_mismatch')
        if type(usage_documents) is not dict or set(usage_documents) != {'license', 'terms', 'robots'}:
            raise CollectionRefusal('exact_usage_documents_required')
        if any(type(v) is not bytes or not 0 < len(v) <= 16384 for v in usage_documents.values()):
            raise CollectionRefusal('usage_document_budget')
        self._documents = dict(usage_documents)
        if getattr(fetcher, 'source_ref', None) != source_ref or not callable(getattr(fetcher, 'fetch', None)):
            raise CollectionRefusal('source_fetcher_binding_mismatch')
        if quarantine_store is not None and type(quarantine_store) is not R2QuarantineStore:
            raise CollectionRefusal('quarantine_store_binding_required')
        self._verify, self._fetcher, self._observe, self._store = verify_review, fetcher, observe, quarantine_store
        with ledger._transaction('collector_open', source_id) as db:
            db.execute('CREATE TABLE IF NOT EXISTS collection_attempts (source_id TEXT NOT NULL, '
                       'attempt TEXT NOT NULL, intent_ref TEXT NOT NULL, terminal_ref TEXT, PRIMARY KEY(source_id,attempt))')

    def _check(self):
        context = self.ledger._authorize('collect', self.source_id)
        with self.ledger.db.transaction() as db:
            source, _ = self.ledger._read_source(db, self.source_ref)
        if source['source_id'] != self.source_id:
            raise CollectionRefusal('source_binding_mismatch')
        self.ledger._usable(source)
        for kind, raw in self._documents.items():
            if content_ref(raw) != source[kind]['document_ref']:
                raise CollectionRefusal('usage_document_hash_mismatch')
        intent = {'source_ref': self.source_ref, 'review': self._review,
                  'document_refs': {k: content_ref(v) for k, v in self._documents.items()}}
        if self._verify(deepcopy(intent), deepcopy(context)) is not True:
            raise CollectionRefusal('verified_source_review_required')
        # Read the clock after potentially blocking review verification.
        now = _instant(self.ledger._clock())
        start, end = self._review['checked_at'], self._review['expires_at']
        if (now['frame'] != source['freshness']['frame'] or start['frame'] != now['frame']
                or end['frame'] != now['frame'] or not start['value'] <= now['value'] < end['value']):
            raise CollectionRefusal('source_review_expired_or_clock_mismatch')
        path = urlsplit(source['origin']).path or '/'
        if '%' in path or '\\' in path:
            raise CollectionRefusal('path_requires_review')
        robots_permission(self._documents['robots'], path, interval=source['minimum_interval_seconds'])
        self.ledger._current(context, 'collect', self.source_id)
        return context

    def collect(self, *, attempt):
        _identifier(attempt)
        identity = self._check()
        with self.ledger._transaction('collect', self.source_id) as db:
            if db.execute('SELECT 1 FROM collection_attempts WHERE source_id=? AND attempt=?', (self.source_id, attempt)).fetchone():
                raise CollectionRefusal('collection_attempt_recorded_inspect_no_retry')
        reservation = self.ledger.reserve_collection(self.source_id, self.source_ref)
        with self.ledger._transaction('collect', self.source_id) as db:
            if db.execute('SELECT 1 FROM collection_attempts WHERE source_id=? AND attempt=?', (self.source_id, attempt)).fetchone():
                raise CollectionRefusal('collection_attempt_recorded_inspect_no_retry')
            for raw in self._documents.values():
                self.ledger._blob(db, raw)
            intent = self.ledger._event(db, self.source_id, 'collection_intent', {
                'source_ref': self.source_ref, 'attempt': attempt, 'reservation_ref': reservation,
                'usage_review': self._review,
                'usage_document_refs': {name: content_ref(raw) for name, raw in self._documents.items()},
                'status': 'pending_request'})
            db.execute('INSERT INTO collection_attempts VALUES (?,?,?,NULL)', (self.source_id, attempt, intent))
        # Every failure after this durable intent leaves it inspectable. We never
        # repeat the HTTP request or cloud write under a fresh attempt identity.
        try:
            if self._check() != identity:
                raise CollectionRefusal('source_policy_changed')
            response = self._fetcher.fetch()
            if self._check() != identity:
                raise CollectionRefusal('source_policy_changed')
            if type(response) is not dict or response.get('source_ref') != self.source_ref:
                raise CollectionRefusal('http_source_binding_mismatch')
            raw = response.get('raw')
            observed = self._observe(deepcopy(response))
            if self._check() != identity:
                raise CollectionRefusal('source_policy_changed')
            captured = self.ledger.capture(self.source_id, self.source_ref, raw, attempt=attempt, observed=observed)
            assessment = self.ledger.assess(self.source_id, captured)
            storage = None
            ready = (assessment['hold_ref'] is None and assessment['checks'] == {
                'schema': 'within_contract', 'calibration': 'within_tolerance', 'freshness': 'within_nominal_window'})
            if ready and self._store is not None:
                if self._check() != identity:
                    raise CollectionRefusal('source_policy_changed')
                storage = self._store.retain(self.source_id, raw)
            if self._check() != identity:
                raise CollectionRefusal('source_policy_changed')
            result = {'capture_ref': captured, 'assessment_ref': assessment['assessment_ref'],
                      'status': 'retained_unadmitted' if ready else 'held', 'storage': storage,
                      'transport': response.get('transport', 'host_transport_unqualified'),
                      'timestamp_status': assessment['timestamp_status'], 'nsl_admission': 'not_verified',
                      'trusted_for_downstream': False}
            with self.ledger._transaction('collect', self.source_id) as db:
                self.ledger._current(identity, 'collect', self.source_id)
                terminal = self.ledger._event(db, self.source_id, 'collection_terminal', result)
                db.execute('UPDATE collection_attempts SET terminal_ref=? WHERE source_id=? AND attempt=?',
                           (terminal, self.source_id, attempt))
            return {'receipt_ref': terminal, **result}
        except Exception:
            raise CollectionOutcomeUnknown('collection_intent_retained_inspect_no_retry') from None

    def inspect(self, *, attempt):
        with self.ledger._transaction('read', self.source_id) as db:
            row = db.execute('SELECT intent_ref,terminal_ref FROM collection_attempts WHERE source_id=? AND attempt=?',
                             (self.source_id, _identifier(attempt))).fetchone()
            if row is None:
                raise CollectionRefusal('collection_attempt_unknown')
            result = {'intent_ref': row[0], 'terminal_ref': row[1],
                      'status': 'outcome_unknown' if row[1] is None else 'terminal_recorded', 'retry_authorized': False}
        return result
