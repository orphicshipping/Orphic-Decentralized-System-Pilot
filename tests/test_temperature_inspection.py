"""Operator inspection keeps retained temperature evidence and authority distinct."""
from copy import deepcopy
import json

import pytest

from ciw.agent_api import encode, parse
from ciw.agent_mcp import from_profile, temperature_config
from ciw.net import inspect_path, main
from ciw.operations.runner import seal
from ciw.temperature_contract import example_request
from ciw.temperature_review import summary_lines
from ciw.temperature_workflow import PROCESS, VERIFY


def _workspace(tmp_path, *, request=None, process_only=False, two_audits=False):
    profile = temperature_config(tmp_path / "profile", request)
    if process_only or two_audits:
        config = parse(profile.read_bytes())
        graph_path = profile.parent / "experiment.json"
        graph = parse(graph_path.read_bytes())
        if process_only:
            config.update(allow_operations=[PROCESS], max_nodes=1)
            graph["nodes"] = graph["nodes"][:1]
        else:
            config["max_nodes"] = 3
            extra = deepcopy(graph["nodes"][1])
            extra["node_id"] = "verification_again"
            graph["nodes"].append(extra)
        profile.write_bytes(encode(config))
        graph_path.write_bytes(encode(seal(graph)))
    host = from_profile(profile, instrument="temperature")
    response = host.call("net_execute", {"source": "source", "graph": "baseline", "attempt": "first"})
    assert response["status"] == "completed", response
    path = profile.parent / "agent-output/first/workspace.json"
    return path, profile, host


def _forbid_execution(monkeypatch):
    from ciw import temperature_processing, temperature_verification, temperature_workflow

    def forbidden(*args, **kwargs):
        pytest.fail("Retained inspection dispatched a provider or current runtime identity")

    for module, names in (
        (temperature_processing, ("process_request", "_sensor_output")),
        (temperature_verification, ("verify",)),
        (temperature_workflow, ("_process", "_verify", "runtime_identity")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)


def _result(workspace, operation):
    return next(row for row in workspace["results"] if row["operation_id"] == operation)


def _assert_authority_boundary(text):
    assert "Generic verification status: not_verified" in text
    assert "Fresh numerical verification: not performed by inspection." in text
    assert "physical_qualification=not_established" in text
    assert "calibration_traceability=not_established" in text
    assert "origin_authentication=not_established" in text
    assert "proof=not_generated" in text
    assert "model_inference=not_run" in text
    assert "submitted sample instants only" in text


def test_inspection_distinguishes_saved_pass_from_indeterminate_acceptance_and_physical_scope(tmp_path, monkeypatch, capsys):
    path, _, _ = _workspace(tmp_path)
    original = path.read_bytes()
    stored = parse(original)
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(path)]) == 0
    text = capsys.readouterr().out
    assert "Temperature measurement review (retained records)" in text
    assert "Saved numerical audit (historical, unauthenticated): PASS" in text
    assert "Operational acceptance: indeterminate" in text
    assert "Uncertainty budget: incomplete_declared_budget" in text
    assert "Polymer temperature estimate: unavailable" in text
    assert "No separately declared installed correction" in text
    assert _result(stored, PROCESS)["result_id"] in text
    assert _result(stored, VERIFY)["data"]["verification_id"] in text
    _assert_authority_boundary(text)
    assert path.read_bytes() == original


def test_json_inspection_is_the_original_validated_mapping_without_presentation_fields(tmp_path, monkeypatch, capsys):
    path, _, _ = _workspace(tmp_path)
    original = path.read_bytes()
    _forbid_execution(monkeypatch)
    from ciw import net
    captured = []
    inspect = net.inspect_path

    def capturing(*args, **kwargs):
        result = inspect(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(net, "inspect_path", capturing)
    assert main(["inspect", str(path), "--json"]) == 0
    output = capsys.readouterr().out
    assert len(captured) == 1
    expected = captured[0]
    frozen = deepcopy(expected)
    assert output == json.dumps(expected, indent=2, allow_nan=False) + "\n"
    assert json.loads(output) == expected
    assert summary_lines(expected["results"])
    assert expected == frozen
    assert path.read_bytes() == original


def test_processing_only_workspace_reports_audit_absence_without_running_one(tmp_path, monkeypatch, capsys):
    path, _, _ = _workspace(tmp_path, process_only=True)
    original = path.read_bytes()
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(path)]) == 0
    text = capsys.readouterr().out
    assert "Saved numerical audit: not present" in text
    assert "Saved numerical audit (historical, unauthenticated): PASS" not in text
    _assert_authority_boundary(text)
    assert path.read_bytes() == original


