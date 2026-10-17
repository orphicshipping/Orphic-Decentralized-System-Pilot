"""Native OIT consumer, safe NPZ mapping and retained Session lifecycle."""
import base64
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import py_compile
import shutil
import zipfile

import numpy as np
import pytest

from ciw import data_rank_bridge as bridge
from ciw.data_sources import native_record
from ciw.instruments import make_demo_run
from ciw.operations.registry import OperationRegistry, default_registry
from ciw.session import Session
from ciw.workbench import Workbench

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "instruments/mathematics/observability/src"
STAMP = "2026-10-05T22:00:00Z"
SPEC = importlib.util.spec_from_file_location("carex_qualification", ROOT / "scripts/qualify_carex_rank_consumer.py")
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


def matrices():
    # All four are synthetic contract fixtures, never claimed as CAREX sources.
    return {"R": np.array([[1]], dtype=np.uint8),
            "Q": np.asfortranarray([[4., 0.], [0., 0.]]),
            "B": np.array([[0.], [2.]], dtype=">f8"),
            "A": np.array([[1., 0.], [0., 1e-14]])}


def archive(values=None):
    stream = io.BytesIO()
    np.savez_compressed(stream, **(matrices() if values is None else values))
    return stream.getvalue()


def rewrite(raw, *, replace=None, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as old, zipfile.ZipFile(stream, "w") as new:
        for info in old.infolist():
            new.writestr(info.filename, (replace or {}).get(info.filename, old.read(info)))
        if extra:
            new.writestr(*extra)
    return stream.getvalue()


def retained(workbench, raw=None, **kwargs):
    return bridge.retain_carex(workbench, archive() if raw is None else raw, dataset_id="synthetic-rank-fixture",
        recorded_at=STAMP, provenance={"class": "synthetic_contract_fixture_not_published_CAREX"}, **kwargs)


def registry(root=NATIVE):
    result = OperationRegistry()
    result.register(bridge.RankRuntime(root).operation())
    return result


def call(session, parameters):
    response = session.handle({"protocol_version": 1, "request_id": "rank-test", "type": "operation.execute",
                               "payload": {"operation_id": bridge.OPERATION, "parameters": parameters}})
    assert response["type"] == "response", response
    return response["payload"]


def test_npz_preserves_dtype_shape_member_order_bytes_and_semantics():
    raw = archive()
    values, manifest = bridge.parse_npz(raw)
    assert list(values) == manifest["archive_member_order"] == list(matrices())
    assert [row["dtype"] for row in manifest["arrays"]] == ["|u1", "<f8", ">f8", "<f8"]
    assert manifest["arrays"][1]["storage_order"] == "F"
    assert manifest["arrays"][0]["quantity"] == "input_cost_weight_matrix"
    assert all(row["unit"] == "not_declared_in_retained_source" for row in manifest["arrays"])
    for key, value in values.items():
        assert value.dtype == matrices()[key].dtype
        assert np.array_equal(value, matrices()[key])
    workbench = Workbench()
    source = retained(workbench, raw)
    parameters = bridge.source_parameters(workbench, source["source_id"])
    assert bridge._source(parameters)[2] == raw
    assert bridge._source(parameters)[1] == manifest
    assert bridge.POLICY["Q_R_semantics"] == "Riccati_cost_weights_not_measurement_covariance"


@pytest.mark.parametrize("bad", [np.array([[True]]), np.array([[1j]]), np.array([["1"]]),
    np.array([[None]], dtype=object), np.array([[(1,)]], dtype=[("a", "i8")]),
    np.array([[np.nan]]), np.array([[np.inf]]), np.zeros((0, 1)), np.zeros((513, 1)),
    np.array([1.]), np.array([[2**53 + 1]], dtype=np.uint64)])
def test_unsupported_array_refuses_without_native_execution(bad, monkeypatch):
    values = matrices()
    values["R"] = bad
    monkeypatch.setattr(bridge, "_bounded_process", lambda *a, **k: pytest.fail("Unexpected execution"))
    with pytest.raises(ValueError):
        bridge.parse_npz(archive(values))


def test_object_archive_cannot_execute_pickle(tmp_path):
    sentinel = tmp_path / "executed"
    class Evil:
        def __reduce__(self):
            return (os.system, ("touch " + str(sentinel),))
    values = matrices()
    values["R"] = np.array([[Evil()]], dtype=object)
    with pytest.raises(ValueError, match="plain real numeric"):
        bridge.parse_npz(archive(values))
    assert not sentinel.exists()


@pytest.mark.parametrize("change", ["duplicate", "traversal", "unknown", "trailing", "truncated", "huge_header", "budget"])
def test_malformed_or_unbounded_archive_refuses(change):
    raw = archive()
    if change in {"duplicate", "traversal", "unknown"}:
        name = {"duplicate": "A.npy", "traversal": "../A.npy", "unknown": "extra.npy"}[change]
        with pytest.warns(UserWarning) if change == "duplicate" else __import__("contextlib").nullcontext():
            raw = rewrite(raw, extra=(name, b"invalid"))
    elif change in {"trailing", "truncated"}:
        with zipfile.ZipFile(io.BytesIO(raw)) as old:
            a = old.read("A.npy")
        raw = rewrite(raw, replace={"A.npy": a + b"x" if change == "trailing" else a[:-1]})
    elif change == "huge_header":
        buffer = io.BytesIO()
        np.lib.format.write_array_header_1_0(buffer, {"descr": "<f8", "fortran_order": False, "shape": (10**12, 10**12)})
        raw = rewrite(raw, replace={"A.npy": buffer.getvalue()})
    else:
        raw = b"x" * (bridge.MAX_ARCHIVE + 1)
    with pytest.raises(ValueError):
        bridge.parse_npz(raw)


def test_carex_dimensions_are_not_silently_repaired():
    values = matrices()
    values["B"] = np.ones((3, 1))
    with pytest.raises(ValueError, match="dimensions"):
        bridge.parse_npz(archive(values))


def test_actual_native_rank_session_reopen_and_explicit_replay(tmp_path, monkeypatch):
    session = Session(make_demo_run(), tmp_path / "live", operations=registry())
    source = retained(session.workbench)
    parameters = bridge.source_parameters(session.workbench, source["source_id"])
    first = call(session, parameters)
    assert first["status"] == "completed", first
    data = first["result"]["data"]
    assert {row["name"]: row["diagnostics"]["rank"] for row in data["native"]["rows"]} == {"R": 1, "Q": 1, "B": 1, "A": 1}
    assert data["native"]["rows"][1]["diagnostics"]["condition_number"] == "positive_infinity"
    assert first["result"]["verification_status"] == "not_verified"
    assert first["execution"]["runtime"]["provider"] == "oit.rank_diagnostics"
    assert first["execution"]["parameters"]["tolerances"] == bridge.TOLERANCES
    path = session.save_workspace(tmp_path / "workspace.json")
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "_bounded_process", lambda *a, **k: pytest.fail("Offline native dispatch"))
        patch.setattr(np.linalg, "svd", lambda *a, **k: pytest.fail("Offline SVD"))
        restored = Session.from_workspace(path, output_dir=tmp_path / "offline")
        assert restored.results == session.results
        assert bridge._source(bridge.source_parameters(restored.workbench, source["source_id"]))[2] == archive()
        refused = call(restored, parameters)
        assert refused["status"] == "refused"
        assert refused["execution"]["refusal"]["code"] == "operation_unavailable"
    restored.operations = registry()
    replay = call(restored, parameters)
    assert replay["status"] == "completed"
    assert replay["result"]["data"] == data
    assert replay["execution"]["execution_id"] != first["execution"]["execution_id"]


