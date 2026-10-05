"""Native contract/transport tests, not scientific or real-world qualification."""
import base64
from copy import deepcopy
import json

import pytest

from ciw import data_sources as data
from ciw.adapters.protocol import AdapterRefusal
from ciw.check_suite import evaluate, plan
from ciw.control_checks import compare, verify
from ciw.control_contracts import artifact, bytes_ref, observation, record, state
from ciw.control_plane import Fixed, ParameterSpace, builtin_registry, experiment, run_graph
from ciw.core.covariance import create_covariance_artifact, covariance_identity
from ciw.core.identities import canonical_json, content_identity
from ciw.instruments import make_demo_run
from ciw.math_inspector import analyze, analyze_information
from ciw.operations.runner import seal
from ciw.polymer_contract import example_request
from ciw.scientific_observations import export_observations
from ciw.session import Session

REF = content_identity({"fixture": "data-boundary-contract"})
IDENTITY = {"model_id": "contract-model.v1", "entity_id": "test/body", "execution_id": None}
PROVENANCE = {"provider": "contract-fixture", "sources": [REF], "semantics": "simulated"}
LINEAGE = {"dataset_id": "retained/test", "revision": 1, "parent_source_id": None,
           "recorded_at": "2026-10-05T12:00:00+00:00"}
BASE_SCHEMAS = tuple(key for key in data.SCHEMA_FAMILIES if key not in {"ciw.field-observation-mapping.v1", "ciw.compute-canonical-state.v1", "ciw.hoomd-study.v1", "ciw.hoomd-result.v1"})


def encoded(value):
    return canonical_json(value).encode()


def reseal_source(value):
    value["source_id"] = content_identity({k: v for k, v in value.items() if k != "source_id"})
    return value


def obs(value=2, time=0):
    return observation(identity=IDENTITY, clock={"id": "test/clock", "time_s": time},
                       frame="test/world", quantity="position", value=value, unit="m", provenance=PROVENANCE)


def cov():
    return create_covariance_artifact(matrix=[[4., 1.5], [1.5, 3.]],
        quantity_ids=["position[0]", "position[1]"], units=["m", "m"], frame="test/world",
        reference_values=[1., 2.], method="declared correlated fixture",
        basis={"kind": "estimated_state", "id": "test-state"},
        provenance={"provider": "fixture", "source_evidence_ids": [REF], "source_covariance_ids": []},
        assumptions=["Synthetic contract test; no physical calibration"])


def typed_state():
    return state(identity=IDENTITY, clock={"id": "test/clock", "time_s": 0}, frame="test/world",
                 variables={"position": {"unit": "m", "value": [1., 2.]}},
                 provenance=PROVENANCE, uncertainty=cov())


def retain(value, **kwargs):
    schema = value["schema"] if type(value) is dict else json.loads(value)["schema"]
    return data.retain_record(value, data_family=data.SCHEMA_FAMILIES[schema], **{**LINEAGE, **kwargs})


@pytest.fixture(scope="module")
def native_records(tmp_path_factory):
    """Real existing builders/validators for every core schema, without mocks."""
    root = tmp_path_factory.mktemp("data-native-contracts")
    stream = record("observation-stream", observations=[obs(), obs(None, 1)])
    specification = plan("retained-checks", inputs={"samples": {
        "schema": stream["schema"], "sha256": bytes_ref(encoded(stream))}}, checks=[
        {"name": "bounds", "kind": "bounded", "input": "samples",
         "policy": {"minimum": 0, "maximum": 5, "unit": "m"}}])
    graph = experiment("data-graph", model_id="analytic-damped-oscillator.v1", nodes=[
        {"node_id": "statistics", "operation_id": "statistics.v1", "parameters": {},
         "inputs": {}, "depends_on": []}])
    registry = builtin_registry(bind=True)
    session = Session(make_demo_run(), root / "graph", operations=registry.operations)
    graph_run = run_graph(session, graph, registry)
    assert graph_run["status"] == "completed"
    # Existing thermal workflow produces real retained Python numerical results.
    # Their test fixture is explicitly synthetic, not hardware/native-engine evidence.
    from test_scientific_observations import make_case
    _, summary, path, digest = make_case(root / "thermal")
    view = export_observations(path, expected_sha256=digest, bundle_id=summary["bundle_id"],
                               stage="posterior", entity_id="test/core-shell")
    raw_view = encoded(view)
    checkpoint = record("checkpoint", experiment_id="test", provider={
        "runtime": {"provider": "fixture", "revision": "1"}, "model_id": "model.v1",
        "simulation_id": "test-simulation", "owner_id": "test-owner", "state_revision": 0,
        "clock": {"id": "test/clock", "time_s": 0}}, sha256=bytes_ref(b"opaque"), size_bytes=6)
    values = [typed_state(), obs(), stream, artifact(b"bytes", media_type="application/octet-stream",
        producer="fixture", experiment_id="test"), compare([obs()], [obs()], atol=0),
        verify("bounded", "bounded", [obs()], minimum=0, maximum=3, unit="m"),
        ParameterSpace({"length": Fixed(2, "m")}).to_dict(), graph, graph_run, checkpoint,
        specification, evaluate(specification, {"samples": encoded(stream)}), view,
        analyze(raw_view, expected_sha256=bytes_ref(raw_view)),
        analyze_information(raw_view, expected_sha256=bytes_ref(raw_view)), cov(), example_request("injection_molding")]
    from test_product_study import fixture as product_study_fixture
    values.append(product_study_fixture())
    from ciw.manufacturing_runtime import evaluate as manufacture
    from ciw.manufacturing_example import example_request as manufacturing_example
    from ciw.polymer_quality_example import manufacturing_request
    from ciw.toolpoint_stream_example import demo as stream_example
    values.extend([manufacture(manufacturing_example()), manufacture(manufacturing_request()),
                   stream_example(case="health-recovery")])
    result = {value["schema"]: value for value in values}
    assert set(result) == set(BASE_SCHEMAS)
    return result


