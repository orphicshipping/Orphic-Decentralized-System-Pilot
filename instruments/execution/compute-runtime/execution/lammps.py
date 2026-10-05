"""Opt-in native LAMMPS LJ evaluation through existing ExecutionSpecification/Result.

No arbitrary LAMMPS scripts, plugin loading, default bindings, admission or
training. A bounded synthetic non-periodic cluster is not a polymer force field.
Capture hashes detect drift/corruption; they do not attest a binary's behavior,
shared libraries, physical validity or a trusted issuer. No native qualification
is inferred from passing tests that use process doubles.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time
from uuid import uuid4

from execution.commitments import COMPUTATION_TAG, OUTPUT_TAG, canonical_u32, commit_hex
from execution.engine import EngineProtocolError, ExecutionResult
from execution.specification import ExecutionSpecification

HEADER = b"ste.lammps.lj-energy-forces.v1\n"
INPUT_SCHEMA = "ste.lammps-lj-input.v1"
MODEL = {"epsilon_eV": 0.0104, "sigma_A": 3.4, "cutoff_A": 8.5,
         "shift": False, "tail": False, "species": "synthetic_Ar_only"}
CONFIG = {"units": "metal", "boundary": "f f f", "box_A": [-128.0,128.0],
          "step": 0, "cpu_only": True, "processes": 1, "omp_threads": 1,
          "energy_normalization": "total_not_per_atom", "model": MODEL}
POLICY = {"energy_atol_eV": 1e-8, "force_atol_eV_per_A": 1e-8, "rtol": 1e-6,
          "basis": "provisional_numerical_tolerance_not_measurement_uncertainty"}
MAX_BYTES = 4 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
ENV_POLICY = {"OMP_NUM_THREADS":"1","OMP_THREAD_LIMIT":"1","LC_ALL":"C","LANG":"C"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
VERSION_PREFIX = "Large-scale Atomic/Molecular Massively Parallel Simulator - "
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
FILES = {"program.bin","configuration.json","input.json","in.lammps",
         "help.stdout","help.stderr","run.stdout","run.stderr","log.lammps",
         "energy.txt","atoms.dump","result.bin"}
REQUIRED = FILES


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(raw):
    return sha256(raw).hexdigest()


def load(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("JSON byte budget exceeded")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("non-finite JSON constant")
    def floating(text):
        return finite(text)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=constant, parse_float=floating)
    canonical(value)
    return value


def finite(text):
    if type(text) is not str or not NUMBER.fullmatch(text):
        raise ValueError("invalid decimal")
    value = float(text)
    if not math.isfinite(value) or (value == 0.0 and Decimal(text) != 0):
        raise ValueError("non-finite, overflowing or underflowing decimal")
    return value


def validate_input(value):
    if type(value) is not dict or set(value) != {
        "schema","origin","configuration_id","frame_id","atom_ids","positions_A"}:
        raise ValueError("unexpected atomic input fields")
    if value["schema"] != INPUT_SCHEMA or value["origin"] != "synthetic":
        raise ValueError("only the synthetic LJ profile is supported")
    ids, positions = value["atom_ids"], value["positions_A"]
    if (type(ids) is not list or type(positions) is not list
            or not 2 <= len(ids) <= 32 or len(positions) != len(ids)):
        raise ValueError("require 2--32 identified atoms")
    for label in [value["configuration_id"],value["frame_id"],*ids]:
        if type(label) is not str or not NAME.fullmatch(label):
            raise ValueError("invalid scientific identity")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate atom identity")
    for row in positions:
        if type(row) is not list or len(row) != 3:
            raise ValueError("positions require three coordinates")
        for x in row:
            if type(x) not in (int,float) or not math.isfinite(x) or abs(x)>100:
                raise ValueError("coordinates must be finite numbers within +/-100 angstrom")
    for i,a in enumerate(positions):
        for b in positions[i+1:]:
            r=math.dist(a,b)
            if r < 0.75*MODEL["sigma_A"] or abs(r-MODEL["cutoff_A"]) < 0.001:
                raise ValueError("pair separation too small or within hard-cutoff exclusion band")
    return value


def example(distance=4.0):
    return validate_input({"schema":INPUT_SCHEMA,"origin":"synthetic",
        "configuration_id":"lj-pair","frame_id":"cartesian-lab",
        "atom_ids":["Ar-1","Ar-2"],"positions_A":[[0.0,0.0,0.0],[distance,0.0,0.0]]})


def _version(value):
    if (type(value) is not str or not value.startswith(VERSION_PREFIX)
            or not 1 <= len(value[len(VERSION_PREFIX):]) <= 160
            or any(ord(c)<32 or ord(c)>126 for c in value)):
        raise ValueError("invalid LAMMPS version declaration")
    return value


def specification(value, version_line, executable_sha256):
    validate_input(value)
    _version(version_line)
    if type(executable_sha256) is not str or not HEX.fullmatch(executable_sha256):
        raise ValueError("require independently reviewed executable SHA-256")
    # Executable bytes participate in PROGRAM identity, not just an advisory label.
    program=HEADER+canonical({"version_line":version_line,"executable_sha256":executable_sha256})
    return ExecutionSpecification(program,canonical(CONFIG),canonical(value))


def decode_spec(spec):
    if not isinstance(spec,ExecutionSpecification) or not spec.program.startswith(HEADER):
        raise ValueError("not the bounded LAMMPS workload")
    provider=load(spec.program[len(HEADER):]); value=load(spec.input_payload)
    if type(provider) is not dict or set(provider)!={"version_line","executable_sha256"}:
        raise ValueError("invalid program descriptor")
    rebuilt=specification(value,provider["version_line"],provider["executable_sha256"])
    if spec!=rebuilt:
        raise ValueError("noncanonical input or unsupported configuration")
    return provider,value


def input_script(value):
    validate_input(value)
    lines=["clear","units metal","dimension 3","boundary f f f","atom_style atomic",
        "processors 1 1 1","newton on","region box block -128 128 -128 128 -128 128 units box",
        "create_box 1 box"]
    for row in value["positions_A"]:
        lines.append("create_atoms 1 single "+" ".join(format(x,".17g") for x in row)+" units box")
    lines += ["mass 1 39.948","pair_style lj/cut 8.5","pair_coeff 1 1 0.0104 3.4 8.5",
        "pair_modify shift no tail no","neighbor 0.3 bin","neigh_modify delay 0 every 1 check yes",
        "velocity all set 0.0 0.0 0.0","fix integrator all nve",
        "thermo_style custom step atoms pe","thermo_modify norm no lost error format float %.17g",
        "run 0","print \"NET_ENERGY_EV $(pe:%.17g)\" file energy.txt screen no",
        "write_dump all custom atoms.dump id type x y z fx fy fz modify sort id format float %.17g"]
    return ("\n".join(lines)+"\n").encode("ascii")


def parse_outputs(value,energy_raw,dump_raw):
    validate_input(value)
    energy=energy_raw.decode("ascii").split()
    if len(energy)!=2 or energy[0]!="NET_ENERGY_EV":
        raise ValueError("expected one total energy")
    E=finite(energy[1]); lines=dump_raw.decode("ascii").splitlines(); n=len(value["atom_ids"])
    if (len(lines)!=9+n or lines[:4]!=["ITEM: TIMESTEP","0","ITEM: NUMBER OF ATOMS",str(n)]
            or lines[4]!="ITEM: BOX BOUNDS ff ff ff"
            or lines[8]!="ITEM: ATOMS id type x y z fx fy fz"):
        raise ValueError("unsupported or incomplete atomic dump")
    for line in lines[5:8]:
        if [finite(s) for s in line.split()]!=[-128.0,128.0]:
            raise ValueError("dump box differs from the declared box")
    forces=[]
    for i,line in enumerate(lines[9:]):
        fields=line.split()
        if len(fields)!=8 or fields[:2]!=[str(i+1),"1"]:
            raise ValueError("atom order, type, identity or field count differs")
        nums=[finite(s) for s in fields[2:]]
        if any(not math.isclose(x,y,rel_tol=0,abs_tol=1e-12)
               for x,y in zip(nums[:3],value["positions_A"][i])):
            raise ValueError("native coordinates changed in a run-0 evaluation")
        forces.append(nums[3:])
    return {"schema":"ste.lammps-lj-output.v1","input_sha256":digest(canonical(value)),
            "atom_ids":value["atom_ids"],"energy_eV":E,"forces_eV_per_A":forces,
            "origin":"model_prediction","uncertainty":"unavailable"}


def scalar_reference(value):
    """Independent scalar all-pairs equations; not an external-world measurement."""
    validate_input(value); xyz=value["positions_A"]; forces=[[0.,0.,0.] for _ in xyz]; energy=0.
    for i,a in enumerate(xyz):
        for j in range(i+1,len(xyz)):
            d=[a[k]-xyz[j][k] for k in range(3)]; r2=math.fsum(t*t for t in d)
            if r2>=MODEL["cutoff_A"]**2:
                continue
            q=(MODEL["sigma_A"]**2/r2)**3
            energy+=4*MODEL["epsilon_eV"]*(q*q-q)
            c=24*MODEL["epsilon_eV"]*(2*q*q-q)/r2
            for k in range(3):
                forces[i][k]+=c*d[k]; forces[j][k]-=c*d[k]
    return energy,forces


def compare(value,output):
    if (type(output) is not dict or set(output)!={"schema","input_sha256","atom_ids","energy_eV","forces_eV_per_A","origin","uncertainty"}
            or output["schema"]!="ste.lammps-lj-output.v1" or output["input_sha256"]!=digest(canonical(value))
            or output["atom_ids"]!=value["atom_ids"] or output["origin"]!="model_prediction"
            or output["uncertainty"]!="unavailable"):
        raise ValueError("comparison output binding differs")
    rows=output["forces_eV_per_A"]
    if type(rows) is not list or len(rows)!=len(value["atom_ids"]):
        raise ValueError("comparison force count differs")
    for row in rows:
        if type(row) is not list or len(row)!=3:
            raise ValueError("comparison requires three force components")
    if any(type(x) not in (int,float) or not math.isfinite(x)
           for x in [output["energy_eV"],*(x for row in rows for x in row)]):
        raise ValueError("comparison needs finite numerical output")
    energy,forces=scalar_reference(value)
    er=output["energy_eV"]-energy
    fr=[a-b for row,ref in zip(output["forces_eV_per_A"],forces) for a,b in zip(row,ref)]
    passed=math.isclose(output["energy_eV"],energy,abs_tol=POLICY["energy_atol_eV"],rel_tol=POLICY["rtol"])
    passed &= all(math.isclose(a,b,abs_tol=POLICY["force_atol_eV_per_A"],rel_tol=POLICY["rtol"])
                  for row,ref in zip(output["forces_eV_per_A"],forces) for a,b in zip(row,ref))
    return {"status":"PASS" if passed else "FAIL","policy":dict(POLICY),
            "reference_energy_eV":energy,"energy_residual_eV":er,
            "max_force_residual_eV_per_A":max(map(abs,fr))}


def read_regular(path,limit=MAX_BYTES):
    mode=path.lstat()
    if not stat.S_ISREG(mode.st_mode) or mode.st_nlink!=1 or mode.st_size>limit:
        raise ValueError("artifact is not a bounded, unlinked regular file")
    with path.open("rb") as stream:
        raw=stream.read(limit+1)
    if len(raw)>limit:
        raise ValueError("artifact byte budget exceeded")
    return raw


def binary_hash(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise EngineProtocolError("bind an absolute regular executable, not a symlink")
    h=sha256()
    with path.open("rb") as stream:
        while chunk:=stream.read(1024*1024): h.update(chunk)
    return h.hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _process(path,pin,args,base,label,timeout=30):
    """Bounded adapter I/O, not a sandbox for an untrusted native executable."""
    if binary_hash(path)!=pin:
        raise EngineProtocolError("LAMMPS executable differs from the reviewed pin")
    # No inherited preloads, Python/Julia paths, LAMMPS plugins or accelerator flags.
    env={k:os.environ[k] for k in ("PATH","SystemRoot","WINDIR","TEMP","TMP") if k in os.environ}
    env.update(ENV_POLICY)
    started=_now(); fault=None
    with (base/(label+".stdout")).open("xb") as out, (base/(label+".stderr")).open("xb") as err:
        proc=subprocess.Popen([str(path),*args],cwd=base,env=env,stdin=subprocess.DEVNULL,
            stdout=out,stderr=err,shell=False,start_new_session=os.name!="nt")
        try:
            start=time.monotonic()
            while proc.poll() is None:
                size=0
                for name in FILES:
                    p=base/name
                    if p.exists() and not p.is_symlink():
                        size+=p.stat().st_size
                if time.monotonic()-start>timeout: fault="TIMEOUT"
                if size>MAX_TOTAL or os.fstat(out.fileno()).st_size>MAX_BYTES or os.fstat(err.fileno()).st_size>MAX_BYTES:
                    fault="OUTPUT_LIMIT"
                if fault:
                    break
                time.sleep(0.01)
        finally:
            # Reap the child on timeout, cancellation or an unexpected I/O error.
            if proc.poll() is None:
                try:
                    if os.name!="nt": os.killpg(proc.pid,signal.SIGKILL)
                    else: proc.kill()
                except ProcessLookupError: pass
            proc.wait(timeout=5)
    # Reject oversized final output even if the process exited between polls.
    read_regular(base/(label+".stdout"));read_regular(base/(label+".stderr"))
    if binary_hash(path)!=pin: raise EngineProtocolError("LAMMPS executable changed during execution")
    if fault: raise EngineProtocolError(fault)
    return {"stage":label,"arguments":args,"started_at":started,"finished_at":_now(),"exit_code":proc.returncode,"environment":dict(ENV_POLICY)}


def _help_version(raw):
    lines=[x for x in raw.decode("ascii").splitlines() if x.startswith(VERSION_PREFIX)]
    if len(lines)!=1: raise ValueError("LAMMPS help did not report one unambiguous version")
    return _version(lines[0])


def probe(path,pin):
    if type(pin) is not str or not HEX.fullmatch(pin): raise ValueError("invalid reviewed SHA-256")
    with tempfile.TemporaryDirectory(prefix="net-lammps-probe-") as tmp:
        base=Path(tmp); stage=_process(Path(path),pin,["-help"],base,"help")
        if stage["exit_code"]: raise EngineProtocolError("LAMMPS help failed")
        return _help_version(read_regular(base/"help.stdout"))


def _result(spec,status,code,output=None,detail=None):
    output_id=None if output is None else commit_hex(OUTPUT_TAG,[output])
    computation=None if output is None else commit_hex(COMPUTATION_TAG,[
        bytes.fromhex(spec.program_identity()),bytes.fromhex(spec.input_identity()),
        bytes.fromhex(output_id),canonical_u32(code)])
    return ExecutionResult(spec,spec.identity(),spec.program_identity(),spec.input_identity(),
                           0,status,code,output,output_id,computation,detail)


def run_lammps_specification(spec,lmp_path,*,artifact_directory):
    """Existing runner signature extended by a required fresh capture destination.

    Compatible with SpecificationDispatcher.runner via functools.partial. The
    caller owns registration/admission. Re-running needs a new capture directory.
    """
    provider,value=decode_spec(spec); path=Path(lmp_path); dest=Path(artifact_directory).absolute()
    if dest.exists() or dest.is_symlink(): raise ValueError("capture directory must not exist")
    if dest.parent.resolve()!=dest.parent: raise ValueError("capture parent must not contain symlinks")
    dest.mkdir(parents=True,exist_ok=False)
    report={"schema":"net.lammps-capture.v1","capture_id":"capture-"+uuid4().hex,
        "created_at":_now(),"status":"refused","runtime":provider,"stages":[],"files":{},
        "physical_validation":"not_performed","uncertainty":"unavailable","equipment_authority":False,
        "dependency_attestation":"not_established","source_to_binary_attestation":"not_established",
        "specification_identity":spec.identity(),"result":None}
    result=None
    try:
        expected={"program.bin":spec.program,"configuration.json":spec.configuration,
                  "input.json":spec.input_payload,"in.lammps":input_script(value)}
        for name,raw in expected.items(): (dest/name).write_bytes(raw)
        stage=_process(path,provider["executable_sha256"],["-help"],dest,"help")
        report["stages"].append(stage)
        if stage["exit_code"] or _help_version(read_regular(dest/"help.stdout"))!=provider["version_line"]:
            raise EngineProtocolError("LAMMPS version differs from descriptor or probe failed")
        for name,raw in expected.items():
            if read_regular(dest/name)!=raw: raise EngineProtocolError("input artifact changed during probe")
        stage=_process(path,provider["executable_sha256"],
                       ["-in","in.lammps","-log","log.lammps","-echo","none"],dest,"run")
        report["stages"].append(stage)
        for name,raw in expected.items():
            if read_regular(dest/name)!=raw: raise EngineProtocolError("consumed input artifact changed")
        if stage["exit_code"]:
            result=_result(spec,"halted",10,detail="LAMMPS process failed; inspect retained logs")
        else:
            log=read_regular(dest/"log.lammps")
            combined=log+read_regular(dest/"run.stdout")+read_regular(dest/"run.stderr")
            if re.search(rb"(?m)^\s*(WARNING|ERROR)(:|\s)",combined):
                raise ValueError("LAMMPS warning/error requires review; no success promotion")
            output=parse_outputs(value,read_regular(dest/"energy.txt"),read_regular(dest/"atoms.dump"))
            result=_result(spec,"completed",0,canonical(output))
            (dest/"result.bin").write_bytes(result.output)
        report["status"]=result.status
        report["result"]={k:getattr(result,k) for k in (
            "status","exit_code","program_identity","input_identity","output_identity","computation_identity")}
    except (OSError,ValueError,EngineProtocolError,subprocess.SubprocessError) as exc:
        report["error"]={"type":type(exc).__name__,"message":str(exc)}
        raise
    finally:
        try:
            total=0
            for name in sorted(FILES):
                p=dest/name
                if p.exists() or p.is_symlink():
                    raw=read_regular(p);total+=len(raw)
                    report["files"][name]={"sha256":digest(raw),"byte_count":len(raw)}
            if total>MAX_TOTAL: raise ValueError("capture aggregate byte budget exceeded")
            if report["status"]=="completed" and set(report["files"])!=REQUIRED:
                raise ValueError("completed capture lacks required artifacts")
        except (OSError,ValueError) as exc:
            report.update(status="refused",result=None,capture_error=str(exc))
            result=None
        report["finished_at"]=_now()
        (dest/"manifest.json").write_bytes(canonical(report))
        if report["status"]=="refused" and "capture_error" in report:
            raise EngineProtocolError(report["capture_error"])
    return result


def verify_capture(directory,expected_manifest_sha256):
    """Offline verification: external hash anchor required, never executes a provider."""
    base=Path(directory).absolute()
    if base.resolve()!=base: raise ValueError("capture paths must not contain symlinks")
    raw=read_regular(base/"manifest.json")
    if type(expected_manifest_sha256) is not str or not HEX.fullmatch(expected_manifest_sha256) or digest(raw)!=expected_manifest_sha256:
        raise ValueError("capture manifest differs from external anchor")
    report=load(raw)
    required={"schema","capture_id","created_at","finished_at","status","runtime","stages","files",
              "physical_validation","uncertainty","equipment_authority","dependency_attestation",
              "source_to_binary_attestation","specification_identity","result"}
    if type(report) is not dict or set(report)!=required:
        raise ValueError("unexpected completed capture fields")
    if not re.fullmatch(r"capture-[0-9a-f]{32}",str(report["capture_id"])):
        raise ValueError("invalid capture identity")
    def timestamp(text):
        t=datetime.fromisoformat(text)
        if t.utcoffset() is None: raise ValueError("timestamps require timezone")
        return t
    if timestamp(report["finished_at"]) < timestamp(report["created_at"]):
        raise ValueError("capture clock ordering differs")
    if report["dependency_attestation"]!="not_established" or report["source_to_binary_attestation"]!="not_established":
        raise ValueError("unsupported runtime attestation claim")
    if report.get("schema")!="net.lammps-capture.v1" or report.get("status")!="completed":
        raise ValueError("capture does not contain a completed native-process result")
    if set(report["files"])!=REQUIRED: raise ValueError("capture artifact set differs")
    artifacts={};total=0
    for name,record in report["files"].items():
        raw=read_regular(base/name);total+=len(raw)
        if (type(record) is not dict or type(record.get("byte_count")) is not int
                or record!={"sha256":digest(raw),"byte_count":len(raw)}):
            raise ValueError("artifact digest/size mismatch")
        artifacts[name]=raw
    if total>MAX_TOTAL: raise ValueError("aggregate artifact budget exceeded")
    spec=ExecutionSpecification(artifacts["program.bin"],artifacts["configuration.json"],artifacts["input.json"])
    provider,value=decode_spec(spec)
    if provider!=report["runtime"] or spec.identity()!=report["specification_identity"]:
        raise ValueError("capture source/runtime binding mismatch")
    if input_script(value)!=artifacts["in.lammps"] or _help_version(artifacts["help.stdout"])!=provider["version_line"]:
        raise ValueError("captured program/help differs from its contract")
    expected_args=[["-help"],["-in","in.lammps","-log","log.lammps","-echo","none"]]
    if len(report["stages"])!=2: raise ValueError("capture stage count differs")
    for row,args,label in zip(report["stages"],expected_args,["help","run"]):
        if set(row)!={"stage","arguments","started_at","finished_at","exit_code","environment"}:
            raise ValueError("unexpected stage fields")
        if (row["environment"]!=ENV_POLICY or timestamp(row["finished_at"])<timestamp(row["started_at"])
                or timestamp(row["started_at"])<timestamp(report["created_at"])
                or timestamp(row["finished_at"])>timestamp(report["finished_at"])):
            raise ValueError("stage environment or timestamps differ")
        if row["arguments"]!=args or row["stage"]!=label or type(row["exit_code"]) is not int or row["exit_code"]!=0:
            raise ValueError("capture stage policy or completion differs")
    combined=artifacts["log.lammps"]+artifacts["run.stdout"]+artifacts["run.stderr"]
    if re.search(rb"(?m)^\s*(WARNING|ERROR)(:|\s)",combined): raise ValueError("capture contains unreviewed warnings/errors")
    output=parse_outputs(value,artifacts["energy.txt"],artifacts["atoms.dump"])
    if canonical(output)!=artifacts["result.bin"]: raise ValueError("result differs from native artifacts")
    result=_result(spec,"completed",0,artifacts["result.bin"])
    if canonical(report["result"])!=canonical({k:getattr(result,k) for k in report["result"]}) or set(report["result"])!={
            "status","exit_code","program_identity","input_identity","output_identity","computation_identity"}:
        raise ValueError("capture result identities differ")
    if report["physical_validation"]!="not_performed" or report["uncertainty"]!="unavailable" or report["equipment_authority"] is not False:
        raise ValueError("capture makes unsupported scientific/authority claims")
    return {"schema":"net.lammps-numerical-check.v1","verification_id":"verification-"+uuid4().hex,
        "manifest_sha256":expected_manifest_sha256,"capture_id":report["capture_id"],
        "specification_identity":spec.identity(),"computation_identity":result.computation_identity,
        "integrity":"PASS","comparison":compare(value,output),"provider_executed":False,
        "physical_validation":"not_performed","uncertainty":"unavailable","equipment_authority":False}
