"""Qualify the actual installed manufacturing/QC wheel outside its checkout.

Synthetic record-level qualification only; this grants no physical authority.
The full repository regression and optional provider gates remain separate.
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


TESTS = ("test_polymer_manufacturing.py", "test_polymer_toolpoint_qc.py")
MINIMUM_TESTS = 366


def suite(root: Path, report: Path, env: dict[str, str]) -> dict[str, int]:
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *("tests/" + name for name in TESTS),
         "--junitxml=" + str(report)], cwd=root, env=env, check=True,
    )
    totals = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for node in ET.parse(report).getroot().iter("testsuite"):
        for key in totals:
            totals[key] += int(node.attrib.get(key, "0"))
    if totals["tests"] < MINIMUM_TESTS or any(totals[k] for k in ("failures", "errors", "skipped")):
        raise RuntimeError(f"Incomplete manufacturing/QC qualification: {totals}")
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheels = list(args.wheel_dir.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"Expected one candidate wheel, found {len(wheels)}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # Source subprocesses must resolve the candidate source, not another editable install.
    source_env = dict(env, PYTHONPATH=str(root / "src"))
    source = suite(root, output / "source.xml", source_env)
    with tempfile.TemporaryDirectory(prefix="net-manufacturing-installed-") as temporary:
        outside = Path(temporary).resolve()
        if outside.is_relative_to(root):
            raise ValueError("Installed qualification must run outside the checkout")
        (outside / "tests").mkdir()
        for name in TESTS:
            shutil.copy2(root / "tests" / name, outside / "tests" / name)
        # Retain the historical V1 replay comparison in the installed run as well.
        shutil.copytree(root / "validation/polymer-manufacturing",
                        outside / "validation/polymer-manufacturing")
        code = "from pathlib import Path; import ciw; print(Path(ciw.__file__).resolve().parent)"
        package = Path(subprocess.check_output(
            [sys.executable, "-I", "-c", code], cwd=outside, env=env, text=True,
        ).strip())
        if package.is_relative_to(root) or "site-packages" not in package.parts:
            raise ValueError(f"Not a noneditable installed candidate: {package}")
        count = 0
        with zipfile.ZipFile(wheels[0]) as archive:
            for name in archive.namelist():
                if not name.startswith("ciw/") or name.endswith("/"):
                    continue
                data = archive.read(name)
                if (package.parent / name).read_bytes() != data:
                    raise ValueError(f"Installed bytes differ from wheel: {name}")
                if (root / "src" / name).read_bytes() != data:
                    raise ValueError(f"Wheel bytes differ from candidate source: {name}")
                count += 1
        installed = suite(outside, output / "installed.xml", env)
        commands = [
            ["manufacture", "example", "--output", "request.json"],
            ["manufacture", "run", "request.json", "--output", "report.json"],
            ["manufacture", "verify", "report.json", "--output", "verification.json"],
            ["manufacture", "demo", "--quality-control", "--output", "qc-manufacturing.json"],
            ["manufacture", "verify", "qc-manufacturing.json", "--output", "qc-manufacturing-verification.json"],
            ["qc", "example", "--output", "qc-request.json"],
            ["qc", "assess", "qc-request.json", "--output", "qc-report.json"],
            ["qc", "verify", "qc-report.json", "--output", "qc-verification.json"],
        ]
        executable = Path(sys.executable).parent / ("net.exe" if os.name == "nt" else "net")
        if not executable.is_file():
            raise ValueError(f"Missing installed native command: {executable}")
        for command in commands:
            subprocess.run([str(executable), "polymer", *command], cwd=outside, env=env, check=True)
        for path in outside.glob("*.json"):
            shutil.copy2(path, output / path.name)
    receipt = {
        "python": sys.version, "platform": sys.platform,
        "wheel_sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
        "source": source, "installed": installed,
        "verified_package_files": count, "native_command_journeys": len(commands),
        "physical_qualification": "not_performed", "private_provider_qualification": "not_performed",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
