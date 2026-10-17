"""Provider-free regression tests. Process doubles are NOT native GROMACS runs."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from execution import gromacs as gm
from execution.commitments import COMPUTATION_TAG, OUTPUT_TAG, canonical_u32, commit_hex
from execution.engine import EngineProtocolError
from execution.specification import ExecutionSpecification

VERSION = "GROMACS version:    test-double-1"
GRO = ("Synthetic fixture\n    1\n"
       '    1AR      AR    1   1.000   1.000   1.000\n'
       "   3.00000   3.00000   3.00000\n")
RUNNERS = [(gm.GROMACS_DESCRIPTOR_HEADER, gm.run_gromacs_specification),
           (gm.GROMACS_TRAJECTORY_HEADER, gm.run_gromacs_trajectory_specification),
           (gm.GROMACS_MINIMIZATION_HEADER, gm.run_gromacs_minimization_specification)]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    binary = tmp_path / "gmx-double"
    binary.write_bytes(b"explicit process double; not a GROMACS binary")
    state = {"energy": b"0.000000  -0.987654\n", "gro": GRO,
             "calls": [], "fail": None, "drift": None,
             "version": (VERSION + "\n").encode(), "probe_code": 0}

    def run(command, **kwargs):
        stage = command[2]
        state["calls"].append((command, kwargs))
        if stage == "--version":
            if state["drift"] == stage:
                binary.write_bytes(b"changed during probe")
            return subprocess.CompletedProcess(command, state["probe_code"], state["version"], b"")
        if state["fail"] == stage:
            return subprocess.CompletedProcess(command, 1, b"", b"deliberate stage failure")
        if stage == "energy" and state["energy"] is not None:
            payload = state["energy"]
            if callable(payload):
                payload = payload(Path(kwargs["cwd"]))
            (Path(kwargs["cwd"]) / "energy.xvg").write_bytes(payload)
        if stage == "mdrun" and state["gro"] is not None:
            (Path(kwargs["cwd"]) / "run.gro").write_text(state["gro"], encoding="ascii")
        if state["drift"] == stage:
            binary.write_bytes(b"changed during execution")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(gm.subprocess, "run", run)
    return binary, state


def spec(header=gm.GROMACS_DESCRIPTOR_HEADER):
    return ExecutionSpecification(gm.gromacs_program_descriptor(VERSION, b"fixture topology", header),
                                  b"nsteps = 0\n", GRO.encode())


@pytest.mark.parametrize("header,runner", RUNNERS)
def test_existing_result_encoding_and_identity_are_preserved(rig, header, runner):
    binary, state = rig
    result = runner(spec(header), binary)
    assert result.status == "completed" and result.exit_code == 0
    if header == gm.GROMACS_DESCRIPTOR_HEADER:
        assert result.output == b"potential_kj_per_mol -0.987654"
    elif header == gm.GROMACS_TRAJECTORY_HEADER:
        assert result.output == b"gmx-energy-series kJ/mol\n0.000000  -0.987654"
    else:
        expected = (b"gmx-minimization v1\n[energy-series]\n0.000000  -0.987654"
                    b"\n[minimized-structure]\n" + "\n".join(GRO.splitlines()[1:]).encode())
        assert result.output == expected
    assert result.output_identity == commit_hex(OUTPUT_TAG, [result.output])
    assert result.computation_identity == commit_hex(COMPUTATION_TAG, [
        bytes.fromhex(result.program_identity), bytes.fromhex(result.input_identity),
        bytes.fromhex(result.output_identity), canonical_u32(0)])
    assert result.specification_identity == spec(header).identity()
    assert result.engine_occurrence == 0
    assert [c[0][2] for c in state["calls"]] == ["--version", "grompp", "mdrun", "energy"]


@pytest.mark.parametrize("header,runner", RUNNERS)
@pytest.mark.parametrize("bad", [b"0 nan\n", b"0 inf\n", b"0 -Infinity\n", b"0 1e309\n",
                                 b"0 1e-999\n", b"0 1_000\n", b"NaN 1\n", b"-1 1\n",
                                 b"0 garbage\n", b"0 1 2\n", b"# no data\n", b"\xff\n"])
def test_nonfinite_and_malformed_energy_never_become_computations(rig, header, runner, bad):
    binary, state = rig
    state["energy"] = bad
    result = runner(spec(header), binary)
    assert (result.status, result.exit_code) == ("halted", gm.FAULT_PARSE)
    assert result.output is result.output_identity is result.computation_identity is None


@pytest.mark.parametrize("rows", [b"0 1\n0 2\n", b"2 1\n1 2\n", b"0 1\n1 2 3\n"])
def test_bad_series_time_and_shape_refuse(rig, rows):
    binary, state = rig
    state["energy"] = rows
    r = gm.run_gromacs_trajectory_specification(spec(gm.GROMACS_TRAJECTORY_HEADER), binary)
    assert r.exit_code == gm.FAULT_PARSE and r.output is None


def test_multiple_frames_are_not_a_single_point(rig):
    binary, state = rig
    state["energy"] = b"0 1\n1 2\n"
    assert gm.run_gromacs_specification(spec(), binary).exit_code == gm.FAULT_PARSE
    r = gm.run_gromacs_trajectory_specification(spec(gm.GROMACS_TRAJECTORY_HEADER), binary)
    assert r.output == b"gmx-energy-series kJ/mol\n0 1\n1 2"


@pytest.mark.parametrize("header,runner", RUNNERS)
def test_missing_output_is_a_parse_halt(rig, header, runner):
    binary, state = rig
    state["energy"] = None
    result = runner(spec(header), binary)
    assert result.exit_code == gm.FAULT_PARSE and result.output is None


@pytest.mark.parametrize("stage,code", [("grompp",gm.FAULT_GROMPP),
                                        ("mdrun",gm.FAULT_MDRUN),("energy",gm.FAULT_ENERGY)])
@pytest.mark.parametrize("header,runner", RUNNERS)
def test_stage_failure_never_runs_later_stage(rig, header, runner, stage, code):
    binary, state = rig
    state["fail"] = stage
    result = runner(spec(header), binary)
    assert result.status == "halted" and result.exit_code == code
    assert result.output is result.computation_identity is None
    assert state["calls"][-1][0][2] == stage


@pytest.mark.parametrize("suffix", [b".evil", b"0", b"\nextra"])
def test_descriptor_prefix_confusion_is_rejected_before_probe(rig, suffix):
    binary, state = rig
    bad = spec()
    bad = ExecutionSpecification(bad.program.replace(gm.GROMACS_DESCRIPTOR_HEADER,
                                gm.GROMACS_DESCRIPTOR_HEADER + suffix), bad.configuration,bad.input_payload)
    with pytest.raises(EngineProtocolError):
        gm.run_gromacs_specification(bad, binary)
    assert state["calls"] == []


@pytest.mark.parametrize("version,topology", [(VERSION+"\nextra",b"top"), ("",b"top"),
                                             ("unknown",b"top"),("GROMACS versionevil",b"top"),(VERSION,b""),(VERSION,b"  "),
                                             (VERSION+"\r",b"top"),(VERSION+"\x00",b"top")])
def test_bad_descriptor_construction(version, topology):
    with pytest.raises(EngineProtocolError):
        gm.gromacs_program_descriptor(version, topology)


@pytest.mark.parametrize("probe", [b"", b"GROMACS version:\n", b"\xff\n",
                                  (VERSION+"\n"+VERSION+"\n").encode(),
                                  b"GROMACS version: another\n"])
def test_bad_or_mismatching_probe_refuses_before_grompp(rig, probe):
    binary, state = rig
    state["version"] = probe
    with pytest.raises(EngineProtocolError):
        gm.run_gromacs_specification(spec(),binary)
    assert len(state["calls"]) == 1


def test_failed_probe_cannot_smuggle_a_valid_version(rig):
    binary, state = rig
    state["probe_code"] = 1
    with pytest.raises(EngineProtocolError):
        gm.run_gromacs_specification(spec(),binary)
    assert len(state["calls"]) == 1


@pytest.mark.parametrize("pin", ["f"*64, "sha256:"+"a"*64, "a"*63, True])
def test_wrong_pin_refuses_before_any_process(rig,pin):
    binary,state=rig
    with pytest.raises(EngineProtocolError):
        gm.run_gromacs_specification(spec(),binary,expected_binary_sha256=pin)
    assert state["calls"] == []


@pytest.mark.parametrize("stage",["--version","grompp","mdrun","energy"])
def test_executable_drift_blocks_completion(rig,stage):
    binary,state=rig
    state["drift"]=stage
    with pytest.raises(EngineProtocolError,match="changed"):
        gm.run_gromacs_specification(spec(),binary,expected_binary_sha256=gm.gmx_binary_sha256(binary))
    assert state["calls"][-1][0][2] == stage


def test_strict_gate_options_and_legacy_defaults_are_explicit(rig):
    binary,state=rig
    gm.run_gromacs_specification(spec(),binary,
             expected_binary_sha256=gm.gmx_binary_sha256(binary),max_warnings=0,cpu_only=True)
    commands=[c[0] for c in state["calls"]]
    assert commands[1][-2:] == ["-maxwarn","0"]
    for key in ("-nb","-pme","-bonded","-update"):
        assert commands[2][commands[2].index(key)+1] == "cpu"
    state["calls"].clear()
    gm.run_gromacs_specification(spec(),binary)
    assert state["calls"][1][0][-2:] == ["-maxwarn","2"]
    assert "-nb" not in state["calls"][2][0]


@pytest.mark.parametrize("options",[{"max_warnings":True},{"max_warnings":-1},
                                     {"max_warnings":3},{"cpu_only":1}])
def test_bad_runtime_options_refuse(rig,options):
    binary,state=rig
    with pytest.raises(EngineProtocolError):
        gm.run_gromacs_specification(spec(),binary,**options)
    assert not state["calls"]


def test_timeout_is_not_a_completed_calculation(rig,monkeypatch):
    binary,state=rig
    def fail(*args,**kwargs):
        raise subprocess.TimeoutExpired(args[0],60)
    monkeypatch.setattr(gm.subprocess,"run",fail)
    with pytest.raises(EngineProtocolError,match="probe"):
        gm.run_gromacs_specification(spec(),binary)


@pytest.mark.parametrize("gro",[None,"title\n", "title\n2\n", GRO.replace("1.000","    nan",1)])
def test_missing_truncated_or_nonfinite_minimized_geometry_refuses(rig,gro):
    binary,state=rig
    state["gro"]=gro
    r=gm.run_gromacs_minimization_specification(spec(gm.GROMACS_MINIMIZATION_HEADER),binary)
    assert r.exit_code==gm.FAULT_PARSE and r.output is None


def test_energy_artifact_byte_budget(rig,monkeypatch):
    binary,state=rig
    monkeypatch.setattr(gm,"MAX_ARTIFACT_BYTES",8)
    r=gm.run_gromacs_specification(spec(),binary)
    assert r.exit_code==gm.FAULT_PARSE and r.output is None


def test_energy_comments_and_verbatim_number_survive(rig):
    binary,state=rig
    state["energy"]=b"   # comment\n  @ legend ignored\n\n0.000  -9.876540e-01\n"
    r=gm.run_gromacs_specification(spec(),binary)
    assert r.output==b"potential_kj_per_mol -9.876540e-01"


def load_gate():
    import importlib.util
    path=Path(__file__).resolve().parents[4]/"scripts/check_gromacs.py"
    module_spec=importlib.util.spec_from_file_location("net_gromacs_gate_under_test",path)
    module=importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def test_native_gate_is_not_run_without_explicit_binding(tmp_path):
    import json
    gate=load_gate()
    output=tmp_path/"no-runtime"
    assert gate.main(["--output",str(output)])==2
    report=json.loads((output/"gate.json").read_text())
    assert report["status"]=="NOT_RUN" and report["runtime"] is None and report["cases"]==[]
    assert report["equipment_authority"] is False


def test_native_gate_explicit_missing_executable(tmp_path):
    import json
    gate=load_gate()
    output=tmp_path/"no-executable"
    assert gate.main(["--gmx",str(tmp_path/"missing"),"--expected-sha256","0"*64,
                      "--output",str(output)])==2
    assert json.loads((output/"gate.json").read_text())["status"]=="NOT_RUN"


def test_native_gate_reference_and_topology_are_the_same_declared_model():
    gate=load_gate()
    assert gate.scalar_reference(gate.SIGMA_NM)==pytest.approx(0.0,abs=1e-12)
    equilibrium=2**(1/6)*gate.SIGMA_NM
    assert gate.scalar_reference(equilibrium)==pytest.approx(-gate.EPSILON_KJ_MOL,abs=1e-12)
    assert gate.scalar_reference(1.1)==0
    assert b"0.3401 0.978638" in gate.TOPOLOGY
    assert b"vdw-modifier = None" in gate.MDP and b"nsteps = 0" in gate.MDP


@pytest.mark.parametrize("value",[True,float("nan"),float("inf"),0,-1,2])
def test_reference_refuses_bad_separations(value):
    with pytest.raises(ValueError):
        load_gate().scalar_reference(value)


@pytest.mark.parametrize("distance",[0.3,0.4,0.8])
def test_native_gate_gro_encoding_preserves_distances(distance):
    rows=load_gate().pair_gro(distance).decode().splitlines()
    assert len(rows)==5 and int(rows[1])==2
    x=[float(row[20:28]) for row in rows[2:4]]
    assert x[1]-x[0]==pytest.approx(distance,abs=1e-12)


def test_gate_bookkeeping_with_explicit_synthetic_process_double(rig, tmp_path):
    """These outputs use the same scalar reference: this is NOT numerical validation."""
    import json
    binary, state = rig
    gate = load_gate()

    def synthetic_energy(base):
        atoms = (base / "conf.gro").read_text().splitlines()[2:4]
        distance = float(atoms[1][20:28]) - float(atoms[0][20:28])
        return f"0 {gate.scalar_reference(distance):.17g}\n".encode()

    state["energy"] = synthetic_energy
    output = tmp_path / "synthetic-gate"
    assert gate.main(["--gmx", str(binary), "--expected-sha256", gm.gmx_binary_sha256(binary),
                      "--output", str(output)]) == 0
    report = json.loads((output / "gate.json").read_text())
    assert report["status"] == "PASS" and len(report["cases"]) == 4
    assert "test-double" in report["runtime"]["version_line"]
    assert report["physical_validation"] == "not_performed"
    assert report["source_to_binary_attestation"] == "not_established"
    assert len({row["comparison"]["comparison_id"] for row in report["cases"]}) == 4
    assert report["cases"][1]["computation_identity"] == report["cases"][3]["computation_identity"]
    assert all((output / f"case-{i}" / "result.bin").is_file() for i in range(4))


def test_gate_keeps_failed_comparison_instead_of_substituting_reference(rig, tmp_path):
    import json
    binary, state = rig
    state["energy"] = b"0 0.0\n"
    output = tmp_path / "failed-comparison"
    gate = load_gate()
    assert gate.main(["--gmx", str(binary), "--expected-sha256", gm.gmx_binary_sha256(binary),
                      "--output", str(output)]) == 1
    report = json.loads((output / "gate.json").read_text())
    assert report["status"] == "FAIL" and len(report["cases"]) == 1
    assert report["cases"][0]["comparison"]["status"] == "FAIL"
    assert (output / "case-0" / "result.bin").read_bytes() == b"potential_kj_per_mol 0.0"


def test_gate_wrong_binary_pin_refuses_before_any_probe(rig, tmp_path):
    import json
    binary, state = rig
    output = tmp_path / "wrong-pin"
    assert load_gate().main(["--gmx", str(binary), "--expected-sha256", "0" * 64,
                             "--output", str(output)]) == 1
    assert not state["calls"]
    report = json.loads((output / "gate.json").read_text())
    assert report["status"] == "FAIL" and report["cases"] == []
