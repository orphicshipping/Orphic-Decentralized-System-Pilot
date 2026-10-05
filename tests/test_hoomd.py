"""Real CPU dynamics, independent references and provider-free native-GSD replay.

Native tests require NET_HOOMD_PYTHON. The qualification script refuses skips.
"""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

pytest.importorskip("gsd.hoomd", reason="native GSD reader is the optional particles extra")

from ciw.adapters.protocol import AdapterRefusal
from ciw.control_checks import compare
from ciw.core.identities import canonical_json
from ciw.data_sources import native_record, retain_record
from ciw.hoomd_checks import QUANTITIES, reference_observations
from ciw.hoomd_cli import run_study
from ciw.hoomd_contract import (GSD_VERSION, OPERATION, VERSION, artifact, artifact_bytes,
    bonds, example_study, frames, summarize, validate_result, validate_study)
from ciw.hoomd_workflow import HoomdRuntime, observation_stream, source_parameters
from ciw.operations.registry import default_registry
from ciw.operations.runner import seal
from ciw.session import Session

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-10-05T20:00:00Z"


@pytest.fixture(scope="module")
def study():
    return example_study()


@pytest.fixture(scope="module")
def cpu():
    python = os.environ.get("NET_HOOMD_PYTHON")
    if not python:
        pytest.skip("Set NET_HOOMD_PYTHON to the qualified HOOMD 7.2.0 CPU interpreter")
    return HoomdRuntime(python)


@pytest.fixture(scope="module")
def run(cpu, study, tmp_path_factory):
    directory = tmp_path_factory.mktemp("hoomd-native") / "original"
    report = run_study(study, python=cpu.python, output_dir=directory)
    native = json.loads((directory / "native-result.json").read_text())
    return directory, report, native


def replace_initial(study, frame, tmp_path):
    import gsd.hoomd
    path = tmp_path / "changed.gsd"
    with gsd.hoomd.open(str(path), "w", precision="double") as output:
        output.append(frame)
    changed = deepcopy(study)
    changed["initial_state"] = artifact(path.read_bytes())
    return changed


@pytest.mark.parametrize("path,value", [
    (("seed",), True), (("seed",), -1), (("seed",), 65536),
    (("device",), "gpu"), (("hoomd_version",), "7.1.2"), (("ensemble",), "NPT"),
    (("units", "system"), "SI"), (("units", "length_m"), 0),
    (("units", "energy_J"), float("nan")), (("temperature", "unit"), "K"),
    (("temperature", "kT"), float("inf")), (("pressure", "target"), 1),
    (("topology", "chain_lengths"), [3, 5]), (("topology", "chain_lengths"), [8, 8]),
    (("topology", "chain_lengths"), [True, 7]), (("topology", "kind"), "branched"),
    (("potential", "pair"), "arbitrary_script"), (("potential", "epsilon"), True),
    (("potential", "exclude_bonded_pairs"), False), (("potential", "bond_k"), -1),
    (("integrator", "dt"), .5), (("integrator", "gamma"), False),
    (("schedule", "sample_period"), 0), (("schedule", "production_steps"), 201),
    (("material", "meaning"), "qualified_material_property"),
])
def test_contract_rejects_unsupported_or_ambiguous_studies(study, path, value):
    changed = deepcopy(study)
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate_study(changed)


def test_study_roundtrip_keeps_native_initial_bytes_and_units(study):
    envelope = retain_record(study, dataset_id="chains", revision=1, parent_source_id=None,
                            recorded_at=STAMP, data_family="hoomd-study")
    assert native_record(envelope) == study
    assert artifact_bytes(native_record(envelope)["initial_state"]) == artifact_bytes(study["initial_state"])
    assert frames(study["initial_state"])[0].bonds.group.tolist() == bonds(study)
    assert study["units"]["scale_authority"] == "caller_declared_not_calibrated"


def test_checked_in_example_uses_qualified_native_schema():
    study = json.loads((ROOT / "examples/hoomd/linear-chains.json").read_text())
    validate_study(study)
    assert frames(study["initial_state"])[0].particles.N == 8


def test_initial_byte_tamper_is_rejected(study):
    changed = deepcopy(study)
    raw = bytearray(artifact_bytes(study["initial_state"]))
    raw[-1] ^= 1
    changed["initial_state"]["chunks_b64"] = [base64.b64encode(raw).decode()]
    with pytest.raises(ValueError, match="hash"):
        validate_study(changed)


