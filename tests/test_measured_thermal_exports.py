"""Retained measured thermal exports through existing observations and NET CLI."""
from copy import deepcopy
import json

import pytest

from ciw import measured_thermal as measured
from ciw import measured_thermal_workflow as workflow
from ciw import scientific
from ciw.control_checks import compare, inspect_record
from ciw.control_contracts import bytes_ref, save_new
from ciw.instruments import make_demo_run
from ciw.net import main
from ciw.scientific_observations import export_observations, select_observations
from ciw.session import Session
from ciw.telemetry import canonical
from test_measured_thermal import _source, _rewrite_stream
from test_scientific_observations import make_case as synthetic_case


def _make_case(root, source=None):
    source = _source() if source is None else source
    session = Session(make_demo_run(), root)
    summary = scientific.execute(session, "measured-thermal", canonical(source),
                                 label="Simulated ingress export test", repositories={})
    path = session.save_workspace(root / "workspace.json")
    return {"session": session, "summary": summary, "path": path,
            "sha": bytes_ref(path.read_bytes()), "source": source}


@pytest.fixture(scope="module")
def measured_case(tmp_path_factory):
    return _make_case(tmp_path_factory.mktemp("measured-export"))


def _export(case, stage="posterior", **overrides):
    options = {"expected_sha256": case["sha"], "bundle_id": case["summary"]["bundle_id"],
               "stage": stage, "entity_id": case["source"]["asset_id"]}
    options.update(overrides)
    return export_observations(case["path"], **options)


def _snapshot(root):
    return {p.relative_to(root).as_posix(): bytes_ref(p.read_bytes())
            for p in root.rglob("*") if p.is_file()}


def test_posterior_reference_are_comparable_scalar_core_streams(measured_case):
    case = measured_case
    posterior, reference = _export(case), _export(case, "reference")
    bundle = case["session"].workbench.get_bundle(case["summary"]["bundle_id"])
    step = bundle["steps"][0]
    for value in (posterior, reference):
        assert value["schema"] == "ciw.observation-stream.v1"
        inspect_record(value)
        assert len(value["observations"]) == 3
    for index, (estimate, observed) in enumerate(zip(posterior["observations"], reference["observations"])):
        native = step["result"]["data"]["observer"]["trace"][index]
        assert type(estimate["value"]) is float
        assert estimate["value"] == native["posterior_mean"][0]
        assert estimate["uncertainty"]["matrix"] == [[native["posterior_covariance"][0][0]]]
        assert estimate["uncertainty"]["quantity_ids"] == ["core_temperature"]
        for field in ("clock", "frame", "quantity", "unit"):
            assert estimate[field] == observed[field]
        assert estimate["quantity"] == "core_temperature"
        assert estimate["identity"]["entity_id"] == case["source"]["asset_id"]
        assert estimate["identity"]["execution_id"] == step["execution_id"]
        assert observed["identity"]["execution_id"] == "fixture-acquisition:core_reference"
        links = estimate["provenance"]["sources"]
        assert {case["sha"], bundle["bundle_digest"], step["result_id"],
                bytes_ref(canonical(case["source"]))} <= set(links)
        assert case["source"]["series"]["core_reference"] not in links
    comparison = compare(posterior["observations"], reference["observations"], atol=5)
    assert comparison["outcome"]["status"] == "PASS"
    assert comparison["claim_scope"] == "declared_numerical_condition_only"


def test_measurement_preserves_acquisition_provenance_and_covariance(measured_case):
    output = _export(measured_case, "measurement")
    original = measured.streams(measured_case["source"])["shell"]["observations"]
    for row, retained in zip(output["observations"], original):
        assert row["identity"] == retained["identity"]
        assert row["clock"] == retained["clock"]
        assert row["value"] == retained["value"]
        assert row["quantity"] == "shell_temperature"
        assert row["uncertainty"] == retained["uncertainty"]
        assert row["provenance"]["provider"] == retained["provenance"]["provider"]
        assert row["provenance"]["semantics"] == "simulated"
        assert set(retained["provenance"]["sources"]) <= set(row["provenance"]["sources"])
    posterior = _export(measured_case)
    assert compare(posterior["observations"], output["observations"], atol=1000)["outcome"] == {
        "status": "INDETERMINATE", "sample_count": 0, "component_count": 0,
        "metrics": None, "unit": None, "reason": "model_entity_quantity_unit_frame_or_clock_mismatch"}


