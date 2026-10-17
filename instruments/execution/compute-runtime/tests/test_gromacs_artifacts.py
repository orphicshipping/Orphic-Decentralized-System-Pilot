"""Artifact/binding tests with explicit process doubles, NOT native qualification."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from execution import gromacs as gm
from execution import gromacs_artifacts as ga
from execution.engine import EngineProtocolError
from execution.specification import ExecutionSpecification
from test_gromacs_boundary import load_gate


@pytest.fixture
def retained_rig(tmp_path, monkeypatch):
    gate = load_gate()
    executable, dependency, build = (tmp_path / name for name in ("gmx-double", "library-double", "build-record.txt"))
    executable.write_bytes(b"explicit simulated native executable; never execute this")
    dependency.write_bytes(b"explicit simulated dependency")
    build.write_bytes(b"simulated build record; not an attestation")
    entry = lambda p: {"path": str(p), "sha256": sha256(p.read_bytes()).hexdigest()}
    manifest = {"schema": ga.BINDING_SCHEMA, "version_line": "GROMACS version: test-double-retained",
                "executable": entry(executable), "build_record": entry(build), "dependencies": [entry(dependency)]}
    state = {"calls": [], "fail": None, "omit": None, "mutation": None,
             "energy": None, "timeout": None, "large_log": False}

    def run(command, **kwargs):
        stage = "version" if command[2] == "--version" else command[2]
        state["calls"].append((stage, command, kwargs))
        if state["timeout"] == stage:
            raise subprocess.TimeoutExpired(command, 300, output=b"partial stdout", stderr=b"partial stderr")
        if stage == "version":
            outputs = {}
            stdout = (manifest["version_line"] + "\n").encode()
        else:
            base = Path(kwargs["cwd"])
            if stage == "grompp":
                outputs = {"run.tpr": b"simulated TPR\x00\x01", "effective.mdp": (base / "run.mdp").read_bytes(),
                           "processed.top": (base / "system.top").read_bytes()}
            elif stage == "mdrun":
                outputs = {"run.edr": b"simulated EDR\x00\xff", "run.log": b"simulated native log\n"}
            else:
                rows = (base / "conf.gro").read_text().splitlines()
                distance = float(rows[3][20:28]) - float(rows[2][20:28])
                raw = state["energy"] if state["energy"] is not None else f"0 {gate.scalar_reference(distance):.17g}\n".encode()
                outputs = {"energy.xvg": raw}
            for name, raw in outputs.items():
                if name != state["omit"]:
                    (base / name).write_bytes(raw)
            stdout = ("simulated " + stage + " stdout").encode()
        if state["mutation"] and state["mutation"][0] == stage:
            target = state["mutation"][1]
            (target if isinstance(target, Path) else Path(kwargs["cwd"]) / target).write_bytes(b"mutated")
        if state["large_log"]:
            stdout = b"x" * (ga.MAX_BYTES + 1)
        return subprocess.CompletedProcess(command, 1 if state["fail"] == stage else 0,
                                           stdout, ("simulated " + stage + " stderr").encode())

    monkeypatch.setattr(gm.subprocess, "run", run)
    program = gm.gromacs_program_descriptor(manifest["version_line"], gate.TOPOLOGY, runtime_manifest=manifest)
    spec = ExecutionSpecification(program, gate.MDP, gate.pair_gro(0.4))
    return executable, dependency, build, manifest, spec, state


def execute(rig, output, **overrides):
    exe, _, _, manifest, spec, _ = rig
    options = {"expected_binary_sha256": manifest["executable"]["sha256"], "max_warnings": 0,
               "cpu_only": True, "runtime_manifest": manifest, "artifact_directory": output}
    options.update(overrides)
    return gm.run_gromacs_specification(spec, exe, **options)


def reseal(path, mutate):
    value = json.loads(path.read_text())
    mutate(value)
    value.pop("receipt_sha256", None)
    value["receipt_sha256"] = sha256(ga.canonical(value)).hexdigest()
    path.write_bytes(ga.canonical(value))


def test_retains_native_inputs_intermediates_logs_and_result(retained_rig, tmp_path):
    output = tmp_path / "retained"
    result = execute(retained_rig, output)
    receipt = ga.validate_receipt(output)
    assert result.status == receipt["status"] == "completed"
    assert receipt["result"]["computation_identity"] == result.computation_identity
    assert {"run.tpr", "run.edr", "run.log", "effective.mdp", "processed.top", "energy.xvg",
            "result.bin", "build-record.bin", "runtime-binding.json"} <= receipt["artifacts"].keys()
    assert (output / "run.edr").read_bytes() == b"simulated EDR\x00\xff"
    assert receipt["limits"]["physical_validation"] == "not_performed"
    assert receipt["limits"]["dependency_closure_attestation"] == "not_established"
    assert [row["stage"] for row in receipt["stages"]] == ["version", "grompp", "mdrun", "energy"]
    assert receipt["stages"][2]["inputs"]["run.tpr"] == receipt["artifacts"]["run.tpr"]
    assert receipt["stages"][3]["inputs"]["run.edr"] == receipt["artifacts"]["run.edr"]


def test_repeat_has_new_receipt_identity_but_same_computation(retained_rig, tmp_path):
    a, b = (tmp_path / name for name in ("a", "b"))
    x, y = execute(retained_rig, a), execute(retained_rig, b)
    assert x.output == y.output and x.computation_identity == y.computation_identity
    assert ga.validate_receipt(a)["execution_id"] != ga.validate_receipt(b)["execution_id"]


def test_offline_reopen_uses_no_executable_or_dependency(retained_rig, tmp_path, monkeypatch):
    output = tmp_path / "retained"
    execute(retained_rig, output)
    for p in retained_rig[:3]:
        p.unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError("offline reopen tried to execute or bind runtime")
    monkeypatch.setattr(gm.subprocess, "run", forbidden)
    monkeypatch.setattr(ga, "check_runtime_files", forbidden)
    moved = tmp_path / "moved"
    shutil.move(output, moved)
    assert ga.validate_receipt(moved)["status"] == "completed"


@pytest.mark.parametrize("target", ["executable", "build_record", "dependency"])
def test_runtime_file_drift_refuses_before_any_process(retained_rig, tmp_path, target):
    path = dict(zip(("executable", "dependency", "build_record"), retained_rig[:3]))[target]
    path.write_bytes(b"wrong bytes")
    with pytest.raises(EngineProtocolError, match="changed"):
        execute(retained_rig, tmp_path / "refused")
    assert retained_rig[-1]["calls"] == []


@pytest.mark.parametrize("stage", ["version", "grompp", "mdrun", "energy"])
def test_dependency_drift_preserves_failed_attempt_not_result(retained_rig, tmp_path, stage):
    retained_rig[-1]["mutation"] = (stage, retained_rig[1])
    out = tmp_path / "drift"
    with pytest.raises(EngineProtocolError, match="changed"):
        execute(retained_rig, out)
    receipt = ga.validate_receipt(out)
    assert receipt["status"] == "failed" and receipt["result"] is None
    assert retained_rig[-1]["calls"][-1][0] == stage
    assert (out / (stage + ".stdout")).is_file()


@pytest.mark.parametrize("stage,file", [("grompp", "system.top"), ("mdrun", "run.tpr"), ("energy", "run.edr")])
def test_stage_input_mutation_is_detected(retained_rig, tmp_path, stage, file):
    retained_rig[-1]["mutation"] = (stage, file)
    out = tmp_path / "input-drift"
    with pytest.raises(EngineProtocolError, match="mutated"):
        execute(retained_rig, out)
    assert ga.validate_receipt(out)["result"] is None


@pytest.mark.parametrize("file", ["run.tpr", "processed.top", "effective.mdp", "run.edr", "run.log", "energy.xvg"])
def test_success_without_native_artifacts_is_not_completion(retained_rig, tmp_path, file):
    retained_rig[-1]["omit"] = file
    out = tmp_path / "missing"
    with pytest.raises(EngineProtocolError, match="omitted"):
        execute(retained_rig, out)
    receipt = ga.validate_receipt(out)
    assert receipt["status"] == "failed" and receipt["result"] is None


@pytest.mark.parametrize("stage,code", [("grompp", 10), ("mdrun", 11), ("energy", 12)])
def test_stage_failures_retain_pipes_and_absent_result_output(retained_rig, tmp_path, stage, code):
    retained_rig[-1]["fail"] = stage
    out = tmp_path / "failure"
    r = execute(retained_rig, out)
    receipt = ga.validate_receipt(out)
    assert r.status == "halted" and r.exit_code == code and r.output is None
    assert receipt["result"]["output"] is None
    assert not (out / "result.bin").exists()
    assert (out / (stage + ".stderr")).read_bytes()


@pytest.mark.parametrize("stage", ["version", "grompp", "mdrun", "energy"])
def test_timeout_preserves_partial_pipes(retained_rig, tmp_path, stage):
    retained_rig[-1]["timeout"] = stage
    out = tmp_path / "timeout"
    with pytest.raises(EngineProtocolError):
        execute(retained_rig, out)
    assert ga.validate_receipt(out)["status"] == "failed"
    assert (out / (stage + ".stdout")).read_bytes() == b"partial stdout"


def test_nonfinite_energy_retained_as_failed_parse_not_substituted(retained_rig, tmp_path):
    retained_rig[-1]["energy"] = b"0 nan\n"
    out = tmp_path / "nan"
    r = execute(retained_rig, out)
    assert r.exit_code == gm.FAULT_PARSE and r.output is None
    assert ga.validate_receipt(out)["result"]["status"] == "halted"
    assert (out / "energy.xvg").read_bytes() == b"0 nan\n"


@pytest.mark.parametrize("file", ["run.tpr", "run.edr", "effective.mdp", "system.top", "result.bin", "build-record.bin"])
def test_offline_artifact_tamper_refuses(retained_rig, tmp_path, file):
    out = tmp_path / "tampered"
    execute(retained_rig, out)
    (out / file).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        ga.validate_receipt(out)


@pytest.mark.parametrize("mutation", ["command", "runtime", "authority", "stage-input", "stage-output", "result", "output", "order", "occurrence"])
def test_resealed_semantic_tamper_refuses(retained_rig, tmp_path, mutation):
    out = tmp_path / "semantic"
    execute(retained_rig, out)
    def mutate(r):
        if mutation == "command": r["stages"][1]["command"][r["stages"][1]["command"].index("-maxwarn") + 1] = "2"
        if mutation == "runtime": r["runtime_manifest"]["executable"]["sha256"] = "0" * 64
        if mutation == "authority": r["limits"]["equipment_authority"] = True
        if mutation == "stage-input": r["stages"][2]["inputs"]["run.tpr"] = r["artifacts"]["run.edr"]
        if mutation == "stage-output": r["stages"][1]["outputs"] = {}
        if mutation == "result": r["result"]["computation_identity"] = "0" * 64
        if mutation == "output": r["result"]["output"] = None
        if mutation == "order": r["stages"] = list(reversed(r["stages"]))
        if mutation == "occurrence": r["result"]["engine_occurrence"] = True
    reseal(out / "receipt.json", mutate)
    with pytest.raises((ValueError, EngineProtocolError, RuntimeError)):
        ga.validate_receipt(out)


@pytest.mark.parametrize("option,value", [("max_warnings", 2), ("cpu_only", False), ("expected_binary_sha256", None),
                                          ("runtime_manifest", None), ("artifact_directory", None)])
def test_no_silent_downgrade_of_bound_descriptor(retained_rig, tmp_path, option, value):
    with pytest.raises((ValueError, EngineProtocolError)):
        execute(retained_rig, tmp_path / "downgrade", **{option: value})
    assert not retained_rig[-1]["calls"]


def test_manifest_changes_program_and_specification_identity(retained_rig):
    gate = load_gate()
    manifest = deepcopy(retained_rig[3])
    manifest["dependencies"][0]["sha256"] = "0" * 64
    other = gm.gromacs_program_descriptor(manifest["version_line"], gate.TOPOLOGY, runtime_manifest=manifest)
    spec = retained_rig[4]
    changed = ExecutionSpecification(other, spec.configuration, spec.input_payload)
    assert changed.program_identity() != spec.program_identity() and changed.identity() != spec.identity()
    assert changed.input_identity() == spec.input_identity()


@pytest.mark.parametrize("field,raw", [("configuration", b"include = -I/private\n"),
                                      ("configuration", b"nsteps = 4\n"),
                                      ("configuration", b"nsteps = 0\nnsteps = 1\n"),
                                      ("topology", b'#include "unbound.itp"\n')])
def test_unbound_external_inputs_and_trajectories_refuse(retained_rig, tmp_path, field, raw):
    exe, _, _, manifest, spec, state = retained_rig
    if field == "configuration":
        spec = ExecutionSpecification(spec.program, raw, spec.input_payload)
    else:
        program = gm.gromacs_program_descriptor(manifest["version_line"], raw, runtime_manifest=manifest)
        spec = ExecutionSpecification(program, spec.configuration, spec.input_payload)
    rig = (exe, retained_rig[1], retained_rig[2], manifest, spec, state)
    with pytest.raises(EngineProtocolError):
        execute(rig, tmp_path / "external")
    assert not state["calls"]


def test_existing_evidence_is_not_overwritten(retained_rig, tmp_path):
    out = tmp_path / "once"
    execute(retained_rig, out)
    raw = (out / "receipt.json").read_bytes()
    calls = len(retained_rig[-1]["calls"])
    with pytest.raises(FileExistsError):
        execute(retained_rig, out)
    assert (out / "receipt.json").read_bytes() == raw
    assert len(retained_rig[-1]["calls"]) == calls


def test_environment_does_not_inherit_loader_or_gromacs_overrides(retained_rig, tmp_path, monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/unbound.so")
    monkeypatch.setenv("GMXLIB", "/unbound-force-field")
    execute(retained_rig, tmp_path / "env")
    for _, _, kwargs in retained_rig[-1]["calls"]:
        assert "LD_PRELOAD" not in kwargs["env"] and "GMXLIB" not in kwargs["env"]
        assert kwargs["env"]["OMP_NUM_THREADS"] == "1"


def test_manifest_loader_rejects_duplicate_json_and_symlink_artifacts(tmp_path):
    with pytest.raises(ValueError): ga.strict_json(b'{"schema":1,"schema":2}')
    source, alias = tmp_path / "source", tmp_path / "alias"
    source.write_bytes(b"x")
    try: alias.symlink_to(source)
    except OSError: pytest.skip("host cannot create symlinks")
    with pytest.raises(ValueError): ga.read_bytes(alias)


def test_bound_gate_retains_four_receipts_and_reference_bindings(retained_rig, tmp_path):
    """The fake energy producer uses the reference; this tests bookkeeping ONLY."""
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_bytes(ga.canonical(retained_rig[3]))
    output = tmp_path / "gate"
    assert load_gate().main(["--runtime-binding", str(manifest_file), "--output", str(output)]) == 0
    report = json.loads((output / "gate.json").read_text())
    assert report["status"] == "PASS" and report["runtime_artifact_binding"] == "requested"
    assert len(report["cases"]) == 4
    for index, case in enumerate(report["cases"]):
        native = ga.validate_receipt(output / f"case-{index}" / "native")
        assert case["native_receipt_sha256"] == native["receipt_sha256"]
        assert case["comparison"]["computation_identity"] == native["result"]["computation_identity"]
        assert case["comparison"]["input_sha256"] == native["artifacts"]["conf.gro"]["sha256"]
    assert len({c["native_execution_id"] for c in report["cases"]}) == 4
    assert report["cases"][1]["computation_identity"] == report["cases"][3]["computation_identity"]


def test_bound_failed_comparison_retains_actual_native_bytes(retained_rig, tmp_path):
    retained_rig[-1]["energy"] = b"0 0\n"
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_bytes(ga.canonical(retained_rig[3]))
    out = tmp_path / "mismatch"
    assert load_gate().main(["--runtime-binding", str(manifest_file), "--output", str(out)]) == 1
    report = json.loads((out / "gate.json").read_text())
    assert report["status"] == "FAIL" and report["cases"][0]["comparison"]["status"] == "FAIL"
    assert (out / "case-0/native/result.bin").read_bytes() == b"potential_kj_per_mol 0"
    assert ga.validate_receipt(out / "case-0/native")["status"] == "completed"
    assert report["physical_validation"] == "not_performed"


@pytest.mark.parametrize("changed", ["geometry", "model", "settings", "box"])
def test_reference_refuses_unmatched_specification(retained_rig, changed):
    gate, spec = load_gate(), retained_rig[4]
    if changed == "geometry": spec = ExecutionSpecification(spec.program, spec.configuration, spec.input_payload.replace(b"1.400", b"1.401"))
    if changed == "model": spec = ExecutionSpecification(spec.program.replace(b"0.978638", b"0.978639"), spec.configuration, spec.input_payload)
    if changed == "settings": spec = ExecutionSpecification(spec.program, spec.configuration.replace(b"vdw-modifier = None", b"vdw-modifier = Potential-shift"), spec.input_payload)
    if changed == "box": spec = ExecutionSpecification(spec.program, spec.configuration, spec.input_payload.replace(b"3.00000", b"2.00000"))
    with pytest.raises(ValueError): gate.reference_for_specification(spec)


def test_nonzero_time_refuses_in_bound_single_point(retained_rig, tmp_path):
    retained_rig[-1]["energy"] = b"1 -0.1\n"
    result = execute(retained_rig, tmp_path / "time")
    assert result.exit_code == gm.FAULT_PARSE and result.output is None


def test_missing_manifest_returns_not_run_without_native_process(retained_rig, tmp_path):
    out = tmp_path / "missing-manifest"
    assert load_gate().main(["--runtime-binding", str(tmp_path / "absent.json"), "--output", str(out)]) == 2
    report = json.loads((out / "gate.json").read_text())
    assert report["status"] == "NOT_RUN" and not retained_rig[-1]["calls"]


def test_manifest_path_arguments_cannot_retarget_execution(retained_rig, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(ga.canonical(retained_rig[3]))
    assert load_gate().main(["--runtime-binding", str(manifest), "--gmx", str(tmp_path / "different"),
                            "--output", str(tmp_path / "retarget")]) == 1
    assert not retained_rig[-1]["calls"]


@pytest.mark.parametrize("change", ["duplicate", "extra", "relative", "hash", "empty", "overlong"])
def test_invalid_manifests_do_not_load_files(retained_rig, monkeypatch, change):
    manifest = deepcopy(retained_rig[3])
    if change == "duplicate": manifest["dependencies"].append(manifest["dependencies"][0])
    if change == "extra": manifest["execute"] = "untrusted command"
    if change == "relative": manifest["executable"]["path"] = "gmx"
    if change == "hash": manifest["executable"]["sha256"] = "sha256:" + "0" * 64
    if change == "empty": manifest["version_line"] = ""
    if change == "overlong": manifest["dependencies"] *= 33
    with pytest.raises(ValueError): ga.validate_manifest(manifest)
    assert not retained_rig[-1]["calls"]


def test_completed_receipt_requires_energy_time_zero(retained_rig, tmp_path):
    out = tmp_path / "time-tamper"
    execute(retained_rig, out)
    raw = (out / "energy.xvg").read_bytes().replace(b"0 ", b"1 ", 1)
    (out / "energy.xvg").write_bytes(raw)
    def mutate(r):
        r["artifacts"]["energy.xvg"] = ga.identity(raw)
        r["stages"][-1]["outputs"]["energy.xvg"] = ga.identity(raw)
    reseal(out / "receipt.json", mutate)
    with pytest.raises(ValueError): ga.validate_receipt(out)


@pytest.mark.parametrize("field,value", [("threads", True), ("cpu_only", 1), ("max_warnings", False)])
def test_resealed_policy_preserves_exact_json_types(retained_rig, tmp_path, field, value):
    output = tmp_path / "policy-type"
    execute(retained_rig, output)
    reseal(output / "receipt.json", lambda r: r["policy"].update({field: value}))
    with pytest.raises(ValueError):
        ga.validate_receipt(output)


def test_started_attempt_is_in_the_hashed_inventory(retained_rig, tmp_path):
    output = tmp_path / "attempt"
    execute(retained_rig, output)
    receipt = ga.validate_receipt(output)
    assert receipt["artifacts"]["attempt.json"] == ga.identity((output / "attempt.json").read_bytes())
    (output / "attempt.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        ga.validate_receipt(output)


def test_actual_subprocess_transport_with_labelled_double(tmp_path):
    """Four real subprocesses running a script double, NOT native GROMACS."""
    import os
    import sys
    if os.name != "posix":
        pytest.skip("POSIX shebang transport fixture; other tests cover portable process contracts")
    exe, build = tmp_path / "process-double", tmp_path / "build.txt"
    exe.write_text("#!" + str(Path(sys.executable).resolve()) + "\n" + r'''
import pathlib, sys
p = pathlib.Path
stage = sys.argv[2]
if stage == "--version":
    print("GROMACS version: explicit-subprocess-double")
elif stage == "grompp":
    p("run.tpr").write_bytes(b"double TPR")
    p("effective.mdp").write_bytes(p("run.mdp").read_bytes())
    p("processed.top").write_bytes(p("system.top").read_bytes())
elif stage == "mdrun":
    p("run.edr").write_bytes(b"double EDR")
    p("run.log").write_bytes(b"double log")
elif stage == "energy":
    assert sys.stdin.read() == "Potential\n"
    p("energy.xvg").write_bytes(b"0 -0.9\n")
else:
    raise RuntimeError("unexpected stage")
''', encoding="utf-8")
    exe.chmod(0o700)
    build.write_text("explicit subprocess transport double, not GROMACS or a build attestation")
    entry = lambda p: {"path": str(p), "sha256": sha256(p.read_bytes()).hexdigest()}
    manifest = {"schema": ga.BINDING_SCHEMA, "version_line": "GROMACS version: explicit-subprocess-double",
                "executable": entry(exe), "build_record": entry(build), "dependencies": []}
    gate = load_gate()
    spec = ExecutionSpecification(gm.gromacs_program_descriptor(manifest["version_line"], gate.TOPOLOGY,
                                                               runtime_manifest=manifest),
                                  gate.MDP, gate.pair_gro(0.4))
    result = gm.run_gromacs_specification(spec, exe, expected_binary_sha256=manifest["executable"]["sha256"],
        max_warnings=0, cpu_only=True, runtime_manifest=manifest, artifact_directory=tmp_path / "recorded")
    assert result.output == b"potential_kj_per_mol -0.9"
    receipt = ga.validate_receipt(tmp_path / "recorded")
    assert receipt["status"] == "completed" and len(receipt["stages"]) == 4
    assert receipt["result"]["computation_identity"] == result.computation_identity
