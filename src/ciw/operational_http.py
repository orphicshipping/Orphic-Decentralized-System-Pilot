"""Operational MCP on the existing Cloudflare Access transport boundary.

No public bind or credential provisioning. A fixed worker/caller/investigation
mapping is checked on every request, including retries after revocation.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .agent_api import parse, identifier
from .control_contracts import keys
from .external_work import require
from .external_work_http import AccessVerifier, AccessBoundary, PRINCIPAL
from .operational_agent import OperationalHost, SPEC

REMOTE_TOOLS = frozenset(SPEC) | {'net_capabilities','net_inspect','net_observe','net_execute','net_replay'}


class Binding:
    """Minimal AccessVerifier client lookup seam; no competing task database."""
    def __init__(self, host, principal, worker):
        self.host=host; self.principal=principal; self.worker=worker

    @contextmanager
    def _transaction(self): yield self

    def _client(self, db, principal, role):
        require(principal==self.principal and role=='worker','caller_binding_refused')
        self.host._check('inspect')


class RemoteHost:
    def __init__(self, host, binding): self.host=host; self.binding=binding
    instructions=OperationalHost.instructions
    resource_templates=OperationalHost.resource_templates

    def read_resource(self,uri):
        self._check()
        return self.host.read_resource(uri)

    def descriptions(self):
        self._check()
        return [t for t in self.host.descriptions() if t['name'] in REMOTE_TOOLS]

    def _check(self):
        require(PRINCIPAL.get()==self.binding.principal,'caller_binding_refused')
        self.host._check('inspect')

    def call(self, name, args):
        self._check()
        require(name in REMOTE_TOOLS,'remote_method_refused')
        v=self.host.call(name,args)
        return {**v,'remote_binding':{'worker':self.binding.worker,'caller':self.binding.principal,
                'investigation':self.host.spec['investigation']}}


def create_app(config, *, host=None, verifier_factory=AccessVerifier):
    from .mcp_server import create_server
    from mcp.server.transport_security import TransportSecuritySettings
    keys(config,{'schema','profile','resume','issuer','endpoint','caller','worker','investigation'})
    require(config['schema']=='ncp.operational-access.v1','invalid_configuration')
    identifier(config['caller']);identifier(config['worker'])
    issuer=urlsplit(config['issuer'])
    require(issuer.scheme=='https' and issuer.hostname and issuer.hostname.endswith('.cloudflareaccess.com')
        and issuer.netloc==issuer.hostname and not issuer.path and not issuer.query and not issuer.fragment,'invalid_issuer')
    endpoint=config['endpoint'];keys(endpoint,{'public_url','audience','role','subjects'})
    url=urlsplit(endpoint['public_url'])
    require(url.scheme=='https' and url.netloc==url.hostname and url.path=='/operational/mcp'
            and not url.query and not url.fragment,'invalid_endpoint')
    require(endpoint['role']=='worker' and type(endpoint['audience']) is str and 0<len(endpoint['audience'])<=128,
            'invalid_audience_or_role')
    require(type(endpoint['subjects']) is dict and 1<=len(endpoint['subjects'])<=16
        and set(endpoint['subjects'].values())=={config['caller']},'invalid_subject_bindings')
    for subject in endpoint['subjects']:
        require(type(subject) is str and 0<len(subject)<=512,'invalid_subject_binding')
    if host is None: host=OperationalHost(Path(config['profile']),resume=config['resume'])
    require(host.spec['investigation']==config['investigation'],'investigation_binding_refused')
    binding=Binding(host,config['caller'],config['worker'])
    verifier=verifier_factory(binding,config['issuer'])
    server=create_server(RemoteHost(host,binding),name='ncp-operational-remote')
    app=server.streamable_http_app(streamable_http_path=url.path,json_response=True,stateless_http=True,
        max_request_body_size=65536,transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,allowed_hosts=[url.netloc],allowed_origins=['https://'+url.netloc]))
    boundary=AccessBoundary(app,verifier,endpoint)
    # Preserve the SDK lifespan when wrapping its ASGI application.
    app.add_middleware(_BoundaryMiddleware,verifier=verifier,endpoint=endpoint)
    app.state.operational_host=host
    app.state.access_verifier=verifier
    return app


class _BoundaryMiddleware:
    def __init__(self,app,verifier,endpoint):self.inner=AccessBoundary(app,verifier,endpoint)
    async def __call__(self,scope,receive,send):await self.inner(scope,receive,send)


def main(argv=None):
    import argparse
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--port',type=int,default=18767)
    args=parser.parse_args(argv)
    if not 1024<=args.port<=65535:raise ValueError('Invalid loopback port')
    config=parse(args.config.read_bytes());config['profile']=str(args.config.parent/config['profile'])
    app=create_app(config)
    try:
        uvicorn.run(app,host='127.0.0.1',port=args.port,proxy_headers=False,access_log=False,
                    limit_concurrency=8,timeout_keep_alive=5,log_level='warning')
    finally:app.state.operational_host.close()
    return 0
