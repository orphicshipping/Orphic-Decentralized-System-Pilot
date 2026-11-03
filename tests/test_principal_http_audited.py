"""Actual HTTP transport preserves the existing durable audit/identity gate."""
import asyncio
import json
from unittest.mock import patch

import httpx2
import pytest
from starlette.testclient import TestClient

from ciw.audit_chain import AuditChain
from ciw.audited_host import AuditedPrincipalHost
from ciw.principal_http import create_app
from test_campaign_audited_host import Anchor
from test_principal_http import configured, issuer, rpc, running


def audited(c, root, cas=None):
    chain = AuditChain(root/'http-audit.jsonl', chain_id='audited-profile',
                       allowed_operations=['ncp_capabilities_list', 'ncp_plan_template', 'resource_read'])
    anchor = Anchor(root/'independent-anchor.sqlite')
    host = AuditedPrincipalHost(c['facades']['alice'], chain=chain, read_anchor=anchor.read,
                                compare_and_set_anchor=cas or anchor.cas)
    app = create_app(c['settings'], facades={'alice': host}, subjects=c['subjects'], authority=c['authority'])
    return host, app, chain, anchor


def call(client, c):
    response = rpc(client, c['token'](), 'tools/call', {'name': 'ncp_capabilities_list', 'arguments': {}})
    assert response.status_code == 200
    return response.json()['result']


def test_official_http_sdk_dispatches_through_durable_audit(configured, tmp_path):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    c = configured; host, app, chain, anchor = audited(c, tmp_path)
    token = c['token']()
    with patch.object(host, 'call', wraps=host.call) as calls:
        with running(app) as port:
            async def exercise():
                async with httpx2.AsyncClient(headers={'Host': 'ncp.example', 'Authorization': 'Bearer '+token}, trust_env=False) as http:
                    async with streamable_http_client(f'http://127.0.0.1:{port}/mcp', http_client=http) as (reader, writer):
                        async with ClientSession(reader, writer) as client:
                            await client.discover()
                            tools = await client.list_tools()
                            assert {tool.name for tool in tools.tools} == {'ncp_capabilities_list', 'ncp_plan_template'}
                            result = await client.call_tool('ncp_capabilities_list', {})
                            assert result.is_error is False and result.structured_content['status'] == 'ok'
                            assert json.loads(result.content[0].text) == result.structured_content
                            await client.list_resources()
                            await client.list_resource_templates()
            asyncio.run(exercise())
        assert calls.call_count == 1
        assert calls.call_args.args == ('ncp_capabilities_list', {})
    records = chain.verify(anchor.read())
    assert [r['event']['phase'] for r in records] == ['admitted', 'succeeded']
    assert token not in chain.path.read_text()
    assert all(c['owners'][p].capabilities()['execution_budget']['used'] == 0 for p in c['owners'])


def test_failed_audit_admission_never_dispatches_http_call(configured, tmp_path):
    c = configured; host, app, chain, anchor = audited(c, tmp_path, cas=lambda *_: False)
    with patch.object(c['facades']['alice'], 'call', side_effect=AssertionError('audit bypass')) as calls:
        with TestClient(app, base_url='https://ncp.example') as client:
            result = call(client, c)
            assert result['isError'] is True
            assert result['structuredContent']['error']['code'] == 'audit_unavailable'
    assert calls.call_count == 0


def test_terminal_audit_failure_remains_unknown_and_holds_next_http_call(configured, tmp_path):
    c = configured; host, app, chain, anchor = audited(c, tmp_path)
    original = anchor.cas
    host._audit.compare_and_set_anchor = lambda before, after: False if after['sequence'] == 2 else original(before, after)
    with patch.object(c['facades']['alice'], 'call', wraps=c['facades']['alice'].call) as calls:
        with TestClient(app, base_url='https://ncp.example') as client:
            result = call(client, c)
            assert result['isError'] is True
            assert result['structuredContent']['error']['code'] == 'outcome_unknown'
            assert result['structuredContent']['data'] is None
            second = call(client, c)
            assert second['isError'] is True and second['structuredContent']['data'] is None
        assert calls.call_count == 1
    assert anchor.read()['sequence'] == 1


@pytest.mark.parametrize('boundary', ['admission', 'terminal'])
def test_subject_revocation_during_audit_io_cannot_bypass_current_binding(configured, tmp_path, boundary):
    c = configured; host, app, chain, anchor = audited(c, tmp_path)
    def update(before, after):
        result = anchor.cas(before, after)
        if after['sequence'] == (1 if boundary == 'admission' else 2):
            c['bindings']['subjects'][0]['enabled'] = False
        return result
    host._audit.compare_and_set_anchor = update
    with patch.object(c['facades']['alice'], 'call', wraps=c['facades']['alice'].call) as calls:
        with TestClient(app, base_url='https://ncp.example') as client:
            result = call(client, c)
            assert result['isError'] is True and result['structuredContent']['data'] is None
            assert rpc(client, c['token']()).status_code == 401
        assert calls.call_count == (0 if boundary == 'admission' else 1)
    records = chain.verify(anchor.read())
    assert records[-1]['event']['phase'] == ('refused' if boundary == 'admission' else 'succeeded')
    # An already durable terminal record is not rewritten as rolled back.
    if boundary == 'terminal':
        assert result['structuredContent']['error']['code'] == 'outcome_unknown'


def test_audited_facade_rebind_refuses_even_after_restoration(configured, tmp_path):
    c = configured; host, app, _, _ = audited(c, tmp_path)
    with TestClient(app, base_url='https://ncp.example') as client:
        assert rpc(client, c['token']()).status_code == 200
        host.facade = c['facades']['bob']
        assert rpc(client, c['token']()).status_code == 401
        host.facade = c['facades']['alice']
        assert rpc(client, c['token']()).status_code == 401


def test_raw_facade_principal_rebind_is_not_an_http_identity(configured):
    c = configured
    with TestClient(c['app'], base_url='https://ncp.example') as client:
        c['facades']['alice'].principal = 'bob'
        assert rpc(client, c['token']()).status_code == 401


@pytest.mark.parametrize('data', [None, {'private': 'must-not-escape'}])
def test_refusal_status_does_not_bypass_final_subject_check(configured, data):
    c = configured
    def refuse(*_):
        c['bindings']['subjects'][0]['enabled'] = False
        return {'status': 'refused', 'data': data, 'error': {'code': 'provider_refused', 'private': 'must-not-escape'}}
    with patch.object(c['facades']['alice'], 'call', side_effect=refuse):
        with TestClient(c['app'], base_url='https://ncp.example') as client:
            result = call(client, c)
            assert result['isError'] is True and result['structuredContent']['data'] is None
            assert 'must-not-escape' not in json.dumps(result)
            assert rpc(client, c['token']()).status_code == 401