@pytest.mark.parametrize("changed", ["hash", "entity", "bundle", "same_json_new_bytes"])
def test_export_refuses_wrong_workspace_bytes_entity_or_bundle(measured_case, tmp_path, changed):
    options = {}
    case = dict(measured_case)
    if changed == "hash":
        options["expected_sha256"] = "sha256:" + "0" * 64
    elif changed == "entity":
        options["entity_id"] = "asset:another"
    elif changed == "bundle":
        options["bundle_id"] = "sha256:" + "1" * 64
    else:
        copied = tmp_path / "same-content.json"
        copied.write_bytes(case["path"].read_bytes() + b"\n")
        assert json.loads(copied.read_bytes()) == json.loads(case["path"].read_bytes())
        case["path"] = copied
    with pytest.raises(ValueError):
        _export(case, **options)


def test_export_is_read_only_and_returned_values_are_detached(measured_case, monkeypatch):
    case = measured_case
    before = deepcopy(case["session"].workbench.serialize())
    files_before = _snapshot(case["path"].parent)
    def forbidden(*args, **kwargs):
        pytest.fail("Observation export entered a workflow execution path")
    monkeypatch.setattr(measured, "calculate", forbidden)
    for method in ("create_session", "replay_session", "_step", "_adapters"):
        monkeypatch.setattr(workflow.MeasuredThermalWorkflow, method, forbidden)
    for stage in ("posterior", "measurement", "reference"):
        exported = _export(case, stage)
        inspect_record(exported)
        direct = select_observations(case["session"], bundle_id=case["summary"]["bundle_id"],
            stage=stage, entity_id=case["source"]["asset_id"], workspace_sha256=case["sha"])
        assert direct == exported
        direct["observations"][0]["value"] = 999.0
        direct["observations"][0]["provenance"]["sources"].clear()
    assert case["session"].workbench.serialize() == before
    assert _snapshot(case["path"].parent) == files_before


def test_declared_measured_metadata_does_not_authenticate_or_grant_authority(tmp_path):
    source = _source()
    source["origin"] = "measured"
    for channel in measured.CHANNELS:
        def mark_observed(rows):
            for row in rows:
                row["provenance"]["semantics"] = "observed"
        _rewrite_stream(source, channel, mark_observed)
    # This is a constructed metadata-acceptance test, not acquired laboratory data.
    case = _make_case(tmp_path / "measured-declaration", source)
    for stage, channel in (("measurement", "shell"), ("reference", "core_reference")):
        exported = _export(case, stage)
        for row in exported["observations"]:
            assert row["provenance"]["semantics"] == "observed"
            assert row["identity"]["execution_id"] == "fixture-acquisition:" + channel
            assert "verification_id" not in row
    bundle = case["session"].workbench.get_bundle(case["summary"]["bundle_id"])
    assert bundle["steps"][0]["result"]["data"]["recording"]["origin"] == "measured"
    assert bundle["steps"][0]["result"]["authority"] == workflow.AUTHORITY
    assert bundle["verification"]["independent"] is False
    assert bundle["steps"][0]["result"]["data"]["evaluation"]["calibration_authenticity"] == "not_established"


