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
            invoke("ncp_domain_run", {"domain": "atmosphere"})
        except ValueError as exc:
            assert "example" in str(exc)
        else:
            raise AssertionError("silent example")
        try:
            invoke("ncp_domain_run", {"domain": "weather", "example": True})
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
        atmosphere = invoke("ncp_domain_run", {"domain": "atmosphere", "example": True})
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
        assert part["digest"] == "sha256:" + __import__("hashlib").sha256(raw).hexdigest()
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
    (retention._owner_dir(retention._OWNER) / (link_id + ".json")).symlink_to(outside)
    try:
        retention.native(link_id)
    except ValueError as exc:
        assert "Unknown" in str(exc)
    else:
        raise AssertionError("symlink")
    other = retention.Store("other-host")
    try:
        other.native(ids[-1])
    except ValueError as exc:
        assert "another host session" in str(exc)
    else:
        raise AssertionError("other host")
    first = retention.Store("restart-host")
    kept = first.keep({"n": 1, "bundle_digest": "one"}, kind="project-graph", catalog=catalog)
    second = retention.Store("restart-host")
    assert second.native(kept)["n"] == 1
    again = second.keep({"n": 2, "bundle_digest": "two"}, kind="project-graph", catalog=catalog)
    assert second.native(again)["n"] == 2
    sequence = (retention._owner_dir("restart-host") / "sequence").read_text(encoding="utf-8")
    assert sequence.strip() == "2"


def test_catalog_union_rejects_conflicts_and_cycles():
    from ciw.workbench_surface import compose_catalogs
    shared = {"schema": "ciw.retained-workbench.v1", "sources": [{"source_id": "s", "n": 1}],
              "bundles": [{"bundle_id": "b", "upstream_bundle_id": None}]}
    composed = compose_catalogs([shared, shared])
    assert len(composed["sources"]) == 1 and len(composed["bundles"]) == 1
    conflict = {"schema": "ciw.retained-workbench.v1", "sources": [{"source_id": "s", "n": 2}],
                "bundles": [{"bundle_id": "b", "upstream_bundle_id": None}]}
    try:
        compose_catalogs([shared, conflict])
    except ValueError as exc:
        assert "conflict" in str(exc)
    else:
        raise AssertionError("conflict")
    cycle = {"schema": "ciw.retained-workbench.v1", "sources": [{"source_id": "s", "n": 1}], "bundles": [
        {"bundle_id": "a", "upstream_bundle_id": "b"},
        {"bundle_id": "b", "upstream_bundle_id": "a"},
    ]}
    try:
        compose_catalogs([cycle])
    except ValueError as exc:
        assert "cycle" in str(exc)
    else:
        raise AssertionError("cycle")


def test_atmosphere_accepts_a_caller_request_and_refuses_a_bad_one():
    from copy import deepcopy
    from ciw.atmosphere_contract import example_request
    from ciw.services import invoke
    request = deepcopy(example_request())
    request["profile"]["wind_enu_m_per_s"] = [1.0, 0.0, 0.0]
    body = invoke("ncp_domain_run", {"domain": "atmosphere", "request": request})
    assert body["example"] is False
    assert body["authorizes_execution"] is False
    assert body["bundle_retained"] is True
    try:
        invoke("ncp_domain_run", {"domain": "atmosphere", "request": {"schema": "not-a-request"}})
    except ValueError as exc:
        assert "contract" in str(exc).lower() or "schema" in str(exc).lower()
    else:
        raise AssertionError("bad atmosphere request")


def test_hosts_do_not_evict_each_other_and_a_shared_catalog_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retain"))
    from ciw.agent_mcp import demo_config, from_profile
    from ciw.control_gateway import ControlHost
    from ciw.retention import Store
    from ciw.workbench import Workbench
    from ciw.workbench_surface import compose_catalogs
    left = ControlHost(from_profile(demo_config(tmp_path / "left")))
    right = ControlHost(from_profile(demo_config(tmp_path / "right")))
    try:
        body = left.workbench_run("project-graph")
        try:
            right.retained.native(body["artifact_id"])
        except ValueError as exc:
            assert "another host session" in str(exc)
        else:
            raise AssertionError("cross host read")
        catalog = {"schema": "ciw.retained-workbench.v1", "revision": 0, "sources": [], "bundles": []}
        for index in range(40):
            right.retained.keep({"n": index, "bundle_digest": str(index)}, kind="project-graph", catalog=catalog)
        assert left.retained.native(body["artifact_id"])["bundle_digest"] == body["bundle_digest"]
        saved = left.retained.catalog(body["artifact_id"])
        restored = Workbench.restore(compose_catalogs([saved, saved]))
        assert len(restored.list_bundles()) == 1
    finally:
        left.close()
        right.close()
        left.owner.close()
        right.owner.close()


