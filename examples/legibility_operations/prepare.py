"""Prepare a synthetic legibility compilation profile. Preparation compiles nothing.

The destination must not exist. The contract is the illustrative coupon fixture.
Compilation is not physical qualification.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ciw.control_contracts import save_new
from ciw.control_plane import experiment
from ciw.legibility_workflow import OPERATION, demo_source


def prepare(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="ncp-legibility-fixture-") as directory:
        run, contract, _artifacts = demo_source(Path(directory))
    graph = experiment(
        "agent-legibility-compile",
        model_id="legibility.synthetic-impact-fixture.v1",
        nodes=[{
            "node_id": "compile",
            "operation_id": OPERATION,
            "parameters": {"contract": contract},
            "inputs": {},
            "depends_on": [],
        }],
    )
    save_new(destination / "source.json", run)
    save_new(destination / "experiment.json", graph)
    save_new(destination / "profile.json", {
        "schema": "ciw.agent-host-profile.v1",
        "inputs": {"source": "source.json", "baseline": "experiment.json"},
        "output_dir": "agent-output",
        "allow_operations": [OPERATION],
        "candidate_domains": {},
        "comparisons": {},
        "check_suites": {},
        "max_executions": 4,
        "max_nodes": 1,
    })
    return destination / "profile.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(prepare(parser.parse_args().output_dir.resolve()))
