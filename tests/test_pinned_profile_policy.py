"""Actual pinned profile execution behind current shared SDK/MCP policy."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import ciw
import pytest
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.recompiler_profile import RecompilerHost, OPERATION, PIN, TOOLS
from ciw.services import invoke, capability_map
from test_recompiler_profile import INPUT


def policy_value():
    return {"schema": "ciw.principal-policy.v1", "revision": "pinned-fixture",
            "principals": [{"principal": "fixture-owner", "enabled": True,
                "operations": [{"name": name, "constraints": {}} for name in TOOLS],
                "scientific_operations": [OPERATION], "resources": [],
                "reference_scopes": [], "handoff_recipients": []}]}


@pytest.fixture
def checkout():
    raw = os.environ.get("CIW_NSR_CHECKOUT")
    if not raw:
        pytest.skip("CIW_NSR_CHECKOUT must provide the approved NSR pin for this qualification")
    path = Path(raw).absolute()
    assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == PIN
    return path


def test_nsr_shared_policy_checks_identity_tool_and_science_before_kernel(checkout, monkeypatch):
    host = RecompilerHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner")
    value = policy_value()
    policy = PrincipalPolicy(lambda: value)
    calls = []
    original = host._kernel
    def observed(request):
        calls.append(deepcopy(request))
        return original(request)
    monkeypatch.setattr(host, "_kernel", observed)
    wrong = deepcopy(value)
    wrong["principals"][0]["principal"] = "another-owner"
    with pytest.raises(PrincipalRefusal):
        PrincipalPolicy(lambda: wrong).facade("another-owner", recompiler=host)
    facade = policy.facade("fixture-owner", recompiler=host)
    described = invoke("ncp_recompiler_describe", {}, host=facade)
    assert described["runtime"]["revision"] == PIN and not calls
    request = {"request": deepcopy(INPUT)}
    result = invoke("ncp_recompiler_reconcile", request, host=facade)
    assert result["outcome"] == "accepted" and len(calls) == 1
    verified = invoke("ncp_recompiler_verify_receipt", {"receipt": result["receipt"]}, host=facade)
    assert verified["outcome"] == "verified" and len(calls) == 2
    value["principals"][0]["scientific_operations"] = []
    for active in (facade, policy.facade("fixture-owner", recompiler=host)):
        for name, args in (("ncp_recompiler_reconcile", request),
                           ("ncp_recompiler_verify_receipt", {"receipt": result["receipt"]})):
            with pytest.raises(PrincipalRefusal):
                invoke(name, args, host=active)
    assert len(calls) == 2
    assert invoke("ncp_recompiler_describe", {}, host=facade)["authorizes_execution"] is False
    value["principals"][0]["operations"] = []
    assert facade.descriptions() == []
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_recompiler_describe", {}, host=facade)
    assert len(calls) == 2


@pytest.mark.parametrize("mutation,outcome", [
    ({}, "accepted"), ({"max_normalized_residual": .1}, "held"),
    ({"crosscov_policy": "unknown"}, "refused"), ({"unsupported": True}, "invalid_contract")])
def test_nsr_shared_dispatch_preserves_scientific_outcomes(checkout, mutation, outcome):
    host = RecompilerHost(checkout=checkout, python_executable=sys.executable, principal="fixture-owner")
    facade = PrincipalPolicy(policy_value).facade("fixture-owner", recompiler=host)
    arguments = {"request": {**deepcopy(INPUT), **mutation}}
    assert invoke("ncp_recompiler_reconcile", arguments, host=facade)["outcome"] == outcome
    assert invoke("ncp_recompiler_reconcile", arguments, host=host)["outcome"] == outcome


def test_nsr_profile_is_discoverable_without_creating_a_binding():
    manifests = capability_map()["bounded_computation_profiles"]
    row, = [row for row in manifests if row["profile_id"] == "nsr.affine-exact.v1"]
    assert row["adapter_contract_commit"] == PIN and row["operation_id"] == OPERATION
    assert row["authorizes_execution"] is row["authorizes_state_admission"] is False
    with pytest.raises(ValueError, match="host_required"):
        invoke("ncp_recompiler_describe", {})


def test_nsr_actual_principal_mcp_rechecks_grants_across_restart(checkout, tmp_path):
    pytest.importorskip("mcp", reason="Official MCP transport is a required qualification prerequisite")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    policy_path = tmp_path / "policy.json"
    value = policy_value()
    policy_path.write_text(json.dumps(value))
    launcher = tmp_path / "serve.py"
    launcher.write_text("import sys\nsys.path.insert(0," + repr(str(Path(ciw.__file__).resolve().parents[1])) + ")\n"
        "from ciw.principal_policy import PrincipalPolicy\nfrom ciw.recompiler_profile import RecompilerHost\n"
        "from ciw.mcp_server import serve_sdk\n"
        "host=RecompilerHost(checkout=" + repr(str(checkout)) + ",python_executable=sys.executable,principal='fixture-owner')\n"
        "policy=PrincipalPolicy.from_file(" + repr(str(policy_path)) + ")\n"
        "serve_sdk(policy.facade('fixture-owner',recompiler=host),name='nsr-policy-fixture')\n")
    async def run():
        for restart in range(2):
            parameters = StdioServerParameters(command=sys.executable, args=["-I", "-B", str(launcher)], cwd=str(tmp_path))
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    described = await client.call_tool("ncp_recompiler_describe", {})
                    assert described.structured_content["data"]["operation_id"] == OPERATION
                    result = await client.call_tool("ncp_recompiler_reconcile", {"request": INPUT})
                    if restart == 0:
                        assert result.structured_content["data"]["outcome"] == "accepted"
                        value["principals"][0]["scientific_operations"] = []
                        policy_path.write_text(json.dumps(value))
                        result = await client.call_tool("ncp_recompiler_reconcile", {"request": INPUT})
                    assert result.is_error and result.structured_content["error"]["code"] == "principal_policy_refused"
    asyncio.run(run())
