#!/usr/bin/env python3
"""Qualify installed thermal delivery and reconnect through the official MCP SDK.

This checks software using an explicitly supplied source. Fixture acceptance and
numerical agreement do not establish physical accuracy or calibration trust.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from hashlib import sha256
from importlib.metadata import distribution, version
import json
from pathlib import Path
import platform
import sys
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ciw import agent_api, measured_thermal_workflow
from ciw.agent_api import encode
from ciw.agent_mcp import measured_thermal_config
from ciw.measured_thermal_agent import SOURCE_LIMIT


def require(condition, message):
    if not condition:
        raise ValueError(message)


def wheel_identity(wheel: Path):
    package = Path(agent_api.__file__).resolve().parent
    project = Path(__file__).resolve().parents[1]
    require(not package.is_relative_to(project), "Qualification requires an installed package outside source")
    metadata = distribution("computational-instrumentation-workbench")
    direct = json.loads(metadata.read_text("direct_url.json") or "{}")
    require(not direct.get("dir_info", {}).get("editable"), "Editable installation cannot qualify")
    files = {}
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            if item.is_dir() or not item.filename.startswith("ciw/"):
                continue
            path = Path(item.filename).relative_to("ciw")
            require(not path.is_absolute() and ".." not in path.parts and item.filename not in files,
                    "Invalid or repeated wheel package path")
            digest = sha256(archive.read(item)).hexdigest()
            require(sha256((package / path).read_bytes()).hexdigest() == digest,
                    "Installed package differs from wheel: " + item.filename)
            files[item.filename] = digest
    require(bool(files), "Wheel has no ciw package")
    return {"wheel_sha256": sha256(wheel.read_bytes()).hexdigest(), "package": str(package),
            "ciw_files_matched": len(files), "files": files}


async def campaign(root: Path, source: Path):
    with source.open("rb") as stream:
        source_bytes = stream.read(SOURCE_LIMIT + 1)
    require(0 < len(source_bytes) <= SOURCE_LIMIT, "Measured thermal MCP source exceeds 48 KiB")
    profile = measured_thermal_config(root / "profile", source)
    checks, transcript = [], []
    first = bundle = replay = None
    for resumed in (False, True):
        arguments = ["-I", "-m", "ciw.agent_mcp", "serve", "--instrument", "measured-thermal",
                     "--profile", str(profile)]
        if resumed:
            arguments.append("--resume")
        server = StdioServerParameters(command=sys.executable, args=arguments, cwd=str(root))
        with (root / ("resume-stderr.log" if resumed else "first-stderr.log")).open("w", encoding="utf-8") as errors:
            async with stdio_client(server, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()

                    async def call(name, values):
                        response = await client.call_tool(name, values)
                        value = response.structuredContent
                        transcript.append({"resumed": resumed, "tool": name, "arguments": values,
                                           "result": value, "isError": response.isError})
                        (root / "transcript.json").write_bytes(encode({"calls": transcript}))
                        require(value is not None and json.loads(response.content[0].text) == value,
                                "MCP text and structured results disagree")
                        require(not response.isError, "MCP call refused: " + str(value))
                        return value

                    caps = await call("net_capabilities", {})
                    operations = caps["catalog"]["operations"]
                    require(set(operations) == {"measured-thermal.assess.v1"}
                            and operations["measured-thermal.assess.v1"]["agent_execution_enabled"],
                            "Unexpected measured thermal grants")
                    if not resumed:
                        checks.append("explicit-thermal-profile-through-official-sdk")
                        first = await call("net_execute", {"source": "source", "graph": "baseline", "attempt": "pilot"})
                        require(first["status"] == "completed" and len(first["execution_ids"]) == 1,
                                "Thermal graph did not complete")
                        checks.append("existing-scientific-workflow-retained")
                    else:
                        require(caps["execution_budget"]["used"] == 1, "Restart lost consumed execution budget")
                        retried = await call("net_execute", {"source": "source", "graph": "baseline", "attempt": "pilot"})
                        require(retried == {**first, "reused_response": True}, "Restart duplicated or changed the attempt")
                        checks.append("new-server-process-reuses-original-response-and-budget")
                    inspection = await call("net_inspect", {
                        "artifact": first["artifacts"]["graph-run"]["artifact_id"],
                        "selector": ["nodes", "assessment", "result", "data"],
                    })
                    payload = inspection["data"]
                    require(payload["source_origin"] == json.loads(source_bytes)["origin"], "Origin changed")
                    require(payload["correction_status"] == "not_checked; no_state_ledger_handoff_performed",
                            "Unperformed correction handoff was promoted")
                    current = payload["bundle"]
                    measured_thermal_workflow.MeasuredThermalWorkflow()._validate(current)
                    raw = base64.b64decode(current["source"]["evidence"][0]["bytes_b64"], validate=True)
                    require(raw == source_bytes, "Original recording bytes were not retained")
                    scientific = current["steps"][0]["result"]["data"]
                    require(scientific["authority"]["physical_validation"] == "not_established"
                            and scientific["authority"]["state_admission"] == "not_performed",
                            "Physical or admission authority was promoted")
                    require(scientific["evaluation"]["uncertainty_coverage"] == "not_established"
                            and scientific["recording"]["assumptions"]["input_model_clock_uncertainty"]
                            == "retained_not_propagated_by_this_kernel", "Uncertainty scope disappeared")
                    if not resumed:
                        bundle = current
                        checks.extend(["exact-input-provenance-and-native-bundle-validation",
                                       "uncertainty-physical-admission-and-correction-limits-visible"])
                    else:
                        require(current == bundle, "Reopening changed scientific evidence")
                        checks.append("retained-scientific-result-inspectable-after-reconnect")
                        replay = await call("net_replay", {"original_attempt": "pilot", "new_attempt": "replayed"})
                        require(replay["status"] == "completed" and replay["execution_ids"] != first["execution_ids"]
                                and replay["result_ids"] != first["result_ids"], "Explicit replay reused occurrence IDs")
                        again = await call("net_inspect", {
                            "artifact": replay["artifacts"]["graph-run"]["artifact_id"],
                            "selector": ["nodes", "assessment", "result", "data", "bundle", "steps", 0],
                        })
                        step = again["data"]
                        require(step["numerical_result_id"] == bundle["steps"][0]["numerical_result_id"]
                                and step["execution_id"] != bundle["steps"][0]["execution_id"],
                                "Fresh scientific occurrence failed numerical reproduction")
                        checks.append("explicit-replay-new-identities-same-numerical-result")
                        final_caps = await call("net_capabilities", {})
                        require(final_caps["execution_budget"]["used"] == 2, "Replay budget accounting differs")
                        checks.append("cumulative-execution-budget-survives-reconnect")
    return {"status": "passed", "checks": checks, "check_count": len(checks),
            "source_sha256": sha256(source_bytes).hexdigest(), "source_origin": json.loads(source_bytes)["origin"],
            "client": "official MCP Python SDK", "client_version": version("mcp"),
            "python": platform.python_version(), "platform": platform.platform(),
            "execution_ids": first["execution_ids"] + replay["execution_ids"],
            "scope": "installed software, retained source, independent MCP client and process reconnect",
            "physical_validation": "not_established", "state_admission": "not_performed",
            "correction_status": "not_checked; no_state_ledger_handoff_performed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-wheel", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=False, exist_ok=False)
    try:
        require(sys.flags.isolated == 1, "Run this qualification using python -I")
        before = wheel_identity(args.expected_wheel)
        report = asyncio.run(asyncio.wait_for(campaign(root, args.source.resolve()), timeout=180))
        require(wheel_identity(args.expected_wheel) == before, "Installed package changed during qualification")
        report["wheel_identity"] = before
        report["harness_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
        report["artifact_sha256"] = {x.relative_to(root).as_posix(): sha256(x.read_bytes()).hexdigest()
                                     for x in root.rglob("*") if x.is_file()}
    except Exception as exc:
        (root / "report.json").write_bytes(encode({"status": "failed", "type": type(exc).__name__, "reason": str(exc)[:4000]}))
        raise
    (root / "report.json").write_bytes(encode(report))
    print(json.dumps({key: value for key, value in report.items() if key not in {"artifact_sha256", "wheel_identity"}}, indent=2))


if __name__ == "__main__":
    main()
