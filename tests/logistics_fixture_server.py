"""Separate official MCP server; its upstream is explicitly synthetic."""
import asyncio
from logistics_fixture_helpers import host
from ciw.logistics_mcp import serve

if __name__ == '__main__':
    asyncio.run(serve(host()))
