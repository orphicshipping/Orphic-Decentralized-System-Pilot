"""Four consumer journeys on one real input, through the one canonical service.

The input is not a packaged example: it is the binary 3-block law of the bits of
this repository's LICENSE file, read with circular windows so the table is an
exactly stationary declared distribution. Each consumer (deterministic
software, an LLM over MCP stdio, an agent through the control host, and a
coordinator handing work between owners) must reach the same analysis.
"""
import asyncio
import json
import math
import os
import sys
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

import ciw
from ciw import coordinator
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.retention import Store
from ciw.sequence_kinetics import example_request
from ciw.services import invoke

ROOT = Path(__file__).resolve().parents[1]
LICENSE = ROOT / "LICENSE"
K = 3


def real_request():
    data = LICENSE.read_bytes()
    bits = [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]
    counts = [0] * (2 ** K)
    for start in range(len(bits)):
        word = 0
        for offset in range(K):
            word = word * 2 + bits[(start + offset) % len(bits)]
        counts[word] += 1
    total = len(bits)
    probs = [c / total for c in counts]
    return {"schema": example_request()["schema"], "source_kind": "declared_distribution",
            "source_ref": "sha256:" + sha256(data).hexdigest(), "alphabet": ["0", "1"], "block_length": K,
            "probabilities": probs, "unit": "1", "queries": [["1"], ["0", "1"], ["0", "1", "1", "0", "0"]],
            "allow_markov_closure": True, "evolution": None}, counts, total


def stable(value):
    """Drop owner-scoped ids; everything else must agree across consumers."""
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in {"artifact_id", "replayed"}}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


@pytest.fixture
def retain(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retain"))
    return tmp_path / "retain"


def test_real_input_is_a_valid_stationary_table():
    request, counts, total = real_request()
    assert LICENSE.is_file() and sum(counts) == total > 10000
    assert math.isclose(math.fsum(request["probabilities"]), 1.0, abs_tol=1e-12)
    assert request["probabilities"] != example_request()["probabilities"]


def test_deterministic_software_journey(retain):
    request, counts, total = real_request()
    first = invoke("ncp_domain_run", {"domain": "sequence", "request": request})
    again = invoke("ncp_domain_run", {"domain": "sequence", "request": request})
    assert first["status"] == "PASS" and stable(first) == stable(again)
    record = invoke("ncp_retained_read", {"artifact_id": first["artifact_id"]})
    assert record["schema"] == "ncp.retained-artifact.v1" and record["byte_size"] > 0
    # Independent value: H(X3 | X1 X2) = H(3-blocks) - H(2-blocks) from the raw counts.
    pairs = [counts[2 * i] + counts[2 * i + 1] for i in range(4)]
    entropy = lambda xs: -math.fsum(c / total * math.log(c / total) for c in xs if c)
    assert first["markov_entropy_rate_nats_per_symbol"] == pytest.approx(entropy(counts) - entropy(pairs), rel=1e-12)
    invoke("ncp_retained_release", {"artifact_id": first["artifact_id"]})
    with pytest.raises(ValueError):
        invoke("ncp_retained_read", {"artifact_id": first["artifact_id"]})


def test_agent_journey_through_the_control_host(tmp_path, retain):
    request, _, _ = real_request()
    owner = from_profile(demo_config(tmp_path / "agent"))
    host = ControlHost(owner)
    try:
        ran = host.call("ncp_domain_run", {"domain": "sequence", "request": request})
        assert ran["status"] == "ok", ran
        read = host.call("ncp_retained_read", {"artifact_id": ran["data"]["artifact_id"]})
        assert read["status"] == "ok"
        sdk = invoke("ncp_domain_run", {"domain": "sequence", "request": request})
        assert stable(ran["data"]) == stable(sdk)
        smuggled = host.call("ncp_domain_run", {"domain": "sequence", "request": request, "owner": "other"})
        assert smuggled["status"] == "refused"
    finally:
        owner.close()


SDK = None
try:
    SDK = version("mcp")
except PackageNotFoundError:
    pass


@pytest.mark.skipif(SDK != "2.3.0", reason="requires optional pinned MCP SDK 2.3.0")
def test_llm_journey_over_mcp_stdio(tmp_path, retain):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    request, _, _ = real_request()
    env = dict(os.environ, PYTHONPATH=str(Path(ciw.__file__).parent.parent), NCP_RETAIN_DIR=str(tmp_path / "mcp"))
    args = ["-m", "ciw.net", "control", "serve", "--profile", str(demo_config(tmp_path / "llm"))]
    seen = {}

    async def run():
        params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=str(tmp_path))
        with (tmp_path / "err.log").open("w", encoding="utf-8") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=60) as client:
                    await client.discover()
                    tools = {t.name: t for t in (await client.list_tools()).tools}
                    assert "ncp_domain_run" in tools and "ncp_coordinate" in tools
                    ran = await client.call_tool("ncp_domain_run", {"domain": "sequence",
                                                                    "request": request})
                    seen["run"] = ran.structured_content
                    aid = ran.structured_content["data"]["artifact_id"]
                    seen["read"] = (await client.call_tool("ncp_retained_read", {"artifact_id": aid})).structured_content
                    seen["release"] = (await client.call_tool("ncp_retained_release", {"artifact_id": aid})).structured_content
                    seen["after"] = (await client.call_tool("ncp_retained_read", {"artifact_id": aid}))

    asyncio.run(run())
    assert seen["run"]["status"] == "ok" and seen["read"]["status"] == "ok" and seen["release"]["status"] == "ok"
    assert seen["after"].is_error or seen["after"].structured_content["status"] == "refused"
    sdk = invoke("ncp_domain_run", {"domain": "sequence", "request": request})
    assert stable(seen["run"]["data"]) == stable(sdk)


