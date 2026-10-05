"""Explicitly provider-free tests. Process doubles are not native LAMMPS evidence."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from execution import lammps as lm
from execution.engine import EngineProtocolError
from execution.specification import ExecutionSpecification

VERSION=lm.VERSION_PREFIX+"NET-TEST-DOUBLE"


def dump(value,energy=None,forces=None):
    e,f=lm.scalar_reference(value)
    if energy is None: energy=e
    if forces is None: forces=f
    rows=["ITEM: TIMESTEP","0","ITEM: NUMBER OF ATOMS",str(len(f)),
          "ITEM: BOX BOUNDS ff ff ff","-128 128","-128 128","-128 128",
          "ITEM: ATOMS id type x y z fx fy fz"]
    for i,(xyz,force) in enumerate(zip(value["positions_A"],forces)):
        rows.append(f"{i+1} 1 "+" ".join(format(x,".17g") for x in [*xyz,*force]))
    return f"NET_ENERGY_EV {energy:.17g}\n".encode(),("\n".join(rows)+"\n").encode()


@pytest.fixture
def rig(tmp_path,monkeypatch):
    binary=tmp_path/"lammps-double";binary.write_bytes(b"not a native LAMMPS executable")
    state={"calls":[],"mode":"ok","energy":None,"forces":None}
    def process(path,pin,args,base,label,timeout=30):
        if lm.binary_hash(path)!=pin: raise EngineProtocolError("wrong binary pin")
        state["calls"].append(label)
        (base/(label+".stdout")).write_bytes((VERSION+"\n").encode() if label=="help" else b"explicit synthetic process double\n")
        (base/(label+".stderr")).write_bytes(b"")
        if label=="run":
            value=lm.load((base/"input.json").read_bytes())
            e,d=dump(value,state["energy"],state["forces"])
            (base/"log.lammps").write_bytes(b"explicit synthetic process double\n")
            if state["mode"]=="warning": (base/"log.lammps").write_bytes(b"WARNING: synthetic warning\n")
            if state["mode"]=="error": (base/"log.lammps").write_bytes(b"ERROR: synthetic error\n")
            if state["mode"]=="nan": e=b"NET_ENERGY_EV NaN\n"
            if state["mode"]!="missing": (base/"energy.txt").write_bytes(e)
            (base/"atoms.dump").write_bytes(d)
            if state["mode"]=="mutate": (base/"in.lammps").write_bytes(b"altered")
        return {"stage":label,"arguments":args,"started_at":lm._now(),"finished_at":lm._now(),
                "exit_code":1 if state["mode"]=="fail" and label=="run" else 0,"environment":dict(lm.ENV_POLICY)}
    monkeypatch.setattr(lm,"_process",process)
    return binary,state


def run(rig,tmp_path,value=None,name="capture"):
    binary,state=rig;value=lm.example() if value is None else value
    spec=lm.specification(value,VERSION,lm.binary_hash(binary));base=tmp_path/name
    result=lm.run_lammps_specification(spec,binary,artifact_directory=base)
    return base,spec,result


def anchor(base): return lm.digest((base/"manifest.json").read_bytes())


def rewrite(base,fn):
    path=base/"manifest.json";r=lm.load(path.read_bytes());fn(r);path.write_bytes(lm.canonical(r))


def test_reference_exact_landmarks():
    assert lm.scalar_reference(lm.example(lm.MODEL["sigma_A"]))[0]==pytest.approx(0,abs=1e-14)
    r=2**(1/6)*lm.MODEL["sigma_A"]
    e,f=lm.scalar_reference(lm.example(r))
    assert e==pytest.approx(-lm.MODEL["epsilon_eV"],abs=1e-14)
    assert max(abs(x) for row in f for x in row)<1e-14
    assert lm.scalar_reference(lm.example(10.0))==(0.,[[0.,0.,0.],[0.,0.,0.]])


@pytest.mark.parametrize("r",[2.6,3.0,3.4,4.0,5.5,8.0,9.0])
def test_scalar_force_matches_independent_energy_finite_difference(r):
    value=lm.example(r);h=1e-5
    for atom in range(2):
        for k in range(3):
            plus,minus=deepcopy(value),deepcopy(value)
            plus["positions_A"][atom][k]+=h;minus["positions_A"][atom][k]-=h
            numeric=-(lm.scalar_reference(plus)[0]-lm.scalar_reference(minus)[0])/(2*h)
            assert numeric==pytest.approx(lm.scalar_reference(value)[1][atom][k],abs=1e-8,rel=1e-6)


def triangle():
    v=lm.example();v["positions_A"].append([2.,3.5,1.]);v["atom_ids"].append("Ar-3");return v


def test_scalar_translation_rotation_and_permutation():
    v=triangle();e,f=lm.scalar_reference(v)
    translated=deepcopy(v);translated["positions_A"]=[[x+1,y-2,z+3] for x,y,z in v["positions_A"]]
    et,ft=lm.scalar_reference(translated)
    assert et==pytest.approx(e)
    for a,b in zip(ft,f): assert a==pytest.approx(b,abs=1e-14)
    rotated=deepcopy(v);rotated["positions_A"]=[[-y,x,z] for x,y,z in v["positions_A"]]
    e2,f2=lm.scalar_reference(rotated)
    assert e2==pytest.approx(e)
    for a,b in zip(f2,[[-y,x,z] for x,y,z in f]): assert a==pytest.approx(b,abs=1e-14)
    perm=deepcopy(v);perm["positions_A"]=list(reversed(v["positions_A"]));perm["atom_ids"].reverse()
    ep,fp=lm.scalar_reference(perm);assert ep==pytest.approx(e)
    for a,b in zip(fp,reversed(f)): assert a==pytest.approx(b,abs=1e-14)
    for k in range(3): assert math.fsum(row[k] for row in f)==pytest.approx(0,abs=1e-14)


@pytest.mark.parametrize("field,value",[("origin","measured"),("schema","other.v1"),
    ("frame_id","x\nrun 100"),("configuration_id",True),("atom_ids",["x","x"]),
    ("atom_ids",["x"]),("atom_ids",[1,2]),("positions_A",[[0,0,0]]),
    ("positions_A",[[True,0,0],[4,0,0]]),("positions_A",[[0,0,0],[101,0,0]]),
    ("positions_A",[[0,0,0],[float("nan"),0,0]]),("positions_A",[[0,0,0],[float("inf"),0,0]]),
    ("positions_A",[[0,0,0],[2,0,0]]),("positions_A",[[0,0,0],[8.5,0,0]]),
    ("positions_A",[[0,0],[4,0,0]])])
def test_invalid_inputs_refuse_before_process(field,value,rig,tmp_path):
    v=lm.example();v[field]=value
    with pytest.raises(ValueError): lm.specification(v,VERSION,lm.binary_hash(rig[0]))
    assert rig[1]["calls"]==[]


@pytest.mark.parametrize("bad",[b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}',b'{"x":1e-999}',b'\xff',b''])
def test_strict_json(bad):
    with pytest.raises((ValueError,UnicodeError)): lm.load(bad)


@pytest.mark.parametrize("bad",["nan","inf","Infinity","1e999","1e-999","1_000","0x01","true",""])
def test_strict_decimals(bad):
    with pytest.raises(ValueError): lm.finite(bad)


def test_exact_model_policy_and_identity():
    v=lm.example();s=lm.specification(v,VERSION,"a"*64)
    assert lm.decode_spec(s)[1]==v
    assert s.program_identity()!=lm.specification(v,VERSION,"b"*64).program_identity()
    changed=lm.example(5.);assert lm.specification(changed,VERSION,"a"*64).input_identity()!=s.input_identity()
    script=lm.input_script(v)
    for line in (b"units metal",b"pair_modify shift no tail no",b"thermo_modify norm no",b"run 0"):
        assert line in script
    assert b"include " not in script and b"shell " not in script and b"python " not in script


@pytest.mark.parametrize("part",["program","configuration","input_payload"])
def test_changed_or_noncanonical_spec_refuses(part):
    s=lm.specification(lm.example(),VERSION,"a"*64)
    d={k:getattr(s,k) for k in ("program","configuration","input_payload")};d[part]+=b"\n"
    with pytest.raises(ValueError): lm.decode_spec(ExecutionSpecification(**d))


@pytest.mark.parametrize("bad",["",VERSION+"\n",VERSION+"\x00","LAMMPS blah"])
def test_bad_version(bad):
    with pytest.raises(ValueError): lm.specification(lm.example(),bad,"a"*64)


@pytest.mark.parametrize("bad",["0"*63,"A"*64,"sha256:"+"0"*64,True,None])
def test_bad_binary_digest(bad):
    with pytest.raises(ValueError): lm.specification(lm.example(),VERSION,bad)


@pytest.mark.parametrize("change",["nan_energy","extra_energy","duplicate_id","missing_atom","coords","box","periodic","step","forces"])
def test_malformed_native_output(change):
    v=lm.example();e,d=dump(v)
    if change=="nan_energy":e=b"NET_ENERGY_EV nan\n"
    if change=="extra_energy":e+=b"NET_ENERGY_EV 0\n"
    rows=d.decode().splitlines()
    if change=="duplicate_id":rows[-1]=rows[-1].replace("2 1","1 1",1)
    if change=="missing_atom":rows.pop()
    if change=="coords":rows[-1]=rows[-1].replace("2 1 4","2 1 5",1)
    if change=="box":rows[5]="-100 100"
    if change=="periodic":rows[4]="ITEM: BOX BOUNDS pp pp pp"
    if change=="step":rows[1]="1"
    if change=="forces":rows[-1]=" ".join(rows[-1].split()[:-1]+["inf"])
    with pytest.raises(ValueError):lm.parse_outputs(v,e,("\n".join(rows)+"\n").encode())


def test_retained_result_and_offline_check(rig,tmp_path,monkeypatch):
    base,s,r=run(rig,tmp_path)
    assert r.status=="completed" and r.output_identity and r.computation_identity
    assert rig[1]["calls"]==["help","run"]
    assert set(lm.load((base/"manifest.json").read_bytes())["files"])==lm.REQUIRED
    monkeypatch.setattr(lm,"_process",lambda *a,**kw:pytest.fail("offline must not launch"))
    checked=lm.verify_capture(base,anchor(base))
    assert checked["integrity"]=="PASS" and checked["comparison"]["status"]=="PASS"
    assert checked["provider_executed"] is False and checked["equipment_authority"] is False


def test_repeated_output_identity_distinct_capture_and_verification_ids(rig,tmp_path):
    a,_,ra=run(rig,tmp_path,name="first");b,_,rb=run(rig,tmp_path,name="second")
    assert ra.output==rb.output and ra.computation_identity==rb.computation_identity
    va=lm.verify_capture(a,anchor(a));vb=lm.verify_capture(b,anchor(b))
    assert va["capture_id"]!=vb["capture_id"] and va["verification_id"]!=vb["verification_id"]


@pytest.mark.parametrize("name",sorted(lm.FILES))
def test_artifact_tampering_refuses(rig,tmp_path,name):
    base,_,_=run(rig,tmp_path);a=anchor(base)
    (base/name).write_bytes((base/name).read_bytes()+b"\n")
    with pytest.raises(ValueError):lm.verify_capture(base,a)


def test_external_manifest_anchor_is_mandatory(rig,tmp_path):
    base,_,_=run(rig,tmp_path);a=anchor(base)
    rewrite(base,lambda x:x.update(physical_validation="performed"))
    with pytest.raises(ValueError,match="anchor"):lm.verify_capture(base,a)
    with pytest.raises(ValueError):lm.verify_capture(base,anchor(base))


@pytest.mark.parametrize("mutation",["runtime","specification","result","stage","authority","script"])
def test_reanchored_semantic_corruption_still_refuses(rig,tmp_path,mutation):
    base,_,_=run(rig,tmp_path)
    def change(r):
        if mutation=="runtime":r["runtime"]["executable_sha256"]="0"*64
        if mutation=="specification":r["specification_identity"]="0"*64
        if mutation=="result":r["result"]["computation_identity"]="0"*64
        if mutation=="stage":r["stages"][1]["arguments"].append("-suffix")
        if mutation=="authority":r["equipment_authority"]=True
        if mutation=="script":
            (base/"in.lammps").write_bytes(b"run 10000\n")
            raw=(base/"in.lammps").read_bytes();r["files"]["in.lammps"]={"sha256":lm.digest(raw),"byte_count":len(raw)}
    rewrite(base,change)
    with pytest.raises(ValueError):lm.verify_capture(base,anchor(base))


@pytest.mark.parametrize("mode",["warning","error","missing","nan","mutate"])
def test_bad_process_outputs_do_not_create_success(rig,tmp_path,mode):
    rig[1]["mode"]=mode
    with pytest.raises((ValueError,OSError,EngineProtocolError)):run(rig,tmp_path)
    report=lm.load((tmp_path/"capture/manifest.json").read_bytes())
    assert report["status"]=="refused" and report["result"] is None
    assert not (tmp_path/"capture/result.bin").exists()


def test_nonzero_process_halts_without_computation(rig,tmp_path):
    rig[1]["mode"]="fail";base,_,r=run(rig,tmp_path)
    assert r.status=="halted" and r.output is r.computation_identity is None
    with pytest.raises(ValueError):lm.verify_capture(base,anchor(base))


def test_numerical_disagreement_is_retained_not_substituted(rig,tmp_path):
    rig[1]["energy"]=0.;base,_,r=run(rig,tmp_path)
    assert r.status=="completed" and lm.load(r.output)["energy_eV"]==0.
    check=lm.verify_capture(base,anchor(base));assert check["comparison"]["status"]=="FAIL"


def test_capture_cannot_overwrite(rig,tmp_path):
    run(rig,tmp_path)
    with pytest.raises(ValueError):run(rig,tmp_path)


def test_symlink_artifact_refuses(rig,tmp_path):
    base,_,_=run(rig,tmp_path);a=anchor(base);p=base/"energy.txt";target=tmp_path/"external"
    target.write_bytes(p.read_bytes());p.unlink()
    try:p.symlink_to(target)
    except OSError:pytest.skip("symlinks unavailable on this host")
    with pytest.raises(ValueError):lm.verify_capture(base,a)


def test_real_subprocess_transport_not_a_lammps_calculation(tmp_path):
    path=Path(sys.executable).resolve();pin=lm.binary_hash(path)
    stage=lm._process(path,pin,["-c","import os;print(os.environ['OMP_NUM_THREADS'])"],tmp_path,"run")
    assert stage["exit_code"]==0 and (tmp_path/"run.stdout").read_bytes().strip()==b"1"


def test_real_subprocess_timeout_not_a_lammps_calculation(tmp_path):
    path=Path(sys.executable).resolve()
    with pytest.raises(EngineProtocolError,match="TIMEOUT"):
        lm._process(path,lm.binary_hash(path),["-c","import time;time.sleep(30)"],tmp_path,"run",timeout=0.05)


def test_real_subprocess_output_limit_not_a_lammps_calculation(tmp_path,monkeypatch):
    path=Path(sys.executable).resolve();monkeypatch.setattr(lm,"MAX_TOTAL",1024)
    with pytest.raises((EngineProtocolError,ValueError)):
        lm._process(path,lm.binary_hash(path),["-c","import sys,time;sys.stdout.write('x'*100000);sys.stdout.flush();time.sleep(1)"],tmp_path,"run")


def gate():
    p=Path(__file__).resolve().parents[4]/"scripts/check_lammps.py"
    s=importlib.util.spec_from_file_location("lammps_gate_test",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def test_cli_missing_binding_not_run(tmp_path):
    out=tmp_path/"no-binding"
    assert gate().main(["qualify","--output",str(out)])==2
    report=json.loads((out/"gate.json").read_text());assert report["status"]=="NOT_RUN" and report["cases"]==[]


def test_cli_missing_executable_not_run(tmp_path):
    out=tmp_path/"no-binary"
    assert gate().main(["qualify","--lmp",str(tmp_path/"missing"),"--expected-sha256","0"*64,"--output",str(out)])==2


def test_cli_example_run_verify_replay_with_labelled_double(rig,tmp_path):
    m=gate();binary,_=rig;pin=lm.binary_hash(binary)
    source=tmp_path/"input.json";assert m.main(["example","--output",str(source)])==0
    out=tmp_path/"cli-run"
    assert m.main(["run","--input",str(source),"--lmp",str(binary),"--expected-sha256",pin,"--output",str(out)])==0
    cap=out/"case-0";a=anchor(cap)
    assert m.main(["verify","--capture",str(cap),"--manifest-sha256",a])==0
    replay=tmp_path/"cli-replay"
    assert m.main(["replay","--capture",str(cap),"--manifest-sha256",a,"--lmp",str(binary),"--expected-sha256",pin,"--output",str(replay)])==0
    assert lm.load((replay/"case-0/manifest.json").read_bytes())["capture_id"]!=lm.load((cap/"manifest.json").read_bytes())["capture_id"]
    assert anchor(cap)==a


def test_cli_qualification_bookkeeping_is_not_native_evidence(rig,tmp_path):
    binary,_=rig;out=tmp_path/"qualification-double"
    assert gate().main(["qualify","--lmp",str(binary),"--expected-sha256",lm.binary_hash(binary),"--output",str(out)])==0
    report=json.loads((out/"gate.json").read_text())
    assert "TEST-DOUBLE" in report["runtime"]["version_line"] and len(report["cases"])==6
    assert report["physical_validation"]=="not_performed"


@pytest.mark.parametrize("field,value",[("forces_eV_per_A",[]),("forces_eV_per_A",[[0,0,0]]),
    ("energy_eV",True),("energy_eV",float("nan")),("forces_eV_per_A",[[0,0,0],[0,0,float("inf")]]),
    ("atom_ids",["wrong","identity"]),("uncertainty",0)])
def test_comparison_does_not_accept_truncated_or_malformed_prediction(field,value):
    v=lm.example();e,d=dump(v);out=lm.parse_outputs(v,e,d);out[field]=value
    with pytest.raises(ValueError):lm.compare(v,out)


@pytest.mark.parametrize("field,value",[("dependency_attestation","verified"),
    ("source_to_binary_attestation","verified"),("capture_id","bad"),
    ("finished_at","2000-01-01T00:00:00+00:00"),("created_at","2026-10-05T00:00:00")])
def test_reanchored_false_attestation_and_clock_claims_refuse(rig,tmp_path,field,value):
    base,_,_=run(rig,tmp_path);rewrite(base,lambda r:r.update({field:value}))
    with pytest.raises(ValueError):lm.verify_capture(base,anchor(base))


def test_reanchored_thread_policy_refuses(rig,tmp_path):
    base,_,_=run(rig,tmp_path)
    rewrite(base,lambda r:r["stages"][1]["environment"].update(OMP_NUM_THREADS="8"))
    with pytest.raises(ValueError):lm.verify_capture(base,anchor(base))


def test_capture_larger_than_aggregate_limit_refuses(rig,tmp_path,monkeypatch):
    monkeypatch.setattr(lm,"MAX_TOTAL",100)
    with pytest.raises(EngineProtocolError):run(rig,tmp_path)
    r=lm.load((tmp_path/"capture/manifest.json").read_bytes());assert r["status"]=="refused" and r["result"] is None


def test_wrong_binary_is_not_executed(tmp_path):
    binary=tmp_path/"lmp";binary.write_bytes(b"not executable")
    s=lm.specification(lm.example(),VERSION,"0"*64)
    with pytest.raises(EngineProtocolError,match="pin"):
        lm.run_lammps_specification(s,binary,artifact_directory=tmp_path/"capture")
    assert lm.load((tmp_path/"capture/manifest.json").read_bytes())["result"] is None


def test_actual_transport_reaps_child_on_unexpected_polling_failure(tmp_path,monkeypatch):
    # Python is a transport test process, not a LAMMPS substitute.
    binary=Path(sys.executable).resolve();children=[];original=subprocess.Popen
    def launch(*a,**kw):
        child=original(*a,**kw);children.append(child);return child
    def interrupt(_): raise RuntimeError("deliberate polling interruption")
    monkeypatch.setattr(lm.subprocess,"Popen",launch)
    monkeypatch.setattr(lm.time,"sleep",interrupt)
    with pytest.raises(RuntimeError,match="polling interruption"):
        lm._process(binary,lm.binary_hash(binary),["-c","import time; time.sleep(60)"],tmp_path,"run")
    assert len(children)==1 and children[0].poll() is not None


def test_reanchored_boolean_byte_count_refuses(rig,tmp_path):
    base,_,_=run(rig,tmp_path)
    rewrite(base,lambda r:r["files"]["help.stderr"].update(byte_count=False))
    with pytest.raises(ValueError):lm.verify_capture(base,anchor(base))
