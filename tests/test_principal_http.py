"""Real TLS issuer discovery and native SDK transport over principal-bound hosts.

The local issuer fixture serves metadata/public keys only. Its ephemeral test
signatures do not qualify a live provider, OAuth consent flow or hosted service.
"""
import asyncio
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import socket
import ssl
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
import httpx2
import jwt
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient
import uvicorn

from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.principal_http import OAuthSettings, SubjectBindings, JWKAuthority, create_app
from ciw.principal_policy import PrincipalPolicy, owner_for


@contextmanager
def running(app, *, listener=None, tls=None):
    listener = listener or socket.socket()
    if not listener.getsockname()[1]:
        listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    options = {} if tls is None else {"ssl_certfile": str(tls[0]), "ssl_keyfile": str(tls[1])}
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False,
                                         proxy_headers=False, limit_concurrency=16,
                                         timeout_graceful_shutdown=2, **options))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, "local server did not start"
        yield port
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive(), "local server did not stop"


@pytest.fixture
def issuer(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "local-test-only")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(tls_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(minutes=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(tls_key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "ca.pem", tmp_path / "tls.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(tls_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    key_path.chmod(0o600)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = "https://127.0.0.1:" + str(listener.getsockname()[1])
    issuer_url = origin + "/tenant"
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-key", alg="RS256", use="sig", key_ops=["verify"])
    state = {"metadata": {"issuer": issuer_url, "jwks_uri": origin + "/keys",
                           "authorization_endpoint": origin + "/authorize", "token_endpoint": origin + "/token",
                           "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"]},
             "jwks": {"keys": [jwk]}, "paths": [], "fallback": False, "redirect": False, "broken": False}
    async def metadata(request):
        state["paths"].append(request.url.path)
        if state["redirect"]:
            return RedirectResponse("https://untrusted.invalid/keys")
        if state["fallback"] and "oauth-authorization-server" in request.url.path:
            return Response(status_code=404)
        if state["broken"]:
            return Response(status_code=503)
        return JSONResponse(state["metadata"])
    async def keys(request):
        state["paths"].append(request.url.path)
        return JSONResponse(state["jwks"])
    app = Starlette(routes=[Route("/.well-known/oauth-authorization-server/tenant", metadata),
                            Route("/.well-known/openid-configuration/tenant", metadata),
                            Route("/tenant/.well-known/openid-configuration", metadata), Route("/keys", keys)])
    context = ssl.create_default_context(cafile=str(cert_path))
    with running(app, listener=listener, tls=(cert_path, key_path)):
        yield issuer_url, key, context, state


@pytest.fixture
def configured(tmp_path, monkeypatch, issuer):
    issuer_url, key, context, state = issuer
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retained"))
    operations = ["ncp_capabilities_list", "ncp_plan_template", "ncp_retained_read"]
    grants = [{"principal": principal, "enabled": True,
               "operations": [{"name": operation, "constraints": {}} for operation in operations],
               "scientific_operations": ["statistics.v1"], "resources": ["ncp://artifacts/", "ncp://retained/"],
               "reference_scopes": [], "handoff_recipients": []} for principal in ("alice", "bob")]
    policy_doc = {"schema": "ciw.principal-policy.v1", "revision": "test-1", "principals": grants}
    policy = PrincipalPolicy(lambda: policy_doc)
    bindings_doc = {"schema": "ciw.oauth-subjects.v1", "revision": "test-1", "subjects": [
        {"subject": principal + "-subject", "principal": principal, "enabled": True, "not_before": 0}
        for principal in ("alice", "bob")]}
    settings = OAuthSettings("https://ncp.example/mcp", issuer_url)
    subjects = SubjectBindings(lambda: bindings_doc)
    authority = JWKAuthority(settings, ssl_context=context)
    with ExitStack() as stack:
        facades, owners = {}, {}
        for principal in ("alice", "bob"):
            owner = stack.enter_context(from_profile(demo_config(tmp_path / principal)))
            control = stack.enter_context(ControlHost(owner, retained_owner=owner_for(principal)))
            facades[principal] = policy.facade(principal, control=control)
            owners[principal] = owner
        app = create_app(settings, facades=facades, subjects=subjects, authority=authority)
        def token(subject="alice-subject", *, changes=None, headers=None, signing_key=None):
            now = int(time.time())
            claims = {"iss": issuer_url, "aud": settings.resource, "sub": subject, "iat": now - 1,
                      "exp": now + 300, "client_id": "test-client", "jti": "fixture-only", "scope": "odsp:invoke"}
            claims.update(changes or {})
            return jwt.encode(claims, signing_key or key, algorithm="RS256",
                              headers={"typ": "at+jwt", "kid": "test-key", **(headers or {})})
        yield {"app": app, "token": token, "policy": policy_doc, "bindings": bindings_doc,
               "settings": settings, "subjects": subjects, "facades": facades, "owners": owners,
               "authority": authority, "issuer": state}


def rpc(client, token, method="tools/list", params=None, ident=1, **kwargs):
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    headers.update(kwargs.pop("headers", {}))
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if ident is not None:
        message["id"] = ident
    return client.post("/mcp", json=message, headers=headers, **kwargs)


def test_discovery_challenge_and_real_https_metadata(configured):
    c = configured
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        discovery = client.get("/.well-known/oauth-protected-resource/mcp")
        assert discovery.status_code == 200
        assert discovery.json()["resource"] == c["settings"].resource
        assert discovery.json()["authorization_servers"] == [c["settings"].issuer]
        assert discovery.json()["scopes_supported"] == ["odsp:invoke"]
        assert discovery.json()["bearer_methods_supported"] == ["header"]
        refused = rpc(client, None)
        assert refused.status_code == 401
        assert 'resource_metadata="' + c["settings"].metadata_url + '"' in refused.headers["www-authenticate"]
        assert 'scope="odsp:invoke"' in refused.headers["www-authenticate"]
        assert refused.headers["cache-control"] == "no-store"
        assert client.get("/.well-known/oauth-authorization-server").status_code == 404
    assert c["issuer"]["paths"] == ["/.well-known/oauth-authorization-server/tenant", "/keys"]


def test_native_legacy_initialize_notification_and_policy_revocation(configured):
    c = configured
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        token = c["token"]()
        init = rpc(client, token, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "legacy-test", "version": "1"}})
        assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "odsp-principal-http"
        assert "mcp-session-id" not in init.headers
        notification = rpc(client, token, "notifications/initialized", ident=None)
        assert notification.status_code == 202 and not notification.content
        listing = rpc(client, token).json()["result"]["tools"]
        assert {row["name"] for row in listing} == {"ncp_capabilities_list", "ncp_plan_template", "ncp_retained_read"}
        result = rpc(client, token, "tools/call", {"name": "ncp_capabilities_list", "arguments": {}})
        assert result.json()["result"]["structuredContent"]["status"] == "ok"
        c["policy"]["principals"][0]["operations"] = []
        assert rpc(client, token).json()["result"]["tools"] == []
        denied = rpc(client, token, "tools/call", {"name": "ncp_capabilities_list", "arguments": {}})
        assert denied.json()["result"]["isError"] is True
        c["policy"]["principals"][0]["enabled"] = False
        assert rpc(client, token).status_code == 401


