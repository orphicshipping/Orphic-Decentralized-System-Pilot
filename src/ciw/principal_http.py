"""Native MCP OAuth resource server over current host-bound principal facades.

No authorization server, token issuer, shared credential or stdio subprocess is
created here. The trusted launcher supplies issuer, resource, bindings and hosts.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
import re
import ssl
from threading import RLock
import time
from types import MappingProxyType
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

import anyio
import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from .campaign_contracts import Limits, REQUEST_LIMITS, closed_object, parse, text
from .mcp_server import create_server
from .principal_policy import PrincipalFacade, _read_policy, owner_for
from .audited_host import AuditedPrincipalHost

_JSON_LIMITS = Limits(max_bytes=131072, max_nodes=8192, max_array=256)
_SCOPE = re.compile(r"[A-Za-z0-9:._/-]{1,128}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")


class AuthenticationRefused(ValueError):
    """Data-independent refusal; never includes tokens, claims or remote bodies."""


def _require(value):
    if not value:
        raise AuthenticationRefused("oauth_authentication_refused")


def _https(value):
    text(value, 2048)
    uri = urlsplit(value)
    _require(uri.scheme == "https" and uri.hostname and uri.username is None and uri.password is None
             and not uri.query and not uri.fragment and not any(c.isspace() for c in value)
             and uri.netloc == uri.netloc.lower() and "\\" not in value and "%" not in value
             and all(part not in (".", "..") for part in uri.path.split("/")))
    if uri.port is not None:
        _require(1 <= uri.port <= 65535)
    return uri


@dataclass(frozen=True)
class OAuthSettings:
    """Fixed host configuration. RS256 RFC 9068 JWT access tokens only."""

    resource: str
    issuer: str
    required_scopes: tuple[str, ...] = ("odsp:invoke",)
    max_token_seconds: int = 3600

    def __post_init__(self):
        resource, issuer = _https(self.resource), _https(self.issuer)
        _require(resource.path.startswith("/") and not resource.path.endswith("/")
                 and not resource.path.startswith("/.well-known/") and "//" not in resource.path
                 and not issuer.path.endswith("/") and "//" not in issuer.path)
        _require(type(self.required_scopes) is tuple and 1 <= len(self.required_scopes) <= 16
                 and all(type(s) is str and _SCOPE.fullmatch(s) for s in self.required_scopes)
                 and len(set(self.required_scopes)) == len(self.required_scopes))
        _require(type(self.max_token_seconds) is int and 1 <= self.max_token_seconds <= 3600)

    @property
    def metadata_url(self):
        uri = urlsplit(self.resource)
        return f"https://{uri.netloc}/.well-known/oauth-protected-resource{uri.path}"


class SubjectBindings:
    """Fresh host-owned subject mapping/revocation; never supplied by a caller.

    not_before invalidates all older tokens for a subject. Setting enabled=false
    or removing the subject denies subsequent requests, including reconnects.
    """

    def __init__(self, read_bindings):
        _require(callable(read_bindings))
        self._reader = read_bindings

    @classmethod
    def from_file(cls, path):
        from pathlib import Path
        path = Path(path)
        _require(path.is_absolute())
        return cls(lambda: _read_policy(path))

    def principal(self, subject, issued_at):
        document = self._reader()
        closed_object(document, {"schema", "revision", "subjects"})
        _require(document["schema"] == "ciw.oauth-subjects.v1")
        text(document["revision"], 128)
        rows = document["subjects"]
        _require(type(rows) is list and len(rows) <= 64)
        names, result = set(), None
        for row in rows:
            closed_object(row, {"subject", "principal", "enabled", "not_before"})
            text(row["subject"], 512)
            owner_for(row["principal"])
            _require(row["subject"] not in names and type(row["enabled"]) is bool
                     and type(row["not_before"]) is int and 0 <= row["not_before"] < 2**53)
            names.add(row["subject"])
            if row["subject"] == subject and row["enabled"] and issued_at >= row["not_before"]:
                result = row["principal"]
        _require(result is not None)
        return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise AuthenticationRefused("oauth_metadata_redirect_refused")


class JWKAuthority:
    """Bounded HTTPS metadata/JWKS discovery, fixed issuer, public RSA keys.

    Discovery supports RFC 8414 and OIDC path insertion/appending. Only 404/405
    permit discovery fallback. Redirects, issuer mismatch and off-origin JWKS
    fail closed. Keys expire after 60 seconds; no stale-key fallback on failure.
    An unknown kid permits one refresh per ten seconds, limiting fetch pressure.
    """

    def __init__(self, settings: OAuthSettings, *, ssl_context=None):
        self.settings = settings
        context = ssl_context or ssl.create_default_context()
        _require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname)
        self._opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=context))
        self._lock = RLock()
        self._keys, self._expires, self._attempted = {}, 0.0, float("-inf")

    def _read(self, url):
        uri, issuer = _https(url), urlsplit(self.settings.issuer)
        _require(uri.netloc == issuer.netloc)
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        deadline = time.monotonic() + 2
        try:
            with self._opener.open(request, timeout=2) as response:
                _require(response.status == 200 and response.headers.get_content_type() == "application/json"
                         and response.headers.get("Content-Encoding", "identity") == "identity")
                raw = bytearray()
                while True:
                    _require(time.monotonic() < deadline)
                    block = response.read1(min(4096, _JSON_LIMITS.max_bytes + 1 - len(raw)))
                    _require(time.monotonic() < deadline)
                    if not block:
                        break
                    raw.extend(block)
                    _require(len(raw) <= _JSON_LIMITS.max_bytes)
        except HTTPError as exc:
            exc.close()
            raise
        return parse(bytes(raw), limits=_JSON_LIMITS)

    def _refresh(self):
        self._keys, self._expires = {}, 0.0
        self._attempted = time.monotonic()
        issuer = urlsplit(self.settings.issuer)
        origin = f"https://{issuer.netloc}"
        paths = ["/.well-known/oauth-authorization-server" + issuer.path,
                 "/.well-known/openid-configuration" + issuer.path]
        if issuer.path:
            paths.append(issuer.path + "/.well-known/openid-configuration")
        metadata = None
        for path in paths:
            try:
                metadata = self._read(origin + path)
                break
            except HTTPError as exc:
                if exc.code not in (404, 405):
                    raise AuthenticationRefused("oauth_metadata_unavailable") from None
        _require(type(metadata) is dict and metadata.get("issuer") == self.settings.issuer)
        jwks_url = metadata.get("jwks_uri")
        _require(type(jwks_url) is str)
        jwks = self._read(jwks_url)
        closed_object(jwks, {"keys"})
        _require(type(jwks["keys"]) is list and 1 <= len(jwks["keys"]) <= 32)
        seen, keys = set(), {}
        for raw in jwks["keys"]:
            _require(type(raw) is dict)
            kid = text(raw.get("kid"), 256)
            _require(kid not in seen)
            seen.add(kid)
            # Never accept private/symmetric material or let token headers choose a key URL.
            _require(not set(raw) & {"d", "p", "q", "dp", "dq", "qi", "oth", "k"})
            if raw.get("kty") != "RSA" or raw.get("use", "sig") != "sig" or raw.get("alg", "RS256") != "RS256":
                continue
            _require("key_ops" not in raw or raw["key_ops"] == ["verify"])
            key = jwt.PyJWK.from_dict(raw, algorithm="RS256").key
            _require(2048 <= key.key_size <= 8192)
            keys[kid] = key
        _require(keys)
        self._keys, self._expires = keys, time.monotonic() + 60

    def prepare(self):
        with self._lock:
            self._refresh()

    def key(self, kid):
        with self._lock:
            now = time.monotonic()
            if now >= self._expires or kid not in self._keys:
                _require(now - self._attempted >= 10)
                self._refresh()
            _require(kid in self._keys)
            return self._keys[kid]


class PrincipalTokenVerifier:
    """Official SDK TokenVerifier with strict JWT and current principal checks."""

    def __init__(self, settings, subjects, facades, authority):
        self.settings, self.subjects, self.authority = settings, subjects, authority
        _require(type(facades) is dict and 1 <= len(facades) <= 64)
        underlying = {}
        for principal, host in facades.items():
            _require(type(host) in (PrincipalFacade, AuditedPrincipalHost))
            facade = host._check_identity() if type(host) is AuditedPrincipalHost else host
            _require(type(facade) is PrincipalFacade and facade.principal == principal)
            facade.policy.check_principal(principal)
            underlying[principal] = facade
        self.facades = MappingProxyType(dict(facades))
        self._underlying = MappingProxyType(underlying)

    def _facade(self, principal):
        host = self.facades[principal]
        facade = host._check_identity() if type(host) is AuditedPrincipalHost else host
        _require(facade is self._underlying[principal] and facade.principal == principal)
        facade.policy.check_principal(principal)
        return facade

    def current(self, token):
        _require(token is not None and token.expires_at > int(time.time())
                 and token.resource == self.settings.resource and token.claims["iss"] == self.settings.issuer
                 and set(self.settings.required_scopes) <= set(token.scopes))
        principal = self.subjects.principal(token.subject, token.claims["iat"])
        _require(principal == token.claims["principal"] and principal in self.facades)
        self._facade(principal)
        return self.facades[principal]

    def _verify(self, raw):
        _require(type(raw) is str and len(raw) <= 16384 and _TOKEN.fullmatch(raw))
        header, body, _ = raw.split(".")
        header = parse(jwt.utils.base64url_decode(header.encode()), limits=REQUEST_LIMITS)
        # Parse payload before PyJWT to reject ambiguous duplicate claims and scalar coercions.
        claims = parse(jwt.utils.base64url_decode(body.encode()), limits=REQUEST_LIMITS)
        closed_object(header, {"alg", "kid", "typ"})
        _require(header["alg"] == "RS256" and header["typ"] == "at+jwt")
        text(header["kid"], 256)
        key = self.authority.key(header["kid"])
        decoded = jwt.decode(raw, key, algorithms=["RS256"], issuer=self.settings.issuer,
                             audience=self.settings.resource,
                             options={"require": ["iss", "aud", "exp", "iat", "sub", "client_id", "jti", "scope"]})
        _require(decoded == claims and type(claims) is dict)
        _require(claims["aud"] in (self.settings.resource, [self.settings.resource]))
        now = int(time.time())
        _require(type(claims["iat"]) is int and type(claims["exp"]) is int
                 and 0 <= claims["iat"] <= now < claims["exp"]
                 and 0 < claims["exp"] - claims["iat"] <= self.settings.max_token_seconds)
        if "nbf" in claims:
            _require(type(claims["nbf"]) is int and 0 <= claims["nbf"] <= now)
        for field in ("sub", "client_id", "jti"):
            text(claims[field], 512)
        scope = text(claims["scope"], 2048).split(" ")
        _require(len(scope) <= 64 and all(_SCOPE.fullmatch(s) for s in scope) and len(set(scope)) == len(scope))
        principal = self.subjects.principal(claims["sub"], claims["iat"])
        _require(principal in self.facades)
        self._facade(principal)
        # The raw credential is retained only in SDK auth context; it never reaches a provider.
        return AccessToken(token=raw, client_id=claims["client_id"], scopes=scope,
                           expires_at=claims["exp"], resource=self.settings.resource, subject=claims["sub"],
                           claims={"iss": self.settings.issuer, "iat": claims["iat"], "principal": principal})

    async def verify_token(self, token):
        try:
            return await anyio.to_thread.run_sync(self._verify, token)
        except Exception:
            # Signature, metadata, binding, policy and transport errors disclose nothing.
            return None


class _RequestHost:
    instructions = PrincipalFacade.instructions
    mcp_result = staticmethod(PrincipalFacade.mcp_result)
    refusal = staticmethod(PrincipalFacade.refusal)

    def __init__(self, verifier):
        self.verifier = verifier

    def current(self):
        return self.verifier.current(get_access_token())

    def _guarded(self, invoke):
        token = get_access_token()
        host = self.verifier.current(token)
        def current():
            return self.verifier.current(token) is host
        # The existing audit gate checks this at dispatch after durable audit
        # I/O, and before disclosure. Never unwrap it to make a tool call.
        guard = host.scope_guard(current) if type(host) is AuditedPrincipalHost else nullcontext()
        with guard:
            value = invoke(host)
            try:
                _require(current())
            except Exception:
                # Preserve only the audit gate's fixed public failure code.
                # A provider refusal can still contain protected data/locators;
                # refusal status alone must never bypass the disclosure check.
                if (type(host) is AuditedPrincipalHost and type(value) is dict
                        and value.get('status') == 'refused' and value.get('data') is None
                        and type(value.get('error')) is dict
                        and value['error'].get('code') in ('audit_unavailable', 'outcome_unknown')):
                    return {'status': 'refused', 'data': None, 'error': {'code': value['error']['code']},
                            'authorizes_execution': False}
                raise
            return value

    def descriptions(self):
        return self._guarded(lambda host: host.descriptions())

    def call(self, name, arguments):
        return self._guarded(lambda host: host.call(name, arguments))

    def read_resource(self, uri):
        return self._guarded(lambda host: host.read_resource(uri))

    def list_resources(self):
        return self._guarded(lambda host: host.resource_descriptions() if type(host) is AuditedPrincipalHost
                             else host.list_resources())

    @property
    def resource_templates(self):
        return self._guarded(lambda host: host.resource_templates() if type(host) is AuditedPrincipalHost
                             else host.resource_templates)


class _HTTPBoundary:
    """Fixed authority, no cookie/header identity fallback, bounded exact input."""

    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        uri = urlsplit(self.settings.resource)
        headers = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value)
        async def refuse(status, code):
            await JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})(scope, receive, send)
        if headers.get(b"host") != [uri.netloc.encode()] or (b"origin" in headers and
                headers[b"origin"] != [("https://" + uri.netloc).encode()]):
            return await refuse(403, "origin_refused")
        sensitive = (b"authorization", b"content-type", b"content-length", b"mcp-protocol-version",
                     b"mcp-method", b"mcp-name")
        if any(len(headers.get(key, [])) > 1 for key in sensitive) or scope.get("query_string"):
            return await refuse(400, "ambiguous_request")
        # The current protocol has no sessions. Legacy stateless requests also
        # authenticate independently; stale/foreign session IDs are not authority.
        if b"mcp-session-id" in headers or b"last-event-id" in headers:
            return await refuse(400, "session_not_supported")
        if scope["path"] == uri.path and scope["method"] == "POST":
            try:
                raw = bytearray()
                with anyio.fail_after(5):
                    while True:
                        message = await receive()
                        _require(message["type"] == "http.request")
                        block = message.get("body", b"")
                        if len(raw) + len(block) > REQUEST_LIMITS.max_bytes:
                            return await refuse(413, "request_too_large")
                        raw.extend(block)
                        if not message.get("more_body", False):
                            break
                value = parse(bytes(raw))
                _require(type(value) is dict and value.get("jsonrpc") == "2.0"
                         and type(value.get("method")) is str
                         and not set(value) - {"jsonrpc", "id", "method", "params"})
                if "id" in value:
                    _require(type(value["id"]) in (str, int) and
                             (type(value["id"]) is int or 1 <= len(value["id"]) <= 128))
                _require("params" not in value or type(value["params"]) is dict)
            except TimeoutError:
                return await refuse(408, "request_timeout")
            except Exception:
                return await refuse(400, "invalid_request")
            consumed = False
            original_receive = receive
            async def buffered_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(raw), "more_body": False}
                return await original_receive()
            receive = buffered_receive
        async def private_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                response_headers = [(k, v) for k, v in message.get("headers", [])
                                    if k.lower() not in (b"cache-control", b"vary")]
                challenge_scope = ('scope="' + " ".join(self.settings.required_scopes) + '"').encode()
                response_headers = [(k, v + b", " + challenge_scope if k.lower() == b"www-authenticate" else v)
                                    for k, v in response_headers]
                message["headers"] = response_headers + [(b"cache-control", b"no-store"),
                                                           (b"vary", b"Authorization, Origin")]
            await send(message)
        await self.app(scope, receive, private_send)


def create_app(settings, *, facades, subjects, authority=None):
    """Construct in trusted launcher code; caller credentials never select hosts.

    The optional authority is for a host-owned TLS trust configuration, not a
    request option. App startup validates real AS metadata/JWKS before serving.
    Values in facades may be existing AuditedPrincipalHost wrappers. They remain
    the invocation authority; unwrapping is only for binding/current-policy checks.
    """
    _require(type(settings) is OAuthSettings and type(subjects) is SubjectBindings)
    authority = authority or JWKAuthority(settings)
    _require(type(authority) is JWKAuthority and authority.settings == settings)
    verifier = PrincipalTokenVerifier(settings, subjects, facades, authority)
    server = create_server(_RequestHost(verifier), name="odsp-principal-http")
    uri = urlsplit(settings.resource)
    app = server.streamable_http_app(
        streamable_http_path=uri.path, json_response=True, stateless_http=True,
        max_request_body_size=REQUEST_LIMITS.max_bytes,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[uri.netloc], allowed_origins=["https://" + uri.netloc]),
        auth=AuthSettings(issuer_url=settings.issuer, resource_server_url=settings.resource,
                          required_scopes=list(settings.required_scopes), validate_token_resource=True),
        token_verifier=verifier)
    sdk_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        await anyio.to_thread.run_sync(authority.prepare)
        async with sdk_lifespan(application):
            yield
    app.router.lifespan_context = lifespan
    app.add_middleware(_HTTPBoundary, settings=settings)
    return app


def main(argv=None):
    """Single-principal launcher. Multi-principal deployments use create_app."""
    import argparse
    from pathlib import Path
    import sys
    import uvicorn
    from .agent_mcp import INSTRUMENTS, from_profile, native_bindings
    from .control_gateway import ControlHost
    from .principal_policy import PrincipalPolicy

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--scope", action="append", dest="scopes", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--subjects", type=Path, required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--instrument", choices=INSTRUMENTS, default="builtin")
    parser.add_argument("--provider-checkout", type=Path)
    parser.add_argument("--provider-revision")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        _require(1024 <= args.port <= 65535)
        settings = OAuthSettings(args.resource, args.issuer, tuple(args.scopes))
        policy = PrincipalPolicy.from_file(args.policy)
        subjects = SubjectBindings.from_file(args.subjects)
        policy.check_principal(args.principal)
        bindings = native_bindings(args.instrument, checkout=args.provider_checkout,
                                   revision=args.provider_revision, python=args.python)
        with from_profile(args.profile, instrument=args.instrument, resume=args.resume, **bindings) as owner:
            with ControlHost(owner, resume=args.resume, retained_owner=owner_for(args.principal)) as control:
                app = create_app(settings, facades={args.principal: policy.facade(args.principal, control=control)},
                                 subjects=subjects)
                server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                    proxy_headers=False, access_log=False, log_level="critical", limit_concurrency=16,
                    timeout_keep_alive=5, timeout_graceful_shutdown=10))
                server.run()
                return 0 if server.started else 1
    except Exception:
        print("ODSP principal HTTP startup refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
