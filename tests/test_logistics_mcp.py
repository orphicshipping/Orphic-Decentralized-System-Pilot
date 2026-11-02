"""Real official MCP client + separate server process; synthetic upstream only."""
import asyncio,json,os,shutil,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from logistics_fixture_helpers import host
from ciw.services import invoke

@unittest.skipUnless(shutil.which('node'), 'OLM canonical digest requires a host-selected Node executable')
class MCPTests(unittest.TestCase):
    def test_client_sdk_restart_and_refusal(self):
        from importlib.metadata import PackageNotFoundError, version
        try:
            version('mcp')
        except PackageNotFoundError:
            self.skipTest('Optional official MCP transport dependency is not installed')
        try:
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ModuleNotFoundError as exc:
            if exc.name not in {'mcp','anyio'}:
                raise
            self.skipTest('Optional official MCP transport dependencies are not installed')
        async def run():
            with TemporaryDirectory() as root:
                old=os.environ.get('NCP_RETAIN_DIR');os.environ['NCP_RETAIN_DIR']=root
                try:
                    sdk=invoke('ncp_logistics_catalog',{},host=host())
                    params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).with_name('logistics_fixture_server.py'))],env=dict(os.environ))
                    async with stdio_client(params) as (reader,writer):
                        async with ClientSession(reader,writer) as client:
                            await client.initialize()
                            listed=await client.list_tools(); self.assertEqual(5,len(listed.tools))
                            result=await client.call_tool('ncp_logistics_catalog',{})
                            value=result.model_dump(by_alias=True)['structuredContent']
                            self.assertFalse(result.model_dump(by_alias=True)['isError'])
                            self.assertEqual(sdk['bundle']['whole_rpc']['result']['structuredContent'],value['data']['bundle']['whole_rpc']['result']['structuredContent'])
                            bad=await client.call_tool('ncp_logistics_catalog',{'principal':'evil'})
                            self.assertTrue(bad.model_dump(by_alias=True)['isError'])
                            artifact=value['data']['artifact_id']
                    async with stdio_client(params) as (reader,writer):
                        async with ClientSession(reader,writer) as client:
                            await client.initialize()
                            page=await client.call_tool('ncp_logistics_retained_read',{'artifact_id':artifact})
                            self.assertFalse(page.model_dump(by_alias=True)['isError'])
                            self.assertEqual(artifact,page.model_dump(by_alias=True)['structuredContent']['data']['artifact_id'])
                finally:
                    if old is None:os.environ.pop('NCP_RETAIN_DIR',None)
                    else:os.environ['NCP_RETAIN_DIR']=old
        asyncio.run(run())
