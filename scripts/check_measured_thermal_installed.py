"""Qualify measured thermal fixtures against the exact installed candidate wheel.

Test inputs are copied into a short temporary directory. No source-tree CIW
imports, hardware acquisition, calibration authentication or admission is allowed.
"""
from __future__ import annotations

import argparse
import base64
import csv
from hashlib import sha256
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

SELECTED = ("test_measured_thermal.py", "test_measured_thermal_review.py",
            "test_measured_thermal_exports.py")
HELPERS = ("test_scientific_observations.py", "test_thermal_workflow.py")
EXPECTED_CASES = 106

PYTEST_CHILD = r"""
import json
from pathlib import Path
import sys
import pytest

temporary, output, source, installed = map(Path, sys.argv[1:5])
code = pytest.main([
    "-c", str(temporary / "pytest.ini"), "--rootdir", str(temporary),
    "--import-mode=prepend", "-o", "pythonpath=", "-o", "addopts=",
    "--basetemp", str(temporary / "tmp"),
    "--junitxml", str(output / "measured-thermal-installed.xml"), "-q",
    *[str(temporary / name) for name in sys.argv[5:]],
])
modules = {name: str(Path(module.__file__).resolve())
           for name, module in list(sys.modules.items())
           if (name == "ciw" or name.startswith("ciw.")) and getattr(module, "__file__", None)}
outside = [name for name, path in modules.items() if not Path(path).is_relative_to(installed)]
source_paths = [path for path in sys.path if path and Path(path).resolve().is_relative_to(source)]
valid = bool(modules) and "ciw.measured_thermal" in modules and not outside and not source_paths
value = {"status": "PASS" if valid else "FAIL", "modules": modules,
         "outside_installed_package": outside, "source_sys_path": source_paths,
         "sys_path": sys.path, "isolated": sys.flags.isolated,
         "user_site_disabled": sys.flags.no_user_site, "pytest_exit_code": int(code)}
(output / "import-isolation.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
raise SystemExit(int(code) if valid else 1)
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": sha256(raw).hexdigest()}


def installed_binding(source, wheel):
    require(sys.flags.isolated == 1, "Run this qualification with python -I")
    distribution = importlib.metadata.distribution("computational-instrumentation-workbench")
    installed = Path(distribution.locate_file("ciw")).resolve(strict=True)
    require(not installed.is_relative_to(source), "Candidate must be installed outside the checkout")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    wheel_identity = file_ref(wheel)
    require(direct.get("url") == wheel.as_uri(), "Installed distribution must come from the selected wheel")
    require(direct.get("archive_info", {}).get("hashes", {}).get("sha256") == wheel_identity["sha256"],
            "Installed distribution does not bind the selected wheel hash")
    rows = []
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) <= 1024 and len(names) == len(set(names)), "Ambiguous or oversized wheel member list")
        require(all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                    and "\\" not in name for name in names), "Unsafe wheel member path")
        require(sum(info.file_size for info in archive.infolist()) <= 64 * 1024 * 1024,
                "Wheel exceeds the uncompressed qualification budget")
        records = [name for name in names if name.endswith(".dist-info/RECORD")]
        require(len(records) == 1, "Require one wheel RECORD")
        record_rows = list(csv.reader(io.StringIO(archive.read(records[0]).decode("utf-8"))))
        require(len(record_rows) == len(names) and {row[0] for row in record_rows} == set(names),
                "Wheel RECORD does not cover every member exactly once")
        for name, encoded, size in record_rows:
            raw = archive.read(name)
            if name == records[0]:
                require(encoded == size == "", "Invalid RECORD self-entry")
            else:
                expected = "sha256=" + base64.urlsafe_b64encode(sha256(raw).digest()).rstrip(b"=").decode("ascii")
                require(encoded == expected and size == str(len(raw)), "Wheel RECORD mismatch: " + name)
            if name.startswith("ciw/"):
                require(raw == (source / "src" / name).read_bytes(), "Wheel differs from checkout: " + name)
                require(raw == Path(distribution.locate_file(name)).read_bytes(), "Installed bytes differ: " + name)
                rows.append({"path": name, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
        require(rows, "Wheel contains no CIW package files")
    return {"wheel": wheel_identity, "wheel_members": len(names),
            "record_hashed_members": len(record_rows) - 1,
            "package_files": rows, "installed_package": str(installed), "direct_url": direct,
            "python": sys.version, "interpreter": sys.executable,
            "versions": {name: importlib.metadata.version(name)
                         for name in ("computational-instrumentation-workbench", "numpy", "websockets", "pytest")}}


def qualify(source, wheel, output):
    binding = installed_binding(source, wheel)
    (output / "installed-binding.json").write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    test_inputs = output / "test-inputs"
    test_inputs.mkdir()
    retained_tests = []
    for name in (*SELECTED, *HELPERS):
        path = source / "tests" / name
        require(path.is_file() and not path.is_symlink(), "Missing or unsafe test input: " + name)
        shutil.copyfile(path, test_inputs / name)
        retained_tests.append({**file_ref(test_inputs / name), "selected": name in SELECTED})
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    temporary_root = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve(strict=True)
    require(temporary_root.is_dir(), "Temporary root must be an existing directory")
    with tempfile.TemporaryDirectory(prefix="mti-", dir=temporary_root) as temporary:
        directory = Path(temporary)
        require(directory.resolve().is_relative_to(temporary_root), "Temporary path escaped its declared root")
        for name in (*SELECTED, *HELPERS):
            shutil.copyfile(test_inputs / name, directory / name)
        (directory / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        command = [sys.executable, "-I", "-B", "-c", PYTEST_CHILD, str(directory), str(output),
                   str(source), binding["installed_package"], *SELECTED]
        (output / "command.json").write_text(json.dumps({
            "argv": command, "cwd": str(directory), "source_imports": "not_permitted",
            "environment": {"PYTHONPATH": "removed", "PYTHONHOME": "removed",
                            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}}, indent=2) + "\n", encoding="utf-8")
        with (output / "pytest.log").open("wb") as log:
            completed = subprocess.run(command, cwd=directory, env=environment,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=300)
    cases = list(ET.parse(output / "measured-thermal-installed.xml").getroot().iter("testcase"))
    counts = {"tests": len(cases), "failures": sum(c.find("failure") is not None for c in cases),
              "errors": sum(c.find("error") is not None for c in cases),
              "skipped": sum(c.find("skipped") is not None for c in cases)}
    identities = {(case.get("classname"), case.get("name")) for case in cases}
    isolation = json.loads((output / "import-isolation.json").read_text(encoding="utf-8"))
    require(completed.returncode == 0 and isolation["status"] == "PASS", "Installed test or import-isolation failure")
    require(counts == {"tests": EXPECTED_CASES, "failures": 0, "errors": 0, "skipped": 0}
            and len(identities) == EXPECTED_CASES, "Measured thermal qualification is incomplete")
    return {"schema": "ciw.measured-thermal-installed-qualification.v1", "status": "PASS",
            "candidate_revision": os.environ.get("GITHUB_SHA"),
            "scope": "constructed_fixture_contracts_and_retained_lifecycle",
            "operation_id": "ciw.measured-thermal.v1", "wheel": binding["wheel"],
            "counts": counts, "selected_tests": list(SELECTED), "import_only_helpers": list(HELPERS),
            "test_inputs": retained_tests, "installed_package": binding["installed_package"],
            "package_file_count": len(binding["package_files"]),
            "evidence": [file_ref(output / name) for name in
                         ("installed-binding.json", "command.json", "pytest.log",
                          "measured-thermal-installed.xml", "import-isolation.json")],
            "authority": {"physical_validation": "not_established", "calibration_authenticity": "not_established",
                          "external_provider_qualification": "not_performed", "state_admission": "not_performed",
                          "hardware_actuation": "not_performed"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    try:
        wheels = list(args.wheel_dir.resolve(strict=True).glob("*.whl"))
        require(len(wheels) == 1 and wheels[0].is_file() and not wheels[0].is_symlink(),
                "Require exactly one regular candidate wheel")
        require(wheels[0].stat().st_size <= 32 * 1024 * 1024, "Wheel exceeds qualification byte budget")
        report = qualify(source, wheels[0].resolve(), output)
    except Exception as exc:
        report = {"schema": "ciw.measured-thermal-installed-qualification.v1", "status": "FAIL",
                  "error_type": type(exc).__name__, "message": str(exc),
                  "physical_validation": "not_established"}
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "counts": report["counts"],
                      "wheel_sha256": report["wheel"]["sha256"], "report": str(output / "report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
