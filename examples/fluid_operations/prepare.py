"""Prepare a synthetic two-reservoir fluid profile. Preparation does not solve.

The destination must not exist. Simulation and verification are grants for a
later admitted plan. A trajectory is not a physical measurement.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ciw.control_contracts import save_new
from ciw.control_plane import experiment
from ciw.fluid_contract import OPERATIONS, example_request
from ciw.fluid_workflow import make_source


def prepare(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    request = example_request("reservoir")
    # Same step as the published example, fewer samples, so the retained
    # trajectory fits the control document budget. Still the reservoir solver.
    request["clock"]["duration_s"] = 0.08
    request["clock"]["coarse_step_count"] = 8
    source = make_source(request)
    simulate, verify = OPERATIONS["reservoir"]
    graph = experiment(
        "agent-fluid-reservoir",
        model_id="fluid.reservoir.synthetic.v1",
        nodes=[
            {"node_id": "simulation", "operation_id": simulate, "parameters": {}, "inputs": {}, "depends_on": []},
            {"node_id": "verification", "operation_id": verify, "parameters": {},
             "inputs": {"candidate": {"node_id": "simulation", "port": "result"}}, "depends_on": []},
        ],
    )
    save_new(destination / "request.json", request)
    save_new(destination / "source.json", source)
    save_new(destination / "experiment.json", graph)
    save_new(destination / "profile.json", {
        "schema": "ciw.agent-host-profile.v1",
        "inputs": {"source": "source.json", "baseline": "experiment.json"},
        "output_dir": "agent-output",
        "allow_operations": [simulate, verify],
        "candidate_domains": {},
        "comparisons": {},
        "check_suites": {},
        "max_executions": 4,
        "max_nodes": 2,
    })
    return destination / "profile.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(prepare(parser.parse_args().output_dir.resolve()))