@pytest.mark.parametrize("changes", [
    {"iss": "https://other.example"}, {"aud": "https://other.example/mcp"},
    {"aud": ["https://ncp.example/mcp", "https://other.example/mcp"]},
    {"sub": "unmapped"}, {"exp": 1}, {"iat": 2**40}, {"nbf": 2**40},
    {"exp": True}, {"iat": False}, {"nbf": False}, {"exp": int(time.time()) + 7200},
    {"scope": "odsp:invoke odsp:invoke"}, {"scope": ["odsp:invoke"]}, {"client_id": ""}, {"jti": ""},
])
def test_invalid_access_claims_refused_before_disclosure(configured, changes):
    c = configured
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        response = rpc(client, c["token"](changes=changes))
        assert response.status_code == 401
        assert "alice" not in response.text and "test-client" not in response.text
    assert all(owner.capabilities()["execution_budget"]["used"] == 0 for owner in c["owners"].values())


def test_scope_signature_token_type_and_key_selection(configured):
    c = configured
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        response = rpc(client, c["token"](changes={"scope": "other:read"}))
        assert response.status_code == 403 and response.json()["error"] == "insufficient_scope"
        for token in (c["token"](signing_key=wrong_key), c["token"](headers={"typ": "JWT"}),
                      c["token"](headers={"kid": "missing"}),
                      c["token"](headers={"jku": "https://untrusted.invalid/keys"}), "shared-secret"):
            assert rpc(client, token).status_code == 401
        assert len(c["issuer"]["paths"]) == 2  # unknown kid cannot turn each request into an outbound fetch


def test_valid_signature_does_not_accept_duplicate_claims(configured, issuer):
    c = configured
    _, key, _, _ = issuer
    original = c["token"]().split(".")
    payload = jwt.utils.base64url_decode(original[1].encode())
    variants = [payload[:-1] + b',"sub":"bob-subject"}',
                payload[:-1] + b',"\\u0073ub":"bob-subject"}']
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        for body in variants:
            signing_input = original[0].encode() + b"." + jwt.utils.base64url_encode(body)
            signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
            raw = (signing_input + b"." + jwt.utils.base64url_encode(signature)).decode()
            assert rpc(client, raw).status_code == 401


