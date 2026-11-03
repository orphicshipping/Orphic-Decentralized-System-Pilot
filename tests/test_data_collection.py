"""Local approved fixtures and fake object storage; no external network/R2 calls.

Real stdlib HTTP parsing uses socketpair, not a live source or TLS authority.
Synthetic source/rights reviews do not establish rights or NSL admission.
"""
import base64
from copy import deepcopy
from hashlib import md5
import http.client
import io
import socket
from threading import Thread
import time
from types import SimpleNamespace

import pytest

from ciw.campaign_contracts import canonical
from ciw.data_collection import (CollectionOutcomeUnknown, CollectionRefusal,
                                 R2QuarantineStore, SourceCollector, SourceHTTPS,
                                 _ResponseSocket, robots_permission)
from ciw.data_instruments import DataInstrumentLedger, InstrumentRefusal, content_ref, version_ref
from ciw.principal_policy import PrincipalPolicy
from test_data_instruments import BASELINE, GOOD, BAD, FRAME, policy_document, source_document


USAGE = {'license': b'Synthetic CC0 fixture rights review only.',
         'terms': b'Synthetic fixture permits the explicit test intake.',
         'robots': b'User-agent: ODSP\nAllow: /places.json\nDisallow: /\nCrawl-delay: 10\n'}


class FixtureFetcher:
    def __init__(self, ref, raw=GOOD):
        self.source_ref, self.raw, self.calls = ref, raw, 0
        self.effect = lambda: None
    def fetch(self):
        self.calls += 1
        self.effect()
        return {'source_ref': self.source_ref, 'raw': self.raw,
                'transport': 'approved_local_fixture_no_network', 'http_status': 200}


class PreconditionFailure(Exception):
    response = {'ResponseMetadata': {'HTTPStatusCode': 412}}


class FixtureR2:
    """Fake conditional object semantics; endpoint metadata is not authentication."""
    def __init__(self):
        self.meta = SimpleNamespace(endpoint_url='https://' + 'a' * 32 + '.r2.cloudflarestorage.com',
            config=SimpleNamespace(signature_version='s3v4', connect_timeout=1, read_timeout=1,
                                   retries={'total_max_attempts': 1}))
        self.objects, self.puts, self.gets, self.bodies = {}, [], [], []
        self.after_put = lambda: None
        self.get_transform = lambda raw: raw
    def put_object(self, **request):
        self.puts.append(request)
        assert request['IfNoneMatch'] == '*'
        assert request['ContentMD5'] == base64.b64encode(md5(request['Body'], usedforsecurity=False).digest()).decode()
        if request['Key'] in self.objects:
            raise PreconditionFailure()
        self.objects[request['Key']] = request['Body']
        self.after_put()
        return {'ETag': 'opaque-not-a-content-digest'}
    def get_object(self, **request):
        self.gets.append(request)
        raw = self.get_transform(self.objects[request['Key']])
        body = io.BytesIO(raw)
        self.bodies.append(body)
        return {'Body': body, 'ContentLength': len(raw), 'ETag': 'opaque'}


def store_for(client, authorize=lambda _: True, **changes):
    return R2QuarantineStore(client, account_id='a' * 32, bucket='fixture-bucket',
                             tenant=changes.pop('tenant', 'fixture-tenant'),
                             principal='fixture-owner', authorize=authorize, **changes)


