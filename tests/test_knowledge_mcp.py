"""Official SDK transport and metadata/work authority boundary checks."""
from hashlib import sha256
import json

import pytest

from ciw.external_work import ExternalWorkStore
from ciw.external_work_mcp import ExternalWorkHost
from ciw.knowledge_mcp import (
    KnowledgeHost, KnowledgeRefusal, MAX_RESPONSE_BYTES, TOOLS, create_server,
)


class Catalog:
    """Small immutable metadata fixture, not a paper or instrument claim."""

    ref = "sha256:" + "a" * 64

    def __init__(self):
        self.record = {"source_id": "fixture:1", "title": "Unqualified synthetic fixture",
                       "source_sha256": None, "representation": "catalog_metadata",
                       "category": "test", "kind": "document", "metadata_only": True,
                       "source_url": "https://example.invalid/untrusted-source"}
        self.raw = json.dumps(self.record, sort_keys=True, separators=(",", ":")).encode()
        self.record_id = "sha256:" + sha256(self.raw).hexdigest()

    def overview(self):
        return {"corpus_ref": self.ref, "record_count": 1, "metadata_only": True}

    def source(self, record_id):
        if record_id != self.record_id:
            raise ValueError("private local path must never escape")
        return {"record": {"record_id": record_id, **self.record}}

    def search(self, query, limit=10, category=None, kind=None, offset=0):
        rows = [self.source(self.record_id)["record"]] if query.casefold() in self.record["title"].casefold() else []
        if (category is not None and category != "test") or (kind is not None and kind != "document"):
            rows = []
        return {"items": rows[offset:offset + limit], "next_offset": None}

    def relations(self, record_id, limit=50, offset=0):
        self.source(record_id)
        return {"items": [], "next_offset": None}

    def review_queue(self, limit=20, offset=0, kind=None):
        return {"items": [{"reason": "synthetic fixture"}][offset:offset + limit], "next_offset": None}

    def record_bytes(self, record_id):
        self.source(record_id)
        return self.raw


@pytest.fixture
def corpus():
    return Catalog()


@pytest.fixture
def host(corpus):
    return KnowledgeHost(corpus)


def bound_host(tmp_path, corpus, role="requester"):
    store = ExternalWorkStore(tmp_path / "work.sqlite3")
    store.provision_client("bound-client", role=role, parent_ref="intent:fixture")
    work = ExternalWorkHost(store, principal="bound-client")
    return KnowledgeHost(corpus, work, "intent:fixture"), store


def test_metadata_tools_have_strict_schemas_and_no_authority(host, corpus):
    tools = host.descriptions()
    assert {x["name"] for x in tools} == set(TOOLS)
    assert all(x["inputSchema"]["additionalProperties"] is False for x in tools)
    assert all(x["annotations"]["readOnlyHint"] for x in tools)
    assert host.call("ncp_knowledge_search", {"query": "synthetic"})["data"]["items"][0]["record_id"] == corpus.record_id
    assert host.call("ncp_knowledge_source", {"record_id": corpus.record_id})["data"]["record"]["source_sha256"] is None
    assert host.call("ncp_knowledge_relations", {"record_id": corpus.record_id})["data"]["items"] == []
    assert host.call("ncp_knowledge_review_queue", {"offset": 1})["data"]["items"] == []
    status = host.call("ncp_system_status", {})["data"]
    assert status["acceptance"] == status["authorization"] == "host_only"
    assert status["metadata_only"] and not status["raw_source_access"] and not status["source_execution"]


