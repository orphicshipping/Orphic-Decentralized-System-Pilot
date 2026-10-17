"""Opt-in runtime/artifact binding for the existing GROMACS single-point runner.

No alternate runner, registry or executable discovery. A trusted operator binds
files; saved receipts only validate bytes and never recreate executable bindings.
File checks detect drift, not malicious races, loader closure or physical truth.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from execution.engine import EngineProtocolError, ExecutionResult, _check_result
from execution.specification import ExecutionSpecification

BINDING_SCHEMA = "ste.gromacs-runtime-binding.v1"
RECEIPT_SCHEMA = "ste.gromacs-artifact-receipt.v1"
BINDING_PREFIX = b"runtime-sha256:"
MAX_BYTES = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_RUNTIME_FILE = 1024 * 1024 * 1024
_STAGE_INPUTS = {"version": (), "grompp": ("run.mdp", "conf.gro", "system.top"),
                 "mdrun": ("run.tpr",), "energy": ("run.edr",)}
_STAGE_OUTPUTS = {"version": (), "grompp": ("run.tpr", "effective.mdp", "processed.top"),
                  "mdrun": ("run.edr", "run.log"), "energy": ("energy.xvg",)}
_POLICY = {"cpu_only": True, "threads": 1, "max_warnings": 0,
           "environment": "minimal-cpu-v1", "topology_includes": "refused"}
_LIMITS = {"source_to_binary_attestation": "not_established",
           "dependency_closure_attestation": "not_established",
           "physical_validation": "not_performed", "computation_proof": "not_performed",
           "equipment_authority": False, "uncertainty": "unavailable"}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def identity(raw: bytes) -> dict:
    return {"sha256": sha256(raw).hexdigest(), "byte_count": len(raw)}


def strict_json(raw: bytes) -> dict:
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("JSON byte budget exceeded")
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("duplicate JSON key")
            result[k] = v
        return result
    def constant(_):
        raise ValueError("nonfinite JSON constant")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if type(value) is not dict:
        raise ValueError("expected JSON object")
    canonical(value)
    return value


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("unexpected or missing binding/receipt fields")


def validate_manifest(value: dict) -> dict:
    """Data-only: validation does not open any operator-named file."""
    _keys(value, {"schema", "version_line", "executable", "build_record", "dependencies"})
    if value["schema"] != BINDING_SCHEMA:
        raise ValueError("unsupported GROMACS runtime binding")
    from execution.gromacs import _VERSION
    if type(value["version_line"]) is not str or not _VERSION.fullmatch(value["version_line"]):
        raise ValueError("invalid bound version line")
    if type(value["dependencies"]) is not list or len(value["dependencies"]) > 32:
        raise ValueError("at most 32 explicitly reviewed runtime files")
    paths = []
    for entry in [value["executable"], value["build_record"], *value["dependencies"]]:
        _keys(entry, {"path", "sha256"})
        if (type(entry["path"]) is not str or not entry["path"] or len(entry["path"]) > 4096
                or any(ord(c) < 32 for c in entry["path"])):
            raise ValueError("explicit bounded runtime path required")
        # Offline inspection is portable; platform-specific absolute-path checks
        # belong to live binding, not opening a Windows receipt on Linux.
        if not (entry["path"].startswith("/") or re.match(r"[A-Za-z]:[\\/]", entry["path"])):
            raise ValueError("runtime paths must be absolute")
        if type(entry["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("expected lowercase SHA-256")
        paths.append(entry["path"])
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate runtime artifact path")
    if len(canonical(value)) > 256 * 1024:
        raise ValueError("runtime manifest byte budget exceeded")
    return deepcopy(value)


def manifest_digest(value: dict) -> str:
    return sha256(canonical(validate_manifest(value))).hexdigest()


def read_bytes(path: Path, limit: int = MAX_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected a regular, non-symlink artifact")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("artifact byte budget exceeded")
    return raw


def check_runtime_files(manifest: dict) -> None:
    """Check only the supplied closure; do not guess libraries with ldd or PATH."""
    for entry in [manifest["executable"], manifest["build_record"], *manifest["dependencies"]]:
        path = Path(entry["path"])
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise EngineProtocolError("bound runtime file missing or symlinked: " + str(path))
        digest, size = sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_RUNTIME_FILE:
                    raise EngineProtocolError("runtime file exceeds 1 GiB")
                digest.update(chunk)
        if size == 0 or digest.hexdigest() != entry["sha256"]:
            raise EngineProtocolError("bound runtime file changed: " + str(path))


def _closed_inputs(spec: ExecutionSpecification, topology: bytes) -> None:
    # This retained mode is a single-point profile, not a general force-field
    # packager. A closed MDP option vocabulary rules out external table/file
    # inputs. Topology preprocessing/includes are refused, not silently ignored.
    for raw in (spec.program, spec.configuration, spec.input_payload):
        if type(raw) is not bytes or not 0 < len(raw) <= 1024 * 1024:
            raise EngineProtocolError("bound single-point input exceeds 1 MiB")
    if any(line.split(b";", 1)[0].lstrip().startswith(b"#") for line in topology.splitlines()):
        raise EngineProtocolError("bound profile requires self-contained topology; no directives")
    allowed = {"integrator", "nsteps", "dt", "cutoff-scheme", "nstlist", "verlet-buffer-tolerance",
               "rlist", "coulombtype", "rcoulomb", "vdwtype", "vdw-modifier", "rvdw", "dispcorr",
               "pbc", "constraints", "tcoupl", "pcoupl", "comm-mode", "nstcomm", "gen-vel",
               "nstcalcenergy", "nstenergy", "nstlog"}
    settings = {}
    for line in spec.configuration.decode("ascii").splitlines():
        line = line.partition(";")[0].strip()
        if not line:
            continue
        key, sep, val = line.partition("=")
        key, val = key.strip().lower().replace("_", "-"), val.strip()
        if not sep or key not in allowed or key in settings or not val:
            raise EngineProtocolError("unsupported, duplicate or external-file MDP option")
        settings[key] = val.lower()
    if settings.get("integrator") != "md" or settings.get("nsteps") != "0":
        raise EngineProtocolError("retained mode requires explicit md, nsteps=0")
    if settings.get("coulombtype") not in ("cut-off", "cutoff") or settings.get("vdwtype") not in ("cut-off", "cutoff"):
        raise EngineProtocolError("retained mode requires explicit cut-off interactions")


class RetainedRun:
    """Per-invocation recorder used INSIDE run_gromacs_specification."""
    def __init__(self, spec, gmx_path, manifest, destination, binary_pin, max_warnings, cpu_only):
        from execution.gromacs import GROMACS_DESCRIPTOR_HEADER, _TOPOLOGY_MARKER, _split_descriptor
        self.manifest = validate_manifest(manifest)
        self.spec, self.destination = spec, Path(destination).absolute()
        self.artifacts, self.stages, self.result = {}, [], None
        self.attempt_id, self.started_at = "execution-" + uuid4().hex, datetime.now(timezone.utc).isoformat()
        topology = _split_descriptor(spec.program, GROMACS_DESCRIPTOR_HEADER)
        expected = (GROMACS_DESCRIPTOR_HEADER + b"\n" + self.manifest["version_line"].encode()
                    + b"\n" + BINDING_PREFIX + manifest_digest(self.manifest).encode()
                    + _TOPOLOGY_MARKER + topology)
        if (spec.program != expected or str(Path(gmx_path).absolute()) != self.manifest["executable"]["path"]
                or binary_pin != self.manifest["executable"]["sha256"]
                or type(max_warnings) is not int or max_warnings != 0 or cpu_only is not True):
            raise EngineProtocolError("descriptor/runtime/options binding mismatch")
        _closed_inputs(spec, topology)
        self.initial = {"program.bin": spec.program, "run.mdp": spec.configuration,
                        "conf.gro": spec.input_payload, "system.top": topology,
                        "runtime-binding.json": canonical(self.manifest)}

    def __enter__(self):
        check_runtime_files(self.manifest)
        build = read_bytes(Path(self.manifest["build_record"]["path"]), 1024 * 1024)
        if identity(build)["sha256"] != self.manifest["build_record"]["sha256"]:
            raise EngineProtocolError("build record changed during binding")
        self.destination.mkdir(parents=False, exist_ok=False)
        for name, raw in {**self.initial, "build-record.bin": build}.items():
            self._store(name, raw)
        self._store("attempt.json", canonical({
            "schema": "ste.gromacs-started-attempt.v1", "execution_id": self.attempt_id,
            "created_at": self.started_at, "specification_identity": self.spec.identity(),
            "status": "started", "note": "no completion without receipt.json"}))
        return self

    def _store(self, name, raw):
        if type(raw) is not bytes or len(raw) > MAX_BYTES:
            raise EngineProtocolError("retained artifact exceeds byte budget")
        if name in self.artifacts:
            if self.artifacts[name] != identity(raw):
                raise EngineProtocolError("immutable retained artifact changed: " + name)
            return
        if sum(v["byte_count"] for v in self.artifacts.values()) + len(raw) > MAX_TOTAL:
            raise EngineProtocolError("retained bundle exceeds 64 MiB")
        with (self.destination / name).open("xb") as stream:
            stream.write(raw)
        self.artifacts[name] = identity(raw)

    def process_options(self):
        environment = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "OMP_NUM_THREADS": "1",
                       "GMX_MAXBACKUP": "-1", "GMX_NO_QUOTES": "1", "HOME": str(self.destination)}
        if os.name == "nt" and "SystemRoot" in os.environ:
            environment["SystemRoot"] = os.environ["SystemRoot"]
        return {"env": environment}

    def before(self, stage, command, stdin, base=None):
        check_runtime_files(self.manifest)
        if stage not in _STAGE_INPUTS or stage in [s["stage"] for s in self.stages]:
            raise EngineProtocolError("unexpected or repeated GROMACS stage")
        inputs = {}
        if base is not None:
            # Check request bytes before every stage, including the effective
            # intermediate that the next stage is about to consume.
            for name in ("run.mdp", "conf.gro", "system.top", *_STAGE_INPUTS[stage]):
                raw = read_bytes(base / name)
                if self.artifacts.get(name) != identity(raw):
                    raise EngineProtocolError("stage input differs from retained bytes: " + name)
                inputs[name] = identity(raw)
        self._store(stage + ".stdin.bin", stdin or b"")
        self.stages.append({"stage": stage, "command": list(command), "inputs": inputs,
                            "outputs": {}, "returncode": None, "status": "started",
                            "environment": self.process_options()["env"]})

    def after(self, stage, proc, base=None):
        row = self.stages[-1]
        if row["stage"] != stage:
            raise EngineProtocolError("stage receipt mismatch")
        row.update(returncode=proc.returncode, status="completed" if proc.returncode == 0 else "failed")
        self._store(stage + ".stdout", proc.stdout)
        self._store(stage + ".stderr", proc.stderr)
        if base is not None:
            for name in _STAGE_OUTPUTS[stage]:
                path = base / name
                if path.exists() or path.is_symlink():
                    raw = read_bytes(path)
                    if not raw:
                        raise EngineProtocolError("empty native artifact: " + name)
                    self._store(name, raw)
                    row["outputs"][name] = identity(raw)
                elif proc.returncode == 0:
                    raise EngineProtocolError("successful stage omitted native artifact: " + name)
            for name in row["inputs"]:
                if identity(read_bytes(base / name)) != row["inputs"][name]:
                    raise EngineProtocolError("stage mutated a bound input: " + name)
        check_runtime_files(self.manifest)

    def process_error(self, stage, exc):
        if self.stages and self.stages[-1]["stage"] == stage:
            self.stages[-1].update(status="failed", error=type(exc).__name__)
        # Timeout partial pipes are evidence, never a successful response.
        if isinstance(exc, subprocess.TimeoutExpired):
            for suffix, raw in (("stdout", exc.stdout), ("stderr", exc.stderr)):
                self._store(stage + "." + suffix, (raw or b""))

    def record_result(self, result: ExecutionResult):
        self.result = result
        if result.output is not None:
            self._store("result.bin", result.output)

    def __exit__(self, kind, exc, traceback):
        if exc is not None and self.stages:
            self.stages[-1].update(status="failed", error=kind.__name__)
        result = None
        if self.result is not None and exc is None:
            result = asdict(self.result)
            del result["specification"]
            result["output"] = "result.bin" if self.result.output is not None else None
        receipt = {"schema": RECEIPT_SCHEMA, "execution_id": self.attempt_id,
                   "created_at": self.started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
                   "status": result["status"] if result else "failed",
                   "error": None if exc is None else {"type": kind.__name__, "message": str(exc)[:2000]},
                   "runtime_manifest": self.manifest, "runtime_digest": manifest_digest(self.manifest),
                   "policy": deepcopy(_POLICY), "limits": deepcopy(_LIMITS),
                   "artifacts": self.artifacts, "stages": self.stages, "result": result}
        receipt["receipt_sha256"] = sha256(canonical(receipt)).hexdigest()
        # Completion marker is last. Existing evidence is never overwritten.
        with (self.destination / "receipt.json").open("xb") as stream:
            stream.write(canonical(receipt))
        return False


def validate_receipt(directory: Path) -> dict:
    """Offline integrity/semantic check. Never accesses manifest runtime paths."""
    from execution.gromacs import _energy_lines, _finite_number
    directory = Path(directory)
    receipt = strict_json(read_bytes(directory / "receipt.json"))
    _keys(receipt, {"schema", "execution_id", "created_at", "finished_at", "status", "error",
                    "runtime_manifest", "runtime_digest", "policy", "limits", "artifacts",
                    "stages", "result", "receipt_sha256"})
    unsigned = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    if receipt["receipt_sha256"] != sha256(canonical(unsigned)).hexdigest():
        raise ValueError("receipt integrity mismatch")
    if receipt["schema"] != RECEIPT_SCHEMA or canonical(receipt["policy"]) != canonical(_POLICY) or canonical(receipt["limits"]) != canonical(_LIMITS):
        raise ValueError("receipt schema, policy or authority differs")
    if not re.fullmatch(r"execution-[0-9a-f]{32}", str(receipt["execution_id"])):
        raise ValueError("invalid occurrence identity")
    for field in ("created_at", "finished_at"):
        if datetime.fromisoformat(receipt[field]).utcoffset() is None:
            raise ValueError("receipt timestamps require timezone")
    manifest = validate_manifest(receipt["runtime_manifest"])
    if receipt["runtime_digest"] != manifest_digest(manifest):
        raise ValueError("runtime manifest binding differs")
    artifacts = receipt["artifacts"]
    if type(artifacts) is not dict or not 1 <= len(artifacts) <= 40:
        raise ValueError("invalid artifact inventory")
    total = 0
    for name, record in artifacts.items():
        if type(name) is not str or not re.fullmatch(r"[a-z][a-z0-9.-]{0,63}", name):
            raise ValueError("invalid retained artifact name")
        raw = read_bytes(directory / name)
        if canonical(record) != canonical(identity(raw)):
            raise ValueError("retained artifact binding differs: " + name)
        total += len(raw)
    if total > MAX_TOTAL:
        raise ValueError("retained bundle exceeds byte budget")
    required = {"program.bin", "run.mdp", "conf.gro", "system.top", "runtime-binding.json", "build-record.bin", "attempt.json"}
    if not required <= artifacts.keys():
        raise ValueError("missing request/runtime artifacts")
    if read_bytes(directory / "runtime-binding.json") != canonical(manifest):
        raise ValueError("raw manifest differs")
    if artifacts["build-record.bin"]["sha256"] != manifest["build_record"]["sha256"]:
        raise ValueError("build record binding differs")
    spec = ExecutionSpecification(*(read_bytes(directory / p) for p in ("program.bin", "run.mdp", "conf.gro")))
    from execution.gromacs import gromacs_program_descriptor
    topology = read_bytes(directory / "system.top")
    expected_program = gromacs_program_descriptor(manifest["version_line"], topology,
                                                  runtime_manifest=manifest)
    if spec.program != expected_program:
        raise ValueError("program/topology/runtime binding differs")
    _closed_inputs(spec, topology)
    attempt = strict_json(read_bytes(directory / "attempt.json"))
    _keys(attempt, {"schema", "execution_id", "created_at", "specification_identity", "status", "note"})
    if (attempt["schema"] != "ste.gromacs-started-attempt.v1" or attempt["status"] != "started"
            or attempt.get("execution_id") != receipt["execution_id"]
            or attempt.get("created_at") != receipt["created_at"]
            or attempt.get("specification_identity") != spec.identity()):
        raise ValueError("started attempt differs from final receipt")
    stages = receipt["stages"]
    if (type(stages) is not list or any(type(s) is not dict for s in stages)
            or [s.get("stage") for s in stages] != list(_STAGE_INPUTS)[:len(stages)] or len(stages) > 4):
        raise ValueError("invalid retained stage order")
    expected_commands = {
        "version": ["--version"],
        "grompp": ["grompp", "-f", "run.mdp", "-c", "conf.gro", "-p", "system.top", "-o", "run.tpr",
                   "-maxwarn", "0", "-po", "effective.mdp", "-pp", "processed.top"],
        "mdrun": ["mdrun", "-s", "run.tpr", "-deffnm", "run", "-nt", "1", "-nb", "cpu",
                  "-pme", "cpu", "-bonded", "cpu", "-update", "cpu"],
        "energy": ["energy", "-f", "run.edr", "-o", "energy.xvg"],
    }
    for stage in stages:
        name = stage["stage"]
        mandatory = {"stage", "command", "inputs", "outputs", "returncode", "status", "environment"}
        if not mandatory <= stage.keys() <= mandatory | {"error"}:
            raise ValueError("unknown stage receipt fields")
        if stage["command"] != [manifest["executable"]["path"], "-quiet", *expected_commands[name]]:
            raise ValueError("retained stage command differs from fixed policy")
        expected_inputs = set() if name == "version" else {"run.mdp", "conf.gro", "system.top", *_STAGE_INPUTS[name]}
        if (type(stage["inputs"]) is not dict or type(stage["outputs"]) is not dict
                or set(stage["inputs"]) != expected_inputs or not set(stage["outputs"]) <= set(_STAGE_OUTPUTS[name])):
            raise ValueError("stage artifact coverage differs")
        for group in ("inputs", "outputs"):
            for file_name, record in stage[group].items():
                if canonical(artifacts.get(file_name)) != canonical(record):
                    raise ValueError("stage/artifact binding differs")
        env = stage["environment"]
        required_env = {"PATH", "LANG", "LC_ALL", "OMP_NUM_THREADS", "GMX_MAXBACKUP", "GMX_NO_QUOTES", "HOME"}
        if type(env) is not dict or not required_env <= env.keys() <= required_env | {"SystemRoot"}:
            raise ValueError("unexpected process environment")
        for key, val in {"LANG": "C", "LC_ALL": "C", "OMP_NUM_THREADS": "1",
                         "GMX_MAXBACKUP": "-1", "GMX_NO_QUOTES": "1"}.items():
            if env[key] != val:
                raise ValueError("process environment policy changed")
        if any(type(v) is not str or not v for v in env.values()):
            raise ValueError("invalid environment values")
        if read_bytes(directory / (name + ".stdin.bin")) != (b"Potential\n" if name == "energy" else b""):
            raise ValueError("stage stdin differs from the requested observable")
        if stage["status"] == "completed":
            if type(stage["returncode"]) is not int or stage["returncode"] != 0 or set(stage["outputs"]) != set(_STAGE_OUTPUTS[name]):
                raise ValueError("completed stage has incomplete outputs or failed exit")
            if not {name + ".stdout", name + ".stderr", name + ".stdin.bin"} <= artifacts.keys():
                raise ValueError("completed stage is missing raw pipes")
        elif stage["status"] not in {"started", "failed"}:
            raise ValueError("unknown stage state")
    result = receipt["result"]
    if receipt["status"] == "failed":
        if result is not None or type(receipt["error"]) is not dict:
            raise ValueError("failed receipt cannot claim a completed result")
        return receipt
    if type(result) is not dict or receipt["status"] != result.get("status") or receipt["error"] is not None:
        raise ValueError("result/status binding differs")
    _keys(result, {"specification_identity", "program_identity", "input_identity", "engine_occurrence",
                   "status", "exit_code", "output", "output_identity", "computation_identity", "detail"})
    if (type(result["engine_occurrence"]) is not int or result["engine_occurrence"] != 0
            or type(result["exit_code"]) is not int or result["exit_code"] not in {0, 10, 11, 12, 13}
            or result["output"] not in (None, "result.bin")):
        raise ValueError("invalid retained result fields")
    raw_output = read_bytes(directory / "result.bin") if result["output"] == "result.bin" else None
    checked = _check_result(spec, {"spec": result["specification_identity"], "program": result["program_identity"],
        "input": result["input_identity"], "occurrence": result["engine_occurrence"], "status": result["status"],
        "exit_code": result["exit_code"], "detail": result["detail"], "output": (raw_output or b"").hex(),
        "output_id": result["output_identity"], "computation": result["computation_identity"]})
    if result["status"] == "completed":
        all_outputs = {name for names in _STAGE_OUTPUTS.values() for name in names}
        if (checked.exit_code != 0 or result["detail"] is not None or raw_output is None
                or not (required | all_outputs | {"result.bin"}) <= artifacts.keys()
                or len(stages) != 4 or any(s["returncode"] != 0 or s["status"] != "completed" for s in stages)):
            raise ValueError("incomplete native evidence cannot establish completion")
        fields = _energy_lines(directory / "energy.xvg", single=True)[0].split()
        if _finite_number(fields[0]) != 0.0:
            raise ValueError("retained single-point energy must be at time zero")
        expected = b"potential_kj_per_mol " + fields[1].encode()
        if checked.output != expected:
            raise ValueError("native energy bytes differ from retained result")
        version_lines = [line.strip() for line in read_bytes(directory / "version.stdout").decode("utf-8").splitlines()
                         if line.startswith("GROMACS version")]
        if version_lines != [manifest["version_line"]]:
            raise ValueError("retained version probe differs from runtime manifest")
    else:
        if any(result[k] is not None for k in ("output", "output_identity", "computation_identity")):
            raise ValueError("halted computation cannot carry output")
        failure_stage = {10: "grompp", 11: "mdrun", 12: "energy", 13: "energy"}.get(result["exit_code"])
        if not stages or stages[-1]["stage"] != failure_stage:
            raise ValueError("halt code does not match the last executed stage")
        if result["exit_code"] != 13 and stages[-1]["returncode"] in (0, None):
            raise ValueError("native stage halt requires a failed native exit")
    return receipt
