import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import ciw
BASE = Path(ciw.__file__).resolve().parents[1]
from ciw.state_read_profile import *
from ciw.services import invoke
import pytest


def capture(operation='ncp_state_releases', arguments=None, body=None, error=False, principal='test-owner'):
    # Deliberately invented transport fixtures, NOT a run of the upstream server.
    body = body or {'receipt':{'synthetic':True},'result':{'fixture_only':True,'releases':[]}}
    return {'schema':'odsp.nsl.capture.v1','source_commit':SOURCE_COMMIT,'principal':principal,
            'origin':'synthetic','captured_at':'2026-11-02T06:00:00Z','projection':'COUNTERPARTY_SHARED',
            'records':[{'operation':operation,'arguments':arguments or {},
                        'response':{'content':[{'type':'text','text':json.dumps(body)}],'isError':error}}]}


def host(data=None, **kwargs):
    raw=encode(data or capture())
    bindings=dict(capture_bytes=raw,expected_digest=digest(raw),source_commit=SOURCE_COMMIT,
                  principal='test-owner',origin='synthetic')
    bindings.update(kwargs)
    return StateReadHost(**bindings)


def test_exact_discovery_read_and_copy():
    h=host()
    assert len(h.descriptions())==8
    result=invoke_state(h,'ncp_state_releases',{})
    assert result['upstream']['result']['fixture_only'] is True
    assert result['provenance']['fresh_upstream_read'] is False
    result['upstream']['result']['releases'].append('tampered')
    assert invoke_state(h,'ncp_state_releases',{})['upstream']['result']['releases']==[]
    assert h.call('ncp_state_releases',{'corpus':'not-captured'})['reason']=='exact_capture_unavailable'


@pytest.mark.parametrize('args',[{'principal':'x'},{'pin':'x'},{'projection':'PUBLIC_RULING'}, {'cursor':'x'}, {'corpus':1}])
def test_payload_cannot_rebind(args):
    assert host().call('ncp_state_releases',args)['status']=='refused'


@pytest.mark.parametrize('name',['ncp_state_propose','ncp_notation_apply','ncp_state_capability','runMcpTool'])
def test_no_mutation_or_invented_capability(name):
    assert host().call(name,{})['reason']=='unknown_state_operation'


def test_principal_pin_origin_and_restart():
    for overrides in ({'principal':'other'},{'source_commit':'a'*40},{'origin':'governed_capture'},{'expected_digest':'sha256:'+'0'*64}):
        with pytest.raises(StateRefusal): host(**overrides)
    assert host().call('ncp_state_releases',{})==host().call('ncp_state_releases',{})


def test_ruling_requires_exact_allowlist():
    data=capture('ncp_state_ruling',{'ruling_id':'r1'})
    assert host(data).call('ncp_state_ruling',{'ruling_id':'r1'})['reason']=='ruling_scope_refused'
    assert host(data,allowed_ruling_ids=['r1']).call('ncp_state_ruling',{'ruling_id':'r1'})['status']=='ok'


def test_temporal_and_projection():
    args={'release_id':'r','subject':'s','predicate':'p','valid_at':'2026-02-30T12:00Z',
          'known_at':'2026-11-02T06:00Z','question':'WHAT_WE_HELD'}
    assert host().call('ncp_state_as_of',args)['reason']=='invalid_time'
    args['valid_at']='2026-02-28T12:00Z'
    name,m=mapped('ncp_state_as_of',args,'COUNTERPARTY_SHARED')
    assert name=='query_as_of' and 'projection' not in m
    with pytest.raises(StateRefusal): mapped('ncp_state_as_of',args,'PUBLIC_RULING')
    del args['question']
    assert host().call('ncp_state_as_of',args)['reason']=='invalid_arguments'


def test_refusal_is_preserved_not_tool_error():
    data=capture(body={'receipt':{'synthetic':True},'refusal':{'code':'source_clock_missing'}})
    response=host(data).call('ncp_state_releases',{})
    assert response['status']=='ok'
    assert response['data']['upstream']['refusal']['code']=='source_clock_missing'
    assert host(capture(error=True)).call('ncp_state_releases',{})['reason']=='upstream_tool_error'


def test_budgets_duplicates_receipt():
    assert host().call('ncp_state_releases',{'corpus':'a'*70000})['reason']=='invalid_arguments'
    with pytest.raises(ValueError):
        host(capture(body={'receipt':{},'result':{'big':['a'*40000]*7}}))
    data=capture(); data['records']*=2
    with pytest.raises(StateRefusal): host(data)
    with pytest.raises(StateRefusal): host(capture(body={'result':{}}))
    with pytest.raises(StateRefusal): host(capture_bytes=b'x'*(MAX_CAPTURE+1))


