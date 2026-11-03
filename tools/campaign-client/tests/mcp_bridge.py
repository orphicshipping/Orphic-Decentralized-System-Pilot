"""Test-only official MCP bridge. Synthetic capture, no network/device operations."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'tests'))
from ciw.campaign_contracts import canonical, parse
from ciw.offline_intake import TOOLS, encode
from test_offline_intake import fixture, launch_text
from test_offline_intake_policy import policy_value
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    request = parse(sys.stdin.buffer.read(65537))
    assert set(request) == {'jsonrpc','id','method','params'} and request['method'] == 'tools/call'
    data = fixture()
    data['records'][1]['observation']['node_id'] = 'ns=2;s=<img src=x onerror="window.injected=true">'
    value = policy_value()
    value['principals'][0]['operations'] = [{'name': name, 'constraints':
        {'record_id':['opc-1']} if name == 'ncp_intake_record' else {}} for name in TOOLS]
    with tempfile.TemporaryDirectory() as root:
        policy_path = Path(root)/'policy.json'; policy_path.write_text(json.dumps(value))
        launcher = Path(root)/'host.py'
        extra = f"from ciw.principal_policy import PrincipalPolicy\nhost=PrincipalPolicy.from_file({str(policy_path)!r}).facade('owner-a',intake=host)\n"
        launcher.write_text(launch_text(encode(data)).replace('asyncio.run(serve_stdio(host))', extra+'asyncio.run(serve_stdio(host))'))
        async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B',str(launcher)])) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                result = await client.call_tool(request['params']['name'], request['params']['arguments'])
        response = {'jsonrpc':'2.0','id':request['id'],'result':result.model_dump(by_alias=True,exclude_none=True)}
        sys.stdout.buffer.write(canonical(response))


if __name__ == '__main__':
    asyncio.run(main())
