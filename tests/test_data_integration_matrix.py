"""Cross-family executable evidence, with provider/qualification limits explicit.

These tests exercise native consumers and exact retention, rather than counting
schema names as integration. Local package tests do not establish host runtime
pin qualification. Optional columnar execution is a separately reported gate.
"""
from copy import deepcopy
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from ciw.control_checks import compare
from ciw.control_contracts import observation, record
from ciw.control_plane import ObservationBus
from ciw.core.identities import canonical_json
from ciw.data_field_bridge import observation_stream, validate, wave_observations
from ciw.data_inventory import inventory
from ciw.data_sources import csv_observation_stream, native_record, retain_record
from ciw.data_workflow import COMPARE, comparison_parameters
from ciw.fluid_wave_contract import FIELD_NAMES, example_request, wave_speed_m_per_s
from ciw.instruments import make_demo_run
from ciw.operations.runner import seal
from ciw.session import Session

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-10-05T19:00:00Z"


def _call(session, message, payload):
    answer = session.handle({"protocol_version": 1, "request_id": "family-matrix",
                             "type": message, "payload": payload})
    assert answer["type"] == "response", answer
    return answer["payload"]


def _retain(session, native, family, dataset, *, input_source_ids=None):
    source = retain_record(native, dataset_id=dataset, revision=1, parent_source_id=None,
                           recorded_at=STAMP, data_family=family)
    descriptor = session.workbench.add_source({"kind": "data-record", "label": dataset,
        "bytes_b64": base64.b64encode(canonical_json(source).encode()).decode(),
        **({"input_source_ids": input_source_ids} if input_source_ids else {})})
    return source, descriptor


def _compare_sources(session, left, right, quantity):
    result = _call(session, "operation.execute", {"operation_id": COMPARE,
        "parameters": comparison_parameters(session.workbench, left_source_id=left["source_id"],
            right_source_id=right["source_id"], quantity=quantity, atol=0.0)})
    assert result["status"] == "completed", result
    return result["result"]


@pytest.fixture(scope="module")
def wave():
    from ciw.fluid_wave_solver import simulate
    request = example_request()
    request["integration"]["cells"] = request["integration"]["steps"] = 32
    request["integration"]["duration_s"] = (
        0.1 * request["model"]["length_m"] / wave_speed_m_per_s(request["model"]))
    return request, simulate(request)


def _mapped(wave, field=FIELD_NAMES[0], index=0, **extra):
    request, result = wave
    return wave_observations(request, result, field=field, location_index=index,
                             model_id="wave-model", entity_id="channel", **extra)


def test_inventory_uses_actual_registries_and_never_executes(monkeypatch):
    from ciw.workbench import OPERATIONS
    from ciw.operations.registry import default_registry
    from ciw.operator_provision import catalog
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only inventory executed or probed a provider")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr("ciw.fluid_wave_solver.simulate", forbidden)
    monkeypatch.setattr("ciw.system_models.simulate", forbidden)
    report = inventory(repository=ROOT)
    assert report["read_only"] is True and report["authorizes_execution"] is False
    assert report["qualification"] == "not_performed_by_inventory"
    sources = {row["kind"]: row for row in report["sources"]}
    assert all(row["parser"] and row["source_schema"] for row in sources.values())
    assert {name: row["operation_id"] for name, row in sources.items() if row["operation_id"]} == OPERATIONS
    assert {row["operation_id"] for row in report["operations"]} == {
        row["operation_id"] for row in default_registry().describe()}
    assert {"system-specification", "reference-evidence", "geographic-context", "data-record"} <= sources.keys()
    assert all(sources[k]["consumer"] is None for k in (
        "system-specification", "reference-evidence", "geographic-context", "data-record"))
    assert sources["project-graph"]["available_in_session"] is True
    assert sources["mesh-path"]["available_in_session"] is False
    requirements = {row["workflow"]: row for row in catalog()["workflows"]}
    assert sources["mesh-path"]["providers"] == requirements["mesh-path"]["providers"]
    for route in report["family_routes"]:
        assert route["retention"] and route["parser"] and route["consumers"] and route["limits"]
        assert all(route["callable_checks"].values()), route
        assert route["executable_evidence"]["status"] == "test_references_not_run_by_inventory"
        assert all((ROOT / path).is_file() for path in route["executable_evidence"]["tests"]), route
    families = {family for route in report["family_routes"] for family in route["families"]}
    assert {"tables", "time-series", "geometry", "grid-fields", "graphs", "simulation"} <= families
    report["sources"].clear()
    assert inventory(repository=ROOT)["sources"]


