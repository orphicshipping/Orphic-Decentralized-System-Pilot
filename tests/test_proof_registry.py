"""Synthetic host-owned trust snapshots; no credential issuance or native proof."""
import base64
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ciw.campaign_contracts import ContractRefusal
from ciw.legibility import key_fingerprint
from ciw.proof_registry import ProofKeyRegistry, ProofRefusal, SCHEMA


def registry_document():
    # Public, deterministic test vector. Never used as a deployment credential.
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    entry = {'entry_id': 'synthetic-reviewer', 'algorithm': 'Ed25519',
             'program_id': 'sha256:' + '1' * 64, 'circuit_id': 'receipt-fixture.v1',
             'source_revision': '2' * 40, 'policy_id': 'sha256:' + '3' * 64,
             'key_id': key_fingerprint(public), 'public_key_b64': base64.b64encode(public).decode(),
             'subjects': ['fixture-owner'], 'enabled': True}
    return {'schema': SCHEMA, 'registry_id': 'synthetic-registry', 'version': 1, 'entries': [entry]}


def test_exact_scope_key_and_subject_are_required():
    document = registry_document()
    registry = ProofKeyRegistry(document)
    entry = document['entries'][0]
    selected = registry.resolve(entry, entry['key_id'], 'fixture-owner')
    registry.assert_current(selected)
    assert selected.version == 1
    assert registry.describe()['active_entries'] == 1
    for changed in ({'program_id': 'sha256:' + '4' * 64}, {'circuit_id': 'another.v1'},
                    {'source_revision': '5' * 40}, {'policy_id': 'sha256:' + '6' * 64}):
        with pytest.raises(ProofRefusal, match='trust_binding_refused'):
            registry.resolve({**entry, **changed}, entry['key_id'], 'fixture-owner')
    with pytest.raises(ProofRefusal):
        registry.resolve(entry, entry['key_id'], 'another-principal')
    with pytest.raises(ProofRefusal):
        registry.resolve(entry, 'ed25519:sha256:' + '0' * 64, 'fixture-owner')


def test_constructor_copies_host_data_and_reports_no_key_material():
    document = registry_document()
    registry = ProofKeyRegistry(document)
    entry = deepcopy(document['entries'][0])
    document['entries'][0]['enabled'] = False
    assert registry.resolve(entry, entry['key_id'], 'fixture-owner').entry_id == entry['entry_id']
    assert 'public_key_b64' not in str(registry.describe())


def test_replacement_revokes_inflight_and_future_selection():
    document = registry_document()
    registry = ProofKeyRegistry(document)
    entry = document['entries'][0]
    selection = registry.resolve(entry, entry['key_id'], 'fixture-owner')
    document['version'] = 2
    entry['enabled'] = False
    registry.replace(document)
    with pytest.raises(ProofRefusal, match='snapshot_changed'):
        registry.assert_current(selection)
    with pytest.raises(ProofRefusal, match='disabled'):
        registry.resolve(entry, entry['key_id'], 'fixture-owner')
    assert registry.describe()['active_entries'] == 0


@pytest.mark.parametrize('mutation', [
    {'version': 1}, {'version': 0}, {'version': True}, {'version': 1.0},
    {'registry_id': 'other-registry', 'version': 2},
])
def test_replacement_requires_monotonic_same_registry(mutation):
    document = registry_document()
    registry = ProofKeyRegistry(document)
    with pytest.raises((ProofRefusal, ContractRefusal)):
        registry.replace({**document, **mutation})
    assert registry.describe()['version'] == 1


@pytest.mark.parametrize('mutation', [
    {'algorithm': 'none'}, {'enabled': 1}, {'key_id': 'ed25519:sha256:' + '0' * 64},
    {'public_key_b64': 'AAAA'}, {'subjects': []}, {'subjects': ['fixture-owner', 'fixture-owner']},
    {'source_revision': 'main'}, {'program_id': 'sha256:' + 'G' * 64},
    {'circuit_id': '../../guest.elf'}, {'unknown_flag': True},
])
def test_invalid_registry_never_becomes_trust(mutation):
    document = registry_document()
    document['entries'][0].update(mutation)
    with pytest.raises((ProofRefusal, ContractRefusal)):
        ProofKeyRegistry(document)


def test_ambiguous_duplicate_binding_and_capacity_refuse():
    document = registry_document()
    document['entries'].append({**document['entries'][0], 'entry_id': 'same-binding'})
    with pytest.raises(ProofRefusal, match='ambiguous'):
        ProofKeyRegistry(document)
    document = registry_document()
    document['entries'] *= 33
    with pytest.raises(ProofRefusal, match='capacity'):
        ProofKeyRegistry(document)


def test_empty_registry_has_no_implicit_trust():
    document = registry_document()
    entry = document['entries'].pop()
    registry = ProofKeyRegistry(document)
    with pytest.raises(ProofRefusal, match='binding_refused'):
        registry.resolve(entry, entry['key_id'], 'fixture-owner')
