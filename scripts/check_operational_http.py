"""Actual SDK HTTP J1-J5 across a separate authenticated loopback worker.

Run with the installed interpreter and -I. Ephemeral RSA/JWKS fixtures exercise
the production signature verifier; this is not a production TLS/Access deployment.
"""
import argparse
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from ciw.agent_api import encode, parse
from ciw.operational_agent import OperationalHost
from ciw.operational_cli import prepare, default_context, configuration, default_template, main


def worker(config_path, public_key, port):
    import os
    import uvicorn
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from ciw.external_work_http import AccessVerifier
    from ciw.operational_http import create_app
    from ciw import operational_engineering as engineering
    original=engineering.assess
    def fixture_assessment(*args,**kwargs):
        # Test-driver-only abrupt failure, after the production durable claim.
        if (config_path.parent/'kill-next-model').exists(): os._exit(77)
        return original(*args,**kwargs)
    engineering.assess=fixture_assessment
    key=load_pem_public_key(public_key.read_bytes())
    def factory(binding,issuer):
        verifier=AccessVerifier(binding,issuer)
        verifier.jwks=SimpleNamespace(get_signing_key_from_jwt=lambda token:SimpleNamespace(key=key))
        return verifier
    app=create_app(parse(config_path.read_bytes()),verifier_factory=factory)
    try: uvicorn.run(app,host='127.0.0.1',port=port,proxy_headers=False,access_log=False,log_level='error')
    finally: app.state.operational_host.close()