def test_inventory_reports_current_session_and_preserves_explicit_workload_boundary(tmp_path):
    from ciw.fluid_workflow import registry
    session = Session(make_demo_run(), tmp_path, operations=registry())
    report = inventory(repository=ROOT, session=session)
    ids = {row["operation_id"] for row in report["operations"]}
    assert "fluid.wave.simulate.v1" in ids
    assert all(row["registration"] == "current_session" for row in report["operations"])
    base = inventory(repository=ROOT)
    assert "fluid.wave.simulate.v1" not in {row["operation_id"] for row in base["operations"]}
    assert "fluid.wave.simulate.v1" in {row["operation_id"] for row in base["explicit_workload_operations"]}
    assert not session.results and not session.executions


def test_inventory_all_consolidated_packages_are_source_inventory_not_binding():
    report = inventory(repository=ROOT)
    manifest = json.loads((ROOT / "instruments/manifest.json").read_text())
    packages = report["local_packages"]["packages"]
    assert {row["id"] for row in packages} == {row["id"] for row in manifest["modules"]}
    assert len(packages) == len(manifest["modules"])
    for row in packages:
        assert row["source_present"] is True
        assert row["binding_status"] == "source_presence_does_not_install_or_bind"
        assert row["execution_evidence"] == "not_executed_by_inventory"
    calibration = next(row for row in packages if row["id"] == "calibration")
    assert "calibrated-observable" in calibration["host_workflows"]
    assert calibration["package_metadata"]["requires_python"]


@pytest.mark.parametrize("path", ["/tmp/outside", "instruments/../../outside", "src/ciw"])
def test_inventory_refuses_escaping_local_package_paths(tmp_path, path):
    (tmp_path / "instruments").mkdir()
    (tmp_path / "instruments/manifest.json").write_text(json.dumps({
        "schema": "notations.monorepo-imports.v1", "modules": [{"id": "bad", "path": path}]}))
    with pytest.raises(ValueError, match="path"):
        inventory(repository=tmp_path)


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_native_wave_field_runs_through_exact_grid_mapping_and_existing_comparison(wave, field):
    mapped = _mapped(wave, field)
    stream = observation_stream(mapped)
    request, native = wave
    grid = "cell_x_m" if field in {"free_surface_elevation_m", "bottom_gauge_pressure_pa"} else "face_x_m"
    assert mapped["native_request"] == request and mapped["native_result"] == native
    assert mapped["mapping"]["location"]["coordinate_array"] == grid
    assert mapped["mapping"]["location"]["x_m"] == native["primary"][grid][0]
    assert [row["value"] for row in stream["observations"]] == [row[0] for row in native["primary"][field]]
    assert [row["clock"]["time_s"] for row in stream["observations"]] == native["primary"]["time_s"]
    assert all(row["provenance"]["semantics"] == "simulated" for row in stream["observations"])
    result = compare(stream["observations"], stream["observations"], atol=0.0)
    assert result["outcome"]["status"] == "PASS" and result["outcome"]["metrics"]["max_abs_error"] == 0.0
    assert result["claim_scope"] == "declared_numerical_condition_only"
    assert mapped["authority"]["state_admission"] == "not_performed"


def test_field_mapping_retention_real_operation_and_exact_offline_reopen(wave, tmp_path):
    mapping = _mapped(wave)
    session = Session(make_demo_run(), tmp_path / "original")
    wrapped, mapping_source = _retain(session, mapping, "field-observation-mapping", "wave-grid")
    stream = observation_stream(mapping)
    _, left = _retain(session, stream, "observation-stream", "sampled-wave",
                      input_source_ids=[mapping_source["source_id"]])
    _, right = _retain(session, stream, "observation-stream", "reference-wave",
                       input_source_ids=[mapping_source["source_id"]])
    first = _compare_sources(session, left, right, FIELD_NAMES[0])
    assert first["data"]["comparison"]["outcome"]["status"] == "PASS"
    assert first["verification_status"] == "not_verified"
    from ciw.dependency_graph import artifact_graph
    graph = artifact_graph(session.run, session.results, session.executions, session.workbench)
    assert mapping_source["source_id"] in graph[left["source_id"]]["dependencies"]
    assert left["source_id"] in graph[first["result_id"]]["dependencies"]
    path = session.save_workspace(tmp_path / "workspace.json")
    original_bytes = path.read_bytes()
    with patch("ciw.fluid_wave_solver.simulate", side_effect=AssertionError("offline solve")), \
         patch("ciw.fluid_wave_verification.verify", side_effect=AssertionError("offline verification")), \
         patch("ciw.session.execute_operation", side_effect=AssertionError("offline operation")):
        reopened = Session.from_workspace(path, tmp_path / "reopened")
        retained = _call(reopened, "data.get", {"source_id": mapping_source["source_id"]})
        assert retained == wrapped
        assert native_record(retained) == mapping
    assert reopened.results == session.results
    assert reopened.workbench.serialize() == session.workbench.serialize()
    assert path.read_bytes() == original_bytes
    second = _compare_sources(reopened, left, right, FIELD_NAMES[0])
    assert second["data"] == first["data"]
    assert second["execution_id"] != first["execution_id"]
    assert second["result_id"] != first["result_id"]


