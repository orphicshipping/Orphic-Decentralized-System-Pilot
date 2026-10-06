"""Qualify the Link wheel outside its source directory with exact byte receipts.

Requires pinned host distributions unless --allow-host-dependencies explicitly
records a non-pinned local qualification. No package downloads or source import
fallback are performed here. CI provisions the declared pins before this gate.
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
import xml.etree.ElementTree as ET
import zipfile

PINS = {"numpy": "2.4.3", "websockets": "16.0", "pytest": "9.0.2", "cryptography": "46.0.0"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--require-pins", action="store_true")
    mode.add_argument("--allow-host-dependencies", action="store_true")
    parser.add_argument("--with-scalar-study", action="store_true", help="Also require the Linux serial/scalar/MCP increment")
    parser.add_argument("--with-delivery", action="store_true", help="Require signed local custody, occurrence hardening and operator preview")
    parser.add_argument("--with-installation", action="store_true", help="Also qualify the retained paired installation workflow")
    parser.add_argument("--with-engineering", action="store_true", help="Also require the existing-controller engineering-intent recipes")
    args = parser.parse_args()
    if args.with_engineering and not args.with_installation:
        parser.error("Engineering recipes require --with-installation and its inherited gates")
    if args.with_installation and not args.with_delivery:
        parser.error("Installation qualification requires --with-delivery and its inherited gates")
    if args.with_delivery and not args.with_scalar_study:
        parser.error("Delivery qualification includes the scalar investigation; select --with-scalar-study")
    if args.with_scalar_study and sys.platform != "linux":
        parser.error("The scalar serial qualification is a Linux-only gate")
    source = Path(__file__).resolve().parents[1]
    wheel = args.wheel.resolve()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    versions = {name: importlib.metadata.version(name) for name in PINS}
    pin_match = versions == PINS
    steps = []
    summary = {"schema": "ciw.link-install-check.v1", "started_at": started,
               "status": "RUNNING", "versions": versions, "required_versions": PINS,
               "pinned_dependencies_match": pin_match, "python": sys.version,
               "steps": steps, "full_repository_regression": False, "physical_qualification": False}

    def retain():
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    try:
        retain()
        if not pin_match and not args.allow_host_dependencies:
            raise ValueError("Host dependencies differ from unchanged repository pins; no pinned pass")
        with zipfile.ZipFile(wheel) as archive:
            expected = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                        if name.startswith("ciw/") and not name.endswith("/")}
        actual_paths = {"ciw/" + str(p.relative_to(source / "src/ciw")).replace(os.sep, "/")
                        for p in (source / "src/ciw").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        if not expected or set(expected) != actual_paths:
            raise ValueError("Wheel/source package file inventory differs")
        for name, digest in expected.items():
            if hashlib.sha256((source / "src" / name).read_bytes()).hexdigest() != digest:
                raise ValueError("Wheel differs from package source: " + name)
        (root / "expected-package.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(root / "env")
        python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "NET_LINK_NATIVE_CODEC"):
            env.pop(key, None)
        env.update(PYTHONNOUSERSITE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")

        def execute(name, arguments, expected_exit=0):
            before = datetime.now(timezone.utc).isoformat()
            process = subprocess.run([str(python), *map(str, arguments)], cwd=root, env=env,
                                     capture_output=True, text=True, timeout=240)
            (root / (name + ".log")).write_text(process.stdout + process.stderr, encoding="utf-8")
            steps.append({"name": name, "started_at": before, "finished_at": datetime.now(timezone.utc).isoformat(),
                          "exit_code": process.returncode, "expected_exit": expected_exit})
            retain()
            if process.returncode != expected_exit:
                raise RuntimeError(f"{name} failed; inspect retained log")
            return process.stdout

        site = Path(execute("site", ["-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"]).strip())
        dependencies = ["numpy", "websockets", "pytest", "packaging", "pluggy", "iniconfig", "pygments", "cryptography", "cffi", "pycparser"]
        if os.name == "nt":
            dependencies.append("colorama")
        records = []
        for name in dependencies:
            distribution = importlib.metadata.distribution(name)
            hashes = {}
            for relative in distribution.files or []:
                if ".." in relative.parts or relative.suffix == ".pyc":
                    continue
                original = distribution.locate_file(relative)
                if not original.is_file():
                    raise ValueError("Missing dependency file: " + str(original))
                target = site / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
            if not hashes:
                raise ValueError("No dependency files copied")
            records.append({"name": name, "version": distribution.version,
                            "host_distribution": str(distribution.locate_file("")), "files": hashes})
        (root / "dependency-files.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        execute("install", ["-I", "-m", "pip", "install", "--no-deps", "--no-index", wheel])
        probe = '''import hashlib, json, pathlib, sys
import ciw, ciw.link_workflow, ciw.link_security, ciw.connectivity_workflow
root = pathlib.Path(ciw.__file__).resolve().parent
assert root.is_relative_to(pathlib.Path(sys.prefix).resolve()), str(root)
expected = json.loads(pathlib.Path("expected-package.json").read_text())
for name,digest in expected.items():
    assert hashlib.sha256((root.parent/name).read_bytes()).hexdigest()==digest,name
for name,module in list(sys.modules.items()):
    if name=="ciw" or name.startswith("ciw."):
        location=getattr(module,"__file__",None)
        if location: assert pathlib.Path(location).resolve().is_relative_to(root),name
print(json.dumps({"package_files":len(expected),"package_root":str(root),"all_bytes_match":True}))
'''
        summary["package"] = json.loads(execute("package-provenance", ["-I", "-c", probe]))
        (root / "tests").mkdir()
        test_names = ["test_link.py", "test_connectivity.py"] + (["test_link_study.py"] if args.with_scalar_study else [])
        if args.with_delivery:
            test_names.extend(["test_link_delivery.py", "test_link_inspection.py", "test_link_ingress_recovery.py", "test_link_scientific_guards.py", "test_link_snapshot_integrity.py", "test_link_unification.py"])
        if args.with_installation:
            test_names.extend(["test_mount_characterization.py", "test_link_installation.py"])
        if args.with_engineering:
            test_names.extend(["test_engineering_thread.py", "test_engineering_continuity.py", "test_link_offline_environment.py"])
            (root / "scripts").mkdir()
            shutil.copyfile(source / "scripts/provision_link_environment.py", root / "scripts/provision_link_environment.py")
        for name in test_names:
            shutil.copyfile(source / "tests" / name, root / "tests" / name)
        shutil.copytree(source / "examples/device-link/native", root / "examples/device-link/native")
        (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        execute("installed-tests", ["-I", "-m", "pytest", "-q", "-c", "pytest.ini", "-o", "pythonpath=",
                                    *["tests/" + name for name in test_names], "--junitxml=installed-tests.xml"])
        cases = ET.parse(root / "installed-tests.xml").findall(".//testcase")
        skipped = sum(x.find("skipped") is not None for x in cases)
        if not cases or skipped:
            raise ValueError(f"Installed native/security suite has {skipped} skips; no complete gate")
        summary["tests"] = {"passed": len(cases), "skipped": skipped}
        for persona in ("GaugeLink", "MachineLink", "ConditionLink", "FieldLink", "ThermalLink", "DAQLink", "CalLink"):
            execute("demo-" + persona, ["-I", "-m", "ciw.net", "link", "demo", "--profile", persona, "--output-dir", persona])
        if args.with_scalar_study:
            execute("scalar-example", ["-I", "-m", "ciw.net", "link", "study", "example", "--output", "scalar-request.json"])
            execute("scalar-run", ["-I", "-m", "ciw.net", "link", "study", "run", "scalar-request.json", "--output-dir", "scalar-study"])
            for action in ("inspect", "replay", "export", "report"):
                suffix = [] if action == "inspect" else ["--output", "scalar-panel.html"] if action == "report" else ["--output-dir", "scalar-" + action]
                execute("scalar-" + action, ["-I", "-m", "ciw.net", "link", "study", action, "scalar-study", *suffix])
        if args.with_delivery:
            execute("signed-delivery-demo", ["-I", "-m", "ciw.net", "link", "delivery", "demo", "--output-dir", "delivery-demo"])
            delivery = json.loads((root / "delivery-demo/summary.json").read_text())
            execute("signed-delivery-inspect", ["-I", "-m", "ciw.net", "link", "delivery", "inspect",
                "delivery-demo/signed-receipt.json", "--receipt-ref", delivery["receipt_ref"],
                "--node-public-key", "delivery-demo/node-public-key.raw"])
            summary["delivery"] = {"new_acknowledgements": delivery["delivery"]["new_acknowledgements"],
                "historical_acknowledgements": delivery["retry"]["existing_acknowledgements"],
                "scope": delivery["scope"], "inspection_qualification": delivery["study"]["results"]["link.mean-model.v1"]["data"]["inspection_qualification"]}
        if args.with_delivery:
            operator = json.loads(execute("digital-inspection-demo", ["-I", "-m", "ciw.net", "link", "inspection", "demo",
                "--output-dir", "digital-inspection", "--advanced"]))
            execute("digital-inspection-status", ["-I", "-m", "ciw.net", "link", "inspection", "status",
                "digital-inspection", "--request-ref", operator["reference"]])
            assert operator["qualification"] == "HELD" and operator["calculation"] == "COMPLETE"
            assert operator["processing"]["first_qualified_measurement_ns"] is None
            summary["operator_preview"] = {k: operator[k] for k in ("status", "qualification", "checks", "processing", "report")}
        if args.with_delivery:
            execute("unified-link-catalog", ["-I", "-m", "ciw.net", "link", "catalog"])
            for profile in ("pressure", "vibration", "electrical"):
                execute("unified-" + profile, ["-I", "-m", "ciw.net", "link", "demo",
                    "--profile", profile, "--output-dir", "unified-" + profile])
            mathematical = json.loads(execute("link-mathematical-binding", ["-I", "-m", "ciw.net", "link", "semantics",
                "digital-inspection", "--request-ref", operator["reference"], "--output", "link-semantics.json"]))
            assert mathematical["arithmetic_audit"] == "PASS"
            assert mathematical["scope"]["physical_qualification"] == "HELD"
            assert all(row["status"] == "COMPOSABLE" for row in mathematical["compositions"])
            summary["link_semantics"] = {"review_ref": mathematical["record_digest"],
                "stage_count": len(mathematical["original_stages"]), "new_scientific_execution": False}
        if args.with_installation:
            paired = json.loads(execute("installation-demo", ["-I", "-m", "ciw.net", "link", "installation", "demo",
                "--output-dir", "installation-demo", "--compliant"]))
            for action in ("inspect", "replay", "restore"):
                suffix = [] if action == "inspect" else ["--output-dir", "installation-" + action]
                execute("installation-" + action, ["-I", "-m", "ciw.net", "link", "installation", action,
                    "installation-demo/investigation", "--request-ref", paired["request_ref"], *suffix])
            assert paired["scope"]["physical_qualification"] == "HELD"
            assert paired["passing_sampled_frequencies_hz"] == [32., 64.]
            summary["installation"] = paired
        if args.with_engineering:
            summary["engineering"] = {}
            for profile in ("installation", "scalar"):
                folder = "engineering-" + profile
                result = json.loads(execute(folder, ["-I", "-m", "ciw.net", "production", "engineering", "demo",
                    "--profile", profile, "--output-dir", folder], expected_exit=2))
                expected = "REQUIREMENTS_NOT_MET" if profile == "installation" else "EVIDENCE_REQUIRED"
                if result["review"]["status"] != expected or result["review"]["scope"]["physical_qualification"] != "HELD":
                    raise ValueError("Engineering demonstration made an unsupported success claim")
                execute(folder + "-inspect", ["-I", "-m", "ciw.net", "production", "engineering", "inspect",
                    folder + "/execution", "--profile", profile, "--thread-ref", result["review"]["thread_ref"]], expected_exit=2)
                summary["engineering"][profile] = {"status": result["review"]["status"],
                    "thread_ref": result["review"]["thread_ref"], "execution_count": len(result["review"]["execution_ids"]),
                    "source_fixture_execution_is_separate": result["source_fixture_execution_is_separate"],
                    "scope": result["review"]["scope"]}
        if args.with_engineering:
            original = summary["engineering"]["scalar"]
            recovered = json.loads(execute("engineering-recovery", ["-I", "-m", "ciw.net", "production", "engineering", "restore",
                "engineering-scalar/execution", "--profile", "scalar", "--thread-ref", original["thread_ref"],
                "--output-dir", "engineering-recovered"]))
            if recovered["new_scientific_executions"] != 0 or recovered["active_authority_restored"] is not False:
                raise ValueError("Recovery restored execution authority or created scientific occurrences")
            intent = json.loads((root / "engineering-recovered/intent.json").read_bytes())
            intent["revision"] += 1
            (root / "next-intent.json").write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
            revised = json.loads(execute("engineering-revision", ["-I", "-m", "ciw.net", "production", "engineering", "revise",
                "engineering-recovered", "--profile", "scalar", "--thread-ref", original["thread_ref"],
                "--intent", "next-intent.json", "--request", "engineering-recovered/request.json",
                "--reason", "Synthetic recovery and continuation qualification", "--output-dir", "engineering-next"]))
            command = ["-I", "-m", "ciw.net", "production", "engineering", "run", "engineering-next",
                       "--profile", "scalar", "--compiled-ref", revised["compilation"]["compiled_ref"],
                       "--allow-compute", "--output-dir", "engineering-next-run"]
            execute("engineering-missing-predecessor", command, expected_exit=1)
            if (root / "engineering-next-run").exists():
                raise ValueError("Missing predecessor created an execution directory")
            continued = json.loads(execute("engineering-continued", command + ["--predecessor", "engineering-recovered"], expected_exit=2))
            if (continued["status"] != "EVIDENCE_REQUIRED" or continued["scope"]["physical_qualification"] != "HELD"
                    or set(continued["execution_ids"]) & set(recovered["execution_ids"])):
                raise ValueError("Continuation reused occurrences or cleared physical evidence holds")
            summary["engineering_continuity"] = {"restored_files": recovered["files_restored"],
                "prior_thread_ref": original["thread_ref"], "new_thread_ref": continued["thread_ref"],
                "prior_execution_ids": recovered["execution_ids"], "new_execution_ids": continued["execution_ids"],
                "status": continued["status"], "scope": continued["scope"]}
        execute("catalog", ["-I", "-m", "ciw.net", "catalog", "--audit", "--json"])
        execute("package-provenance-after", ["-I", "-c", probe])
        summary.update(status="PASS", finished_at=datetime.now(timezone.utc).isoformat(),
                       wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                       scope="installed_link_scalar_serial_mcp_subset_not_full_repository" if args.with_scalar_study else "installed_device_link_native_codec_and_connectivity_not_full_repository")
        retain()
    except BaseException as exc:
        summary.update(status="BLOCKED", finished_at=datetime.now(timezone.utc).isoformat(), reason=str(exc))
        retain()
        raise
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