def test_remaining_domains_accept_a_caller_request():
    from copy import deepcopy
    from ciw.geomatics_workflow import example as geomatics_example
    from ciw.sequence_kinetics import example_request as sequence_example
    from ciw.services import invoke
    from ciw.thermofluids_models import example_request as thermo_example
    geo = invoke("ncp_domain_run", {"domain": "geomatics", "name": "geomatics.atmosphere.transmission.v1",
                                    "request": geomatics_example("geomatics.atmosphere.transmission.v1")})
    assert geo["example"] is False and geo["field_retained"] is True
    thermo = deepcopy(thermo_example("convection"))
    thermo["parameters"]["bulk_temperature_k"] = 309.0
    body = invoke("ncp_domain_run", {"domain": "thermofluids", "name": "convection", "request": thermo})
    assert body["example"] is False and body["data_retained"] is True
    sequence = invoke("ncp_domain_run", {"domain": "sequence", "request": sequence_example()})
    assert sequence["example"] is False and sequence["artifact_id"].startswith("retained-")


def test_installed_package_answers_outside_the_checkout(tmp_path):
    import os
    import subprocess
    import sys
    target = tmp_path / "site"
    env = os.environ.copy()
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", str(ROOT), "--target", str(target), "--no-deps", "--no-build-isolation", "--ignore-requires-python"],
            cwd="/tmp",
            env=env,
            stdout=subprocess.DEVNULL,
        )
        env["PYTHONPATH"] = str(target)
        env.pop("PYTHONSAFEPATH", None)
        contracts = subprocess.check_output(
            ["/usr/bin/python3.11", "-c",
             "from ciw.plug_in import document; from ciw.services import read_contract, ledger_read; "
             "print(document()['schema']); "
             "print(read_contract('ncp://contract/verification')['schema']); "
             "print(ledger_read('public_core')['record']['id'])"],
            cwd="/tmp",
            env=env,
        )
        assert contracts.splitlines() == [b"ncp.mcp-plug-in.v1", b"ncp.verification-report.v1", b"public_core"]
        invoked = subprocess.check_output(
            [sys.executable, "-c", "from ciw.services import invoke; print(invoke('ncp_plug_in')['schema'])"],
            cwd="/tmp",
            env=env,
        )
        assert invoked.strip() == b"ncp.mcp-plug-in.v1"
    finally:
        import shutil
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        shutil.rmtree(ROOT / "src" / "computational_instrumentation_workbench.egg-info", ignore_errors=True)
        shutil.rmtree(ROOT / "computational_instrumentation_workbench.egg-info", ignore_errors=True)


