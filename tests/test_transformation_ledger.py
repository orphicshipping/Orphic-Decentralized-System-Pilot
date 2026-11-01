"""The ledger accounts for every tracked path and the services match the MCP tools."""
import json
import subprocess
from pathlib import Path

from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import TOOLS, ControlHost
from ciw.services import CONTRACTS, OPERATIONS, SESSION, invoke, open_demo
from ciw.transformation import document

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "mcp" / "transformation-ledger.json"
REPORT = ROOT / "mcp" / "verification-report.json"
CAPABILITIES = ROOT / "mcp" / "capability-map.json"


def _tracked():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [item.decode() for item in raw.split(b"\0") if item]


def test_every_tracked_path_has_one_disposition():
    paths = _tracked()
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    assert ledger["schema"] == "ncp.transformation-ledger.v1"
    assert ledger["authorizes_execution"] is False
    assert [row["path"] for row in ledger["files"]] == sorted(paths)
    assert document(paths)["files"] == ledger["files"]
    allowed = {
        "retained_and_verified", "rewritten", "relocated", "replaced", "removed",
        "generated", "vendor_exempt", "blocked",
    }
    assert {row["disposition"] for row in ledger["files"]} <= allowed
    assert all(row["reason"] and row["authorizes_execution"] is False for row in ledger["files"])
    assert ledger["counts"]["blocked"] > 0
    assert ledger["counts"]["vendor_exempt"] > 0


def test_services_and_mcp_call_the_same_functions(tmp_path):
    owner = from_profile(demo_config(tmp_path / "demo"))
    host = ControlHost(owner)
    try:
        for name in ("ncp_plug_in", "ncp_domain_list"):
            via_sdk = invoke(name)
            via_mcp = host.call(name, {})
            assert via_mcp["status"] == "ok"
            assert via_mcp["data"] == via_sdk
            assert via_sdk["authorizes_execution"] is False
        missing = host.call("ncp_domain_run", {"domain": "weather"})
        assert missing["status"] == "refused"
        try:
            invoke("ncp_domain_run", {"domain": "weather"})
        except ValueError as exc:
            assert "easiest" in str(exc)
        else:
            raise AssertionError("weather name")
        blocked = host.call("ncp_workbench_run", {"workflow": "julia-oscillator"})
        assert blocked["status"] == "refused"
        try:
            invoke("ncp_workbench_run", {"workflow": "julia-oscillator"})
        except ValueError as exc:
            assert "does not invent" in str(exc)
        else:
            raise AssertionError("julia")
        page = invoke("ncp_workbench_list")
        assert len(page["operations"]) == 29
        assert page["not_selected_by_default"] is True
        try:
            invoke("ncp_shell", {})
        except ValueError as exc:
            assert "not a shell" in str(exc)
        else:
            raise AssertionError("shell")
        try:
            invoke("ncp_plug_in", {"pin": "deadbeef"})
        except ValueError as exc:
            assert "cannot select" in str(exc)
        else:
            raise AssertionError("pin")
        atmosphere = invoke("ncp_domain_run", {"domain": "atmosphere"})
        assert atmosphere["authorizes_execution"] is False
        assert atmosphere["synthetic"] is True
        project = invoke("ncp_workbench_run", {"workflow": "project-graph"})
        assert project["authorizes_execution"] is False
        assert project["physical_qualification"] == "not_established"
        assert project["bundle_retained"] is False
        assert set(OPERATIONS) | set(SESSION) == set(TOOLS)
        doctor = invoke("ncp_doctor")
        via_doctor = host.call("ncp_doctor", {})
        assert via_doctor["status"] == "ok"
        assert doctor["authorizes_execution"] is False
        assert doctor["qualification"] == "not_performed"
        assert via_doctor["data"]["qualification"] == "not_performed"
        listed = {row["uri"] for row in host.list_resources()}
        assert listed == set(CONTRACTS)
        contract = host.read_resource("ncp://contract/plug-in")
        assert contract["schema"] == "ncp.mcp-plug-in.v1"
        assert contract["authorizes_execution"] is False
        try:
            invoke("ncp_plan_template", {"baseline": "baseline"})
        except ValueError as exc:
            assert "open_demo" in str(exc)
        else:
            raise AssertionError("session")
    finally:
        host.close()
        owner.close()
    session = open_demo(tmp_path / "sdk")
    try:
        template = invoke("ncp_plan_template", {"baseline": "baseline"}, host=session)
        assert "graph" in template
        assert template.get("authorizes_execution") is not True
    finally:
        session.close()
        session._service_owner.close()


def test_capability_map_matches_advertised_tools_and_the_report_stays_incomplete():
    mapped = json.loads(CAPABILITIES.read_text(encoding="utf-8"))
    assert mapped["authorizes_execution"] is False
    assert {row["name"] for row in mapped["tools"]} == set(TOOLS)
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["status"] == "INCOMPLETE"
    assert report["mcp_surface"] == "implemented"
    assert report["authorizes_execution"] is False
    assert {item["id"] for item in report["blocked"]} >= {"julia", "godot", "sp1-proof", "leakage-without-host-pin", "bim-without-host-pin"}
    assert "not an SP1 proof" in (ROOT / "README.md").read_text(encoding="utf-8")
