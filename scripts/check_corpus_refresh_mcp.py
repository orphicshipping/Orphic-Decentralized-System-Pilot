#!/usr/bin/env python
"""Qualify a prepared metrology corpus through original bytes and real MCP stdio.

Uses retained local inputs, never Drive, provider execution or a live host change.
Requires the project's optional mcp-control extra.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import zipfile

from ciw.evidence_corpus import EvidenceCorpus
from ciw.proto_corpus import ProtoCorpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.refresh.resolve()
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    catalog = ProtoCorpus.open(root / "catalog", receipt["catalog_ref"])
    evidence = EvidenceCorpus.open(root / "evidence", receipt["evidence_ref"], catalog)
    sources = {r["source_id"]: r for r in receipt["sources"]}
    for source in sources.values():
        raw = (root / source["original_bytes"]).read_bytes()
        assert len(raw) == source["size_bytes"] and sha256(raw).hexdigest() == source["sha256"]
        record = catalog.resolve(source["source_id"])["record"]
        assert record["source_sha256"] == source["sha256"]
        assert record["authorizes_execution"] is False and record["rights_review"] == "unresolved"
        assert record["selected_annotations"][0]["fulltext_ingestion_approved"] is False
    before = {p: sha256(p.read_bytes()).hexdigest() for folder in ("catalog", "evidence", "originals", "provenance")
              for p in (root / folder).rglob("*") if p.is_file()}
    checked_spans = 0
    representations = evidence.representations(limit=50)["items"]
    for representation in representations:
        rid = representation["representation_id"]
        source = evidence.source(rid, limit=50)
        descriptor = source["descriptor"]
        original = sources[descriptor["source_id"]]
        with zipfile.ZipFile(root / original["original_bytes"]) as archive:
            raw = archive.read(descriptor["archive_member"])
        assert sha256(raw).hexdigest() == descriptor["text_hash"][7:]
        offset = 0
        chunks = []
        while True:
            page = evidence.source(rid, offset=offset, limit=50)
            for item in page["items"]:
                span = evidence.span(item["span_id"])["span"]
                location = span["locator"]
                original_bytes = raw[location["byte_start"]:location["byte_end_exclusive"]]
                assert span["text"].encode("utf-8") == original_bytes
                assert sha256(original_bytes).hexdigest() == location["span_sha256"][7:]
                chunks.append(original_bytes)
                checked_spans += 1
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert b"".join(chunks) == raw
    config = {"command": sys.executable, "args": ["-m", "ciw.knowledge_cli", "serve",
              "--bundle", str(root / "catalog"), "--expected-ref", receipt["catalog_ref"],
              "--evidence-bundle", str(root / "evidence"), "--evidence-ref", receipt["evidence_ref"]],
              "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}}
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    calls = []

    async def run(modern):
        first_call = len(calls)
        with anyio.fail_after(60):
            async with stdio_client(StdioServerParameters(**config)) as streams:
                async with ClientSession(*streams) as client:
                    await (client.discover() if modern else client.initialize())
                    tools = await client.list_tools()
                    names = {tool.name for tool in tools.tools}
                    assert len(names) == 12 and "ncp_evidence_span" in names
                    async def call(name, arguments, *, refused=False):
                        value = await client.call_tool(name, arguments)
                        result = value.structured_content
                        assert result["status"] == ("refused" if refused else "ok"), result
                        calls.append({"protocol": client.protocol_version, "tool": name, "arguments": arguments, "status": result["status"]})
                        return result.get("data")
                    status = await call("ncp_system_status", {})
                    assert status["source_execution"] is False
                    overview = await call("ncp_evidence_overview", {})
                    assert overview["spans"] == checked_spans
                    py = await call("ncp_knowledge_resolve", {"source_id": "gdrive:1mZUcrLME5lfgI0JHocewNd7anEePUdwU"})
                    record = py["record"]
                    found = await call("ncp_evidence_search", {"query": "None certain", "record_id": record["record_id"], "limit": 20})
                    hit = next(x for x in found["items"] if x["archive_member"].endswith("propagate_convolution.py"))
                    span = await call("ncp_evidence_span", {"span_id": hit["span_id"]})
                    assert span["span"] == evidence.span(hit["span_id"])["span"]
                    assert "certain" in span["span"]["text"] and "None" in span["span"]["text"]
                    timing = await call("ncp_knowledge_search", {"query": "timing sampling"})
                    assert any(x["source_id"] == "gdrive:1zdkio_3eVDD4Tu81DgGj-dSjzKZumrXY" for x in timing["items"])
                    restrictions = await call("ncp_knowledge_search", {"query": "TECKit observational"})
                    assert len(restrictions["items"]) == 1
                    paper = await call("ncp_knowledge_resolve", {"source_id": "gdrive:1QovAf3VIAfF85Ni_-V-rY7SR850OBmmf"})
                    excluded = await call("ncp_evidence_representations", {"record_id": paper["record"]["record_id"]})
                    assert excluded["total"] == 0
                    reviews = await call("ncp_knowledge_review_queue", {"kind": "rights_review", "limit": 50})
                    assert reviews["total"] == 14
                    await call("ncp_evidence_search", {"query": "certain", "path": "arbitrary-source"}, refused=True)
                    await call("ncp_work_authorize", {}, refused=True)
                    uri = "ncp://proto-corpus/" + catalog.ref[7:] + "/source/" + record["record_id"][7:]
                    resource = await client.read_resource(uri)
                    assert resource.contents[0].text.encode("utf-8") == catalog.record_bytes(record["record_id"])
                    return {"protocol": client.protocol_version, "tools": len(names), "tool_calls": len(calls) - first_call,
                            "resource_reads": 1, "server_processes": 1}
    protocols = [anyio.run(run, False), anyio.run(run, True)]
    assert before == {path: sha256(path.read_bytes()).hexdigest() for path in before}
    result = {"status": "passed", "catalog_ref": catalog.ref, "evidence_ref": evidence.ref,
              "source_hashes_checked": len(sources), "exact_representations_checked": len(representations),
              "exact_spans_checked": checked_spans, "protocol_runs": protocols, "tool_calls": len(calls),
              "resource_reads": 2, "server_processes": 2, "calls": calls,
              "scientific_dispatches": 0, "instrument_writes": 0, "live_host_activated": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    config_path = args.output.with_name(args.output.stem + "-mcp-config.json")
    with config_path.open("x", encoding="utf-8") as stream:
        json.dump({"mcpServers": {"ncp-knowledge-reviewed-metrology": config}}, stream, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != "calls"}, indent=2))


if __name__ == "__main__":
    main()
