"""Authenticated HTTP with real RSA signatures and the actual MCP SDK transport.

JWKS retrieval is supplied by a local fixture; no Cloudflare login is simulated
as a live success. Network tests bind only loopback and use ephemeral keys.
"""
import json
from types import SimpleNamespace
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from ciw.control_contracts import bytes_ref
from ciw.external_work import ExternalWorkStore
from ciw.external_work_http import create_app, validate_config
from ciw.external_work_corpus import bind_span


@pytest.fixture
def gateway(tmp_path):
    store = ExternalWorkStore(tmp_path / "work.sqlite3")
    for principal, role in [("chatgpt-client", "requester"), ("grok-client", "worker")]:
        store.provision_client(principal, role=role, parent_ref="intent:http-test")
    raw = b"Synthetic HTTP fixture. Validation remains unresolved."
    context_id = store.retain_context(parent_ref="intent:http-test", snapshot_ref="fixture:1",
        source_id="fixture:http", version_ref="v1", source_bytes=raw,
        expected_source_hash=bytes_ref(raw), start=0, end=len(raw))
    config = {"schema": "ciw.external-work-cloudflare.v1", "database": str(store.path),
              "issuer": "https://fixture.cloudflareaccess.com", "endpoints": [
                  {"public_url": "https://ncp.example/requester/mcp", "audience": "requester-audience",
                   "role": "requester", "subjects": {"user-1": "chatgpt-client"}},
                  {"public_url": "https://ncp.example/worker/mcp", "audience": "worker-audience",
                   "role": "worker", "subjects": {"user-1": "grok-client"}}]}
    app = create_app(config)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app.state.verifier.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key()))
    def token(role="requester", **overrides):
        now = int(time.time())
        claims = {"iss": config["issuer"], "aud": [role + "-audience"], "sub": "user-1",
                  "iat": now - 1, "exp": now + 300, "type": "app"}
        claims.update(overrides)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    with TestClient(app, base_url="https://ncp.example") as client:
        yield store, client, token, context_id, config, key


