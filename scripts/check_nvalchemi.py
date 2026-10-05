"""Qualify the bounded CPU LJ adapter with real NVIDIA provider execution.

Requires a clean source checkout and explicitly supplied, pinned interpreter.
The analytic reference below does not import or call the vendor implementation.
This gate is numerical/software qualification, never physical validation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

from ciw.adapters.nvalchemi import NvalchemiLJBinding
from ciw.adapters.nvalchemi_contract import example_input, validate_output
from ciw.adapters.protocol import AdapterRefusal
from ciw.core.identities import content_identity, new_identity

TOLERANCES = {"energy_ev": 2e-12, "force_ev_per_angstrom": 2e-12,
              "finite_difference_force_ev_per_angstrom": 2e-8, "replay_absolute": 2e-12}


def reference(request):
    """Independent binary64 all-pairs unshifted, hard-cutoff LJ calculation."""
    positions = request["positions"]
    model = request["model"]
    energies = []
    forces = [[0.0] * 3 for _ in positions]
    for i, left in enumerate(positions):
        for j in range(i + 1, len(positions)):
            delta = [a - b for a, b in zip(left, positions[j])]
            r2 = math.fsum(x * x for x in delta)
            if r2 >= model["cutoff_angstrom"] ** 2:
                continue
            s6 = (model["sigma_angstrom"] ** 2 / r2) ** 3
            energies.append(4 * model["epsilon_ev"] * (s6 * s6 - s6))
            scale = 24 * model["epsilon_ev"] * (2 * s6 * s6 - s6) / r2
            for k, component in enumerate(delta):
                forces[i][k] += scale * component
                forces[j][k] -= scale * component
    return math.fsum(energies), forces


def cases():
    base = example_input()
    result = {"attractive_pair": base}
    for label, distance in (("zero_crossing", 3.4), ("energy_minimum", 3.4 * 2 ** (1 / 6)),
                            ("repulsive_pair", 3.06), ("beyond_cutoff", 9.0)):
        request = deepcopy(base)
        request["positions"][1][0] = distance
        result[label] = request
    triangle = deepcopy(base)
    triangle["atom_ids"].append("atom-c")
    triangle["atomic_numbers"].append(18)
    triangle["positions"].append([0.0, 4.25, 0.0])
    result["three_atom"] = triangle
    translated = deepcopy(triangle)
    translated["positions"] = [[x + 8, y - 4, z + 2] for x, y, z in triangle["positions"]]
    result["translated"] = translated
    rotated = deepcopy(triangle)
    rotated["positions"] = [[-y, x, z] for x, y, z in triangle["positions"]]
    result["rotated"] = rotated
    permuted = deepcopy(triangle)
    for key in ("positions", "atom_ids", "atomic_numbers"):
        permuted[key] = [permuted[key][i] for i in (2, 0, 1)]
    result["permuted"] = permuted
    for label, shift in (("fd_plus", 1e-4), ("fd_minus", -1e-4)):
        request = deepcopy(base)
        request["positions"][0][0] += shift
        result[label] = request
    return result


def numerical_check(request, result):
    validate_output(result, request)
    energy, forces = reference(request)
    e_error = abs(result["energy"] - energy)
    f_error = max(abs(a - b) for row, other in zip(result["forces"], forces) for a, b in zip(row, other))
    return {"passed": e_error <= TOLERANCES["energy_ev"] and f_error <= TOLERANCES["force_ev_per_angstrom"],
            "energy_error_ev": e_error, "force_max_error_ev_per_angstrom": f_error}


def attempt(binding, request):
    """Retain refused attempts as well as successful, detached predictions."""
    row = {"execution_id": new_identity("execution"), "result_id": None,
           "request": deepcopy(request), "status": "refused", "result": None,
           "comparison": {"passed": False}}
    try:
        result = binding.evaluate(request)
        comparison = numerical_check(request, result)
    except AdapterRefusal as exc:
        row["refusal"] = exc.to_dict()
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        row["refusal"] = {"code": "INVALID_OUTPUT", "message": str(exc)}
    else:
        row.update(status="completed", result_id=new_identity("result"),
                   result=deepcopy(result), comparison=comparison)
    return row


def qualify(binding):
    runtime = binding.runtime_identity()
    rows = {}
    for name, request in cases().items():
        rows[name] = attempt(binding, request)
        print(f"{name}: {rows[name]['status']}", file=sys.stderr, flush=True)
    original = rows["attractive_pair"]["result"]
    fd_error = None
    plus, minus = rows["fd_plus"]["result"], rows["fd_minus"]["result"]
    if original is not None and plus is not None and minus is not None:
        derivative_force = -(plus["energy"] - minus["energy"]) / 2e-4
        fd_error = abs(derivative_force - original["forces"][0][0])
    # A new child process recompiles its own fresh Warp cache. Reopening below
    # only reads JSON; it neither creates a binding nor reruns the provider.
    replay_attempt = attempt(binding, rows["attractive_pair"]["request"])
    replay = replay_attempt["result"]
    replay_error = None
    if original is not None and replay is not None:
        replay_error = max(abs(replay["energy"] - original["energy"]),
                           *(abs(a - b) for row, other in zip(replay["forces"], original["forces"])
                             for a, b in zip(row, other)))
    refusals = {}
    for label, key, value in (("periodic", "pbc", [True, False, False]),
                              ("wrong_species", "atomic_numbers", [1, 1]),
                              ("charge", "charge", 1.0), ("spin", "spin", 1),
                              ("singular_cell", "cell", [[0.0] * 3] * 3)):
        request = example_input()
        request[key] = value
        try:
            binding.evaluate(request)
        except AdapterRefusal as exc:
            refusals[label] = exc.to_dict()
        else:
            refusals[label] = None
    checks = {"independent_pair_sum": all(row["comparison"]["passed"] for row in rows.values()),
              "force_energy_finite_difference": fd_error is not None and fd_error <= TOLERANCES["finite_difference_force_ev_per_angstrom"],
              "fresh_process_replay": replay_error is not None and replay_error <= TOLERANCES["replay_absolute"],
              "refusals": all(v and v["code"] == "INVALID_INPUT" for v in refusals.values())}
    retained = json.loads(json.dumps(rows, allow_nan=False))
    for row in retained.values():
        if row["result"] is not None:
            validate_output(row["result"], row["request"])
    checks["offline_json_reopen"] = retained == rows
    postflight_refusal = None
    try:
        checks["runtime_unchanged"] = binding.runtime_identity() == runtime
    except AdapterRefusal as exc:
        checks["runtime_unchanged"] = False
        postflight_refusal = exc.to_dict()
    report = {"schema": "ciw.nvalchemi-qualification.v1", "status": "PASS" if all(checks.values()) else "FAIL",
              "scope": "bounded_nonperiodic_cpu_lj_software_and_numerics_only",
              "provider_execution": "performed" if any(r["status"] == "completed" for r in rows.values()) else "no_successful_execution",
              "physical_validation": "not_performed",
              "gpu_qualification": "not_performed", "learned_model_qualification": "not_performed",
              "verification_id": new_identity("verification"), "runtime": runtime,
              "tolerances": TOLERANCES, "checks": checks, "cases": rows,
              "finite_difference_force_error": fd_error, "replay_max_error": replay_error,
              "replay": replay_attempt, "refusals": refusals, "postflight_refusal": postflight_refusal}
    report["report_digest"] = content_identity(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Explicit isolated ALCHEMI CPU interpreter")
    parser.add_argument("--revision", required=True, help="Full clean NET source commit")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        binding = NvalchemiLJBinding(args.repository, args.revision, python_executable=args.python)
        report = qualify(binding)
    except (AdapterRefusal, ValueError, OSError) as exc:
        # A provider may already have run when a later gate refuses: never
        # rewrite partial execution history as a successful or never-run gate.
        report = {"schema": "ciw.nvalchemi-qualification.v1", "status": "REFUSED",
                  "physical_validation": "not_performed", "refusal": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": report["status"], "output": str(args.output)}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
