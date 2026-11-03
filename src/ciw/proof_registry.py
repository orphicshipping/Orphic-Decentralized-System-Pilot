"""Host-owned, versioned Ed25519 trust anchors for bounded proof receipts.

A fingerprint is an identifier, not a source of trust. No request can add a key,
change a registry, or enable a revoked entry. This is not a certificate service.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import re
from threading import RLock

from .campaign_contracts import Limits, canonical, closed_object, integer, parse, text
from .legibility import key_fingerprint

REGISTRY_LIMITS = Limits(max_bytes=65536, max_depth=12, max_nodes=8192, max_array=64)
SCHEMA = 'odsp.proof-key-registry.v1'
SCOPE = ('program_id', 'circuit_id', 'source_revision', 'policy_id')


class ProofRefusal(ValueError):
    """Stable refusal without caller content or key material in the diagnostic."""


def hash_id(value):
    if type(value) is not str or re.fullmatch(r'sha256:[0-9a-f]{64}', value) is None:
        raise ProofRefusal('invalid_hash_identity')
    return value


def scope(value):
    hash_id(value['program_id'])
    hash_id(value['policy_id'])
    text(value['circuit_id'], 128)
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value['circuit_id']) is None:
        raise ProofRefusal('invalid_circuit_identity')
    if type(value['source_revision']) is not str or re.fullmatch(r'[0-9a-f]{40}', value['source_revision']) is None:
        raise ProofRefusal('invalid_source_revision')
    return tuple(value[name] for name in SCOPE)


def unbase64(value, *, size=None, maximum=65536):
    if type(value) is not str or len(value) > 4 * ((maximum + 2) // 3):
        raise ProofRefusal('invalid_base64')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError):
        raise ProofRefusal('invalid_base64') from None
    if (base64.b64encode(raw).decode('ascii') != value or not 0 < len(raw) <= maximum
            or (size is not None and len(raw) != size)):
        raise ProofRefusal('invalid_base64')
    return raw


@dataclass(frozen=True)
class TrustSelection:
    registry_id: str
    version: int
    digest: str
    entry_id: str
    key_id: str
    public_key: bytes


class ProofKeyRegistry:
    """Construct/replace from trusted launcher configuration, never tool input.

    Replacement requires the same registry identity and a strictly later version.
    Every verification resolves a fresh snapshot and checks it again before
    accepting. A concurrent replacement invalidates an in-flight acceptance.
    """

    def __init__(self, document):
        self._lock = RLock()
        self._document, self._entries, self._digest = self._validate(document)

    @staticmethod
    def _validate(document):
        document = parse(canonical(document, limits=REGISTRY_LIMITS), limits=REGISTRY_LIMITS)
        closed_object(document, ('schema', 'registry_id', 'version', 'entries'))
        if document['schema'] != SCHEMA:
            raise ProofRefusal('registry_schema_refused')
        text(document['registry_id'], 128)
        integer(document['version'], 1, 9007199254740991)
        if type(document['entries']) is not list or len(document['entries']) > 32:
            raise ProofRefusal('registry_capacity_refused')
        ids, bindings, entries = set(), set(), []
        for row in document['entries']:
            closed_object(row, ('entry_id', 'algorithm', *SCOPE, 'key_id',
                                'public_key_b64', 'subjects', 'enabled'))
            text(row['entry_id'], 128)
            if row['entry_id'] in ids or row['algorithm'] != 'Ed25519' or type(row['enabled']) is not bool:
                raise ProofRefusal('registry_entry_refused')
            ids.add(row['entry_id'])
            binding = scope(row)
            raw = unbase64(row['public_key_b64'], size=32, maximum=32)
            if row['key_id'] != key_fingerprint(raw):
                raise ProofRefusal('registry_key_mismatch')
            if type(row['subjects']) is not list or not 1 <= len(row['subjects']) <= 64:
                raise ProofRefusal('registry_subjects_refused')
            for subject in row['subjects']:
                text(subject, 512)
                key = (binding, row['key_id'], subject)
                if key in bindings:
                    raise ProofRefusal('registry_ambiguous_binding')
                bindings.add(key)
            entries.append((deepcopy(row), raw))
        digest = 'sha256:' + sha256(canonical(document, limits=REGISTRY_LIMITS)).hexdigest()
        return document, tuple(entries), digest

    def replace(self, document):
        checked, entries, digest = self._validate(document)
        with self._lock:
            if (checked['registry_id'] != self._document['registry_id']
                    or checked['version'] <= self._document['version']):
                raise ProofRefusal('registry_version_refused')
            self._document, self._entries, self._digest = checked, entries, digest

    def describe(self):
        with self._lock:
            return {'registry_id': self._document['registry_id'], 'version': self._document['version'],
                    'digest': self._digest, 'active_entries': sum(row['enabled'] for row, _ in self._entries),
                    'authority': 'host_selected_trust_snapshot', 'source_authentication': 'not_established'}

    def resolve(self, statement, key_id, subject):
        binding = scope(statement)
        with self._lock:
            for row, raw in self._entries:
                if (scope(row) == binding and row['key_id'] == key_id and subject in row['subjects']):
                    if not row['enabled']:
                        raise ProofRefusal('trust_key_disabled')
                    return TrustSelection(self._document['registry_id'], self._document['version'],
                                          self._digest, row['entry_id'], row['key_id'], raw)
        raise ProofRefusal('trust_binding_refused')

    def assert_current(self, selection):
        with self._lock:
            if (type(selection) is not TrustSelection or selection.digest != self._digest
                    or selection.registry_id != self._document['registry_id']
                    or selection.version != self._document['version']):
                raise ProofRefusal('trust_snapshot_changed')
