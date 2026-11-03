"""Independent protocol and I/O-boundary review of the server toolbox.

Only local synthetic identities and real local services/storage are used. These
tests do not qualify an external issuer, hosted registry, or physical device.
"""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from threading import Barrier, Event
import time

import httpx2
import jwt
import pytest

from ciw.persistent_cache import PersistentCache
from ciw.principal_http import OAuthSettings, SubjectBindings, JWKAuthority, create_app
from ciw.toolbox_cache import CacheScopeChanged
from ciw.workflow_registry import WorkflowRegistry, WorkflowRefusal, signing_bytes
from test_mcp_toolbox import toolbox, local_client, run_args, TOOLS
from test_persistent_cache import authority
from test_principal_http import issuer, running
from test_toolbox_skills import configured
from test_workflow_registry import signed


def revoke(trust):
    trust['generation'] += 1
    trust['keys'][0]['enabled'] = False


def test_official_http_sdk_activates_component_and_runs_signed_workflow(toolbox, issuer):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    c, _, outer, inner, _, _, outer_anchor, inner_anchor, _ = toolbox
    issuer_url, key, context, _ = issuer
    settings = OAuthSettings('https://ncp.example/mcp', issuer_url)
    subjects = SubjectBindings(lambda: {'schema': 'ciw.oauth-subjects.v1', 'revision': 'local-review',
        'subjects': [{'subject': 'alice-fixture', 'principal': 'alice', 'enabled': True, 'not_before': 0}]})
    app = create_app(settings, facades={'alice': outer}, subjects=subjects,
                     authority=JWKAuthority(settings, ssl_context=context))
    now = int(time.time())
    token = jwt.encode({'iss': issuer_url, 'aud': settings.resource, 'sub': 'alice-fixture',
        'iat': now - 1, 'exp': now + 300, 'client_id': 'local-review', 'jti': 'test-only',
        'scope': 'odsp:invoke'}, key, algorithm='RS256', headers={'typ': 'at+jwt', 'kid': 'test-key'})
    with running(app) as port:
        async def exercise():
            async with httpx2.AsyncClient(headers={'Host': 'ncp.example', 'Authorization': 'Bearer ' + token},
                                           trust_env=False) as http:
                async with streamable_http_client(f'http://127.0.0.1:{port}/mcp', http_client=http) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.discover()
                        before = await client.list_tools()
                        assert {tool.name for tool in before.tools} == set(TOOLS)
                        async def call(name, arguments):
                            reply = await client.call_tool(name, arguments)
                            assert not reply.is_error, reply
                            return reply.structured_content['data']
                        description = await call('ncp_toolbox_describe', {'component_id': 'reference'})
                        await call('ncp_toolbox_load', {'component_id': 'reference',
                            'expected_digest': description['digest']})
                        invoked = await call('ncp_toolbox_call', {'component_id': 'reference',
                            'operation': 'ncp_knowledge_resolve', 'arguments': {'source_id': 'gdrive:Original'}})
                        assert invoked['result']['record']['record_id'] == c.record_id
                        outcome = await call('ncp_skill_run', run_args('http-review'))
                        assert outcome['status'] == 'completed' and outcome['steps_completed'] == 3
                        final = await call('ncp_skill_receipt', {'attempt': 'http-review'})
                        passage = await call('ncp_skill_receipt', {'attempt': 'http-review',
                            'artifact_id': final['receipt']['steps'][-1]['artifact_id']})
                        assert passage['receipt']['result']['span']['text'].encode() == c.text
                        await call('ncp_toolbox_unload', {'component_id': 'reference'})
                        assert (await client.list_tools()).tools == before.tools
        asyncio.run(exercise())
    phases = [row['event']['phase'] for row in inner._audit.chain.verify(inner_anchor.read())]
    assert phases == ['admitted', 'succeeded'] * 4
    outer._audit.chain.verify(outer_anchor.read())
    assert token not in inner._audit.chain.path.read_text() + outer._audit.chain.path.read_text()


def test_signer_revoked_during_post_verification_policy_io_withholds_definition(toolbox, monkeypatch):
    c, host, _, _, registry, trust, _, _, _ = toolbox
    original_get, original_reader = registry.get, c.policy._reader
    pending = [False]
    def verified(*args):
        value = original_get(*args)
        pending[0] = True
        return value
    def read_policy():
        if pending[0]:
            pending[0] = False
            revoke(trust)
        return original_reader()
    monkeypatch.setattr(registry, 'get', verified)
    monkeypatch.setattr(c.policy, '_reader', read_policy)
    result = host.call('ncp_skill_describe', {'workflow_id': 'reference-citation', 'version': '1.0.0'})
    assert trust['keys'][0]['enabled'] is False
    assert result['status'] == 'refused' and result['data'] is None, result


