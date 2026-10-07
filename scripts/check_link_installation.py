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
import io
import runpy
import json
from email.parser import Parser
import tomllib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import xml.etree.ElementTree as ET
import zipfile

MAX_DEPENDENCIES = 64


def dependency_selection(project_raw, *, lookup=None, root_metadata=None):
    """Inspect the installed closure of the existing dev+legibility profile.

    No resolver/download or application import. Direct pin differences are
    reported for the explicit development mode; broken transitive constraints
    always refuse. Metadata is a local declaration, not publisher attestation.
    """
    from packaging.markers import default_environment
    from packaging.requirements import Requirement, InvalidRequirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    if type(project_raw) is not bytes or not 0 < len(project_raw) <= 262144:
        raise ValueError("Project metadata exceeds qualification bounds")
    project = tomllib.loads(project_raw.decode("utf-8"))["project"]
    if project.get("dynamic"):
        raise ValueError("Qualification requires static project requirements")
    environment = default_environment()
    if not SpecifierSet(project["requires-python"]).contains(environment["python_full_version"]):
        raise ValueError("Project Python requirement is not satisfied")
    extras = ("dev", "legibility")
    requirements = list(project.get("dependencies", []))
    for extra in extras:
        requirements.extend(project["optional-dependencies"][extra])

    def active(raws, selected):
        if not isinstance(raws, list) or len(raws) > 256:
            raise ValueError("Dependency metadata exceeds qualification bounds")
        for raw in raws:
            try:
                req = Requirement(raw)
            except InvalidRequirement as exc:
                raise ValueError("Malformed qualification dependency") from None
            if req.marker is not None and not any(req.marker.evaluate({**environment, "extra": x})
                                                  for x in ("", *sorted(selected))):
                continue
            if req.url is not None:
                raise ValueError("Active URL dependency is not an offline qualification input")
            yield req

    project_requirements = list(active(requirements, extras))
    root_ref = None
    if root_metadata is not None:
        if type(root_metadata) is not str or len(root_metadata.encode('utf-8')) > 4 * 1024 * 1024:
            raise ValueError("Root wheel metadata exceeds qualification bound")
        root = Parser().parsestr(root_metadata)
        if (root.get_all("Name") != [project["name"]] or root.get_all("Version") != [project["version"]]
                or root.get_all("Requires-Python") != [project["requires-python"]]
                or not set(extras) <= set(root.get_all("Provides-Extra", []))):
            raise ValueError("Root wheel identity or extras differ from project")
        def key(req):
            return (canonicalize_name(req.name), str(req.specifier), tuple(sorted(req.extras)))
        if sorted(map(key, active(root.get_all("Requires-Dist", []), extras))) != sorted(map(key, project_requirements)):
            raise ValueError("Root wheel dependencies differ from selected project")
        root_ref = "sha256:" + hashlib.sha256(root_metadata.encode('utf-8')).hexdigest()
    pins = {}; needed = {}; constraints = []
    for req in project_requirements:
        name = canonicalize_name(req.name); spec = list(req.specifier)
        if len(spec) != 1 or spec[0].operator != "==" or "*" in spec[0].version:
            raise ValueError("Direct qualification requirements must remain exact pins")
        version = str(Version(spec[0].version))
        if name in pins and pins[name] != version:
            raise ValueError("Conflicting direct pins: " + name)
        pins[name] = version
        needed.setdefault(name, set()).update(canonicalize_name(x) for x in req.extras)
    lookup = importlib.metadata.distribution if lookup is None else lookup
    distributions = {}; snapshots = {}; processed = {}
    while True:
        if len(needed) > MAX_DEPENDENCIES:
            raise ValueError("Dependency closure exceeds qualification bound")
        pending = [name for name in sorted(needed) if processed.get(name) != frozenset(needed[name])]
        if not pending:
            break
        for name in pending:
            if name not in distributions:
                try:
                    dist = lookup(name)
                except importlib.metadata.PackageNotFoundError:
                    raise ValueError("Missing qualification dependency: " + name) from None
                text = dist.read_text("METADATA")
                if text is None or len(text.encode("utf-8")) > 4 * 1024 * 1024:
                    raise ValueError("Missing or oversized distribution metadata: " + name)
                meta = Parser().parsestr(text)
                if (len(meta.get_all("Name", [])) != 1 or len(meta.get_all("Version", [])) != 1
                        or canonicalize_name(meta["Name"]) != name or len(meta.get_all("Requires-Python", [])) > 1):
                    raise ValueError("Distribution metadata identity differs: " + name)
                python = meta.get("Requires-Python")
                if python and not SpecifierSet(python).contains(environment["python_full_version"], prereleases=True):
                    raise ValueError("Distribution Python requirement is not satisfied: " + name)
                declared_extras = meta.get_all("Provides-Extra", [])
                if len(declared_extras) > 64:
                    raise ValueError("Dependency extras exceed qualification bound")
                snapshots[name] = {"version": str(Version(meta["Version"])),
                    "requires_python": python, "requires_dist": meta.get_all("Requires-Dist", []),
                    "declared_extras": sorted(canonicalize_name(x) for x in declared_extras),
                    "metadata_ref": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()}
                distributions[name] = dist
            item = snapshots[name]; selected = needed[name]
            if not selected <= set(item["declared_extras"]):
                raise ValueError("Undeclared dependency extra: " + name)
            processed[name] = frozenset(selected)
            for req in active(item["requires_dist"], selected):
                dep = canonicalize_name(req.name)
                needed.setdefault(dep, set()).update(canonicalize_name(x) for x in req.extras)
                constraints.append((name, dep, req.specifier))
                if len(needed) > MAX_DEPENDENCIES:
                    raise ValueError("Dependency closure exceeds qualification bound")
    for parent, dep, spec in constraints:
        if not spec.contains(snapshots[dep]["version"], prereleases=True):
            raise ValueError("Incompatible dependency: " + parent + " -> " + dep)
    versions = {name: snapshots[name]["version"] for name in sorted(snapshots)}
    deviations = [{"name": name, "required": pins[name], "installed": versions[name]}
                  for name in sorted(pins) if pins[name] != versions[name]]
    return {"project_ref": "sha256:" + hashlib.sha256(project_raw).hexdigest(),
        "root_metadata_matches_project": root_metadata is not None, "root_metadata_ref": root_ref,
        "project_name": canonicalize_name(project["name"]),
        "selected_extras": list(extras), "environment": environment,
        "required_versions": dict(sorted(pins.items())), "versions": versions,
        "pin_deviations": deviations, "pinned_dependencies_match": not deviations,
        "distributions": {name: {**snapshots[name], "extras": sorted(needed[name])} for name in sorted(snapshots)},
        "publisher_authenticated": False, "vulnerabilities_checked": False}, distributions