def test_foreign_source_or_changed_policy_refuses_before_provider(tmp_path, monkeypatch):
    session = Session(make_demo_run(), tmp_path, operations=registry())
    other = Workbench()
    source = retained(other)
    foreign = bridge.source_parameters(other, source["source_id"])
    monkeypatch.setattr(bridge.RankRuntime, "evaluate", lambda *a, **k: pytest.fail("Unvalidated provider call"))
    assert call(session, foreign)["status"] == "refused"
    local = retained(session.workbench)
    changed = bridge.source_parameters(session.workbench, local["source_id"])
    changed["tolerances"]["rank_rtol"] = 1e-6
    assert call(session, changed)["status"] == "refused"
    changed = bridge.source_parameters(session.workbench, local["source_id"])
    changed["source"]["label"] = "forged"
    assert call(session, changed)["status"] == "refused"


@pytest.mark.parametrize("mutation", ["rank", "manifest", "policy", "source", "condition", "direction"])
def test_saved_result_corruption_rejected_without_svd(mutation, monkeypatch):
    workbench = Workbench()
    source = retained(workbench)
    parameters = bridge.source_parameters(workbench, source["source_id"])
    data = bridge.RankRuntime(NATIVE).execute(parameters)
    if mutation == "rank":
        data["native"]["rows"][0]["diagnostics"]["rank"] = 0
    elif mutation == "manifest":
        data["manifest"]["arrays"][1]["storage_order"] = "C"
    elif mutation == "policy":
        data["policy"]["observability"] = "observable"
    elif mutation == "source":
        data["source_id"] = "sha256:" + "0" * 64
    elif mutation == "condition":
        data["native"]["rows"][1]["diagnostics"]["condition_number"] = 1.0
    else:
        data["native"]["rows"][1]["diagnostics"]["numerical_nullspace"] = []
    monkeypatch.setattr(np.linalg, "svd", lambda *a, **k: pytest.fail("Offline SVD"))
    with pytest.raises(ValueError):
        bridge.validate_payload(bridge.OPERATION, data, {}, parameters, {})