def test_signer_revoked_during_outer_terminal_audit_io_withholds_receipt(toolbox, monkeypatch):
    c, host, outer, _, _, trust, _, _, _ = toolbox
    initial = host.call('ncp_skill_run', run_args('receipt-review'))
    assert initial['data']['status'] == 'completed'
    original = outer._audit.compare_and_set_anchor
    def terminal(before, after):
        committed = original(before, after)
        if after['sequence'] == 2:
            revoke(trust)
        return committed
    monkeypatch.setattr(outer._audit, 'compare_and_set_anchor', terminal)
    result = local_client(outer).call('ncp_skill_receipt', {'attempt': 'receipt-review'})
    assert trust['keys'][0]['enabled'] is False
    assert result['status'] == 'refused' and result['data'] is None, result
    assert c.text.decode() not in json.dumps(result)


def test_registry_concurrent_different_signed_versions_have_one_immutable_winner(tmp_path):
    tmp_path.chmod(0o700)
    first, trust, key = signed()
    second = deepcopy(first)
    second['payload']['definition']['description'] = 'Independently signed conflicting definition.'
    second['signature_b64'] = base64.b64encode(key.sign(signing_bytes(second['payload'], second['key_id']))).decode()
    instances = [WorkflowRegistry(tmp_path / 'registry.sqlite', lambda: trust) for _ in range(2)]
    barrier = Barrier(2)
    def publish(index):
        barrier.wait(timeout=3)
        try:
            return ('published', instances[index].publish((first, second)[index]))
        except WorkflowRefusal as exc:
            return ('refused', str(exc))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(publish, range(2)))
    assert sorted(row[0] for row in results) == ['published', 'refused']
    assert next(row[1] for row in results if row[0] == 'refused') == 'workflow_version_immutable'
    winner = next(row[1] for row in results if row[0] == 'published')['workflow_sha256']
    assert all(instance.get('lab', 'inspect', '1.0.0')['workflow_sha256'] == winner for instance in instances)
    with sqlite3.connect(instances[0].db.path) as db:
        assert db.execute('SELECT count(*) FROM workflow_versions').fetchone()[0] == 1


def test_registry_signer_revocation_while_waiting_for_write_lock_prevents_import(tmp_path):
    tmp_path.chmod(0o700)
    envelope, trust, _ = signed()
    observed = Event()
    def current():
        observed.set()
        return trust
    registry = WorkflowRegistry(tmp_path / 'registry.sqlite', current)
    observed.clear()
    with ThreadPoolExecutor(1) as pool:
        with registry.db.transaction():
            pending = pool.submit(registry.publish, envelope)
            assert observed.wait(timeout=1)
            revoke(trust)
        with pytest.raises(WorkflowRefusal, match='trust_changed|revocation'):
            pending.result(timeout=4)
    with sqlite3.connect(registry.db.path) as db:
        assert db.execute('SELECT count(*) FROM workflow_versions').fetchone()[0] == 0


def test_registry_signer_revoked_after_database_read_withholds_envelope(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    envelope, trust, _ = signed()
    registry = WorkflowRegistry(tmp_path / 'registry.sqlite', lambda: trust)
    registry.publish(envelope)
    original = registry.db.connect
    @contextmanager
    def read_then_revoke():
        with original() as db:
            yield db
        revoke(trust)
    monkeypatch.setattr(registry.db, 'connect', read_then_revoke)
    with pytest.raises(WorkflowRefusal, match='trust_changed'):
        registry.get('lab', 'inspect', '1.0.0')


def test_shared_cache_authority_revoked_after_commit_withholds_detached_content(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    current = [authority()]
    cache = PersistentCache(tmp_path / 'cache.sqlite', lambda: current[0])
    args = dict(request={'record': 'fixture'}, content_version='v1', schema_sha256='c' * 64)
    cache.put_result('ncp_fixture_read', {'private': 'must-not-disclose'}, **args)
    other = PersistentCache(cache.db.path, lambda: current[0])
    original = other.db.transaction
    @contextmanager
    def commit_then_revoke():
        with original() as db:
            yield db
        current[0] = replace(current[0], immutable_reads=())
    monkeypatch.setattr(other.db, 'transaction', commit_then_revoke)
    with pytest.raises(CacheScopeChanged):
        other.get_result('ncp_fixture_read', **args)


def test_shared_cache_revocation_while_waiting_for_write_lock_prevents_new_content(tmp_path):
    tmp_path.chmod(0o700)
    current = [authority()]
    observed = Event()
    def read():
        observed.set()
        return current[0]
    cache = PersistentCache(tmp_path / 'cache.sqlite', read)
    observed.clear()
    args = dict(request={}, content_version='v1', schema_sha256='c' * 64)
    with ThreadPoolExecutor(1) as pool:
        with cache.db.transaction():
            pending = pool.submit(cache.put_result, 'ncp_fixture_read', {'private': 'fixture'}, **args)
            assert observed.wait(timeout=1)
            current[0] = replace(current[0], immutable_reads=())
        with pytest.raises(CacheScopeChanged):
            pending.result(timeout=4)
    with sqlite3.connect(cache.db.path) as db:
        assert db.execute('SELECT count(*) FROM cache_entries').fetchone()[0] == 0
