"""Cloudflare Access authenticated Streamable HTTP for the existing work host.

Managed OAuth runs at Cloudflare. The origin verifies the signed assertion on
every request; a header or Bot label alone confers no identity. No OAuth server,
new task store, or scientific execution authority is implemented here.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import jwt
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from .agent_api import parse
from .control_contracts import keys
from .external_work import ExternalWorkStore, WorkRefusal, require, text
from .external_work_mcp import ExternalWorkHost


PRINCIPAL = ContextVar("ncp_external_principal", default=None)


def validate_config(config):
    keys(config, {"schema", "database", "issuer", "endpoints"})
    require(config["schema"] == "ciw.external-work-cloudflare.v1", "invalid_configuration")
    text(config["database"], 2048)
    issuer = urlsplit(text(config["issuer"], 512))
    require(issuer.scheme == "https" and issuer.hostname is not None and
            issuer.hostname.endswith(".cloudflareaccess.com") and
            issuer.netloc == issuer.hostname and not issuer.path and not issuer.query and
            not issuer.fragment, "invalid_access_issuer")
    endpoints = config["endpoints"]
    require(type(endpoints) is list and 1 <= len(endpoints) <= 2, "invalid_endpoints")
    paths, audiences, roles = set(), set(), set()
    for endpoint in endpoints:
        keys(endpoint, {"public_url", "audience", "role", "subjects"})
        url = urlsplit(text(endpoint["public_url"], 512))
        require(url.scheme == "https" and url.hostname and url.netloc == url.hostname and
                url.path in ("/requester/mcp", "/worker/mcp") and not url.query and not url.fragment,
                "invalid_public_url")
        require(endpoint["role"] in ("requester", "worker") and
                url.path == "/" + endpoint["role"] + "/mcp", "invalid_endpoint_role")
        audience = text(endpoint["audience"], 128)
        require(audience not in audiences and url.path not in paths and endpoint["role"] not in roles,
                "duplicate_endpoint_binding")
        require(type(endpoint["subjects"]) is dict and len(endpoint["subjects"]) <= 64,
                "invalid_subject_bindings")
        for subject, principal in endpoint["subjects"].items():
            text(subject, 512)
            text(principal, 128)
        paths.add(url.path)
        audiences.add(audience)
        roles.add(endpoint["role"])
    return config


class AccessVerifier:
    """RS256 verification using only the configured Access organization's JWKS."""

    def __init__(self, store, issuer):
        self.store = store
        self.issuer = issuer
        self.jwks = jwt.PyJWKClient(issuer + "/cdn-cgi/access/certs", timeout=5,
                                   cache_keys=False, lifespan=60, cooldown_duration=30)

    def verify(self, token, endpoint):
        require(type(token) is str and 0 < len(token) <= 16384, "authentication_required")
        header = jwt.get_unverified_header(token)
        require(header.get("alg") == "RS256" and type(header.get("kid")) is str and
                0 < len(header["kid"]) <= 256 and not header.get("crit"), "invalid_access_token")
        signing_key = self.jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], issuer=self.issuer,
                            audience=endpoint["audience"],
                            options={"require": ["iss", "aud", "exp", "iat", "sub", "type"]})
        require(claims["type"] == "app" and type(claims["exp"]) is int and
                type(claims["iat"]) is int and claims["exp"] > claims["iat"], "invalid_access_token")
        require(claims["aud"] in (endpoint["audience"], [endpoint["audience"]]),
                "ambiguous_access_audience")
        principal = endpoint["subjects"].get(claims["sub"])
        require(principal is not None, "subject_not_bound")
        with self.store._transaction() as db:
            self.store._client(db, principal, endpoint["role"])
        return principal


class AccessBoundary:
    def __init__(self, app, verifier, endpoint):
        self.app, self.verifier, self.endpoint = app, verifier, endpoint
        url = urlsplit(endpoint["public_url"])
        self.host, self.origin = url.netloc, "https://" + url.netloc

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        def values(name):
            return [v.decode("latin1") for k, v in headers if k.lower() == name]
        hosts, origins = values(b"host"), values(b"origin")
        if hosts != [self.host] or (origins and origins != [self.origin]):
            return await JSONResponse({"error": "origin_refused"}, status_code=403)(scope, receive, send)
        assertions = values(b"cf-access-jwt-assertion")
        try:
            require(len(assertions) == 1, "authentication_required")
            principal = await anyio.to_thread.run_sync(self.verifier.verify, assertions[0], self.endpoint)
        except (WorkRefusal, jwt.PyJWTError, ValueError, TypeError, KeyError, OSError):
            # Never echo identity claims, tokens, network exceptions or internal paths.
            return await JSONResponse({"error": "access_denied"}, status_code=401,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)
        token = PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            PRINCIPAL.reset(token)


def endpoint_app(store, verifier, endpoint):
    def host():
        principal = PRINCIPAL.get()
        require(principal is not None, "authentication_required")
        return ExternalWorkHost(store, principal=principal)

    async def list_tools(ctx, params):
        require(params is None or params.cursor is None, "invalid_cursor")
        catalog = await anyio.to_thread.run_sync(lambda: host().descriptions())
        return types.ListToolsResult(tools=[types.Tool.model_validate(item) for item in catalog])

    async def call_tool(ctx, params):
        current = host()
        value = await anyio.to_thread.run_sync(current.call, params.name, params.arguments or {})
        return types.CallToolResult.model_validate(current.mcp_result(params.name, value))

    server = Server("ncp-external-work", version="1.0.0", instructions=ExternalWorkHost.instructions,
                    on_list_tools=list_tools, on_call_tool=call_tool)
    url = urlsplit(endpoint["public_url"])
    app = server.streamable_http_app(
        streamable_http_path=url.path, json_response=True, stateless_http=True,
        max_request_body_size=65536,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=[url.netloc],
            allowed_origins=["https://" + url.netloc]))
    return AccessBoundary(app, verifier, endpoint), app


def create_app(config):
    from contextlib import AsyncExitStack, asynccontextmanager
    from starlette.applications import Starlette
    from starlette.routing import Route

    config = validate_config(config)
    require(Path(config["database"]).is_file(), "database_not_provisioned")
    store = ExternalWorkStore(Path(config["database"]))
    verifier = AccessVerifier(store, config["issuer"])
    apps, routes = [], []
    for endpoint in config["endpoints"]:
        boundary, inner = endpoint_app(store, verifier, endpoint)
        routes.append(Route(urlsplit(endpoint["public_url"]).path, boundary))
        apps.append(inner)

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for inner in apps:
                await stack.enter_async_context(inner.router.lifespan_context(inner))
            yield

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.verifier = verifier
    return app


def main(argv=None):
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args(argv)
    require(1024 <= args.port <= 65535, "invalid_port")
    config = parse(args.config.read_bytes())
    database = Path(config["database"])
    config["database"] = str(database if database.is_absolute() else args.config.parent / database)
    # Never trust arbitrary forwarded headers or open an unauthenticated public bind.
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, proxy_headers=False,
                access_log=False, limit_concurrency=16, timeout_keep_alive=5, log_level="warning")


if __name__ == "__main__":
    main()