@pytest.mark.parametrize("mutation", ["nonfinite", "topology", "velocity", "overlap", "mass", "image", "box"])
def test_native_initial_state_errors_refuse_before_execution(study, tmp_path, mutation):
    frame = frames(study["initial_state"])[0]
    for name in ("position", "velocity", "image", "mass"):
        setattr(frame.particles, name, getattr(frame.particles, name).copy())
    if mutation == "nonfinite":
        frame.particles.position[0, 0] = float("nan")
    elif mutation == "topology":
        frame.bonds.group[0] = [0, 2]
    elif mutation == "velocity":
        frame.particles.velocity[0, 0] = 1
    elif mutation == "overlap":
        frame.particles.position[1] = frame.particles.position[0]
    elif mutation == "mass":
        frame.particles.mass[0] = 2
    elif mutation == "image":
        frame.particles.image[0, 0] = 1
    elif mutation == "box":
        frame.configuration.box[3] = .1
    with pytest.raises(ValueError):
        validate_study(replace_initial(study, frame, tmp_path))


def test_engine_registration_is_explicit_and_inventory_does_not_probe(monkeypatch):
    from ciw.data_inventory import inventory
    assert OPERATION not in {row["operation_id"] for row in default_registry().describe()}
    monkeypatch.setattr(HoomdRuntime, "_probe", lambda *args: pytest.fail("Inventory probed runtime"))
    catalog = inventory()
    assert OPERATION in {row["operation_id"] for row in catalog["explicit_workload_operations"]}
    assert catalog["typed_native_schemas"]["ciw.hoomd-result.v1"] == "hoomd-result"


@pytest.mark.integration
def test_actual_cpu_end_to_end_native_artifacts_and_independent_checks(run):
    directory, report, native = run
    assert report["frame_counts"] == {"initial": 1, "equilibration": 6, "production": 11}
    assert native["runtime"]["hoomd_version"] == VERSION
    assert native["runtime"]["gsd_version"] == GSD_VERSION
    assert native["runtime"]["device"] == "cpu"
    assert all(item["outcome"]["status"] == "PASS" for item in report["independent_static_comparisons"].values())
    assert set(report["independent_static_comparisons"]) == set(QUANTITIES)
    assert native["authority"]["physical_validation"] == "not_established"
    assert native["authority"]["equilibration"] == "scheduled_not_demonstrated"
    for name, value in native["artifacts"].items():
        assert (directory / (name + ".gsd")).read_bytes() == artifact_bytes(value)
    assert native["summary"]["production_descriptors"][0]["finite_lag_diffusivity"] is None
    assert native["summary"]["production_descriptors"][-1]["mean_square_displacement"] > 0
    assert all(len(item["frame_ids"]) == len(item["steps"]) for item in native["summary"]["native_frames"].values())
    stream = observation_stream(native)
    assert len(stream["observations"]) == 66
    assert {item["unit"] for item in stream["observations"]} == {"J", "Pa", "m", "m^2", "m^2/s"}


@pytest.mark.integration
def test_offline_reopen_and_native_inspection_never_launch_provider(run, tmp_path, monkeypatch):
    directory, _, native = run
    monkeypatch.setattr(HoomdRuntime, "_call", lambda *args: pytest.fail("Offline restore launched provider"))
    assert "hoomd" not in sys.modules
    validate_result(native)
    session = Session.from_workspace(directory / "workspace.json", output_dir=tmp_path / "offline")
    assert len(session.results) == 5
    original = next(value for value in session.results.values() if value["operation_id"] == OPERATION)
    assert original["verification_status"] == "not_verified"
    refusal = session.handle({"protocol_version": 1, "request_id": "unbound", "type": "operation.execute",
        "payload": {"operation_id": OPERATION, "parameters": original["parameters"]}})["payload"]
    assert refusal["status"] == "refused"
    assert refusal["execution"]["refusal"]["code"] == "operation_unavailable"
    retained = [native_record(source) for source in session.workbench.retained_sources().values()]
    assert any(value["schema"] == "ciw.hoomd-result.v1" for value in retained)
    assert "hoomd" not in sys.modules


@pytest.mark.integration
def test_replay_after_explicit_rebinding_matches_with_declared_tolerance(run, cpu, tmp_path):
    directory, _, native = run
    session = Session.from_workspace(directory / "workspace.json", output_dir=tmp_path / "replay")
    session.operations.register(cpu.operation())
    original = next(value for value in session.results.values() if value["operation_id"] == OPERATION)
    replay = session.handle({"protocol_version": 1, "request_id": "replay", "type": "operation.execute",
        "payload": {"operation_id": OPERATION, "parameters": original["parameters"]}})["payload"]
    assert replay["status"] == "completed", replay["execution"].get("refusal")
    assert replay["result"]["execution_id"] != original["execution_id"]
    rows = replay["result"]["data"]["native_result"]["summary"]["production_descriptors"]
    for left, right in zip(rows, native["summary"]["production_descriptors"]):
        assert left["step"] == right["step"]
        for name in ("potential_energy", "kinetic_energy", "pressure", "radius_gyration", "mean_square_displacement"):
            assert left[name] == pytest.approx(right[name], rel=1e-10, abs=1e-10)


