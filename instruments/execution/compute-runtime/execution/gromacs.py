"""GROMACS workloads: real scientific computations behind the STE seam.

Three workloads, three descriptors, one shared grompp->mdrun->energy
pipeline (stage 4 widened this module from the original single-point
calculation):

    ste.gromacs.single-point-energy.v1   one Potential value (stage 1)
    ste.gromacs.trajectory-energy.v1     a short deterministic MD
                                         trajectory; output = the full
                                         per-frame Potential series
    ste.gromacs.energy-minimization.v1   steepest-descent relaxation of
                                         a structured input; output =
                                         the convergence series PLUS the
                                         minimized structure

Original module docstring (still accurate for the shared machinery):

Runs `gmx grompp` -> `gmx mdrun` (nsteps=0) -> `gmx energy` over a
caller-supplied system and reports the potential energy -- through the
SAME `ExecutionSpecification` / `ExecutionResult` types and the same
identity discipline as the Rust engine, so a GROMACS computation and a
native-kernel computation are the same KIND of object to everything
downstream.

The dimension mapping (and why it is honest):

    program        the workload descriptor: engine name+version line and
                   the full topology bytes -- WHAT would be computed
    configuration  the .mdp bytes -- the parameters GOVERNING the run
    input          the .gro bytes -- the system the run is OVER

WHERE THIS SITS ON THE TRUST SPECTRUM -- weaker than the Rust engine,
and stated rather than blurred. The Rust engine echoes every identity
and is recomputed against (`execution/engine.py`); GROMACS knows nothing
of our identities, so every identity here is computed BY THIS MODULE
from bytes it holds. The coupling between the descriptor and gmx's
actual behavior is DECLARED (the descriptor's version line is trusted to
name the binary that ran -- verified against `gmx --version` output at
descriptor-build time AND at run time). Optional operator-supplied
executable SHA-256 pins and between-stage drift checks bind executable
bytes, not shared libraries, drivers, source-to-binary correspondence or
scientific correctness. This remains a bytes-vs-behavior gap. What the
workload still gains from the substrate: content-addressed request and
computation identities, refusal semantics, the operation ledger at the
dispatch seam, and an empirical repeat-determinism check in its tests.

DETERMINISM SCOPE: same binary, same machine -- exercised by running
twice and comparing computation identities. Bit-identical results ACROSS
machines are NOT claimed (this is floating-point code; the build pins
double precision and no SIMD, which helps and is not a proof). The
output bytes are gmx's OWN text for the energy value, taken verbatim
from the .xvg -- no float re-formatting on our side that could
introduce a second source of divergence.

COMPUTATION != MEASUREMENT applies with full force: a computed argon
pair energy is a computation about a model system. Nothing here measured
anything, and the dispatch declaration downstream says `simulation:`.
"""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import math
import pathlib
import re
from decimal import Decimal
import subprocess
import tempfile
from typing import Optional

from execution.commitments import COMPUTATION_TAG, OUTPUT_TAG, canonical_u32, commit_hex
from execution.engine import EngineProtocolError, ExecutionResult
from execution.specification import ExecutionSpecification

GROMACS_DESCRIPTOR_HEADER = b"ste.gromacs.single-point-energy.v1"
GROMACS_TRAJECTORY_HEADER = b"ste.gromacs.trajectory-energy.v1"
GROMACS_MINIMIZATION_HEADER = b"ste.gromacs.energy-minimization.v1"
_TOPOLOGY_MARKER = b"\n[topology]\n"

#: Fault exit codes for the stages of a run. A failed stage HALTS the
#: execution (no output, no computation identity); it is never papered
#: over with a zero energy.
FAULT_GROMPP = 10
FAULT_MDRUN = 11
FAULT_ENERGY = 12
FAULT_PARSE = 13


MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_VERSION = re.compile(r"GROMACS version(?::[ \t]*|[ \t]+)[A-Za-z0-9][^\r\n]*\Z")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def _finite_number(text: str) -> float:
    if not _NUMBER.fullmatch(text):
        raise ValueError("expected a finite decimal number")
    value = float(text)
    if not math.isfinite(value) or (value == 0.0 and Decimal(text) != 0):
        raise ValueError("decimal value overflows or underflows binary64")
    return value


