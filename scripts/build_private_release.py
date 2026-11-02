"""Build an offline private candidate from one clean committed Git tree.

No publish, pull, download, credential or deployment action is performed. Two
independent wheel builds use the same recorded interpreter/backend and epoch.
The result is a local build receipt, not the separate CI evidence collector's
56-job/45-artifact qualification and not an image or scientific attestation.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata as metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("private_release_build_contract", ROOT / "src/ciw/private_release.py")
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)
PRIVATE = "tools/odrp-private-host-candidate"
REPOSITORY = "orphicsystems/Orphic-Decentralized-Systems-Protocol"


def git(root, *args, binary=False):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True, timeout=120)
    return result.stdout if binary else result.stdout.decode().strip()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def dependency_inventory():
    """Record the complete selected environment, excluding packaging tools."""
    excluded = {contract.DISTRIBUTION, "pip", "setuptools", "wheel"}
    return dict(sorted((re.sub(r"[-_.]+", "-", item.metadata["Name"]).lower(), item.version)
                       for item in metadata.distributions()
                       if re.sub(r"[-_.]+", "-", item.metadata["Name"]).lower() not in excluded))


def audit_pins(root, revision):
    raw = git(root, "show", revision + ":instruments/manifest.json", binary=True)
    imported = contract.strict_json(raw)["modules"]
    contract.require(len(imported) == 21, "expected all 21 imported modules")
    records = []
    for module in imported:
        tree = git(root, "rev-parse", revision + ":" + module["path"])
        contract.require(tree == module["import_tree"], "import tree mismatch: " + module["path"])
        records.append({key: module[key] for key in ("role", "path", "import_revision", "import_tree", "runtime_revision") if key in module})
    capsule = "examples/workcells/godot-prop"
    source_pins = {}
    for name in git(root, "ls-tree", "-r", "--name-only", revision, "--", "src/ciw").splitlines():
        if name.endswith("-runtimes.json") or name.endswith("-runtime.json"):
            source_pins[name] = sha256(git(root, "show", revision + ":" + name, binary=True)).hexdigest()
    return {"schema": "odsp.private-preserved-sources.v1", "imports": records,
            "manifest_sha256": sha256(raw).hexdigest(), "runtime_pin_files": source_pins,
            "godot_prop": {"path": capsule, "tree": git(root, "rev-parse", revision + ":" + capsule)}}


def export_source(root, revision, destination):
    # Git archive uses committed bytes, never untracked or mutable source files.
    # Only build inputs are materialized. Imported engines are verified directly
    # by their Git trees and are neither wheel inputs nor copied into this image.
    inputs = ("pyproject.toml", "LICENSE", "src", PRIVATE,
              "tools/mcp-operator-console/console.py", "examples/numerical_operations/prepare.py",
              "tests/reference_fixture_helpers.py")
    raw = git(root, "archive", "--format=tar", revision, "--", *inputs, binary=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(destination, filter="data")


def bound_wheel(path, source, epoch):
    """Add the generated source binding and update the wheel's RECORD exactly."""
    with zipfile.ZipFile(path) as archive:
        entries = {item.filename: archive.read(item) for item in archive.infolist() if not item.is_dir()}
    record = next(name for name in entries if name.endswith(".dist-info/RECORD"))
    entries.pop(record)
    entries["ciw/" + contract.SOURCE_MARKER] = contract.canonical(source) + b"\n"
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    for name, data in sorted(entries.items()):
        encoded = base64.urlsafe_b64encode(sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + encoded, len(data)))
    writer.writerow((record, "", ""))
    entries[record] = rows.getvalue().encode()
    stamp = time.gmtime(max(epoch, 315532800))[:6]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, stamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def context_files(source, output):
    mapping = {}
    candidate = source / PRIVATE
    for entry in sorted((candidate / "tools/private-mcp-host").iterdir()):
        if entry.suffix in {".py", ".mjs"}:
            mapping["tools/private-mcp-host/" + entry.name] = entry
    for name in ("tools/mcp-operator-console/console.py", "examples/numerical_operations/prepare.py"):
        mapping[name] = source / name
    mapping["tools/private-mcp-host/reference_fixture_helpers.py"] = source / "tests/reference_fixture_helpers.py"
    checksums = {}
    for name, original in mapping.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        checksums[name] = contract.file_hash(target)
    for name in ("Dockerfile", "Dockerfile.ci"):
        shutil.copyfile(candidate / name, output / name)
    return checksums


