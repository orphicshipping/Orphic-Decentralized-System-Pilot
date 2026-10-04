"""Measured thermal MCP binds exact recordings and preserves scientific scope."""
from copy import deepcopy
import base64
from pathlib import Path

import pytest

from ciw import measured_thermal_agent as adapter
from ciw import measured_thermal_workflow as workflow
from ciw.agent_api import encode, parse
from ciw.agent_mcp import from_profile, main, measured_thermal_config
from ciw.core.identities import evidence_id
from ciw.operations.runner import seal
from ciw.session import Session

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "measured-thermal" / "source.json"


def _profile(tmp_path):
    return measured_thermal_config(tmp_path / "profile", FIXTURE)


def _execute(host, attempt="first"):
    return host.call("net_execute", {"source": "source", "graph": "baseline", "attempt": attempt})


def test_profile_creation_preserves_exact_recording_without_execution(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Creating a profile executed the thermal workflow")

    monkeypatch.setattr(workflow.MeasuredThermalWorkflow, "create_session", forbidden)
    raw = FIXTURE.read_bytes() + b"\n  "
    supplied = tmp_path / "supplied.json"
    supplied.write_bytes(raw)
    profile = measured_thermal_config(tmp_path / "profile", supplied)
    assert (profile.parent / "recording.json").read_bytes() == raw
    assert adapter.source_bytes(parse((profile.parent / "source.json").read_bytes())) == raw
    assert not (profile.parent / "agent-output").exists()
    with pytest.raises(FileExistsError):
        measured_thermal_config(profile.parent, supplied)


def test_measured_profile_retains_scope_exact_bytes_and_fresh_replay(tmp_path):
    profile = _profile(tmp_path)
    with from_profile(profile, instrument="measured-thermal") as host:
        catalog = host.capabilities()["catalog"]
        assert set(catalog["operations"]) == {adapter.ASSESS}
        assert catalog["authorizes_execution"] is False
        first = _execute(host)
        assert first["status"] == "completed", first
        workspace = parse((profile.parent / "agent-output" / "first" / "workspace.json").read_bytes())
        result, = workspace["results"]
        payload = result["data"]
        bundle = payload["bundle"]
        assert result["operation_id"] == adapter.ASSESS
        assert payload["authority"] == workflow.AUTHORITY
        assert payload["source_origin"] == "test_fixture"
        assert payload["correction_status"] == adapter.CORRECTION_STATUS
        assert base64.b64decode(bundle["source"]["evidence"][0]["bytes_b64"]) == FIXTURE.read_bytes()
        data = bundle["steps"][0]["result"]["data"]
        assert all(row[0] is None for row in data["request"]["observations"])
        assert data["evaluation"]["uncertainty_coverage"] == "not_established"
        assert data["evaluation"]["calibration_authenticity"] == "not_established"
        assert data["observer"]["trace"][0]["posterior_covariance"]
        assert bundle["verification"]["independent"] is False
        inspected = host.call("net_inspect", {"artifact": first["artifacts"]["workspace"]["artifact_id"],
            "selector": ["results", result["result_id"], "data", "bundle", "steps", 0, "result", "data", "evaluation"]})
        assert inspected["data"] == data["evaluation"]
        assert _execute(host)["execution_ids"] == first["execution_ids"]
        replay = host.call("net_replay", {"original_attempt": "first", "new_attempt": "second"})
        assert replay["status"] == "completed"
        assert set(replay["execution_ids"]).isdisjoint(first["execution_ids"])
        repeated = parse((profile.parent / "agent-output" / "second" / "workspace.json").read_bytes())
        fresh = repeated["results"][0]["data"]["bundle"]
        assert fresh["steps"][0]["execution_id"] != bundle["steps"][0]["execution_id"]
        assert fresh["steps"][0]["result_id"] != bundle["steps"][0]["result_id"]
        assert fresh["steps"][0]["numerical_result_id"] == bundle["steps"][0]["numerical_result_id"]


def test_profile_requires_explicit_instrument_and_grant(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    with pytest.raises(ValueError, match="not advertised"):
        from_profile(profile)
    value = parse(profile.read_bytes())
    value["allow_operations"] = []
    profile.write_bytes(encode(value))
    monkeypatch.setattr(workflow.MeasuredThermalWorkflow, "create_session",
                        lambda *args: pytest.fail("Read-only host executed a workflow"))
    with from_profile(profile, instrument="measured-thermal") as host:
        with pytest.raises(ValueError, match="not enabled"):
            _execute(host)
        assert host.capabilities()["execution_budget"]["used"] == 0


@pytest.mark.parametrize("mutation", ["graph_parameter", "node_parameter", "candidate_domain"])
def test_thermal_profile_refuses_agent_model_or_reference_overrides(tmp_path, mutation):
    profile = _profile(tmp_path)
    if mutation == "candidate_domain":
        value = parse(profile.read_bytes())
        value["candidate_domains"] = {"baseline": {"assessment": {}}}
        profile.write_bytes(encode(value))
    else:
        path = profile.parent / "experiment.json"
        graph = parse(path.read_bytes())
        target = graph if mutation == "graph_parameter" else graph["nodes"][0]
        target["parameters"] = {"core_reference": [310.0]}
        path.write_bytes(encode(seal(graph)))
    with pytest.raises(ValueError, match="parameters|domains|bound source"):
        from_profile(profile, instrument="measured-thermal")
    assert not (profile.parent / "agent-output").exists()


def test_source_refuses_transport_changes_even_after_resealing(tmp_path):
    profile = _profile(tmp_path)
    path = profile.parent / "source.json"
    run = parse(path.read_bytes())
    run["channels"]["recording_declaration"]["values"] = [2.0]
    run["evidence_id"] = evidence_id(run)
    path.write_bytes(encode(run))
    with pytest.raises(ValueError, match="exact source evidence"):
        from_profile(profile, instrument="measured-thermal")


def test_workspace_readback_preserves_result_and_refuses_authority_tamper(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    with from_profile(profile, instrument="measured-thermal") as host:
        assert _execute(host)["status"] == "completed"
    path = profile.parent / "agent-output" / "first" / "workspace.json"
    monkeypatch.setattr(workflow.MeasuredThermalWorkflow, "create_session",
                        lambda *args: pytest.fail("Readback created a scientific execution"))
    restored = Session.from_workspace(path, output_dir=tmp_path / "reader")
    assert len(restored.results) == 1
    value = parse(path.read_bytes())
    result = value["results"][0]
    result["data"]["authority"]["physical_validation"] = "established"
    seal(result)
    path.write_bytes(encode(value))
    with pytest.raises(ValueError, match="authority"):
        Session.from_workspace(path, output_dir=tmp_path / "bad-reader")


def test_profile_cli_requires_real_source_argument_and_rejects_missing_calibration(tmp_path, capsys):
    destination = tmp_path / "profile"
    assert main(["measured-thermal-config", "--source", str(FIXTURE), "--output-dir", str(destination)]) == 0
    assert Path(capsys.readouterr().out.strip()) == destination / "profile.json"
    invalid = parse(FIXTURE.read_bytes())
    del invalid["channels"]["shell"]["calibration_ref"]
    bad = tmp_path / "invalid.json"
    bad.write_bytes(encode(invalid))
    assert main(["measured-thermal-config", "--source", str(bad), "--output-dir", str(tmp_path / "bad")]) == 1
    assert not (tmp_path / "bad").exists()


def test_exact_mcp_source_byte_limit_executes_and_retains_without_truncation(tmp_path):
    raw = FIXTURE.read_bytes()
    raw += b" " * (adapter.SOURCE_LIMIT - len(raw))
    source = tmp_path / "at-limit.json"
    source.write_bytes(raw)
    profile = measured_thermal_config(tmp_path / "profile", source)
    with from_profile(profile, instrument="measured-thermal") as host:
        response = _execute(host)
        assert response["status"] == "completed", response
        workspace = parse((profile.parent / "agent-output" / "first" / "workspace.json").read_bytes())
        retained = workspace["results"][0]["data"]["bundle"]["source"]["evidence"][0]["bytes_b64"]
        assert len(retained) == 65536
        assert base64.b64decode(retained) == raw


def test_one_byte_over_mcp_source_limit_refuses_before_destination_creation(tmp_path):
    raw = FIXTURE.read_bytes()
    raw += b" " * (adapter.SOURCE_LIMIT + 1 - len(raw))
    # The source remains valid for the native workflow's distinct 1 MiB ingress.
    from ciw.measured_thermal import validate_source
    validate_source(raw)
    source = tmp_path / "over-limit.json"
    source.write_bytes(raw)
    with pytest.raises(ValueError, match="49152 bytes"):
        measured_thermal_config(tmp_path / "profile", source)
    assert not (tmp_path / "profile").exists()
    with pytest.raises(ValueError, match="49152 bytes"):
        adapter.make_source(raw)


@pytest.mark.parametrize("extract", [False, True])
def test_resealed_graph_model_mismatch_refuses_before_dispatch(tmp_path, monkeypatch, extract):
    profile = _profile(tmp_path)
    path = profile.parent / "experiment.json"
    graph = parse(path.read_bytes())
    graph["model_id"] = "sha256:" + "0" * 64
    path.write_bytes(encode(seal(graph)))
    monkeypatch.setattr(workflow.MeasuredThermalWorkflow, "create_session",
                        lambda *args: pytest.fail("Mismatched model dispatched thermal inference"))
    with from_profile(profile, instrument="measured-thermal") as host:
        selected = "baseline"
        if extract:
            selected = host.call("net_extract", {"artifact": selected, "selector": []})["artifact"]["artifact_id"]
        with pytest.raises(ValueError, match="graph model differs"):
            host.call("net_execute", {"source": "source", "graph": selected, "attempt": "mismatch"})
        assert host.capabilities()["execution_budget"]["used"] == 0
        assert not (profile.parent / "agent-output" / "mismatch").exists()


def test_mixed_bound_recordings_cannot_pair_with_another_models_graph(tmp_path, monkeypatch):
    from ciw.control_contracts import bytes_ref
    from ciw.core.identities import content_identity
    from ciw.telemetry import canonical

    profile = _profile(tmp_path)
    different = parse(FIXTURE.read_bytes())
    different["request"]["model"]["capacities_j_per_k"][0] *= 2
    model_id = content_identity(different["request"]["model"])
    for name, ref in list(different["series"].items()):
        item = next(item for item in different["artifacts"] if item["sha256"] == ref)
        stream = parse(base64.b64decode(item["bytes_b64"]))
        for row in stream["observations"]:
            row["identity"]["model_id"] = model_id
            seal(row)
        raw = canonical(seal(stream))
        item.update(sha256=bytes_ref(raw), bytes_b64=base64.b64encode(raw).decode("ascii"))
        different["series"][name] = item["sha256"]
    other = adapter.make_source(canonical(different))
    (profile.parent / "other.json").write_bytes(encode(other))
    value = parse(profile.read_bytes())
    value["inputs"]["other"] = "other.json"
    profile.write_bytes(encode(value))
    monkeypatch.setattr(workflow.MeasuredThermalWorkflow, "create_session",
                        lambda *args: pytest.fail("Mixed model/source pair dispatched thermal inference"))
    with from_profile(profile, instrument="measured-thermal") as host:
        with pytest.raises(ValueError, match="graph model differs"):
            host.call("net_execute", {"source": "other", "graph": "baseline", "attempt": "mixed"})
        assert host.capabilities()["execution_budget"]["used"] == 0