def _read_artifact(path: pathlib.Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected a regular GROMACS output artifact")
    with path.open("rb") as stream:
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("GROMACS output artifact exceeds 4 MiB")
    return raw.decode("ascii")


def _energy_lines(path: pathlib.Path, *, single: bool = False) -> list[str]:
    # Validate without reformatting: accepted energy bytes keep their identity.
    lines = [line for line in _read_artifact(path).splitlines()
             if line.strip() and not line.lstrip().startswith(("#", "@"))]
    if not lines or (single and len(lines) != 1):
        raise ValueError("unexpected number of GROMACS energy rows")
    previous = -math.inf
    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            raise ValueError("GROMACS energy rows need time and Potential only")
        time, _ = map(_finite_number, fields)
        if time < 0 or time <= previous:
            raise ValueError("GROMACS energy times must be nonnegative and increasing")
        previous = time
    return lines


def gmx_binary_sha256(gmx_path: pathlib.Path) -> str:
    """Fingerprint executable bytes; this is not dependency or build attestation."""
    path = pathlib.Path(gmx_path)
    if not path.is_absolute() or not path.is_file():
        raise EngineProtocolError("bind an absolute, existing GROMACS executable path")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def gmx_version_line(gmx_path: pathlib.Path, *, _retained=None) -> str:
    """Require a successful probe with exactly one unambiguous version line."""
    try:
        command = [str(gmx_path), "-quiet", "--version"]
        if _retained is not None:
            _retained.before("version", command, None)
        proc = subprocess.run(
            command, capture_output=True, timeout=60,
            **({} if _retained is None else _retained.process_options())
        )
        if _retained is not None:
            _retained.after("version", proc)
        if proc.returncode != 0:
            raise EngineProtocolError("GROMACS version probe exited unsuccessfully")
        lines = [line.strip() for line in proc.stdout.decode("utf-8").splitlines()
                 if line.startswith("GROMACS version")]
        if len(lines) != 1 or not _VERSION.fullmatch(lines[0]):
            raise EngineProtocolError("GROMACS must report exactly one version")
        return lines[0]
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        if _retained is not None:
            _retained.process_error("version", exc)
        raise EngineProtocolError("GROMACS version probe unavailable or malformed") from exc


def _bind_runtime(spec, gmx_path, expected_binary_sha256, max_warnings, cpu_only, *, retained=None):
    if type(max_warnings) is not int or not 0 <= max_warnings <= 2:
        raise EngineProtocolError("max_warnings must be an explicit integer in 0..2")
    if type(cpu_only) is not bool:
        raise EngineProtocolError("cpu_only must be a boolean")
    if expected_binary_sha256 is not None and (
            type(expected_binary_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", expected_binary_sha256)):
        raise EngineProtocolError("expected executable digest must be lowercase SHA-256")
    # Preserve an absolute symlink spelling so retargeting is detected as drift.
    path = pathlib.Path(gmx_path).absolute()
    digest = gmx_binary_sha256(path)
    if expected_binary_sha256 is not None and digest != expected_binary_sha256:
        raise EngineProtocolError("GROMACS executable differs from the operator pin")
    declared = spec.program.partition(_TOPOLOGY_MARKER)[0].split(b"\n")[1].decode("utf-8")
    observed_version = (gmx_version_line(path) if retained is None
                        else gmx_version_line(path, _retained=retained))
    if observed_version != declared:
        raise EngineProtocolError("GROMACS runtime version differs from the descriptor")
    if gmx_binary_sha256(path) != digest:
        raise EngineProtocolError("GROMACS executable changed during the version probe")
    return path, digest


def _checked_process(path, digest, args, *, base, stdin, timeout, retained=None):
    # The process is trusted, not sandboxed. A before/after digest is drift
    # detection, not race-free executable attestation or a resource sandbox.
    try:
        if gmx_binary_sha256(path) != digest:
            raise EngineProtocolError("GROMACS executable changed before a stage")
        command = [str(path), "-quiet", *args]
        if retained is not None:
            retained.before(args[0], command, stdin, base)
        proc = subprocess.run(command, cwd=base, input=stdin,
                              capture_output=True, timeout=timeout,
                              **({} if retained is None else retained.process_options()))
        if retained is not None:
            retained.after(args[0], proc, base)
        if gmx_binary_sha256(path) != digest:
            raise EngineProtocolError("GROMACS executable changed during a stage")
        return proc
    except (OSError, subprocess.TimeoutExpired) as exc:
        if retained is not None:
            retained.process_error(args[0], exc)
        raise EngineProtocolError("GROMACS stage could not start or timed out") from exc


def gromacs_program_descriptor(
    version_line: str, topology: bytes, header: bytes = GROMACS_DESCRIPTOR_HEADER,
    *, runtime_manifest: Optional[dict] = None,
) -> bytes:
    """Build the program bytes for a GROMACS workload.

    The engine version is part of the PROGRAM, not incidental metadata:
    two GROMACS versions are two programs, exactly as two backend
    versions are two proofs (Phase 126 §8). The header names WHICH
    GROMACS workload this is -- single-point, trajectory, minimization
    -- because they have different output semantics and are therefore
    different programs even over the same topology."""
    if (type(version_line) is not str or type(topology) is not bytes
            or type(header) is not bytes):
        raise EngineProtocolError("GROMACS descriptor requires version text and topology bytes")
    binding_line = b""
    if runtime_manifest is not None:
        from execution.gromacs_artifacts import BINDING_PREFIX, manifest_digest
        if header != GROMACS_DESCRIPTOR_HEADER or runtime_manifest.get("version_line") != version_line:
            raise EngineProtocolError("runtime manifest is bound to single-point/version only")
        binding_line = b"\n" + BINDING_PREFIX + manifest_digest(runtime_manifest).encode("ascii")
    program = header + b"\n" + version_line.encode("utf-8") + binding_line + _TOPOLOGY_MARKER + topology
    _split_descriptor(program, header)
    return program


def _split_descriptor(program: bytes, header: bytes) -> bytes:
    allowed = {GROMACS_DESCRIPTOR_HEADER, GROMACS_TRAJECTORY_HEADER,
               GROMACS_MINIMIZATION_HEADER}
    if type(program) is not bytes or type(header) is not bytes or header not in allowed:
        raise EngineProtocolError("unknown GROMACS workload descriptor")
    head, marker, topology = program.partition(_TOPOLOGY_MARKER)
    fields = head.split(b"\n")
    bound = (len(fields) == 3 and header == GROMACS_DESCRIPTOR_HEADER
             and re.fullmatch(rb"runtime-sha256:[0-9a-f]{64}", fields[2]) is not None)
    if (not marker or not topology.strip() or (len(fields) != 2 and not bound) or fields[0] != header
            or not fields[1].startswith(b"GROMACS version")
            or not fields[1][len(b"GROMACS version"):].strip(b" :\t")
            or any(byte < 32 and byte != 9 for byte in fields[1])):
        raise EngineProtocolError(f"not a {header.decode()} program descriptor")
    try:
        if not _VERSION.fullmatch(fields[1].decode("utf-8")):
            raise EngineProtocolError("invalid GROMACS version line")
    except UnicodeError as exc:
        raise EngineProtocolError("invalid GROMACS version encoding") from exc
    return topology


def run_gromacs_specification(
    spec: ExecutionSpecification,
    gmx_path: pathlib.Path,
    workdir: Optional[pathlib.Path] = None,
    *,
    expected_binary_sha256: Optional[str] = None,
    max_warnings: int = 2,
    cpu_only: bool = False,
    runtime_manifest: Optional[dict] = None,
    artifact_directory: Optional[pathlib.Path] = None,
) -> ExecutionResult:
    """Run one GROMACS single-point energy evaluation of `spec`.

    Every invocation is a fresh working directory and a fresh set of
    processes. Opt-in runtime_manifest/artifact_directory retain native bytes
    and stage bindings without changing ExecutionResult. Legacy calls retain
    their documented defaults and narrower evidence boundary. The occurrence
    field remains 0; the receipt has a separate per-invocation execution ID."""
    topology = _split_descriptor(spec.program, GROMACS_DESCRIPTOR_HEADER)
    bound_descriptor = len(spec.program.partition(_TOPOLOGY_MARKER)[0].split(b"\n")) == 3
    if runtime_manifest is None and artifact_directory is None and not bound_descriptor:
        context = nullcontext(None)
    else:
        if runtime_manifest is None or artifact_directory is None or not bound_descriptor:
            raise EngineProtocolError("retained execution requires manifest, bound descriptor and new artifact directory")
        from execution.gromacs_artifacts import RetainedRun
        context = RetainedRun(spec, gmx_path, runtime_manifest, artifact_directory,
                              expected_binary_sha256, max_warnings, cpu_only)
    with context as retained:
        gmx_path, binary_digest = _bind_runtime(
            spec, gmx_path, expected_binary_sha256, max_warnings, cpu_only, retained=retained)

        def _result(status, exit_code, output=None, detail=None):
            output_identity = None
            computation_identity = None
            if output is not None:
                output_identity = commit_hex(OUTPUT_TAG, [output])
                computation_identity = commit_hex(
                    COMPUTATION_TAG,
                    [
                        bytes.fromhex(spec.program_identity()),
                        bytes.fromhex(spec.input_identity()),
                        bytes.fromhex(output_identity),
                        canonical_u32(exit_code),
                    ],
                )
            result = ExecutionResult(
                specification=spec, specification_identity=spec.identity(),
                program_identity=spec.program_identity(), input_identity=spec.input_identity(),
                engine_occurrence=0, status=status, exit_code=exit_code,
                output=output, output_identity=output_identity,
                computation_identity=computation_identity, detail=detail,
            )
            if retained is not None:
                retained.record_result(result)
            return result

        with tempfile.TemporaryDirectory(dir=workdir) as tmp:
            base = pathlib.Path(tmp)
            (base / "system.top").write_bytes(topology)
            (base / "conf.gro").write_bytes(spec.input_payload)
            (base / "run.mdp").write_bytes(spec.configuration)

            def _gmx(args, stdin=None):
                return _checked_process(gmx_path, binary_digest, args, base=base,
                                        stdin=stdin, timeout=300, retained=retained)

            grompp = _gmx(["grompp", "-f", "run.mdp", "-c", "conf.gro",
                           "-p", "system.top", "-o", "run.tpr", "-maxwarn", str(max_warnings)]
                           + (["-po", "effective.mdp", "-pp", "processed.top"] if retained is not None else []))
            if grompp.returncode != 0:
                return _result("halted", FAULT_GROMPP,
                               detail=grompp.stderr.decode(errors="replace")[-400:])
            mdrun = _gmx(["mdrun", "-s", "run.tpr", "-deffnm", "run", "-nt", "1"]
                          + (["-nb", "cpu", "-pme", "cpu", "-bonded", "cpu", "-update", "cpu"]
                             if cpu_only else []))
            if mdrun.returncode != 0:
                return _result("halted", FAULT_MDRUN,
                               detail=mdrun.stderr.decode(errors="replace")[-400:])
            energy = _gmx(["energy", "-f", "run.edr", "-o", "energy.xvg"],
                          stdin=b"Potential\n")
            if energy.returncode != 0:
                return _result("halted", FAULT_ENERGY,
                               detail=energy.stderr.decode(errors="replace")[-400:])

            try:
                data_lines = _energy_lines(base / "energy.xvg", single=True)
                if retained is not None and _finite_number(data_lines[0].split()[0]) != 0.0:
                    raise ValueError("bound nsteps=0 result must report time zero")
            except (OSError, ValueError, UnicodeError) as exc:
                return _result("halted", FAULT_PARSE, detail=str(exc)[-400:])
            # gmx's own text for the value, verbatim -- our one canonical
            # output encoding for this workload, with no re-formatting.
            potential_text = data_lines[0].split()[1]
            output = b"potential_kj_per_mol " + potential_text.encode("ascii")
            return _result("completed", 0, output=output)


def _run_series_workload(
    spec: ExecutionSpecification,
    gmx_path: pathlib.Path,
    header: bytes,
    output_builder,
    workdir: Optional[pathlib.Path] = None,
    *,
    expected_binary_sha256: Optional[str] = None,
    max_warnings: int = 2,
    cpu_only: bool = False,
) -> ExecutionResult:
    """Shared grompp -> mdrun -> energy pipeline for the stage-4
    workloads. `output_builder(data_lines, base)` turns the xvg data
    lines (verbatim text) and the run directory into the workload's
    canonical output bytes -- each descriptor commits to its own output
    encoding, so the builder is per-workload by design."""
    topology = _split_descriptor(spec.program, header)
    gmx_path, binary_digest = _bind_runtime(
        spec, gmx_path, expected_binary_sha256, max_warnings, cpu_only)

    def _result(status, exit_code, output=None, detail=None):
        output_identity = None
        computation_identity = None
        if output is not None:
            output_identity = commit_hex(OUTPUT_TAG, [output])
            computation_identity = commit_hex(
                COMPUTATION_TAG,
                [
                    bytes.fromhex(spec.program_identity()),
                    bytes.fromhex(spec.input_identity()),
                    bytes.fromhex(output_identity),
                    canonical_u32(exit_code),
                ],
            )
        return ExecutionResult(
            specification=spec, specification_identity=spec.identity(),
            program_identity=spec.program_identity(), input_identity=spec.input_identity(),
            engine_occurrence=0, status=status, exit_code=exit_code,
            output=output, output_identity=output_identity,
            computation_identity=computation_identity, detail=detail,
        )

    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        base = pathlib.Path(tmp)
        (base / "system.top").write_bytes(topology)
        (base / "conf.gro").write_bytes(spec.input_payload)
        (base / "run.mdp").write_bytes(spec.configuration)

        def _gmx(args, stdin=None):
            return _checked_process(gmx_path, binary_digest, args, base=base,
                                    stdin=stdin, timeout=600)

        grompp = _gmx(["grompp", "-f", "run.mdp", "-c", "conf.gro",
                       "-p", "system.top", "-o", "run.tpr", "-maxwarn", str(max_warnings)])
        if grompp.returncode != 0:
            return _result("halted", FAULT_GROMPP,
                           detail=grompp.stderr.decode(errors="replace")[-400:])
        mdrun = _gmx(["mdrun", "-s", "run.tpr", "-deffnm", "run", "-nt", "1"]
                      + (["-nb", "cpu", "-pme", "cpu", "-bonded", "cpu", "-update", "cpu"]
                         if cpu_only else []))
        if mdrun.returncode != 0:
            return _result("halted", FAULT_MDRUN,
                           detail=mdrun.stderr.decode(errors="replace")[-400:])
        energy = _gmx(["energy", "-f", "run.edr", "-o", "energy.xvg"],
                      stdin=b"Potential\n")
        if energy.returncode != 0:
            return _result("halted", FAULT_ENERGY,
                           detail=energy.stderr.decode(errors="replace")[-400:])

        try:
            data_lines = _energy_lines(base / "energy.xvg")
            output = output_builder(data_lines, base)
        except (OSError, ValueError, UnicodeError) as exc:
            return _result("halted", FAULT_PARSE, detail=str(exc)[-400:])
        return _result("completed", 0, output=output)


def run_gromacs_trajectory_specification(
    spec: ExecutionSpecification,
    gmx_path: pathlib.Path,
    workdir: Optional[pathlib.Path] = None,
    *,
    expected_binary_sha256: Optional[str] = None,
    max_warnings: int = 2,
    cpu_only: bool = False,
) -> ExecutionResult:
    """A short deterministic MD trajectory (A1 / Target E).

    Output bytes: `gmx-energy-series kJ/mol\\n` + every per-frame
    Potential line from the .xvg, verbatim -- the trajectory's energy
    evolution IS the result, not just its endpoint. Everything about the
    trust spectrum, determinism scope (same binary, same machine) and
    COMPUTATION != MEASUREMENT from the single-point runner applies
    unchanged."""

    def build(data_lines, _base):
        return b"gmx-energy-series kJ/mol\n" + "\n".join(data_lines).encode("ascii")

    return _run_series_workload(
        spec, gmx_path, GROMACS_TRAJECTORY_HEADER, build, workdir,
        expected_binary_sha256=expected_binary_sha256,
        max_warnings=max_warnings, cpu_only=cpu_only)


def run_gromacs_minimization_specification(
    spec: ExecutionSpecification,
    gmx_path: pathlib.Path,
    workdir: Optional[pathlib.Path] = None,
    *,
    expected_binary_sha256: Optional[str] = None,
    max_warnings: int = 2,
    cpu_only: bool = False,
) -> ExecutionResult:
    """Steepest-descent energy minimization (A3): a structured scientific
    input (a perturbed lattice) relaxed to a structured numerical output.

    Output bytes:

        gmx-minimization v1\\n
        [energy-series]\\n     the per-step Potential convergence series
        ...\\n
        [minimized-structure]\\n
        ...                    run.gro WITHOUT its title line (the atom
                               count, atom records and box vectors; the
                               title is presentation, not result)
    """

    def build(data_lines, base):
        structure_lines = _read_artifact(base / "run.gro").splitlines()[1:]
        # This is shape validation, not evidence that minimization converged.
        if not structure_lines or not structure_lines[0].strip().isdigit():
            raise ValueError("minimized GRO atom count is missing")
        count = int(structure_lines[0])
        if count < 1 or len(structure_lines) != count + 2:
            raise ValueError("minimized GRO atom count differs from its records")
        for atom in structure_lines[1:-1]:
            if len(atom) < 44:
                raise ValueError("truncated minimized GRO atom record")
            # Standard GRO coordinates are three fixed-width fields of 8 chars.
            for start in (20, 28, 36):
                _finite_number(atom[start:start + 8].strip())
        box = structure_lines[-1].split()
        if len(box) not in (3, 9):
            raise ValueError("minimized GRO needs 3 or 9 box components")
        for value in box:
            _finite_number(value)
        return (
            b"gmx-minimization v1\n[energy-series]\n"
            + "\n".join(data_lines).encode("ascii")
            + b"\n[minimized-structure]\n"
            + "\n".join(structure_lines).encode("ascii")
        )

    return _run_series_workload(
        spec, gmx_path, GROMACS_MINIMIZATION_HEADER, build, workdir,
        expected_binary_sha256=expected_binary_sha256,
        max_warnings=max_warnings, cpu_only=cpu_only)
