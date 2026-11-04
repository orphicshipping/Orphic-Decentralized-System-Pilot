"""Canonical SDK, audit barriers and actual MCP stdio for a fixed held pipeline."""
import asyncio
import base64
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from ciw.agent_api import encode
from ciw.agent_mcp import Server
from ciw.campaign_contracts import canonical, parse
from ciw.data_instruments import content_ref
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import admit, capability_map, invoke
from ciw.survey_analysis_pipeline import validate_artifact
from ciw.survey_pipeline_profile import SurveyPipelineHost, TOOLS, profile_manifest

ROOT = Path(__file__).resolve().parents[1]


def example():
    spec = importlib.util.spec_from_file_location('canonical_survey_pipeline_example',
        ROOT / 'examples/domain-h-survey-mcp/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def configured(tmp_path):
    return example().bind(tmp_path / 'synthetic')


def args(p, attempt='survey-1'):
    return {'plan_ref': p.source.active.plan_ref, 'attempt': attempt}


def queue_bytes(p):
    return (p.source.root / 'pipeline/queue/queue.json').read_bytes()


def forbidden(*a, **kw):
    pytest.fail('request bypassed a guard or repeated computation')


def test_canonical_sdk_replay_and_mcp_pages_preserve_held_artifact(configured):
    import ciw.data_analysis as numerical
    p = configured
    with patch.object(numerical, 'numerical', wraps=numerical.numerical) as calls, \
         patch('socket.create_connection', side_effect=forbidden):
        catalog = invoke('ncp_pipeline_catalog', {}, host=p.wrapped)
        assert catalog['plan_ref'] == p.source.active.plan_ref
        assert catalog['readiness'] == 'not_asserted_by_discovery'
        first = invoke('ncp_pipeline_run', args(p), host=p.wrapped)
        assert calls.call_count == 1
        custody = queue_bytes(p)
        records = p.source.ledger.verify_chain()['records']
        replay = invoke('ncp_pipeline_run', args(p), host=p.wrapped)
        inspected = invoke('ncp_pipeline_inspect', args(p), host=p.wrapped)
        assert first == replay == inspected
        server = Server(p.wrapped)
        init = server.handle(encode({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'synthetic', 'version': '1'}}}))
        assert 'result' in init
        server.handle(encode({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        assembled, offset, request_id = bytearray(), 0, 2
        while True:
            response = server.handle(encode({'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                'params': {'name': 'ncp_pipeline_read', 'arguments': {**args(p), 'offset': offset, 'limit': 16384}}}))
            assert len(encode(response)) <= 262144
            result = response['result']
            assert result['isError'] is False
            page = result['structuredContent']['data']
            assert page['artifact_ref'] == first['artifact_ref']
            assembled.extend(base64.b64decode(page['chunk_b64'], validate=True))
            offset, request_id = page['next_offset'], request_id + 1
            if offset is None:
                break
        assert calls.call_count == 1
    assert queue_bytes(p) == custody and p.source.ledger.verify_chain()['records'] == records
    assert content_ref(bytes(assembled)) == first['artifact_ref']
    artifact = validate_artifact(parse(bytes(assembled)), expected_ref=first['artifact_ref'])
    assert artifact['status'] == first['status'] == 'held'
    assert artifact['claims']['authenticated_time'] is False
    assert first['statistics']['mean'] == 10.1875
    assert first['uncertainty']['mean']['status'] == 'declared_model_propagated'
    assert first['service']['implementation_ref'] == content_ref((ROOT / 'src/ciw/survey_pipeline_profile.py').read_bytes())
    assert p.chain.verify(p.anchor.read())


def test_shared_validator_registration_and_raw_service_refusal(configured):
    p = configured
    admit('ncp_pipeline_run', args(p))
    with pytest.raises(ValueError):
        invoke('ncp_pipeline_run', args(p), host=p.profile)
    assert {row['name'] for row in p.facade.descriptions()} == set(TOOLS)
    assert {row['name'] for row in p.wrapped.descriptions()} == set(TOOLS)
    assert all(row['annotations']['readOnlyHint'] is (row['name'] != 'ncp_pipeline_run')
               for row in p.facade.descriptions())
    assert profile_manifest() in capability_map()['data_systems_profiles']
    # Binding a fake or another principal's profile cannot create a route.
    class Fake:
        _bound_principal = 'survey-operator'
    with pytest.raises(PrincipalRefusal):
        p.source.policy.facade('survey-operator', pipeline=Fake())


@pytest.mark.parametrize('change', [
    {'principal': 'other'}, {'owner': 'other'}, {'policy': {}}, {'token': 'not-a-token'},
    {'endpoint': 'https://example.invalid'}, {'directory': '/tmp/other'},
    {'sql': 'DROP TABLE samples'}, {'model': {}}, {'source_ref': 'sha256:' + '1'*64},
    {'capture_ref': 'sha256:' + '2'*64}, {'admit': True}, {'plan_ref': 'sha256:' + '0'*64},
    {'attempt': True}, {'attempt': 1}, {'attempt': 'a'*81}, {'attempt': "x'); DROP TABLE samples;--"},
    {'attempt': []}, {'attempt': ''}, {'limit': 512},
])
def test_typed_requests_cannot_select_authority_source_sql_or_code(configured, change):
    p = configured
    before = queue_bytes(p)
    with patch.object(p.source.active, 'run', side_effect=forbidden):
        response = p.facade.call('ncp_pipeline_run', {**args(p), **change})
    assert response['status'] == 'refused' and response['data'] is None
    assert queue_bytes(p) == before


@pytest.mark.parametrize('revoke', ['operation', 'constraints', 'science', 'profile', 'truthy', 'pipeline', 'source'])
def test_each_current_boundary_independently_prevents_dispatch(configured, revoke):
    p = configured
    grant = p.source.state['policy']['principals'][0]
    if revoke == 'operation':
        grant['operations'] = []
    elif revoke == 'constraints':
        next(r for r in grant['operations'] if r['name'] == 'ncp_pipeline_run')['constraints'] = {'attempt': ['different-attempt']}
    elif revoke == 'science':
        grant['scientific_operations'] = ['statistics.v1']
    elif revoke == 'profile':
        p.state['decision'] = False
    elif revoke == 'truthy':
        p.state['decision'] = 1
    elif revoke == 'pipeline':
        p.source.state['pipeline_allowed'] = False
    else:
        p.source.state['source_allowed'] = False
    before = queue_bytes(p)
    with patch.object(p.source.active, 'run', side_effect=forbidden):
        result = p.wrapped.call('ncp_pipeline_run', args(p))
    assert result['status'] == 'refused' and result['data'] is None
    assert queue_bytes(p) == before


def test_drift_blocks_execution_saved_disclosure_and_pages(configured):
    p = configured
    first = invoke('ncp_pipeline_run', args(p), host=p.facade)
    changed = parse(p.source.files['capture.json'])
    changed[0]['height_m'] = 11
    p.source.ledger.capture(p.source.survey.source_id, p.source.survey.source_ref,
        canonical(changed), attempt='synthetic-drift', observed={'value': 990, 'frame': p.source.example.FRAME})
    before = queue_bytes(p)
    with patch.object(p.source.active, 'run', side_effect=forbidden):
        for name, request in [('ncp_pipeline_run', args(p, 'after-drift')), ('ncp_pipeline_inspect', args(p)),
                              ('ncp_pipeline_read', {**args(p), 'offset': 0, 'limit': 128})]:
            result = p.facade.call(name, request)
            assert result['status'] == 'refused' and result['data'] is None
    assert queue_bytes(p) == before
    assert invoke('ncp_pipeline_catalog', {}, host=p.facade)['readiness'] == 'not_asserted_by_discovery'
    assert first['status'] == 'held'


def test_replay_and_pages_require_current_permission(configured):
    p = configured
    invoke('ncp_pipeline_run', args(p), host=p.facade)
    before = queue_bytes(p)
    p.source.state['policy']['principals'][0]['operations'] = []
    for name, request in [('ncp_pipeline_run', args(p)), ('ncp_pipeline_inspect', args(p)),
                          ('ncp_pipeline_read', {**args(p), 'offset': 0, 'limit': 128})]:
        assert p.facade.call(name, request)['status'] == 'refused'
    # The raw profile also enforces operation grants; its callback alone is insufficient.
    assert p.profile.call('ncp_pipeline_run', args(p))['effect_state'] == 'not_started'
    assert queue_bytes(p) == before


def test_scope_guard_survives_admission_and_terminal_audit_io(configured, monkeypatch):
    p = configured
    original = p.wrapped._audit.compare_and_set_anchor
    def revoke_before_dispatch(before, after):
        result = original(before, after)
        if after['sequence'] == 1:
            p.source.state['now'] = 1101
        return result
    monkeypatch.setattr(p.wrapped._audit, 'compare_and_set_anchor', revoke_before_dispatch)
    before = queue_bytes(p)
    with patch.object(p.source.active, 'run', side_effect=forbidden):
        result = p.wrapped.call('ncp_pipeline_run', args(p))
    assert result['status'] == 'refused' and result['data'] is None
    assert queue_bytes(p) == before
    assert [r['event']['phase'] for r in p.chain.verify(p.anchor.read())] == ['admitted', 'refused']


def test_terminal_audit_revocation_withholds_committed_artifact_without_reexecution(configured, monkeypatch):
    p = configured
    original = p.wrapped._audit.compare_and_set_anchor
    def revoke_after_result(before, after):
        result = original(before, after)
        if after['sequence'] == 2:
            p.state['decision'] = False
        return result
    monkeypatch.setattr(p.wrapped._audit, 'compare_and_set_anchor', revoke_after_result)
    result = p.wrapped.call('ncp_pipeline_run', args(p))
    assert result['error']['code'] == 'outcome_unknown' and result['data'] is None
    assert [r['event']['phase'] for r in p.chain.verify(p.anchor.read())] == ['admitted', 'succeeded']
    state = json.loads(queue_bytes(p))
    assert len(state['jobs']) == 1 and state['jobs'][0]['status'] == 'completed'
    p.state['decision'] = True
    with patch.object(p.source.active, '_produce', side_effect=forbidden):
        assert invoke('ncp_pipeline_inspect', args(p), host=p.wrapped)['status'] == 'held'
        assert invoke('ncp_pipeline_run', args(p), host=p.wrapped)['status'] == 'held'
    assert queue_bytes(p) == canonical(state)


def test_audit_call_id_replay_never_dispatches_twice(configured):
    p = configured
    first = p.wrapped.call('ncp_pipeline_run', args(p), call_id='trusted-call-1')
    assert first['status'] == 'ok'
    with patch.object(p.source.active, 'run', side_effect=forbidden):
        replay = p.wrapped.call('ncp_pipeline_run', args(p), call_id='trusted-call-1')
    assert replay['error']['code'] == 'audit_call_already_recorded'
    assert len(p.chain.verify(p.anchor.read())) == 2


def test_uncertain_native_attempt_is_readable_but_never_resubmitted(configured):
    p = configured
    original = p.source.active.store.keep
    def lost_reply(*a, **kw):
        original(*a, **kw)
        raise OSError('synthetic retention acknowledgement loss')
    with patch.object(p.source.active.store, 'keep', side_effect=lost_reply):
        response = p.facade.call('ncp_pipeline_run', args(p, 'uncertain'))
    assert response['error']['code'] == 'outcome_unknown' and response['data'] is None
    before = queue_bytes(p)
    with patch.object(p.source.active, '_produce', side_effect=forbidden):
        assert p.facade.call('ncp_pipeline_run', args(p, 'uncertain'))['error']['code'] == 'outcome_unknown'
        inspected = invoke('ncp_pipeline_inspect', args(p, 'uncertain'), host=p.facade)
        assert inspected['status'] == 'outcome_unknown' and inspected['automatic_retry'] is False
        assert p.facade.call('ncp_pipeline_read', {**args(p, 'uncertain'), 'offset': 0, 'limit': 128})['status'] == 'refused'
        assert p.facade.call('ncp_pipeline_run', args(p, 'new-identity'))['status'] == 'refused'
    assert queue_bytes(p) == before


@pytest.mark.parametrize('field', ['principal', 'policy'])
def test_facade_rebinding_cannot_expose_another_subjects_pipeline(configured, field):
    p = configured
    if field == 'principal':
        other = deepcopy(p.source.state['policy']['principals'][0])
        other['principal'] = 'other-operator'
        p.source.state['policy']['principals'].append(other)
        p.facade.principal = 'other-operator'
    else:
        p.facade.policy = PrincipalPolicy(lambda: p.source.state['policy'])
    before = queue_bytes(p)
    with patch.object(p.profile, 'call', side_effect=forbidden):
        assert p.facade.call('ncp_pipeline_run', args(p))['status'] == 'refused'
    assert queue_bytes(p) == before


@pytest.mark.parametrize('offset,limit', [(-1, 128), (0, True), (0, 127), (0, 16385), (131073, 128), (100000, 128)])
def test_artifact_page_bounds_never_dispatch_work(configured, offset, limit):
    p = configured
    invoke('ncp_pipeline_run', args(p), host=p.facade)
    before = queue_bytes(p)
    result = p.facade.call('ncp_pipeline_read', {**args(p), 'offset': offset, 'limit': limit})
    assert result['status'] == 'refused' and result['data'] is None
    assert queue_bytes(p) == before


def test_actual_official_mcp_stdio_uses_canonical_bound_service(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    root = tmp_path / 'official-stdio'
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TZ': 'UTC',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(ROOT / 'src')}
    evidence = {}
    async def run():
        parameters = StdioServerParameters(command=sys.executable, args=[
            str(ROOT / 'examples/domain-h-survey-mcp/run.py'), '--output', str(root), '--stdio'], env=env)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert {t.name for t in (await session.list_tools()).tools} == set(TOOLS)
                catalog = await session.call_tool('ncp_pipeline_catalog', {})
                assert not catalog.is_error
                request = {'plan_ref': catalog.structured_content['data']['plan_ref'], 'attempt': 'stdio-1'}
                first = await session.call_tool('ncp_pipeline_run', request)
                assert not first.is_error and first.structured_content['data']['status'] == 'held'
                replay = await session.call_tool('ncp_pipeline_run', request)
                assert replay.structured_content['data']['artifact_ref'] == first.structured_content['data']['artifact_ref']
                assert (await session.call_tool('ncp_pipeline_run', {**request, 'principal': 'other'})).is_error
                assert (await session.call_tool('ncp_dbops_health', {})).is_error
                assembled, offset = bytearray(), 0
                while True:
                    reply = await session.call_tool('ncp_pipeline_read', {**request, 'offset': offset, 'limit': 16384})
                    assert not reply.is_error
                    page = reply.structured_content['data']
                    assembled.extend(base64.b64decode(page['chunk_b64'], validate=True))
                    offset = page['next_offset']
                    if offset is None:
                        break
                assert content_ref(bytes(assembled)) == first.structured_content['data']['artifact_ref']
                assert (await session.list_resources()).resources == []
                evidence.update(catalog=catalog.structured_content,
                    first=first.structured_content, replay=replay.structured_content,
                    artifact=parse(bytes(assembled)), assembled_ref=content_ref(bytes(assembled)))
    asyncio.run(run())
    queue = json.loads((root / 'survey/pipeline/queue/queue.json').read_bytes())
    assert len(queue['jobs']) == 1 and queue['jobs'][0]['status'] == 'completed'
    capture = os.environ.get('ODSP_PIPELINE_MCP_CAPTURE')
    if capture:
        output = Path(capture)
        output.mkdir(mode=0o700, exist_ok=False)
        for name, value in [('official-stdio.json', evidence), ('queue.json', queue)]:
            with (output / name).open('xb') as stream:
                stream.write(canonical(value))
        with (output / 'audit.jsonl').open('xb') as stream:
            stream.write((root / 'audit/calls.jsonl').read_bytes())
