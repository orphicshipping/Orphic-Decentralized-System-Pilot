"""Published recording ingress stays passive and content-bound in Session/CLI."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import urllib.request

import pytest

from ciw.control_contracts import load
from ciw.core.identities import evidence_id
from ciw.instruments import validate_run
from ciw.operations.runner import seal
from ciw.scientific_cli import main
from ciw.session import Session
from ciw.temperature_recording import import_recording, observation_stream
from ciw.temperature_recording_session import make_source, source_recording

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "temperature-recording"


def recording():
    return import_recording((EXAMPLE / "data.csv").read_bytes(), load(EXAMPLE / "source.json"))


def import_args(output):
    return ["import-temperature", "--csv", str(EXAMPLE / "data.csv"),
            "--manifest", str(EXAMPLE / "source.json"), "--output-dir", str(output), "--json"]


def test_public_recording_is_retained_without_manufacturing_results(tmp_path):
    value = recording()
    source = make_source(value)
    session = Session(source, tmp_path)
    assert source_recording(source) == value
    assert source["time_s"] == [row["time_s"] for row in value["rows"]]
    assert source["metadata"]["sample_rate_hz"] is None
    assert source["channels"]["Q1"]["unit"] == "%"
    assert source["channels"]["Q1"]["semantics"] == "recorded_command"
    assert source["channels"]["T1"]["unit"] == "degC"
    assert session.executions == session.results == {}
    assert source["metadata"]["temperature_recording"]["recording_context"]["acquisition_execution_id"] is None


def test_reopen_and_export_use_retained_bytes_only(tmp_path, monkeypatch):
    session = Session(make_source(recording()), tmp_path / "initial")
    workspace = session.save_workspace(tmp_path / "initial" / "workspace.json")
    before = workspace.read_bytes()

    def unexpected(*args, **kwargs):
        pytest.fail("Retained reopen/export attempted device, network, provider or process execution")

    monkeypatch.setattr(socket, "create_connection", unexpected)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr("ciw.session.execute_operation", unexpected)
    reopened = Session.from_workspace(workspace, output_dir=tmp_path / "reopened")
    assert workspace.read_bytes() == before
    assert reopened.run == session.run
    assert reopened.session_id != session.session_id  # New viewing session, unchanged acquisition evidence.
    assert reopened.executions == reopened.results == {}
    exported = observation_stream(source_recording(reopened.run), "T1")
    assert len(exported["observations"]) == 201
    assert all(row["identity"]["execution_id"] is None for row in exported["observations"])
    assert all(row["uncertainty"] is None for row in exported["observations"])


@pytest.mark.parametrize("mutation", ["temperature", "command_unit", "time", "origin", "authority", "strip_recording", "renamed_instrument"])
def test_resealed_source_cannot_detach_channels_or_change_authority(mutation):
    source = make_source(recording())
    if mutation == "renamed_instrument":
        source["instrument"] = source["metadata"]["manifest"]["instrument_id"] = "renamed-temperature-recording.v1"
        source["channels"]["T1"]["values"][0] = 100.0
    elif mutation == "temperature":
        source["channels"]["T1"]["values"][1] += 1
    elif mutation == "command_unit":
        source["channels"]["Q1"]["unit"] = "W"
        source["metadata"]["manifest"]["units"]["Q1"] = "W"
    elif mutation == "time":
        source["time_s"][1] += 0.01
    elif mutation == "origin":
        source["metadata"]["provenance"]["source"] = "live_acquisition"
    elif mutation == "authority":
        bundle = source["metadata"]["temperature_recording"]
        bundle["authority"]["physical_validation"] = "established"
        seal(bundle)
    else:
        del source["metadata"]["temperature_recording"]
    source["evidence_id"] = evidence_id(source)
    with pytest.raises(ValueError):
        validate_run(source)


def test_reimport_is_another_session_not_another_physical_acquisition(tmp_path):
    first = Session(make_source(recording()), tmp_path / "first")
    second = Session(make_source(recording()), tmp_path / "second")
    assert first.session_id != second.session_id
    assert first.run == second.run
    assert first.run["metadata"]["temperature_recording"]["authority"]["new_physical_acquisition"] == "not_performed"


def test_cli_import_inspect_export_and_create_only(tmp_path, capsys):
    output = tmp_path / "imported"
    assert main(import_args(output)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["row_count"] == 201
    assert summary["physical_qualification"]["status"] == "incomplete"
    retained = load(output / "recording.json")
    workspace_before = (output / "workspace.json").read_bytes()
    assert main(["inspect-temperature", "--recording", str(output / "recording.json"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["record_digest"] == retained["record_digest"]
    stream = tmp_path / "temperatures.json"
    assert main(["temperature-observations", "--recording", str(output / "recording.json"),
                 "--channel", "T2", "--output", str(stream), "--json"]) == 0
    capsys.readouterr()
    assert load(stream) == observation_stream(retained, "T2")
    assert main(import_args(output)) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert (output / "workspace.json").read_bytes() == workspace_before


def test_cli_invalid_source_writes_nothing(tmp_path, capsys):
    manifest = load(EXAMPLE / "source.json")
    manifest["temperature_unit"] = "K"
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "must-not-exist"
    args = import_args(output)
    args[args.index("--manifest") + 1] = str(invalid)
    assert main(args) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert not output.exists()


def test_cli_oversize_input_writes_nothing(tmp_path, capsys):
    from ciw.temperature_recording import MAX_BYTES
    raw = tmp_path / "large.csv"
    raw.write_bytes(b"x" * (MAX_BYTES + 1))
    output = tmp_path / "must-not-exist"
    args = import_args(output)
    args[args.index("--csv") + 1] = str(raw)
    assert main(args) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert not output.exists()


def test_sample_index_cannot_be_relabelled_seconds():
    manifest = load(EXAMPLE / "source.json")
    manifest["origin"] = "test_fixture"
    manifest["clock"]["time_kind"] = "nominal_sample_index"
    with pytest.raises(ValueError, match="sample indices"):
        import_recording((EXAMPLE / "data.csv").read_bytes(), manifest)


def test_cli_numeric_refusal_identifies_source_cell_before_creating_output(tmp_path, capsys):
    from ciw.control_contracts import bytes_ref

    raw = b"Time (sec), Temperature 1, Temperature 2, Heater 1, Heater 2\n0,1e-999,21,0,0\n"
    manifest = load(EXAMPLE / "source.json")
    manifest.update(origin="test_fixture", raw_sha256=bytes_ref(raw))
    csv_path, manifest_path = tmp_path / "input.csv", tmp_path / "source.json"
    csv_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "refused-import"
    assert main(["import-temperature", "--csv", str(csv_path), "--manifest", str(manifest_path),
                 "--output-dir", str(output), "--json"]) == 1
    captured = capsys.readouterr()
    refusal = json.loads(captured.err)
    assert captured.out == ""
    assert refusal["status"] == "refused"
    assert "CSV record 2, column T1" in refusal["reason"]
    assert "silent conversion to zero" in refusal["reason"]
    assert not output.exists()
    assert csv_path.read_bytes() == raw


def test_single_sample_with_missing_temperature_survives_session_reopen(tmp_path):
    from ciw.control_contracts import bytes_ref

    raw = b"Time (sec), Temperature 1, Temperature 2, Heater 1, Heater 2\n0,,21,0,0\n"
    manifest = load(EXAMPLE / "source.json")
    manifest.update(origin="test_fixture", raw_sha256=bytes_ref(raw))
    retained = import_recording(raw, manifest)
    session = Session(make_source(retained), tmp_path / "initial-single-sample")
    workspace = session.save_workspace(session.output_dir / "workspace.json")
    reopened = Session.from_workspace(workspace, output_dir=tmp_path / "reopened-single-sample")
    exported = observation_stream(source_recording(reopened.run), "T1")
    assert len(exported["observations"]) == 1
    assert exported["observations"][0]["value"] is None
    assert exported["observations"][0]["clock"]["time_s"] == 0.0
    assert exported["observations"][0]["provenance"]["semantics"] == "simulated"
    assert reopened.executions == reopened.results == {}


def test_cli_unserializable_selection_endpoint_refuses_before_output_creation(tmp_path, capsys):
    from ciw.control_contracts import bytes_ref

    raw = b"Time (sec), Temperature 1, Temperature 2, Heater 1, Heater 2\n1e150,20,21,0,0\n"
    manifest = load(EXAMPLE / "source.json")
    manifest.update(origin="test_fixture", raw_sha256=bytes_ref(raw))
    csv_path, manifest_path = tmp_path / "large-time.csv", tmp_path / "large-time-source.json"
    csv_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # The recording itself is valid; its exclusive Session bound is not representable
    # within the shared persisted-document contract.
    assert import_recording(raw, manifest)["rows"][0]["time_s"] == 1e150
    output = tmp_path / "must-not-contain-partial-recording"
    assert main(["import-temperature", "--csv", str(csv_path), "--manifest", str(manifest_path),
                 "--output-dir", str(output), "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    refusal = json.loads(captured.err)
    assert refusal["status"] == "refused"
    assert "Final recording time" in refusal["reason"]
    assert "Session selection endpoint" in refusal["reason"]
    assert not output.exists()
    assert csv_path.read_bytes() == raw


def test_last_serializable_selection_endpoint_survives_import_and_reopen(tmp_path, capsys):
    import math
    from ciw.control_contracts import bytes_ref

    timestamp = math.nextafter(1e150, 0.0)
    raw = ("Time (sec), Temperature 1, Temperature 2, Heater 1, Heater 2\n"
           + repr(timestamp) + ",20,21,0,0\n").encode()
    manifest = load(EXAMPLE / "source.json")
    manifest.update(origin="test_fixture", raw_sha256=bytes_ref(raw))
    csv_path, manifest_path = tmp_path / "supported-time.csv", tmp_path / "supported-source.json"
    csv_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "supported-time-import"
    assert main(["import-temperature", "--csv", str(csv_path), "--manifest", str(manifest_path),
                 "--output-dir", str(output), "--json"]) == 0
    capsys.readouterr()
    reopened = Session.from_workspace(output / "workspace.json", output_dir=tmp_path / "reopened")
    assert reopened.run["time_s"] == [timestamp]
    assert reopened.run["metadata"]["duration_s"] == 1e150
    assert source_recording(reopened.run)["rows"][0]["time_s"] == timestamp
    assert reopened.executions == reopened.results == {}
