"""Official SDK transport adapter; no second retrieval implementation.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import inspect
import json
from mcp import types
from mcp.server.lowlevel import Server
from .knowledge_mcp import KnowledgeRefusal

INSTRUCTIONS = ('Orphic Decentralized Reference Protocol (ODRP). '
    'This deployment is a local pinned reference node, not a proven replicated network. ')

def create_server(host):
    # Upstream SDK 2.x adapter uses callback constructor. Preserve that path.
    if 'on_list_tools' in inspect.signature(Server).parameters:
        from .knowledge_mcp import create_server as original
        server = original(host)
        server.name = 'odrp-reference'
        return server
    # SDK 1.29 transport compatibility: all application behavior delegates.
    from mcp.shared.exceptions import McpError
    server = Server('odrp-reference', version='0.1.0',
                    instructions=INSTRUCTIONS + host.instructions)
    @server.list_tools()
    async def list_tools():
        return [types.Tool.model_validate(v) for v in host.descriptions()]
    @server.call_tool()
    async def call_tool(name, arguments):
        value = host.call(name, arguments)
        return types.CallToolResult.model_validate(host.mcp_result(name, value))
    @server.list_resources()
    async def list_resources():
        return [types.Resource.model_validate(v) for v in host.resource_descriptions()]
    @server.list_resource_templates()
    async def list_templates():
        return [types.ResourceTemplate.model_validate(v) for v in host.resource_templates()]
    @server.read_resource()
    async def read_resource(uri):
        from mcp.server.lowlevel.helper_types import ReadResourceContents
        try:
            return [ReadResourceContents(content=host.read_resource(str(uri)),
                                         mime_type='application/json')]
        except Exception:
            raise McpError(types.ErrorData(code=types.INVALID_PARAMS,
                                          message='resource_unavailable')) from None
    return server

async def serve(host):
    from mcp.server.stdio import stdio_server
    host.descriptions()
    server = create_server(host)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