@pytest.fixture
def configured(tmp_path):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    state = {'now': 1000, 'allowed': True, 'policy': policy_document(), 'review_allowed': True}
    policy = PrincipalPolicy(lambda: state['policy'])
    def reopen():
        return DataInstrumentLedger(private / 'instrument.sqlite', tenant='fixture-tenant',
            principal='fixture-owner', policy=policy, admit=lambda *_: state['allowed'],
            clock=lambda: {'value': state['now'], 'frame': FRAME})
    ledger = reopen()
    def make(raw=GOOD, usage=None, document=None, storage=None, review=None, verify=None):
        docs = deepcopy(USAGE if usage is None else usage)
        source = deepcopy(source_document() if document is None else document)
        for name, value in docs.items():
            source[name]['document_ref'] = content_ref(value)
        ref = ledger.register_source(source, BASELINE)
        receipt = {'source_ref': ref, 'review_ref': content_ref(b'exact synthetic usage review'),
                   'checked_at': {'value': 990, 'frame': FRAME},
                   'expires_at': {'value': 2000, 'frame': FRAME}}
        if review is not None:
            receipt.update(review)
        fetcher = FixtureFetcher(ref, raw)
        verified = []
        def verifier(intent, context):
            verified.append(deepcopy(intent))
            return state['review_allowed'] and canonical(intent['review']) == canonical(receipt)
        collector = SourceCollector(ledger, source_id=source['source_id'], source_ref=ref,
            usage_documents=docs, review=receipt, verify_review=verify or verifier, fetcher=fetcher,
            observe=lambda _: {'value': state['now'], 'frame': FRAME}, quarantine_store=storage)
        return SimpleNamespace(collector=collector, fetcher=fetcher, source=source,
                               source_ref=ref, review=receipt, verified=verified)
    return SimpleNamespace(ledger=ledger, state=state, reopen=reopen, make=make)


def test_collection_retains_exact_review_intent_capture_and_unadmitted_result(configured):
    client = FixtureR2()
    item = configured.make(storage=store_for(client))
    result = item.collector.collect(attempt='fixture-1')
    assert item.fetcher.calls == 1
    assert result['status'] == 'retained_unadmitted'
    assert result['transport'] == 'approved_local_fixture_no_network'
    assert result['trusted_for_downstream'] is False and result['nsl_admission'] == 'not_verified'
    assert result['timestamp_status'] == 'host_observation_only'
    assert result['storage']['retention'] == 'quarantine'
    assert result['storage']['readback_sha256_verified'] is True
    assert configured.ledger.snapshot('places-fixture', result['capture_ref']) == GOOD
    state = item.collector.inspect(attempt='fixture-1')
    assert state['status'] == 'terminal_recorded' and state['retry_authorized'] is False
    intent = configured.ledger.receipt('places-fixture', state['intent_ref'])['body']
    assert intent['usage_review'] == item.review
    assert intent['usage_document_refs'] == {k: content_ref(v) for k, v in USAGE.items()}
    terminal = configured.ledger.receipt('places-fixture', result['receipt_ref'])
    assert terminal['authorizes_execution'] is terminal['authorizes_state_admission'] is False
    assert len(client.puts) == len(client.gets) == 1 and all(b.closed for b in client.bodies)
    assert client.puts[0]['Metadata']['odsp-trust'] == 'quarantined'
    assert item.verified and all(i['source_ref'] == item.source_ref for i in item.verified)


def test_attempt_identity_survives_restart_and_rate_limit_stops_another_fetch(configured):
    first = configured.make()
    first.collector.collect(attempt='first')
    reopened = configured.make()
    reopened.collector.ledger = configured.reopen()
    with pytest.raises(CollectionRefusal, match='attempt_recorded'):
        reopened.collector.collect(attempt='first')
    with pytest.raises(InstrumentRefusal, match='rate_limited'):
        reopened.collector.collect(attempt='second')
    assert reopened.fetcher.calls == 0
    configured.state['now'] += 10
    assert reopened.collector.collect(attempt='second')['status'] == 'retained_unadmitted'
    assert reopened.fetcher.calls == 1


@pytest.mark.parametrize('raw', [BAD, b'{', b'[]'])
def test_drifted_capture_is_retained_locally_and_never_uploaded(configured, raw):
    client = FixtureR2()
    item = configured.make(raw=raw, storage=store_for(client))
    result = item.collector.collect(attempt='drifted')
    assert result['status'] == 'held' and result['storage'] is None
    assert configured.ledger.snapshot('places-fixture', result['capture_ref']) == raw
    assert configured.ledger.inspect_capture('places-fixture', result['capture_ref'])['assessment']['hold_ref']
    assert client.puts == client.gets == []


@pytest.mark.parametrize('change,reason', [
    ('review', 'verified_source_review'), ('document', 'usage_document_hash'),
    ('expired', 'source_review_expired'), ('robots', 'robots_disallowed'),
    ('license', 'source_usage_held')])
