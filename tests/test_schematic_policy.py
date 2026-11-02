"""Pinned SRA execution and inert scientific fields under current principal grants."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import ciw
import pytest
from ciw.agent_api import encode
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.schematic_profile import SchematicHost, OPERATION, PIN, TOOLS, _receipt_digest
from ciw.services import invoke, capability_map
from test_schematic_profile import request


def policy_value():
    return {"schema": "ciw.principal-policy.v1", "revision": "schematic-fixture",
            "principals": [{"principal": "fixture-owner", "enabled": True,
                "operations": [{"name": name, "constraints": {}} for name in TOOLS],
                "scientific_operations": [OPERATION], "resources": [],
                "reference_scopes": [], "handoff_recipients": []}]}


@pytest.fixture(scope="module")
def checkout():
    raw = os.environ.get("CIW_SRA_CHECKOUT")
    if not raw:
        pytest.skip("CIW_SRA_CHECKOUT must supply the approved SRA pin for qualification")
    path = Path(raw).absolute()
    assert subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip() == PIN
    return path


@pytest.fixture
def bound(checkout):
    with SchematicHost(checkout=checkout, python=sys.executable, principal="fixture-owner") as host:
        value = policy_value()
        policy = PrincipalPolicy(lambda: value)
        yield host, value, policy, policy.facade("fixture-owner", schematic=host)


def certificate_request():
    value = request()
    value["schematic"]["nodes"].append({"id": "declared-certificate", "kind": "certificate",
        "attrs": {"owner": "jspt", "result": "SAMPLED", "A": [[-1.0]]}})
    value["schematic"]["edges"].append({"kind": "linearizes", "src": "declared-certificate", "dst": "f", "attrs": {}})
    return value


def test_shared_sra_policy_accepts_native_owner_and_frozen_configuration(bound, monkeypatch):
    host, value, policy, facade = bound
    calls = []
    original = host._invoke
    def observed(name, arguments):
        calls.append(name)
        return original(name, arguments)
    monkeypatch.setattr(host, "_invoke", observed)
    source = certificate_request()
    validated = invoke("ncp_schematic_validate", {"schematic": source["schematic"]}, host=facade)
    assert validated["outcome"] == "validated"
    assert invoke("ncp_schematic_retrieve", {"schematic": source["schematic"], "queries": source["queries"]}, host=facade)["outcome"] == "retrieved"
    saved = invoke("ncp_schematic_assess", {"request": source}, host=facade)["receipt"]
    verified = invoke("ncp_schematic_verify_receipt", {"receipt": saved}, host=facade)
    assert verified["outcome"] == "verified" and verified["numerical_match"] is True
    assert verified["reproduction_receipt"]["bundle"]["session_id"] != saved["bundle"]["session_id"]
    assert saved["authority"]["certificate_authentication"] == "not_performed"
    count = len(calls)
    value["principals"][0]["scientific_operations"] = []
    for active in (facade, policy.facade("fixture-owner", schematic=host)):
        for name, arguments in (("ncp_schematic_validate", {"schematic": source["schematic"]}),
                ("ncp_schematic_retrieve", {"schematic": source["schematic"], "queries": source["queries"]}),
                ("ncp_schematic_assess", {"request": source}),
                ("ncp_schematic_verify_receipt", {"receipt": saved})):
            with pytest.raises(PrincipalRefusal):
                invoke(name, arguments, host=active)
    assert len(calls) == count
    assert invoke("ncp_schematic_describe", {}, host=facade)["authorizes_execution"] is False
    value["principals"][0]["operations"] = []
    assert facade.descriptions() == []
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_schematic_describe", {}, host=facade)


@pytest.mark.parametrize("where,key,value", [
    ("request", "principal", "evil"), ("request", "configuration", {}),
    ("meta", "owner", "jspt"), ("meta", "endpoint", "https://example.invalid"),
    ("variable", "owner", "jspt"), ("certificate", "owner", "evil"),
    ("certificate", "grants", []), ("certificate", "configuration", {}),
])
def test_inert_sra_keys_do_not_allow_identity_or_policy_overrides(bound, monkeypatch, where, key, value):
    host, _, _, facade = bound
    source = certificate_request()
    target = {"request": source, "meta": source["schematic"]["meta"],
              "variable": source["schematic"]["nodes"][0]["attrs"],
              "certificate": source["schematic"]["nodes"][-1]["attrs"]}[where]
    target[key] = value
    calls = []
    monkeypatch.setattr(host, "call", lambda *args: calls.append(args))
    with pytest.raises(PrincipalRefusal):
        invoke("ncp_schematic_assess", {"request": source}, host=facade)
    assert calls == []


def test_sra_receipt_exceptions_are_exact_and_identity_stays_host_bound(bound, monkeypatch):
    host, value, _, facade = bound
    saved = invoke("ncp_schematic_assess", {"request": certificate_request()}, host=facade)["receipt"]
    calls = []
    monkeypatch.setattr(host, "call", lambda *args: calls.append(args))
    for path, key, replacement in ((["bundle"], "configuration", {}),
            (["bundle", "steps", 0, "request"], "configuration", {"companion_execution": "allowed"}),
            (["bundle", "verification", "reproduction", "request"], "configuration", {}),
            (["bundle", "steps", 0, "result", "data", "decisions", 0], "owner", "other-principal"),
            (["bundle", "runtimes", "sra"], "owner", "jspt"),
            (["authority"], "principal", "evil")):
        altered = deepcopy(saved)
        target = altered
        for part in path:
            target = target[part]
        target[key] = replacement
        altered["receipt_digest"] = _receipt_digest(altered)
        with pytest.raises(PrincipalRefusal):
            invoke("ncp_schematic_verify_receipt", {"receipt": altered}, host=facade)
    assert not calls
    value["principals"][0]["principal"] = "different-owner"
    with pytest.raises(PrincipalRefusal):
        PrincipalPolicy(lambda: value).facade("different-owner", schematic=host)


def test_sra_profile_discovery_and_direct_sdk_preserve_non_authority(bound):
    host, _, _, _ = bound
    row, = [row for row in capability_map()["bounded_computation_profiles"] if row["profile_id"] == "sra.schematic.v1"]
    assert row["runtime_commit"] == PIN and row["scientific_operation"] == OPERATION
    assert row["authorizes_execution"] is False
    assert invoke("ncp_schematic_describe", {}, host=host) == row
    with pytest.raises(ValueError, match="host_required"):
        invoke("ncp_schematic_describe", {})


def test_actual_sra_principal_mcp_roundtrip_restart_and_revocation(checkout, tmp_path):
    pytest.importorskip("mcp", reason="Official MCP transport is required for qualification")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    value = policy_value()
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(value))
    launcher = tmp_path / "serve.py"
    launcher.write_text("import sys\nsys.path.insert(0," + repr(str(Path(ciw.__file__).resolve().parents[1])) + ")\n"
        "from ciw.principal_policy import PrincipalPolicy\nfrom ciw.schematic_profile import SchematicHost\n"
        "from ciw.mcp_server import serve_sdk\n"
        "with SchematicHost(checkout=" + repr(str(checkout)) + ",python=sys.executable,principal='fixture-owner') as host:\n"
        " policy=PrincipalPolicy.from_file(" + repr(str(policy_path)) + ")\n"
        " serve_sdk(policy.facade('fixture-owner',schematic=host),name='sra-policy-fixture')\n")
    async def run():
        saved = None
        for restart in range(2):
            params = StdioServerParameters(command=sys.executable, args=["-I", "-B", str(launcher)], cwd=str(tmp_path))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == 5
                    if restart == 0:
                        response = await client.call_tool("ncp_schematic_assess", {"request": certificate_request()})
                        assert not response.is_error
                        saved = response.structured_content["data"]["receipt"]
                    response = await client.call_tool("ncp_schematic_verify_receipt", {"receipt": saved})
                    assert not response.is_error and response.structured_content["data"]["numerical_match"] is True
                    assert len(encode(response.model_dump(by_alias=True))) <= 262144
                    if restart == 1:
                        value["principals"][0]["scientific_operations"] = []
                        policy_path.write_text(json.dumps(value))
                        response = await client.call_tool("ncp_schematic_verify_receipt", {"receipt": saved})
                        assert response.is_error and response.structured_content["error"]["code"] == "principal_policy_refused"
                        value["principals"][0]["enabled"] = False
                        policy_path.write_text(json.dumps(value))
                        response = await client.call_tool("ncp_schematic_describe", {})
                        assert response.is_error
    asyncio.run(run())