def test_real_mcp_subprocess_and_sdk_reconnect(tmp_path):
    from importlib.metadata import PackageNotFoundError, version
    try:
        version('mcp')
    except PackageNotFoundError:
        pytest.skip('Optional official MCP SDK transport dependency is not installed')
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    raw=encode(capture()); (tmp_path/'capture.json').write_bytes(raw)
    launcher=tmp_path/'host.py'
    launcher.write_text(f'''import asyncio,sys
sys.path.insert(0,{str(BASE)!r})
from ciw.state_read_profile import StateReadHost
from ciw.knowledge_mcp import serve_stdio
from pathlib import Path
host=StateReadHost(capture_bytes=Path({str(tmp_path/'capture.json')!r}).read_bytes(),expected_digest={digest(raw)!r},source_commit={SOURCE_COMMIT!r},principal='test-owner',origin='synthetic')
asyncio.run(serve_stdio(host))
''')
    async def run():
        for _ in range(2):
            async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(launcher)],env=dict(os.environ))) as (r,w):
                async with ClientSession(r,w) as session:
                    await session.initialize()
                    tools=await session.list_tools()
                    assert len(tools.tools)==8
                    assert (await session.list_resources()).resources == []
                    assert (await session.list_resource_templates()).resource_templates == []
                    result=await session.call_tool('ncp_state_releases',{})
                    assert result.structured_content['data']==invoke('ncp_state_releases',{},host=host())
                    refused=await session.call_tool('ncp_state_propose',{})
                    assert refused.is_error
    asyncio.run(run())

@pytest.mark.parametrize('name',list(ROUTES))
def test_each_mapping_and_actual_capture(name):
    _, required, _ = ROUTES[name]
    args={key:'value' for key in required}
    if 'question' in args:
        args.update(question='WHAT_THE_SOURCE_KNEW',valid_at='2026-11-02T06:00Z',known_at='2026-11-02T06:00Z')
    h=host(capture(name,args),allowed_ruling_ids=['value'])
    response=h.call(name,args)
    assert response['status']=='ok'
    assert response['data']['upstream_operation']==ROUTES[name][0]


def test_corrupt_nonjson_duplicate_and_missing_response_refuse():
    for raw in (b'not json',b'{"schema":1,"schema":2}',b'[]'):
        with pytest.raises((ValueError,TypeError)):
            host(capture_bytes=raw,expected_digest=digest(raw))
    data=capture(); data['records'][0]['response']['content'][0]['text']='not json'
    with pytest.raises(ValueError): host(data)


def test_unknown_principal_does_not_reopen_across_process(tmp_path):
    raw=encode(capture())
    script=f'''import sys
sys.path.insert(0,{str(BASE)!r})
from ciw.state_read_profile import *
try:
 StateReadHost(capture_bytes={raw!r},expected_digest={digest(raw)!r},source_commit={SOURCE_COMMIT!r},principal='other',origin='synthetic')
except StateRefusal as exc:
 assert str(exc)=='capture_binding_mismatch'
else:
 raise AssertionError('principal isolation failed')
'''
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,cwd=tmp_path)
    assert result.returncode==0,result.stderr

@pytest.mark.parametrize('content',[None,{},'text',{'type':'text','text':None}])
def test_malformed_content_is_typed_refusal(content):
    data=capture();data['records'][0]['response']['content']=[content]
    with pytest.raises(StateRefusal): host(data)


def test_declared_capture_is_not_authenticated_governance():
    data=capture();data['origin']='governed_capture'
    result=invoke_state(host(data,origin='governed_capture'),'ncp_state_releases',{})
    assert result['provenance']['capture_authority']=='host_declared_unverified'
    assert result['provenance']['upstream_receipt_verification']=='not_performed'


def test_complete_mcp_envelope_budget_matches_sdk():
    # A Unicode value is compact upstream JSON but escaped at ODSP wire boundary.
    data=capture(body={'receipt':{},'result':{'fixture_only':True,'value':'\u2603'*20000}})
    data['records'][0]['response']['content'][0]['text']=json.dumps(
        {'receipt':{},'result':{'fixture_only':True,'value':'\u2603'*20000}},ensure_ascii=False)
    h=host(data)
    response=h.call('ncp_state_releases',{})
    assert response=={'status':'refused','reason':'response_too_large'}
    with pytest.raises(StateRefusal,match='response_too_large'):
        invoke_state(h,'ncp_state_releases',{})
    assert len(encode(h.mcp_result('ncp_state_releases',response)))<=MAX_RESPONSE


def test_nested_upstream_source_clock_refusal_has_explicit_outcome():
    data=capture(body={'receipt':{'synthetic':True},'result':{'fixture_only':True,'answer':None,
        'refusal':{'code':'QUESTION_NOT_ANSWERABLE'}}})
    result=invoke_state(host(data),'ncp_state_releases',{})
    assert result['outcome']=='refused'
    assert result['upstream']['result']['refusal']['code']=='QUESTION_NOT_ANSWERABLE'
