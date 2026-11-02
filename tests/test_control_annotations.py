"""Discovery describes effects; retry hints never grant execution authority."""
import asyncio
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import sys

import ciw
import pytest

from ciw import retention
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost, TOOLS


# Public effect contract, independent of the tool factory's defaults. A new
# control tool must be classified rather than inheriting a misleading hint.
EFFECT_GROUPS = {
    (True, False, True): {
        "ncp_integrations_list", "ncp_integration_describe", "ncp_workflows_list",
        "ncp_capabilities_list", "ncp_index_list", "ncp_doctor", "ncp_demo",
        "ncp_linear_preview", "ncp_domain_list", "ncp_workbench_list",
        "ncp_retained_read", "ncp_host_preflight", "ncp_plug_in", "ncp_ledger_read",
        "ncp_capability_describe", "ncp_plan_template", "ncp_plan_get",
        "ncp_run_get", "ncp_artifact_read", "ncp_observe",
    },
    (False, False, False): {
        "ncp_lesson", "ncp_hypergraph", "ncp_fluid_field", "ncp_polymer_cycle",
        "ncp_toolpoint",
    },
    (False, False, True): {
        "ncp_plan_create", "ncp_plan_admit", "ncp_run_submit", "ncp_compare",
        "ncp_qualify",
    },
    (False, True, True): {"ncp_retained_release", "ncp_run_cancel"},
    (False, True, False): {
        "ncp_domain_run", "ncp_workbench_run", "ncp_coordinate",
    },
}
EXPECTED = {
    name: {"readOnlyHint": read, "destructiveHint": destructive,
           "idempotentHint": idempotent, "openWorldHint": False}
    for (read, destructive, idempotent), names in EFFECT_GROUPS.items()
    for name in names
}


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retained"))
    with from_profile(demo_config(tmp_path / "demo")) as owner:
        with ControlHost(owner) as control:
            yield control


def call(host, name, **arguments):
    response = host.call(name, arguments)
    assert response["status"] == "ok", response
    return response["data"]


def assert_annotations(rows):
    assert [row["name"] for row in rows] == sorted(EXPECTED)
    for row in rows:
        assert row["annotations"] == EXPECTED[row["name"]], row["name"]


def test_discovery_classifies_every_control_tool(host):
    assert_annotations(host.descriptions())


def test_python_and_packaged_discovery_preserve_every_annotation():
    from ciw.services import _packaged_text, capability_map

    for catalog in (capability_map(), json.loads(_packaged_text("capability-map.json"))):
        assert_annotations(catalog["tools"])
        assert catalog["authorizes_execution"] is False
        for row in catalog["tools"]:
            assert row["read_only"] is row["annotations"]["readOnlyHint"]
            assert row["authorizes_execution"] is False


def test_content_addressed_writes_reuse_records_and_refuse_capacity(host, monkeypatch):
    from ciw import agent_api

    graph = call(host, "ncp_plan_template", baseline="baseline")["graph"]
    arguments = {"source": "source", "baseline": "baseline", "graph": graph}
    draft = call(host, "ncp_plan_create", **arguments)
    admission = {"plan_id": draft["plan_id"], "expected_digest": draft["digest"]}
    admitted = call(host, "ncp_plan_admit", **admission)
    assert call(host, "ncp_plan_create", **arguments) == admitted
    assert call(host, "ncp_plan_admit", **admission) == admitted
    assert len(host._plans) == 1

    comparison = call(host, "ncp_compare", left="reference", right="reference", policy="strict")
    inputs = {"difference": comparison["comparison"]["artifact_id"]}
    report = call(host, "ncp_qualify", suite="regression", inputs=inputs)
    generated = set(host.owner._generated)
    assert call(host, "ncp_compare", left="reference", right="reference", policy="strict") == comparison
    assert call(host, "ncp_qualify", suite="regression", inputs=inputs) == report
    assert host.owner._generated == generated
    retained = {key: host.owner._artifacts[key] for key in generated}
    monkeypatch.setattr(agent_api, "MAX_ARTIFACTS", len(host.owner._artifacts))
    # Missing evidence produces a different report, requiring another slot.
    assert host.call("ncp_qualify", {"suite": "regression", "inputs": {}})["status"] == "refused"
    assert host.owner._generated == generated
    assert {key: host.owner._artifacts[key] for key in generated} == retained
    assert host.owner.capabilities()["execution_budget"]["used"] == 0


