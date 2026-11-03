"""Local native-proof qualification inputs and evidence checks; no registry write.

SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HARNESSES = frozenset({
    "proofs::every_accepted_statement_is_exact_and_bounded",
    "proofs::every_out_of_domain_statement_refuses",
    "proofs::every_wrong_header_refuses",
    "proofs::every_short_or_single_trailing_byte_refuses",
})


def digest(path):
    result = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def check_sources(repo=REPO):
    lock = json.loads((HERE / "source-lock.json").read_bytes())
    for relative, expected in lock["files"].items():
        path = Path(repo) / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError("native proof source binding differs: " + relative)
    return lock


def check_kani_result(result):
    """Validate a result from an actual host-owned run, never an uploaded proof.

    The caller must independently bind this JSON to the executed binaries,
    command, exit code and source hashes. JSON is not a signed certificate.
    """
    verification = result["verification_results"]
    summary = verification["summary"]
    if any(summary.get(name) != value for name, value in {
        "total_harnesses": 4, "executed": 4, "successful": 4,
        "failed": 0, "status": "completed",
    }.items()):
        raise ValueError("all four native Kani harnesses must complete successfully")
    rows = verification["results"]
    if len(rows) != 4 or {row["harness_id"] for row in rows} != HARNESSES:
        raise ValueError("native Kani harness identities differ")
    for row in rows:
        if row["status"] != "Success" or not row["checks"]:
            raise ValueError("native Kani harness did not succeed")
        # Covers can be satisfied or unreachable; assertions must be successful
        # or explicitly unreachable. Check the complete aggregate below too.
        if any(check["status"] not in {"Success", "Unreachable", "Satisfied"}
               for check in row["checks"]):
            raise ValueError("native Kani has failed or unresolved checks")
    details = result["property_details"]
    if len(details) != 4 or {row["harness_id"] for row in details} != HARNESSES:
        raise ValueError("native Kani property identities differ")
    for row in details:
        properties = row["property_details"]
        for name in ("failed", "undetermined", "solver_error", "unsatisfiable", "uncovered"):
            if properties[name] != 0:
                raise ValueError("native Kani has unresolved properties")
        if properties["passed"] <= 0:
            raise ValueError("native Kani has no passed properties")
    errors = result["error_details"]
    if len(errors) != 4 or {row["harness_id"] for row in errors} != HARNESSES:
        raise ValueError("native Kani error identities differ")
    for row in errors:
        if row["has_errors"]:
            raise ValueError("native Kani reported errors")
    runtime = json.loads((HERE / "runtime-lock.json").read_bytes())
    if (result["tools"]["kani"] != runtime["kani_version"]
            or result["tools"]["rustc"] != runtime["rustc_version"]
            or result["tools"]["cbmc"] != "6.11.0 (cbmc-6.11.0)"):
        raise ValueError("native Kani tool versions differ")
    return summary


def sp1_preflight(repo=REPO, *, scratch=Path("/tmp"), fork=None, rustc=None):
    """Read-only prerequisites. Does not download, build, register or authorize."""
    lock = check_sources(repo)
    scr = Path(repo) / "instruments/execution/compute-runtime"
    fork = Path(fork) if fork else scr.parent / "notationsystems/SP1-zero-knowledge-virtual-machine"
    blockers = []
    if not fork.is_dir():
        blockers.append({"gate": "sp1_checkout", "required_revision": lock["sp1"]["revision"],
                         "required_tree": lock["sp1"]["tree"], "path": str(fork), "status": "missing"})
    else:
        result = subprocess.run(["git", "--no-replace-objects", "-C", str(fork),
                                 "rev-parse", "HEAD", "HEAD^{tree}"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode or result.stdout.splitlines() != [lock["sp1"]["revision"], lock["sp1"]["tree"]]:
            blockers.append({"gate": "sp1_checkout", "status": "identity_mismatch"})
        status = subprocess.run(["git", "--no-replace-objects", "-C", str(fork),
                                 "status", "--porcelain", "--untracked-files=all"],
                                capture_output=True, text=True, timeout=15)
        if status.returncode or status.stdout:
            blockers.append({"gate": "sp1_checkout", "status": "not_clean"})
    if rustc is None or not Path(rustc).is_file():
        blockers.append({"gate": "succinct_compiler", "status": "missing", **{
            key: lock["sp1"][key] for key in ("toolchain_archive", "toolchain_archive_sha256", "toolchain_archive_bytes")}})
    else:
        output = subprocess.run([str(rustc), "--print", "target-list"], capture_output=True,
                                text=True, timeout=15)
        if output.returncode or lock["sp1"]["target"] not in output.stdout.splitlines():
            blockers.append({"gate": "succinct_compiler", "status": "required_target_unavailable"})
    if not (scr / "zk/guest-device-boundary/Cargo.lock").is_file():
        blockers.append({"gate": "guest_dependency_lock", "status": "not_generated_from_pinned_checkout"})
    free = shutil.disk_usage(scratch).free
    required = lock["sp1"]["local_disk_requirement_bytes"]
    if free < required + 1024**3:
        blockers.append({"gate": "scratch_capacity", "status": "insufficient", "available_bytes": free,
                         "minimum_working_bytes": required, "reserve_bytes": 1024**3})
    blockers.append({"gate": "device_guest_registration", "status": "held_pending_two_matching_native_builds_and_cpu_proof"})
    return {"schema": "odsp.sp1-preflight.v1", "native_sp1_verified": False,
            "source_binding": "matched", "blockers": blockers,
            "registry_mutated": False, "execution_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sources", "sp1-preflight", "check-kani-result"))
    parser.add_argument("--result", type=Path)
    parser.add_argument("--scratch", type=Path, default=Path("/tmp"))
    parser.add_argument("--sp1-checkout", type=Path)
    parser.add_argument("--succinct-rustc", type=Path)
    args = parser.parse_args()
    if args.action == "sources":
        result = {"source_binding": "matched", "files": len(check_sources()["files"])}
        code = 0
    elif args.action == "sp1-preflight":
        result = sp1_preflight(scratch=args.scratch, fork=args.sp1_checkout, rustc=args.succinct_rustc)
        code = 2 if result["blockers"] else 0
    else:
        if not args.result:
            parser.error("--result is required")
        result = check_kani_result(json.loads(args.result.read_bytes()))
        code = 0
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