def test_source_reviews_and_robots_stop_before_intent_fetch_or_storage(configured, change, reason):
    usage, source, review = deepcopy(USAGE), source_document(), None
    if change == 'robots':
        usage['robots'] = b'User-agent: ODSP\nDisallow: /\n'
    if change == 'license':
        source['license']['status'] = 'held'
    if change == 'expired':
        review = {'expires_at': {'value': 1000, 'frame': FRAME}}
    item = configured.make(usage=usage, document=source, review=review)
    if change == 'review':
        configured.state['review_allowed'] = False
    if change == 'document':
        item.collector._documents['terms'] = b'changed unreviewed fixture'
    before = configured.ledger.verify_chain()
    with pytest.raises((CollectionRefusal, InstrumentRefusal), match=reason):
        item.collector.collect(attempt='refused')
    assert item.fetcher.calls == 0
    assert configured.ledger.verify_chain() == before


def test_blocking_review_expiry_stops_before_fetch(configured):
    def verify(*_):
        configured.state['now'] = 2000
        return True
    item = configured.make(verify=verify)
    with pytest.raises(CollectionRefusal, match='source_review_expired'):
        item.collector.collect(attempt='expired-during-review')
    assert item.fetcher.calls == 0


@pytest.mark.parametrize('effect', ['policy', 'source', 'exception'])
def test_post_request_change_retains_intent_without_retry_or_cloud_write(configured, effect):
    client = FixtureR2()
    item = configured.make(storage=store_for(client))
    def changed():
        if effect == 'policy':
            configured.state['allowed'] = False
        elif effect == 'source':
            source = deepcopy(item.source)
            source['version'] = 2
            configured.ledger.register_source(source, BASELINE)
        else:
            raise TimeoutError('fixture request may have completed')
    item.fetcher.effect = changed
    with pytest.raises(CollectionOutcomeUnknown, match='inspect_no_retry'):
        item.collector.collect(attempt='unknown')
    configured.state['allowed'] = True
    assert item.fetcher.calls == 1 and client.puts == []
    pending = item.collector.inspect(attempt='unknown')
    assert pending['status'] == 'outcome_unknown' and pending['terminal_ref'] is None
    if effect != 'source':
        with pytest.raises(CollectionRefusal, match='attempt_recorded'):
            item.collector.collect(attempt='unknown')
    assert item.fetcher.calls == 1


def test_stale_observation_never_uploads(configured):
    client = FixtureR2()
    item = configured.make(storage=store_for(client))
    item.collector._observe = lambda _: {'value': 1, 'frame': FRAME}
    result = item.collector.collect(attempt='stale')
    assert result['status'] == 'held' and client.puts == []


def test_robots_exact_agent_longest_prefix_and_ties():
    assert robots_permission(USAGE['robots'], '/places.json', interval=10)
    rules = b'User-agent: *\nDisallow: /\nUser-agent: ODSP\nAllow: /a\nDisallow: /a/private\nAllow: /a/private\n'
    assert robots_permission(rules, '/a/private', interval=10)
    assert robots_permission(rules, '/a/open', interval=10)
    with pytest.raises(CollectionRefusal, match='disallowed'):
        robots_permission(b'User-agent: *\nDisallow: /\n', '/any', interval=10)


@pytest.mark.parametrize('raw,reason', [
    (b'User-agent: ODSP\nDisallow: /*.json\n', 'requires_review'),
    (b'User-agent: ODSP\nDisallow: /encoded%2Fpath\n', 'requires_review'),
    (b'User-agent: ODSP\nX-Unknown: allow\n', 'requires_review'),
    (b'User-agent: Other\nDisallow: /\n', 'matching_group'),
    (b'User-agent: ODSP\nCrawl-delay: 11\n', 'below_robots_delay'),
    (b'not a rule', 'syntax'), (b'\xff', 'encoding')])
def test_robots_unsupported_and_stricter_rules_require_review(raw, reason):
    with pytest.raises(CollectionRefusal, match=reason):
        robots_permission(raw, '/places.json', interval=10)