def test_repeated_retained_work_can_evict_previous_evidence(host, monkeypatch):
    # Legacy/demo hosts may evict; static discovery also serves private hosts
    # that refuse capacity instead. The shared hint must cover both policies.
    monkeypatch.setattr(retention, "_LIMIT", 1)
    first = call(host, "ncp_domain_run", domain="sequence", example=True)["artifact_id"]
    before = host.retained._read(first)
    view = call(host, "ncp_retained_read", artifact_id=first)
    assert call(host, "ncp_retained_read", artifact_id=first) == view
    assert host.retained._read(first) == before
    second = call(host, "ncp_domain_run", domain="sequence", example=True)["artifact_id"]
    assert second != first
    assert host.retained.native(second) == before["native"]
    assert host.call("ncp_retained_read", {"artifact_id": first})["status"] == "refused"


def test_release_retry_has_no_additional_effect_and_cannot_delete_another_owner(host):
    artifact = host.retained.keep({"owned": True}, kind="fixture", catalog={})
    foreign = retention.Store("different-owner")
    other = foreign.keep({"owned": False}, kind="fixture", catalog={})
    assert host.call("ncp_retained_release", {"artifact_id": other})["status"] == "refused"
    assert foreign.native(other) == {"owned": False}
    assert call(host, "ncp_retained_release", artifact_id=artifact)["released"] is True
    assert host.call("ncp_retained_release", {"artifact_id": artifact})["status"] == "refused"
    assert not host.retained._path(artifact).exists()
    assert foreign.native(other) == {"owned": False}


def test_annotations_do_not_authorize_an_ungranted_operation(host, monkeypatch):
    from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal

    policy = PrincipalPolicy(lambda: {
        "schema": "ciw.principal-policy.v1", "revision": "annotation-test",
        "principals": [{"principal": "reader", "enabled": True,
                        "operations": [], "scientific_operations": [],
                        "resources": [], "reference_scopes": [], "handoff_recipients": []}],
    })
    # Even maximally reassuring hints do not supply an operation grant.
    monkeypatch.setitem(TOOLS["ncp_retained_release"], "annotations", {
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    })
    artifact = host.retained.keep({"preserved": True}, kind="fixture", catalog={})
    with pytest.raises(PrincipalRefusal):
        policy.authorize("reader", "ncp_retained_release", {"artifact_id": artifact})
    assert host.retained.native(artifact) == {"preserved": True}


@pytest.mark.parametrize("modern", [True, False])
def test_official_sdk_discovery_preserves_every_annotation(tmp_path, modern):
    try:
        sdk = version("mcp")
    except PackageNotFoundError:
        sdk = None
    if sdk != "2.3.0":
        pytest.skip("requires optional pinned MCP SDK 2.3.0")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    profile = demo_config(tmp_path / "profile")
    env = dict(os.environ, PYTHONPATH=str(Path(ciw.__file__).resolve().parent.parent),
               NCP_RETAIN_DIR=str(tmp_path / "retained"), PYTHONDONTWRITEBYTECODE="1")

    async def discover():
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "ciw.net", "control", "serve", "--profile", str(profile)],
            env=env, cwd=str(tmp_path))
        with (tmp_path / "stderr.log").open("w", encoding="utf-8") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=30) as client:
                    await (client.discover() if modern else client.initialize())
                    listing = await client.list_tools()
                    assert_annotations([tool.model_dump(by_alias=True, exclude_none=True)
                                        for tool in listing.tools])

    asyncio.run(discover())
