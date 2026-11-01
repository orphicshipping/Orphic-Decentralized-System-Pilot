"""Prepare a synthetic scientific-system profile. Preparation does not execute.

The destination must not exist. Compile, simulate, verify, compare and study
stay grants for a later admitted plan. A completed graph is not physical
qualification.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ciw.control_contracts import save_new
from ciw.control_plane import experiment
from ciw.system_spec import demo_spec
from ciw.system_workflow import COMPARE, COMPILE, RUN, STUDY, VERIFY, source_run


def _reduction(spec, cells):
    if not any(item["target_cells"] == cells for item in spec["representations"]):
        spec["representations"].append({
            "representation_id": f"thermal-reduced-{cells}",
            "kind": "cell-average.v1",
            "source_node": "thermal",
            "target_cells": cells,
            "assumptions": ["Equal-volume averaging discards temperature gradients within cell blocks."],
        })
    return spec


def prepare(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    fine = _reduction(demo_spec(cells=16), 8)
    coarse = demo_spec(cells=8)
    run = source_run([fine, coarse])
    specs = run["metadata"]["system_specifications"]
    result = {"node_id": "", "port": "result"}
    graph = experiment(
        "agent-scientific-system",
        model_id="scientific-system.specification.v1",
        nodes=[
            {"node_id": "compile-fine", "operation_id": COMPILE, "parameters": {"specification": specs[0]},
             "inputs": {}, "depends_on": []},
            {"node_id": "compile-coarse", "operation_id": COMPILE, "parameters": {"specification": specs[1]},
             "inputs": {}, "depends_on": []},
            {"node_id": "simulate-fine", "operation_id": RUN, "parameters": {},
             "inputs": {"plan": {**result, "node_id": "compile-fine"}}, "depends_on": []},
            {"node_id": "simulate-coarse", "operation_id": RUN, "parameters": {},
             "inputs": {"plan": {**result, "node_id": "compile-coarse"}}, "depends_on": []},
            {"node_id": "verify-fine", "operation_id": VERIFY, "parameters": {},
             "inputs": {"candidate": {**result, "node_id": "simulate-fine"}}, "depends_on": []},
            {"node_id": "verify-coarse", "operation_id": VERIFY, "parameters": {},
             "inputs": {"candidate": {**result, "node_id": "simulate-coarse"}}, "depends_on": []},
            {"node_id": "compare", "operation_id": COMPARE, "parameters": {},
             "inputs": {"left": {**result, "node_id": "simulate-fine"},
                        "right": {**result, "node_id": "simulate-coarse"}}, "depends_on": []},
            {"node_id": "study", "operation_id": STUDY, "parameters": {},
             "inputs": {"plan": {**result, "node_id": "compile-fine"}}, "depends_on": []},
        ],
    )
    save_new(destination / "source.json", run)
    save_new(destination / "experiment.json", graph)
    save_new(destination / "profile.json", {
        "schema": "ciw.agent-host-profile.v1",
        "inputs": {"source": "source.json", "baseline": "experiment.json"},
        "output_dir": "agent-output",
        "allow_operations": [COMPILE, RUN, VERIFY, COMPARE, STUDY],
        "candidate_domains": {},
        "comparisons": {},
        "check_suites": {},
        "max_executions": 4,
        "max_nodes": 8,
    })
    return destination / "profile.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(prepare(parser.parse_args().output_dir.resolve()))
