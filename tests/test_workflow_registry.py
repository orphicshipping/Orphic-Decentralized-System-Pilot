"""Real Ed25519 verification and persisted registry versions, including restart."""
import base64
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import sqlite3

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from ciw.toolbox_skills import SkillRegistry
from ciw.workflow_registry import WorkflowRegistry, WorkflowRefusal, signing_bytes, public_key_id


def signed(definition=None, *, tenant='lab', pins=None):
    # Public, fixed test fixture only; never an operational signing credential.
    key = Ed25519PrivateKey.from_private_bytes(b'ODSP fixture workflow signing!!!'[:32].ljust(32, b'.'))
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = public_key_id(public)
    definition = definition or {'schema': 'ncp.client-skill.v1', 'skill_id': 'inspect', 'version': '1.0.0',
        'description': 'Read local fixture reference metadata.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'steps': [{'step_id': 'read', 'tool': 'ncp_knowledge_overview', 'arguments': {}}]}
    compiled = SkillRegistry([definition]).describe(definition['skill_id'], definition['version'])['definition']
    payload = {'schema': 'odsp.workflow-definition.v1', 'tenant': tenant, 'definition': deepcopy(definition),
        'revision': 1, 'not_before': 0, 'expires_at': 4102444800,
        'pins': pins or {'source': 'fixture-v1'},
        'tool_schemas': {s['tool']: s['schema_sha256'] for s in compiled['steps']}}
    envelope = {'schema': 'odsp.signed-workflow.v1', 'algorithm': 'Ed25519', 'key_id': key_id,
        'payload': payload, 'signature_b64': base64.b64encode(key.sign(signing_bytes(payload, key_id))).decode()}
    trust = {'schema': 'odsp.workflow-trust.v1', 'registry_id': 'test-signers', 'generation': 1,
        'keys': [{'key_id': key_id, 'public_key_b64': base64.b64encode(public).decode(), 'enabled': True,
                  'tenants': [tenant], 'workflows': [definition['skill_id']], 'minimum_revision': 1}], 'revoked_digests': []}
    return envelope, trust, key


def _restart(path, trust_path, queue):
    try:
        registry = WorkflowRegistry(path, lambda: json.loads(Path(trust_path).read_text()))
        queue.put(registry.get('lab', 'inspect', '1.0.0')['workflow_sha256'])
    except Exception as error:
        queue.put(type(error).__name__ + ':' + str(error))


def setup(tmp_path):
    tmp_path.chmod(0o700)
    envelope, trust, key = signed()
    registry = WorkflowRegistry(tmp_path / 'workflows.sqlite', lambda: trust)
    return registry, envelope, trust, key


def test_signed_version_persists_across_fresh_process_and_identical_publish(tmp_path):
    registry, envelope, trust, _ = setup(tmp_path)
    published = registry.publish(envelope)
    assert registry.publish(envelope) == published
    trust_path = tmp_path / 'trust.json'
    trust_path.write_text(json.dumps(trust))
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    child = ctx.Process(target=_restart, args=(str(registry.db.path), str(trust_path), queue))
    child.start()
    assert queue.get(timeout=15) == published['workflow_sha256']
    child.join(15)
    assert child.exitcode == 0
    assert registry.list('lab')['items'][0]['workflow_sha256'] == published['workflow_sha256']
    assert published['authorizes_execution'] is False


def test_actual_ed25519_tamper_unknown_signer_scope_expiry_and_immutable_version(tmp_path):
    registry, envelope, trust, key = setup(tmp_path)
    registry.publish(envelope)
    altered = deepcopy(envelope)
    altered['payload']['definition']['description'] = 'Altered payload'
    with pytest.raises(WorkflowRefusal, match='signature_invalid'):
        registry.publish(altered)
    altered['signature_b64'] = base64.b64encode(key.sign(signing_bytes(altered['payload'], altered['key_id']))).decode()
    with pytest.raises(WorkflowRefusal, match='immutable'):
        registry.publish(altered)
    for change, reason in [({'key_id': 'sha256:' + '0' * 64}, 'untrusted'),
                           ({'algorithm': 'none'}, 'signature_contract')]:
        with pytest.raises(WorkflowRefusal, match=reason):
            registry.publish({**envelope, **change})
    with pytest.raises(WorkflowRefusal, match='validity_window'):
        WorkflowRegistry(registry.db.path, lambda: trust, clock=lambda: 4102444800).get('lab', 'inspect', '1.0.0')
    with pytest.raises(WorkflowRefusal, match='not_registered'):
        registry.get('another', 'inspect', '1.0.0')


@pytest.mark.parametrize('kind', ['key', 'digest', 'minimum'])
def test_current_revocation_survives_reopen(tmp_path, kind):
    registry, envelope, trust, _ = setup(tmp_path)
    checksum = registry.publish(envelope)['workflow_sha256']
    trust['generation'] += 1
    if kind == 'key':
        trust['keys'][0]['enabled'] = False
    elif kind == 'digest':
        trust['revoked_digests'] = [checksum]
    else:
        trust['keys'][0]['minimum_revision'] = 2
    for instance in (registry, WorkflowRegistry(registry.db.path, lambda: trust)):
        with pytest.raises(WorkflowRefusal, match='revocation|revoked'):
            instance.get('lab', 'inspect', '1.0.0')


def test_trust_regression_generation_reuse_and_unavailable_reader_refuse(tmp_path):
    registry, envelope, trust, _ = setup(tmp_path)
    registry.publish(envelope)
    trust['generation'] = 2
    registry.get('lab', 'inspect', '1.0.0')
    trust['generation'] = 1
    with pytest.raises(WorkflowRefusal, match='regression'):
        registry.get('lab', 'inspect', '1.0.0')
    trust['generation'] = 2
    trust['keys'][0]['enabled'] = False
    with pytest.raises(WorkflowRefusal, match='regression'):
        registry.get('lab', 'inspect', '1.0.0')


def test_list_cannot_mix_trust_generations_or_disclose_an_earlier_revoked_row(tmp_path):
    registry, first, trust, _ = setup(tmp_path)
    checksum = registry.publish(first)['workflow_sha256']
    definition = deepcopy(first['payload']['definition'])
    definition['version'] = '2.0.0'
    second, _, _ = signed(definition)
    registry.publish(second)
    reads = []
    def current():
        reads.append(1)
        changed = deepcopy(trust)
        if len(reads) >= 3:
            changed['generation'] += 1
            changed['revoked_digests'] = [checksum]
        return changed
    registry._reader = current
    with pytest.raises(WorkflowRefusal, match='changed|revoked'):
        registry.list('lab')


def test_database_tampering_and_signature_valid_row_rebinding_refused(tmp_path):
    registry, envelope, _, _ = setup(tmp_path)
    registry.publish(envelope)
    with sqlite3.connect(registry.db.path) as db:
        db.execute('UPDATE workflow_versions SET workflow=?', ('different',))
    with pytest.raises(WorkflowRefusal, match='persisted_binding'):
        registry.get('lab', 'different', '1.0.0')
    with sqlite3.connect(registry.db.path) as db:
        db.execute('UPDATE workflow_versions SET envelope=?', (b'{"schema":"none"}',))
    with pytest.raises(ValueError):
        registry.get('lab', 'different', '1.0.0')


def test_revocation_between_verification_and_disclosure_refuses(tmp_path):
    registry, envelope, trust, _ = setup(tmp_path)
    registry.publish(envelope)
    reads = []
    def current():
        reads.append(1)
        if len(reads) == 2:
            trust['generation'] = 2
            trust['keys'][0]['enabled'] = False
        return trust
    registry._reader = current
    with pytest.raises(WorkflowRefusal, match='trust_changed'):
        registry.get('lab', 'inspect', '1.0.0')


def test_registry_capacity_and_arbitrary_tool_rejected(tmp_path):
    tmp_path.chmod(0o700)
    envelope, trust, _ = signed()
    registry = WorkflowRegistry(tmp_path / 'workflows.sqlite', lambda: trust, max_versions=1)
    registry.publish(envelope)
    definition = deepcopy(envelope['payload']['definition'])
    definition['version'] = '1.0.1'
    second, _, _ = signed(definition)
    with pytest.raises(WorkflowRefusal, match='capacity'):
        registry.publish(second)
    definition['steps'][0]['tool'] = 'arbitrary_shell'
    with pytest.raises(ValueError, match='unknown_skill_service'):
        signed(definition)


@pytest.mark.parametrize('operation', ['get', 'list', 'assert_current'])
def test_validity_expiring_during_final_trust_io_withholds_verified_content(tmp_path, operation):
    registry, envelope, trust, _ = setup(tmp_path)
    registry._clock = lambda: 10
    registry.publish(envelope)
    checked = registry.get('lab', 'inspect', '1.0.0')
    clock, reads = [10], []
    registry._clock = lambda: clock[0]
    def delayed_trust():
        reads.append(1)
        # get/assert_current verify before read 2; list performs its final
        # enclosing trust read after verifying and collecting the first row.
        target = 5 if operation == 'list' else 2
        if len(reads) == target:
            clock[0] = envelope['payload']['expires_at']
        return trust
    registry._reader = delayed_trust
    with pytest.raises(WorkflowRefusal, match='validity_window'):
        if operation == 'get':
            registry.get('lab', 'inspect', '1.0.0')
        elif operation == 'list':
            registry.list('lab')
        else:
            registry.assert_current(checked)