def run(output):
    import ciw
    import jwt
    import httpx2 as httpx
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from ciw.reference_comparison import example_request
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    if not Path(ciw.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError('Installed HTTP check refuses source imports')
    output.mkdir(parents=True,exist_ok=False)
    request=example_request()
    profile=prepare(output/'profile',investigation_id='http-worker',sources={'recorded':{
        'raw':encode(request['right']),'family':'observations',
        'context':default_context(clock='synthetic.clock',origin='synthetic'),
        'configurations':[configuration('before',{'power':10},clock='synthetic.clock'),
            configuration('after',{'power':20},clock='synthetic.clock',effective=(.5,.5))]}},
        models={'model':{'source':'recorded','engine':'reference','request':encode(request),'policy':{}}},
        templates={'resolve':default_template()})
    with OperationalHost(profile) as host: investigation=host.spec['investigation']
    config={'schema':'ncp.operational-access.v1','profile':str(profile),'resume':True,
        'issuer':'https://fixture.cloudflareaccess.com','caller':'personal-caller','worker':'isolated-worker',
        'investigation':investigation,'endpoint':{'public_url':'https://ncp.example/operational/mcp',
            'audience':'operational-audience','role':'worker','subjects':{'user-1':'personal-caller'}}}
    conf=output/'worker.json';conf.write_bytes(encode(config))
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=output/'fixture-public.pem';public.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    def token(**changes):
        now=int(time.time())
        return jwt.encode({'iss':config['issuer'],'aud':['operational-audience'],'sub':'user-1',
            'iat':now-1,'exp':now+300,'type':'app',**changes},key,algorithm='RS256',headers={'kid':'fixture'})
    headers={'Host':'ncp.example','Origin':'https://ncp.example','Cf-Access-Jwt-Assertion':token()}
    seen={}
    worker_exits=[]
    @contextmanager
    def server():
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        with (output/'worker.log').open('a') as errors:
            process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--worker',
                '--config',str(conf),'--public-key',str(public),'--port',str(port)],
                cwd=output,stdin=subprocess.DEVNULL,stdout=errors,stderr=errors)
            try:
                deadline=time.monotonic()+15
                while True:
                    if process.poll() is not None: raise RuntimeError('Worker failed startup; inspect worker.log')
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.2): break
                    except OSError:
                        if time.monotonic()>deadline: raise RuntimeError('Worker readiness timeout')
                        time.sleep(.05)
                yield f'http://127.0.0.1:{port}/operational/mcp'
            finally:
                if process.poll() is None: process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
                worker_exits.append(process.returncode)
    async def journey(url,reconnect):
        async with httpx.AsyncClient(headers=headers,timeout=30,trust_env=False) as http:
            async with streamable_http_client(url,http_client=http) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=30) as client:
                    await client.discover()
                    async def call(name,args={}):
                        value=await client.call_tool(name,args)
                        if value.is_error: raise RuntimeError((name,value))
                        return value.structured_content
                    catalog=await call('oe_catalog')
                    assert catalog['remote_binding']['worker']=='isolated-worker'
                    selection={'source':'recorded','clock':'synthetic.clock','boot':'boot-1','channel':'temperature','start':0,'end':.5}
                    a=await call('oe_query',selection);b=await call('oe_query',{**selection,'start':.5,'end':2})
                    changed=await call('oe_change',{'baseline':a['record'],'changed':b['record']})
                    run=await call('net_execute',{'source':'model','graph':'model-graph','attempt':'http-first'})
                    finding=await call('oe_model_support',{'attempt':'http-first'})
                    proposal=await call('oe_propose',{'finding':finding['record'],'template':'resolve'})
                    resource=await client.read_resource('ncp://operational-records/'+finding['record'][7:])
                    assert json.loads(resource.contents[0].text)['record']==finding['record']
                    assert changed['data']['configuration_diff'] and finding['data']['status']=='AGREEMENT'
                    assert proposal['data']['discrimination']['hypothesis_tests'] and not proposal['data']['executed']
                    if reconnect:
                        assert run['reused_response'] and run['result_ids']==seen['J3']['result_ids']
                        assert finding['record']==seen['finding']['record']
                    else: seen.update(J1=a,J2=changed,J3=run,finding=finding,J4=proposal)
        # Every transport request passes the actual signed-token boundary.
        async with httpx.AsyncClient(trust_env=False) as http:
            invalid={**headers,'Cf-Access-Jwt-Assertion':token(sub='unbound-caller')}
            denied=await http.post(url,headers=invalid,json={'jsonrpc':'2.0','method':'tools/list','id':1})
            assert denied.status_code==401
        return {'reconnect':reconnect,'worker':catalog['remote_binding'],'result_ids':run['result_ids']}
    sessions=[]
    with server() as url:
        sessions.append(asyncio.run(journey(url,False)))
        sessions.append(asyncio.run(journey(url,True)))
    with server() as url:
        sessions.append(asyncio.run(journey(url,True)))
    marker=output/'kill-next-model'
    async def interrupted(url,retry):
        async with httpx.AsyncClient(headers=headers,timeout=10,trust_env=False) as http:
            async with streamable_http_client(url,http_client=http) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=10) as client:
                    await client.discover()
                    value=await client.call_tool('net_execute',{'source':'model','graph':'model-graph','attempt':'http-killed'})
                    if not retry: raise AssertionError('Abrupt worker death returned a completed call')
                    retained=value.structured_content
                    assert retained['reused_response'] and retained['status']=='incomplete'
                    assert retained['execution_outcome']=='unknown'
                    return retained
    with server() as url:
        marker.write_text('test fixture')
        try: asyncio.run(interrupted(url,False))
        except Exception:
            # The process exit is independently checked below; an unrelated
            # client assertion cannot qualify this failure-path test.
            pass
    assert worker_exits[-1]==77,worker_exits
    marker.unlink()
    with server() as url:
        seen['interrupted_recovery']=asyncio.run(interrupted(url,True))
        assert main(['revoke','--profile',str(profile)])==0
        async def revoked():
            async with httpx.AsyncClient(trust_env=False) as http:
                response=await http.post(url,headers=headers,json={'jsonrpc':'2.0','method':'tools/list','id':1})
                assert response.status_code==401
        asyncio.run(revoked())
    report={'status':'PASS','scope':'installed SDK HTTP over loopback TCP with separate worker process and RSA fixture',
        'sessions':sessions,'journeys':seen,'checks':['J1-J5','resource identity','client reconnect','worker restart',
            'same-attempt recovery','abrupt worker kill retains unknown','no automatic redispatch',
            'wrong-caller denial','revocation'],'worker_exits':worker_exits,'production_TLS_Access_deployment':'not_performed'}
    (output/'report.json').write_bytes(encode(report));print(json.dumps({'status':'PASS','sessions':len(sessions)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--worker',action='store_true');parser.add_argument('--config',type=Path)
    parser.add_argument('--public-key',type=Path);parser.add_argument('--port',type=int)
    args=parser.parse_args()
    if args.worker: worker(args.config,args.public_key,args.port)
    else: run(args.output_dir.resolve())
