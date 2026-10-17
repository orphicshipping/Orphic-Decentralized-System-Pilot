"""Installed mixed NCP/corpus inspection acceptance; no scientific execution."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys


PROTOCOLS = ("2025-11-25", "2025-06-18")
TOOLS = {"net_capabilities", "net_inspect", "net_source_context", "net_check_edit",
         "net_observe", "ncp_corpus_overview", "ncp_corpus_search", "ncp_corpus_source",
         "ncp_corpus_span", "ncp_corpus_reported_field", "ncp_corpus_relations"}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def reference(raw):
    return "sha256:" + sha256(raw).hexdigest()


def save(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def snapshot(root):
    return {p.relative_to(root).as_posix(): reference(p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()}


def installed_package():
    import ciw
    location = Path(ciw.__file__).resolve()
    assert sys.flags.isolated, "Run this check with the installed Python and -I"
    assert location.is_relative_to(Path(sys.prefix).resolve()), "Editable/source import refused"
    return location


def rpc(number, method, params):
    value = {"jsonrpc": "2.0", "method": method, "params": params}
    if number is not None:
        value["id"] = number
    return value


def check_protocol(version, profile, bundle, corpus_ref, output, workspace_raw, corpus, catalog):
    saved = json.loads(workspace_raw)
    requests = [rpc(1, "initialize", {"protocolVersion": version, "capabilities": {},
        "clientInfo": {"name": "ncp-installed-corpus-acceptance", "version": "1"}}),
        rpc(None, "notifications/initialized", {}), rpc(2, "tools/list", {})]
    calls = []

    def call(label, name, arguments):
        number = len(calls) + 3
        requests.append(rpc(number, "tools/call", {"name": name, "arguments": arguments}))
        calls.append((label, number))
        return label

    call("before", "net_capabilities", {})
    call("executions", "net_inspect", {"artifact": "workspace", "selector": ["executions"]})
    call("results", "net_inspect", {"artifact": "workspace", "selector": ["results"]})
    call("catalog", "net_inspect", {"artifact": "catalog"})
    call("overview", "ncp_corpus_overview", {})
    # An exact selection identifier is a valid bounded lexical query for any bundle.
    records = {row["selection_id"]: row for row in
               map(json.loads, (bundle / "source_manifest.jsonl").read_bytes().splitlines())}
    first_sid = sorted(records)[0]
    call("search", "ncp_corpus_search", {"query": first_sid, "limit": 5})
    source_checks, span_checks, field_checks = [], [], []
    for sid in sorted(records):
        offset = 0
        while True:
            page = corpus.source(sid, offset, 2)
            label = call(f"source-{sid}-{offset}", "ncp_corpus_source",
                         {"selection_id": sid, "offset": offset, "limit": 2})
            source_checks.append((label, sid, page))
            for item in page["spans"]:
                label = call("span-" + item["span_id"], "ncp_corpus_span", {"span_id": item["span_id"]})
                span_checks.append((label, sid, item))
            if page["next_offset"] is None:
                break
            assert page["next_offset"] > offset
            offset = page["next_offset"]
        call("relations-" + sid, "ncp_corpus_relations", {"selection_id": sid})
    # Select bounded original JSON fields; compare types as well as values.
    for sid, member in sorted(catalog["exposed_internal_text"].items()):
        if member.endswith(".json"):
            value = json.loads((bundle / member).read_bytes())
            choices = [("/" + k.replace("~", "~0").replace("/", "~1"), v)
                       for k, v in value.items()] if type(value) is dict else [("", value)]
            choices = [(pointer, value) for pointer, value in choices if len(encoded(value)) < 32000]
            assert choices, "JSON source has no bounded top-level field"
            for index, (pointer, value) in enumerate(choices[:8]):
                label = call(f"field-{sid}-{index}", "ncp_corpus_reported_field",
                             {"selection_id": sid, "pointer": pointer})
                field_checks.append((label, sid, pointer, value))
    denied = [
        ("deny-execute", "net_execute", {"source": "workspace", "graph": "catalog", "attempt": "forbidden"}),
        ("deny-extract", "net_extract", {"artifact": "workspace", "selector": []}),
        ("deny-candidate", "net_candidate", {"baseline": "catalog", "changes": {}}),
        ("deny-native-selection", "net_inspect", {"artifact": first_sid}),
        ("deny-corpus-native", "ncp_corpus_source", {"selection_id": "workspace"}),
        ("deny-native-path", "net_inspect", {"artifact": str(profile)}),
    ]
    for label, name, arguments in denied:
        call(label, name, arguments)
    call("after", "net_capabilities", {})
    assert len(requests) < 4096, "Selected corpus exceeds this bounded acceptance connection"
    wire = b"".join(encoded(item) + b"\n" for item in requests)
    command = [sys.executable, "-I", "-m", "ciw.net", "agent", "serve", "--profile", str(profile),
               "--corpus-bundle", str(bundle), "--corpus-ref", corpus_ref]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    stem = "mixed-mcp-" + version
    save(output / (stem + ".request.ndjson"), wire)
    process = subprocess.run(command, input=wire, capture_output=True, timeout=120,
                             cwd=output, env=environment)
    save(output / (stem + ".response.ndjson"), process.stdout)
    save(output / (stem + ".stderr.txt"), process.stderr)
    assert process.returncode == 0, "Actual MCP CLI exited unsuccessfully; inspect retained stderr"
    assert process.stderr == b"", "Actual MCP CLI wrote stderr; inspect retained stderr"
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    expected_ids = [item["id"] for item in requests if "id" in item]
    assert [item.get("id") for item in replies] == expected_ids, "Missing, extra or misordered MCP responses"
    by_id = {item["id"]: item for item in replies}
    assert all(item.get("jsonrpc") == "2.0" and "error" not in item for item in replies)
    assert by_id[1]["result"]["protocolVersion"] == version
    assert "untrusted" in by_id[1]["result"]["instructions"]
    tools = by_id[2]["result"]["tools"]
    assert len(tools) == 11 and {item["name"] for item in tools} == TOOLS
    assert all(item["annotations"]["readOnlyHint"] is True for item in tools)
    labels = dict(calls)

    def result(label, *, refused=False):
        response = by_id[labels[label]]["result"]
        assert response["isError"] is refused, "Unexpected MCP result for " + label
        value = response["structuredContent"]
        assert json.loads(response["content"][0]["text"]) == value
        if refused:
            assert value["status"] == "refused"
        return value

    capabilities = result("before")
    assert encoded(result("after")) == encoded(capabilities)
    assert capabilities["output_enabled"] is False
    assert capabilities["host_policy"]["allowed_operations"] == []
    assert capabilities["execution_budget"]["used"] == 0
    assert capabilities["retained_artifacts"] == [] and capabilities["attempts"] == []
    assert not any(capabilities[name] for name in ("candidate_domains", "comparison_policies", "check_suites"))
    assert all(item["bound"] is False and item["agent_execution_enabled"] is False
               for item in capabilities["catalog"]["operations"].values())
    for field, identity in (("executions", "execution_id"), ("results", "result_id")):
        value = result(field)
        expected = {row[identity]: row for row in saved[field]}
        assert encoded(value["data"]) == encoded(expected), "Original workspace occurrence or data changed"
        assert value["binding"]["sha256"] == reference(workspace_raw)
        assert value["source_text_is_untrusted_data"] is True
    view = result("catalog")["data"]
    assert view["authorizes_execution"] is False
    assert all(item["bound"] is False for item in view["catalog"]["operations"].values())
    assert encoded(result("overview")) == encoded(corpus.overview())
    search = result("search")
    assert search["hits"] and search["score_is_probability"] is False
    assert encoded(search) == encoded(corpus.search(first_sid, 5))
    for label, sid, expected in source_checks:
        assert encoded(result(label)) == encoded(expected)
    for sid in sorted(records):
        assert encoded(result("relations-" + sid)) == encoded(corpus.relations(sid))
    recovered = {sid: bytearray() for sid in catalog["exposed_internal_text"]}
    for label, sid, descriptor in span_checks:
        value = result(label)
        assert value["authorizes_execution"] is False and value["semantic_support_checked"] is False
        span = value["span"]
        assert span["selection_id"] == sid and span["span_id"] == descriptor["span_id"]
        assert encoded(span["locator"]) == encoded(descriptor["locator"])
        raw = span["text"].encode("utf-8")
        assert span["locator"]["byte_start"] == len(recovered[sid])
        assert span["locator"]["byte_end_exclusive"] == len(recovered[sid]) + len(raw)
        assert sha256(raw).hexdigest() == span["locator"]["span_sha256"]
        recovered[sid].extend(raw)
    reconstruction = []
    for sid, member in sorted(catalog["exposed_internal_text"].items()):
        raw = bytes(recovered[sid])
        assert raw == (bundle / member).read_bytes()
        assert sha256(raw).hexdigest() == records[sid]["snapshot"]["sha256"]
        reconstruction.append({"selection_id": sid, "bytes": len(raw), "sha256": reference(raw), "exact_match": True})
    for label, sid, pointer, expected in field_checks:
        value = result(label)
        assert encoded(value["value"]) == encoded(expected)
        assert value["value_type"] == type(expected).__name__
        assert value["json_pointer"] == pointer and value["selection_id"] == sid
        assert value["value_role"] == "verbatim_parsed_retained_report_data_not_current_state"
    for label, _, _ in denied:
        result(label, refused=True)
    return {"protocol": version, "command": command, "exit_code": process.returncode,
            "request_count": len(requests), "response_count": len(replies), "advertised_tools": len(tools),
            "capabilities_unchanged": True, "execution_budget_used": 0, "output_enabled": False,
            "native_execution_ids": [r["execution_id"] for r in saved["executions"]],
            "native_result_ids": [r["result_id"] for r in saved["results"]],
            "source_pages": len(source_checks), "source_spans": len(span_checks),
            "reported_fields": len(field_checks), "source_reconstruction": reconstruction,
            "refusals": [label for label, _, _ in denied]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--workspace-ref", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    import_root = installed_package()
    from ciw.control_plane import builtin_registry
    from ciw.corpus_retrieval import Corpus
    bundle, workspace, output = args.bundle.resolve(strict=True), args.workspace.resolve(strict=True), args.output_dir.resolve()
    assert not output.is_relative_to(bundle) and not bundle.is_relative_to(output)
    assert not output.is_relative_to(workspace.parent) and not workspace.is_relative_to(output)
    corpus = Corpus.open(bundle, expected_ref=args.expected_ref)
    catalog = json.loads((bundle / "catalog.json").read_bytes())
    workspace_raw = workspace.read_bytes()
    assert reference(workspace_raw) == args.workspace_ref, "Workspace pin mismatch"
    before = {"bundle": snapshot(bundle), "workspace": reference(workspace_raw)}
    output.mkdir(parents=False, exist_ok=False)
    catalog_raw = encoded(builtin_registry(bind=False).catalog())
    save(output / "catalog.json", catalog_raw)
    profile = {"schema": "ciw.agent-host-profile.v1", "inputs": {"workspace": str(workspace),
        "catalog": "catalog.json"}, "output_dir": None, "allow_operations": [],
        "candidate_domains": {}, "comparisons": {}, "check_suites": {}, "max_executions": 1, "max_nodes": 1}
    profile_path = output / "profile.json"
    profile_raw = encoded(profile)
    save(profile_path, profile_raw)
    reports = [check_protocol(version, profile_path, bundle, args.expected_ref, output,
                              workspace_raw, corpus, catalog) for version in PROTOCOLS]
    after = {"bundle": snapshot(bundle), "workspace": reference(workspace.read_bytes())}
    assert before == after, "Retained input changed"
    assert profile_path.read_bytes() == profile_raw and (output / "catalog.json").read_bytes() == catalog_raw
    assert not any(name in p.name for p in output.iterdir() for name in ("execution-", "result-", "agent-output"))
    report = {"status": "PASS_SOFTWARE_ONLY", "checked_at": datetime.now(timezone.utc).isoformat(),
        "executable": sys.executable, "ciw_path": str(import_root), "corpus_ref": args.expected_ref,
        "workspace_ref": args.workspace_ref, "profile_ref": reference(profile_raw),
        "input_hashes_before": before, "input_hashes_after": after, "protocol_checks": reports,
        "scope": "installed actual mixed MCP CLI inspection, original occurrence and byte retention, read-only refusals",
        "scientific_execution_performed": False, "scientific_admission": False,
        "physical_qualification": "NOT_ESTABLISHED", "named_host_activation": False}
    save(output / "report.json", json.dumps(report, indent=2, allow_nan=False).encode("utf-8") + b"\n")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json"),
                      "protocols": len(reports), "source_text_printed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