@pytest.mark.parametrize("attack", ["unchecked_bytecode", "sibling_numpy", "changed_source"])
def test_only_explicit_unchanged_source_snapshot_executes(tmp_path, attack):
    root = tmp_path / "source"
    shutil.copytree(NATIVE / "oit", root / "oit", ignore=shutil.ignore_patterns("__pycache__"))
    if attack == "unchecked_bytecode":
        target = root / "oit/diagnostics.py"
        rogue = tmp_path / "rogue.py"
        rogue.write_text("raise RuntimeError('UNHASHED BYTECODE')\n")
        cache = Path(importlib.util.cache_from_source(str(target)))
        cache.parent.mkdir(exist_ok=True)
        py_compile.compile(str(rogue), cfile=str(cache), dfile=str(target), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    elif attack == "sibling_numpy":
        (root / "numpy.py").write_text("raise RuntimeError('SOURCE ROOT SHADOW')\n")
    runtime = bridge.RankRuntime(root)
    if attack == "changed_source":
        with (root / "oit/diagnostics.py").open("a") as stream:
            stream.write("\n# drift\n")
        with pytest.raises(ValueError, match="changed after binding"):
            runtime.evaluate({"A": np.eye(2)})
    else:
        assert runtime.evaluate({"A": np.eye(2)})["rows"][0]["diagnostics"]["rank"] == 2


def test_rank_tolerance_and_scale_have_analytic_perturbation_oracles():
    runtime = bridge.RankRuntime(NATIVE)
    # Diagonal singular values are known analytically, independent of any SVD.
    cases = {"below": np.diag([1., 0.5e-12]), "above": np.diag([1., 2e-12]),
             "at": np.diag([1., 1e-12]), "zero": np.zeros((2, 3)),
             "small": np.diag([1., 2e-12]) * 1e-100,
             "large": np.diag([1., 2e-12]) * 1e100,
             "duplicate": np.array([[1., 1.], [2., 2.]])}
    result = runtime.evaluate(cases)
    assert {row["name"]: row["diagnostics"]["rank"] for row in result["rows"]} == {
        "below": 1, "above": 2, "at": 1, "zero": 0, "small": 2, "large": 2, "duplicate": 1}
    assert runtime.evaluate({"A": np.diag([1., 2e-12])},
        {**bridge.TOLERANCES, "rank_atol": 1e-11})["rows"][0]["diagnostics"]["rank"] == 1


def test_independent_oracle_detects_wrong_native_rank_spectrum_and_basis():
    pytest.importorskip("scipy")
    values = matrices()
    result = bridge.RankRuntime(NATIVE).evaluate(values)
    assert qualification.check_reference(values, result)["status"] == "PASS"
    for field, value in [("rank", 0), ("singular_values", [3.]), ("numerical_nullspace", [[2.], [2.]])]:
        changed = deepcopy(result)
        changed["rows"][1]["diagnostics"][field] = value
        with pytest.raises(ValueError):
            qualification.check_reference(values, changed)


def test_correction_invalidates_rank_result_and_leaves_old_evidence_intact(tmp_path):
    session = Session(make_demo_run(), tmp_path, operations=registry())
    original = retained(session.workbench)
    original_envelope = json.loads(base64.b64decode(session.workbench.get_source(original["source_id"])["bytes_b64"]))
    first = call(session, bridge.source_parameters(session.workbench, original["source_id"]))["result"]
    values = matrices()
    values["A"] = np.eye(2)
    changed = retained(session.workbench, archive(values), revision=2,
                       parent_source_id=original_envelope["source_id"])
    def command(kind, payload):
        response = session.handle({"protocol_version": 1, "request_id": kind, "type": kind, "payload": payload})
        assert response["type"] == "response", response
        return response["payload"]
    proposal = command("correction.propose", {"old_source_id": original["source_id"], "new_source_id": changed["source_id"],
        "kind": "source_data", "reason": "synthetic perturbation"})
    command("correction.review", {"correction_id": proposal["correction_id"], "decision": "accept",
        "expected_revision": session.correction_journal.revision, "reviewer": "test", "reason": "checked"})
    statuses = session.dependency_status()["artifact_status"]
    assert all(statuses[key]["status"] == "stale" for key in [original["source_id"], first["result_id"], first["execution_id"]])
    assert session.results[first["result_id"]] == first
    after = call(session, bridge.source_parameters(session.workbench, changed["source_id"]))
    assert after["status"] == "completed"
    assert after["result"]["data"]["native"]["rows"][-1]["diagnostics"]["rank"] == 2


def test_inventory_exposes_explicit_operation_without_binding_or_execution(monkeypatch):
    from ciw.data_inventory import inventory
    monkeypatch.setattr(bridge.RankRuntime, "__init__", lambda *a, **k: pytest.fail("Inventory binds provider"))
    value = inventory(repository=ROOT)
    assert bridge.OPERATION not in {row["operation_id"] for row in default_registry().describe()}
    assert bridge.OPERATION in {row["operation_id"] for row in value["explicit_workload_operations"]}


@pytest.mark.integration
def test_all_acquired_carex_arrays_native_session_restore_replay(tmp_path):
    root = os.environ.get("NET_CAREX_SOURCE_DIR")
    if not root:
        pytest.skip("Original acquired CAREX bytes absent; no synthetic substitution")
    pytest.importorskip("scipy")
    report = qualification.qualify(Path(root), NATIVE, tmp_path / "carex")
    assert report["status"] == "PASS"
    assert report["published_arrays"] == 20
    assert report["native_endpoint_calls"] == 40
    assert report["session_executions"] == 10
    assert report["synthetic_source_substitution"] is False
    assert len({execution for row in report["cases"] for execution in row["execution_ids"]}) == 10


def test_shared_native_interface_accepts_rectangular_matrices_without_carex_roles(monkeypatch):
    values = {"rectangular_uint8": np.array([[1, 0, 0, 0, 0], [0, 2, 0, 0, 0], [0, 0, 3, 0, 0]], dtype=np.uint8),
              "row": np.array([[0., 1., 2., 3., 4., 5., 6., 7., 8.]])}
    result = bridge.RankRuntime(NATIVE).evaluate(values, bridge.TOLERANCES)
    assert [row["diagnostics"]["rank"] for row in result["rows"]] == [3, 1]
    monkeypatch.setattr(np.linalg, "svd", lambda *a, **k: pytest.fail("Saved structural validation ran SVD"))
    bridge.validate_native(values, result, bridge.TOLERANCES)


@pytest.mark.parametrize("bad", [np.zeros((2, 3, 4)), np.ones((513, 1)), np.ones((1, 1), dtype=bool),
                                np.array([[np.nan]]), np.array([[2**53 + 1]], dtype=np.uint64)])
def test_shared_native_interface_refuses_unsupported_shape_dtype_without_execution(bad, monkeypatch):
    runtime = bridge.RankRuntime(NATIVE)
    monkeypatch.setattr(bridge, "_bounded_process", lambda *a, **k: pytest.fail("Unexpected native execution"))
    with pytest.raises(ValueError):
        runtime.evaluate({"original_array": bad})
