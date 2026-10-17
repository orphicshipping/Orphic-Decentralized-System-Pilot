"""Evidence MCP disclosure, integrity and SDK composition boundaries."""
from hashlib import sha256
import json

import pytest

from ciw.external_work import ExternalWorkStore
from ciw.external_work_mcp import ExternalWorkHost
from ciw.knowledge_mcp import EVIDENCE_TOOLS, KnowledgeHost, KnowledgeRefusal, create_server
from test_knowledge_mcp import Catalog


class Evidence:
    """Small retained source fixture with independent text/span/record identities."""
    ref = "sha256:" + "b" * 64

    def __init__(self, catalog):
        self.catalog_ref = catalog.ref
        self.raw = b"Synthetic solver is not validated. Sensor values are simulated."
        self.span_id = "sha256:" + "c" * 64
        self.representation_id = "sha256:" + "d" * 64
        self.value = {"evidence_ref": self.ref, "catalog_ref": catalog.ref,
                      "authorizes_execution": False,
                      "span": {"span_id": self.span_id, "representation_id": self.representation_id,
                               "record_id": catalog.record_id, "text": self.raw.decode(),
                               "locator": {"byte_start": 0, "byte_end_exclusive": len(self.raw),
                                           "span_sha256": "sha256:" + sha256(self.raw).hexdigest()}}}

        preimage = {"representation_id": self.representation_id, "locator": self.value["span"]["locator"]}
        self.span_id = "sha256:" + sha256(json.dumps(preimage, sort_keys=True,
                                                    separators=(",", ":")).encode()).hexdigest()
        self.value["span"]["span_id"] = self.span_id

    def overview(self):
        return {"evidence_ref": self.ref, "representation_count": 1, "authorizes_execution": False}

    def span(self, span_id):
        if span_id != self.span_id:
            raise ValueError("private source path must never escape")
        return json.loads(json.dumps(self.value))

    def span_bytes(self, span_id):
        self.span(span_id)
        return self.raw

    def search(self, query, limit=10, offset=0, record_id=None):
        return {"items": [self.value["span"]], "total": 1, "next_offset": None}


def make_host(tmp_path, *, role="worker"):
    catalog = Catalog()
    evidence = Evidence(catalog)
    store = ExternalWorkStore(tmp_path / "work.sqlite3")
    store.provision_client("fixture", role=role, parent_ref="intent:fixture")
    host = KnowledgeHost(catalog, ExternalWorkHost(store, principal="fixture"),
                         "intent:fixture", evidence=evidence)
    return host, evidence, store


def test_evidence_is_explicit_and_bound_to_catalog(tmp_path):
    catalog = Catalog()
    evidence = Evidence(catalog)
    assert not set(EVIDENCE_TOOLS) & {x["name"] for x in KnowledgeHost(catalog).descriptions()}
    assert KnowledgeHost(catalog).call("ncp_evidence_search", {"query": "solver"})["reason"] == "unknown_tool"
    evidence.catalog_ref = "sha256:" + "e" * 64
    with pytest.raises(KnowledgeRefusal, match="evidence_binding_mismatch"):
        KnowledgeHost(catalog, evidence=evidence)
    host, evidence, _ = make_host(tmp_path)
    assert set(EVIDENCE_TOOLS) <= {x["name"] for x in host.descriptions()}
    status = host.call("ncp_system_status", {})["data"]
    assert status["evidence_enabled"] and not status["metadata_only"]
    assert status["retained_source_text"] and status["authorization"] == "host_only"
    evidence.ref = "sha256:" + "f" * 64
    assert host.call("ncp_evidence_overview", {})["reason"] == "evidence_binding_changed"


def test_span_resources_preserve_exact_bytes_and_refuse_alternate_routes(tmp_path):
    host, evidence, _ = make_host(tmp_path)
    uri = host.evidence_uri + "/span/" + evidence.span_id[7:]
    value = json.loads(host.read_resource(uri))
    assert value["span"]["text"].encode() == evidence.raw
    assert value["span"]["locator"]["span_sha256"] != evidence.span_id
    for invalid in (uri + "?path=private", uri + "/../overview", uri.upper(),
                    "file:///private", host.evidence_uri + "/span/" + "0" * 64):
        with pytest.raises(KnowledgeRefusal, match="resource_unavailable"):
            host.read_resource(invalid)
    evidence.raw += b"tampered"
    with pytest.raises(KnowledgeRefusal, match="span_integrity_failure"):
        host.read_resource(uri)


