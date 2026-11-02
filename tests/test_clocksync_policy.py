"""Actual pinned clock arithmetic behind current principal SDK/MCP grants."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import ciw
import pytest
from ciw.clocksync_profile import ClockSyncHost, OPERATION, PIN, PROFILE, TOOLS
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import capability_map, invoke
from test_clocksync_profile import INPUT, reseal


def policy_value():
    return {"schema": "ciw.principal-policy.v1", "revision": "clocksync-fixture",
            "principals": [{"principal": "fixture-owner", "enabled": True,
                "operations": [{"name": name, "constraints": {}} for name in TOOLS],
                "scientific_operations": [OPERATION], "resources": [],
                "reference_scopes": [], "handoff_recipients": []}]}


@pytest.fixture(scope="module")
def checkout():
    selected = os.environ.get("CIW_CLOCKSYNC_CHECKOUT")
    if not selected:
        pytest.skip("CIW_CLOCKSYNC_CHECKOUT must select the approved 40507060 runtime")
    path = Path(selected).absolute()
    assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == PIN
    return path


@pytest.fixture
def bound(checkout):
    with ClockSyncHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner") as host:
        yield host


def test_current_identity_tool_and_science_grants_before_kernel(bound, monkeypatch):
    value = policy_value()
    policy = PrincipalPolicy(lambda: value)
    wrong = deepcopy(value)
    wrong["principals"][0]["principal"] = "other-owner"
    with pytest.raises(PrincipalRefusal):
        PrincipalPolicy(lambda: wrong).facade("other-owner", clocksync=bound)
    facade = policy.facade("fixture-owner", clocksync=bound)
    calls = []
    original = bound._kernel
    def observed(*args, **kwargs):
        calls.append(deepcopy(args))
        return original(*args, **kwargs)
    monkeypatch.setattr(bound, "_kernel", observed)
    assert invoke("ncp_clocksync_describe", {}, host=facade)["runtime"]["revision"] == PIN
    assert calls == []
    result = invoke("ncp_clocksync_reconcile", {"request": INPUT}, host=facade)
    assert result["numerical_result"]["event_time_delta"] == 6.5
    assert result["numerical_result"]["variance"] == 22.75
    assert invoke("ncp_clocksync_replay", {"receipt": result}, host=facade)["outcome"] == "verified"
    assert len(calls) == 2
    value["principals"][0]["scientific_operations"] = []
    for active in (facade, policy.facade("fixture-owner", clocksync=bound)):
        for name, args in (("ncp_clocksync_reconcile", {"request": INPUT}),
                           ("ncp_clocksync_replay", {"receipt": result})):
            with pytest.raises(PrincipalRefusal):
                invoke(name, args, host=active)
        assert invoke("ncp_clocksync_describe", {}, host=active)["authority"]["authorizes_execution"] is False
    assert len(calls) == 2
    value["principals"][0]["operations"] = []
    assert facade.descriptions() == []
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_clocksync_describe", {}, host=facade)
    assert len(calls) == 2


def test_nested_reference_constraint_and_reduced_grants(bound, monkeypatch):
    value = policy_value()
    rules = {row["name"]: row for row in value["principals"][0]["operations"]}
    rules["ncp_clocksync_reconcile"]["constraints"] = {"request.expected_reference.clock_id": ["fixture:reference"]}
    facade = PrincipalPolicy(lambda: value).facade("fixture-owner", clocksync=bound)
    result = invoke("ncp_clocksync_reconcile", {"request": INPUT}, host=facade)
    assert result["outcome"] == "reconciled"
    monkeypatch.setattr(bound, "_kernel", lambda *a, **k: pytest.fail("denied scope reached kernel"))
    request = deepcopy(INPUT)
    request["expected_reference"] = {**request["expected_reference"], "clock_id": "other-reference"}
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_clocksync_reconcile", {"request": request}, host=facade)
    rules["ncp_clocksync_reconcile"]["constraints"]["request.expected_reference.clock_id"] = ["different-grant"]
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_clocksync_reconcile", {"request": INPUT}, host=facade)


@pytest.mark.parametrize("key", ["principal", "owner", "checkout", "python_executable", "pin",
    "provider_revision", "endpoint", "token", "configuration", "policy"])
def test_caller_cannot_rebind_nested_host_configuration(bound, monkeypatch, key):
    facade = PrincipalPolicy(policy_value).facade("fixture-owner", clocksync=bound)
    monkeypatch.setattr(bound, "_kernel", lambda *a, **k: pytest.fail("host override reached kernel"))
    request = {**deepcopy(INPUT), key: "caller-choice"}
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_clocksync_reconcile", {"request": request}, host=facade)


def test_shared_replay_rejects_complete_rehashed_science_tamper(bound):
    facade = PrincipalPolicy(policy_value).facade("fixture-owner", clocksync=bound)
    original = invoke("ncp_clocksync_reconcile", {"request": INPUT}, host=facade)
    changed = deepcopy(original)
    changed["numerical_result"]["event_time_delta"] = 7.0
    reseal(changed)
    replay = invoke("ncp_clocksync_replay", {"receipt": changed}, host=facade)
    assert replay["outcome"] == "mismatch" and replay["canonical_receipt_match"] is False
    assert replay["request_digest_match"] and replay["numerical_digest_match"] and replay["receipt_digest_match"]
    assert invoke("ncp_clocksync_replay", {"receipt": original}, host=facade)["outcome"] == "verified"


def test_request_budget_and_disabled_principal_refuse_before_host(bound, monkeypatch):
    value = policy_value()
    facade = PrincipalPolicy(lambda: value).facade("fixture-owner", clocksync=bound)
    monkeypatch.setattr(bound, "call", lambda *a, **k: pytest.fail("denied request reached host"))
    request = {**deepcopy(INPUT), "execution_id": "\u2603" * 20000}
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_clocksync_reconcile", {"request": request}, host=facade)
    value["principals"][0]["enabled"] = False
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_clocksync_describe", {}, host=facade)


def test_discovery_does_not_bind_or_register_a_default_engine(monkeypatch):
    monkeypatch.setattr(ClockSyncHost, "__init__", lambda *a, **k: pytest.fail("discovery created a runtime binding"))
    row, = [row for row in capability_map()["bounded_computation_profiles"] if row["profile_id"] == PROFILE]
    assert row["adapter_contract_commit"] == PIN and row["operation_id"] == OPERATION
    assert row["authorizes_execution"] is row["authorizes_state_admission"] is False
    assert {tool["name"] for tool in row["tools"]} == set(TOOLS)
    from ciw.control_gateway import TOOLS as CONTROL_TOOLS
    assert set(TOOLS).isdisjoint(CONTROL_TOOLS)
    with pytest.raises(ValueError, match="clocksync_host_required"):
        invoke("ncp_clocksync_describe", {})
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_clocksync_describe", {}, host=PrincipalPolicy(policy_value).facade("fixture-owner"))


def test_real_principal_mcp_replay_revocation_and_restart(checkout, tmp_path):
    pytest.importorskip("mcp", reason="Official MCP transport is a required qualification prerequisite")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    policy_path = tmp_path / "policy.json"
    value = policy_value()
    policy_path.write_text(json.dumps(value))
    launcher = tmp_path / "serve.py"
    launcher.write_text("import sys\nsys.path.insert(0," + repr(str(Path(ciw.__file__).resolve().parents[1])) + ")\n"
        "from ciw.principal_policy import PrincipalPolicy\nfrom ciw.clocksync_profile import ClockSyncHost\n"
        "from ciw.mcp_server import serve_sdk\n"
        "with ClockSyncHost(checkout=" + repr(str(checkout)) + ",python_executable=sys.executable,principal='fixture-owner') as host:\n"
        " policy=PrincipalPolicy.from_file(" + repr(str(policy_path)) + ")\n"
        " serve_sdk(policy.facade('fixture-owner',clocksync=host),name='clock-policy-fixture')\n")
    async def run():
        receipt = None
        for restart in range(2):
            params = StdioServerParameters(command=sys.executable, args=["-I", "-B", str(launcher)], cwd=str(tmp_path))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    assert {t.name for t in (await client.list_tools()).tools} == set(TOOLS)
                    description = await client.call_tool("ncp_clocksync_describe", {})
                    assert description.structured_content["data"]["operation_id"] == OPERATION
                    if restart == 0:
                        result = await client.call_tool("ncp_clocksync_reconcile", {"request": INPUT})
                        assert not result.is_error
                        receipt = result.structured_content["data"]
                        replay = await client.call_tool("ncp_clocksync_replay", {"receipt": receipt})
                        assert replay.structured_content["data"]["outcome"] == "verified"
                        value["principals"][0]["scientific_operations"] = []
                        policy_path.write_text(json.dumps(value))
                    for name, args in (("ncp_clocksync_reconcile", {"request": INPUT}),
                                       ("ncp_clocksync_replay", {"receipt": receipt})):
                        refused = await client.call_tool(name, args)
                        assert refused.is_error and refused.structured_content["error"]["code"] == "principal_policy_refused"
    asyncio.run(run())
