"""Official MCP transport, delegating every operation to LogisticsHost."""
import inspect
from mcp import types
from mcp.server.lowlevel import Server

def create_server(host):
    if 'on_list_tools' in inspect.signature(Server).parameters:
        async def listing(ctx, params):
            if params.cursor is not None: raise ValueError('cursor_not_supported')
            return types.ListToolsResult(tools=[types.Tool.model_validate(x) for x in host.descriptions()])
        async def calling(ctx, params):
            value=host.call(params.name,params.arguments or {})
            return types.CallToolResult.model_validate(host.mcp_result(params.name,value))
        return Server('odsp-logistics-read',version='0.1.0',instructions=host.instructions,on_list_tools=listing,on_call_tool=calling)
    server=Server('odsp-logistics-read',version='0.1.0',instructions=host.instructions)
    @server.list_tools()
    async def listing(): return [types.Tool.model_validate(x) for x in host.descriptions()]
    @server.call_tool()
    async def calling(name,arguments): return types.CallToolResult.model_validate(host.mcp_result(name,host.call(name,arguments or {})))
    return server

async def serve(host):
    from mcp.server.stdio import stdio_server
    server=create_server(host)
    async with stdio_server() as (reader,writer):
        await server.run(reader,writer,server.create_initialization_options())
