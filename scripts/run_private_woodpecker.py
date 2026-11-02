"""Offline, manual Woodpecker preparation; no credentials, Docker or deployment.

Uses the existing exact-source builder and bounded qualification command runner.
This local-backend profile assumes reviewed source on a disposable trusted host.
Its receipts are not accepted GitHub release-collector or hosted evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import xml.etree.ElementTree as ET

from check_private_host_qualification import (parse_tap, require, run_command,
                                               source_identity, validate_phase,
                                               PHASES as QUALIFICATION_PHASES)
from build_private_release import contract

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "orphicsystems/Orphic-Decentralized-System-Protocol"
PHASES = ("contracts", "build", "install", "qualify", "node")
RELEASE_CONTRACT_TESTS = ("tests/test_private_release.py", "scripts/tests/test_private_release_build.py",
                          "scripts/tests/test_private_woodpecker.py")
ANNOTATION_CONTRACT_TESTS = ("tests/test_annotations.py", "tests/test_annotation_nise.py",
                             "tests/test_control_annotations.py")
NISE_CONTRACT_TESTS = ("tests/test_nise_contract.py", "tests/test_nise_handoff.py",
                       "tests/test_semantic_capabilities.py")
CONTRACT_TESTS = RELEASE_CONTRACT_TESTS + ANNOTATION_CONTRACT_TESTS + NISE_CONTRACT_TESTS
NATIVE_NISE_TESTS = ("tests/test_nise_profile.py", "tests/test_nise_policy.py")
INSTALLED_NISE_TESTS = ("tests/test_nise_contract.py",) + NATIVE_NISE_TESTS + ("tests/test_control_annotations.py",)
NODE_TESTS = tuple("tools/odrp-private-host-candidate/tools/private-mcp-host/test_" + name + ".mjs"
                   for name in ("gateway", "release", "storage", "worker")) + (
    "tools/odrp-private-host-candidate/panel/tests/build.test.mjs",
    "tools/odrp-private-host-candidate/panel/tests/proxy.test.mjs",
)
COMMANDS = ("source-pins", "node-version", "uv-version", "contracts", "build",
            "installed-environment", "install", "installed-verification", "qualify", "node")


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def absolute_directory(value, name):
    require(type(value) is str and value and Path(value).is_absolute(), name + " must be absolute")
    path = Path(value)
    require(not path.is_symlink() and path.resolve() == path, name + " must not traverse symlinks")
    return path


def settings(environ, root):
    output = absolute_directory(environ.get("ODSP_CI_OUTPUT"), "ODSP_CI_OUTPUT")
    cache = absolute_directory(environ.get("ODSP_CI_CACHE"), "ODSP_CI_CACHE")
    require(not output.is_relative_to(root) and output != root.parent,
            "attempt evidence must be outside the checkout")
    require(cache.is_dir() and not cache.is_relative_to(root)
            and not output.is_relative_to(cache) and not cache.is_relative_to(output),
            "offline cache and evidence must be separate")
    return output, cache


def metadata(environ, identity):
    require(environ.get("CI_REPO") == REPOSITORY, "canonical repository metadata required")
    require(environ.get("CI_COMMIT_SHA") == identity["commit"], "runner commit differs from checkout")
    require(environ.get("CI_PIPELINE_EVENT") == "manual", "this preparation profile is manual only")
    return {key: environ.get(key) for key in ("CI_REPO", "CI_COMMIT_SHA", "CI_PIPELINE_EVENT",
            "CI_PIPELINE_NUMBER", "CI_WORKFLOW_NAME", "CI_SYSTEM_VERSION")}


def nise_selection(environ):
    """Native qualification is an explicit host choice, never inferred from discovery."""
    mode = environ.get("ODSP_CI_NISE_MODE", "contracts")
    require(type(mode) is str and mode in {"contracts", "native"},
            "ODSP_CI_NISE_MODE must be contracts or native")
    bindings = {}
    if mode == "native":
        for key in ("CIW_NISE_CHECKOUT", "CIW_NISE_PYTHON"):
            value = environ.get(key)
            require(type(value) is str and bool(value) and Path(value).is_absolute(),
                    "native NISE qualification requires absolute " + key)
            path = Path(value)
            require(path.is_dir() if key == "CIW_NISE_CHECKOUT" else
                    path.is_file() and os.access(path, os.X_OK), "unavailable host binding: " + key)
            bindings[key] = str(path)
    return {"mode": mode, "bindings": bindings}


def selected_contract_tests(selection):
    require(selection["mode"] in {"contracts", "native"}, "unknown NISE qualification mode")
    return CONTRACT_TESTS + (NATIVE_NISE_TESTS if selection["mode"] == "native" else ())


def selected_commands(selection):
    return COMMANDS + (("installed-native",) if selection["mode"] == "native" else ())


def command_env(root, cache, selection=None):
    # Explicit child environment is hygiene for this invocation, not a host sandbox.
    result = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
              if key in os.environ}
    result.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(root / "src"),
                  OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PIP_NO_INDEX="1",
                  PIP_DISABLE_PIP_VERSION_CHECK="1", UV_OFFLINE="1", UV_NO_PYTHON_DOWNLOADS="1",
                  UV_CACHE_DIR=str(cache), UV_COMPILE_BYTECODE="false")
    if selection is not None:
        result.update(selection["bindings"])
    return result


def junit(path):
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    require(totals["tests"] > 0 and all(totals[k] == 0 for k in ("failures", "errors", "skipped")),
            "missing, failed or skipped contract tests")
    return totals


def load_attempt(output, identity, asserted, selection=None):
    selection = nise_selection({}) if selection is None else selection
    record = json.loads((output / "attempt.json").read_text())
    require(record["source"] == identity and record["runner_asserted_metadata"] == asserted,
            "attempt identity changed")
    require(record["python"] == str(Path(sys.executable).absolute()), "build interpreter changed")
    require(record["nise_qualification"] == selection, "NISE qualification selector or binding changed")
    return record


def run_checked(argv, output, name, *, timeout, env, cwd=None):
    destination = output / "commands" / name
    require(not destination.exists(), "command evidence already exists; use a fresh attempt")
    record = run_command(argv, ROOT if cwd is None else cwd, destination, timeout=timeout, env=env)
    require(record["status"] == "PASS", "command failed: " + name + "; inspect retained receipt")
    return record


def installed_native_files(output):
    manifest_path = output / "installed-native-fixtures.json"
    manifest = json.loads(manifest_path.read_text())
    expected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in INSTALLED_NISE_TESTS}
    require(manifest == {"schema": "odsp.woodpecker-installed-fixtures.v1", "files": expected},
            "installed native fixture selection or source hash changed")
    paths = [manifest_path, output / "installed-native.xml"]
    for name, digest in expected.items():
        path = output / "installed-native-fixtures" / Path(name).name
        require(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                "installed native fixture copy changed")
        paths.append(path)
    junit(output / "installed-native.xml")
    return paths


def result_artifacts(name, output, identity, selection=None):
    """Revalidate real nested outcomes, not just a successful parent exit marker."""
    release = output / "release"
    if name == "contracts":
        junit(output / "contracts.xml")
        paths = [output / "contracts.xml"]
    elif name == "build":
        build = json.loads((release / "build-receipt.json").read_text())
        manifest = contract.verify_manifest(release / "wheel/release-manifest.json")
        require(build["status"] == "passed" and build["source_commit"] == identity["commit"]
                and build["source_tree"] == identity["tree"] and build["identity"] == manifest["identity"]
                and build["reproducible_wheels"] == [manifest["identity"]["wheel_sha256"]] * 2,
                "build evidence mismatch")
        require(manifest["identity"]["source_commit"] == identity["commit"]
                and manifest["identity"]["source_tree"] == identity["tree"], "wheel source mismatch")
        paths = list(release.rglob("*"))
    elif name == "install":
        actual = json.loads((output / "commands/installed-verification/command.log").read_text())
        expected = json.loads((release / "identity.json").read_text())
        require(actual == expected, "installed identity mismatch")
        paths = [output / "commands/installed-verification/command.log"]
        if selection is not None and selection["mode"] == "native":
            paths.extend(installed_native_files(output))
    elif name == "qualify":
        area = output / "qualification"
        report = json.loads((area / "report.json").read_text())
        require(report["status"] == "LOCAL_PASS" and report["source"] == identity
                and set(report["phases"]) == set(QUALIFICATION_PHASES), "qualification source/phases mismatch")
        for key, row in report["phases"].items():
            directory = area / key
            require(row["status"] == "PASS" and row["command"]["status"] == "PASS"
                    and row["command"]["returncode"] == 0
                    and row["command"]["surviving_child_processes"] == 0,
                    "qualification command incomplete")
            require(hashlib.sha256((directory / "result.json").read_bytes()).hexdigest() == row["result_sha256"],
                    "phase result digest mismatch")
            require(json.loads((directory / "result.json").read_text()) == row["receipt"], "phase receipt mismatch")
            require(hashlib.sha256((directory / "command.log").read_bytes()).hexdigest() == row["command"]["log_sha256"],
                    "phase command log mismatch")
            validate_phase(key, row["receipt"], directory)
        paths = list(area.rglob("*"))
    else:
        parse_tap((output / "commands/node/command.log").read_text())
        paths = [output / "commands/node/command.log"]
    files = {}
    for path in sorted(paths):
        require(not path.is_symlink(), "result evidence symlink refused")
        if path.is_file():
            files[path.relative_to(output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    require(bool(files), "result evidence is missing")
    return files


def preflight(output, cache, identity, asserted, selection=None):
    selection = nise_selection({}) if selection is None else selection
    require(platform.system() == "Linux" and Path("/proc").is_dir(), "Linux resource sampler required")
    require(platform.python_version() == "3.12.14", "declared Python 3.12.14 required")
    require(not output.exists(), "use a new empty attempt directory")
    require(shutil.which("node") and shutil.which("uv"), "preprovisioned Node and uv required")
    output.mkdir(parents=True)
    record = {"schema": "odsp.woodpecker-local-attempt.v1", "source": identity,
              "runner_asserted_metadata": asserted, "metadata_authentication": "not_established",
              "python": str(Path(sys.executable).absolute()), "cache": str(cache),
              "nise_qualification": selection,
              "created_at": datetime.now(timezone.utc).isoformat(), "backend": "trusted-local",
              "network_isolation": "not_established", "deployment": "not_performed"}
    write_new(output / "attempt.json", record)
    env = command_env(ROOT, cache, selection)
    run_checked([sys.executable, "-B", "scripts/monorepo.py"], output, "source-pins", timeout=120, env=env)
    run_checked(["node", "--version"], output, "node-version", timeout=10, env=env)
    require((output / "commands/node-version/command.log").read_text().strip() == "v24.19.0",
            "declared Node 24.19.0 required")
    run_checked(["uv", "--version"], output, "uv-version", timeout=10, env=env)
    write_new(output / "preflight.json", {"status": "PASS", "source": identity})


def installed_native(output, installed, env):
    """Copy selected tests only; the isolated child must import the installed wheel."""
    fixtures = output / "installed-native-fixtures"
    fixtures.mkdir()
    hashes = {}
    for name in INSTALLED_NISE_TESTS:
        source = ROOT / name
        require(source.is_file() and not source.is_symlink(), "native test source is missing or linked")
        raw = source.read_bytes()
        (fixtures / source.name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    write_new(output / "installed-native-fixtures.json",
              {"schema": "odsp.woodpecker-installed-fixtures.v1", "files": hashes})
    isolated_env = {key: value for key, value in env.items() if key != "PYTHONPATH"}
    program = ("import json,os,pathlib,sys; import ciw; "
               "origin=pathlib.Path(ciw.__file__).resolve(); prefix=pathlib.Path(sys.prefix).resolve(); "
               "assert origin.is_relative_to(prefix), 'ciw is not from the installed environment'; "
               "print(json.dumps({'installed_ciw_origin':str(origin),'prefix':str(prefix)}),flush=True); "
               "import pytest; raise SystemExit(pytest.main(" + repr([
                   "-q", "-p", "no:cacheprovider", "-c", os.devnull,
                   "--rootdir=" + str(fixtures), "--confcutdir=" + str(fixtures),
                   str(fixtures), "--junitxml=" + str(output / "installed-native.xml"),
                   "--basetemp=" + str(output / "installed-native-temporary")]) + "))")
    run_checked([str(installed / "bin/python"), "-I", "-B", "-c", program],
                output, "installed-native", timeout=180, env=isolated_env, cwd=fixtures)
    installed_native_files(output)


def phase(name, output, cache, identity, selection=None):
    selection = nise_selection({}) if selection is None else selection
    require((output / "preflight.json").is_file(), "preflight incomplete")
    require(not (output / (name + ".json")).exists(), "phase already attempted")
    position = PHASES.index(name)
    for earlier in PHASES[:position]:
        require(json.loads((output / (earlier + ".json")).read_text())["status"] == "PASS",
                "earlier phase did not pass")
    env = command_env(ROOT, cache, selection)
    release = output / "release"
    installed = output / "installed"
    record = {"status": "FAIL", "phase": name, "source": identity}
    try:
        if name == "contracts":
            run_checked([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                         *selected_contract_tests(selection), "--junitxml=" + str(output / "contracts.xml"),
                         "--basetemp=" + str(output / "contract-temporary")], output, name, timeout=300, env=env)
            record["tests"] = junit(output / "contracts.xml")
        elif name == "build":
            run_checked([sys.executable, "-B", "scripts/build_private_release.py", "--output-dir", str(release)],
                        output, name, timeout=240, env=env)
            build = json.loads((release / "build-receipt.json").read_text())
            require(build["status"] == "passed" and build["source_commit"] == identity["commit"], "build mismatch")
            record["identity"] = build["identity"]
        elif name == "install":
            run_checked(["uv", "venv", "--offline", "--no-python-downloads", "--python", sys.executable,
                         str(installed)], output, "installed-environment", timeout=60, env=env)
            wheel, = (release / "wheel").glob("*.whl")
            run_checked(["uv", "pip", "install", "--offline", "--no-python-downloads", "--no-deps",
                         "--python", str(installed / "bin/python"), "-r", str(release / "requirements.lock"),
                         str(wheel)], output, name, timeout=180, env=env)
            program = ("import json; from ciw.private_release import verify_release; print(json.dumps(verify_release("
                       + repr(str(release / "wheel/release-manifest.json")) + ")))")
            run_checked([str(installed / "bin/python"), "-I", "-B", "-c", program], output,
                        "installed-verification", timeout=30, env=env)
            if selection["mode"] == "native":
                installed_native(output, installed, env)
        elif name == "qualify":
            wheel, = (release / "wheel").glob("*.whl")
            run_checked([sys.executable, "-B", "scripts/check_private_host_qualification.py", "--candidate-root", str(ROOT),
                         "--python", str(installed / "bin/python"), "--wheel", str(wheel), "--manifest",
                         str(release / "wheel/release-manifest.json"), "--output-dir", str(output / "qualification")],
                        output, name, timeout=600, env=env)
            qualification = json.loads((output / "qualification/report.json").read_text())
            require(qualification["status"] == "LOCAL_PASS" and qualification["source"] == identity,
                    "qualification did not pass for this source")
        elif name == "node":
            run_checked(["node", "--test", "--test-reporter=tap", *NODE_TESTS], output, name, timeout=180, env=env)
            record["tests"] = parse_tap((output / "commands/node/command.log").read_text())
        require(source_identity(ROOT) == identity, "source changed during phase")
        record["artifacts"] = result_artifacts(name, output, identity, selection)
        record["status"] = "PASS"
    except BaseException as exc:
        record["error_type"] = type(exc).__name__
        raise
    finally:
        write_new(output / (name + ".json"), record)


def summarize(output, identity, selection=None):
    selection = nise_selection({}) if selection is None else selection
    phases = {}
    for name in ("preflight", *PHASES):
        path = output / (name + ".json")
        phases[name] = json.loads(path.read_text()) if path.is_file() else {"status": "MISSING"}
    passed = all(row.get("status") == "PASS" and row.get("source") == identity for row in phases.values())
    result_errors = {}
    for name in PHASES:
        try:
            require(result_artifacts(name, output, identity, selection) == phases[name].get("artifacts"),
                    "nested result evidence changed")
        except Exception as exc:
            result_errors[name] = type(exc).__name__ + ": " + str(exc)
            passed = False
    commands = {}
    for name in selected_commands(selection):
        directory = output / "commands" / name
        path = directory / "command.json"
        record = json.loads(path.read_text()) if path.is_file() else {"status": "MISSING"}
        commands[name] = record
        if record.get("status") != "PASS" or record.get("returncode") != 0:
            passed = False
        log = directory / "command.log"
        if not log.is_file() or record.get("log_sha256") != hashlib.sha256(log.read_bytes()).hexdigest():
            passed = False
    files = {}
    for path in sorted(output.rglob("*")):
        # Environment binaries and mutable test workspaces are not evidence exports.
        relative = path.relative_to(output)
        if any(part in {"installed", "contract-temporary", "installed-native-temporary", "__pycache__"}
               for part in relative.parts):
            continue
        require(not path.is_symlink(), "evidence symlink refused")
        if path.is_file():
            files[relative.as_posix()] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                         "size_bytes": path.stat().st_size}
    report = {"schema": "odsp.woodpecker-local-summary.v1", "source": identity,
              "status": "LOCAL_PIPELINE_PASS" if passed else "FAIL", "phases": phases, "commands": commands,
              "result_errors": result_errors,
              "nise_qualification": {"mode": selection["mode"],
                  "status": ("PASS" if passed else "FAIL") if selection["mode"] == "native" else "NOT_REQUESTED",
                  "source_tests": list(selected_contract_tests(selection)),
                  "installed_tests": list(INSTALLED_NISE_TESTS) if selection["mode"] == "native" else []},
              "files": files, "docker_gate": "NOT_EXECUTED_REQUIRES_APPROVED_DISPOSABLE_RUNNER",
              "hosted_woodpecker": "NOT_CONFIGURED", "github_release_collector": "NOT_SATISFIED",
              "full_scientific_suite": "NOT_EXECUTED", "publication": "not_performed",
              "deployment": "not_performed"}
    write_new(output / "summary.json", report)
    require(passed, "one or more preparation phases missing or failed")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", *PHASES, "summarize"))
    args = parser.parse_args()
    try:
        identity = source_identity(ROOT)
        selection = nise_selection(os.environ)
        asserted = metadata(os.environ, identity)
        output, cache = settings(os.environ, ROOT)
        if args.phase == "preflight":
            preflight(output, cache, identity, asserted, selection)
        else:
            load_attempt(output, identity, asserted, selection)
            if args.phase == "summarize":
                summarize(output, identity, selection)
            else:
                phase(args.phase, output, cache, identity, selection)
        print(json.dumps({"phase": args.phase, "status": "PASS", "source_commit": identity["commit"]}))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"phase": args.phase, "status": "FAIL", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