def rpc(gateway, method, arguments=None, *, role="requester", name=None, token=None, extra_headers=None):
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-11-25",
               "Cf-Access-Jwt-Assertion": token or gateway[2](role)}
    headers.update(extra_headers or {})
    params = {"name": name, "arguments": arguments or {}} if name else arguments or {}
    return gateway[1].post("/" + role + "/mcp", headers=headers,
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def tool(gateway, name, arguments=None, **kwargs):
    response = rpc(gateway, "tools/call", arguments, name=name, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_authenticated_http_exchange_through_sdk(gateway):
    store, client, token, context_id, config, key = gateway
    initialized = rpc(gateway, "initialize", {"protocolVersion": "2025-11-25",
        "capabilities": {}, "clientInfo": {"name": "fixture-client", "version": "1"}})
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["serverInfo"]["name"] == "ncp-external-work"
    proposed = tool(gateway, "ncp_work_propose", {"request_key": "http-1", "task": {
        "parent_ref": "intent:http-test", "instruction": "Extract limitation", "expected_output": "cited candidate",
        "context_ids": [context_id]}})["structuredContent"]["data"]
    task_id, task_hash = proposed["task_id"], proposed["task_hash"]
    assert tool(gateway, "ncp_work_discover", role="worker")["structuredContent"]["data"]["items"] == []
    store.authorize(task_id, expected_hash=task_hash, approval_ref="fixture:local-host",
                    worker_id="grok-client", expires_at=time.time()+600)
    context = tool(gateway, "ncp_work_context", {"task_id": task_id, "context_id": context_id})
    assert context == tool(gateway, "ncp_work_context", {"task_id": task_id, "context_id": context_id}, role="worker")
    claimed = tool(gateway, "ncp_work_claim", {"task_id": task_id, "expected_hash": task_hash,
        "attempt_id": "http-attempt", "bot_label": "test attribution"}, role="worker")
    assert not claimed["isError"]
    submitted = tool(gateway, "ncp_work_submit", {"task_id": task_id, "expected_hash": task_hash,
        "attempt_id": "http-attempt", "result": {"outcome": "completed", "candidate": "Unresolved validation",
            "citations": [{"context_id": context_id, "quote": "Validation remains unresolved."}],
            "limitations": ["Synthetic test; no scientific acceptance."]}}, role="worker")
    assert not submitted["isError"]
    read = tool(gateway, "ncp_work_read", {"task_id": task_id})["structuredContent"]["data"]
    assert read["review"] is None and read["execution_status"] == "submitted"
    assert read["submission"] == submitted["structuredContent"]["data"]


def test_role_catalogs_remain_separate(gateway):
    requester = {t["name"] for t in rpc(gateway, "tools/list").json()["result"]["tools"]}
    worker = {t["name"] for t in rpc(gateway, "tools/list", role="worker").json()["result"]["tools"]}
    assert "ncp_work_propose" in requester and "ncp_work_propose" not in worker
    assert "ncp_work_submit" in worker and "ncp_work_submit" not in requester
    assert "ncp_work_review" not in requester | worker


@pytest.mark.parametrize("change", [
    {"iss": "https://wrong.cloudflareaccess.com"}, {"aud": ["worker-audience"]},
    {"aud": ["requester-audience", "worker-audience"]}, {"sub": "unbound-user"},
    {"exp": 1}, {"nbf": 2**40}, {"iat": 2**40}, {"type": "service"}, {"exp": True},
])
def test_invalid_signed_claims_are_refused(gateway, change):
    response = rpc(gateway, "tools/list", token=gateway[2](**change))
    assert response.status_code == 401 and response.json() == {"error": "access_denied"}


def test_bad_signatures_algorithms_and_forged_headers(gateway):
    store, client, token, context_id, config, key = gateway
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(token(), options={"verify_signature": False})
    bad = [jwt.encode(claims, wrong, algorithm="RS256", headers={"kid": "test"}),
           jwt.encode(claims, "fake-secret-is-not-an-asymmetric-key", algorithm="HS256", headers={"kid": "test"}),
           "not-a-token"]
    for value in bad:
        assert rpc(gateway, "tools/list", token=value).status_code == 401
    missing = client.post("/requester/mcp", headers={"Authorization": "Bearer " + token(),
        "X-Principal": "chatgpt-client", "Cf-Access-Authenticated-User-Email": "user@example.com"}, json={})
    assert missing.status_code == 401
    duplicate = client.post("/requester/mcp", headers=[("Cf-Access-Jwt-Assertion", token()),
                           ("Cf-Access-Jwt-Assertion", token())], json={})
    assert duplicate.status_code == 401


def test_requester_token_cannot_cross_to_worker_route(gateway):
    assert rpc(gateway, "tools/list", role="worker", token=gateway[2]("requester")).status_code == 401
    assert rpc(gateway, "tools/list", role="requester", token=gateway[2]("worker")).status_code == 401


def test_revocation_applies_to_existing_token(gateway):
    token = gateway[2]()
    assert rpc(gateway, "tools/list", token=token).status_code == 200
    gateway[0].revoke_client("chatgpt-client")
    assert rpc(gateway, "tools/list", token=token).status_code == 401


@pytest.mark.parametrize("headers", [{"Host": "evil.example"}, {"Origin": "https://evil.example"},
                                     {"Origin": "null"}])
def test_origin_and_host_refused(gateway, headers):
    assert rpc(gateway, "tools/list", extra_headers=headers).status_code == 403


def test_no_admin_or_corpus_wide_tools_and_unknown_paths(gateway):
    for name in ["ncp_work_review", "ncp_work_authorize", "ncp_corpus_search", "net_execute"]:
        response = tool(gateway, name)
        assert response["isError"] and response["structuredContent"]["reason"] == "unknown_tool"
    assert gateway[1].get("/admin").status_code == 404
    # OAuth discovery is supplied by Access at the edge, never fabricated locally.
    assert gateway[1].get("/.well-known/oauth-authorization-server").status_code == 404


def test_body_budget_enforced(gateway):
    response = gateway[1].post("/requester/mcp", content=b" " * 65537,
        headers={"Cf-Access-Jwt-Assertion": gateway[2](), "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    assert response.status_code == 413


def test_official_sdk_client_over_loopback_tcp(gateway):
    import asyncio
    import socket
    import threading
    import httpx2
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app = create_app(gateway[4])
    app.state.verifier.jwks = gateway[1].app.state.verifier.jwks
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False,
                                         proxy_headers=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started

            async def exchange():
                async with httpx2.AsyncClient(headers={"Host": "ncp.example",
                        "Cf-Access-Jwt-Assertion": gateway[2]("worker")}, trust_env=False) as client:
                    async with streamable_http_client(f"http://127.0.0.1:{port}/worker/mcp",
                                                       http_client=client) as (reader, writer):
                        async with ClientSession(reader, writer) as session:
                            initialized = await session.initialize()
                            assert initialized.server_info.name == "ncp-external-work"
                            catalog = await session.list_tools()
                            assert "ncp_work_submit" in {t.name for t in catalog.tools}
                            polled = await session.call_tool("ncp_work_discover", {})
                            assert polled.structured_content["data"]["items"] == []
            asyncio.run(exchange())
        finally:
            server.should_exit = True
            thread.join(5)
            assert not thread.is_alive()


def test_config_rejects_duplicate_authority_and_bad_issuer(gateway):
    import copy
    config = copy.deepcopy(gateway[4])
    config["endpoints"][1]["audience"] = config["endpoints"][0]["audience"]
    with pytest.raises(ValueError):
        validate_config(config)
    config = copy.deepcopy(gateway[4])
    config["issuer"] = "https://cloudflareaccess.com.evil.example"
    with pytest.raises(ValueError):
        validate_config(config)


def test_corpus_binding_preserves_original_identity_and_findings(tmp_path):
    # Reuse the original corpus builder fixtures; no installed archive dependency.
    from ciw.corpus_retrieval import Corpus
    import test_corpus_retrieval as fixtures
    # Public fixture creates a complete reconciled synthetic bundle.
    bundle, ref, _ = fixtures.built.__wrapped__(fixtures.setup.__wrapped__(tmp_path))
    corpus = Corpus.open(bundle, expected_ref=ref)
    page = corpus.source("NCP-CS-001")
    store = ExternalWorkStore(tmp_path / "work.sqlite3")
    store.provision_client("requester", role="requester", parent_ref="intent:corpus-test")
    context_id = bind_span(store, bundle=bundle, expected_ref=ref,
        span_id=page["spans"][0]["span_id"], parent_ref="intent:corpus-test", disclosure_ref="fixture:local-only")
    task = store.propose("requester", request_key="corpus-test", task={"parent_ref": "intent:corpus-test",
        "instruction": "Inspect", "expected_output": "Candidate", "context_ids": [context_id]})
    context = store.context("requester", task_id=task["task_id"], context_id=context_id)
    assert context["source_id"] == page["source"]["source_id"]
    assert context["source_hash"] == "sha256:" + page["source"]["snapshot"]["sha256"]
    assert context["provenance"]["findings"] == page["findings"]
    assert context["provenance"]["semantic_support_checked"] is False
