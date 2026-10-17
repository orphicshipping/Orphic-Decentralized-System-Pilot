"""Installed NCP acceptance using exact retained synthetic GaugeLink artifacts."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys


CASES = {
    "valid": ("EVIDENCE_REQUIRED", 0.03125),
    "interrupted": ("ANALYSIS_UNRESOLVED", None),
    "insufficient-context": ("REQUIREMENTS_NOT_MET", 1.0),
}
PROTOCOLS = ("2025-11-25", "2025-06-18")
SERVER = """import hashlib,json,pathlib,sys
import ciw
from ciw import link_study
from ciw.agent_api import AgentHost,encode
from ciw.agent_mcp import serve
from ciw.session import Session
workspace=pathlib.Path(sys.argv[1]).read_bytes()
assert 'sha256:'+hashlib.sha256(workspace).hexdigest()==sys.argv[2]
assert pathlib.Path(ciw.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())
dispatches=[]
def forbidden(*args,**kwargs):
    dispatches.append('Session.handle')
    raise AssertionError('Read-only consumer dispatched scientific work')
Session.handle=forbidden
registry=link_study.registry(bind=False)
with AgentHost(registry=registry,inputs={'workspace':workspace,'catalog':encode(registry.catalog())},output_dir=None,allow_operations=()) as host:
    status=serve(host,sys.stdin.buffer,sys.stdout.buffer)
    assert not dispatches,dispatches
    assert host.capabilities()['execution_budget']['used']==0
    assert not host.capabilities()['host_policy']['allowed_operations']
    assert host.capabilities()['output_enabled'] is False
