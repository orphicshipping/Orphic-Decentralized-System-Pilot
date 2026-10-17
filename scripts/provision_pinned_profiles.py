"""Materialize exact provider profiles from existing local Git commit objects.

The donor worktree is never imported or modified. Each new role checkout owns
its object database, uses the committed manifest pin, and passes the same raw
tracked-byte validation as runtime provisioning. This is source preparation,
not scientific or physical qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from local_provider_sources import local_environment
from provider_checkouts import validate_checkout


ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "calibrated-observable": ("calibrated-observable-runtimes.json",),
    "identified-design": ("calibrated-observable-runtimes.json", "identified-design-runtimes.json"),
    "calibrated-window": ("calibrated-window-runtimes.json",),
}


def _git(arguments: list[str], environment: dict[str, str], data: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", "--no-replace-objects", *arguments], env=environment,
        input=data, capture_output=True, check=False, timeout=120,
    )
    if completed.returncode:
        raise RuntimeError("Pinned local Git source preparation failed")
    return completed.stdout


def _complete_tree(source: Path, revision: str, environment: dict[str, str]) -> None:
    """Do not mistake a partial clone's commit header for available source."""
    expected = {revision: "commit"}
    expected[_git(["-C", str(source), "rev-parse", revision + "^{tree}"], environment).decode().strip()] = "tree"
    entries = _git(["-C", str(source), "ls-tree", "-r", "-t", "-z", revision], environment)
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        _, kind, object_id = entry.split(b"\t", 1)[0].decode("ascii").split()
        if kind in {"blob", "tree"}:
            expected[object_id] = kind
        elif kind != "commit":
            raise ValueError("Unsupported provider tree object")
    observed = _git(["-C", str(source), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                    environment, ("\n".join(expected) + "\n").encode("ascii"))
    if dict(line.split() for line in observed.decode("ascii").splitlines()) != expected:
        raise ValueError("The donor is missing required pinned provider tree or blob objects")


def provision(source: Path, output: Path, profiles: list[str]) -> dict:
    source = source.expanduser().resolve(strict=True)
    output = output.expanduser().absolute()
    for candidate in (output, *output.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise ValueError("Output must not traverse a filesystem link")
    if output.exists():
        raise FileExistsError("Provider output must be a new directory")
    output = output.resolve()
    if output.is_relative_to(source):
        raise ValueError("Provider output must be outside the donor repository")
    if not profiles or len(set(profiles)) != len(profiles):
        raise ValueError("Select at least one distinct profile")
    environment = local_environment(dict(os.environ), [])
    selected = {}
    manifests = {}
    for profile in profiles:
        if profile not in PROFILES:
            raise ValueError("Unknown pinned provider profile")
        pins = {}
        for filename in PROFILES[profile]:
            raw = (ROOT / "src" / "ciw" / filename).read_bytes()
            values = json.loads(raw)
            if pins.keys() & values.keys():
                raise ValueError("A profile must not replace an inherited role pin")
            pins.update(values)
            manifests[filename] = hashlib.sha256(raw).hexdigest()
        for pin in pins.values():
            revision = pin["revision"]
            if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
                raise ValueError("Provider manifests require complete lowercase Git commit IDs")
            if _git(["-C", str(source), "cat-file", "-t", revision], environment).strip() != b"commit":
                raise ValueError("The donor must contain the exact provider commit")
            _complete_tree(source, revision, environment)
        selected[profile] = pins
    # Reserve only after every pin exists. A failure never overwrites earlier
    # evidence; partial preparation remains visibly incomplete and inspectable.
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "ciw-pinned-profile-provisioning.v1", "status": "preparing",
               "source_repository": str(source), "manifest_sha256": manifests,
               "profiles": {}, "scientific_qualification": "not_performed",
               "physical_qualification": "not_established"}
    try:
        for profile, pins in selected.items():
            directory = output / profile
            directory.mkdir()
            roles = {}
            for role, pin in pins.items():
                path = directory / role
                _git(["-c", "init.templateDir=", "init", "--quiet", str(path)], environment)
                _git(["-C", str(path), "fetch", "--quiet", "--no-tags", "--depth=1",
                      source.as_uri(), pin["revision"]], environment)
                _git(["-C", str(path), "checkout", "--quiet", "--detach", pin["revision"]], environment)
                validate_checkout(path, pin["revision"])
                roles[role] = {**pin, "tree": _git(["-C", str(path), "rev-parse", "HEAD^{tree}"], environment).decode().strip(),
                               "path": str(path), "tracked_bytes_verified": True}
                receipt["profiles"][profile] = roles
        receipt["status"] = "PINNED_SOURCES_PREPARED"
        return receipt
    except Exception as exc:
        receipt.update(status="PREPARATION_FAILED", error_type=type(exc).__name__)
        raise
    finally:
        with (output / "provisioning.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repository", type=Path, required=True,
                        help="Local Git object source containing every exact manifest commit")
    parser.add_argument("--output-dir", type=Path, required=True, help="New private checkout directory")
    parser.add_argument("--profile", choices=tuple(PROFILES), action="append", required=True)
    args = parser.parse_args(argv)
    receipt = provision(args.source_repository, args.output_dir, args.profile)
    print(json.dumps({"status": receipt["status"], "profiles": {k: len(v) for k, v in receipt["profiles"].items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