def test_evidence_arguments_cannot_select_paths_principals_or_bind_context(tmp_path):
    host, evidence, _ = make_host(tmp_path)
    for extra in ({"path": "/private"}, {"member": "README"}, {"principal": "admin"},
                  {"disclosure_ref": "self-approved"}):
        value = host.call("ncp_evidence_search", {"query": "solver", **extra})
        assert value == {"status": "refused", "reason": "invalid_arguments"}
    for name in ("ncp_evidence_build", "ncp_evidence_bind_context", "ncp_work_authorize", "ncp_work_review"):
        assert host.call(name, {})["reason"] == "unknown_tool"


def test_revoke_denies_all_evidence_surfaces_before_lookup(tmp_path):
    host, evidence, store = make_host(tmp_path)
    uri = host.evidence_uri + "/span/" + evidence.span_id[7:]
    store.revoke_client("fixture")
    evidence.span = lambda *a, **k: pytest.fail("revoked client reached source content")
    evidence.overview = lambda *a, **k: pytest.fail("revoked client reached evidence overview")
    for name in EVIDENCE_TOOLS:
        assert host.call(name, {}) == {"status": "refused", "reason": "client_refused"}
    for operation in (host.descriptions, host.resource_descriptions, host.resource_templates,
                      lambda: host.read_resource(uri), lambda: host.read_resource(uri + "?invalid")):
        with pytest.raises(KnowledgeRefusal, match="client_refused"):
            operation()


def test_evidence_sdk_roundtrip_and_revocation(tmp_path):
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.shared.exceptions import MCPError
    from mcp.shared.memory import create_client_server_memory_streams
    host, evidence, store = make_host(tmp_path)
    server = create_server(host)

    async def exercise():
        with anyio.fail_after(20):
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as group:
                    group.start_soon(server.run, *server_streams, server.create_initialization_options())
                    async with ClientSession(*client_streams) as session:
                        await session.initialize()
                        names = {item.name for item in (await session.list_tools()).tools}
                        assert set(EVIDENCE_TOOLS) <= names
                        assert len((await session.list_resources()).resources) == 2
                        assert len((await session.list_resource_templates()).resource_templates) == 2
                        found = await session.call_tool("ncp_evidence_search", {"query": "solver"})
                        assert not found.is_error
                        assert found.structured_content["data"]["items"][0]["span_id"] == evidence.span_id
                        uri = host.evidence_uri + "/span/" + evidence.span_id[7:]
                        value = json.loads((await session.read_resource(uri)).contents[0].text)
                        assert value["span"]["text"].encode() == evidence.raw
                        with pytest.raises(MCPError) as error:
                            await session.read_resource(uri + "?bad")
                        assert error.value.code == -32602
                        store.revoke_client("fixture")
                        assert (await session.call_tool("ncp_evidence_span", {"span_id": evidence.span_id})).is_error
                        with pytest.raises(MCPError, match="client_refused"):
                            await session.read_resource(uri)
                    group.cancel_scope.cancel()
    anyio.run(exercise)


@pytest.mark.parametrize("field", ["locator", "representation_id"])
def test_resource_verifies_span_id_preimage(tmp_path, field):
    host, evidence, _ = make_host(tmp_path)
    uri = host.evidence_uri + "/span/" + evidence.span_id[7:]
    if field == "locator":
        evidence.value["span"]["locator"]["byte_start"] += 1
        evidence.value["span"]["locator"]["byte_end_exclusive"] += 1
    else:
        evidence.value["span"]["representation_id"] = "sha256:" + "9" * 64
    with pytest.raises(KnowledgeRefusal, match="span_integrity_failure"):
        host.read_resource(uri)
