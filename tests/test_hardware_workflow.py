"""Retained hardware evidence tests; fixtures do not qualify physical equipment."""
from copy import deepcopy
import html
import json
import uuid

import pytest

from ciw import hardware_contract as contract, hardware_workflow as workflow
from ciw.adapters.protocol import AdapterRefusal
from ciw.control_contracts import bytes_ref
from ciw.core.identities import evidence_id, validate_evidence_identity, validate_identity
from ciw.operations.registry import default_registry
from ciw.operations.runner import check_seal, seal
from ciw.session import Session


def _workspace(directory):
    return json.loads((directory / "workspace.json").read_text(encoding="utf-8"))


def _write_workspace(directory, value):
    (directory / "workspace.json").write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def _files(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def _call(session, parameters=None):
    response = session.handle({
        "protocol_version": 1, "request_id": uuid.uuid4().hex, "type": "operation.execute",
        "payload": {"operation_id": contract.OPERATION, "parameters": parameters or {}},
    })
    assert response["type"] == "response", response
    return response["payload"]


def _source(request=None):
    request = contract.example_request() if request is None else request
    raw = json.dumps(request).encode("utf-8")
    return workflow.make_source(request, bytes_ref(raw))


@pytest.mark.parametrize("status", ["PASS", "FAIL", "INDETERMINATE"])
def test_import_and_inspection_retain_exact_outcome_and_separate_identities(tmp_path, status):
    request = contract.example_request()
    if status == "FAIL":
        request["observations"]["records"][0]["value"] = 1
    elif status == "INDETERMINATE":
        request["observations"]["records"].pop()
    unchanged = deepcopy(request)
    directory = tmp_path / "bundle"
    result = workflow.run(request, directory)
    assert request == unchanged
    assert result["status"] == status
    assert result["verification_status"] == "not_verified"
    assert result["provider_executed_by_inspection"] is False
    assert result["assessment"] == contract.assess(request)
    workspace = _workspace(directory)
    validate_evidence_identity(workspace["run"])
    assert workspace["workspace_version"] == 2
    assert len(workspace["results"]) == len(workspace["executions"]) == 1
    execution, assessment = workspace["executions"][0], workspace["results"][0]
    check_seal(execution)
    check_seal(assessment)
    validate_identity(execution["execution_id"], "execution")
    validate_identity(assessment["result_id"], "result")
    assert execution["result_id"] == assessment["result_id"]
    assert assessment["execution_id"] == execution["execution_id"]
    assert assessment["role"] == "decision"
    assert assessment["verification_id"] is None
    assert assessment["verification_status"] == "not_verified"
    assert assessment["evidence_id"] == result["evidence_id"]
    before = _files(directory)
    assert workflow.inspect(directory) == result
    assert _files(directory) == before


def test_replay_preserves_noncanonical_utf8_crlf_bytes_but_creates_new_occurrences(tmp_path):
    request = contract.example_request()
    request["observations"]["environment"] = "Synthetic café — room \u00b0C"
    raw = ("\r\n" + json.dumps(request, ensure_ascii=False, indent=3).replace("\n", "\r\n") + "\r\n").encode("utf-8")
    original, replayed = tmp_path / "original", tmp_path / "replayed"
    first = workflow.run(request, original, raw=raw)
    assert (original / "request.json").read_bytes() == raw
    before = _files(original)
    second = workflow.replay(original, replayed)
    assert (replayed / "request.json").read_bytes() == raw
    for field in ("evidence_id", "input_bytes_ref", "configuration_ref", "assessment"):
        assert second[field] == first[field]
    assert second["input_bytes_ref"] == bytes_ref(raw)
    for field in ("execution_id", "result_id"):
        assert second[field] != first[field]
    assert _files(original) == before


def test_inspection_restore_and_report_never_dispatch_provider_or_read_current_runtime(tmp_path, monkeypatch):
    directory = tmp_path / "bundle"
    first = workflow.run(contract.example_request(), directory)
    before = _files(directory)

    def forbidden(*args, **kwargs):
        pytest.fail("Retained inspection dispatched an executable hardware provider")

    for name in ("_assess", "runtime_identity", "registry", "operation"):
        monkeypatch.setattr(workflow, name, forbidden)
    restored = Session.from_workspace(directory / "workspace.json", tmp_path / "restored")
    assert len(restored.results) == len(restored.executions) == 1
    assert workflow.inspect(directory) == first
    report = workflow.export_report(directory, tmp_path / "report.html")
    assert report["evidence_id"] == first["evidence_id"]
    assert _files(directory) == before


def test_hardware_provider_requires_explicit_registry_binding():
    with pytest.raises(AdapterRefusal):
        default_registry().get(contract.OPERATION)
    advertised = workflow.capability_registry()
    catalog = advertised.catalog()
    assert catalog["authorizes_execution"] is False
    assert set(catalog["operations"]) == {contract.OPERATION}
    assert catalog["operations"][contract.OPERATION]["bound"] is False
    with pytest.raises(AdapterRefusal):
        advertised.operations.get(contract.OPERATION)
    bound = workflow.capability_registry(bind=True)
    assert bound.catalog()["operations"][contract.OPERATION]["bound"] is True
    assert bound.operations.get(contract.OPERATION).role == "decision"


def test_failed_provider_attempt_is_retained_and_inspectable(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Simulated provider failure")

    monkeypatch.setattr(workflow, "_assess", fail)
    directory = tmp_path / "failed"
    result = workflow.run(contract.example_request(), directory)
    assert result["status"] == "REFUSED"
    assert result["assessment"] is None
    assert result["result_id"] is None
    assert result["refusal"]["code"] == "operation_failed"
    workspace = _workspace(directory)
    assert workspace["results"] == []
    assert len(workspace["executions"]) == 1
    assert workspace["executions"][0]["status"] == "refused"
    assert workflow.inspect(directory) == result
    report = workflow.export_report(directory, tmp_path / "failed.html")
    assert report["status"] == "REFUSED"


def test_forged_provider_output_retains_refusal_instead_of_result(tmp_path, monkeypatch):
    def forge(source, parameters):
        data = contract.assess(workflow.source_request(source))
        data["status"] = "FAIL"
        return data

    monkeypatch.setattr(workflow, "_assess", forge)
    directory = tmp_path / "forged"
    result = workflow.run(contract.example_request(), directory)
    assert result["status"] == "REFUSED"
    assert result["refusal"]["code"] == "invalid_operation"
    assert not _workspace(directory)["results"]


@pytest.mark.parametrize("mode", ["whitespace", "number_representation", "content"])
def test_input_byte_tampering_is_rejected_even_if_json_meaning_is_unchanged(tmp_path, mode):
    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    path = directory / "request.json"
    raw = path.read_bytes()
    if mode == "whitespace":
        raw += b" \n"
    elif mode == "number_representation":
        raw = raw.replace(b'"value": 0,', b'"value": 0.0,', 1)
    else:
        raw = raw.replace(b'"value": 0,', b'"value": 7,', 1)
    assert raw != path.read_bytes()
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        workflow.inspect(directory)
    with pytest.raises(ValueError):
        workflow.replay(directory, tmp_path / "replay")
    assert not (tmp_path / "replay").exists()


@pytest.mark.parametrize("mode", ["status", "check", "authority", "plan_ref"])
def test_forged_resealed_assessment_cannot_be_reopened_or_exported(tmp_path, mode):
    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    workspace = _workspace(directory)
    result = workspace["results"][0]
    if mode == "status":
        result["data"]["status"] = "FAIL"
    elif mode == "check":
        result["data"]["checks"][0]["value"] = 1
    elif mode == "authority":
        result["data"]["authority"]["release_authorization"] = "granted"
    else:
        result["data"]["plan_ref"] = "sha256:" + "0" * 64
    seal(result)
    _write_workspace(directory, workspace)
    with pytest.raises(ValueError, match="exact retained request"):
        Session.from_workspace(directory / "workspace.json", tmp_path / "restored")
    with pytest.raises(ValueError):
        workflow.export_report(directory, tmp_path / "report.html")
    assert not (tmp_path / "restored").exists()
    assert not (tmp_path / "report.html").exists()


@pytest.mark.parametrize("field", ["provider", "version", "scope", "source_normalization", "code_sha256", "environment"])
def test_resealed_wrong_runtime_metadata_is_refused_on_restore(tmp_path, field):
    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    workspace = _workspace(directory)
    replacement = {"python": ""} if field == "environment" else "wrong"
    for record in (workspace["executions"][0], workspace["results"][0]):
        record["runtime"][field] = replacement
        seal(record)
    _write_workspace(directory, workspace)
    with pytest.raises(ValueError):
        workflow.inspect(directory)
    with pytest.raises(ValueError):
        Session.from_workspace(directory / "workspace.json", tmp_path / "restored")


@pytest.mark.parametrize("mode", ["render", "channel_value", "provenance"])
def test_source_declaration_cannot_be_relabelled_after_rehashing(mode):
    source = _source()
    if mode == "render":
        source["render"] = {"coordinate_frame": workflow.FRAME, "label": "unsupported alteration"}
    elif mode == "channel_value":
        source["channels"][workflow.CHANNEL]["values"] = [2.0]
    else:
        source["metadata"]["provenance"]["source"] = "authenticated"
    source["evidence_id"] = evidence_id(source)
    with pytest.raises(ValueError, match="exact retained declaration"):
        workflow.source_request(source)


def test_stale_configuration_refused_before_creating_bundle(tmp_path):
    request = contract.example_request()
    request["configuration"]["components"][0]["model"] = "replacement"
    with pytest.raises(ValueError, match="different configuration"):
        workflow.run(request, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_partial_selection_and_unrequested_policy_parameters_cannot_produce_assessment(tmp_path):
    session = Session(_source(), tmp_path / "session", operations=workflow.registry())
    response = session.handle({"protocol_version": 1, "request_id": "partial", "type": "selection.update",
                               "payload": {"expected_revision": 0, "interval_s": [0.0, 0.5]}})
    assert response["type"] == "response"
    partial = _call(session)
    assert partial["status"] == "refused"
    assert "complete imported declaration" in partial["execution"]["refusal"]["message"]
    assert not session.results
    session.handle({"protocol_version": 1, "request_id": "complete", "type": "selection.update",
                    "payload": {"expected_revision": 1, "interval_s": [0.0, 1.0]}})
    injected = _call(session, {"minimum": 0})
    assert injected["status"] == "refused"
    assert not session.results
    session.save_workspace(tmp_path / "saved.json")
    restored = Session.from_workspace(tmp_path / "saved.json", tmp_path / "restored")
    assert len(restored.executions) == 2
    assert not restored.results


def test_resealed_partial_selection_cannot_relabel_complete_assessment(tmp_path):
    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    workspace = _workspace(directory)
    for record in (workspace["executions"][0], workspace["results"][0]):
        record["interval_s"] = [0.0, 0.5]
        seal(record)
    _write_workspace(directory, workspace)
    with pytest.raises(ValueError, match="complete imported declaration"):
        workflow.inspect(directory)


def test_run_replay_and_report_are_create_only(tmp_path):
    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    before = _files(directory)
    with pytest.raises(FileExistsError):
        workflow.run(contract.example_request(), directory)
    with pytest.raises(FileExistsError):
        workflow.replay(directory, directory)
    with pytest.raises(FileExistsError):
        workflow.export_report(directory, directory / "request.json")
    assert _files(directory) == before
    report = tmp_path / "report.html"
    workflow.export_report(directory, report)
    old_report = report.read_bytes()
    with pytest.raises(FileExistsError):
        workflow.export_report(directory, report)
    assert report.read_bytes() == old_report


def test_supplied_raw_request_must_match_request_before_creating_bundle(tmp_path):
    request = contract.example_request()
    other = deepcopy(request)
    other["plan"]["checks"][0]["maximum"] = 100
    with pytest.raises(ValueError, match="Raw request bytes differ"):
        workflow.run(request, tmp_path / "bundle", raw=json.dumps(other).encode("utf-8"))
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("raw", [b"", b"{", b'{"schema":1,"schema":2}', b'{"value":NaN}', b"\xff"])
def test_invalid_json_bytes_are_refused(raw):
    with pytest.raises((ValueError, UnicodeDecodeError)):
        workflow.parse_request(raw)


def test_regular_file_read_is_bounded_and_refuses_empty_or_directory(tmp_path):
    path = tmp_path / "raw.json"
    path.write_bytes(b"12345")
    assert workflow.read_bytes(path, limit=5) == b"12345"
    with pytest.raises(ValueError, match="byte budget"):
        workflow.read_bytes(path, limit=4)
    path.write_bytes(b"")
    with pytest.raises(ValueError):
        workflow.read_bytes(path)
    with pytest.raises(ValueError):
        workflow.read_bytes(tmp_path)


def test_report_escapes_imported_text_and_carries_evidence_and_mismatched_procedure(tmp_path):
    request = contract.example_request()
    unsafe = '<script>alert("hardware")</script>&'
    request["configuration"]["asset_id"] = unsafe
    request["configuration"]["components"][0]["model"] = unsafe
    request["configuration"]["software"][0]["name"] = unsafe
    request["observations"]["asset_id"] = unsafe
    request["observations"]["configuration_ref"] = contract.configuration_ref(request["configuration"])
    request["observations"]["records"][0].update(procedure_id="actual-observed-procedure-v2", notes=unsafe)
    request["observations"]["operator"] = unsafe
    request["observations"]["environment"] = unsafe
    directory = tmp_path / "bundle"
    result = workflow.run(request, directory)
    before = _files(directory)
    output = tmp_path / "report.html"
    exported = workflow.export_report(directory, output)
    text = output.read_text(encoding="utf-8")
    assert unsafe not in text
    assert html.escape(unsafe, quote=True) in text
    assert "SYNTHETIC EXAMPLE" in text
    assert "INDETERMINATE" in text
    assert "actual-observed-procedure-v2" in text
    assert request["plan"]["checks"][0]["procedure_id"] in text
    for key in ("configuration_ref", "evidence_id", "input_bytes_ref", "execution_id", "result_id"):
        assert html.escape(result[key], quote=True) in text
    assert "not_verified" in text
    assert exported["report_sha256"] == bytes_ref(output.read_bytes())
    assert _files(directory) == before


def test_operator_report_explicitly_labels_import_and_unauthenticated_acquisition(tmp_path):
    request = contract.example_request()
    request["observations"]["source_kind"] = "operator_import"
    directory, output = tmp_path / "bundle", tmp_path / "report.html"
    workflow.run(request, directory)
    workflow.export_report(directory, output)
    text = output.read_text(encoding="utf-8")
    assert "Operator-imported observations" in text
    assert "acquisition not authenticated" in text
    assert "SYNTHETIC EXAMPLE" not in text


@pytest.mark.parametrize("version", [3, 4])
def test_dedicated_bundle_refuses_workbench_and_correction_workspace_versions(tmp_path, version):
    from ciw.correction_journal import CorrectionJournal
    from ciw.workbench import Workbench

    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    workspace = _workspace(directory)
    workspace["workspace_version"] = version
    workspace["workbench"] = Workbench().serialize()
    if version == 4:
        workspace["correction_journal"] = CorrectionJournal().serialize()
    _write_workspace(directory, workspace)
    # Generic NET can read this data, but the bounded hardware projection must
    # not silently drop correction/workbench applicability information.
    Session.from_workspace(directory / "workspace.json", tmp_path / "generic-restored")
    with pytest.raises(ValueError):
        workflow.inspect(directory)
    with pytest.raises(ValueError):
        workflow.export_report(directory, tmp_path / "report.html")
    assert not (tmp_path / "report.html").exists()


@pytest.mark.parametrize("field", ["workbench", "correction_journal"])
def test_v2_bundle_cannot_hide_later_version_state_in_extra_fields(tmp_path, field):
    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    workspace = _workspace(directory)
    workspace[field] = {}
    _write_workspace(directory, workspace)
    with pytest.raises(ValueError):
        workflow.inspect(directory)


@pytest.mark.parametrize("outcome", ["refused", "completed"])
def test_dedicated_bundle_requires_exactly_one_assessment_occurrence(tmp_path, outcome):
    directory = tmp_path / "bundle"
    workflow.run(contract.example_request(), directory)
    workspace = _workspace(directory)
    execution = deepcopy(workspace["executions"][0])
    execution["execution_id"] = "execution-" + uuid.uuid4().hex
    if outcome == "refused":
        execution.update(status="refused", result_id=None,
                         refusal={"code": "invalid_operation", "message": "extra occurrence"})
    else:
        result = deepcopy(workspace["results"][0])
        result["result_id"] = "result-" + uuid.uuid4().hex
        result["execution_id"] = execution["execution_id"]
        execution["result_id"] = result["result_id"]
        workspace["results"].append(seal(result))
    workspace["executions"].append(seal(execution))
    _write_workspace(directory, workspace)
    Session.from_workspace(directory / "workspace.json", tmp_path / "generic-restored")
    with pytest.raises(ValueError, match="exactly one"):
        workflow.inspect(directory)

@pytest.mark.parametrize("field,value", [
    ("provider", "other-provider"), ("scope", "physical_qualification"),
    ("code_sha256", "not-a-digest"), ("environment", {"python": ""}),
])
def test_invalid_live_runtime_refuses_before_provider_and_retains_reopenable_attempt(tmp_path, field, value):
    from ciw.operations.registry import Operation, OperationRegistry

    runtime = workflow.runtime_identity()
    runtime[field] = value

    def forbidden(*args, **kwargs):
        pytest.fail("Hardware provider dispatched with invalid runtime metadata")

    operations = OperationRegistry()
    operations.register(Operation(contract.OPERATION, "decision", forbidden, lambda: runtime))
    session = Session(_source(), tmp_path / "session", operations=operations)
    outcome = _call(session)
    assert outcome["status"] == "refused"
    assert outcome["result"] is None
    assert outcome["execution"]["runtime"] is None
    assert outcome["execution"]["refusal"]["code"] == "invalid_operation"
    saved = session.save_workspace(tmp_path / "saved.json")
    restored = Session.from_workspace(saved, tmp_path / "restored")
    assert restored.results == {}
    assert restored.executions == session.executions