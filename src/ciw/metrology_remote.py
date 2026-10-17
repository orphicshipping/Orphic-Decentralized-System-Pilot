"""Bounded authenticated Streamable HTTP pilot over existing retained AgentHosts.

Operator-bound bearer credentials select isolated investigations. No uploads,
provider selectors, URL fetching, browser endpoint or physical release are exposed.
TLS and real identity provisioning are deployment gates, not inferred here.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from hashlib import sha256
import hmac
from pathlib import Path
from urllib.parse import urlsplit

from .agent_api import encode, parse
from .control_contracts import keys
from .metrology_contract import digest, hash_ref, ref

MAX_BODY = 32*1024
TOOLS = ("net_capabilities","net_execute","net_inspect")


def configuration(path):
    path=Path(path)
    if path.is_symlink():raise ValueError("Remote configuration cannot be a symlink")
    with path.open("rb") as stream:raw=stream.read(16385)
    if len(raw)>16384:raise ValueError("Remote configuration exceeds 16 KiB")
    value=parse(raw)
    keys(value,{"schema","origin","listen","principals"})
    if value["schema"]!="ciw.metrology-remote-config.v1":raise ValueError("Unsupported remote configuration")
    origin=urlsplit(value["origin"])
    if (origin.scheme not in {"http","https"} or not origin.netloc or origin.username or origin.password
            or origin.path or origin.query or origin.fragment):raise ValueError("Require an exact origin")
    if origin.scheme=="http" and origin.hostname not in {"127.0.0.1","localhost"}:
        raise ValueError("Non-loopback remote access requires HTTPS")
    keys(value["listen"],{"host","port"})
    if (value["listen"]["host"]!="127.0.0.1" or type(value["listen"]["port"]) is not int
            or not 1<=value["listen"]["port"]<=65535):raise ValueError("Pilot listener must be bounded loopback")
    principals=value["principals"]
    if type(principals) is not list or len(principals)>16:raise ValueError("At most 16 configured principals")
    names=set();tokens=set();profiles=set()
    for row in principals:
        keys(row,{"id","token_sha256","profile","profile_sha256","resume"})
        ref(row["id"]);hash_ref(row["token_sha256"]);hash_ref(row["profile_sha256"])
        if type(row["resume"]) is not bool or type(row["profile"]) is not str:
            raise ValueError("Invalid operator profile binding")
        p=Path(row["profile"])
        if not p.is_absolute() or p.is_symlink() or not p.is_file():raise ValueError("Require an existing host-selected absolute profile")
        with p.open("rb") as stream:profile_bytes=stream.read(16385)
        if len(profile_bytes)>16384 or digest(profile_bytes)!=row["profile_sha256"]:
            raise ValueError("Frozen investigation profile changed")
        identity=str(p.resolve())
        if row["id"] in names or row["token_sha256"] in tokens or identity in profiles:
            raise ValueError("Principals require isolated profiles and distinct credentials")
        names.add(row["id"]);tokens.add(row["token_sha256"]);profiles.add(identity)
    return value


def create_app(config_path):
    import mcp_types as types
    from mcp.server.lowlevel import Server
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from .agent_tools import descriptions
    from .metrology_workflow import from_profile

    config_path=Path(config_path).absolute();config=configuration(config_path)
    if not config["principals"]:raise ValueError("Configure at least one investigation principal")
    hosts={};managers={};active={};pending=set()
    bindings={row["id"]:{k:v for k,v in row.items() if k!="token_sha256"} for row in config["principals"]}
    outputs=[]
    try:
        for row in config["principals"]:
            p=Path(row["profile"])
            raw=p.read_bytes()
            if len(raw)>16384 or digest(raw)!=row["profile_sha256"]:raise ValueError("Profile bytes differ from frozen binding")
            output=parse(raw)["output_dir"]
            if not output:raise ValueError("Remote pilot requires retained output")
            output_path=(p.parent/output).resolve()
            if any(output_path.is_relative_to(old) or old.is_relative_to(output_path) for old in outputs):
                raise ValueError("Investigations cannot share or nest retained output directories")
            outputs.append(output_path)
            hosts[row["id"]]=from_profile(p,resume=row["resume"])
            active[row["id"]]=0
    except BaseException:
        for host in hosts.values():host.close()
        raise

    selected=[t for t in descriptions() if t["name"] in TOOLS]
    schemas={t["name"]:t["inputSchema"] for t in selected}

    def server_for(principal):
        host=hosts[principal]
        async def listed(ctx,params):
            return types.ListToolsResult(tools=[types.Tool(**t) for t in selected])
        async def called(ctx,params):
            try:
                if params.name not in TOOLS:raise ValueError("Tool is outside remote grants")
                arguments=params.arguments or {}
                if params.name=="net_execute" and (arguments.get("source"),arguments.get("graph"))!=("source","baseline"):
                    raise ValueError("Remote execution uses only its host-bound inspection and fixed graph")
                if active[principal]>=2:raise ValueError("Investigation job limit reached")
                active[principal]+=1
                task=asyncio.create_task(asyncio.to_thread(host.call,params.name,arguments))
                pending.add(task)
                def finished(done):
                    active[principal]-=1;pending.discard(done)
                    if not done.cancelled():done.exception()  # Consume unknown interrupted-call failures.
                task.add_done_callback(finished)
                result=await asyncio.shield(task)
                return types.CallToolResult(content=[types.TextContent(type="text",text=encode(result).decode())],structured_content=result)
            except (ValueError,KeyError,IndexError):
                # Do not disclose profile paths, tenant names or provider internals.
                return types.CallToolResult(is_error=True,content=[types.TextContent(type="text",text="Metrology request refused; inspect arguments, grants and retained attempt state")])
        return Server("NCP bounded metrology",version="1",on_list_tools=listed,on_call_tool=called,
                      get_tool_input_schema=lambda name:schemas.get(name))

    origin=urlsplit(config["origin"])
    security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
        allowed_hosts=[origin.netloc],allowed_origins=[config["origin"]])
    for principal in hosts:
        managers[principal]=StreamableHTTPSessionManager(server_for(principal),json_response=True,
            stateless=True,security_settings=security,max_request_body_size=MAX_BODY)

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with AsyncExitStack() as stack:
                for manager in managers.values():await stack.enter_async_context(manager.run())
                yield
        finally:
            if pending:await asyncio.gather(*list(pending),return_exceptions=True)
            for host in hosts.values():host.close()
    shell=Starlette(lifespan=lifespan)
    in_flight={key:0 for key in hosts}

    async def reject(scope,receive,send,status):
        response=JSONResponse({"error":"request_not_authorized_or_outside_pilot_limits"},status_code=status,
                              headers={"Cache-Control":"no-store","WWW-Authenticate":"Bearer realm=metrology"})
        await response(scope,receive,send)

    async def app(scope,receive,send):
        if scope["type"]=="lifespan":return await shell(scope,receive,send)
        if scope["type"]!="http":return
        if scope.get("path")!="/mcp" or scope.get("query_string"):
            return await reject(scope,receive,send,404)
        headers=scope.get("headers",[])
        def values(name):return [v for k,v in headers if k.lower()==name]
        auth=values(b"authorization")
        if len(auth)!=1 or not auth[0].startswith(b"Bearer ") or not 32<=len(auth[0][7:])<=512:
            return await reject(scope,receive,send,401)
        candidate="sha256:"+sha256(auth[0][7:]).hexdigest()
        try:
            current=configuration(config_path)
            if {k:current[k] for k in ("origin","listen")}!={k:config[k] for k in ("origin","listen")}:
                raise ValueError("Server binding changed")
            principal=None
            for row in current["principals"]:
                if row["id"] not in bindings or {k:v for k,v in row.items() if k!="token_sha256"}!=bindings[row["id"]]:
                    raise ValueError("Investigation binding changed")
                if hmac.compare_digest(candidate,row["token_sha256"]):principal=row["id"]
        except (OSError,ValueError,KeyError):return await reject(scope,receive,send,503)
        if principal is None:return await reject(scope,receive,send,401)
        if values(b"host")!=[origin.netloc.encode()] or (values(b"origin") and values(b"origin")!=[config["origin"].encode()]):
            return await reject(scope,receive,send,403)
        if in_flight[principal]>=2:return await reject(scope,receive,send,429)
        in_flight[principal]+=1
        try:
            body=bytearray()
            while True:
                event=await receive()
                if event["type"]=="http.disconnect":return
                body.extend(event.get("body",b""))
                if len(body)>MAX_BODY:return await reject(scope,receive,send,413)
                if not event.get("more_body",False):break
            if body:
                try:parse(bytes(body))
                except (ValueError,UnicodeError,RecursionError):return await reject(scope,receive,send,400)
            delivered=False
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered=True
                    return {"type":"http.request","body":bytes(body),"more_body":False}
                return await receive()
            return await managers[principal].asgi_app(scope,replay,send)
        finally:in_flight[principal]-=1
    return app


def main(argv=None):
    import argparse
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("configuration")
    args=parser.parse_args(argv);config=configuration(args.configuration)
    uvicorn.run(create_app(args.configuration),host=config["listen"]["host"],port=config["listen"]["port"],
                proxy_headers=False,access_log=False)


if __name__=="__main__":main()
