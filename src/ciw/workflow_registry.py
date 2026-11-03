"""Immutable persisted workflow versions verified against current host trust.

SPDX-License-Identifier: AGPL-3.0-or-later
Publication and trust configuration are launcher APIs, never MCP operations.
Signatures attribute a definition; they do not admit its execution or establish
scientific truth. Every step still needs current canonical grants and admission.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import re
from threading import RLock
import time

from .campaign_contracts import Limits, canonical, closed_object, integer, parse, text
from .persistent_cache import PrivateSQLite
from .proof_registry import unbase64
from .toolbox_skills import SkillRegistry, digest, ident

ENVELOPE = 'odsp.signed-workflow.v1'
PAYLOAD = 'odsp.workflow-definition.v1'
TRUST = 'odsp.workflow-trust.v1'
LIMITS = Limits(max_bytes=32768, max_depth=24, max_nodes=8192)
TRUST_LIMITS = Limits(max_bytes=65536, max_depth=12, max_nodes=8192)
DOMAIN = b'ODSP signed workflow v1\x00'


class WorkflowRefusal(ValueError):
    pass


def signing_bytes(payload, key_id):
    """Public signing contract; no private key is generated or retained here."""
    text(key_id, 80)
    return DOMAIN + canonical({'key_id': key_id, 'payload': payload}, limits=LIMITS)


def public_key_id(raw):
    if type(raw) is not bytes or len(raw) != 32:
        raise WorkflowRefusal('workflow_public_key_refused')
    return 'sha256:' + sha256(raw).hexdigest()


def _trust(document):
    value = parse(canonical(document, limits=TRUST_LIMITS), limits=TRUST_LIMITS)
    closed_object(value, ('schema', 'registry_id', 'generation', 'keys', 'revoked_digests'))
    if value['schema'] != TRUST:
        raise WorkflowRefusal('workflow_trust_schema')
    ident(value['registry_id'])
    integer(value['generation'], 1, 2**53 - 1)
    if type(value['keys']) is not list or len(value['keys']) > 32:
        raise WorkflowRefusal('workflow_trust_budget')
    keys = {}
    for row in value['keys']:
        closed_object(row, ('key_id', 'public_key_b64', 'enabled', 'tenants', 'workflows', 'minimum_revision'))
        raw = unbase64(row['public_key_b64'], size=32, maximum=32)
        if row['key_id'] != public_key_id(raw) or row['key_id'] in keys or type(row['enabled']) is not bool:
            raise WorkflowRefusal('workflow_trust_key')
        for field in ('tenants', 'workflows'):
            values = row[field]
            if (type(values) is not list or not 1 <= len(values) <= 128
                    or any(type(v) is not str for v in values) or len(values) != len(set(values))):
                raise WorkflowRefusal('workflow_trust_scope')
            for item in values:
                ident(item)
        integer(row['minimum_revision'], 1, 2**53 - 1)
        keys[row['key_id']] = (row, raw)
    revoked = value['revoked_digests']
    if (type(revoked) is not list or len(revoked) > 256
            or any(type(v) is not str or re.fullmatch('[0-9a-f]{64}', v) is None for v in revoked)):
        raise WorkflowRefusal('workflow_revocations')
    return value, keys, sha256(canonical(value, limits=TRUST_LIMITS)).hexdigest()


class WorkflowRegistry:
    """SQLite persistence with independently supplied current signing trust.

    The trusted reader must load durable launcher configuration on each call.
    Generation regression/reuse is refused within this process. Protection
    against restoring both a database and host trust configuration requires an
    external monotonic trust anchor; SQLite alone cannot establish that claim.
    The signer minimum_revision and explicit revoked digests survive restarts
    when the launcher persists its trust document independently of this store.
    """
    def __init__(self, path, read_trust, *, clock=time.time, max_versions=128):
        if not callable(read_trust) or not callable(clock):
            raise TypeError('host_workflow_trust_required')
        integer(max_versions, 1, 1024)
        self._reader, self._clock, self._maximum = read_trust, clock, max_versions
        self._lock = RLock()
        self._last = None
        self.db = PrivateSQLite(path)
        with self.db.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workflow_config '
                       '(singleton INTEGER PRIMARY KEY CHECK(singleton=1), maximum INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO workflow_config VALUES (1,?)', (max_versions,))
            if db.execute('SELECT maximum FROM workflow_config').fetchall() != [(max_versions,)]:
                raise WorkflowRefusal('workflow_configuration_changed')
            db.execute('CREATE TABLE IF NOT EXISTS workflow_versions '
                       '(tenant TEXT NOT NULL, workflow TEXT NOT NULL, version TEXT NOT NULL, '
                       'envelope BLOB NOT NULL, digest TEXT NOT NULL, '
                       'PRIMARY KEY(tenant,workflow,version))')
        self._snapshot()

    def _snapshot(self):
        with self._lock:
            value, keys, checksum = _trust(self._reader())
            identity = (value['registry_id'], value['generation'], checksum)
            if self._last and (identity[0] != self._last[0] or identity[1] < self._last[1]
                               or identity[1] == self._last[1] and identity[2] != self._last[2]):
                raise WorkflowRefusal('workflow_trust_regression')
            self._last = identity
            return value, keys, identity

    def _assert_current(self, identity):
        if self._snapshot()[2] != identity:
            raise WorkflowRefusal('workflow_trust_changed')

    def _assert_validity(self, payload):
        now = self._clock()
        if type(now) not in (int, float) or not payload['not_before'] <= now < payload['expires_at']:
            raise WorkflowRefusal('workflow_validity_window')

    def assert_current(self, checked):
        """Recheck a detached verified envelope after host policy or audit I/O.

        This host-only consistency check grants no execution permission. It
        binds the exact trust generation and signed bytes previously checked;
        even a benign trust update requires a new request.
        """
        snapshot = self._snapshot()
        if (snapshot[2][1] != checked['trust_generation']
                or snapshot[2][2] != checked['trust_sha256']):
            raise WorkflowRefusal('workflow_trust_changed')
        current = self._verify(checked['envelope'], snapshot)
        if (current['workflow_sha256'] != checked['workflow_sha256']
                or current['skill_sha256'] != checked['skill_sha256']):
            raise WorkflowRefusal('workflow_definition_changed')
        self._assert_current(snapshot[2])
        self._assert_validity(current['envelope']['payload'])

    def _verify(self, envelope, snapshot):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        value = parse(canonical(envelope, limits=LIMITS), limits=LIMITS)
        closed_object(value, ('schema', 'algorithm', 'key_id', 'payload', 'signature_b64'))
        if value['schema'] != ENVELOPE or value['algorithm'] != 'Ed25519':
            raise WorkflowRefusal('workflow_signature_contract')
        payload = value['payload']
        closed_object(payload, ('schema', 'tenant', 'definition', 'revision', 'not_before', 'expires_at',
                               'pins', 'tool_schemas'))
        if payload['schema'] != PAYLOAD:
            raise WorkflowRefusal('workflow_definition_contract')
        ident(payload['tenant'])
        integer(payload['revision'], 1, 2**53 - 1)
        for name in ('not_before', 'expires_at'):
            integer(payload[name], 0, 2**53 - 1)
        self._assert_validity(payload)
        if (type(payload['pins']) is not dict or not 1 <= len(payload['pins']) <= 16
                or any(type(k) is not str or type(v) is not str or not 1 <= len(v) <= 512
                       for k, v in payload['pins'].items())):
            raise WorkflowRefusal('workflow_pins_required')
        # This validates the actual canonical recipes and selectors; no code,
        # dynamic imports, paths or download locations are part of a definition.
        definition = payload['definition']
        registry = SkillRegistry([definition])
        compiled = registry.describe(definition['skill_id'], definition['version'])
        names = {step['tool'] for step in compiled['definition']['steps']}
        schemas = payload['tool_schemas']
        if (type(schemas) is not dict or set(schemas) != names
                or any(name.startswith(('ncp_toolbox_', 'ncp_skill_')) for name in names)
                or any(type(v) is not str or re.fullmatch('[0-9a-f]{64}', v) is None for v in schemas.values())):
            raise WorkflowRefusal('workflow_tool_schema_contract')
        # A signer may pin a narrower host schema than the generic source
        # catalog. Actual invocation compares these signed hashes to the
        # current principal-visible schemas; signature verification alone does
        # not establish runtime compatibility.
        for step in compiled['definition']['steps']:
            step['schema_sha256'] = schemas[step['tool']]
        compiled['skill_sha256'] = digest(compiled['definition'])
        trust, keys, identity = snapshot
        selected = keys.get(value['key_id'])
        if selected is None:
            raise WorkflowRefusal('workflow_signer_untrusted')
        row, public = selected
        if (not row['enabled'] or payload['tenant'] not in row['tenants']
                or definition['skill_id'] not in row['workflows']
                or payload['revision'] < row['minimum_revision']):
            raise WorkflowRefusal('workflow_signer_scope_or_revocation')
        checksum = sha256(signing_bytes(payload, value['key_id'])).hexdigest()
        if checksum in trust['revoked_digests']:
            raise WorkflowRefusal('workflow_revoked')
        signature = unbase64(value['signature_b64'], size=64, maximum=64)
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(signature, signing_bytes(payload, value['key_id']))
        except InvalidSignature:
            raise WorkflowRefusal('workflow_signature_invalid') from None
        return {'envelope': value, 'workflow_sha256': checksum, 'trust_generation': identity[1],
                'trust_sha256': identity[2], 'skill_sha256': compiled['skill_sha256'],
                'authorizes_execution': False}

    def publish(self, envelope):
        """Trusted administrative import of already signed bytes, not MCP."""
        snapshot = self._snapshot()
        checked = self._verify(envelope, snapshot)
        value = checked['envelope']
        payload, definition = value['payload'], value['payload']['definition']
        key = (payload['tenant'], definition['skill_id'], definition['version'])
        raw = canonical(value, limits=LIMITS)
        with self.db.transaction() as db:
            self._assert_current(snapshot[2])
            old = db.execute('SELECT envelope FROM workflow_versions WHERE tenant=? AND workflow=? AND version=?', key).fetchone()
            if old:
                if old[0] != raw:
                    raise WorkflowRefusal('workflow_version_immutable')
            else:
                if db.execute('SELECT COUNT(*) FROM workflow_versions').fetchone()[0] >= self._maximum:
                    raise WorkflowRefusal('workflow_registry_capacity')
                db.execute('INSERT INTO workflow_versions VALUES (?,?,?,?,?)', (*key, raw, checked['workflow_sha256']))
            self._assert_current(snapshot[2])
        self._assert_current(snapshot[2])
        self._assert_validity(payload)
        return {k: v for k, v in checked.items() if k != 'envelope'}

    def get(self, tenant, workflow_id, version):
        ident(tenant)
        ident(workflow_id)
        if type(version) is not str or re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}', version) is None:
            raise WorkflowRefusal('workflow_version_refused')
        snapshot = self._snapshot()
        with self.db.connect() as db:
            row = db.execute('SELECT envelope,digest FROM workflow_versions '
                             'WHERE tenant=? AND workflow=? AND version=?', (tenant, workflow_id, version)).fetchone()
        if row is None:
            raise WorkflowRefusal('workflow_not_registered')
        if type(row[0]) is not bytes or len(row[0]) > LIMITS.max_bytes:
            raise WorkflowRefusal('workflow_persisted_contract')
        checked = self._verify(parse(row[0], limits=LIMITS), snapshot)
        payload = checked['envelope']['payload']
        definition = payload['definition']
        if ((payload['tenant'], definition['skill_id'], definition['version']) != (tenant, workflow_id, version)
                or checked['workflow_sha256'] != row[1]):
            raise WorkflowRefusal('workflow_persisted_binding')
        self._assert_current(snapshot[2])
        self._assert_validity(payload)
        return deepcopy(checked)

    def list(self, tenant, *, offset=0, limit=16):
        ident(tenant)
        integer(offset, 0, self._maximum)
        integer(limit, 1, 64)
        snapshot = self._snapshot()
        with self.db.connect() as db:
            rows = db.execute('SELECT workflow,version FROM workflow_versions WHERE tenant=? '
                              'ORDER BY workflow,version LIMIT ? OFFSET ?', (tenant, limit + 1, offset)).fetchall()
        items, payloads = [], []
        for workflow, version in rows[:limit]:
            checked = self.get(tenant, workflow, version)
            self._assert_current(snapshot[2])
            payloads.append(checked['envelope']['payload'])
            items.append({'workflow_id': workflow, 'version': version,
                          'workflow_sha256': checked['workflow_sha256'],
                          'trust_generation': checked['trust_generation']})
        self._assert_current(snapshot[2])
        for payload in payloads:
            self._assert_validity(payload)
        return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None,
                'authorizes_execution': False}