def test_field_sites_and_trace_clocks_cannot_be_silently_conflated(wave):
    zero = observation_stream(_mapped(wave, index=0))["observations"]
    one = observation_stream(_mapped(wave, index=1))["observations"]
    assert compare(zero, one, atol=1e10)["outcome"]["status"] == "INDETERMINATE"
    refined = observation_stream(_mapped(wave, index=0, trace="time_refined"))["observations"]
    result = compare(zero, refined, atol=1e10)
    assert result["outcome"]["reason"] == "time_grid_mismatch"


@pytest.mark.parametrize("index", [True, -1, 32, 0.0])
def test_field_mapping_refuses_nonexact_or_absent_locations(wave, index):
    with pytest.raises(ValueError, match="location_index"):
        _mapped(wave, index=index)


@pytest.mark.parametrize("mutation", ["coordinate", "value", "clock", "unit", "semantics", "authority"])
def test_resealed_field_mapping_forgery_refuses(wave, mutation):
    mapping = _mapped(wave)
    if mutation == "coordinate":
        mapping["mapping"]["location"]["coordinate_array"] = "face_x_m"
    elif mutation == "authority":
        mapping["authority"]["physical_validation"] = "established"
    else:
        row = mapping["observation_stream"]["observations"][0]
        if mutation == "value":
            row["value"] += 1.0
        elif mutation == "clock":
            row["clock"]["time_s"] += 0.1
        elif mutation == "unit":
            row["unit"] = "mm"
        else:
            row["provenance"]["semantics"] = "observed"
        seal(row)
        seal(mapping["observation_stream"])
    seal(mapping)
    with pytest.raises(ValueError):
        validate(mapping)
    with pytest.raises(ValueError):
        retain_record(mapping, dataset_id="forged", revision=1, parent_source_id=None,
                      recorded_at=STAMP, data_family="field-observation-mapping")


def test_source_field_corruption_and_no_numerics_during_inspection(wave):
    mapping = _mapped(wave)
    with patch("ciw.fluid_wave_solver.simulate", side_effect=AssertionError("solve")), \
         patch("ciw.fluid_wave_verification.verify", side_effect=AssertionError("verify")):
        assert validate(mapping) == mapping
        mapping["native_result"]["primary"]["cell_x_m"][0] += 1.0
        seal(mapping["native_result"])
        seal(mapping)
        with pytest.raises(ValueError):
            validate(mapping)


def test_csv_table_multiple_quantities_executes_named_consumer_without_column_loss(tmp_path):
    raw = b"time,temperature,pressure\n0,300,101000\n1,301,101050\n"
    source = csv_observation_stream(raw, mapping={
        "identity": {"model_id": "fixture", "entity_id": "pipe", "execution_id": None},
        "clock": {"id": "sample-clock", "column": "time", "unit": "s"}, "frame": "pipe-frame",
        "provenance": {"provider": "fixture-log", "semantics": "observed"},
        "quantities": [{"column": "temperature", "quantity": "temperature", "unit": "K"},
                       {"column": "pressure", "quantity": "pressure", "unit": "Pa"}]},
        dataset_id="table", revision=1, parent_source_id=None, recorded_at=STAMP)
    session = Session(make_demo_run(), tmp_path)
    left = session.workbench.add_source({"kind": "data-record", "label": "table",
        "bytes_b64": base64.b64encode(canonical_json(source).encode()).decode()})
    _, right = _retain(session, source["native_record"], "observation-stream", "reference")
    for quantity in ("temperature", "pressure"):
        result = _compare_sources(session, left, right, quantity)["data"]
        assert result["comparison"]["outcome"]["status"] == "PASS"
        assert result["inputs"]["left"]["selected_observation_count"] == 2
        assert result["inputs"]["left"]["unselected_observation_count"] == 2
    assert base64.b64decode(source["source_bytes"]["bytes_b64"]) == raw
    assert len(source["native_record"]["observations"]) == 4


