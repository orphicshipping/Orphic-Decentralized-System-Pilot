"""Direct and coordinator routes require the same explicit scientific grants."""
import asyncio
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import ciw
import pytest
from ciw import coordinator, services
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal, owner_for
from ciw.sequence_kinetics import example_request

TOOLS = ("ncp_domain_run", "ncp_workbench_run", "ncp_coordinate", "ncp_fluid_field",
         "ncp_demo", "ncp_linear_preview", "ncp_lesson", "ncp_hypergraph",
         "ncp_polymer_cycle", "ncp_toolpoint")


def policy_value():
    return {"schema": "ciw.principal-policy.v1", "revision": "direct-science-fixture",
            "principals": [{"principal": "fixture-owner", "enabled": True,
                "operations": [{"name": name, "constraints": {}} for name in TOOLS],
                "scientific_operations": [], "resources": [], "reference_scopes": [],
                "handoff_recipients": []}]}


@pytest.fixture
def bound(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retained"))
    value = policy_value()
    policy = PrincipalPolicy(lambda: value)
    profile = demo_config(tmp_path / "profile")
    with from_profile(profile) as owner, ControlHost(owner, retained_owner=owner_for("fixture-owner")) as control:
        facade = policy.facade("fixture-owner", control=control)
        yield value, policy, control, facade, tmp_path


def no_retained(tmp_path):
    assert not list((tmp_path / "retained").rglob("retained-*.json"))
    assert not list((tmp_path / "retained").rglob("failure-*.json"))
    assert not list((tmp_path / "retained").rglob("job-*.json"))


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("example", [False, True])
def test_domain_science_denial_precedes_direct_or_nested_execution(bound, monkeypatch, nested, example):
    _, policy, _, facade, root = bound
    arguments = {"domain": "sequence", **({"example": True} if example else {"request": example_request()})}
    name = "ncp_domain_run"
    if nested:
        name, arguments = "ncp_coordinate", {"action": "submit", "operation": name,
            "arguments": arguments, "idempotency_key": "denied-fixture"}
    calls = []
    original = services.domain_run
    def observed(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(services, "domain_run", observed)
    before = coordinator._EXECUTIONS
    # The same decision is available before an HTTP adapter admits a transaction.
    with pytest.raises(PrincipalRefusal):
        policy.authorize("fixture-owner", name, arguments)
    response = facade.call(name, arguments)
    assert response["error"]["code"] == "principal_policy_refused"
    assert calls == [] and coordinator._EXECUTIONS == before
    no_retained(root)


@pytest.mark.parametrize("supplied_source", [False, True])
def test_workbench_science_denial_precedes_binding_or_retention(bound, monkeypatch, supplied_source):
    from ciw.workbench_surface import _project_bytes
    _, _, _, facade, root = bound
    arguments = {"workflow": "project-graph"}
    if supplied_source:
        arguments["source_b64"] = base64.b64encode(_project_bytes()).decode()
    calls = []
    original = services.workbench_run
    def observed(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(services, "workbench_run", observed)
    assert facade.call("ncp_workbench_run", arguments)["error"]["code"] == "principal_policy_refused"
    assert calls == []
    no_retained(root)


def test_authorized_domain_example_request_and_nested_replay_preserve_results(bound):
    value, _, control, facade, _ = bound
    value["principals"][0]["scientific_operations"] = ["sequence.markov.v1"]
    for arguments, example in (({"domain": "sequence", "example": True}, True),
                               ({"domain": "sequence", "request": example_request()}, False)):
        result = services.invoke("ncp_domain_run", arguments, host=facade)
        assert result["example"] is example and result["synthetic"] is True
        assert result["authorizes_execution"] is False and control.retained.native(result["artifact_id"])
    args = {"action": "submit", "operation": "ncp_domain_run", "arguments": {"domain": "sequence", "example": True},
            "idempotency_key": "authorized-fixture"}
    before = coordinator._EXECUTIONS
    first = services.invoke("ncp_coordinate", args, host=facade)
    again = services.invoke("ncp_coordinate", args, host=facade)
    assert first["status"] == "completed" and again["replayed"] is True
    assert first["artifact_id"] == again["artifact_id"] and coordinator._EXECUTIONS == before + 1
    value["principals"][0]["scientific_operations"] = []
    with pytest.raises(PrincipalRefusal):
        services.invoke("ncp_coordinate", args, host=facade)
    assert coordinator._EXECUTIONS == before + 1


def test_authorized_workbench_uses_exact_published_operation_and_fixture_label(bound):
    value, _, control, facade, _ = bound
    value["principals"][0]["scientific_operations"] = ["ciw.project-graph.v1"]
    result = services.invoke("ncp_workbench_run", {"workflow": "project-graph"}, host=facade)
    assert result["operation_id"] == "ciw.project-graph.v1" and result["synthetic_fixture"] is True
    assert result["authorizes_execution"] is False and control.retained.native(result["artifact_id"])
    value["principals"][0]["scientific_operations"] = ["ciw.encoder-position.v1"]
    assert facade.call("ncp_workbench_run", {"workflow": "project-graph"})["error"]["code"] == "principal_policy_refused"


def test_workbench_catalog_and_named_domain_selectors_are_authorized_exactly():
    from ciw.domain_surface import OPERATIONS as DOMAIN
    from ciw.workbench import OPERATIONS as WORKBENCH
    value = policy_value();policy = PrincipalPolicy(lambda: value)
    for workflow, operation in WORKBENCH.items():
        value["principals"][0]["scientific_operations"] = [operation]
        policy.authorize("fixture-owner", "ncp_workbench_run", {"workflow": workflow})
        value["principals"][0]["scientific_operations"] = []
        with pytest.raises(PrincipalRefusal):
            policy.authorize("fixture-owner", "ncp_workbench_run", {"workflow": workflow})
    assert WORKBENCH["machine-manifest"] == "ciw.encoder-position.v1"
    for operation, domain, name, _ in DOMAIN:
        arguments = {"domain": domain, "example": True, **({"name": name} if name else {})}
        value["principals"][0]["scientific_operations"] = [operation]
        policy.authorize("fixture-owner", "ncp_domain_run", arguments)
        value["principals"][0]["scientific_operations"] = ["not-the-selected-operation"]
        with pytest.raises(PrincipalRefusal):
            policy.authorize("fixture-owner", "ncp_domain_run", arguments)


@pytest.mark.parametrize("name,args,grants", [
    ("ncp_fluid_field", {"solver": "wave"}, ["fluid.wave.simulate.v1", "fluid.wave.verify.v1"]),
    ("ncp_fluid_field", {"solver": "sph"}, ["fluid.sph.simulate.v1", "fluid.sph.verify.v1"]),
    ("ncp_demo", {}, ["synthetic-oscillator-demo"]),
    ("ncp_linear_preview", {"model": "ignored-before-dispatch", "base_point": "1", "delta": "1"}, ["linear-response.library"]),
    ("ncp_lesson", {"mode": "work"}, ["statistics.v1"]),
    ("ncp_hypergraph", {}, ["hypergraph_rewriting"]),
    ("ncp_polymer_cycle", {}, ["data.assess-polymer-cycle.v1"]),
    ("ncp_toolpoint", {}, ["data.review-toolpoint-containment.v1"]),
])
def test_other_direct_computation_wrappers_require_their_existing_identity(bound, monkeypatch, name, args, grants):
    value, policy, control, facade, root = bound
    calls = []
    monkeypatch.setattr(control, "call", lambda *call: calls.append(call))
    assert facade.call(name, args)["error"]["code"] == "principal_policy_refused"
    assert calls == []
    for removed in grants:
        value["principals"][0]["scientific_operations"] = [grant for grant in grants if grant != removed]
        with pytest.raises(PrincipalRefusal):
            policy.authorize("fixture-owner", name, args)
    value["principals"][0]["scientific_operations"] = grants
    policy.authorize("fixture-owner", name, args)
    no_retained(root)


def test_lesson_read_and_real_fluid_positive_keep_separate_permissions(bound):
    value, _, _, facade, _ = bound
    assert services.invoke("ncp_lesson", {"mode": "learn"}, host=facade)["mode"] == "learn"
    value["principals"][0]["scientific_operations"] = ["fluid.wave.simulate.v1", "fluid.wave.verify.v1"]
    result = services.invoke("ncp_fluid_field", {"solver": "wave"}, host=facade)
    assert result["profile"] == "wave" and result["authorizes_execution"] is False


def test_actual_mcp_direct_and_nested_grants_reduce_across_reconnect(tmp_path, monkeypatch):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    retained = tmp_path / "retained"
    monkeypatch.setenv("NCP_RETAIN_DIR", str(retained))
    profile = demo_config(tmp_path / "profile")
    value = policy_value();policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(value))
    env = {**os.environ, "PYTHONPATH": str(Path(ciw.__file__).resolve().parents[1]), "PYTHONDONTWRITEBYTECODE": "1"}
    installed = Path(ciw.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    calls = [("ncp_domain_run", {"domain": "sequence", "example": True}),
             ("ncp_workbench_run", {"workflow": "project-graph"}),
             ("ncp_coordinate", {"action": "submit", "operation": "ncp_domain_run",
                  "arguments": {"domain": "sequence", "example": True}, "idempotency_key": "mcp-fixture"})]
    async def run():
        for restart in range(2):
            args = (["-I", "-B"] if installed else ["-B"]) + ["-m", "ciw.principal_mcp", "--profile", str(profile),
                    "--policy", str(policy_path), "--principal", "fixture-owner"] + (["--resume"] if restart else [])
            async with stdio_client(StdioServerParameters(command=sys.executable,args=args,env=env,cwd=str(tmp_path))) as streams:
                async with ClientSession(*streams, read_timeout_seconds=20) as client:
                    await client.initialize()
                    for name, arguments in calls:
                        refused = await client.call_tool(name, arguments)
                        assert refused.is_error and refused.structured_content["error"]["code"] == "principal_policy_refused"
                    if restart == 0:
                        no_retained(tmp_path)
                        value["principals"][0]["scientific_operations"] = ["sequence.markov.v1", "ciw.project-graph.v1"]
                        policy_path.write_text(json.dumps(value))
                        for name, arguments in calls:
                            response = await client.call_tool(name, arguments)
                            assert not response.is_error, response
                        value["principals"][0]["scientific_operations"] = []
                        policy_path.write_text(json.dumps(value))
                        for name, arguments in calls:
                            refused = await client.call_tool(name, arguments)
                            assert refused.is_error and refused.structured_content["error"]["code"] == "principal_policy_refused"
    asyncio.run(run())
