"""Qualify current native QC additions from an isolated, noneditable wheel install.

This is synthetic record-level verification, not physical manufacturing evidence.
Legacy snapshot adapters are explicitly outside this native qualification profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

TESTS = (
    "test_polymer_manufacturing.py", "test_polymer_toolpoint_qc.py",
    "test_toolpoint_stream.py", "test_toolpoint_stream_store.py",
    "test_toolpoint_validation.py", "test_toolpoint_outcome_bench.py",
    "test_toolpoint_native_integration.py",
)
MINIMUM_TESTS = 1024


def suite(root: Path, report: Path, env: dict[str, str]) -> dict[str, int]:
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-k", "not legacy",
                    *("tests/" + name for name in TESTS), "--junitxml=" + str(report)],
                   cwd=root, env=env, check=True)
    totals = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for node in ET.parse(report).getroot().iter("testsuite"):
        for key in totals:
            totals[key] += int(node.attrib.get(key, 0))
    if totals["tests"] < MINIMUM_TESTS or any(totals[k] for k in ("failures", "errors", "skipped")):
        raise RuntimeError(f"Incomplete native QC qualification: {totals}")
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheels = list(args.wheel_dir.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"Expected exactly one wheel, found {len(wheels)}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    source = suite(root, output / "source.xml", dict(env, PYTHONPATH=str(root / "src")))
    commands = []
    with tempfile.TemporaryDirectory(prefix="net-qc-upgrades-installed-") as temporary:
        outside = Path(temporary).resolve()
        if outside.is_relative_to(root):
            raise ValueError("Installed qualification must be outside the checkout")
        (outside / "tests").mkdir()
        for name in TESTS:
            shutil.copy2(root / "tests" / name, outside / "tests" / name)
        shutil.copytree(root / "validation/polymer-manufacturing", outside / "validation/polymer-manufacturing")
        code = "from pathlib import Path; import ciw; print(Path(ciw.__file__).resolve().parent)"
        package = Path(subprocess.check_output([sys.executable, "-I", "-c", code],
                       cwd=outside, env=env, text=True).strip())
        if package.is_relative_to(root) or "site-packages" not in package.parts:
            raise ValueError(f"Expected noneditable installed package: {package}")
        count = 0
        with zipfile.ZipFile(wheels[0]) as archive:
            for name in archive.namelist():
                if not name.startswith("ciw/") or name.endswith("/"):
                    continue
                data = archive.read(name)
                if (package.parent / name).read_bytes() != data or (root / "src" / name).read_bytes() != data:
                    raise ValueError(f"Candidate source, wheel and installed bytes differ: {name}")
                count += 1
        installed = suite(outside, output / "installed.xml", env)
        executable = Path(sys.executable).parent / ("net.exe" if os.name == "nt" else "net")
        if not executable.is_file():
            raise ValueError("Missing installed native net executable")

        def run(expected, *arguments):
            result = subprocess.run([str(executable), "polymer", *map(str, arguments)],
                                    cwd=outside, env=env, capture_output=True, text=True)
            commands.append({"arguments": list(arguments), "expected_exit": expected,
                             "actual_exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            if result.returncode != expected:
                raise RuntimeError(f"Native command failed: {commands[-1]}")
            return result

        for instrument in ("stream", "detector", "outcome"):
            name = instrument + ".json"
            run(2, "qc", instrument, "demo", "--output", name)
            run(0, "qc", instrument, "verify", name)
            before = (outside / name).read_bytes()
            run(1, "qc", instrument, "demo", "--output", name)
            if (outside / name).read_bytes() != before:
                raise ValueError("Native create-only output was overwritten")
        stream = json.loads((outside / "stream.json").read_text())
        anchors = ["--expected-spec-ref", stream["spec_ref"],
                   "--expected-head-ref", stream["receipts"][-1]["receipt_ref"]]
        run(0, "qc", "store", "init", "quality.sqlite3")
        run(2, "qc", "store", "import", "quality.sqlite3", "cycle0", "stream.json", *anchors)
        run(0, "qc", "store", "verify", "quality.sqlite3", "cycle0", *anchors)
        run(2, "qc", "store", "export", "quality.sqlite3", "cycle0", *anchors, "--output", "reopened.json")
        reopened = json.loads((outside / "reopened.json").read_text())
        for field in ("spec", "updates", "receipts", "result", "result_ref"):
            if reopened[field] != stream[field]:
                raise ValueError("Reopened native store changed accepted evidence")
        foreign = "sha256:" + "0" * 64
        run(1, "qc", "store", "verify", "quality.sqlite3", "cycle0", "--expected-head-ref", foreign)
        run(1, "qc", "detector", "verify", "detector.json", "--expected-protocol-ref", foreign)
        run(1, "qc", "outcome", "verify", "outcome.json", "--expected-protocol-ref", foreign)
        run(0, "manufacture", "demo", "--quality-control", "--output", "manufacturing.json")
        run(0, "manufacture", "verify", "manufacturing.json")
        for path in outside.glob("*.json"):
            shutil.copy2(path, output / path.name)
    receipt = {"python": sys.version, "platform": sys.platform,
        "wheel_sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
        "source": source, "installed": installed, "verified_package_files": count,
        "native_command_journeys": len(commands), "native_commands": commands,
        "legacy_snapshot_adapter": "excluded; not distributed or qualified",
        "physical_qualification": "not_established", "live_acquisition": "not_performed",
        "source_authentication": "not_established", "shipment_release": "not_authorized"}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items() if k != "native_commands"}, indent=2))


if __name__ == "__main__":
    main()
