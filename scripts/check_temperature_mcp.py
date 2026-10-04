#!/usr/bin/env python3
"""Exercise installed temperature processing/audit using the independent MCP SDK.

Synthetic software qualification only. Requires test-only mcp==1.26.0.
"""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ciw import temperature_verification
from ciw.agent_api import encode, parse
from ciw.agent_mcp import temperature_config
from ciw.session import Session
from ciw.temperature_contract import AUTHORITY
from ciw.temperature_workflow import PROCESS, VERIFY


async def campaign(root: Path):
    root.mkdir(parents=True, exist_ok=False)
    checks, transcript = [], []
    profile = temperature_config(root / "profile")
    server = StdioServerParameters(command=sys.executable, args=[
        "-I", "-m", "ciw.agent_mcp", "serve", "--instrument", "temperature", "--profile", str(profile)])
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            checks.append("independent-sdk-initialize")
            async def call(name, arguments):
                response = await client.call_tool(name, arguments)
                value = response.structuredContent
                if response.isError or not isinstance(value, dict) or json.loads(response.content[0].text) != value:
                    raise RuntimeError("Invalid MCP response for " + name)
                transcript.append({"tool": name, "arguments": arguments, "result": value})
                return value
            caps = await call("net_capabilities", {})
            assert set(caps["catalog"]["operations"]) == {PROCESS, VERIFY}
            assert all(caps["catalog"]["operations"][op]["agent_execution_enabled"] for op in (PROCESS, VERIFY))
            checks.append("explicit-processing-and-checker-grants")
            first = await call("net_execute", {"source": "source", "graph": "baseline", "attempt": "first"})
            assert first["status"] == "completed" and len(first["execution_ids"]) == 2
            checks.append("actual-processing-and-numerical-checker")
            retry = await call("net_execute", {"source": "source", "graph": "baseline", "attempt": "first"})
            assert retry["reused_response"] and retry["execution_ids"] == first["execution_ids"]
            checks.append("idempotent-retry")
            replay = await call("net_replay", {"original_attempt": "first", "new_attempt": "replayed"})
            assert replay["status"] == "completed" and len(replay["execution_ids"]) == 2
            assert set(first["execution_ids"]).isdisjoint(replay["execution_ids"])
            assert set(first["result_ids"]).isdisjoint(replay["result_ids"])
            checks.append("fresh-processing-and-checker-replay")
    records, workspaces = [], []
    for attempt in ("first", "replayed"):
        path = profile.parent / "agent-output" / attempt / "workspace.json"
        raw = path.read_bytes()
        workspace = parse(raw)
        result_map = {row["operation_id"]: row for row in workspace["results"]}
        processing, verification = result_map[PROCESS], result_map[VERIFY]
        assert processing["data"]["acceptance"]["status"] == "indeterminate"
        assert verification["data"]["report"]["status"] == "PASS"
        assert verification["parameters"]["processing"] == processing
        assert processing["data"]["authority"] == verification["data"]["report"]["authority"] == AUTHORITY
        assert all(row["verification_status"] == "not_verified" and row["verification_id"] is None
                   for row in result_map.values())
        records.append(verification["data"]["verification_id"])
        Session.from_workspace(path, root / (attempt + "-readback"))
        assert path.read_bytes() == raw
        workspaces.append({"attempt": attempt, "sha256": sha256(raw).hexdigest()})
    assert records[0] != records[1]
    checks += ["audit-pass-does-not-promote-operational-or-physical-acceptance",
               "fresh-verification-occurrences-and-unchanged-readback"]
    (root / "transcript.json").write_bytes(encode({"calls": transcript}))
    report = {"schema": "ciw.temperature-mcp-client-check.v1", "status": "PASS",
              "origin": "synthetic_software_fixture_not_experimental_evidence",
              "python": platform.python_version(), "mcp_sdk": version("mcp"),
              "installed_checker": str(Path(temperature_verification.__file__).resolve()),
              "checks": checks, "verification_ids": records, "workspaces": workspaces,
              "authority": AUTHORITY}
    (root / "report.json").write_bytes(encode(report))
    print(json.dumps({"status": "PASS", "checks": len(checks), "report": str(root / "report.json")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assert version("mcp") == "1.26.0", "Require the declared independent SDK version"
    checkout = Path(__file__).resolve().parents[1]
    installed = Path(temperature_verification.__file__).resolve()
    assert not installed.is_relative_to(checkout), "Run against an installed wheel outside source imports"
    asyncio.run(campaign(args.output_dir.resolve()))


if __name__ == "__main__":
    main()