def test_coordinator_journey_between_two_owners(retain):
    request, _, _ = real_request()
    arguments = {"domain": "sequence", "request": request}
    lab_a, lab_b = Store("lab-a"), Store("lab-b")
    first = coordinator.coordinate("submit", store=lab_a, idempotency_key="license-1",
                                   operation="ncp_domain_run", arguments=arguments)
    replay = coordinator.coordinate("submit", store=lab_a, idempotency_key="license-1",
                                    operation="ncp_domain_run", arguments=arguments)
    assert replay["artifact_id"] == first["artifact_id"]
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        coordinator.coordinate("submit", store=lab_a, idempotency_key="license-1", operation="ncp_domain_run",
                               arguments={**arguments, "request": {**request, "queries": [["1", "1"]]}})
    recalled = coordinator.coordinate("recall", store=lab_a, idempotency_key="license-1")
    assert recalled["status"] not in {"running", "failed"}
    grant = coordinator.coordinate("handoff", store=lab_a, artifact_id=first["artifact_id"], recipient="lab-b")
    claimed = coordinator.coordinate("claim", store=lab_b, grant_id=grant["grant_id"])
    again = coordinator.coordinate("claim", store=lab_b, grant_id=grant["grant_id"])
    assert again["artifact_id"] == claimed["artifact_id"] and again.get("replayed") is True
    with pytest.raises(ValueError):
        coordinator.coordinate("claim", store=Store("intruder"), grant_id=grant["grant_id"])
    received = lab_b.native(claimed["artifact_id"])
    sdk = invoke("ncp_domain_run", arguments)
    direct = invoke("ncp_retained_read", {"artifact_id": sdk["artifact_id"]})
    assert received["status"] == sdk["status"] == "PASS"
    assert json.dumps(stable(received), sort_keys=True).count("markov_entropy_rate") >= 1
    assert stable(received).get("markov_entropy_rate_nats_per_symbol") == stable(sdk)["markov_entropy_rate_nats_per_symbol"]