@pytest.mark.parametrize("name,args", [
    ("ncp_knowledge_search", {"query": "fixture", "principal": "admin"}),
    ("ncp_knowledge_search", {"query": "fixture", "limit": True}),
    ("ncp_knowledge_search", {"query": "fixture", "limit": 51}),
    ("ncp_knowledge_search", {"query": "fixture", "offset": -1}),
    ("ncp_knowledge_search", {"query": "fixture", "corpus": "/private/path"}),
    ("ncp_knowledge_search", {"query": " "}),
    ("ncp_knowledge_search", {"query": "x" * 513}),
    ("ncp_knowledge_source", {"record_id": "https://example.invalid/source"}),
    ("ncp_knowledge_source", {"record_id": "sha256:" + "Z" * 64}),
    ("ncp_knowledge_overview", {"approve": True}),
    ("ncp_work_authorize", {}),
    ("ncp_work_record_review", {}),
    ("ncp_work_provision_client", {}),
])
def test_model_arguments_cannot_select_authority_or_paths(host, name, args):
    assert host.call(name, args)["status"] == "refused"


def test_resource_pin_and_record_hash_are_exact(host, corpus):
    overview = host.resource_descriptions()[0]["uri"]
    assert json.loads(host.read_resource(overview))["record_count"] == 1
    uri = host.base_uri + "/source/" + corpus.record_id[7:]
    assert json.loads(host.read_resource(uri)) == corpus.record
    assert host.resource_templates()[0]["uriTemplate"] == host.base_uri + "/source/{record_digest}"
    for bad in ["file:///private/path", "https://example.invalid", uri + "?admin=true",
                uri + "/..", uri.replace("/source/", "/source/%2f"),
                uri.replace("a" * 64, "b" * 64), host.base_uri + "/source/" + "b" * 64]:
        with pytest.raises(KnowledgeRefusal):
            host.read_resource(bad)
    corpus.raw += b" "
    with pytest.raises(KnowledgeRefusal, match="record_integrity_failure"):
        host.read_resource(uri)


def test_bound_work_requires_explicit_parent_and_role_scopes(tmp_path, corpus):
    host, store = bound_host(tmp_path, corpus)
    with pytest.raises(KnowledgeRefusal, match="work_binding_required"):
        KnowledgeHost(corpus, host.work_host)
    with pytest.raises(KnowledgeRefusal, match="scope_refused"):
        KnowledgeHost(corpus, host.work_host, "intent:other")
    names = {item["name"] for item in host.descriptions()}
    assert "ncp_work_propose" in names and "ncp_work_cancel" in names
    assert "ncp_work_submit" not in names and "ncp_work_claim" not in names
    assert host.call("ncp_work_claim", {}) == {"status": "refused", "reason": "unknown_tool"}
    assert host.call("ncp_work_discover", {})["status"] == "ok"
    assert host.call("ncp_work_discover", {"principal": "someone-else"})["status"] == "refused"
    store.revoke_client("bound-client")
    for tool in TOOLS:
        assert host.call(tool, {}) == {"status": "refused", "reason": "client_refused"}
    for read in (host.descriptions, host.resource_descriptions, host.resource_templates,
                 lambda: host.read_resource(host.base_uri + "/overview")):
        with pytest.raises(KnowledgeRefusal, match="client_refused"):
            read()


def test_parent_binding_is_rechecked_after_launch(tmp_path, corpus):
    host, store = bound_host(tmp_path, corpus, role="worker")
    names = {item["name"] for item in host.descriptions()}
    assert "ncp_work_claim" in names and "ncp_work_propose" not in names
    with store._transaction() as db:
        db.execute("UPDATE clients SET parent=? WHERE id=?", ("intent:other", "bound-client"))
    assert host.call("ncp_knowledge_overview", {}) == {"status": "refused", "reason": "scope_refused"}
    with pytest.raises(KnowledgeRefusal, match="scope_refused"):
        host.resource_descriptions()