def test_retained_numerical_failure_is_visible_and_does_not_fail_read_only_inspection(tmp_path, monkeypatch, capsys):
    from ciw import temperature_processing
    process = temperature_processing.process_request

    def faulty_provider(request):
        report = process(request)
        report["calibrated_sensor_temperature"]["samples"][0]["value_K"] += 1.0
        return seal(report)

    monkeypatch.setattr(temperature_processing, "process_request", faulty_provider)
    path, _, _ = _workspace(tmp_path)
    original = path.read_bytes()
    assert _result(parse(original), VERIFY)["data"]["report"]["status"] == "FAIL"
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(path)]) == 0
    text = capsys.readouterr().out
    assert "Saved numerical audit (historical, unauthenticated): FAIL" in text
    assert "independent_affine_transform" in text
    assert "Operational acceptance: indeterminate" in text
    _assert_authority_boundary(text)
    assert path.read_bytes() == original


def test_identical_report_contents_do_not_attach_replay_audits_to_the_wrong_occurrence(tmp_path, monkeypatch, capsys):
    path, profile, host = _workspace(tmp_path)
    first = parse(path.read_bytes())
    replay = host.call("net_replay", {"original_attempt": "first", "new_attempt": "second"})
    assert replay["status"] == "completed", replay
    second = parse((profile.parent / "agent-output/second/workspace.json").read_bytes())
    processing1, processing2 = (_result(value, PROCESS) for value in (first, second))
    audit1, audit2 = (_result(value, VERIFY) for value in (first, second))
    assert processing1["data"] == processing2["data"]
    assert audit1["data"]["report"] == audit2["data"]["report"]
    assert processing1["result_id"] != processing2["result_id"]
    combined = deepcopy(first)
    combined["results"] = [audit2, processing1, audit1, processing2]
    combined["executions"] += second["executions"]
    if "saved_at" in second:
        combined["saved_at"] = second["saved_at"]
    combined_path = tmp_path / "combined.json"
    combined_path.write_bytes(encode(combined))
    original = combined_path.read_bytes()
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(combined_path)]) == 0
    text = capsys.readouterr().out
    blocks = text.split("Processing result: ")[1:]
    assert len(blocks) == 2
    for processing, own, other in ((processing1, audit1, audit2), (processing2, audit2, audit1)):
        block = next(value for value in blocks if value.startswith(processing["result_id"]))
        assert own["data"]["verification_id"] in block
        assert other["data"]["verification_id"] not in block
        assert text.count(own["data"]["verification_id"]) == 1
    assert combined_path.read_bytes() == original


def test_multiple_audits_of_one_processing_occurrence_are_all_presented(tmp_path, monkeypatch, capsys):
    path, _, _ = _workspace(tmp_path, two_audits=True)
    original = path.read_bytes()
    audits = [row for row in parse(original)["results"] if row["operation_id"] == VERIFY]
    assert len(audits) == 2
    assert audits[0]["data"]["report"] == audits[1]["data"]["report"]
    assert audits[0]["data"]["verification_id"] != audits[1]["data"]["verification_id"]
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(path)]) == 0
    text = capsys.readouterr().out
    assert text.count("Processing result: ") == 1
    assert text.count("Saved numerical audit (historical, unauthenticated): PASS") == 2
    assert all(text.count(row["data"]["verification_id"]) == 1 for row in audits)
    assert path.read_bytes() == original


