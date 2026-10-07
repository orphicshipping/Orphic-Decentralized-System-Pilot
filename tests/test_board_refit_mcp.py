"""Protocol and real official-SDK transport checks for Board refit MCP."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ciw.board_refit_mcp import MAX_RESPONSE_BYTES, create_server, mcp_result


class BoundaryHost:
    """Deliberately small host to exercise transport, not domain simulation."""

    instructions = "Transport fixture only. No engineering or physical claims."

    def __init__(self):
        self.calls = []
        self.response = {"status": "ok", "data": {"retained": True}}
        self.failure = False
        self.list_failure = False
        self.refusal = None

    def descriptions(self):
        if self.list_failure:
            raise RuntimeError("private-token=do-not-return /private/path")
        return [{"name": "ncp_board_inspect", "description": "Read the bound Board.",
                 "inputSchema": {"type": "object", "properties": {
                     "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                     "required": [], "additionalProperties": False},
                 "outputSchema": {"type": "object", "properties": {
                     "status": {"enum": ["ok", "refused"]},
                     "data": {"type": "object"}, "reason": {"type": "string"}},
                     "required": ["status"], "additionalProperties": False},
                 "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                 "idempotentHint": True, "openWorldHint": False}}]

    def call(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if self.refusal is not None:
            from ciw.board_refit import BoardRefitRefusal
            raise BoardRefitRefusal(self.refusal)
        if self.failure:
            raise RuntimeError("private-token=do-not-return /private/path")
        return deepcopy(self.response)


def test_result_text_matches_structured_content_and_is_bounded():
    value = {"status": "ok", "data": {"name": "synthetic \u03bb"}}
    result = mcp_result(value)
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"] == value
    assert result["isError"] is False
    # Each string stays inside the shared JSON contract; total bytes exceed it.
    huge = {"status": "ok", "data": {"chunks": ["x" * 60000] * 5}}
    result = mcp_result(huge)
    assert len(result["content"][0]["text"].encode()) < MAX_RESPONSE_BYTES
    assert result["structuredContent"] == {"status": "refused", "reason": "response_too_large"}
    assert result["isError"] is True


def test_official_sdk_checks_shapes_sanitizes_failures_and_rechecks_discovery():
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    from mcp import ClientSession, types
    from mcp.shared.exceptions import MCPError
    from mcp.shared.memory import create_client_server_memory_streams

    host = BoundaryHost()
    server = create_server(host)

    async def exercise():
        with anyio.fail_after(20):
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as group:
                    group.start_soon(server.run, *server_streams, server.create_initialization_options())
                    async with ClientSession(*client_streams) as client:
                        initialized = await client.initialize()
                        assert initialized.server_info.name == "ncp-board-refit"
                        assert initialized.capabilities.tools is not None
                        assert initialized.capabilities.resources is None
                        names = {item.name for item in (await client.list_tools()).tools}
                        assert names == {"ncp_board_inspect"}
                        result = await client.call_tool("ncp_board_inspect", {"limit": 2})
                        assert not result.is_error
                        assert result.structured_content == host.response
                        assert json.loads(result.content[0].text) == result.structured_content
                        assert len(host.calls) == 1
                        for arguments in ({"principal": "admin"}, {"limit": True},
                                          {"limit": 0}, {"limit": 11}, {"limit": "2"}):
                            result = await client.call_tool("ncp_board_inspect", arguments)
                            assert result.is_error
                            assert result.structured_content == {
                                "status": "refused", "reason": "invalid_arguments"}
                        for forbidden in ("ncp_board_authorize", "ncp_board_accept", "run_shell"):
                            result = await client.call_tool(forbidden, {})
                            assert result.is_error
                            assert result.structured_content["reason"] == "unknown_tool"
                        assert len(host.calls) == 1
                        with pytest.raises(MCPError) as cursor:
                            await client.list_tools(params=types.PaginatedRequestParams(cursor="private-cursor"))
                        assert cursor.value.error.code == types.INVALID_PARAMS
                        assert cursor.value.error.message == "invalid_cursor"
                        host.failure = True
                        failed = await client.call_tool("ncp_board_inspect", {})
                        assert failed.structured_content == {
                            "status": "refused", "reason": "operation_refused"}
                        host.failure = False
                        host.refusal = "attempt_outcome_unknown"
                        unknown = await client.call_tool("ncp_board_inspect", {})
                        assert unknown.is_error
                        assert unknown.structured_content["reason"] == "attempt_outcome_unknown"
                        host.refusal = None
                        host.response = {"status": "ok", "unexpected": "private-path"}
                        invalid = await client.call_tool("ncp_board_inspect", {})
                        assert invalid.structured_content["reason"] == "invalid_host_result"
                        host.response = {"status": "ok", "data": {"chunks": ["x" * 60000] * 5}}
                        oversized = await client.call_tool("ncp_board_inspect", {})
                        assert oversized.structured_content["reason"] == "response_too_large"
                        host.list_failure = True
                        with pytest.raises(MCPError) as discovery:
                            await client.list_tools()
                        assert discovery.value.error.code == types.INTERNAL_ERROR
                        assert discovery.value.error.message == "operation_refused"
                        denied = await client.call_tool("ncp_board_inspect", {})
                        assert denied.structured_content["reason"] == "operation_refused"
                    group.cancel_scope.cancel()

    anyio.run(exercise)

async def exercise_board_refit_journey(profile_path):
    """Run the real SDK stdio journey; reusable for installed-wheel acceptance.

    The adjacent launcher-generated client configuration selects the interpreter
    and package. A receipt records actual negotiation and bounded tool results.
    This helper neither creates a grant nor accepts/promotes a candidate.
    """
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    profile_path = Path(profile_path)
    configured = json.loads((profile_path.parent / "mcp-config.json").read_text(encoding="utf-8"))
    params = StdioServerParameters(**configured["mcpServers"]["ncp-board-refit"])
    preview_arguments = json.loads((profile_path.parent / "example-preview.json").read_text(encoding="utf-8"))
    receipt = {"schema": "ciw.board-refit-sdk-journey-receipt.v1", "transport": "stdio",
               "protocol_versions": [], "calls": [], "process_sessions": 0}

    async def initialize(client):
        negotiated = await client.initialize()
        assert negotiated.server_info.name == "ncp-board-refit"
        assert negotiated.capabilities.tools is not None
        receipt["protocol_versions"].append(negotiated.protocol_version)
        receipt["process_sessions"] += 1
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert names == {"ncp_board_status", "ncp_board_preview", "ncp_board_validate",
                         "ncp_board_baseline", "ncp_board_recompute", "ncp_board_compare",
                         "ncp_board_candidate"}
        assert all(tool.input_schema.get("additionalProperties") is False for tool in listed.tools)
        receipt["tools"] = sorted(names)

    async def call(client, name, arguments, *, refused=False):
        result = await client.call_tool(name, arguments)
        data = result.structured_content
        assert isinstance(data, dict)
        assert bool(result.is_error) is refused
        assert json.loads(result.content[0].text) == data
        receipt["calls"].append({"tool": name, "arguments": arguments,
                                 "is_error": bool(result.is_error), "result": data})
        return data

    with anyio.fail_after(60):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as client:
                await initialize(client)
                before = await call(client, "ncp_board_status", {})
                assert before["baseline_ref"] is None
                assert before["candidate_refs"] == []
                assert preview_arguments["base_board_ref"] == before["board_ref"]
                stale = await call(client, "ncp_board_preview", {**preview_arguments,
                           "base_board_ref": "sha256:" + "0" * 64}, refused=True)
                assert stale["reason"] == "stale_board"
                out_of_scope = await call(client, "ncp_board_preview", {**preview_arguments,
                           "node_id": "spectrum"}, refused=True)
                assert out_of_scope["reason"] == "scope_refused"
                await call(client, "ncp_board_preview", {**preview_arguments,
                           "replacement": "not-a-channel"}, refused=True)
                await call(client, "ncp_board_preview", {**preview_arguments,
                           "principal": "admin"}, refused=True)
                preview = await call(client, "ncp_board_preview", preview_arguments)
                assert set(preview["affected_nodes"]) == {"statistics", "spectrum"}
                assert "energy_statistics" in preview["unaffected_nodes"]
                assert preview["before"] == "q" and preview["replacement"] == "v"
                assert preview["provider_execution"] is False
                validation = await call(client, "ncp_board_validate", {"preview_ref": preview["preview_ref"]})
                assert validation["valid"] is True
                assert validation["source_selection_checked"] is True
                assert validation["execution_authority"] is False
                planned = await call(client, "ncp_board_status", {})
                assert planned["baseline_ref"] is None
                assert planned["attempts"] == before["attempts"]
                baseline = await call(client, "ncp_board_baseline", {"attempt_id": "sdk-baseline"})
                assert baseline["status"] == "completed"
                assert set(baseline["node_result_refs"]) == {"statistics", "spectrum", "energy_statistics"}
                # Bind a validation to the now-retained baseline. This performs
                # no recomputation and survives the next fresh MCP process.
                validation = await call(client, "ncp_board_validate", {"preview_ref": preview["preview_ref"]})

        recompute_arguments = {"preview_ref": preview["preview_ref"],
                               "validation_ref": validation["validation_ref"],
                               "baseline_ref": baseline["baseline_ref"],
                               "attempt_id": "sdk-refit"}
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as client:
                await initialize(client)
                candidate = await call(client, "ncp_board_recompute", recompute_arguments)
                assert candidate["status"] == "completed"
                assert candidate["review_status"] == "unreviewed"
                assert candidate["accepted"] is False
                assert candidate["node_parameters"]["statistics"]["channel"] == "v"
                assert candidate["node_parameters"]["spectrum"]["channel"] == "q"
                assert candidate["node_parameters"]["energy_statistics"]["channel"] == "energy"
                assert set(candidate["rerun_nodes"]) == {"statistics", "spectrum"}
                assert candidate["reused_nodes"] == ["energy_statistics"]
                assert candidate["baseline_ref"] == baseline["baseline_ref"]
                assert candidate["node_result_refs"]["energy_statistics"] == baseline["node_result_refs"]["energy_statistics"]
                comparison = await call(client, "ncp_board_compare", {"candidate_ref": candidate["candidate_ref"]})
                compared_nodes = {row["node_id"]: row for row in comparison["nodes"]}
                assert compared_nodes["statistics"]["data_changed"] is True
                assert compared_nodes["spectrum"]["data_changed"] is False
                assert compared_nodes["spectrum"]["recomputation"] == "RERUN"
                assert compared_nodes["energy_statistics"]["recomputation"] == "REUSED"
                assert compared_nodes["energy_statistics"]["baseline_result_ref"] == compared_nodes["energy_statistics"]["candidate_result_ref"]
                retained = await call(client, "ncp_board_candidate", {"candidate_ref": candidate["candidate_ref"]})
                assert retained == candidate
                replayed = await call(client, "ncp_board_recompute", recompute_arguments)
                assert replayed == candidate
                await call(client, "ncp_board_accept", {"candidate_ref": candidate["candidate_ref"]}, refused=True)
                await call(client, "ncp_board_authorize", {}, refused=True)

        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as client:
                await initialize(client)
                after = await call(client, "ncp_board_status", {})
                assert after["board_ref"] == before["board_ref"]
                assert after["baseline_ref"] == baseline["baseline_ref"]
                assert after["candidate_refs"] == [candidate["candidate_ref"]]
                assert len(after["attempts"]) == 2
                assert after["budgets"]["candidate_runs_used"] == 1
                assert await call(client, "ncp_board_candidate", {"candidate_ref": candidate["candidate_ref"]}) == candidate
                assert await call(client, "ncp_board_baseline", {"attempt_id": "sdk-baseline"}) == baseline
                assert await call(client, "ncp_board_recompute", recompute_arguments) == candidate

    receipt.update({"baseline_ref": baseline["baseline_ref"], "candidate_ref": candidate["candidate_ref"],
                    "delta_ref": candidate["delta_ref"], "review_status": candidate["review_status"],
                    "accepted": candidate["accepted"], "baseline_unchanged": True,
                    "exact_retry_across_restart": True, "comparison": comparison})
    return receipt


def test_real_sdk_stdio_board_refit_journey_survives_reconnects(tmp_path, record_property):
    import os
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    import ciw
    # Launch the same distribution under test, including installed-wheel runs.
    source_root = Path(ciw.__file__).resolve().parents[1]
    output = tmp_path / "demo"
    completed = subprocess.run(
        [sys.executable, "-m", "ciw.board_refit_cli", "demo", "--output-dir", str(output)],
        env={**os.environ, "PYTHONPATH": str(source_root)}, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    configured = json.loads(completed.stdout)
    assert configured["providers_executed"] is False
    assert configured["synthetic"] is True
    baseline_board_bytes = (output / "board.json").read_bytes()
    receipt = anyio.run(exercise_board_refit_journey, output / "profile.json")
    assert (output / "board.json").read_bytes() == baseline_board_bytes
    assert receipt["process_sessions"] == 3
    assert len(set(receipt["protocol_versions"])) == 1
    assert receipt["protocol_versions"][0]
    record_property("negotiated_mcp_protocol", receipt["protocol_versions"][0])
    record_property("stdio_process_sessions", receipt["process_sessions"])
    (tmp_path / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")




def test_real_sdk_stdio_rechecks_grant_before_execution(tmp_path):
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from ciw.board_refit_cli import demo, open_host

    output = tmp_path / "revocable-demo"
    demo(output)
    config = json.loads((output / "mcp-config.json").read_text(encoding="utf-8"))
    params = StdioServerParameters(**config["mcpServers"]["ncp-board-refit"])
    grant_path = output / "grant.json"
    grant_bytes = grant_path.read_bytes()
    preview_arguments = json.loads((output / "example-preview.json").read_text(encoding="utf-8"))

    async def exercise():
        with anyio.fail_after(30):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    planned = await client.call_tool("ncp_board_preview", preview_arguments)
                    assert not planned.is_error
                    changed = json.loads(grant_bytes)
                    changed["grant_id"] = "private-host-identity-must-not-escape"
                    grant_path.write_text(json.dumps(changed), encoding="utf-8")
                    for name, arguments in (("ncp_board_baseline", {"attempt_id": "revoked-baseline"}),
                                            ("ncp_board_status", {})):
                        denied = await client.call_tool(name, arguments)
                        assert denied.is_error
                        assert denied.structured_content == {"status": "refused", "reason": "authorization_refused"}
                        assert "private-host-identity" not in denied.content[0].text
                        assert str(grant_path) not in denied.content[0].text

    try:
        anyio.run(exercise)
    finally:
        grant_path.write_bytes(grant_bytes)
    host = open_host(output / "profile.json")
    try:
        retained = host.call("ncp_board_status", {})
        assert retained["baseline_ref"] is None
        assert retained["attempts"] == []
        assert retained["candidate_refs"] == []
    finally:
        host.close()
