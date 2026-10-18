"""Read-only preflight for the dedicated Ubuntu 24.04 native CI runner.

This checks the execution environment, not scientific/provider qualification.
The existing workflow installers and resource/proof gates remain authoritative.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil


GIB = 1024**3
TOOLS = ("git", "python3", "curl", "tar", "unzip", "rustup", "sudo")


def validate(info: dict) -> list[str]:
    errors = []
    if info["runner_environment"] != "self-hosted":
        errors.append("Expected a registered self-hosted runner")
    if info["system"] != "Linux" or info["architecture"] != "x86_64":
        errors.append("Native workflows require Linux x86_64")
    if info["os_id"] != "ubuntu" or info["os_version"] != "24.04":
        errors.append("Native workflows require Ubuntu 24.04")
    if info["available_memory_bytes"] < 7 * GIB:
        errors.append("Need at least 7 GiB available RAM; no reduced-resource fallback")
    if info["free_disk_bytes"] < 20 * GIB:
        errors.append("Need at least 20 GiB free runner-temp disk")
    if info["workspace_free_disk_bytes"] < 20 * GIB:
        errors.append("Need at least 20 GiB free workspace disk")
    for tool in TOOLS:
        if not info["tools"].get(tool):
            errors.append(f"Missing prerequisite executable: {tool}")
    return errors


def inspect() -> dict:
    release = platform.freedesktop_os_release() if platform.system() == "Linux" else {}
    memory = {}
    if Path("/proc/meminfo").exists():
        memory = {key: int(value.split()[0]) * 1024
                  for key, value in (line.split(":", 1) for line in
                                     Path("/proc/meminfo").read_text().splitlines())}
    temp = Path(os.environ.get("RUNNER_TEMP", "/tmp"))
    return {
        "schema": "ncp.native-runner-preflight.v1",
        "runner_environment": os.environ.get("RUNNER_ENVIRONMENT", ""),
        "runner_name": os.environ.get("RUNNER_NAME", ""),
        "candidate_sha": os.environ.get("GITHUB_SHA", ""),
        "system": platform.system(), "architecture": platform.machine(),
        "os_id": release.get("ID", ""), "os_version": release.get("VERSION_ID", ""),
        "available_memory_bytes": memory.get("MemAvailable", 0),
        "free_disk_bytes": shutil.disk_usage(temp).free,
        "workspace_free_disk_bytes": shutil.disk_usage(Path.cwd()).free,
        "tools": {tool: shutil.which(tool) for tool in TOOLS},
        "qualification": "NOT_ESTABLISHED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    info = inspect()
    info["errors"] = validate(info)
    info["status"] = "FAIL" if info["errors"] else "PASS"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps(info, indent=2))
    return int(bool(info["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