@pytest.mark.parametrize("schema", BASE_SCHEMAS)
def test_every_supported_native_contract_roundtrips_unchanged(native_records, schema):
    native = native_records[schema]
    raw = (json.dumps(native, indent=2, ensure_ascii=False) + "\n").encode()
    wrapper = retain(raw)
    assert data.native_record(wrapper) == native
    assert base64.b64decode(wrapper["source_bytes"]["bytes_b64"]) == raw
    assert wrapper["source_bytes"]["sha256"] == bytes_ref(raw)
    assert data._source(encoded(wrapper)) == wrapper
    assert wrapper["adapter_receipt"] is None
    descriptor = data.descriptor(wrapper)
    assert descriptor["native_schema"] == schema
    assert descriptor["native_record_sha256"] == bytes_ref(encoded(native))
    assert descriptor["physical_validation"] == descriptor["state_admission"] == "not_performed"
    assert "native_record" not in descriptor


@pytest.mark.parametrize("schema", BASE_SCHEMAS)
def test_every_native_route_really_runs_its_validator(native_records, schema):
    native = deepcopy(native_records[schema])
    # Bypass content-seal failure where present, then demand structural rejection.
    if schema == "covariance-artifact.v1":
        native.pop("matrix")
        native["covariance_id"] = covariance_identity(native)
    else:
        field = next(k for k in native if k not in {"schema", "record_digest"})
        native.pop(field)
        if "record_digest" in native:
            seal(native)
    with pytest.raises(ValueError):
        retain(native)


def test_full_covariance_missingness_and_detachment():
    native = typed_state()
    wrapper = retain(native)
    native["uncertainty"]["matrix"][0][1] = 999
    loaded = data.native_record(wrapper)
    assert loaded["uncertainty"] == cov()
    assert loaded["uncertainty"]["matrix"][0][1] == 1.5
    loaded["variables"]["position"]["value"][0] = 999
    assert data.native_record(wrapper)["variables"]["position"]["value"] == [1., 2.]
    for missing in (None, [1, None]):
        assert data.native_record(retain(obs(missing)))["value"] == missing


@pytest.mark.parametrize("mutation", ["unit", "frame", "axes", "partial", "off_diagonal", "reference"])
def test_native_covariance_binding_cannot_be_relaxed(mutation):
    native = typed_state()
    covariance = native["uncertainty"]
    if mutation == "unit": native["variables"]["position"]["unit"] = "K"
    elif mutation == "frame": native["frame"] = "other/world"
    elif mutation == "axes": covariance["quantity_ids"] = ["a", "b"]
    elif mutation == "partial": native["variables"]["velocity"] = {"unit": "m/s", "value": 1}
    elif mutation == "off_diagonal": covariance["matrix"][0][1] = 999
    else: covariance["reference_values"][0] = 77
    covariance["covariance_id"] = covariance_identity(covariance)
    seal(native)
    with pytest.raises(ValueError): retain(native)


@pytest.mark.parametrize("change", [
    {"revision": True}, {"revision": 0}, {"revision": -1}, {"revision": 1.0},
    {"revision": 2**53}, {"dataset_id": ""}, {"parent_source_id": "source:not-an-envelope-id"},
    {"recorded_at": "2026-10-05T12:00:00"}, {"recorded_at": "not-a-date"},
])
def test_bad_lineage_metadata_refused(change):
    with pytest.raises(ValueError): retain(obs(), **change)


