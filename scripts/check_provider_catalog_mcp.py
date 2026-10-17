"""Fixture-only installed-process check using the existing test client mcp==1.26.0."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import ciw.agent_api
import ciw.agent_mcp
import ciw.control_plane
from ciw.agent_api import encode
from ciw.control_plane import inspect_provider_catalog


async def campaign(fixture: Path, root: Path, workspace: Path | None = None):
    root.mkdir(parents=True, exist_ok=False)
    raw = (fixture / "catalog.json").read_bytes()
    saved = json.loads(raw)
    expected = inspect_provider_catalog(saved)
    source = root / "catalog.json"
    source.write_bytes(raw)
    shutil.copyfile(fixture / "profile.json", root / "profile.json")
    workspace_raw = None
    if workspace is not None:
        workspace_raw = workspace.read_bytes()
        (root / "workspace.json").write_bytes(workspace_raw)
        profile = json.loads((root / "profile.json").read_bytes())
        profile["inputs"]["workspace"] = "workspace.json"
        (root / "profile.json").write_bytes(encode(profile))
    command = ["-I", "-m", "ciw.agent_mcp", "serve", "--profile", str(root / "profile.json")]
    calls = []
    with (root / "stderr.log").open("w", encoding="utf-8") as errlog:
        async with stdio_client(StdioServerParameters(command=sys.executable, args=command), errlog=errlog) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=20)) as client:
                initialized = await client.initialize()
                assert initialized.protocolVersion == "2025-11-25"
                assert initialized.capabilities.model_dump(exclude_none=True) == {"tools": {"listChanged": False}}
                listed = await client.list_tools()
                assert len(listed.tools) == 11
                inspection = next(tool for tool in listed.tools if tool.name == "net_inspect")
                assert inspection.annotations.readOnlyHint is True
                async def call(name, arguments, error=False):
                    result = await client.call_tool(name, arguments)
                    assert result.isError is error
                    value = result.structuredContent
                    assert json.loads(result.content[0].text) == value
                    calls.append({"tool": name, "arguments": arguments, "result": value, "isError": result.isError})
                    return value
                before = await call("net_capabilities", {})
                assert before["execution_budget"]["used"] == 0
                assert before["output_enabled"] is False
                assert all(not row["bound"] and not row["agent_execution_enabled"]
                           for row in before["catalog"]["operations"].values())
                # Startup already validated and froze these exact bytes. Replacement is data,
                # and cannot change the validated response or its original byte reference.
                source.write_bytes(b'{"replacement":"not the validated catalog"}')
                result = await call("net_inspect", {"artifact": "catalog"})
                assert result["data"] == expected
                assert result["binding"]["sha256"] == "sha256:" + sha256(raw).hexdigest()
                assert result["binding"]["artifact_id"] == "a-" + sha256(raw).hexdigest()
                assert result["binding"]["size_bytes"] == len(raw)
                if workspace_raw is not None:
                    original = json.loads(workspace_raw)
                    retained = await call("net_inspect", {"artifact": "workspace"})
                    assert retained["binding"]["sha256"] == "sha256:" + sha256(workspace_raw).hexdigest()
                    assert retained["data"]["executions"] == {
                        row["execution_id"]: row for row in original["executions"]}
                    assert retained["data"]["results"] == {
                        row["result_id"]: row for row in original["results"]}
                    assert retained["data"]["session_identity"] == "original_session_id_not_retained_by_workspace_schema"
                assert (await call("net_inspect", {"artifact": "catalog", "selector": [
                    "catalog", "operations", "statistics.v1", "bound"]}))["data"] is False
                for arguments in ({"artifact": "../catalog.json"}, {"artifact": "missing"},
                                  {"artifact": "catalog", "limit": 129},
                                  {"artifact": "catalog", "selector": [True]},
                                  {"artifact": "catalog", "execute": True}):
                    await call("net_inspect", arguments, error=True)
                await call("net_execute", {"source": "catalog", "graph": "catalog", "attempt": "denied"}, error=True)
                await call("net_accept", {}, error=True)
                assert await call("net_capabilities", {}) == before
                await client.send_ping()
    source.write_bytes(raw)
    transcript = {"calls": calls}
    (root / "transcript.json").write_bytes(encode(transcript))
    # Raw process observer checks every stdout line, both declared revisions,
    # notification silence, protocol errors, line limits and clean EOF shutdown.
    for revision in ("2025-11-25", "2025-06-18"):
        messages = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": revision, "capabilities": {},
                "clientInfo": {"name": "catalog-raw-check", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "net_inspect", "arguments": {"artifact": "catalog"}}},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}},
            {"jsonrpc": "2.0", "id": 3, "method": "unsupported"},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        run = subprocess.run([sys.executable, *command],
            input=b"\n".join(encode(value) for value in messages) + b"\n",
            capture_output=True, timeout=30)
        (root / (revision + "-stdout.jsonl")).write_bytes(run.stdout)
        (root / (revision + "-stderr.log")).write_bytes(run.stderr)
        assert run.returncode == 0, run.stderr
        responses = [json.loads(line) for line in run.stdout.splitlines()]
        assert [response["id"] for response in responses] == [0, 1, 2, 3, 4]
        assert responses[0]["result"]["protocolVersion"] == revision
        assert responses[2]["result"]["structuredContent"]["data"] == expected
        assert responses[3]["error"]["code"] == -32601
        assert responses[4]["result"] == {}
    oversized = subprocess.run([sys.executable, *command], input=b"x" * (1024 * 1024 + 1),
        capture_output=True, timeout=30)
    assert oversized.returncode == 2
    assert len(oversized.stdout.splitlines()) == 1
    assert json.loads(oversized.stdout)["error"]["code"] == -32700
    (root / "oversized-stdout.jsonl").write_bytes(oversized.stdout)
    assert not (root / "agent-output").exists()
    report = {
        "status": "passed", "source_class": "contract_fixture",
        "fixture_sha256": sha256(raw).hexdigest(), "client": "mcp", "client_version": version("mcp"),
        "python": sys.version, "executable": sys.executable, "entrypoint": command,
        "protocol_versions": ["2025-11-25", "2025-06-18"],
        "imports": {module.__name__: module.__file__ for module in
                    (ciw.agent_api, ciw.agent_mcp, ciw.control_plane)},
        "scientific_executions": 0, "named_host": "not_verified",
        "workspace_sha256": sha256(workspace_raw).hexdigest() if workspace_raw is not None else None,
        "cancellation": "completed-request notification ignored; no in-flight cancellation claimed",
    }
    (root / "report.json").write_bytes(encode(report))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, help="Existing authorized built-in Session workspace; read only")
    args = parser.parse_args()
    asyncio.run(campaign(args.fixture.resolve(), args.output_dir.resolve(),
                         args.workspace.resolve() if args.workspace else None))