def test_public_calls_keep_the_native_bundle_on_the_calling_host(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retain"))
    from copy import deepcopy
    from ciw.agent_mcp import demo_config, from_profile
    from ciw.control_gateway import ControlHost
    from ciw.retention import RetainedFailure
    from ciw.services import invoke
    from ciw.weather_contract import example_request
    left = ControlHost(from_profile(demo_config(tmp_path / "left")))
    right = ControlHost(from_profile(demo_config(tmp_path / "right")))
    try:
        atmosphere = invoke("ncp_domain_run", {"domain": "atmosphere", "example": True}, host=left)
        saved = left.retained.native(atmosphere["artifact_id"])
        assert "height_m" in saved["profile"]
        assert saved["request"]["sampling"]["height_m"] == saved["profile"]["height_m"]
        assert "request.json" in saved["files"]
        try:
            invoke("ncp_retained_read", {"artifact_id": atmosphere["artifact_id"]}, host=right)
        except ValueError as exc:
            assert "another host session" in str(exc)
        else:
            raise AssertionError("shared owner")
        mismatched = deepcopy(example_request("radiation"))
        mismatched["profile"] = "thermodynamics"
        try:
            invoke("ncp_domain_run", {"domain": "weather", "name": "radiation", "request": mismatched}, host=left)
        except ValueError as exc:
            assert "weather_profile_mismatch" in str(exc)
            assert "not executed" in str(exc)
        else:
            raise AssertionError("weather name")
        refused = left.call("ncp_workbench_run", {"workflow": "julia-oscillator"})
        assert refused["status"] == "refused"
        failure_id = refused["error"]["artifact_id"]
        failure = left.retained.native(failure_id)
        assert failure["workflow"] == "julia-oscillator"
        assert "workspace" in failure
    finally:
        left.close()
        right.close()
        left.owner.close()
        right.owner.close()


def test_coordinator_replays_hands_off_and_refuses_a_busy_resource(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retain"))
    import json
    import os
    from ciw.agent_mcp import demo_config, from_profile
    from ciw.control_gateway import ControlHost
    from ciw.coordinator import _exclusive_path
    from ciw.retention import SUPPORTED_PLATFORMS
    from ciw.sequence_kinetics import example_request
    from ciw.services import invoke
    from ciw.workbench_surface import _read_bounded
    assert os.name in SUPPORTED_PLATFORMS
    oversized = tmp_path / "wide.bin"
    oversized.write_bytes(b"x" * 70000)
    try:
        _read_bounded(oversized, 65536)
    except ValueError as exc:
        assert "budget" in str(exc)
    else:
        raise AssertionError("bounded read")
    left = ControlHost(from_profile(demo_config(tmp_path / "left")))
    right = ControlHost(from_profile(demo_config(tmp_path / "right")))
    try:
        payload = {"action": "submit", "idempotency_key": "job-1", "operation": "ncp_domain_run",
                   "arguments": {"domain": "sequence", "request": example_request()}}
        first = invoke("ncp_coordinate", payload, host=left)
        second = invoke("ncp_coordinate", payload, host=left)
        assert first["replayed"] is False and second["replayed"] is True
        assert second["executions"] == 1 and second["artifact_id"] == first["artifact_id"]
        copied = invoke("ncp_coordinate", {"action": "handoff", "artifact_id": first["artifact_id"],
                                           "recipient": right.retained.owner}, host=left)
        claimed = invoke("ncp_coordinate", {"action": "claim", "grant_id": copied["grant_id"]}, host=right)
        assert right.retained.native(claimed["artifact_id"])["schema"]
        try:
            invoke("ncp_coordinate", {"action": "claim", "grant_id": copied["grant_id"]}, host=left)
        except ValueError as exc:
            assert "handoff_refused" in str(exc)
        else:
            raise AssertionError("owner claim")
        try:
            right.retained.native(first["artifact_id"])
        except ValueError as exc:
            assert "another host session" in str(exc)
        else:
            raise AssertionError("handoff bypass")
        held = _exclusive_path("oven")
        held.write_text(json.dumps({"pid": os.getpid(), "owner": left.retained.owner}), encoding="utf-8")
        try:
            invoke("ncp_coordinate", {**payload, "idempotency_key": "job-2", "exclusive": "oven"}, host=left)
        except Exception as exc:
            assert "exclusive_resource_busy" in str(exc)
            assert left.retained.native(exc.artifact_id)["error"] == "exclusive_resource_busy"
        finally:
            held.unlink(missing_ok=True)
        try:
            invoke("ncp_coordinate", {"action": "submit", "idempotency_key": "job-3", "operation": "ncp_domain_run",
                                      "arguments": {"domain": "sequence", "agent_id": "other"}}, host=left)
        except ValueError as exc:
            assert "impersonation" in str(exc)
        else:
            raise AssertionError("impersonation")
    finally:
        left.close()
        right.close()
        left.owner.close()
        right.owner.close()


def test_same_key_is_admitted_once_and_a_dead_worker_is_recovered(tmp_path, monkeypatch):
    monkeypatch.setenv("NCP_RETAIN_DIR", str(tmp_path / "retain"))
    import threading
    from ciw.agent_mcp import demo_config, from_profile
    from ciw.control_gateway import ControlHost
    from ciw.coordinator import _digest, _idem_name, _store_job
    from ciw import coordinator
    from ciw.sequence_kinetics import example_request
    from ciw.services import invoke
    left = ControlHost(from_profile(demo_config(tmp_path / "left")))
    try:
        request = example_request()
        payload = {"action": "submit", "idempotency_key": "job-race", "operation": "ncp_domain_run",
                   "arguments": {"domain": "sequence", "request": request}}
        coordinator._EXECUTIONS = 0
        barrier = threading.Barrier(2)
        errors = []

        def work():
            try:
                barrier.wait()
                invoke("ncp_coordinate", payload, host=left)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work), threading.Thread(target=work)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert coordinator._EXECUTIONS == 1
        try:
            invoke("ncp_coordinate", {"action": "submit", "idempotency_key": "job-race", "operation": "ncp_domain_run",
                                      "arguments": {"domain": "sequence", "example": True}}, host=left)
        except ValueError as exc:
            assert "idempotency_key_conflict" in str(exc)
        else:
            raise AssertionError("digest")
        digest = _digest("ncp_domain_run", {"domain": "sequence", "example": True}, None)
        dead = {"schema": "ncp.coordinator-job.v1", "operation": "ncp_domain_run", "executions": 1,
                "status": "running", "pid": 2**30, "request_digest": digest, "authorizes_execution": False}
        _store_job(left.retained, _idem_name("job-dead"), dead)
        recovered = invoke("ncp_coordinate", {"action": "recall", "idempotency_key": "job-dead"}, host=left)
        assert recovered["status"] == "failed" and recovered["error"] == "worker_lost"
        assert left.retained.native(recovered["artifact_id"])["error"] == "worker_lost"
        again = invoke("ncp_coordinate", {"action": "submit", "idempotency_key": "job-dead", "operation": "ncp_domain_run",
                                          "arguments": {"domain": "sequence", "example": True}}, host=left)
        assert again["status"] == "failed" and again["replayed"] is True
    finally:
        left.close()
        left.owner.close()