def review_qualification_wheel(path, *, expected_ref=None):
    """Reuse the release wheel validator and retain exactly one bounded read.

    The helper is installed release-engineering code, never supplied by a wheel.
    A hash identifies selected bytes; it does not authenticate their publisher.
    """
    tools = runpy.run_path(str(Path(__file__).with_name("provision_link_environment.py")))
    path = Path(path).absolute()
    raw = tools["_read"](path, tools["MAX_WHEEL"])
    if expected_ref is not None:
        tools["_anchor"](raw, expected_ref)
    return raw, tools["inspect_wheel"](path.name, raw)


def _check_wheel_snapshot(path, expected_sha256):
    """Check retained input integrity, without reopening the selected source."""
    tools = runpy.run_path(str(Path(__file__).with_name("provision_link_environment.py")))
    if tools["digest"](tools["_read"](path, tools["MAX_WHEEL"])) != expected_sha256:
        raise ValueError("Qualification wheel snapshot changed")


def verify_dependency_files(site, records):
    """Recheck copied library bytes, not just package version labels.

    The host selected these records in this run; they are not an authorization
    file or a public import format. This is not a concurrent-host sandbox.
    """
    site = Path(site).absolute()
    checked = 0
    for record in records:
        for name, digest in record["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe dependency record path")
            path = site / relative
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError("Dependency path is a symlink")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Copied dependency bytes changed: " + record["name"])
            checked += 1
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--wheel-ref", help="Optional independently retained sha256: reference of the exact wheel")
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
    wheel = args.wheel.absolute()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    steps = []
    summary = {"schema": "ciw.link-install-check.v1", "started_at": started,
               "status": "RUNNING", "versions": {}, "required_versions": {},
               "pinned_dependencies_match": None, "python": sys.version,
               "steps": steps, "full_repository_regression": False, "physical_qualification": False}

    def retain():
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    try:
        retain()
        project_raw = (source / "pyproject.toml").read_bytes()
        wheel_raw, wheel_metadata = review_qualification_wheel(wheel, expected_ref=args.wheel_ref)
        summary["wheel_input"] = {"sha256": wheel_metadata["sha256"][7:],
            "bytes": len(wheel_raw), "snapshot": "inputs/" + wheel.name,
            "external_ref_checked": args.wheel_ref is not None}
        with zipfile.ZipFile(io.BytesIO(wheel_raw)) as archive:
            metadata_paths = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(metadata_paths) != 1 or archive.getinfo(metadata_paths[0]).file_size > 4 * 1024 * 1024:
                raise ValueError("Require one bounded root wheel metadata file")
            root_metadata = archive.read(metadata_paths[0]).decode('utf-8')
        dependencies, distributions = dependency_selection(project_raw, root_metadata=root_metadata)
        pin_match = dependencies["pinned_dependencies_match"]
        summary.update(versions=dependencies["versions"], required_versions=dependencies["required_versions"],
                       pinned_dependencies_match=pin_match, pin_deviations=dependencies["pin_deviations"],
                       dependency_closure=dependencies)
        retain()
        if not pin_match and not args.allow_host_dependencies:
            raise ValueError("Host dependencies differ from unchanged repository pins; no pinned pass")
        with zipfile.ZipFile(io.BytesIO(wheel_raw)) as archive:
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
        # Source replacement after validation must not change the installation.
        # This is the same private snapshot rule as the offline provisioner.
        tools = runpy.run_path(str(Path(__file__).with_name("provision_link_environment.py")))
        (root / "inputs").mkdir(mode=0o700)
        selected_wheel = root / "inputs" / wheel.name
        tools["_write"](selected_wheel, wheel_raw)
        _check_wheel_snapshot(selected_wheel, wheel_metadata["sha256"])
        retain()
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(root / "env")
        python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "NET_LINK_NATIVE_CODEC"):
            env.pop(key, None)
        env.update(PYTHONNOUSERSITE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")

        def execute(name, arguments, expected_exit=0, *, environment=None):
            before = datetime.now(timezone.utc).isoformat()
            process = subprocess.run([str(python), *map(str, arguments)], cwd=root,
                                     env=env if environment is None else environment,
                                     capture_output=True, text=True, timeout=240)
            (root / (name + ".log")).write_text(process.stdout + process.stderr, encoding="utf-8")
            steps.append({"name": name, "started_at": before, "finished_at": datetime.now(timezone.utc).isoformat(),
                          "exit_code": process.returncode, "expected_exit": expected_exit})
            retain()
            if process.returncode not in ((expected_exit,) if type(expected_exit) is int else expected_exit):
                raise RuntimeError(f"{name} failed; inspect retained log")
            return process.stdout

        site = Path(execute("site", ["-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"]).strip())
        records = []
        for name, distribution in sorted(distributions.items()):
            hashes = {}
            for relative in distribution.files or []:
                if relative.is_absolute():
                    raise ValueError("Absolute dependency file path is unsupported")
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
        summary["dependency_files_checked_before"] = verify_dependency_files(site, records)
        _check_wheel_snapshot(selected_wheel, wheel_metadata["sha256"])
        execute("install", ["-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "--no-input", "--no-cache-dir", "install", "--no-deps", "--no-index",
            "--only-binary=:all:", "--no-user", "--prefix", str(root / "env"), selected_wheel],
            environment=tools["_environment"](root))
        _check_wheel_snapshot(selected_wheel, wheel_metadata["sha256"])
        # Reinspect the COPIED metadata with this same trusted qualification
        # script, then ask pip independently about base dependency consistency.
        (root / "scripts").mkdir()
        shutil.copyfile(source / "scripts/check_link_installation.py", root / "scripts/check_link_installation.py")
        shutil.copyfile(source / "scripts/provision_link_environment.py", root / "scripts/provision_link_environment.py")
        (root / "pyproject.toml").write_bytes(project_raw)
        selection_probe = ("import runpy,json,pathlib,tomllib,importlib.metadata as md; "
            "m=runpy.run_path('scripts/check_link_installation.py'); "
            "raw=pathlib.Path('pyproject.toml').read_bytes(); "
            "name=tomllib.loads(raw.decode())['project']['name']; "
            "r,_=m['dependency_selection'](raw,root_metadata=md.distribution(name).read_text('METADATA')); "
            "print(json.dumps(r,sort_keys=True))")
        installed_dependencies = json.loads(execute("installed-dependencies", ["-I", "-c", selection_probe]))
        if installed_dependencies != dependencies:
            raise ValueError("Copied dependency closure differs from the selected host metadata")
        summary["installed_dependency_closure_matches"] = True
        # Development-only direct pin deviations have already been identified;
        # pip's nonzero status remains evidence, never a compatibility PASS.
        execute("pip-check", ["-I", "-m", "pip", "--isolated", "check"],
                expected_exit=0 if pin_match else (0, 1))
        summary["pip_check"] = {"exit_code": steps[-1]["exit_code"],
            "status": "PASS" if steps[-1]["exit_code"] == 0 else "INCOMPATIBLE_DEVELOPMENT_ENVIRONMENT",
            "qualification": "PINNED_CANDIDATE" if pin_match else "DEVELOPMENT_ONLY",
            "extras_checked_separately": True}
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
            test_names.extend(["test_engineering_thread.py", "test_engineering_continuity.py", "test_link_offline_environment.py", "test_link_qualification_dependencies.py", "test_link_qualification_artifacts.py"])
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
        if json.loads(execute("installed-dependencies-after", ["-I", "-c", selection_probe])) != dependencies:
            raise ValueError("Dependency metadata changed during qualification")
        summary["dependency_files_checked_after"] = verify_dependency_files(site, records)
        _check_wheel_snapshot(selected_wheel, wheel_metadata["sha256"])
        summary.update(status="PASS", finished_at=datetime.now(timezone.utc).isoformat(),
                       wheel_sha256=wheel_metadata["sha256"][7:],
                       scope="installed_link_scalar_serial_mcp_subset_not_full_repository" if args.with_scalar_study else "installed_device_link_native_codec_and_connectivity_not_full_repository")
        retain()
    except BaseException as exc:
        summary.update(status="BLOCKED", finished_at=datetime.now(timezone.utc).isoformat(), reason=str(exc))
        retain()
        raise
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