@pytest.mark.integration
@pytest.mark.parametrize("mutation", ["bytes", "descriptor", "frame", "runtime", "nonfinite", "authority"])
def test_retained_result_tamper_is_rejected(run, mutation):
    native = deepcopy(run[2])
    if mutation == "bytes":
        native["artifacts"]["production"]["sha256"] = "sha256:" + "0"*64
    elif mutation == "descriptor":
        native["summary"]["production_descriptors"][1]["radius_gyration"] += 1
    elif mutation == "frame":
        native["summary"]["native_frames"]["production"]["frame_ids"][1] = "sha256:" + "0"*64
    elif mutation == "runtime":
        native["runtime"]["hoomd_version"] = "7.1.2"
    elif mutation == "nonfinite":
        native["summary"]["production_descriptors"][1]["pressure"] = float("nan")
    else:
        native["authority"]["physical_validation"] = "qualified"
    with pytest.raises(ValueError):
        validate_result(native)


@pytest.mark.integration
def test_bound_runtime_drift_refuses(cpu, monkeypatch):
    original = cpu._probe()
    changed = deepcopy(original)
    changed["package_code_sha256"]["hoomd"] = "0"*64
    monkeypatch.setattr(cpu, "_probe", lambda: changed)
    with pytest.raises(AdapterRefusal, match="changed"):
        cpu.runtime_identity()


@pytest.mark.integration
def test_resealed_saved_runtime_contradiction_is_rejected(run, tmp_path):
    saved = json.loads((run[0] / "workspace.json").read_text())
    # Workspace results are serialized as a list. Matching result/execution seals
    # alone must not permit a different runtime inside the native result.
    target = next(value for value in saved["results"] if value["operation_id"] == OPERATION)
    target["data"]["native_result"]["runtime"]["machine"] = "different-architecture"
    seal(target)
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        Session.from_workspace(path, output_dir=tmp_path / "refused")


def test_tiny_gsd_with_huge_default_particle_count_is_rejected(tmp_path):
    import gsd.fl
    import numpy as np
    path = tmp_path / "huge-count.gsd"
    with gsd.fl.open(str(path), "w", application="NET refusal test", schema="hoomd", schema_version=[2, 0]) as output:
        output.write_chunk("particles/N", np.array([4294967295], dtype=np.uint32))
        output.end_frame()
    with pytest.raises(ValueError, match="count"):
        frames(artifact(path.read_bytes()))


@pytest.mark.integration
def test_actual_nonbonded_wca_energy_and_virial_are_exercised(study, cpu, tmp_path):
    modified = deepcopy(study)
    initial = frames(study["initial_state"])[0]
    initial.particles.position[:4, 1] = -.5
    initial.particles.position[4:, 1] = .5
    modified = replace_initial(modified, initial, tmp_path)
    modified["integrator"]["dt"] = .0002
    modified["schedule"] = {"equilibration_steps": 2, "production_steps": 4, "sample_period": 1}
    report = run_study(modified, python=cpu.python, output_dir=tmp_path / "wca")
    native = json.loads((tmp_path / "wca/native-result.json").read_text())
    assert all(item["outcome"]["status"] == "PASS" for item in report["independent_static_comparisons"].values())
    # Four inter-chain pairs initially at sigma activate the shifted WCA term.
    # The bonded-only initial energy is about 0.225 epsilon.
    assert native["summary"]["production_descriptors"][0]["potential_energy"] > 3
    frame = frames(native["artifacts"]["production"])[0]
    assert abs(frame.particles.position[0, 1] - frame.particles.position[4, 1]) < 2**(1/6)


@pytest.mark.integration
def test_initial_equilibration_discontinuity_refuses_even_with_new_hashes(run, tmp_path):
    import gsd.hoomd
    native = deepcopy(run[2])
    equilibration = frames(native["artifacts"]["equilibration"])
    equilibration[0].particles.position[0, 0] += .01
    path = tmp_path / "wrong-start.gsd"
    with gsd.hoomd.open(str(path), "w", precision="double") as output:
        output.extend(equilibration)
    native["artifacts"]["equilibration"] = artifact(path.read_bytes())
    with pytest.raises(ValueError, match="retained initial state"):
        summarize(native["study"], native["artifacts"])


