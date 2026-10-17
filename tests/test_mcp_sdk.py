"""Live SDK transport regressions for existing and new owners."""
import asyncio
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import sys

import pytest
import ciw

try:
    SDK = version("mcp")
except PackageNotFoundError:
    SDK = None
pytestmark = pytest.mark.skipif(SDK != "2.3.0", reason="requires optional pinned MCP SDK 2.3.0")


@pytest.mark.parametrize("surface", ["agent", "semantic", "control"])
@pytest.mark.parametrize("modern", [True, False])
def test_live_modern_and_legacy_clients(tmp_path, surface, modern):
    from ciw.agent_mcp import demo_config
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError
    import jsonschema

    args = ["-m", "ciw.net", surface, "serve"]
    if surface != "control": args += ["--sdk"]
    if surface != "semantic":
        args += ["--profile", str(demo_config(tmp_path / "profile"))]
    env = dict(os.environ, PYTHONPATH=str(Path(ciw.__file__).parent.parent))

    async def run():
        params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=str(tmp_path))
        with (tmp_path / "stderr.log").open("w", encoding="utf-8") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=30) as client:
                    await (client.discover() if modern else client.initialize())
                    assert client.protocol_version == ("2026-07-28" if modern else "2025-11-25")
                    listing = await client.list_tools()
                    names = [t.name for t in listing.tools]
                    assert names == sorted(names)
                    if modern:
                        assert listing.ttl_ms == 0 and listing.cache_scope == "private"
                    name = {"agent": "net_capabilities", "semantic": "net_semantic_catalog",
                            "control": "ncp_capabilities_list"}[surface]
                    assert name in names
                    result = await client.call_tool(name, {})
                    assert not result.is_error
                    assert json.loads(result.content[0].text) == result.structured_content
                    schema = next(t.output_schema for t in listing.tools if t.name == name)
                    if schema is not None: jsonschema.validate(result.structured_content, schema)
                    refused = await client.call_tool("arbitrary_shell", {"command": "anything"})
                    assert refused.is_error
                    if surface == "control":
                        templates = await client.list_resource_templates()
                        assert len(templates.resource_templates) == 3
                        with pytest.raises(MCPError): await client.read_resource("file:///secret")

    asyncio.run(run())