def build(root, output, dependencies):
    contract.require(root.is_dir(), "source checkout is required")
    contract.require(not output.exists() and not output.is_symlink(), "use a new release output directory")
    contract.require(not output.is_relative_to(root), "release output must be outside the checkout")
    contract.require(not git(root, "status", "--porcelain", "--untracked-files=all"), "private release requires a clean committed checkout")
    revision = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    policy = contract.strict_json(git(root, "show", revision + ":release/qualification-policy.json", binary=True))
    contract.require(policy["repository"] == REPOSITORY, "canonical repository policy mismatch")
    epoch = int(git(root, "show", "-s", "--format=%ct", revision))
    pins = audit_pins(root, revision)
    output.mkdir(parents=True)
    receipt = {"schema": "odsp.private-build-receipt.v1", "status": "failed", "repository": REPOSITORY,
               "source_commit": revision, "source_tree": tree, "source_date_epoch": epoch,
               "python": platform.python_version(), "platform": platform.platform(),
               "build_tools": {name: metadata.version(name) for name in ("setuptools", "wheel")},
               "reproducibility_scope": "two isolated source exports with this interpreter, platform and backend; cross-toolchain and base-image reproduction unproven",
               "dependency_evidence": "exact selected installed versions; dependency binary provenance requires retained wheelhouse or pinned cached image",
               "ci_release_qualification": "not_performed", "publication": "not_performed", "deployment": "not_performed"}
    try:
        wheels = []
        with tempfile.TemporaryDirectory(prefix=".odsp-private-build-", dir=output.parent) as temporary:
            area = Path(temporary)
            for index in range(2):
                source = area / ("source-" + str(index))
                source.mkdir()
                export_source(root, revision, source)
                if index == 0:
                    services = context_files(source, output)
                closure = {"dependencies": dependencies, "service_files": services, "pins_digest": contract.digest(pins)}
                binding = {"schema": contract.SOURCE_SCHEMA, "repository": REPOSITORY, "source_commit": revision,
                           "source_tree": tree, "closure_digest": contract.digest(closure)}
                wheel_dir = area / ("wheel-" + str(index))
                env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch), "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
                command = [sys.executable, "-B", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "--wheel-dir", str(wheel_dir), str(source)]
                result = subprocess.run(command, cwd=area, env=env, capture_output=True, text=True, timeout=180)
                (output / ("build-" + str(index) + ".log")).write_text(result.stdout + result.stderr)
                contract.require(result.returncode == 0, "offline wheel build failed; see build log")
                wheel, = wheel_dir.glob("*.whl")
                # Every packaged source/resource byte must originate in this exact
                # source tree. The generated binding is the single declared addition.
                with zipfile.ZipFile(wheel) as archive:
                    for name in archive.namelist():
                        if name.startswith("ciw/") and not name.endswith("/"):
                            original = source / "src" / name
                            contract.require(original.is_file() and archive.read(name) == original.read_bytes(), "source-to-wheel byte mismatch: " + name)
                bound_wheel(wheel, binding, epoch)
                wheels.append(wheel.read_bytes())
                filename = wheel.name
            contract.require(wheels[0] == wheels[1], "independent wheel builds were not byte-reproducible")
            destination = output / "wheel" / filename
            destination.parent.mkdir()
            destination.write_bytes(wheels[0])
        files = contract.wheel_files(destination)
        manifest = {"schema": contract.MANIFEST_SCHEMA, "identity": {},
                    "wheel": {"filename": filename, "size_bytes": destination.stat().st_size},
                    "distribution": {"name": contract.DISTRIBUTION, "version": filename.split("-")[1]},
                    "package_files": files, **closure}
        identity = {"schema": contract.IDENTITY_SCHEMA, "repository": REPOSITORY, "source_commit": revision,
                    "source_tree": tree, "wheel_sha256": contract.file_hash(destination), "package_digest": contract.package_digest(manifest)}
        manifest["identity"] = identity
        manifest_path = output / "wheel/release-manifest.json"
        write(manifest_path, manifest)
        contract.verify_manifest(manifest_path)
        write(output / "identity.json", identity)
        write(output / "pins.json", pins)
        write(output / "dependencies.json", dependencies)
        (output / "requirements.lock").write_text("".join(f"{name}=={version}\n" for name, version in sorted(dependencies.items())))
        (output / "wheelhouse").mkdir()
        receipt.update(status="passed", identity=identity, manifest_sha256=contract.file_hash(manifest_path),
                       package_files=len(files), imported_subtrees=len(pins["imports"]),
                       source_byte_check="passed", reproducible_wheels=[sha256(raw).hexdigest() for raw in wheels])
        return receipt
    except Exception as exc:
        receipt["error"] = str(exc)
        raise
    finally:
        write(output / "build-receipt.json", receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dependencies", type=Path, help="exact environment inventory JSON; defaults to this interpreter")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.inventory_only:
            print(json.dumps(dependency_inventory(), sort_keys=True, indent=2))
            return 0
        contract.require(args.output_dir is not None, "--output-dir is required")
        dependencies = contract.strict_json(args.dependencies.read_bytes()) if args.dependencies else dependency_inventory()
        report = build(args.source_root.resolve(), args.output_dir.absolute(), dependencies)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
