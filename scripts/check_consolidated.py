"""Run every top-level regression module in an isolated process.

Isolation prevents optional native providers and trusted schema registrations
from leaking between modules. It does not provision or qualify those providers.
Full Git history is required by provenance tests; do not substitute a snapshot.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or not 1 <= args.timeout <= 3600:
        parser.error("Require 1..8 workers and a 1..3600 second module timeout")
    root = Path(__file__).resolve().parents[1]
    modules = sorted((root / "tests").glob("test_*.py"))
    if not modules:
        raise RuntimeError("No regression modules discovered")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}

    def run(module: Path) -> dict:
        start = time.monotonic()
        report = output / (module.stem + ".xml")
        with (output / (module.stem + ".log")).open("w", encoding="utf-8") as log:
            try:
                result = subprocess.run(
                    [sys.executable, "-I", "-m", "pytest", "-q", str(module), "--tb=short",
                     "--junitxml=" + str(report)], cwd=root, env=env, stdout=log,
                    stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
        if report.is_file():
            for suite in ET.parse(report).getroot().iter("testsuite"):
                for key in counts:
                    counts[key] += int(suite.get(key, "0"))
        # Pytest returns 5 for a module skipped during collection. Preserve the
        # explicit skip evidence; a missing/empty report cannot pass this gate.
        passed = (code in (0, 5) and counts["tests"] > 0
                  and counts["failures"] == counts["errors"] == 0
                  and (code == 0 or counts["skipped"] == counts["tests"]))
        row = {"module": module.name, "returncode": code, "passed": passed,
               "seconds": round(time.monotonic() - start, 2), **counts}
        (output / (module.stem + ".json")).write_text(json.dumps(row, indent=2) + "\n")
        print(json.dumps(row), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, modules))
    summary = {"modules": len(results), "passed": all(row["passed"] for row in results),
               "results": results}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