def test_native_missing_values_survive_retention_and_produce_indeterminate_comparison(tmp_path):
    items = [observation(identity={"model_id": "m", "entity_id": "e", "execution_id": None},
        clock={"id": "clock", "time_s": float(i)}, frame="f", quantity="x", value=value, unit="m",
        provenance={"provider": "fixture", "semantics": "observed", "sources": ["sha256:" + "1" * 64]})
        for i, value in enumerate([1.0, None, 2.0])]
    native = record("observation-stream", observations=items)
    session = Session(make_demo_run(), tmp_path / "live")
    _, left = _retain(session, native, "observation-stream", "missing")
    _, right = _retain(session, native, "observation-stream", "reference")
    result = _compare_sources(session, left, right, "x")
    assert result["data"]["comparison"]["outcome"]["reason"] == "missing_sample"
    path = session.save_workspace(tmp_path / "saved.json")
    reopened = Session.from_workspace(path, tmp_path / "reopened")
    retained = native_record(reopened.workbench.get_source(left["source_id"]))
    assert retained == native and retained["observations"][1]["value"] is None


def test_synthetic_recording_consumes_native_statistics_and_cannot_become_observed(tmp_path):
    from ciw.control_plane import observations_from_run
    run = make_demo_run()
    channel = next(iter(run["channels"]))
    session = Session(run, tmp_path)
    statistics = _call(session, "operation.execute", {"operation_id": "statistics.v1",
        "parameters": {"channel": channel, "interval_s": [0.0, run["metadata"]["duration_s"]]}})
    assert statistics["status"] == "completed"
    fields = dict(channel=channel, entity_id="oscillator", clock_id="model-clock", model_id="oscillator")
    with pytest.raises(ValueError, match="Observed projection"):
        observations_from_run(run, semantics="observed", **fields)
    values = observations_from_run(run, semantics="simulated", **fields)
    assert len(values) == len(run["time_s"])
    assert compare(values, values, atol=0.0)["outcome"]["status"] == "PASS"


def test_project_graph_native_consumer_retains_declarations_without_running_them(tmp_path):
    from ciw import project_model, project_workflow
    value = project_model.create("project:matrix", "Matrix project")
    value = project_model.put(value, {"object_id": "computation:declared", "kind": "computation",
        "label": "Declared solver", "content": {"operation": "ciw.encoder-position.v1", "parameters": {}}})
    raw = canonical_json({"schema": project_workflow.SOURCE_SCHEMA, "experiment_id": "project:matrix",
        "configuration": project_workflow.CONFIGURATION, "project": value}).encode()
    session = Session(make_demo_run(), tmp_path / "live")
    source = session.workbench.add_source({"kind": "project-graph", "label": "project",
        "bytes_b64": base64.b64encode(raw).decode()})
    result = _call(session, "operation.execute", {"operation_id": project_workflow.OPERATION,
        "parameters": {"source_id": source["source_id"]}})
    bundle = session.workbench.get_bundle(result["bundle_id"])
    assert bundle["steps"][0]["result"]["data"]["authority"]["execution"] == "not_performed"
    path = session.save_workspace(tmp_path / "project.json")
    with patch.object(project_workflow.ProjectGraphWorkflow, "create_session", side_effect=AssertionError("offline graph")):
        reopened = Session.from_workspace(path, tmp_path / "reopened")
    assert reopened.workbench.get_bundle(result["bundle_id"]) == bundle


def test_system_simulation_runs_actual_model_and_retains_exact_offline_results(tmp_path):
    from ciw import system_workflow
    from ciw.system_spec import demo_spec
    spec = demo_spec(cells=8)
    session = Session(system_workflow.source_run([spec]), tmp_path / "live")
    result = system_workflow.run_specification(session, spec)
    assert {row["operation_id"] for row in session.results.values()} == {
        system_workflow.COMPILE, system_workflow.RUN, system_workflow.VERIFY}
    assert result["candidate"]["data"]["simulation"]["canonical_admission"] is False
    path = session.save_workspace(tmp_path / "system.json")
    with patch("ciw.system_models.simulate", side_effect=AssertionError("offline solve")), \
         patch("ciw.system_models.verify_simulation", side_effect=AssertionError("offline verify")):
        reopened = Session.from_workspace(path, tmp_path / "reopened")
    assert reopened.results == session.results and reopened.executions == session.executions