raise SystemExit(status)
"""


def reference(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def snapshot(root):
    return {path.relative_to(root).as_posix(): reference(path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file()}


def anchored_workspace(directory, review):
    manifest_bytes = (directory / "thread.json").read_bytes()
    assert reference(manifest_bytes) == review["thread_ref"]
    manifest = json.loads(manifest_bytes)
    selected = next(item for item in manifest["files"] if item["path"] == "run/session/workspace.json")
    workspace = directory / selected["path"]
    raw = workspace.read_bytes()
    assert reference(raw) == selected["sha256"] and len(raw) == selected["bytes"]
    saved = json.loads(raw)
    assert {item["execution_id"] for item in saved["executions"]} == set(review["execution_ids"])
    assert {item["result_id"] for item in saved["results"]} == set(review["result_ids"])
    return workspace, raw, saved


def mcp_check(directory, review, output, label, commands):
    workspace, raw, saved = anchored_workspace(directory, review)
    checks = []
    for version in PROTOCOLS:
        calls = [(2, "net_capabilities", {}),
                 (3, "net_inspect", {"artifact": "workspace", "selector": ["executions"]}),
                 (4, "net_inspect", {"artifact": "workspace", "selector": ["results"]}),
                 (5, "net_execute", {"source": "workspace", "graph": "catalog", "attempt": "forbidden"}),
                 (6, "net_inspect", {"artifact": "C:/unselected-workspace.json"}),
                 (7, "net_inspect", {"artifact": "catalog"})]
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": version, "capabilities": {},
            "clientInfo": {"name": "ncp-installed-acceptance", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        messages.extend({"jsonrpc": "2.0", "id": number, "method": "tools/call",
                         "params": {"name": name, "arguments": arguments}}
                        for number, name, arguments in calls)
        wire = b"".join(encoded(message) + b"\n" for message in messages)
        command = [sys.executable, "-I", "-c", SERVER, str(workspace), reference(raw)]
        process = subprocess.run(command, input=wire, capture_output=True, timeout=90, cwd=output)
        stem = f"{label}-mcp-{version}"
        (output / f"{stem}.request.ndjson").write_bytes(wire)
        (output / f"{stem}.response.ndjson").write_bytes(process.stdout)
        (output / f"{stem}.stderr.txt").write_bytes(process.stderr)
        commands.append(dict(label=stem, command=command, exit_code=process.returncode))
        assert process.returncode == 0 and not process.stderr, (stem, process.stderr)
        responses = {response["id"]: response for response in map(json.loads, process.stdout.splitlines())}
        assert len(process.stdout.splitlines()) == 7 and set(responses) == set(range(1, 8))
        assert responses[1]["result"]["protocolVersion"] == version
        capabilities = responses[2]["result"]["structuredContent"]
        assert capabilities["host_policy"]["allowed_operations"] == []
        assert capabilities["output_enabled"] is False
        assert capabilities["execution_budget"]["used"] == 0
        for number, field, identifier in ((3, "executions", "execution_id"), (4, "results", "result_id")):
            result = responses[number]["result"]
            assert result["isError"] is False
            value = result["structuredContent"]
            assert encoded(value["data"]) == encoded({item[identifier]: item for item in saved[field]})
            assert set(value["data"]) == set(review[f"{identifier}s"])
            assert value["binding"]["sha256"] == reference(raw)
        denied = responses[5]["result"]
        assert denied["isError"] is True
        assert "This host is read-only; output capability was not granted" in json.dumps(denied)
        assert responses[6]["result"]["isError"] is True
        assert responses[7]["result"]["isError"] is False
        assert workspace.read_bytes() == raw
        checks.append(dict(case=label, protocol=version, workspace_ref=reference(raw),
                           thread_ref=review["thread_ref"], original_session_id=review["session_id"],
                           execution_ids=review["execution_ids"], result_ids=review["result_ids"],
                           dispatches=0, execution_denial="read-only host", exit_code=process.returncode))
    return checks


def installed_package():
    import ciw
    import_root = Path(ciw.__file__).resolve()
    assert import_root.is_relative_to(Path(sys.prefix).resolve()), "Run with the installed environment and -I"
    return import_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", required=True, type=Path)
    parser.add_argument("--summary-ref", required=True)
    parser.add_argument("--golden-journey", required=True, type=Path)
    parser.add_argument("--golden-ref", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    import_root = installed_package()
    producer = args.producer_root.resolve()
    summary_bytes = (producer / "summary.json").read_bytes()
    assert reference(summary_bytes) == args.summary_ref
    summary = json.loads(summary_bytes)
    assert summary["status"] == "PASS_SOFTWARE_SEAM_ONLY"
    assert set(summary["cases"]) == set(CASES)
    golden_bytes = args.golden_journey.read_bytes()
    assert reference(golden_bytes) == args.golden_ref
    golden = json.loads(golden_bytes)
    assert set(golden["reviews"]) == set(CASES)
    output = args.output_dir.resolve()
    assert not output.is_relative_to(producer) and not producer.is_relative_to(output)
    golden_root = args.golden_journey.resolve().parent
    assert not output.is_relative_to(golden_root) and not golden_root.is_relative_to(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "producer-summary.json").write_bytes(summary_bytes)
    (output / "golden-journey.json").write_bytes(golden_bytes)
    commands, reviews, fixtures, mcp_checks = [], {}, {}, []

    def run(label, arguments, expected=0, alias="ncp"):
        executable = Path(sys.executable).parent / (alias + (".exe" if os.name == "nt" else ""))
        command = ([str(executable)] if label.endswith(("-legacy", "-ncp-alias")) else
                   [sys.executable, "-I", "-m", "ciw.net"])
        command.extend(map(str, arguments))
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
        environment.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1")
        process = subprocess.run(command, cwd=output, env=environment, capture_output=True, timeout=90)
        (output / f"{label}.stdout.json").write_bytes(process.stdout)
        (output / f"{label}.stderr.txt").write_bytes(process.stderr)
        commands.append(dict(label=label, command=command, exit_code=process.returncode))
        assert process.returncode == expected, (label, process.returncode, process.stderr)
        if expected == 1:
            assert json.loads(process.stderr)["status"] == "REFUSED"
            return None
        return json.loads(process.stdout)

    for name, (expected_status, expected_mean) in CASES.items():
        original = producer / name / "analysis/study/request.json"
        raw = original.read_bytes()
        source_ref = reference(raw)
        assert source_ref == golden['fixtures'][name]['request_ref'], 'Producer request differs from selected golden bytes'
        assert reference((producer / name / 'capture/capture.json').read_bytes()) == golden['fixtures'][name]['capture_ref']
        selected = output / f"{name}-request.json"
        selected.write_bytes(raw)
        producer_before = snapshot(producer / name)
        view = run(f"{name}-source", ["production", "engineering", "source", selected,
                   "--profile", "scalar", "--request-ref", source_ref])
        assert encoded(view["request"]) == encoded(json.loads(raw))
        assert view["origins"] == ["synthetic"] and view["new_scientific_executions"] == 0
        assert view["authorizes_execution"] is False
        observations = view["capture_review"]["observations"]
        observed = b"".join(base64.b64decode(item["raw"]["bytes_b64"], validate=True) for item in observations)
        assert hashlib.sha256(observed).hexdigest() == summary["cases"][name]["raw_sha256"]
        if name == "insufficient-context":
            assert all(all(value is None for value in item["context"].values()) for item in observations)
        run(f"{name}-stale-source", ["production", "engineering", "source", selected,
            "--profile", "scalar", "--request-ref", "sha256:" + "0" * 64], 1)
        intent = output / f"{name}-intent.json"
        run(f"{name}-intent", ["production", "engineering", "example", "--profile", "scalar",
            "--request-ref", source_ref, "--output", intent])
        compiled = output / f"{name}-compiled"
        planned = run(f"{name}-compile", ["production", "engineering", "compile", "--profile", "scalar",
                      "--intent", intent, "--request", selected, "--output-dir", compiled])
        assert planned["authorizes_execution"] is False and planned["status"] == "COMPILED_NOT_AUTHORIZED"
        base = ["production", "engineering", "run", compiled, "--profile", "scalar", "--compiled-ref"]
        denied = output / f"{name}-denied"
        run(f"{name}-no-grant", [*base, planned["compiled_ref"], "--output-dir", denied], 1)
        assert not denied.exists()
        run(f"{name}-stale-compiled", [*base, "sha256:" + "0" * 64,
            "--allow-compute", "--output-dir", denied], 1)
        assert not denied.exists()
        executed = output / f"{name}-execution"
        review = run(f"{name}-run", [*base, planned["compiled_ref"], "--allow-compute", "--output-dir", executed], 2)
        assert review["status"] == expected_status and review["request_ref"] == source_ref
        assert review["scope"]["physical_qualification"] == "HELD"
        assert len(review["result_ids"]) == (0 if name == "interrupted" else 4)
        if name == "insufficient-context":
            checks = {item["check"]["check_id"]: item["status"] for item in review["checks"]}
            assert checks["independent-arithmetic"] == "PASS" and checks["nominal-limit"] == "FAIL"
        _, _, workspace = anchored_workspace(executed, review)
        if expected_mean is not None:
            results = {item["operation_id"]: item["data"] for item in workspace["results"]}
            decoded = next(value for value in results.values() if "origin" in value)
            affine = next(value for value in results.values() if "uncertainty_status" in value)
            model = results["link.mean-model.v1"]
            assert decoded["origin"] == "synthetic"
            assert affine["covariance"] is None and affine["uncertainty_status"] == "NOT_SUPPLIED"
            assert model["mean"] == expected_mean
            assert model["variance"] is None and model["standard_uncertainty"] is None
        saved = snapshot(executed)
        common = [executed, "--profile", "scalar", "--thread-ref", review["thread_ref"]]
        assert encoded(run(f"{name}-inspect", ["production", "engineering", "inspect", *common], 2)) == encoded(review)
        run(f"{name}-next", ["production", "engineering", "next", *common])
        run(f"{name}-legacy", ["production", "engineering", "inspect", *common], 2, "net")
        run(f"{name}-ncp-alias", ["production", "engineering", "inspect", *common], 2, "ncp")
        assert (output / f"{name}-legacy.stdout.json").read_bytes() == (output / f"{name}-inspect.stdout.json").read_bytes()
        assert (output / f"{name}-ncp-alias.stdout.json").read_bytes() == (output / f"{name}-inspect.stdout.json").read_bytes()
        recovered = output / f"{name}-restored"
        restored = run(f"{name}-restore", ["production", "engineering", "restore", *common, "--output-dir", recovered])
        assert restored["execution_ids"] == review["execution_ids"] and restored["result_ids"] == review["result_ids"]
        assert restored["new_scientific_executions"] == 0 and restored["active_authority_restored"] is False
        assert snapshot(recovered) == saved == snapshot(executed)
        reopened = run(f"{name}-reopened-source", ["production", "engineering", "source", recovered / "request.json",
                       "--profile", "scalar", "--request-ref", source_ref])
        assert encoded(reopened) == encoded(view)
        mcp_checks.extend(mcp_check(recovered, review, output, name, commands))
        assert snapshot(recovered) == saved == snapshot(executed)
        assert snapshot(producer / name) == producer_before
        old_review = golden["reviews"][name]
        old_directory = args.golden_journey.resolve().parent / f"{name}-execution"
        old_snapshot = snapshot(old_directory)
        anchored_workspace(old_directory, old_review)
        old_inspection = run(f"{name}-golden-inspect", ["production", "engineering", "inspect", old_directory,
                             "--profile", "scalar", "--thread-ref", old_review["thread_ref"]], 2)
        assert encoded(old_inspection) == encoded(old_review)
        old_restored = output / f"golden-{name}-restored"
        old_receipt = run(f"{name}-golden-restore", ["production", "engineering", "restore", old_directory,
                          "--profile", "scalar", "--thread-ref", old_review["thread_ref"], "--output-dir", old_restored])
        assert old_receipt["execution_ids"] == old_review["execution_ids"]
        assert old_receipt["result_ids"] == old_review["result_ids"]
        assert old_receipt["new_scientific_executions"] == 0 and old_receipt["active_authority_restored"] is False
        assert snapshot(old_restored) == old_snapshot == snapshot(old_directory)
        mcp_checks.extend(mcp_check(old_restored, old_review, output, f"golden-{name}", commands))
        assert snapshot(old_restored) == old_snapshot == snapshot(old_directory)
        reviews[name] = review
        fixtures[name] = dict(request_ref=source_ref, raw_ref=reference(observed), origin="synthetic",
                              producer_path=str(original), producer_files=producer_before,
                              capture_ref=reference((producer / name / "capture/capture.json").read_bytes()))
    report = dict(status="PASS_INSTALLED_SOFTWARE_ACCEPTANCE", python=sys.version, executable=sys.executable,
                  ciw_path=str(import_root), summary_ref=args.summary_ref, golden_ref=args.golden_ref,
                  commands=commands, reviews=reviews, fixtures=fixtures, mcp_checks=mcp_checks,
                  dependencies={name: importlib.metadata.version(name) for name in
                                ("numpy", "websockets", "cryptography", "pytest", "PyYAML")},
                  physical_qualification="HELD", evidence_admission="NOT_PERFORMED",
                  scope="retained synthetic producer bytes; no instrument or named-host activation")
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(dict(status=report["status"], commands=len(commands), mcp_processes=len(mcp_checks),
                          cases={name: review["status"] for name, review in reviews.items()})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