def test_source_https_has_fixed_public_destination_and_honest_identity():
    source = source_document()
    fixed = SourceHTTPS(source, approved_address='93.184.216.34')
    assert fixed.source_ref == version_ref(source)
    assert fixed._agent == 'ODSP/1.0 (contact=https://example.invalid/contact)'
    with pytest.raises(TypeError):
        fixed.fetch(url='https://caller.invalid')
    for ip in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '192.0.2.1'):
        with pytest.raises(CollectionRefusal, match='public_literal'):
            SourceHTTPS(source, approved_address=ip)
    for origin in ('http://example.invalid', 'https://secret@example.invalid',
                   'https://example.invalid/a?secret=1', 'https://example.invalid/a%2Fprivate'):
        bad = deepcopy(source); bad['origin'] = origin
        with pytest.raises(CollectionRefusal):
            SourceHTTPS(bad, approved_address='93.184.216.34')


def response_from_bytes(raw):
    reader, writer = socket.socketpair()
    reader.settimeout(.1)
    writer.sendall(raw)
    writer.shutdown(socket.SHUT_WR)
    response = http.client.HTTPResponse(_ResponseSocket(reader, time.monotonic() + .1))
    response.begin()
    return response, reader, writer


def test_real_http_parser_accepts_exact_json_bytes_without_network():
    response, reader, writer = response_from_bytes(
        b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(GOOD)).encode() + b'\r\n\r\n' + GOOD)
    try:
        assert SourceHTTPS._read_response(response, max_bytes=262144, deadline=time.monotonic() + .1,
                                           set_timeout=reader.settimeout) == GOOD
    finally:
        response.close(); reader.close(); writer.close()


