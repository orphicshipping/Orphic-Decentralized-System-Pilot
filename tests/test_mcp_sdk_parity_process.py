"""The MCP control server in a separate process returns what the in-process SDK returns.

One canonical service answers both: the stdio server (``python -m ciw.net
control serve``) and ``ciw.services.invoke``. Retained artifact ids are owner
scoped, so they are compared by reading the artifact back on each side.
"""
import asyncio
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

import ciw
from ciw.sequence_kinetics import example_request
from ciw.services import invoke

try:
    SDK = version("mcp")
except PackageNotFoundError:
    SDK = None
pytestmark = pytest.mark.skipif(SDK != "2.3.0", reason="requires optional pinned MCP SDK 2.3.0")

CALLS = [
    ("ncp_domain_list", {}),
    ("ncp_workbench_list", {}),
    ("ncp_domain_run", {"domain": "sequence", "example": True}),
    ("ncp_domain_run", {"domain": "sequence", "request": example_request()}),
]
REFUSED = [
    ("ncp_domain_run", {"domain": "sequence", "request": {"blob": "x" * 70000}}),
    ("ncp_domain_run", {"domain": "sequence", "checkout": "/tmp/elsewhere"}),
    ("ncp_coordinate", {"action": "submit", "idempotency_key": "parity-1", "operation": "ncp_domain_run",
                        "arguments": {"domain": "sequence", "request": {"blob": "x" * 70000}}}),
]


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k != "artifact_id"}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def test_separate_process_mcp_matches_in_process_sdk(tmp_path, monkeypatch):
    from ciw.agent_mcp import demo_config
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "sdk-retain"))
    env = dict(os.environ, PYTHONPATH=str(Path(ciw.__file__).parent.parent),
               NCP_RETAIN_DIR=str(tmp_path / "mcp-retain"))
    args = ["-m", "ciw.net", "control", "serve", "--profile", str(demo_config(tmp_path / "profile"))]
    remote, remote_refusals, remote_reads = [], [], []

    async def run():
        params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=str(tmp_path))
        with (tmp_path / "stderr.log").open("w", encoding="utf-8") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=60) as client:
                    await client.discover()
                    for name, arguments in CALLS:
                        result = await client.call_tool(name, arguments)
                        assert not result.is_error, result
                        envelope = result.structured_content
                        assert envelope["status"] == "ok"
                        remote.append(envelope["data"])
                        if isinstance(envelope["data"], dict) and "artifact_id" in envelope["data"]:
                            back = await client.call_tool("ncp_retained_read", {"artifact_id": envelope["data"]["artifact_id"]})
                            remote_reads.append(back.structured_content["data"])
                    for name, arguments in REFUSED:
                        result = await client.call_tool(name, arguments)
                        remote_refusals.append(result.is_error or result.structured_content["status"] == "refused")

    asyncio.run(run())
    assert remote and remote_reads

    local, local_reads = [], []
    for name, arguments in CALLS:
        value = invoke(name, json.loads(json.dumps(arguments)))
        local.append(value)
        if isinstance(value, dict) and "artifact_id" in value:
            local_reads.append(invoke("ncp_retained_read", {"artifact_id": value["artifact_id"]}))
    assert _strip(remote) == _strip(json.loads(json.dumps(local)))
    assert len(remote_reads) == len(local_reads) == 2
    assert _strip(remote_reads) == _strip(json.loads(json.dumps(local_reads)))

    assert remote_refusals == [True] * len(REFUSED)
    for name, arguments in REFUSED:
        with pytest.raises(ValueError):
            invoke(name, arguments)
