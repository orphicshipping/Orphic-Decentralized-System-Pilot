#!/usr/bin/env python3
"""Required native ALCHEMI pilot gate. Missing binding is NOT_RUN, exit 2.

An explicit operator binding is not loaded from a retained workspace. This gate
executes scientific software, not equipment. It never installs dependencies.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ciw import nvalchemi_contract as c


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, help="Explicit, reviewed local operator binding JSON")
    parser.add_argument("--output", type=Path, required=True, help="Directory for report and retained workspace")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "ciw.alchemi-native-gate.v1", "status": "NOT_RUN", "cases": [],
              "python": platform.python_version(), "platform": platform.platform(),
              "physical_validation": "not_performed", "authorizes_equipment": False}
    exit_code = 2
    try:
        if args.binding is None:
            report["reason"] = "Explicit operator-provisioned ALCHEMI binding was not supplied"
        else:
            from ciw.nvalchemi_adapter import AlchemiAdapter
            from ciw.operations.registry import OperationRegistry
            from ciw.instruments import make_demo_run
            from ciw.session import Session
            binding = c.loads(args.binding.read_bytes())
            c.keys(binding, {"net_root", "net_revision", "python_executable", "expected_python_sha256",
                             "expected_python_version", "expected_dependencies", "device"})
            adapter = AlchemiAdapter(**binding)
            registry = OperationRegistry()
            registry.register(adapter.operation())
            # Existing run.v1 carrier is explicitly synthetic. Atomic scientific
            # inputs are retained separately in parameters/data with their hashes.
            session = Session(make_demo_run(), args.output / "session", operations=registry)
            def execute(label, state):
                response = session.handle({"protocol_version": 1, "request_id": label,
                    "type": "operation.execute", "payload": {"operation_id": c.OPERATION, "parameters": state}})
                outcome = response["payload"]
                row = {"label": label, "status": outcome["status"], "execution_id": outcome["execution"]["execution_id"]}
                if outcome["status"] != "completed":
                    row["refusal"] = outcome["execution"]["refusal"]
                    report["cases"].append(row)
                    raise ValueError("Native operation refused: " + label)
                result = outcome["result"]
                row.update(result_id=result["result_id"], runtime=result["runtime"], numerical_check=result["data"]["numerical_check"])
                report["cases"].append(row)
                if row["numerical_check"]["status"] != "pass":
                    raise ValueError("Independent numerical comparison failed: " + label)
                return result
            try:
                for label, r in (("sigma", 3.4), ("minimum", 2 ** (1 / 6) * 3.4),
                                 ("attractive", 4.0), ("outside-cutoff", 9.0)):
                    execute(label, c.example_input(r))
                cluster = c.example_input()
                cluster.update(atom_ids=["a", "b", "c"], atomic_numbers=[18] * 3,
                               positions=[[0., 0., 0.], [4., 0., 0.], [1., 4., 0.]])
                execute("asymmetric-cluster", cluster)
                rotated = deepcopy(cluster)
                rotated["positions"] = [[-y + 2., x + 1., z - 3.] for x, y, z in cluster["positions"]]
                execute("rotated-translated", rotated)
                for key in ("positions", "atom_ids", "atomic_numbers"):
                    rotated[key].reverse()
                execute("permuted", rotated)
                center = execute("gradient-center", c.example_input(4.0))
                h = 1e-4
                plus = execute("gradient-plus", c.example_input(4.0 + h))
                minus = execute("gradient-minus", c.example_input(4.0 - h))
                derivative = -(plus["data"]["prediction"]["values"]["energy_eV"] - minus["data"]["prediction"]["values"]["energy_eV"]) / (2 * h)
                actual = center["data"]["prediction"]["values"]["forces_eV_per_angstrom"][1][0]
                error = abs(derivative - actual)
                report["native_force_gradient"] = {"absolute_error_eV_per_angstrom": error, "step_angstrom": h,
                                                  "limit_eV_per_angstrom": 1e-6}
                if error > 1e-6:
                    raise ValueError("Native force versus native energy gradient failed")
                repeat = execute("fresh-repeat", c.example_input(4.0))
                if repeat["execution_id"] == center["execution_id"] or repeat["result_id"] == center["result_id"]:
                    raise ValueError("Fresh execution/result identities were reused")
                report["status"] = "PASS"
                exit_code = 0
            finally:
                saved = session.save_workspace(args.output / "workspace.json")
                report["workspace"] = str(saved)
                restored = Session.from_workspace(saved, args.output / "offline-reopen")
                if restored.results != session.results or restored.executions != session.executions:
                    raise ValueError("Offline retained records differ")
                report["offline_reopen"] = "PASS"
    except Exception as exc:
        report.update(status="FAIL", reason=str(exc) or type(exc).__name__)
        exit_code = 1
    (args.output / "gate.json").write_bytes(c.canonical(report) + b"\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
