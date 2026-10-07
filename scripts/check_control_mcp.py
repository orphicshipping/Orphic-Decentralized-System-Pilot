#!/usr/bin/env python3
"""Real SDK 2.3 clients: discover -> plan -> admit -> run -> inspect -> resume.

Run from an isolated installed environment with external-work and bench-models.
Optional leakage arguments exercise the original pinned native provider.
"""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import sys
import time

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ciw.agent_mcp import demo_config, polymer_config, temperature_config, leakage_config


async def campaign(args, root):
    factory = {"builtin": demo_config, "polymer": polymer_config,
               "temperature": temperature_config, "leakage": leakage_config}[args.instrument]
    profile = factory(root / "profile")
    source_files = {str(p): sha256(p.read_bytes()).hexdigest()
                    for p in profile.parent.iterdir() if p.is_file()}
    command = ["-I", "-m", "ciw.net", "control", "serve", "--profile", str(profile),
               "--instrument", args.instrument]
    if args.provider_checkout:
        command += ["--provider-checkout", str(args.provider_checkout), "--python", sys.executable]
    calls, protocols = [], []
    completed = None
    for modern in (True, False):
        params = StdioServerParameters(command=sys.executable,
            args=command + ([] if modern else ["--resume"]), cwd=str(root))
        with (root / ("modern.log" if modern else "legacy.log")).open("w", encoding="utf-8") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=90) as client:
                    await (client.discover() if modern else client.initialize())
                    protocols.append(client.protocol_version)
                    listed = (await client.list_tools()).model_dump(by_alias=True, mode="json")
                    tools = {row["name"]: row for row in listed["tools"]}
                    assert "ncp_plan_create" in tools and "net_execute" not in tools
                    if modern:
                        assert listed["cacheScope"] == "private" and listed["ttlMs"] == 0

                    async def call(name, arguments, error=False):
                        raw = (await client.call_tool(name, arguments)).model_dump(by_alias=True, mode="json")
                        assert raw["isError"] is error, raw
                        body = raw["structuredContent"]
                        assert json.loads(raw["content"][0]["text"]) == body
                        jsonschema.validate(body, tools[name]["outputSchema"])
                        calls.append({"protocol": client.protocol_version, "tool": name,
                                      "arguments": arguments, "response": body})
                        return body["data"]

                    if not modern:
                        saved = await call("ncp_run_get", {"run_id": completed["run_id"]})
                        assert saved == completed
                        retry = await call("ncp_run_submit", {"plan_id": completed["plan_id"],
                            "expected_digest": completed["plan_digest"], "attempt": "sdk-journey"})
                        assert retry == completed
                        continue
                    workflows = await call("ncp_workflows_list", {"limit": 32})
                    assert workflows["total"] == len(workflows["items"]) == 29
                    assert all(not row["authorizes_execution"] for row in workflows["items"])
                    await call("ncp_capabilities_list", {})
                    template = await call("ncp_plan_template", {"baseline": "baseline"})
                    draft = await call("ncp_plan_create", {"source": "source", "baseline": "baseline",
                                                           "graph": template["graph"]})
                    assert draft["admitted"] is False
                    resource = await client.read_resource(draft["uri"])
                    assert json.loads(resource.contents[0].text) == draft
                    await call("ncp_run_submit", {"plan_id": draft["plan_id"],
                        "expected_digest": draft["digest"], "attempt": "not-admitted"}, error=True)
                    await call("ncp_plan_admit", {"plan_id": draft["plan_id"],
                        "expected_digest": "sha256:" + "0" * 64}, error=True)
                    await call("ncp_plan_admit", {"plan_id": draft["plan_id"], "expected_digest": draft["digest"]})
                    submitted = await call("ncp_run_submit", {"plan_id": draft["plan_id"],
                        "expected_digest": draft["digest"], "attempt": "sdk-journey"})
                    until = time.monotonic() + 240
                    while time.monotonic() < until:
                        completed = await call("ncp_run_get", {"run_id": submitted["run_id"]})
                        if completed["state"] not in ("queued", "running"):
                            break
                        await asyncio.sleep(.2)
                    assert completed["state"] == "completed", completed
                    assert completed["result"]["execution_ids"]
                    run_resource = await client.read_resource(completed["uri"])
                    assert json.loads(run_resource.contents[0].text) == completed
                    await call("ncp_artifact_read", {"artifact": completed["result"]["artifacts"]["graph-run"]["artifact_id"],
                        "selector": ["status"]})
                    if args.instrument == "builtin":
                        comparison = await call("ncp_compare", {"left": "reference", "right": "reference", "policy": "strict"})
                        checked = await call("ncp_qualify", {"suite": "regression",
                            "inputs": {"difference": comparison["comparison"]["artifact_id"]}})
                        assert checked["summary"]["status"] == "PASS"
    assert protocols == ["2026-07-28", "2025-11-25"], protocols
    assert all(sha256(Path(p).read_bytes()).hexdigest() == h for p, h in source_files.items())
    (root / "transcript.json").write_text(json.dumps(calls, indent=2), encoding="utf-8")
    return {"status": "PASS", "instrument": args.instrument, "sdk": version("mcp"),
        "protocols": protocols, "calls": len(calls), "execution_ids": completed["result"]["execution_ids"],
        "source_files_unchanged": source_files, "resume_and_retry_preserved_run": True,
        "scope": "labelled synthetic inputs; actual installed MCP and registered computation; no physical qualification",
        "transcript_sha256": sha256((root / "transcript.json").read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--instrument", choices=["builtin", "polymer", "temperature", "leakage"], default="builtin")
    parser.add_argument("--provider-checkout", type=Path)
    args = parser.parse_args()
    root = args.output_dir.absolute()
    root.mkdir(parents=True, exist_ok=False)
    try:
        value = asyncio.run(campaign(args, root))
    except BaseException as exc:
        (root / "report.json").write_text(json.dumps({"status": "FAIL", "reason": str(exc)}), encoding="utf-8")
        raise
    (root / "report.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