@pytest.mark.integration
def test_wrong_source_commit_and_unqualified_distribution_refuse(run):
    from ciw.hoomd_contract import validate_runtime
    runtime = deepcopy(run[2]["runtime"])
    runtime["hoomd_git_sha"] = "0"*40
    with pytest.raises(ValueError, match="pinned source"):
        validate_runtime(runtime)
    runtime["hoomd_git_sha"] = "unknown"
    runtime["distribution"]["sha256"] = "0"*64
    with pytest.raises(ValueError, match="pinned source"):
        validate_runtime(runtime)


@pytest.mark.integration
def test_study_correction_invalidates_native_results_observations_and_comparisons(run, tmp_path):
    from ciw.data_sources import _unwrap
    session = Session.from_workspace(run[0] / "workspace.json", output_dir=tmp_path / "correction")
    original = next(item for item in session.results.values() if item["operation_id"] == OPERATION)
    old = original["parameters"]["source"]
    envelope = _unwrap(old)
    changed = deepcopy(envelope["native_record"])
    changed["seed"] += 1
    new = retain_record(changed, dataset_id=envelope["dataset_id"], revision=2,
        parent_source_id=envelope["source_id"], recorded_at=STAMP, data_family="hoomd-study")
    revised = session.workbench.add_source({"kind": "data-record", "label": "revised seed",
        "bytes_b64": base64.b64encode(canonical_json(new).encode()).decode()})
    def command(kind, payload):
        answer = session.handle({"protocol_version": 1, "request_id": kind, "type": kind, "payload": payload})
        assert answer["type"] == "response", answer
        return answer["payload"]
    correction = command("correction.propose", {"old_source_id": old["source_id"],
        "new_source_id": revised["source_id"], "kind": "source_data", "reason": "explicit seed correction"})
    command("correction.review", {"correction_id": correction["correction_id"], "decision": "accept",
        "expected_revision": session.correction_journal.revision, "reviewer": "operator", "reason": "checked"})
    status = session.dependency_status()["artifact_status"]
    for result in session.results.values():
        assert status[result["result_id"]]["status"] == "stale"
        assert status[result["execution_id"]]["status"] == "stale"
    for source in session.workbench.retained_sources().values():
        if source["source_id"] != revised["source_id"]:
            assert status[source["source_id"]]["status"] == "stale"
    assert status[revised["source_id"]]["status"] == "current"
    assert session.results[original["result_id"]] == original


@pytest.mark.integration
def test_missing_retained_study_source_is_rejected_on_reopen(run, tmp_path):
    value = json.loads((run[0] / "workspace.json").read_text())
    value["workbench"]["sources"] = [source for source in value["workbench"]["sources"]
        if source["data"]["native_schema"] != "ciw.hoomd-study.v1"]
    value["workbench"]["revision"] -= 1
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        Session.from_workspace(path, output_dir=tmp_path / "missing-refused")


def test_malformed_native_particle_attribute_shape_is_rejected(study, tmp_path):
    import gsd.fl
    import numpy as np
    original = tmp_path / "original.gsd"
    original.write_bytes(artifact_bytes(study["initial_state"]))
    malformed = tmp_path / "shape.gsd"
    with gsd.fl.open(str(original), "r") as source:
        with gsd.fl.open(str(malformed), "w", application="NET shape refusal", schema="hoomd", schema_version=[2, 0]) as output:
            for name in source.find_matching_chunk_names(""):
                if source.chunk_exists(0, name) and name != "particles/mass":
                    output.write_chunk(name, source.read_chunk(0, name))
            output.write_chunk("particles/mass", np.ones(1))
            output.end_frame()
    changed = deepcopy(study)
    changed["initial_state"] = artifact(malformed.read_bytes())
    with pytest.raises(ValueError, match="attribute"):
        validate_study(changed)


def test_fractional_native_images_are_rejected_without_coercion(study, tmp_path):
    import gsd.fl
    import numpy as np
    original = tmp_path / "original.gsd"
    original.write_bytes(artifact_bytes(study["initial_state"]))
    malformed = tmp_path / "fractional-images.gsd"
    with gsd.fl.open(str(original), "r") as source:
        with gsd.fl.open(str(malformed), "w", application="NET dtype refusal", schema="hoomd", schema_version=[2, 0]) as output:
            for name in source.find_matching_chunk_names(""):
                if source.chunk_exists(0, name) and name != "particles/image":
                    output.write_chunk(name, source.read_chunk(0, name))
            output.write_chunk("particles/image", np.full((8, 3), .5))
            output.end_frame()
    changed = deepcopy(study)
    changed["initial_state"] = artifact(malformed.read_bytes())
    with pytest.raises(ValueError, match="integer dtype"):
        validate_study(changed)