def test_all_invalid_observations_make_zero_numerical_coverage_visible(tmp_path, monkeypatch, capsys):
    request = example_request()
    for row in request["raw_observations"]["samples"]:
        row.update(status="missing", value=None)
    path, _, _ = _workspace(tmp_path, request=request)
    original = path.read_bytes()
    audit = _result(parse(original), VERIFY)["data"]["report"]
    assert audit["status"] == "PASS"
    assert next(row for row in audit["checks"] if row["name"] == "independent_affine_transform")["compared_values"] == 0
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(path)]) == 0
    text = capsys.readouterr().out
    assert "invalid: 2" in text
    assert "No valid temperature samples; saved audit PASS does not verify unavailable temperature values." in text
    assert "independent_affine_transform: PASS; compared values: 0; mismatches: 0" in text
    assert "independent_joint_covariance: PASS; compared values: 0; mismatches: 0" in text
    assert 'Invalid sample "s0"' in text
    assert 'Invalid sample "s1"' in text
    assert "Operational acceptance: indeterminate" in text
    assert "Saved numerical audit (historical, unauthenticated): PASS" in text
    _assert_authority_boundary(text)
    assert path.read_bytes() == original


def test_control_characters_in_invalid_sample_labels_are_escaped(tmp_path, monkeypatch, capsys):
    request = example_request()
    marker = "sample\x1b[31m\nFORGED_ACCEPTANCE"
    request["raw_observations"]["samples"][0].update(sample_id=marker, status="missing", value=None)
    request["joint_covariance"]["order"][0] = "raw:" + marker
    path, _, _ = _workspace(tmp_path, request=request)
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(path)]) == 0
    text = capsys.readouterr().out
    assert "\x1b" not in text
    assert "\nFORGED_ACCEPTANCE" not in text
    assert json.dumps(marker, ensure_ascii=True) in text


@pytest.mark.parametrize("fault", ["missing_parent", "wrong_parent", "broken_report_seal", "processing_authority", "audit_authority", "malformed_audit_check"])
def test_invalid_retained_workspaces_refuse_before_printing_a_temperature_review(tmp_path, monkeypatch, capsys, fault):
    path, _, _ = _workspace(tmp_path)
    workspace = parse(path.read_bytes())
    processing, audited = _result(workspace, PROCESS), _result(workspace, VERIFY)
    if fault == "missing_parent":
        workspace["results"].remove(processing)
    elif fault == "wrong_parent":
        audited["data"]["processing_result_id"] = "different-processing-occurrence"
        seal(audited)
    elif fault == "broken_report_seal":
        processing["data"]["calibrated_sensor_temperature"]["samples"][0]["value_K"] += 1.0
        seal(processing)
    elif fault == "processing_authority":
        processing["data"]["authority"]["physical_qualification"] = "established"
        seal(processing["data"])
        seal(processing)
    else:
        audit = audited["data"]["report"]
        if fault == "audit_authority":
            audit["authority"]["proof"] = "verified"
        else:
            audit["checks"][1]["compared_values"] += 1
        seal(audit)
        seal(audited)
    path.write_bytes(encode(workspace))
    original = path.read_bytes()
    _forbid_execution(monkeypatch)
    assert main(["inspect", str(path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["status"] == "refused"
    assert path.read_bytes() == original


@pytest.mark.parametrize("field", ["processing_execution_id", "processing_record_digest"])
def test_presentation_refuses_a_mismatched_occurrence_binding(field, tmp_path):
    path, _, _ = _workspace(tmp_path)
    results = inspect_path(path)["results"]
    audited = next(row for row in results.values() if row["operation_id"] == VERIFY)
    audited["data"][field] = "wrong-binding"
    with pytest.raises(ValueError, match="not bound to this processing occurrence"):
        summary_lines(results)


def test_non_temperature_results_do_not_gain_a_temperature_review():
    results = {"unrelated": {"operation_id": "statistics.v1"}}
    original = deepcopy(results)
    assert summary_lines(results) == []
    assert results == original
