"""Native Session gate for atomic.lj-energy-forces.v1 (not the CPU adapter API).

A missing reviewed binding is NOT_RUN/exit 2. Every executed case must pass;
there are no skips, provider doubles, scalar fallbacks or physical claims.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
from unittest.mock import patch

from ciw import nvalchemi_contract as c
from ciw.adapters.protocol import AdapterRefusal
from ciw.instruments import make_demo_run
from ciw.nvalchemi_adapter import AlchemiAdapter
from ciw.operations.registry import OperationRegistry
from ciw.operations.runner import seal
from ciw.session import Session, read_json, write_json


def cases() -> dict[str, dict]:
    result = {"attractive_pair": c.example_input()}
    for name, distance in (("zero_crossing", 3.4), ("minimum", 3.4 * 2 ** (1 / 6)),
                           ("repulsive_pair", 3.06), ("beyond_cutoff", 9.0)):
        result[name] = c.example_input(distance)
    cluster = c.example_input()
    cluster.update(configuration_id="asymmetric-cluster", atom_ids=[f"atom-{i}" for i in range(4)],
                   atomic_numbers=[18]*4, positions=[[0, 0, 0], [4.1, .2, 0], [.8, 4.3, .5], [.5, 1.4, 4.4]])
    result["cluster"] = cluster
    translated, rotated, permuted = (deepcopy(cluster) for _ in range(3))
    translated["positions"] = [[x+2, y-3, z+1] for x, y, z in cluster["positions"]]
    rotated["positions"] = [[-y, x, z] for x, y, z in cluster["positions"]]
    for key in ("positions", "atom_ids", "atomic_numbers"):
        permuted[key] = [cluster[key][i] for i in (2, 0, 3, 1)]
    result.update(translated=translated, rotated=rotated, permuted=permuted)
    maximum = deepcopy(cluster)
    maximum.update(configuration_id="maximum-atoms", atom_ids=[f"atom-{i}" for i in range(c.MAX_ATOMS)],
                   atomic_numbers=[18]*c.MAX_ATOMS,
                   positions=[[4*x, 4*y, 4*z] for x in range(4) for y in range(4) for z in range(2)])
    result["maximum_atoms"] = maximum
    for name, delta in (("fd_plus", 1e-4), ("fd_minus", -1e-4)):
        value = deepcopy(result["attractive_pair"])
        value["positions"][0][0] += delta
        result[name] = value
    for value in result.values():
        c.validate_input(value)
    return result


def execute(session: Session, parameters: dict) -> dict:
    return session.handle({"protocol_version": 1, "request_id": "atomic-native-gate",
                           "type": "operation.execute", "payload": {
                               "operation_id": c.OPERATION, "parameters": parameters}})["payload"]


def bind(document: dict) -> AlchemiAdapter:
    c.keys(document, {"net_root", "net_revision", "python_executable", "expected_python_sha256",
                      "expected_python_version", "expected_dependencies", "device"})
    return AlchemiAdapter(**document, timeout_seconds=300)


def qualify(adapter: AlchemiAdapter, directory: Path, report: dict) -> None:
    registry = OperationRegistry()
    registry.register(adapter.operation())
    session = Session(make_demo_run(), directory / "executions", operations=registry)
    report["runtime"] = adapter.runtime_identity()
    rows = report["cases"] = {}
    checks = report["checks"] = {}

    def check(name: str, condition: bool) -> None:
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(f"native gate failed: {name}")

    inputs = cases()
    try:
        for name, parameters in inputs.items():
            row = execute(session, parameters)
            rows[name] = row
            check(name + "_completed", row["status"] == "completed")
            data = row["result"]["data"]
            check(name + "_scalar_check", data["numerical_check"]["status"] == "pass")
            check(name + "_scope", row["result"]["verification_status"] == "not_verified"
                  and row["result"]["verification_id"] is None and data["authorizes_execution"] is False
                  and data["prediction"]["execution_device"]["device"] == adapter.device)

        def values(name):
            return rows[name]["result"]["data"]["prediction"]["values"]

        check("native_finite_difference", math.isclose(
            -(values("fd_plus")["energy_eV"]-values("fd_minus")["energy_eV"])/2e-4,
            values("attractive_pair")["forces_eV_per_angstrom"][0][0], rel_tol=1e-6, abs_tol=2e-8))
        check("energy_translation_rotation_permutation", all(math.isclose(values(name)["energy_eV"],
              values("cluster")["energy_eV"], rel_tol=1e-10, abs_tol=1e-10)
              for name in ("translated", "rotated", "permuted")))
        forces = values("cluster")["forces_eV_per_angstrom"]
        check("force_rotation", all(math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-10)
              for source, target in zip(forces, values("rotated")["forces_eV_per_angstrom"])
              for a, b in zip([-source[1], source[0], source[2]], target)))
        check("force_permutation", all(math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-10)
              for i, target in zip((2, 0, 3, 1), values("permuted")["forces_eV_per_angstrom"])
              for a, b in zip(forces[i], target)))

        workspace = session.save_workspace(directory / "workspace.json")
        original = workspace.read_bytes()
        # A deliberate guard, not a provider double: offline reopen must call no process.
        with patch("ciw.adapters.subprocess._bounded_process", side_effect=AssertionError("offline execution")):
            reopened = Session.from_workspace(workspace, directory / "reopened")
            check("offline_reopen", reopened.results == session.results and reopened.executions == session.executions)
            unbound = execute(reopened, inputs["attractive_pair"])
            check("offline_no_provider_rehydration", unbound["status"] == "refused"
                  and unbound["execution"]["refusal"]["code"] == "operation_unavailable")
        check("workspace_bytes_preserved", original == workspace.read_bytes())
        # Bind explicitly after reopening. Saved records never select an executable.
        reopened.operations = registry
        replay = execute(reopened, inputs["attractive_pair"])
        report["replay"] = replay
        check("explicit_replay_completed", replay["status"] == "completed")
        first = rows["attractive_pair"]
        check("fresh_replay_identities", replay["execution"]["execution_id"] != first["execution"]["execution_id"]
              and replay["result"]["result_id"] != first["result"]["result_id"]
              and replay["result"]["data"]["prediction"]["prediction_id"] != first["result"]["data"]["prediction"]["prediction_id"]
              and replay["result"]["data"]["numerical_check_id"] != first["result"]["data"]["numerical_check_id"])
        check("replay_values", replay["result"]["data"]["prediction"]["values"] == values("attractive_pair"))
        reopened.save_workspace(directory / "replayed-workspace.json")

        report["tamper_rejections"] = []
        for mutation in ("runtime", "input", "role", "check", "prediction"):
            saved = read_json(workspace)
            result, execution = saved["results"][0], saved["executions"][0]
            if mutation == "runtime":
                result["runtime"]["device_binding"] = "cuda:0" if adapter.device == "cpu" else "cpu"
                execution["runtime"]["device_binding"] = result["runtime"]["device_binding"]
            elif mutation == "input": result["data"]["atomic_input"]["positions"][0][0] += .1
            elif mutation == "role": result["role"] = "verification"
            elif mutation == "check": result["data"]["numerical_check"]["status"] = "fail"
            else: result["data"]["prediction"]["values"]["energy_eV"] += 1
            seal(result)
            seal(execution)
            path = write_json(directory / f"tampered-{mutation}.json", saved)
            rejected = False
            try:
                with patch("ciw.adapters.subprocess._bounded_process", side_effect=AssertionError("offline execution")):
                    Session.from_workspace(path, directory / f"rejected-{mutation}")
            except ValueError:
                rejected = True
            check("tamper_" + mutation, rejected)
            report["tamper_rejections"].append(mutation)

        report["refusals"] = {}
        for name in ("periodic", "species", "overlap", "units", "cutoff"):
            invalid = c.example_input()
            if name == "periodic": invalid["pbc"][0] = True
            elif name == "species": invalid["atomic_numbers"][0] = 1
            elif name == "overlap": invalid["positions"][1] = [0, 0, 0]
            elif name == "units": invalid["units"]["position"] = "m"
            else: invalid["positions"][1][0] = 8.5
            row = execute(session, invalid)
            report["refusals"][name] = row
            check("refusal_" + name, row["status"] == "refused" and row["result"] is None)
        identities = [row["result"]["data"]["prediction"]["prediction_id"] for row in rows.values()]
        check("all_prediction_occurrences_distinct", len(set(identities)) == len(identities))
        check("runtime_unchanged", adapter.runtime_identity() == report["runtime"])
        report["status"] = "PASS"
    finally:
        session.save_workspace(directory / "final-workspace.json")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, help="Reviewed trusted-operator binding JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    directory = args.output_dir.resolve()
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        parser.error("output directory must be new or empty")
    directory.mkdir(parents=True, exist_ok=True)
    report = {"schema": "ciw.atomic-alchemi-native-gate.v1", "operation": c.OPERATION,
              "status": "NOT_RUN", "physical_validation": "not_performed", "may_authorize": False,
              "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    code = 2
    try:
        if args.binding is None:
            report["reason"] = "reviewed native binding not supplied"
        else:
            report["binding_sha256"] = sha256(args.binding.read_bytes()).hexdigest()
            report["status"] = "FAIL"
            qualify(bind(read_json(args.binding)), directory, report)
            code = 0
    except (ValueError, OSError, AssertionError, KeyError, TypeError, AdapterRefusal) as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        code = 1
    finally:
        write_json(directory / "gate.json", report)
    print(json.dumps({"operation": c.OPERATION, "status": report["status"], "report": str(directory / "gate.json")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
