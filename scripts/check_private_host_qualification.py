#!/usr/bin/env python3
"""Run mandatory, offline private-host fixture qualification against one wheel.

The limits below are declared local fixture targets, not customer SLOs. The
native MCP transport is stdio. The HTTP operator adapter and local DO/R2 storage
substitutes are reported separately. This runner never contacts a hosted service,
installs dependencies, builds an image, or retries a state-changing failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
PHASES = ("runtime", "native_clients", "reference_clients", "soak", "adapter_process", "gateway_auth", "storage_faults")
TARGETS = {
    "scope": "declared_local_synthetic_fixture_only",
    "clients": 3,
    "concurrent_calls_per_client": 8,
    "operation_timeout_seconds": 60,
    "soak_seconds": 60,
    "soak_minimum_completed_calls": 100,
    "soak_command_timeout_seconds": 90,
    "aggregate_process_rss_limit_bytes": 1024 * 1024 * 1024,
    "process_count_limit": 96,
    "log_limit_bytes": 8 * 1024 * 1024,
    "resource_sample_interval_seconds": 0.05,
    "resource_enforcement": "sampled_process_tree_abort_not_cgroup_reservation",
}
REQUIRED_CHECKS = {
    "runtime": {"installed_wheel_bytes", "release_service_bytes", "source_wheel_identity", "isolated_outside_checkout", "required_dependencies"},
    "native_clients": {"python_official_mcp_parity", "execution_refusal", "restart_owner_continuity",
        "paging", "handoff", "revocation", "reduced_grants", "concurrent_discovery_read"},
    "reference_clients": {"mounted_reference_sdk_mcp_parity", "reference_resource_read", "reference_scope_reduction", "immutable_catalog"},
    "soak": {"three_client_soak", "minimum_completed_calls", "bounded_duration"},
    "adapter_process": {"real_loopback_process", "installed_stdio_execution", "checkpoint_restart",
        "owner_isolation", "mounted_reference_wrapper", "remote_handoff_restart", "unicode_envelope_bound", "queue_overflow", "unknown_outcome_fence"},
    "gateway_auth": {"gateway_regressions_zero_skip"},
    "storage_faults": {"worker_storage_regressions_zero_skip"},
}
LIMITATIONS = [
    "No hosted Cloudflare Durable Object, R2, container, live identity, or remote portal request was made.",
    "DO/R2 fixtures are local substitutes; their passes do not qualify the hosted runtime.",
    "The operator HTTP wrapper is not a native Streamable HTTP MCP endpoint; native MCP uses stdio.",
    "Synthetic model execution does not establish physical qualification or a customer SLO.",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def redact(value):
    """Receipts retain fixture diagnostics, never authorization header values."""
    value = re.sub(r"(?i)(bearer\s+)[^\s\"',}]+", r"\1[REDACTED]", str(value))
    return re.sub(r'(?i)([\"\']?(?:access_token|authorization|secret|credential)[\"\']?\s*[:=]\s*)[\"\']?[^\s,}\n]+',
                  r'\1[REDACTED]', value)


def source_identity(root):
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(root), *arguments], text=True, timeout=10).strip()
    dirty = git("status", "--porcelain", "--untracked-files=all")
    require(not dirty, "Candidate checkout must be committed and clean before qualification")
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"), "clean": True}


def process_tree(pid):
    """Read Linux process metadata without inspecting environment/command lines."""
    rows = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            # comm may contain spaces or parentheses; fields start after its last ).
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            rows[int(directory.name)] = (int(fields[1]), int(fields[19]), int(fields[21]) * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            continue
    descendants = {pid}
    while True:
        expanded = descendants | {child for child, row in rows.items() if row[0] in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    return {child: rows[child] for child in descendants if child in rows}


def stop_processes(proc, observed):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # MCP children may use their own process group. Check start time before kill
    # so a PID reused by another task never becomes a cleanup target.
    for pid, start in observed.items():
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[19]) == start:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError, IndexError):
            pass
    proc.wait(timeout=5)


def run_command(arguments, cwd, destination, *, timeout, env=None):
    require(os.name == "posix" and Path("/proc").is_dir(), "Linux process resource sampler is required")
    destination.mkdir(parents=True, exist_ok=True)
    log = destination / "command.log"
    started = time.monotonic()
    receipt = {"arguments": list(map(str, arguments)), "cwd": str(cwd), "timeout_seconds": timeout,
               "started_at": datetime.now(timezone.utc).isoformat(), "status": "RUNNING",
               "peak_rss_bytes": 0, "peak_processes": 0, "resource_samples": 0}
    observed = {}
    try:
        with log.open("wb") as output:
            proc = subprocess.Popen(arguments, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                while proc.poll() is None:
                    rows = process_tree(proc.pid)
                    observed.update({pid: row[1] for pid, row in rows.items()})
                    rss = sum(row[2] for row in rows.values())
                    receipt["peak_rss_bytes"] = max(receipt["peak_rss_bytes"], rss)
                    receipt["peak_processes"] = max(receipt["peak_processes"], len(rows))
                    receipt["resource_samples"] += 1
                    require(rss <= TARGETS["aggregate_process_rss_limit_bytes"], "Process-tree memory ceiling exceeded")
                    require(len(rows) <= TARGETS["process_count_limit"], "Process-count ceiling exceeded")
                    require(log.stat().st_size <= TARGETS["log_limit_bytes"], "Command log byte ceiling exceeded")
                    require(time.monotonic() - started < timeout, "Command deadline exceeded")
                    time.sleep(TARGETS["resource_sample_interval_seconds"])
                receipt["returncode"] = proc.returncode
                require(proc.returncode == 0, "Qualification command returned a nonzero exit status")
                require(log.stat().st_size <= TARGETS["log_limit_bytes"], "Command log byte ceiling exceeded")
                survivors = []
                for pid, start in observed.items():
                    if pid == proc.pid:
                        continue
                    try:
                        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                        if int(fields[19]) == start and fields[0] != "Z":
                            survivors.append(pid)
                    except (OSError, ValueError, IndexError):
                        pass
                receipt["surviving_child_processes"] = len(survivors)
                require(not survivors, "Qualification command left a child process running")
                receipt["status"] = "PASS"
            except BaseException:
                stop_processes(proc, observed)
                raise
    except Exception as exc:
        receipt.update(status="FAIL", error=redact(type(exc).__name__ + ": " + str(exc)))
    finally:
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        if log.exists():
            with log.open("rb") as source:
                raw = source.read(TARGETS["log_limit_bytes"] + 1)
            receipt["log_truncated"] = len(raw) > TARGETS["log_limit_bytes"]
            log.write_text(redact(raw[:TARGETS["log_limit_bytes"]].decode("utf-8", errors="replace")), encoding="utf-8")
            receipt.update(log="command.log", log_sha256=sha(log))
        save(destination / "command.json", receipt)
    return receipt


def validate_phase(phase, receipt, directory):
    require(type(receipt) is dict and receipt.get("schema") == "odsp.private-fixture-phase.v1", "Missing phase schema")
    require(receipt.get("phase") == phase and receipt.get("status") == "PASS", "Missing, failed, or unknown phase outcome")
    require(receipt.get("skipped") == 0, "Skipped required checks are not a pass")
    checks = receipt.get("checks")
    require(type(checks) is list and checks, "Missing check evidence")
    by_id = {item.get("id"): item for item in checks if type(item) is dict}
    require(len(by_id) == len(checks), "Missing or duplicate check identity")
    require(REQUIRED_CHECKS[phase] <= by_id.keys(), "Missing required fixture check")
    for item in checks:
        require(item.get("status") == "PASS", "Failed or unknown required check")
        evidence = item.get("evidence")
        require(type(evidence) is list and evidence, "Check lacks retained evidence")
        for record in evidence:
            require(type(record) is dict and type(record.get("path")) is str, "Invalid evidence reference")
            relative = Path(record["path"])
            require(not relative.is_absolute() and ".." not in relative.parts, "Evidence escapes phase directory")
            path = directory / relative
            require(not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(directory.resolve()),
                    "Missing or unsafe evidence file")
            require(record.get("sha256") == sha(path), "Evidence digest mismatch")
    return receipt


def parse_tap(raw):
    result = {}
    for field in ("tests", "pass", "fail", "cancelled", "skipped", "todo"):
        matches = re.findall(rf"^# {field} (\d+)\s*$", raw, re.MULTILINE)
        require(len(matches) == 1, "Missing or ambiguous TAP total: " + field)
        result[field] = int(matches[0])
    require(result["tests"] > 0 and result["tests"] == result["pass"], "No complete passing test evidence")
    require(not any(result[name] for name in ("fail", "cancelled", "skipped", "todo")), "Required TAP checks did not all execute")
    return result


def qualify(args):
    root, destination = args.candidate_root.resolve(), args.output_dir.resolve()
    require(not destination.exists(), "Use a new output directory; prior evidence is never overwritten")
    destination.mkdir(parents=True)
    report = {"schema": "odsp.private-host-qualification.v1", "status": "RUNNING", "targets": TARGETS,
              "phases": {}, "limitations": LIMITATIONS, "hosted_runtime": "BLOCKED_NOT_EXECUTED"}
    try:
        report["source"] = source_identity(root)
        require(args.python.is_file(), "Required installed Python runtime is missing")
        require(args.wheel.is_file() and args.manifest.is_file(), "Required candidate wheel or release manifest is missing")
        report["wheel_sha256"] = sha(args.wheel)
        report["manifest_sha256"] = sha(args.manifest)
        helper = root / "tests/private_host_qualification/journeys.py"
        require(helper.is_file(), "Required independent journey harness is missing")
        env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        for phase in PHASES:
            directory = destination / phase
            if phase in {"gateway_auth", "storage_faults"}:
                names = ["test_gateway.mjs"] if phase == "gateway_auth" else ["test_worker.mjs", "test_storage.mjs"]
                paths = [root / "tools/odrp-private-host-candidate/tools/private-mcp-host" / name for name in names]
                require(all(path.is_file() for path in paths), "Required local substitute test runtime/evidence is missing: " + phase)
                command = [args.node, "--test", "--test-reporter=tap", *map(str, paths)]
            else:
                command = [str(args.python), "-I", "-B", str(helper), "--phase", phase,
                           "--candidate-root", str(root), "--wheel", str(args.wheel.resolve()),
                           "--manifest", str(args.manifest.resolve()), "--output-dir", str(directory)]
            command_receipt = run_command(command, destination, directory,
                timeout=TARGETS["soak_command_timeout_seconds"] if phase == "soak" else TARGETS["operation_timeout_seconds"], env=env)
            report["phases"][phase] = {"command": command_receipt, "status": "FAIL"}
            require(command_receipt["status"] == "PASS", "Required phase failed: " + phase)
            if phase in {"gateway_auth", "storage_faults"}:
                totals = parse_tap((directory / "command.log").read_text())
                evidence = {"path": "command.log", "sha256": sha(directory / "command.log")}
                receipt = {"schema": "odsp.private-fixture-phase.v1", "phase": phase, "status": "PASS", "skipped": 0,
                           "scope": "local_DO_R2_substitutes" if phase == "storage_faults" else "local_gateway_fixture",
                           "totals": totals, "checks": [{"id": next(iter(REQUIRED_CHECKS[phase])), "status": "PASS", "evidence": [evidence]}]}
                save(directory / "result.json", receipt)
            receipt = validate_phase(phase, json.loads((directory / "result.json").read_text()), directory)
            report["phases"][phase].update(status="PASS", result_sha256=sha(directory / "result.json"), receipt=receipt)
            save(destination / "report.json", report)
        require(set(report["phases"]) == set(PHASES), "Missing mandatory phase")
        report["status"] = "LOCAL_PASS"
    except Exception as exc:
        report.update(status="FAIL", error=redact(type(exc).__name__ + ": " + str(exc)))
    save(destination / "report.json", report)
    print(json.dumps({"status": report["status"], "report": str(destination / "report.json")}))
    return 0 if report["status"] == "LOCAL_PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, default=ROOT)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--node", default="node")
    parser.add_argument("--output-dir", type=Path, required=True)
    return qualify(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