def test_current_protocol_header_body_binding_and_sessionless_methods(configured):
    c = configured
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        headers = {"Authorization": "Bearer " + c["token"](),
                   "MCP-Protocol-Version": "2026-07-28", "MCP-Method": "tools/list",
                   "Accept": "application/json, text/event-stream"}
        body = {"jsonrpc": "2.0", "id": "modern", "method": "tools/list", "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                      "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                      "io.modelcontextprotocol/clientCapabilities": {}}}}
        assert client.post("/mcp", json=body, headers=headers).status_code == 200
        for changed in ({"MCP-Method": "tools/call"}, {"MCP-Protocol-Version": "2099-01-01"}):
            assert client.post("/mcp", json=body, headers={**headers, **changed}).status_code == 400
        assert client.get("/mcp", headers=headers).status_code == 405
        assert client.delete("/mcp", headers=headers).status_code == 405


def test_missing_bearer_is_refused_for_notifications_and_resources(configured):
    c = configured
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        assert rpc(client, None, "notifications/initialized", ident=None).status_code == 401
        assert rpc(client, None, "resources/list").status_code == 401
        assert rpc(client, c["token"](), "resources/list").status_code == 200


def test_current_subject_binding_revocation_and_token_epoch(configured):
    c = configured
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        token = c["token"]()
        assert rpc(client, token).status_code == 200
        c["bindings"]["subjects"][0]["enabled"] = False
        assert rpc(client, token).status_code == 401
        c["bindings"]["subjects"][0]["enabled"] = True
        c["bindings"]["subjects"][0]["not_before"] = int(time.time())
        assert rpc(client, token).status_code == 401
        refreshed = c["token"](changes={"iat": int(time.time())})
        assert rpc(client, refreshed).status_code == 200


def test_cross_subject_owner_and_no_header_credential_fallback(configured):
    c = configured
    artifact = c["facades"]["alice"].retained.keep({"secret": "alice-only-data"}, kind="fixture", catalog={})
    args = {"name": "ncp_retained_read", "arguments": {"artifact_id": artifact}}
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        assert not rpc(client, c["token"](), "tools/call", args).json()["result"]["isError"]
        bob = rpc(client, c["token"]("bob-subject"), "tools/call", args)
        assert bob.json()["result"]["isError"] and "alice-only-data" not in bob.text
        headers = {"CF-Access-Jwt-Assertion": c["token"](), "X-Principal": "alice", "Cookie": "token=" + c["token"]()}
        assert rpc(client, None, headers=headers).status_code == 401
        for supplied in ({"principal": "alice"}, {"token": c["token"]()}, {"endpoint": "https://elsewhere.example"}):
            denied = rpc(client, c["token"]("bob-subject"), "tools/call", {"name": "ncp_capabilities_list", "arguments": supplied})
            assert denied.json()["result"]["isError"] is True
            assert c["token"]() not in denied.text


@pytest.mark.parametrize("headers,expected", [
    ({"Host": "evil.example"}, 403), ({"Origin": "https://evil.example"}, 403), ({"Origin": "null"}, 403),
    ({"Mcp-Session-Id": "someone-elses-session"}, 400), ({"Last-Event-Id": "old-event"}, 400),
])
def test_fixed_authority_no_session_or_resume_identity(configured, headers, expected):
    with TestClient(configured["app"], base_url="https://ncp.example") as client:
        assert rpc(client, configured["token"](), headers=headers).status_code == expected


def test_duplicate_headers_and_json_request_budget(configured):
    c = configured
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        headers = [("Host", "ncp.example"), ("Authorization", "Bearer " + c["token"]()),
                   ("Authorization", "Bearer " + c["token"]("bob-subject"))]
        assert client.post("/mcp", content=b"{}", headers=headers).status_code == 400
        assert client.post("/mcp?access_token=untrusted", content=b"{}").status_code == 400
        auth = {"Authorization": "Bearer " + c["token"](), "Content-Type": "application/json"}
        for raw in (b'{"jsonrpc":"2.0","id":1,"method":"ping","id":2}',
                    b'{"jsonrpc":"2.0","id":true,"method":"ping"}',
                    b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}',
                    b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":9007199254740992}}',
                    b'[]', b'\xff'):
            assert client.post("/mcp", content=raw, headers=auth).status_code == 400
        assert client.post("/mcp", content=b" " * 65537, headers=auth).status_code == 413