@pytest.mark.parametrize('header,body,reason', [
    (b'HTTP/1.1 302 Redirect\r\nLocation: https://other.invalid\r\nContent-Type: application/json\r\nContent-Length: 2\r\n', b'[]', 'status'),
    (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n', b'[]', 'duplicate_header'),
    (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: 2\r\n', b'[]', 'encoding'),
    (b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 2\r\n', b'[]', 'content_type'),
    (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n', b'[]', 'length'),
    (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 262145\r\n', b'[]', 'length'),
    (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 3\r\n', b'[]', 'truncated'),
    (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n', b'[]', 'transfer_encoding')])
def test_real_http_parser_refuses_redirect_duplicate_unbounded_or_malformed_transfer(header, body, reason):
    response, reader, writer = response_from_bytes(header + b'\r\n' + body)
    try:
        with pytest.raises(CollectionRefusal, match=reason):
            SourceHTTPS._read_response(response, max_bytes=262144, deadline=time.monotonic() + .1,
                                       set_timeout=reader.settimeout)
    finally:
        response.close(); reader.close(); writer.close()


def test_real_http_body_wait_is_bounded():
    reader, writer = socket.socketpair()
    writer.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n')
    response = http.client.HTTPResponse(_ResponseSocket(reader, time.monotonic() + .1))
    response.begin()
    start = time.monotonic()
    try:
        with pytest.raises((TimeoutError, CollectionRefusal)):
            SourceHTTPS._read_response(response, max_bytes=262144, deadline=start + .05,
                                       set_timeout=reader.settimeout)
        assert time.monotonic() - start < 1
    finally:
        response.close(); reader.close(); writer.close()


def test_trickled_http_headers_share_one_deadline():
    reader, writer = socket.socketpair()
    stop = []
    def trickle():
        try:
            for value in b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n':
                writer.send(bytes([value]))
                time.sleep(.01)
        except OSError:
            pass
        finally:
            stop.append(True)
    thread = Thread(target=trickle)
    start = time.monotonic()
    response = http.client.HTTPResponse(_ResponseSocket(reader, start + .05))
    thread.start()
    try:
        with pytest.raises((TimeoutError, CollectionRefusal)):
            response.begin()
        assert time.monotonic() - start < .5
    finally:
        response.close(); reader.close(); writer.close(); thread.join(timeout=1)
    assert stop and not thread.is_alive()


def test_header_budget_is_enforced_before_http_parser_accepts_large_line():
    reader, writer = socket.socketpair()
    writer.sendall(b'HTTP/1.1 200 OK\r\nX-Large: ' + b'x' * 8192)
    response = http.client.HTTPResponse(_ResponseSocket(reader, time.monotonic() + .1))
    try:
        with pytest.raises(CollectionRefusal, match='header_budget'):
            response.begin()
    finally:
        response.close(); reader.close(); writer.close()


def test_conditional_r2_duplicate_verifies_original_without_overwrite():
    client = FixtureR2()
    store = store_for(client)
    first = store.retain('places-fixture', GOOD)
    second = store.retain('places-fixture', GOOD)
    assert first['existing_identical_object'] is False and second['existing_identical_object'] is True
    assert first['key'] == second['key'] and len(client.objects) == 1
    assert second['trusted_for_downstream'] is False
    assert all(body.closed for body in client.bodies)
    assert len(client.puts) == len(client.gets) == 2
    isolated = store_for(FixtureR2(), tenant='other-tenant')
    assert isolated.retain('places-fixture', GOOD)['key'] != first['key']


@pytest.mark.parametrize('change', ['endpoint', 'signature', 'retries', 'truthy_retries', 'timeout'])
def test_r2_client_must_bind_exact_endpoint_and_bounded_single_attempt(change):
    client = FixtureR2()
    if change == 'endpoint': client.meta.endpoint_url = 'https://attacker.invalid'
    if change == 'signature': client.meta.config.signature_version = 'unsigned'
    if change == 'retries': client.meta.config.retries = {'total_max_attempts': 2}
    if change == 'truthy_retries': client.meta.config.retries = {'total_max_attempts': True}
    if change == 'timeout': client.meta.config.read_timeout = None
    with pytest.raises(CollectionRefusal, match='bounded_exact'):
        store_for(client)
    assert client.puts == client.gets == []


def test_r2_denial_before_write_and_revocation_after_write_are_distinct():
    client = FixtureR2()
    state = {'allowed': False}
    store = store_for(client, lambda _: state['allowed'])
    with pytest.raises(CollectionRefusal, match='authorization'):
        store.retain('places-fixture', GOOD)
    assert not client.objects
    state['allowed'] = True
    client.after_put = lambda: state.update(allowed=False)
    with pytest.raises(CollectionOutcomeUnknown, match='readback_or_authority'):
        store.retain('places-fixture', GOOD)
    assert len(client.objects) == len(client.puts) == 1 and client.gets == []


@pytest.mark.parametrize('mode', ['timeout_committed', 'corrupt_readback', 'conflicting_existing'])
def test_r2_possible_commit_or_unconfirmed_content_never_retries_or_claims_success(mode):
    client = FixtureR2()
    store = store_for(client)
    if mode == 'timeout_committed':
        def timeout(): raise TimeoutError('synthetic uncertain upload')
        client.after_put = timeout
    elif mode == 'corrupt_readback':
        client.get_transform = lambda _: b'x' * len(GOOD)
    else:
        client.objects[store._key('places-fixture', content_ref(GOOD))] = BAD
    with pytest.raises(CollectionOutcomeUnknown):
        store.retain('places-fixture', GOOD)
    assert len(client.puts) == 1
    if mode != 'timeout_committed':
        assert len(client.gets) == 1 and all(body.closed for body in client.bodies)


def test_collector_preserves_local_capture_when_storage_outcome_is_unknown(configured):
    client = FixtureR2()
    def fail(): raise TimeoutError('fixture put may have completed')
    client.after_put = fail
    item = configured.make(storage=store_for(client))
    with pytest.raises(CollectionOutcomeUnknown, match='inspect_no_retry'):
        item.collector.collect(attempt='storage-unknown')
    assert item.fetcher.calls == len(client.puts) == 1
    assert item.collector.inspect(attempt='storage-unknown')['status'] == 'outcome_unknown'
    with configured.ledger.db.transaction() as db:
        ref = db.execute('SELECT receipt_ref FROM instrument_attempts WHERE attempt=?', ('storage-unknown',)).fetchone()[0]
    assert configured.ledger.snapshot('places-fixture', ref) == GOOD
    with pytest.raises(CollectionRefusal, match='attempt_recorded'):
        item.collector.collect(attempt='storage-unknown')
    assert item.fetcher.calls == len(client.puts) == 1