def test_errors_are_sanitized_and_responses_are_bounded(host, corpus):
    assert host.call("ncp_knowledge_source", {"record_id": "sha256:" + "b" * 64}) == {
        "status": "refused", "reason": "invalid_arguments"}
    def bad_overview():
        raise RuntimeError("credential=must-not-escape /private/local/path")
    corpus.overview = bad_overview
    result = host.call("ncp_knowledge_overview", {})
    assert result == {"status": "refused", "reason": "operation_refused"}
    corpus.overview = lambda: {"huge": "x" * MAX_RESPONSE_BYTES}
    result = host.call("ncp_knowledge_overview", {})
    assert result == {"status": "refused", "reason": "response_too_large"}
    assert host.mcp_result("ncp_knowledge_overview", result)["isError"]
    corpus.ref = "sha256:" + "c" * 64
    assert host.call("ncp_system_status", {})["reason"] == "corpus_binding_changed"


def test_official_sdk_memory_transport_negotiates_tools_and_resources(tmp_path, corpus):
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    from mcp import ClientSession, types
    from mcp.shared.exceptions import MCPError
    from mcp.shared.memory import create_client_server_memory_streams

    host, store = bound_host(tmp_path, corpus)
    server = create_server(host)

    async def exercise():
        with anyio.fail_after(20):
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as group:
                    group.start_soon(server.run, *server_streams, server.create_initialization_options())
                    async with ClientSession(*client_streams) as session:
                        initialized = await session.initialize()
                        assert initialized.server_info.name == "ncp-system"
                        assert initialized.capabilities.tools is not None
                        assert initialized.capabilities.resources is not None
                        listed = await session.list_tools()
                        assert "ncp_knowledge_search" in {item.name for item in listed.tools}
                        found = await session.call_tool("ncp_knowledge_search", {"query": "synthetic"})
                        assert found.structured_content["data"]["items"][0]["record_id"] == corpus.record_id
                        refused = await session.call_tool("ncp_knowledge_overview", {"principal": "admin"})
                        assert refused.is_error and refused.structured_content["status"] == "refused"
                        listed_resources = await session.list_resources()
                        read = await session.read_resource(listed_resources.resources[0].uri)
                        assert json.loads(read.contents[0].text)["record_count"] == 1
                        templates = await session.list_resource_templates()
                        assert len(templates.resource_templates) == 1
                        uri = host.base_uri + "/source/" + corpus.record_id[7:]
                        record = await session.read_resource(uri)
                        assert json.loads(record.contents[0].text)["source_id"] == "fixture:1"
                        unavailable_uris = [
                            "file:///private/path",
                            host.base_uri + "/source/" + "b" * 64,
                            uri.replace("a" * 64, "b" * 64),
                            uri + "?private=do-not-echo",
                        ]
                        for unavailable in unavailable_uris:
                            with pytest.raises(MCPError) as missing:
                                await session.read_resource(unavailable)
                            assert missing.value.error.code == types.INVALID_PARAMS
                            assert missing.value.error.message == "resource_unavailable"
                            assert missing.value.error.data is None
                        with pytest.raises(MCPError) as invalid_cursor:
                            await session.list_resources(params=types.PaginatedRequestParams(cursor="not-issued"))
                        assert invalid_cursor.value.error.code == types.INVALID_PARAMS
                        original_overview = corpus.overview
                        def private_failure():
                            raise RuntimeError("credential=do-not-echo /private/path")
                        corpus.overview = private_failure
                        with pytest.raises(MCPError) as internal:
                            await session.read_resource(host.base_uri + "/overview")
                        assert internal.value.error.code == types.INTERNAL_ERROR
                        assert internal.value.error.message == "operation_refused"
                        assert internal.value.error.data is None
                        corpus.overview = original_overview
                        store.revoke_client("bound-client")
                        # A revoked connection must fail before even touching the
                        # catalog, for valid and invalid resource instances alike.
                        corpus.overview = lambda: pytest.fail("revoked overview lookup")
                        corpus.record_bytes = lambda _: pytest.fail("revoked record lookup")
                        revoked = await session.call_tool("ncp_knowledge_overview", {})
                        assert revoked.is_error and revoked.structured_content["reason"] == "client_refused"
                        for read_list in (session.list_tools, session.list_resources, session.list_resource_templates):
                            with pytest.raises(MCPError) as revoked_list:
                                await read_list()
                            assert revoked_list.value.error.message == "client_refused"
                            assert revoked_list.value.error.data is None
                            with pytest.raises(MCPError) as revoked_cursor:
                                await read_list(params=types.PaginatedRequestParams(cursor="not-issued"))
                            assert revoked_cursor.value.error.message == "client_refused"
                            assert revoked_cursor.value.error.data is None
                        for unavailable in [uri, host.base_uri + "/overview", *unavailable_uris]:
                            with pytest.raises(MCPError) as revoked_read:
                                await session.read_resource(unavailable)
                            assert revoked_read.value.error.code == -32000
                            assert revoked_read.value.error.message == "client_refused"
                            assert revoked_read.value.error.data is None
                    group.cancel_scope.cancel()

    anyio.run(exercise)


