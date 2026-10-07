"""Official MCP SDK adapter for a host-bound Board parameter refit journey.

The host owns authorization, durable attempts and candidate retention. This
adapter supplies bounded, schema-checked protocol messages; it cannot grant
execution, replace the baseline or accept a candidate.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
import json

from .control_contracts import json_tree


MAX_ARGUMENT_BYTES = 65536
MAX_RESPONSE_BYTES = 262144


_REF = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$", "minLength": 71, "maxLength": 71}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 512}
_NODES = {"type": "array", "items": _TEXT, "maxItems": 16, "uniqueItems": True}
_REVIEW = {"type": "string", "const": "unreviewed"}
_RESULT_REFS = {"type": "object", "maxProperties": 16,
                "additionalProperties": {"anyOf": [_REF, {"type": "null"}]}}


def _output(properties):
    # Required, typed stable fields; optional retained domain metadata may grow.
    return {"type": "object", "properties": properties, "required": list(properties)}


_CANDIDATE_OUTPUT = _output({
    **{key: _REF for key in ("candidate_ref", "preview_ref", "validation_ref", "baseline_ref",
                             "candidate_board_ref", "grant_ref", "run_ref", "delta_ref", "source_evidence_id")},
    "attempt_id": _TEXT, "status": {"enum": ["completed", "incomplete"]},
    "review_status": _REVIEW, "accepted": {"const": False},
    "rerun_nodes": _NODES, "reused_nodes": _NODES, "node_result_refs": _RESULT_REFS,
    "node_parameters": {"type": "object", "maxProperties": 16, "additionalProperties": {"type": "object"}},
    "comparison_summary": {"type": "object"},
})
OUTPUT_SCHEMAS = {
    "ncp_board_status": _output({
        **{key: _REF for key in ("board_ref", "source_ref", "source_evidence_id", "grant_ref")},
        "baseline_ref": {"anyOf": [_REF, {"type": "null"}]},
        "editable_parameters": {"type": "array", "items": {"type": "object"}, "maxItems": 32},
        "candidate_refs": {"type": "array", "items": _REF, "maxItems": 64, "uniqueItems": True},
        "attempts": {"type": "array", "maxItems": 65, "items": _output({
            "attempt_id": _TEXT, "kind": {"enum": ["baseline", "candidate"]},
            "status": {"enum": ["completed", "outcome_unknown"]},
            "outcome_ref": {"anyOf": [_REF, {"type": "null"}]}})},
        "budgets": {"type": "object"}, "claims": {"type": "object"},
    }),
    "ncp_board_preview": _output({
        **{key: _REF for key in ("preview_ref", "base_board_ref", "candidate_board_ref")},
        "target": _output({"node_id": _TEXT, "parameter": _TEXT}),
        "before": {}, "replacement": {}, "affected_nodes": _NODES, "unaffected_nodes": _NODES,
        "provider_execution": {"const": False},
    }),
    "ncp_board_validate": _output({
        **{key: _REF for key in ("validation_ref", "preview_ref", "board_ref", "source_ref", "grant_ref")},
        "valid": {"const": True}, "source_selection_checked": {"const": True},
        "execution_authority": {"const": False}, "provider_execution": {"const": False},
    }),
    "ncp_board_baseline": _output({
        "baseline_ref": _REF, "attempt_id": _TEXT, "status": {"const": "completed"},
        "source_evidence_id": _REF, "node_result_refs": _RESULT_REFS, "provider_execution": {"const": True},
    }),
    "ncp_board_recompute": _CANDIDATE_OUTPUT,
    "ncp_board_candidate": _CANDIDATE_OUTPUT,
    "ncp_board_compare": _output({
        "candidate_ref": _REF, "baseline_graph_run_ref": _REF, "needle_run_ref": _REF,
        "dependency_closure": _NODES, "review_status": _REVIEW, "accepted": {"const": False},
        "nodes": {"type": "array", "maxItems": 16, "items": _output({
            "node_id": _TEXT, "recomputation": {"enum": ["REUSED", "RERUN", "RERUN_UNRESOLVED"]},
            "baseline_result_ref": _REF, "candidate_result_ref": {"anyOf": [_REF, {"type": "null"}]},
            "data_changed": {"type": ["boolean", "null"]}})},
        "summary": {"type": "object"},
    }),
}


def _descriptions(host):
    return [{**description, **({"outputSchema": deepcopy(OUTPUT_SCHEMAS[description["name"]])}
                              if description["name"] in OUTPUT_SCHEMAS else {})}
            for description in host.descriptions()]


def _json(value, maximum):
    json_tree(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > maximum:
        raise ValueError("message_too_large")
    return raw


def _refused(reason):
    return {"status": "refused", "reason": reason}


def mcp_result(value):
    """Emit matching structured/text JSON, bounded even on a host failure."""
    if type(value) is not dict:
        value = _refused("invalid_host_result")
    try:
        raw = _json(value, MAX_RESPONSE_BYTES)
    except (ValueError, TypeError, RecursionError):
        value = _refused("response_too_large")
        raw = _json(value, MAX_RESPONSE_BYTES)
    return {"content": [{"type": "text", "text": raw}],
            "structuredContent": value, "isError": value.get("status") == "refused"}


def create_server(host):
    """Build the official SDK server without importing optional SDK at import."""
    from jsonschema import Draft202012Validator
    from .board_refit import BoardRefitRefusal, PUBLIC_REFUSALS
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.shared.exceptions import MCPError

    async def list_tools(context, params):
        try:
            # Ask the host again on every request, including an initialized
            # connection. Profile/grant state must never be cached by transport.
            descriptions = _descriptions(host)
            if params is not None and params.cursor is not None:
                raise MCPError(types.INVALID_PARAMS, "invalid_cursor")
            _json(descriptions, MAX_RESPONSE_BYTES)
            return types.ListToolsResult(tools=[types.Tool.model_validate(item)
                                               for item in descriptions])
        except MCPError:
            raise
        except BoardRefitRefusal as exc:
            code = -32000 if exc.code in ("authorization_refused", "scope_refused") else types.INTERNAL_ERROR
            reason = exc.code if exc.code in PUBLIC_REFUSALS else "operation_refused"
            raise MCPError(code, reason) from None
        except Exception:
            raise MCPError(types.INTERNAL_ERROR, "operation_refused") from None

    async def call_tool(context, params):
        try:
            descriptions = {item["name"]: item for item in _descriptions(host)}
            spec = descriptions.get(params.name)
            if spec is None:
                value = _refused("unknown_tool")
            else:
                arguments = params.arguments if params.arguments is not None else {}
                try:
                    _json(arguments, MAX_ARGUMENT_BYTES)
                    valid = Draft202012Validator(spec["inputSchema"]).is_valid(arguments)
                except (ValueError, TypeError, RecursionError):
                    valid = False
                if not valid:
                    value = _refused("invalid_arguments")
                else:
                    value = host.call(params.name, arguments)
                    # The adapter also checks advertised output contracts.
                    # Do not leak raw validation diagnostics or private paths.
                    if type(value) is not dict:
                        value = _refused("invalid_host_result")
                    elif (value.get("status") != "refused" and "outputSchema" in spec
                          and not Draft202012Validator(spec["outputSchema"]).is_valid(value)):
                        value = _refused("invalid_host_result")
        except BoardRefitRefusal as exc:
            value = _refused(exc.code if exc.code in PUBLIC_REFUSALS else "operation_refused")
        except PermissionError:
            value = _refused("authorization_refused")
        except (ValueError, TypeError, KeyError, RecursionError):
            value = _refused("invalid_arguments")
        except Exception:
            value = _refused("operation_refused")
        return types.CallToolResult.model_validate(mcp_result(value))

    return Server("ncp-board-refit", version="0.1.0", instructions=host.instructions,
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def serve_stdio(host):
    """Serve a launcher-bound host on stdin/stdout; emit no console banners."""
    from mcp.server.stdio import stdio_server

    host.descriptions()  # Fail closed before accepting protocol traffic.
    server = create_server(host)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


