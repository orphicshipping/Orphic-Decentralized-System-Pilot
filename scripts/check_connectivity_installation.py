"""Qualify a built wheel outside its source tree; no cores, subscribers or RF.

Host dependencies are explicit. --require-pins refuses mismatched dependencies.
The local HTTP exporter in the copied tests is synthetic, not a native core.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-pins", action="store_true")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1]
    started = datetime.now(timezone.utc).isoformat()
    with zipfile.ZipFile(wheel) as archive:
        expected = {name: hashlib.sha256(archive.read(name)).hexdigest()
                    for name in archive.namelist() if name.startswith("ciw/") and not name.endswith("/")}
    if not expected or any(not (source / "src" / name).is_file() for name in expected):
        raise ValueError("Require the complete package source")
    for name, digest in expected.items():
        if hashlib.sha256((source / "src" / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Wheel differs from package source: " + name)
    (root / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(root / "env")
    python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    steps = []

    def execute(name, arguments, expected_exit=0):
        begin = datetime.now(timezone.utc).isoformat()
        process = subprocess.run([str(python), *arguments], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=180)
        (root / (name + ".log")).write_text(process.stdout + process.stderr, encoding="utf-8")
        steps.append({"name": name, "started_at": begin, "exit_code": process.returncode,
                      "expected_exit": expected_exit})
        if process.returncode != expected_exit:
            raise RuntimeError(f"{name} failed with exit {process.returncode}; inspect its retained log")
        return process.stdout

    site_dir = Path(execute("site-directory", ["-c", "import sysconfig; print(sysconfig.get_path('purelib'))"]).strip())
    dependencies = ["numpy", "websockets", "pytest", "packaging", "pluggy", "iniconfig", "pygments"]
    if os.name == "nt":
        dependencies.append("colorama")
    dependency_records = []
    for name in dependencies:
        distribution = importlib.metadata.distribution(name)
        copied = {}
        for relative in distribution.files or []:
            if ".." in relative.parts or relative.suffix == ".pyc":
                continue
            original = distribution.locate_file(relative)
            if not original.is_file():
                raise ValueError("Missing declared dependency file: " + str(original))
            target = site_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            copied[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
        if not copied:
            raise ValueError("Dependency distribution has no copyable files: " + name)
        dependency_records.append({"name": name, "version": distribution.version,
                                   "source": str(distribution.locate_file("")), "files": copied})
    (root / "dependency-files.json").write_text(json.dumps(dependency_records, indent=2), encoding="utf-8")
    execute("install", ["-m", "pip", "install", "--no-deps", "--no-index", str(wheel)])
    probe = '''import hashlib, importlib.metadata as m, json, pathlib, sys
import ciw, ciw.connectivity_workflow
root = pathlib.Path(ciw.__file__).resolve().parent
assert root.is_relative_to(pathlib.Path(sys.prefix).resolve()), str(root)
expected = json.loads(pathlib.Path("expected.json").read_text())
for name, digest in expected.items():
    assert hashlib.sha256((root.parent / name).read_bytes()).hexdigest() == digest, name
for name, module in list(sys.modules.items()):
    if name == "ciw" or name.startswith("ciw."):
        location = getattr(module, "__file__", None)
        if location: assert pathlib.Path(location).resolve().is_relative_to(root), name
versions = {name: m.version(name) for name in ("numpy", "websockets", "pytest")}
print(json.dumps({"package_root": str(root), "package_files": len(expected), "versions": versions,
 "pinned_dependencies_match": versions == {"numpy":"2.4.3", "websockets":"16.0", "pytest":"9.0.2"}}))
'''
    runtime = json.loads(execute("installed-byte-provenance", ["-c", probe]))
    if args.require_pins and not runtime["pinned_dependencies_match"]:
        raise ValueError("Installed dependency versions differ from the unchanged repository pins")
    shutil.copyfile(source / "tests/test_connectivity.py", root / "test_connectivity.py")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    execute("installed-tests", ["-m", "pytest", "-q", "-c", "pytest.ini", "-o", "pythonpath=",
                                "test_connectivity.py", "--junitxml=installed-tests.xml"])
    for provider in ("open5gs", "free5gc"):
        for verb, arguments in (
            ("example", ["--provider", provider, "--output", f"{provider}.json"]),
            ("run", [f"{provider}.json", "--output-dir", provider]),
            ("inspect", [provider]),
            ("replay", [provider, "--output-dir", provider + "-replay"]),
            ("report", [provider, "--output", provider + ".html"]),
            ("context", ["--provider", provider, "--output", provider + "-context.json"]),
        ):
            execute(provider + "-" + verb, ["-m", "ciw.net", "connectivity", verb, *arguments])
    # Existing catalogue review is a regression check, not physical qualification.
    execute("catalog", ["-m", "ciw.net", "catalog", "--audit", "--json"])
    summary = {"schema": "ciw.connectivity-install-check.v1", "started_at": started,
               "finished_at": datetime.now(timezone.utc).isoformat(), "status": "PASS",
               "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
               "runtime": runtime, "steps": steps,
               "native_core_execution": False, "radio_transmission": False,
               "scope": "installed_connectivity_and_catalog_not_full_repository"}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