def test_consolidated_translation_geometry_result_consumed_by_host_contract():
    """Package execution plus host result validation, not host pin qualification."""
    from ciw.geometry_translation_contract import validate_result
    source = json.loads((ROOT / "examples/geometry-research/translation-flow.json").read_text())
    package = ROOT / "instruments/mathematics/polygon-trajectories/src"
    script = "import json,sys; from translation_surface_dynamics import run; print(json.dumps(run(json.load(sys.stdin))))"
    completed = subprocess.run([sys.executable, "-c", script], input=json.dumps(source["request"]),
        text=True, capture_output=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(package)}, timeout=30)
    assert completed.returncode == 0, completed.stderr
    native = json.loads(completed.stdout)
    validate_result(source["request"], native)
    assert native["status"] == "completed"
    assert native["final_state"]["position"] == ["1/4", "5/6"]
    assert native["elapsed"] == "3"


def test_consolidated_compute_runtime_table_and_graph_actual_canonical_consumers():
    """Independent local-package path, without pretending it is a Session binding."""
    package = ROOT / "instruments/execution/compute-runtime"
    script = r'''
import json
from adapters.csv_adapter import CSVAdapter, parse_csv_rows
from adapters.interface import ExternalRecord, build_candidate_delta
from adapters.json_adapter import JSONAdapter, infer_schema_from_record
from core.canonical.state import CanonicalState
from core.canonical.validation import validate_candidate
from core.projection.project import project_state
from morpho.compiler import CompilerConfig, compile_morpho
raw = {'A': {'value': 1.0, 'unit': 'm'}, 'B': 2.0,
       'relationships': [{'from': 'A', 'to': 'B', 'type': 'depends_on'}]}
record = ExternalRecord(raw=raw, source='matrix-native')
schema = infer_schema_from_record(record, schema_version='matrix-1', edge_types=('depends_on',))
base = CanonicalState(schema_version=schema.schema_version, fields={}, edges=())
candidate = build_candidate_delta(JSONAdapter(), record, None, 'tx-matrix', '2026-10-05T19:00:00Z')
version = validate_candidate(schema, base, candidate)
assert not isinstance(version, list), version
ir = compile_morpho(project_state(version), CompilerConfig())
rows = parse_csv_rows('A,B\n1,2\n', source='matrix.csv')
changes = CSVAdapter().normalize(rows[0])
assert {change.new_value['id']: change.new_value['value'] for change in changes} == {'A': 1, 'B': 2}
assert all(change.new_value['unit'] is None for change in changes)
print(json.dumps({'unit': version.state.fields['A'].unit, 'relation_count': len(ir.relations),
                  'canonical': ir.relations[0].is_canonical, 'csv_values': [c.new_value['value'] for c in changes]}))
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=package,
        env={**os.environ, "PYTHONPATH": str(package)}, text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"unit": "m", "relation_count": 1, "canonical": True, "csv_values": [1, 2]}


@pytest.mark.parametrize("format", ["ipc", "parquet"])
def test_optional_columnar_roundtrip_preserves_all_native_observation_identities(format):
    pytest.importorskip("pyarrow", reason="Optional qualified Arrow/Parquet runtime is not installed")
    from ciw.columnar import encode, decode
    bus = ObservationBus()
    for i in range(3):
        bus.observe(identity={"model_id": "columnar", "entity_id": "sample", "execution_id": None},
            clock={"id": "clock", "time_s": float(i)}, frame="laboratory", quantity="distance",
            value=float(i), unit="m", provenance={"provider": "fixture", "semantics": "observed",
                "sources": ["sha256:" + "1" * 64]})
    stream = bus.snapshot()
    payload, artifact = encode(stream, format=format, experiment_id="matrix-columnar")
    assert decode(payload, artifact, format=format) == stream
    with pytest.raises(ValueError):
        decode(payload + b"corruption", artifact, format=format)


def test_correlated_covariance_keeps_ordered_axes_and_all_offdiagonal_terms_on_reopen(tmp_path):
    from ciw.core.covariance import create_covariance_artifact
    covariance = create_covariance_artifact(
        quantity_ids=["x", "y"], units=["m", "m"], frame="specimen",
        reference_values=[1.0, 2.0], matrix=[[1.0, 0.75], [0.75, 2.0]],
        method="declared_correlated_coordinate_uncertainty", basis={"kind": "coordinate", "id": "xy"},
        provenance={"provider": "fixture", "source_evidence_ids": ["sha256:" + "1" * 64],
                    "source_covariance_ids": [], "metadata": {"physical_validation": "not_established"}},
        assumptions=["Declared covariance, no independent sensor claim"])
    session = Session(make_demo_run(), tmp_path / "live")
    _, descriptor = _retain(session, covariance, "covariance", "correlated-xy")
    path = session.save_workspace(tmp_path / "covariance.json")
    reopened = Session.from_workspace(path, tmp_path / "reopened")
    result = native_record(reopened.workbench.get_source(descriptor["source_id"]))
    assert result == covariance and result["matrix"][0][1] == result["matrix"][1][0] == 0.75
    assert result["quantity_ids"] == ["x", "y"]
    # Schema retention does not turn a covariance matrix into a time series.
    request = comparison_parameters(reopened.workbench, left_source_id=descriptor["source_id"],
        right_source_id=descriptor["source_id"], quantity="x", atol=0.0)
    refusal = _call(reopened, "operation.execute", {"operation_id": COMPARE, "parameters": request})
    assert refusal["status"] == "refused" and refusal["result"] is None


@pytest.mark.parametrize("target", ["location_index", "time_s", "value", "sample_count"])
def test_field_mapping_refuses_boolean_integer_aliases_even_after_outer_reseal(wave, target):
    mapping = _mapped(wave)
    if target == "location_index":
        mapping["mapping"]["location"]["location_index"] = False
    elif target == "sample_count":
        mapping["mapping"]["sample_count"] = float(mapping["mapping"]["sample_count"])
    else:
        row = mapping["observation_stream"]["observations"][0]
        if target == "time_s":
            row["clock"]["time_s"] = False
        else:
            # Preserve the numeric value while changing the strict JSON type.
            # This is only possible when a source is exactly zero, so test a
            # deliberately wrong bool rather than replacing a nonzero sample.
            row["value"] = False
    seal(mapping)
    with pytest.raises(ValueError):
        validate(mapping)


def test_default_native_wave_full_source_fits_and_reopens_without_truncation(tmp_path):
    from ciw.fluid_wave_solver import simulate
    from ciw.data_sources import MAX_BYTES
    from ciw.workbench import MAX_BYTES as WORKBENCH_BYTES
    request = example_request()
    result = simulate(request)
    # Exercise the shipped 64x64 example, whose full refinement payload is
    # larger than the previous 8 MiB source envelope limit.
    assert len(canonical_json(result).encode()) > 8 * 1024 * 1024
    mapping = wave_observations(request, result, field=FIELD_NAMES[0], location_index=0,
                                model_id="default-wave", entity_id="channel")
    session = Session(make_demo_run(), tmp_path / "live")
    source, retained = _retain(session, mapping, "field-observation-mapping", "default-native-field")
    assert len(canonical_json(source).encode()) < MAX_BYTES
    assert len(canonical_json(session.workbench.serialize()).encode()) < WORKBENCH_BYTES
    stream = observation_stream(mapping)
    _, sampled = _retain(session, stream, "observation-stream", "default-field-sample",
                         input_source_ids=[retained["source_id"]])
    consumed = _compare_sources(session, sampled, sampled, FIELD_NAMES[0])
    assert consumed["data"]["comparison"]["outcome"]["status"] == "PASS"
    # Only the small derived stream enters operation parameters.
    assert len(canonical_json(consumed["parameters"]).encode()) < 512 * 1024
    path = session.save_workspace(tmp_path / "default-wave.json")
    with patch("ciw.fluid_wave_solver.simulate", side_effect=AssertionError("offline physics")), \
         patch("ciw.session.execute_operation", side_effect=AssertionError("offline operation")):
        reopened = Session.from_workspace(path, tmp_path / "reopened")
        recovered = native_record(reopened.workbench.get_source(retained["source_id"]))
    assert canonical_json(recovered) == canonical_json(mapping)
    assert recovered["native_result"] == result
    assert reopened.results == session.results
