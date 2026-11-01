"""The ledger accounts for every tracked path and the services match the MCP tools."""
import base64
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
        assert project["bundle_retained"] is True
        saved = invoke("ncp_retained_read", {"artifact_id": project["artifact_id"]})
        assert saved["authorizes_execution"] is False
        raw = b""
        offset = 0
        while True:
            part = invoke("ncp_retained_read", {"artifact_id": project["artifact_id"], "offset": offset, "limit": 1024})
            raw += base64.b64decode(part["chunk_b64"])
            if part["next_offset"] is None:
                break
            offset = part["next_offset"]
        assert json.loads(raw)["bundle_digest"] == project["bundle_digest"]
        from ciw import retention
        previous = retention._OWNER
        retention._OWNER = "other-session"
        try:
            invoke("ncp_retained_read", {"artifact_id": project["artifact_id"]})
        except ValueError as exc:
            assert "another host session" in str(exc)
        else:
            raise AssertionError("cross-session read")
        finally:
            retention._OWNER = previous
        invoke("ncp_retained_release", {"artifact_id": project["artifact_id"]})
        try:
            invoke("ncp_retained_read", {"artifact_id": project["artifact_id"]})
        except ValueError as exc:
            assert "Unknown retained" in str(exc)
        else:
            raise AssertionError("released artifact")
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


def test_a_bad_host_checkout_is_refused_and_not_replaced(tmp_path, monkeypatch):
    root = tmp_path / "not-julia"
    root.mkdir()
    (root / "oscillator_worker.jl").write_text("not a worker\n", encoding="utf-8")
    (root / "Project.toml").write_text("name = \"not-julia\"\n", encoding="utf-8")
    (root / "Manifest.toml").write_text("not a manifest\n", encoding="utf-8")
    executable = tmp_path / "julia"
    executable.write_text("not julia\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "ncp.host-bindings.v1",
        "authorization": "operator",
        "workflows": {"julia-oscillator": {"roles": {
            "julia_runtime": str(root),
            "julia": str(executable),
        }}},
    }), encoding="utf-8")
    monkeypatch.setenv("NCP_HOST_MANIFEST", str(manifest))
    page = invoke("ncp_host_preflight")
    assert page["authorizes_execution"] is False
    assert page["workflows"][0]["workflow"] == "julia-oscillator"
    try:
        invoke("ncp_workbench_run", {"workflow": "julia-oscillator"})
    except ValueError as exc:
        text = str(exc)
        assert "does not invent" not in text
        assert "validated source" not in text
        assert "pinned Git checkout" in text
    else:
        raise AssertionError("bad checkout ran")
    relative = tmp_path / "relative.json"
    relative.write_text(json.dumps({
        "schema": "ncp.host-bindings.v1",
        "authorization": "operator",
        "workflows": {"julia-oscillator": {"roles": {"julia_runtime": "runtimes/julia-oscillator", "julia": "julia"}}},
    }), encoding="utf-8")
    monkeypatch.setenv("NCP_HOST_MANIFEST", str(relative))
    try:
        invoke("ncp_workbench_run", {"workflow": "julia-oscillator"})
    except ValueError as exc:
        assert "absolute" in str(exc)
    else:
        raise AssertionError("relative checkout")
    assert page["workflows"][0]["runtime_ready"] is False
    assert page["workflows"][0]["pin_checked"] is False
    assert page["workflows"][0]["readiness"] == "declared_only"
    try:
        invoke("ncp_workbench_run", {"workflow": "not-a-workflow"})
    except ValueError as exc:
        assert "declared set" in str(exc)
    else:
        raise AssertionError("enum")
    try:
        invoke("ncp_workbench_run", {"workflow": "project-graph", "source_b64": "a" * 40001})
    except ValueError as exc:
        assert "bounds" in str(exc)
    else:
        raise AssertionError("bounds")
    mapped = json.loads(CAPABILITIES.read_text(encoding="utf-8"))
    assert mapped["authorizes_execution"] is False
    assert {row["name"] for row in mapped["tools"]} == set(TOOLS)
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["status"] == "INCOMPLETE"
    assert report["mcp_surface"] == "implemented"
    assert report["authorizes_execution"] is False
    assert {item["id"] for item in report["blocked"]} >= {"julia", "godot", "sp1-proof", "leakage-without-host-pin", "bim-without-host-pin"}
    assert "not an SP1 proof" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_retention_evicts_the_oldest_and_refuses_a_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path))
    from ciw import retention
    catalog = {"schema": "ciw.retained-workbench.v1", "revision": 0, "sources": [], "bundles": []}
    ids = [retention.keep({"n": index, "bundle_digest": str(index)}, kind="project-graph", catalog=catalog)
           for index in range(33)]
    try:
        retention.native(ids[0])
    except ValueError as exc:
        assert "Unknown" in str(exc)
    else:
        raise AssertionError("oldest retained")
    assert retention.native(ids[-1])["n"] == 32
    outside = tmp_path / "secret.json"
    outside.write_text("{}\n", encoding="utf-8")
    link_id = "retained-" + ("ab" * 16)
    (tmp_path / (link_id + ".json")).symlink_to(outside)
    try:
        retention.native(link_id)
    except ValueError as exc:
        assert "Unknown" in str(exc)
    else:
        raise AssertionError("symlink")
