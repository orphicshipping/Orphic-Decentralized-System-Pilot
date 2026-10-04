"""CLI lifecycle and refusal checks for imported hardware declarations."""
import json

import pytest

from ciw import hardware_cli, hardware_contract as contract, hardware_workflow as workflow, net
from ciw.operations.runner import seal
from ciw.session import read_json, write_json


def test_cli_lifecycle_exact_input_and_customer_report(tmp_path, capsys):
    request_path = tmp_path / "request.json"
    assert net.main(["hardware", "example", "--output", str(request_path)]) == 0
    original = request_path.read_bytes()
    request = read_json(request_path)
    config = tmp_path / "configuration.json"
    write_json(config, request["configuration"])
    identity = tmp_path / "identity.json"
    assert net.main(["hardware", "configuration", str(config), "--output", str(identity)]) == 0
    assert read_json(identity)["configuration_ref"] == request["observations"]["configuration_ref"]
    run = tmp_path / "run"
    assert net.main(["hardware", "run", str(request_path), "--output-dir", str(run)]) == 0
    assert (run / "request.json").read_bytes() == original
    assert net.main(["hardware", "inspect", str(run)]) == 0
    report = tmp_path / "report.html"
    assert net.main(["hardware", "report", str(run), "--output", str(report)]) == 0
    content = report.read_text(encoding="utf-8")
    assert "SYNTHETIC EXAMPLE" in content and "no equipment tested" in content
    assert request["configuration"]["asset_id"] in content
    replay = tmp_path / "replay"
    assert net.main(["hardware", "replay", str(run), "--output-dir", str(replay)]) == 0
    before, after = workflow.inspect(run), workflow.inspect(replay)
    assert before["evidence_id"] == after["evidence_id"]
    assert before["execution_id"] != after["execution_id"]
    assert before["result_id"] != after["result_id"]
    assert before["assessment"] == after["assessment"]
    assert (replay / "request.json").read_bytes() == original
    assert '"verification_status": "not_verified"' in capsys.readouterr().out


@pytest.mark.parametrize("case,expected", [("pass", 0), ("fail", 2), ("missing", 3), ("short", 3), ("unit", 3), ("error", 3)])
def test_outcome_exit_codes_are_preserved_on_inspection_and_report(tmp_path, case, expected):
    request = contract.example_request()
    record = request["observations"]["records"][0]
    if case == "fail":
        record["value"] = 1
    elif case == "missing":
        request["observations"]["records"].clear()
    elif case == "short":
        record["duration_s"] = 1
    elif case == "unit":
        record["unit"] = "different"
    elif case == "error":
        record.update(status="error", value=None)
    path, run = tmp_path / "input.json", tmp_path / "run"
    write_json(path, request)
    assert hardware_cli.main(["run", str(path), "--output-dir", str(run)]) == expected
    assert hardware_cli.main(["inspect", str(run)]) == expected
    assert hardware_cli.main(["report", str(run), "--output", str(tmp_path / "report.html")]) == expected
    assert (tmp_path / "report.html").is_file()


def test_create_only_does_not_overwrite_evidence(tmp_path):
    path = tmp_path / "input.json"
    assert hardware_cli.main(["example", "--output", str(path)]) == 0
    before = path.read_bytes()
    assert hardware_cli.main(["example", "--output", str(path)]) == 1
    assert path.read_bytes() == before
    run = tmp_path / "run"
    assert hardware_cli.main(["run", str(path), "--output-dir", str(run)]) == 0
    workspace = (run / "workspace.json").read_bytes()
    assert hardware_cli.main(["run", str(path), "--output-dir", str(run)]) == 1
    assert hardware_cli.main(["replay", str(run), "--output-dir", str(run)]) == 1
    assert (run / "workspace.json").read_bytes() == workspace
    report = tmp_path / "report.html"
    assert hardware_cli.main(["report", str(run), "--output", str(report)]) == 0
    report_bytes = report.read_bytes()
    assert hardware_cli.main(["report", str(run), "--output", str(report)]) == 1
    assert report.read_bytes() == report_bytes


@pytest.mark.parametrize("raw", [
    b'{"schema":"a","schema":"b"}',
    b'{"value":NaN}',
    b'{"value":1e999}',
    b"not json",
    b"\xff",
    b"",
])
def test_malformed_input_refuses_before_creating_output(tmp_path, raw, capsys):
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    destination = tmp_path / "bad"
    assert hardware_cli.main(["run", str(path), "--output-dir", str(destination)]) == 1
    assert not destination.exists()
    assert json.loads(capsys.readouterr().err)["status"] == "REFUSED"


def test_stale_configuration_refuses_before_creating_output(tmp_path):
    request = contract.example_request()
    request["configuration"]["components"][0]["model"] = "Replacement DIMM"
    path = tmp_path / "request.json"
    write_json(path, request)
    assert hardware_cli.main(["run", str(path), "--output-dir", str(tmp_path / "bad")]) == 1
    assert not (tmp_path / "bad").exists()


def test_generic_net_inspect_reopens_without_provider(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "run"
    workflow.run(contract.example_request(), directory)
    def forbidden(*args, **kwargs):
        raise AssertionError("Inspection must not invoke the provider")
    monkeypatch.setattr(workflow, "_assess", forbidden)
    assert net.main(["inspect", str(directory / "workspace.json"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "ciw.workspace-inspection.v1"


def test_report_refuses_forged_resealed_outcome(tmp_path):
    request = contract.example_request()
    request["observations"]["records"][0]["value"] = 5
    directory = tmp_path / "run"
    workflow.run(request, directory)
    path = directory / "workspace.json"
    workspace = read_json(path)
    workspace["results"][0]["data"]["status"] = "PASS"
    seal(workspace["results"][0])
    write_json(path, workspace)
    report = tmp_path / "bad.html"
    assert hardware_cli.main(["report", str(directory), "--output", str(report)]) == 1
    assert not report.exists()
