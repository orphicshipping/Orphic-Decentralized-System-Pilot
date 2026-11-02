"""Optional official-SDK framing; existing owners retain all scientific state.

MCP 2026-07-28 discovery and legacy initialization are handled by the SDK.
"""
from __future__ import annotations
import asyncio
import json
from .agent_api import encode, MAX_RESPONSE


def create_server(host, *, name="ncp-control"):
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.shared.exceptions import MCPError

    def cursor(params):
        if params is not None and params.cursor is not None:
            raise MCPError(types.INVALID_PARAMS, "invalid_cursor")

    async def list_tools(context, params):
        cursor(params)
        from .agent_tools import descriptions
        rows = await anyio.to_thread.run_sync(getattr(host, "descriptions", descriptions))
        return types.ListToolsResult.model_validate({"tools": sorted(rows, key=lambda x: x["name"]),
            "ttlMs": 0, "cacheScope": "private"})

    async def call_tool(context, params):
        try:
            value = await anyio.to_thread.run_sync(lambda: host.call(params.name,
                params.arguments if params.arguments is not None else {}))
            if hasattr(host, "mcp_result"):
                result = host.mcp_result(params.name, value)
            else:
                result = {"content": [{"type": "text", "text": encode(value).decode()}],
                    "structuredContent": value,
                    "isError": value.get("status") in {"refused", "failed", "incomplete"}}
            if len(encode(result)) > MAX_RESPONSE * 3:
                raise ValueError("Response exceeds budget")
        except Exception:
            value = host.refusal() if hasattr(host, "refusal") else {"status": "refused", "reason": "request_refused"}
            result = {"content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value, "isError": True}
        return types.CallToolResult.model_validate(result)

    callbacks = {"on_list_tools": list_tools, "on_call_tool": call_tool}
    if hasattr(host, "read_resource"):
        async def resources(context, params):
            cursor(params)
            rows = host.list_resources() if hasattr(host, "list_resources") else []
            return types.ListResourcesResult.model_validate({"resources": rows, "ttlMs": 0, "cacheScope": "private"})

        async def templates(context, params):
            cursor(params)
            return types.ListResourceTemplatesResult.model_validate({"resourceTemplates": getattr(host, "resource_templates", [
                {"uriTemplate": "ncp://plans/{plan_id}", "name": "Retained plan", "mimeType": "application/json"},
                {"uriTemplate": "ncp://runs/{run_id}", "name": "Durable run", "mimeType": "application/json"},
                {"uriTemplate": "ncp://artifacts/{artifact_id}", "name": "Bounded artifact view", "mimeType": "application/json"}]),
                "ttlMs": 0, "cacheScope": "private"})

        async def read(context, params):
            try:
                value = await anyio.to_thread.run_sync(lambda: host.read_resource(str(params.uri)))
                raw = encode(value)
                if len(raw) > MAX_RESPONSE:
                    raise ValueError("Resource exceeds budget")
            except Exception:
                raise MCPError(types.INVALID_PARAMS, "resource_unavailable") from None
            return types.ReadResourceResult.model_validate({"contents": [{"uri": str(params.uri),
                "mimeType": "application/json", "text": raw.decode()}], "ttlMs": 0, "cacheScope": "private"})

        callbacks.update(on_list_resources=resources, on_list_resource_templates=templates, on_read_resource=read)
    from .agent_mcp import INSTRUCTIONS
    return Server(name, version="0.1.0", instructions=getattr(host, "instructions", INSTRUCTIONS), **callbacks)


def serve_sdk(host, *, name="ncp-control"):
    from mcp.server.stdio import stdio_server
    async def run():
        server = create_server(host, name=name)
        transport = stdio_server
        if getattr(host, 'strict_mcp_input', False):
            from .nise_stdio import strict_stdio_server
            transport = strict_stdio_server
        # SDK 2.3 reserves stdout and redirects Python/native diagnostics.
        async with transport() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    asyncio.run(run())
    return 0