def test_missing_reference_stays_indeterminate_through_net_compare(tmp_path, capsys):
    case = _make_case(tmp_path / "missing-reference", _source(core_values=(None, None, None)))
    left, right = tmp_path / "posterior.json", tmp_path / "reference.json"
    save_new(left, _export(case))
    save_new(right, _export(case, "reference"))
    assert main(["compare", str(left), str(right), "--atol", "5", "--json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"]["status"] == "INDETERMINATE"
    assert result["outcome"]["reason"] == "missing_sample"


def test_actual_replay_exports_fresh_execution_same_numeric_content(measured_case, tmp_path):
    before = measured_case["path"].read_bytes()
    left = _export(measured_case)
    with scientific.open_workspace(measured_case["path"], tmp_path / "replay") as session:
        replay = scientific.replay(session, measured_case["summary"]["bundle_id"], repositories={})
    path = tmp_path / "replay/workspace.json"
    right = export_observations(path, expected_sha256=bytes_ref(path.read_bytes()),
        bundle_id=replay["bundle"]["bundle_id"], stage="posterior", entity_id=measured_case["source"]["asset_id"])
    for old, new in zip(left["observations"], right["observations"]):
        assert old["identity"]["execution_id"] != new["identity"]["execution_id"]
        assert old["record_digest"] != new["record_digest"]
        assert old["value"] == new["value"]
        assert old["uncertainty"] == new["uncertainty"]
        assert replay["bundle"]["bundle_id"] in new["provenance"]["sources"]
    assert compare(left["observations"], right["observations"], atol=0)["outcome"]["status"] == "PASS"
    assert measured_case["path"].read_bytes() == before


def test_net_science_run_export_compare_and_create_only(tmp_path, capsys):
    seed = Session(make_demo_run(), tmp_path / "seed")
    seed_path = seed.save_workspace(tmp_path / "seed/workspace.json")
    seed_bytes = seed_path.read_bytes()
    source = _source()
    source_path = tmp_path / "source.json"
    source_path.write_bytes(canonical(source) + b"\n")
    output_dir = tmp_path / "executed"
    assert main(["science", "run", "--workspace", str(seed_path), "--kind", "measured-thermal",
                 "--source", str(source_path), "--label", "Simulated export CLI test",
                 "--output-dir", str(output_dir), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    workspace = output_dir / "workspace.json"
    workspace_bytes = workspace.read_bytes()
    posterior, reference = tmp_path / "posterior.json", tmp_path / "reference.json"
    args = ["science", "observations", "--workspace", str(workspace),
            "--workspace-sha256", bytes_ref(workspace_bytes), "--bundle", summary["bundle_id"],
            "--stage", "posterior", "--entity", source["asset_id"], "--output", str(posterior)]
    assert main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["schema"] == "ciw.observation-stream.v1"
    assert printed["samples"] == 3
    assert printed["authority"]["new_execution"] == "not_performed"
    posterior_bytes = posterior.read_bytes()
    assert main(args) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert posterior.read_bytes() == posterior_bytes
    args[args.index("--stage") + 1] = "reference"
    args[args.index("--output") + 1] = str(reference)
    assert main(args + ["--json"]) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(reference.read_bytes())
    assert main(["compare", str(posterior), str(reference), "--atol", "5", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"]["status"] == "PASS"
    refused = tmp_path / "refused.json"
    args[args.index("--output") + 1] = str(refused)
    args[args.index("--workspace-sha256") + 1] = "sha256:" + "0" * 64
    assert main(args) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert not refused.exists()
    assert workspace.read_bytes() == workspace_bytes
    assert seed_path.read_bytes() == seed_bytes


def test_existing_synthetic_export_still_refuses_reference(tmp_path, capsys):
    session, summary, path, digest = synthetic_case(tmp_path / "synthetic")
    before = path.read_bytes()
    posterior = export_observations(path, expected_sha256=digest, bundle_id=summary["bundle_id"],
                                   stage="posterior", entity_id="synthetic/core-shell")
    assert posterior["schema"] == "ciw.thermal-observation-view.v1"
    assert posterior["authority"]["reference_truth"] == "not_included"
    with pytest.raises(ValueError, match="no hidden reference-truth projection"):
        export_observations(path, expected_sha256=digest, bundle_id=summary["bundle_id"],
                            stage="reference", entity_id="synthetic/core-shell")
    target = tmp_path / "synthetic-reference.json"
    assert main(["science", "observations", "--workspace", str(path), "--workspace-sha256", digest,
                 "--bundle", summary["bundle_id"], "--stage", "reference", "--entity", "synthetic/core-shell",
                 "--output", str(target)]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert not target.exists()
    assert path.read_bytes() == before