def test_real_corpus_sdk_search_resources_and_host_authorized_work_exchange(tmp_path):
    """Actual compiled catalog, SDK sessions, SQLite grants and quoted metadata."""
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from ciw.control_contracts import bytes_ref
    from ciw.knowledge_cli import bind_metadata
    from ciw.proto_corpus import ProtoCorpus, compile_bundle
    from test_proto_corpus import make_inputs

    inputs = make_inputs(tmp_path)
    pin = compile_bundle(**inputs)
    corpus = ProtoCorpus.open(inputs["output"], pin)
    record_id = corpus.search("polymer")["items"][0]["record_id"]
    record = corpus.source(record_id)["record"]
    store = ExternalWorkStore(tmp_path / "exchange.sqlite3", clock=lambda: 1000)
    for principal, role in (("requester", "requester"), ("worker", "worker")):
        store.provision_client(principal, role=role, parent_ref="intent:metadata-review")
    binding = bind_metadata(store, corpus, record_id=record_id,
                            parent_ref="intent:metadata-review", disclosure_ref="operator:fixture-disclosure")
    assert binding["metadata_hash"] == record_id == bytes_ref(corpus.record_bytes(record_id))
    assert binding["metadata_only"] and not binding["authorizes_execution"]
    assert binding["metadata_hash"] != binding["source_sha256"]
    hosts = [KnowledgeHost(corpus, ExternalWorkHost(store, principal=principal), "intent:metadata-review")
             for principal in ("requester", "worker")]
    servers = [create_server(host) for host in hosts]

    async def tool(session, name, arguments):
        result = await session.call_tool(name, arguments)
        assert not result.is_error, result.structured_content
        assert result.structured_content["status"] == "ok"
        return result.structured_content["data"]

    async def exchange():
        with anyio.fail_after(30):
            async with create_client_server_memory_streams() as (requester_streams, requester_server), \
                       create_client_server_memory_streams() as (worker_streams, worker_server):
                async with anyio.create_task_group() as group:
                    group.start_soon(servers[0].run, *requester_server, servers[0].create_initialization_options())
                    group.start_soon(servers[1].run, *worker_server, servers[1].create_initialization_options())
                    async with ClientSession(*requester_streams) as requester, ClientSession(*worker_streams) as worker:
                        await requester.initialize()
                        await worker.initialize()
                        found = await tool(requester, "ncp_knowledge_search", {"query": "polymer"})
                        assert record_id in {item["record_id"] for item in found["items"]}
                        assert found["catalog_ref"] == pin and found["metadata_only"]
                        source = await tool(requester, "ncp_knowledge_source", {"record_id": record_id})
                        assert source["record"]["source_id"] == record["source_id"]
                        resource = await requester.read_resource(hosts[0].base_uri + "/source/" + record_id[7:])
                        raw = resource.contents[0].text.encode("utf-8")
                        assert raw == corpus.record_bytes(record_id) and bytes_ref(raw) == record_id
                        assert json.loads(raw)["review_status"] == "unreviewed"

                        task = await tool(requester, "ncp_work_propose", {
                            "request_key": "metadata-review-1",
                            "task": {"parent_ref": "intent:metadata-review",
                                     "instruction": "Describe this catalog record's evidence limitations.",
                                     "expected_output": "A cited metadata candidate with limitations.",
                                     "context_ids": [binding["context_id"]]}})
                        assert task["execution_status"] == "proposed" and task["authorization"] is None
                        assert (await tool(worker, "ncp_work_discover", {}))["items"] == []
                        claim_args = {"task_id": task["task_id"], "expected_hash": task["task_hash"],
                                      "attempt_id": "attempt-fixture-1", "bot_label": "unverified fixture bot"}
                        refused = await worker.call_tool("ncp_work_claim", claim_args)
                        assert refused.is_error
                        for session in (requester, worker):
                            names = {item.name for item in (await session.list_tools()).tools}
                            for name in ("ncp_work_authorize", "ncp_work_record_review", "ncp_work_provision_client"):
                                assert name not in names
                                assert (await session.call_tool(name, {})).is_error
                        # Only the explicit trusted-host action authorizes this exact task.
                        store.authorize(task["task_id"], expected_hash=task["task_hash"],
                                        approval_ref="operator:fixture-task-grant", worker_id="worker",
                                        expires_at=2000, lease_seconds=60)
                        eligible = await tool(worker, "ncp_work_discover", {})
                        assert [item["task_id"] for item in eligible["items"]] == [task["task_id"]]
                        mismatch = await worker.call_tool("ncp_work_claim", {
                            **claim_args, "expected_hash": "sha256:" + "0" * 64})
                        assert mismatch.is_error and mismatch.structured_content["reason"] == "task_hash_mismatch"
                        claim = await tool(worker, "ncp_work_claim", claim_args)
                        assert claim["active"] and claim["worker_id"] == "worker"
                        context = await tool(worker, "ncp_work_context", {
                            "task_id": task["task_id"], "context_id": binding["context_id"]})
                        assert context["text"].encode("utf-8") == raw
                        assert context["snapshot_ref"] == pin and context["source_hash"] == record_id
                        assert context["provenance"]["metadata_only"] is True
                        assert context["provenance"]["original_source_id"] == record["source_id"]
                        assert context["source_id"] != record["source_id"]
                        quote = '"review_status":"unreviewed"'
                        assert quote in context["text"]
                        submission = await tool(worker, "ncp_work_submit", {
                            "task_id": task["task_id"], "expected_hash": task["task_hash"],
                            "attempt_id": claim_args["attempt_id"],
                            "result": {"outcome": "completed",
                                       "candidate": "The catalog marks this source as unreviewed.",
                                       "citations": [{"context_id": binding["context_id"], "quote": quote}],
                                       "limitations": ["This is metadata, not source text or scientific qualification."]}})
                        assert submission["structural_status"] == "passed"
                        assert submission["semantic_status"] == "unreviewed"
                        assert submission["verification_id"] is None
                        assert submission["physical_qualification"] == "not_established"
                        retained = await tool(requester, "ncp_work_read", {"task_id": task["task_id"]})
                        assert retained["review"] is None and retained["execution_status"] == "submitted"
                        store.record_review(task["task_id"], result_id=submission["result_id"],
                                            decision="needs_review", reviewer_ref="operator:fixture-reviewer",
                                            scope="metadata-only fixture", findings="Scientific support remains unassessed.")
                        reviewed = await tool(requester, "ncp_work_read", {"task_id": task["task_id"]})
                        assert reviewed["review"]["decision"] == "needs_review"
                        assert reviewed["submission"]["semantic_status"] == "unreviewed"
                        assert corpus.source(record_id)["record"]["review_status"] == "unreviewed"
                    group.cancel_scope.cancel()

    anyio.run(exchange)
