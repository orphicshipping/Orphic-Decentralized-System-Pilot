"""Replay the source-bound standalone Kani command using an existing runtime.

No download, rustup configuration, credential, registry or deployment mutation.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from qualification import HERE, REPO, check_sources, check_kani_result, digest


def run(runtime, output):
    runtime, output = Path(runtime).resolve(), Path(output).resolve()
    check_sources()
    # This is a local trusted-launcher reproducibility check. The file manifest
    # is reviewable source, not an uploaded certificate or a permission grant.
    files = json.loads((HERE / "tool-files.json").read_bytes())["files"]
    for relative, expected in files.items():
        path = runtime / relative
        if not path.is_file() or digest(path) != expected:
            raise ValueError("native proof runtime binding differs: " + relative)
    if shutil.disk_usage(output.parent).free < 1024**3 + 64*1024**2:
        raise ValueError("native proof replay requires 64 MiB working space plus 1 GiB reserve")
    output.mkdir(mode=0o700, exist_ok=False)
    env = {"PATH": str(runtime / "kani-0.68.0/bin") + ":" + str(runtime / "nightly/bin") + ":/usr/bin:/bin",
           "LD_LIBRARY_PATH": str(runtime / "nightly/lib"), "LANG": "C.UTF-8",
           "CARGO_HOME": str(output / "cargo-home"), "RUSTFLAGS": "--edition=2021"}
    command = [str(runtime / "kani-0.68.0/bin/kani-driver"), "native/odsp-proof/src/lib.rs",
               "--target-dir", str(output), "-Z", "unstable-options", "--harness-timeout", "180s",
               "--export-json", str(output / "result.json"), "--output-format", "terse"]
    sources = {relative: digest(REPO / relative) for relative in check_sources()["files"]}
    sources["native/odsp-proof/src/lib.rs"] = digest(HERE / "src/lib.rs")
    record = {"command": command, "source_sha256": sources, "runtime_sha256": files,
              "exit_code": None, "native_sp1_verified": False, "execution_authorized": False}
    (output / "command.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    with (output / "run.log").open("wb") as log:
        proc = subprocess.run(command, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=780)
    record["exit_code"] = proc.returncode
    (output / "command.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    if proc.returncode:
        raise RuntimeError("native Kani command failed; retained output: " + str(output))
    check_sources()
    if any(digest(REPO / path) != expected for path, expected in sources.items()):
        raise ValueError("native proof source changed during execution")
    record["summary"] = check_kani_result(json.loads((output / "result.json").read_bytes()))
    record["evidence_sha256"] = {name: digest(output / name) for name in ("command.json", "run.log", "result.json")}
    (output / "receipt.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.runtime, args.output)["summary"], sort_keys=True))