def test_valid_revision_declarations_do_not_claim_parent_resolution():
    previous = retain(obs())
    next_value = retain(obs(3), revision=2, parent_source_id=previous["source_id"],
                        recorded_at="2026-10-05T12:01:00+00:00")
    assert next_value["source_id"] != previous["source_id"]
    assert data.descriptor(next_value)["parent_source_id"] == previous["source_id"]


@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b'{"schema":1,"schema":2}', b"NaN", b'"\\ud800"'])
def test_non_native_or_ambiguous_json_refused(raw):
    with pytest.raises(ValueError):
        data.retain_record(raw, data_family="observation", **LINEAGE)


def test_family_schema_units_and_time_are_explicit():
    with pytest.raises(ValueError, match="belongs to data family"):
        data.retain_record(obs(), data_family="state", **LINEAGE)
    with pytest.raises(AdapterRefusal) as error:
        data.retain_record({"schema": "unrecognized.field.v1"}, data_family="field", **LINEAGE)
    assert error.value.code == "data_family_unsupported"
    for key, value in (("unit", ""), ("frame", ""), ("clock", {"id": "test", "time_s": True})):
        native = obs(); native[key] = value; seal(native)
        with pytest.raises(ValueError): retain(native)
    duplicate = record("observation-stream", observations=[obs(), obs()])
    with pytest.raises(ValueError): retain(duplicate)


@pytest.mark.parametrize("change", ["extra", "schema", "native_schema", "native", "digest", "size", "base64", "boolean_size"])
def test_tampered_source_envelope_refused(change):
    wrapper = retain(obs())
    if change == "extra": wrapper["qualification"] = "passed"
    elif change == "schema": wrapper["schema"] = "ciw.data-source.v99"
    elif change == "native_schema": wrapper["native_schema"] = "ciw.state.v1"
    elif change == "native": wrapper["native_record"] = obs(7)
    elif change == "digest": wrapper["source_bytes"]["sha256"] = "sha256:" + "0" * 64
    elif change == "size": wrapper["source_bytes"]["byte_count"] += 1
    elif change == "boolean_size": wrapper["source_bytes"]["byte_count"] = True
    else: wrapper["source_bytes"]["bytes_b64"] += "\n"
    reseal_source(wrapper)
    with pytest.raises(ValueError): data._source(encoded(wrapper))


def mapping():
    return {"identity": deepcopy(IDENTITY), "clock": {"id": "test/clock", "column": "time", "unit": "s"},
            "frame": "test/world", "provenance": {"provider": "csv-fixture", "semantics": "simulated"},
            "quantities": [{"column": "x", "quantity": "position", "unit": "m"},
                           {"column": "v", "quantity": "velocity", "unit": "m/s"}]}


def adapted(raw=b"time,x,v\r\n0,1,2\r\n1,3,4\r\n", **kwargs):
    return data.csv_observation_stream(raw, mapping=kwargs.get("mapping", mapping()), **LINEAGE)


def test_csv_explicit_mapping_row_conservation_and_replay():
    raw = b"v,time,x\r\n2,0,1\r\n4,1,3\r\n"
    wrapper = adapted(raw)
    rows = data.native_record(wrapper)["observations"]
    assert [row["quantity"] for row in rows] == ["position", "velocity"] * 2
    assert [row["value"] for row in rows] == [1, 2, 3, 4]
    assert [row["clock"]["time_s"] for row in rows] == [0, 0, 1, 1]
    assert [row["unit"] for row in rows] == ["m", "m/s"] * 2
    assert all(row["identity"] == IDENTITY and row["uncertainty"] is None for row in rows)
    assert all(row["provenance"]["sources"] == [bytes_ref(raw)] for row in rows)
    receipt = wrapper["adapter_receipt"]
    assert receipt["source_columns"] == ["v", "time", "x"]
    assert receipt["mapping"] == mapping()
    assert receipt["input_row_count"] == 2
    assert receipt["output_observation_count"] == receipt["input_row_count"] * receipt["observations_per_row"] == 4
    assert receipt["source_sha256"] == bytes_ref(raw)
    assert receipt["target_sha256"] == bytes_ref(encoded(data.native_record(wrapper)))
    assert base64.b64decode(wrapper["source_bytes"]["bytes_b64"]) == raw
    assert data._source(encoded(wrapper)) == wrapper


