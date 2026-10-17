"""Real SDK HTTP/stdio journeys; local synthetic data, never hosted deployment."""
import asyncio
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

from ciw.agent_api import encode, parse
from ciw.metrology_contract import digest, example
from ciw.metrology_workflow import prepare


@contextmanager
def running(config_path):
    import uvicorn
    from ciw.metrology_remote import create_app
    config=parse(config_path.read_bytes())
    server=uvicorn.Server(uvicorn.Config(create_app(config_path),host="127.0.0.1",port=config["listen"]["port"],log_level="error",access_log=False,proxy_headers=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.01)
    assert server.started
    try:yield
    finally:
        server.should_exit=True;thread.join(10);assert not thread.is_alive()


def configured(tmp_path):
    with socket.socket() as s:s.bind(("127.0.0.1",0));port=s.getsockname()[1]
    rows=[];tokens=["a"*48,"b"*48]
    for i,token in enumerate(tokens):
        raw=example("sampled-plane.v1" if i==0 else "freight-envelope.v1")
        raw["inspection_ref"]=f"inspection-{i}";p=prepare(encode(raw),tmp_path/f"case-{i}")
        rows.append({"id":f"tenant-{i}","token_sha256":"sha256:"+sha256(token.encode()).hexdigest(),
            "profile":str(p),"profile_sha256":digest(p.read_bytes()),"resume":False})
    config={"schema":"ciw.metrology-remote-config.v1","origin":f"http://127.0.0.1:{port}",
        "listen":{"host":"127.0.0.1","port":port},"principals":rows}
    p=tmp_path/"remote.json";p.write_bytes(encode(config));return p,config,tokens


def test_real_http_sdk_isolation_limits_revocation_and_restart(tmp_path):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    p,config,tokens=configured(tmp_path);url=config["origin"]+"/mcp"
    async def journey():
        artifacts=[];executions=[]
        for token in tokens:
            async with httpx2.AsyncClient(headers={"Authorization":"Bearer "+token},trust_env=False) as http:
                async with streamable_http_client(url,http_client=http) as streams:
                    async with ClientSession(streams[0],streams[1]) as client:
                        await client.initialize()
                        assert {t.name for t in (await client.list_tools()).tools}=={"net_capabilities","net_execute","net_inspect"}
                        bad=await client.call_tool("net_execute",{"source":"source","graph":"baseline","attempt":"bad","provider":"arbitrary"})
                        assert bad.is_error
                        result=await client.call_tool("net_execute",{"source":"source","graph":"baseline","attempt":"same"})
                        assert not result.is_error,result
                        body=result.structured_content;assert body["status"]=="completed"
                        executions.append(body["execution_ids"])
                        repeated=await client.call_tool("net_execute",{"source":"source","graph":"baseline","attempt":"same"})
                        assert repeated.structured_content["reused_response"]
                        own=await client.call_tool("net_inspect",{"artifact":body["artifacts"]["workspace"]["artifact_id"]})
                        assert not own.is_error
                        artifacts.append(body)
        # The second principal cannot read the first principal's receipt handle.
        handle=artifacts[0]["artifacts"]["workspace"]["artifact_id"]
        async with httpx2.AsyncClient(headers={"Authorization":"Bearer "+tokens[1]},trust_env=False) as http:
            async with streamable_http_client(url,http_client=http) as streams:
                async with ClientSession(streams[0],streams[1]) as client:
                    await client.initialize();assert (await client.call_tool("net_inspect",{"artifact":handle})).is_error
        async with httpx2.AsyncClient(trust_env=False) as http:
            assert (await http.post(url,json={})).status_code==401
            auth={"Authorization":"Bearer "+tokens[0]}
            assert (await http.post(url,headers={**auth,"Origin":"https://foreign.invalid"},json={})).status_code==403
            assert (await http.post(url,headers=auth,content=b"x"*32769)).status_code==413
            assert (await http.post(url,headers=auth,content=b'{"a":1,"a":2}')).status_code==400
            config["principals"]=config["principals"][1:];p.write_bytes(encode(config))
            assert (await http.post(url,headers=auth,json={})).status_code==401
            profile=Path(config["principals"][0]["profile"]);profile.write_bytes(profile.read_bytes()+b" ")
            assert (await http.post(url,headers={"Authorization":"Bearer "+tokens[1]},json={})).status_code==503
            profile.write_bytes(profile.read_bytes()[:-1])
        return executions
    with running(p):first=asyncio.run(journey())
    # Explicit host resume restores the recorded attempt, never a fresh operation.
    config["principals"][0]["resume"]=True;p.write_bytes(encode(config))
    async def resumed():
        async with httpx2.AsyncClient(headers={"Authorization":"Bearer "+tokens[1]},trust_env=False) as http:
            async with streamable_http_client(url,http_client=http) as streams:
                async with ClientSession(streams[0],streams[1]) as client:
                    await client.initialize();r=await client.call_tool("net_execute",{"source":"source","graph":"baseline","attempt":"same"})
                    assert r.structured_content["execution_ids"]==first[1]
                    assert r.structured_content["reused_response"]
    with running(p):asyncio.run(resumed())


@pytest.mark.parametrize("profile",["sampled-plane.v1","freight-envelope.v1","visible-lean.v1"])
def test_real_stdio_sdk_subprocess(tmp_path,profile):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import ciw
    p=prepare(encode(example(profile)),tmp_path/"case")
    args=["-m","ciw.metrology_workflow","serve",str(p),"--sdk"]
    env=dict(os.environ,PYTHONPATH=str(Path(ciw.__file__).parent.parent),PYTHONNOUSERSITE="1")
    async def journey():
        async with stdio_client(StdioServerParameters(command=sys.executable,args=args,env=env,cwd=str(tmp_path))) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                r=await client.call_tool("net_execute",{"source":"source","graph":"baseline","attempt":"sdk"})
                assert not r.is_error and r.structured_content["status"]=="completed"
                assert (await client.call_tool("net_execute",{"source":"source","graph":"baseline","attempt":"sdk"})).structured_content["reused_response"]
    asyncio.run(journey())
