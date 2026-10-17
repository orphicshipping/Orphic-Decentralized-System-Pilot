"""Real MCP SDK stdio and authenticated Streamable HTTP journeys."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from ciw.agent_api import parse
from ciw.operational_cli import example, main
from ciw.operational_agent import OperationalHost
from ciw.operational_http import create_app


@pytest.mark.parametrize('modern',[True,False])
def test_real_sdk_j1_through_j5_and_restart(tmp_path,modern):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import jsonschema
    p=example(tmp_path/'profile')
    with OperationalHost(p):pass  # Explicit initialize, then all clients reconnect.
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'src'))
    seen={}
    async def connection(reconnect):
        args=['-m','ciw.net','operational','serve','--profile',str(p),'--sdk','--resume']
        params=StdioServerParameters(command=sys.executable,args=args,env=env,cwd=str(tmp_path))
        with (tmp_path/'stderr.log').open('a') as err:
            async with stdio_client(params,errlog=err) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=30) as client:
                    await (client.discover() if modern else client.initialize())
                    listing=await client.list_tools();names={t.name for t in listing.tools}
                    assert {'oe_catalog','oe_query','oe_model_support','oe_propose','net_execute'}<=names
                    async def call(name,args={}):
                        r=await client.call_tool(name,args)
                        assert not r.is_error,(name,r)
                        assert r.structured_content==json.loads(r.content[0].text)
                        schema=next(t.output_schema for t in listing.tools if t.name==name)
                        if schema:jsonschema.validate(r.structured_content,schema)
                        return r.structured_content
                    catalog=await call('oe_catalog')
                    if not reconnect:
                        q=await call('oe_query',{'source':'recorded','clock':'synthetic.clock','boot':'boot-1',
                            'channel':'temperature','start':0,'end':2,'limit':1})
                        assert q['data']['total']==2 and q['data']['next_offset']==1
                        await call('oe_trace',{'source':'recorded','observation':q['data']['rows'][0]['id']})
                    run=await call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'sdk-first'})
                    finding=await call('oe_model_support',{'attempt':'sdk-first'})
                    assert finding['data']['status']=='AGREEMENT'
                    proposal=await call('oe_propose',{'finding':finding['record'],'template':'resolve'})
                    if reconnect:
                        assert run['reused_response'] and run['result_ids']==seen['run']['result_ids']
                        assert finding['record']==seen['finding']['record']
                        assert proposal['record']==seen['proposal']['record']
                        assert (await call('oe_inspect',{'record':seen['proposal']['record']}))['data']==seen['proposal']['data']
                    else:seen.update(run=run,finding=finding,proposal=proposal)
                    refused=await client.call_tool('oe_query',{'source':'/etc/passwd'})
                    assert refused.is_error
    asyncio.run(connection(False));asyncio.run(connection(True))


@pytest.fixture
def remote(tmp_path):
    p=example(tmp_path/'profile');host=OperationalHost(p)
    conf={'schema':'ncp.operational-access.v1','profile':str(p),'resume':False,
        'issuer':'https://fixture.cloudflareaccess.com','caller':'personal-caller','worker':'worker-1',
        'investigation':host.spec['investigation'],'endpoint':{'public_url':'https://ncp.example/operational/mcp',
        'audience':'operational-audience','role':'worker','subjects':{'user-1':'personal-caller'}}}
    app=create_app(conf,host=host)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    app.state.access_verifier.jwks=SimpleNamespace(get_signing_key_from_jwt=lambda t:SimpleNamespace(key=key.public_key()))
    def token(**changes):
        now=int(time.time());claims={'iss':conf['issuer'],'aud':['operational-audience'],'sub':'user-1',
            'iat':now-1,'exp':now+300,'type':'app',**changes}
        return jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test-key'})
    with TestClient(app,base_url='https://ncp.example') as client:yield host,client,token,conf
    host.close()


def rpc(remote,tool,args=None,*,token=None,headers=None):
    h={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25',
        'Cf-Access-Jwt-Assertion':token or remote[2]()}
    h.update(headers or {})
    return remote[1].post('/operational/mcp',headers=h,
        json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':tool,'arguments':args or {}}})


def test_authenticated_remote_journey_fixed_worker_and_caller(remote):
    result=rpc(remote,'oe_catalog');assert result.status_code==200,result.text
    assert result.json()['result']['structuredContent']['remote_binding']=={
        'worker':'worker-1','caller':'personal-caller','investigation':remote[0].spec['investigation']}
    run=rpc(remote,'net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'http-first'})
    assert run.status_code==200 and not run.json()['result']['isError'],run.text
    finding=rpc(remote,'oe_model_support',{'attempt':'http-first'}).json()['result']['structuredContent']
    assert finding['data']['status']=='AGREEMENT'
    proposal=rpc(remote,'oe_propose',{'finding':finding['record'],'template':'resolve'}).json()['result']['structuredContent']
    assert not proposal['data']['executed']
    retry=rpc(remote,'net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'http-first'})
    assert retry.json()['result']['structuredContent']['reused_response']
    denied=rpc(remote,'net_candidate',{'baseline':'assessment-graph','changes':{}})
    assert denied.json()['result']['isError']


@pytest.mark.parametrize('claims',[{'sub':'another-caller'},{'aud':['another-investigation']},{'exp':1},
    {'iss':'https://other.cloudflareaccess.com'},{'aud':['operational-audience','extra']},{'type':'service'}])
def test_wrong_signed_remote_binding_denies_before_dispatch(remote,claims):
    r=rpc(remote,'oe_catalog',token=remote[2](**claims))
    assert r.status_code==401 and not remote[0].agent._attempts


def test_revocation_origin_and_worker_substitution(remote):
    assert rpc(remote,'oe_catalog',headers={'Host':'other.example'}).status_code==403
    assert rpc(remote,'oe_catalog',headers={'Origin':'https://other.example'}).status_code==403
    bad=deepcopy(remote[3]);bad['investigation']='sha256:'+'0'*64
    with pytest.raises(Exception):create_app(bad,host=remote[0])
    assert rpc(remote,'oe_query',{'source':'recorded','worker':'other'}).json()['result']['isError']
    assert main(['revoke','--profile',str(remote[0].profile)])==0
    assert rpc(remote,'oe_catalog').status_code==401