def test_real_sdk_client_discovery_calls_refresh_reconnect_and_isolation(configured):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    c = configured
    artifact = c["facades"]["alice"].retained.keep({"private": "alice-owned"}, kind="fixture", catalog={})
    with running(c["app"]) as port:
        async def exercise():
            async def one(principal):
                token = c["token"](principal + "-subject")
                async with httpx2.AsyncClient(headers={"Host": "ncp.example", "Authorization": "Bearer " + token}, trust_env=False) as client:
                    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=client) as (reader, writer):
                        async with ClientSession(reader, writer) as session:
                            info = await session.discover()
                            assert "2026-07-28" in info.supported_versions
                            assert session.protocol_version == "2026-07-28"
                            assert session.server_info.name == "odsp-principal-http"
                            listing = await session.list_tools()
                            assert len(listing.tools) == 3
                            result = await session.call_tool("ncp_capabilities_list", {})
                            assert result.structured_content["status"] == "ok"
                            assert json.loads(result.content[0].text) == result.structured_content
                            owned = await session.call_tool("ncp_retained_read", {"artifact_id": artifact})
                            assert owned.is_error is (principal != "alice")
                            if principal != "alice":
                                assert "alice-owned" not in str(owned)
                            client.headers["Authorization"] = "Bearer " + c["token"](principal + "-subject", changes={"jti": "refreshed"})
                            assert (await session.call_tool("ncp_plan_template", {"baseline": "baseline"})).is_error is False
            await asyncio.gather(one("alice"), one("bob"))
            await one("alice")  # Fresh TCP connection requires fresh bearer verification.
            assert all(owner.capabilities()["execution_budget"]["used"] == 0 for owner in c["owners"].values())
        asyncio.run(exercise())


def test_oidc_discovery_fallback_and_jwks_rotation(configured):
    c = configured
    c["issuer"]["fallback"] = True
    with TestClient(c["app"], base_url="https://ncp.example") as client:
        assert rpc(client, c["token"]()).status_code == 200
        assert c["issuer"]["paths"][:3] == ["/.well-known/oauth-authorization-server/tenant",
                                            "/.well-known/openid-configuration/tenant", "/keys"]
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        public.update(kid="rotated-key", use="sig", alg="RS256")
        c["issuer"]["jwks"] = {"keys": [public]}
        c["authority"]._attempted = time.monotonic() - 11
        refreshed = c["token"](headers={"kid": "rotated-key"}, signing_key=key)
        assert rpc(client, refreshed).status_code == 200
        assert rpc(client, c["token"]()).status_code == 401
        c["issuer"]["broken"] = True
        c["authority"]._expires = 0
        c["authority"]._attempted = time.monotonic() - 11
        assert rpc(client, refreshed).status_code == 401  # no stale key on failed refresh


@pytest.mark.parametrize("mutation", [
    lambda state: state["metadata"].update(issuer="https://wrong.example"),
    lambda state: state["metadata"].update(jwks_uri="https://untrusted.invalid/keys"),
    lambda state: state.update(redirect=True),
    lambda state: state["jwks"]["keys"].append(deepcopy(state["jwks"]["keys"][0])),
    lambda state: state["jwks"]["keys"][0].update(d="private-material"),
])
def test_untrusted_discovery_or_key_material_prevents_startup(configured, mutation):
    mutation(configured["issuer"])
    with pytest.raises(Exception):
        with TestClient(configured["app"], base_url="https://ncp.example"):
            pytest.fail("invalid authority was accepted")


def test_fixed_host_configuration_refuses_unsafe_inputs():
    for resource, issuer in (("http://ncp.example/mcp", "https://issuer.example"),
                             ("https://ncp.example/mcp?x=1", "https://issuer.example"),
                             ("https://ncp.example/mcp", "https://user:password@issuer.example"),
                             ("https://ncp.example/mcp", "https://issuer.example/../other")):
        with pytest.raises(ValueError):
            OAuthSettings(resource, issuer)
    with pytest.raises(ValueError):
        OAuthSettings("https://ncp.example/mcp", "https://issuer.example", required_scopes=("bad\"scope",))


def test_subject_file_reader_refuses_duplicate_keys_and_symlinks(tmp_path):
    path = tmp_path / "subjects.json"
    path.write_text('{"schema":"ciw.oauth-subjects.v1","revision":"one","revision":"two","subjects":[]}')
    with pytest.raises(ValueError):
        SubjectBindings.from_file(path).principal("alice", 0)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        SubjectBindings.from_file(link).principal("alice", 0)
