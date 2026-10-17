#!/usr/bin/env python3
"""Zero-skip, installed-wheel gate for the explicit HOOMD CPU profile."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="Explicit qualified HOOMD interpreter")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    import ciw
    from ciw.hoomd_cli import run_study
    from ciw.hoomd_contract import validate_study
    root = Path(__file__).resolve().parents[1]
    package = Path(ciw.__file__).resolve()
    if package.is_relative_to(root / "src"):
        raise SystemExit("Qualification requires a clean installed wheel, not source-path injection")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "tmp").mkdir()
    environment = dict(os.environ, NET_HOOMD_PYTHON=str(args.python.absolute()), TMPDIR=str(output / "tmp"))
    environment.pop("PYTHONPATH", None)
    config = output / "pytest.ini"
    config.write_text("[pytest]\nmarkers = integration: requires explicit native CPU runtime\n")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-c", str(config),
        str(root / "tests/test_hoomd.py"), "--basetemp=" + str(output / "tests"),
        "--junitxml=" + str(output / "junit.xml")], env=environment)
    if result.returncode:
        return result.returncode
    suites = ET.parse(output / "junit.xml").getroot()
    tests = sum(int(item.attrib.get("tests", 0)) for item in suites.iter("testsuite"))
    skipped = sum(int(item.attrib.get("skipped", 0)) for item in suites.iter("testsuite"))
    if skipped or not tests:
        raise SystemExit("Qualification refuses skipped or absent native tests")
    os.environ["TMPDIR"] = str(output / "tmp")
    import tempfile
    tempfile.tempdir = str(output / "tmp")
    study = validate_study(json.loads((root / "examples/hoomd/linear-chains.json").read_text()))
    report = run_study(study, python=args.python, output_dir=output / "study")
    if any(item["outcome"]["status"] != "PASS" for item in report["independent_static_comparisons"].values()):
        raise SystemExit("Independent native-frame mathematical comparison did not pass")
    report = {"schema": "ciw.hoomd-cpu-qualification.v1", "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "installed_cpu_md_transport_replay_and_static_model_reference_checks",
        "tests_passed": tests, "tests_skipped": skipped, "installed_package": str(package),
        "runtime": report["runtime"], "native_artifact_sha256": report["native_artifact_sha256"],
        "workspace": report["workspace"], "physical_validation": "not_established",
        "material_properties": "not_qualified", "cross_device_reproducibility": "not_tested"}
    (output / "qualification.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