@pytest.mark.parametrize("raw", [
    b"time,x,v\n0,,1\n", b"time,x,v\n0,null,1\n", b"time,x,v\n0,NaN,1\n",
    b"time,x,v\n0,NA,1\n", b"time,x,v\n0, ,1\n", b"time,x,v\n0,1,2\n\n",
    b"time,x,v\n0,1\n", b"time,x,v\n0,1,2,3\n", b"time,x,v\n0,1e999,2\n",
    b"time,x,v\n0,1e-999,2\n", b"time,x,v\n0,true,2\n", b"time,x,v\n0,1_000,2\n",
    b"time,x,v\n0,1,2\n0,3,4\n", b"time,x,v\n1,1,2\n0,3,4\n",
    b"time,x,v,extra\n0,1,2,3\n", b"time,x\n0,1\n", b"time,x,x\n0,1,2\n",
    b'time,x,v\n0,"1,2\n', b"\xff", b"\xef\xbb\xbftime,x,v\n0,1,2\n",
])
def test_csv_refuses_missing_invalid_duplicate_or_unaccounted_data(raw):
    with pytest.raises(ValueError): adapted(raw)


@pytest.mark.parametrize("change", ["unit", "clock_unit", "missing_identity", "frame", "semantics", "duplicate_quantity", "duplicate_column", "clock_collision", "ignored"])
def test_csv_mapping_refuses_inference_and_collisions(change):
    value = mapping()
    if change == "unit": value["quantities"][0]["unit"] = ""
    elif change == "clock_unit": value["clock"]["unit"] = "ms"
    elif change == "missing_identity": value["identity"].pop("execution_id")
    elif change == "frame": value["frame"] = ""
    elif change == "semantics": value["provenance"]["semantics"] = ""
    elif change == "duplicate_quantity": value["quantities"][1]["quantity"] = "position"
    elif change == "duplicate_column": value["quantities"][1]["column"] = "x"
    elif change == "clock_collision": value["quantities"][0]["column"] = "time"
    else: value["ignored_columns"] = ["metadata"]
    with pytest.raises(ValueError): adapted(mapping=value)


@pytest.mark.parametrize("change", ["rows", "observations", "digest", "mapping", "extra", "native"])
def test_resealed_receipt_is_replayed_not_trusted(change):
    wrapper = adapted()
    receipt = wrapper["adapter_receipt"]
    if change == "rows": receipt["input_row_count"] -= 1
    elif change == "observations": receipt["output_observation_count"] -= 1
    elif change == "digest": receipt["target_sha256"] = "sha256:" + "0" * 64
    elif change == "mapping": receipt["mapping"]["quantities"][0]["unit"] = "K"
    elif change == "extra": receipt["qualified"] = True
    else:
        wrapper["native_record"]["observations"] = wrapper["native_record"]["observations"][:2]
        seal(wrapper["native_record"])
    reseal_source(wrapper)
    with pytest.raises(ValueError, match="receipt or native record contradicts"):
        data._source(encoded(wrapper))


def test_header_only_csv_retains_zero_rows_without_zero_samples():
    wrapper = adapted(b"time,x,v\n")
    assert data.native_record(wrapper)["observations"] == []
    assert wrapper["adapter_receipt"]["input_row_count"] == 0
    assert wrapper["adapter_receipt"]["output_observation_count"] == 0


def test_bounds_refuse_without_truncating(monkeypatch):
    monkeypatch.setattr(data, "MAX_SAMPLES", 3)
    with pytest.raises(ValueError, match="no row was dropped"):
        adapted()
    monkeypatch.setattr(data, "MAX_BYTES", 4)
    with pytest.raises(ValueError, match="bounded"):
        adapted()


def test_workbench_source_accessors_verify_metadata_and_computational_inputs():
    from ciw.workbench import Workbench
    workbench = Workbench()
    def add(value, **extra):
        desc = workbench.add_source({"kind": "data-record", "label": "retained data",
            "bytes_b64": base64.b64encode(encoded(value)).decode(), **extra})
        return workbench.get_source(desc["source_id"])
    first = add(retain(obs()))
    second = add(retain(obs(3), dataset_id="derived/test"), input_source_ids=[first["source_id"]])
    assert data.native_record(first) == obs()
    assert data.native_record(second) == obs(3)
    assert data.descriptor(second) == second["data"]
    for key in ("data", "input_source_ids", "source_id"):
        changed = deepcopy(second)
        if key == "data": changed[key]["revision"] = 7
        elif key == "input_source_ids": changed[key] = ["source:unknown"]
        else: changed[key] = "source:unknown"
        with pytest.raises(ValueError): data.native_record(changed)
