#!/usr/bin/env python3
"""Explicit native gate over NET's EXISTING GROMACS single-point runner.

No binary discovery, downloads, environment installation, force-field includes,
training, or equipment actions. A missing binding returns NOT_RUN / exit 2.
Numerical agreement is against a scalar implementation of the same model,
not an experiment. Reports and synthetic input bytes are retained locally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

SOURCE_REFERENCE = {
    "repository": "notationsystems/GROMACS-Molecular-Simulation",
    "revision": "abc2dc98468993fdad6c670a75616c25c9b3e28b",
    "status": "inspected_archived_source_not_binary_attestation",
}
SIGMA_NM, EPSILON_KJ_MOL, CUTOFF_NM = 0.3401, 0.978638, 1.0
POLICY = {"energy_atol_kj_mol": 2e-5, "energy_rtol": 5e-6,
          "basis": "provisional_numerical_policy_not_calibrated_uncertainty"}
TOPOLOGY = b"""[ defaults ]
1 2 no 1.0 1.0
[ atomtypes ]
Ar 18 39.948 0.0 A 0.3401 0.978638
[ moleculetype ]
AR 1
[ atoms ]
1 Ar 1 AR AR 1 0.0 39.948
[ system ]
NET synthetic Lennard-Jones pair
[ molecules ]
AR 2
"""
MDP = b"""integrator = md
nsteps = 0
dt = 0.001
cutoff-scheme = Verlet
nstlist = 1
verlet-buffer-tolerance = -1
rlist = 1.0
coulombtype = Cut-off
rcoulomb = 1.0
vdwtype = Cut-off
vdw-modifier = None
rvdw = 1.0
DispCorr = no
pbc = xyz
constraints = none
tcoupl = no
pcoupl = no
comm-mode = None
nstcomm = 0
gen-vel = no
nstcalcenergy = 1
nstenergy = 1
nstlog = 1
"""


def pair_gro(distance_nm: float) -> bytes:
    if type(distance_nm) not in (int, float) or not math.isfinite(distance_nm):
        raise ValueError("pair separation must be finite")
    if distance_nm not in (0.3, 0.4, 0.8):
        raise ValueError("native gate accepts only its three declared separations")
    rows = ["NET synthetic model fixture, not measured", "    2"]
    for at, x in enumerate((1.0, 1.0 + distance_nm), 1):
        rows.append(f"{at:5d}{'AR':<5}{'AR':>5}{at:5d}{x:8.3f}{1.0:8.3f}{1.0:8.3f}")
    return ("\n".join(rows) + "\n   3.00000   3.00000   3.00000\n").encode("ascii")


def scalar_reference(distance_nm: float) -> float:
    if (type(distance_nm) not in (int, float) or not math.isfinite(distance_nm)
            or not 0.25 <= distance_nm <= 1.5):
        raise ValueError("reference separation outside the declared finite domain")
    if distance_nm >= CUTOFF_NM:
        return 0.0
    q = (SIGMA_NM / distance_nm) ** 6
    return 4 * EPSILON_KJ_MOL * (q*q - q)


def reference_for_specification(specification) -> float:
    """Reference tied to actual retained coordinates and exact model/MDP bytes.

    This is the declared two-particle, zero-charge, unshifted truncated LJ
    fixture. The 3 nm periodic box has no interacting images at this cutoff.
    Changing the topology, effective request or GRO convention refuses instead
    of comparing against an unrelated nominal distance.
    """
    if (specification.program.partition(b"\n[topology]\n")[2] != TOPOLOGY
            or specification.configuration != MDP):
        raise ValueError("reference model/settings do not match the executed specification")
    rows = specification.input_payload.decode("ascii").splitlines()
    if len(rows) != 5:
        raise ValueError("reference requires the exact two-atom GRO profile")
    distance = float(Decimal(rows[3][20:28].strip()) - Decimal(rows[2][20:28].strip()))
    if specification.input_payload != pair_gro(distance):
        raise ValueError("reference geometry/atom order/box differs from committed bytes")
    return scalar_reference(distance)


def report_path(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    return output / "gate.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gmx", type=Path, help="absolute path to an operator-approved executable")
    parser.add_argument("--expected-sha256", help="independently reviewed executable SHA-256")
    parser.add_argument("--runtime-binding", type=Path,
                        help="reviewed manifest for native artifact retention; executable/build/dependency hashes")
    parser.add_argument("--output", required=True, type=Path, help="new, outside-checkout evidence directory")
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    output = args.output.absolute()
    if output.resolve().is_relative_to(repo):
        parser.error("evidence output must be outside the checkout")
    destination = report_path(output)
    report = {
        "schema": "net.gromacs-pilot-gate.v1", "attempt_id": "attempt-" + uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "NOT_RUN",
        "source_reference": SOURCE_REFERENCE,
        "source_to_binary_attestation": "not_established",
        "dependency_closure_attestation": "not_established",
        "evidence_kind": "synthetic_classical_model_computation",
        "physical_validation": "not_performed", "computation_proof": "not_performed",
        "equipment_authority": False, "uncertainty": "unavailable",
        "numerical_policy": POLICY, "runtime": None, "cases": [],
        "runtime_artifact_binding": "requested" if args.runtime_binding else "not_requested",
        "scope": "existing_single_point_runner_not_full_NET_Session_qualification",
    }
    exit_code = 2
    try:
        manifest = None
        binding_missing = args.runtime_binding is not None and not args.runtime_binding.is_file()
        if args.runtime_binding is not None and not binding_missing:
            runtime_root = repo / "instruments/execution/compute-runtime"
            sys.path.insert(0, str(runtime_root))
            from execution.gromacs_artifacts import read_bytes, strict_json, validate_manifest
            manifest = validate_manifest(strict_json(read_bytes(args.runtime_binding)))
            if args.gmx is not None and str(args.gmx.absolute()) != manifest["executable"]["path"]:
                raise ValueError("--gmx contradicts the reviewed runtime manifest")
            if args.expected_sha256 is not None and args.expected_sha256 != manifest["executable"]["sha256"]:
                raise ValueError("--expected-sha256 contradicts the reviewed runtime manifest")
            args.gmx = Path(manifest["executable"]["path"])
            args.expected_sha256 = manifest["executable"]["sha256"]
        if binding_missing:
            report["blocker"] = "explicit runtime-binding manifest is unavailable; no provider was invoked"
        elif args.gmx is None or args.expected_sha256 is None:
            report["blocker"] = "supply both --gmx and --expected-sha256; no provider was invoked"
        elif not args.gmx.is_absolute() or not args.gmx.is_file():
            report["blocker"] = "the explicitly bound absolute GROMACS executable is unavailable"
            report["attempted_executable"] = str(args.gmx)
        else:
            # Import only after explicit operator binding, using the existing package.
            runtime_root = repo / "instruments/execution/compute-runtime"
            sys.path.insert(0, str(runtime_root))
            from execution.gromacs import (gmx_binary_sha256, gmx_version_line,
                                           gromacs_program_descriptor, run_gromacs_specification)
            from execution.specification import ExecutionSpecification
            actual = gmx_binary_sha256(args.gmx)
            if actual != args.expected_sha256:
                raise ValueError("GROMACS executable differs from the reviewed SHA-256")
            # Bound mode's version probe is performed inside the retained runner,
            # after every declared runtime file is checked. Do not make an
            # unretained probe before the bound environment has been validated.
            version = gmx_version_line(args.gmx) if manifest is None else manifest["version_line"]
            report["runtime"] = {"executable_sha256": actual, "version_line": version,
                                 "cpu_only": True, "threads": 1, "max_warnings": 0}
            program = gromacs_program_descriptor(version, TOPOLOGY, runtime_manifest=manifest)
            if manifest is not None:
                from execution.gromacs_artifacts import manifest_digest, validate_receipt
                report["runtime"]["manifest_sha256"] = manifest_digest(manifest)
            previous = None
            for index, distance in enumerate((0.3, 0.4, 0.8, 0.4)):
                case_dir = output / f"case-{index}"
                case_dir.mkdir()
                gro = pair_gro(distance)
                specification = ExecutionSpecification(program, MDP, gro)
                for name, raw in (("program.bin",program),("system.top",TOPOLOGY),
                                  ("run.mdp",MDP),("conf.gro",gro)):
                    (case_dir/name).write_bytes(raw)
                row = {"case_id": "case-" + uuid4().hex, "distance_nm": distance,
                       "specification_identity": specification.identity(), "status": "started",
                       "input_sha256": hashlib.sha256(gro).hexdigest()}
                report["cases"].append(row)
                result = run_gromacs_specification(
                    specification, args.gmx, expected_binary_sha256=args.expected_sha256,
                    max_warnings=0, cpu_only=True, runtime_manifest=manifest,
                    artifact_directory=None if manifest is None else case_dir / "native")
                if manifest is not None:
                    receipt = validate_receipt(case_dir / "native")
                    if receipt["result"]["computation_identity"] != result.computation_identity:
                        raise ValueError("retained receipt differs from live computation")
                    row["native_receipt_sha256"] = receipt["receipt_sha256"]
                    row["native_execution_id"] = receipt["execution_id"]
                row.update(status=result.status, exit_code=result.exit_code,
                           program_identity=result.program_identity, input_identity=result.input_identity,
                           output_identity=result.output_identity, computation_identity=result.computation_identity,
                           detail=result.detail)
                if result.status != "completed" or result.output is None:
                    raise RuntimeError(f"GROMACS case {index} halted: {result.detail}")
                (case_dir/"result.bin").write_bytes(result.output)
                actual_energy = float(result.output.split()[1])
                expected = reference_for_specification(specification)
                passed = math.isfinite(actual_energy) and math.isclose(actual_energy, expected,
                           rel_tol=POLICY["energy_rtol"], abs_tol=POLICY["energy_atol_kj_mol"])
                row["comparison"] = {"comparison_id":"comparison-" + uuid4().hex,
                    "status":"PASS" if passed else "FAIL", "observed_kj_mol":actual_energy,
                    "reference_kj_mol":expected, "residual_kj_mol":actual_energy-expected,
                    "specification_identity":specification.identity(),
                    "input_sha256":hashlib.sha256(specification.input_payload).hexdigest(),
                    "output_identity":result.output_identity,
                    "computation_identity":result.computation_identity}
                if not passed:
                    raise RuntimeError(f"GROMACS case {index} failed independent scalar comparison")
                if index == 1:
                    previous = (result.output, result.computation_identity)
                if index == 3 and previous != (result.output, result.computation_identity):
                    raise RuntimeError("same-host repeated computation changed output or identity")
            report["status"] = "PASS"
            exit_code = 0
    except Exception as exc:
        if report["cases"] and report["cases"][-1]["status"] == "started":
            report["cases"][-1]["status"] = "failed"
        report.update(status="FAIL", error={"type":type(exc).__name__,"message":str(exc)})
        exit_code = 1
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        destination.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":str(destination)},allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
