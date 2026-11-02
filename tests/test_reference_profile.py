"""Real engine + real subprocess MCP client. Synthetic fixture only."""
import json, os, shutil, sys, tempfile, unittest
from pathlib import Path
import anyio
import ciw
import mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ciw.evidence_corpus import compile_evidence
from reference_fixture_helpers import make_catalog
from ciw.services import open_reference as open_host, invoke as shared_invoke, reference_tools

def invoke(host, operation, arguments):
    try:
        return {"status": "ok", "data": shared_invoke(operation, arguments, host=host)}
    except ValueError as exc:
        return {"status": "refused", "reason": str(exc)}
ROOT = Path(__file__).resolve().parents[1]

def sdk_field(value, camel, snake):
    return getattr(value, camel) if hasattr(value, camel) else getattr(value, snake)


class VerticalSlice(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.text = b'Synthetic reference: calibration needs an independent measured standard.\n'
        corpus, self.record, path = make_catalog(self.root/'input', self.text)
        self.catalog = self.root/'input/catalog-inputs/bundle'
        self.ref = corpus.ref
        selection=self.root/'selection.json'
        selection.write_text(json.dumps({'schema':'ciw.evidence-selection.v1',
            'disclosure_ref':'fixture:synthetic-local-test',
            'sources':[{'record_id':self.record,'path':str(path)}]}))
        self.evidence=self.root/'evidence'
        self.eref=compile_evidence(catalog=corpus,selection=selection,output=self.evidence)
        self.host=self.open()
    def tearDown(self): self.temp.cleanup()
    def open(self):
        return open_host(catalog_path=self.catalog,catalog_ref=self.ref,
                         evidence_path=self.evidence,evidence_ref=self.eref)
    def test_sdk_retrieves_exact_passage_and_provenance(self):
        result=invoke(self.host,'ncp_evidence_search',{'query':'calibration'})
        self.assertEqual(result['status'],'ok')
        span=invoke(self.host,'ncp_evidence_span',{'span_id':result['data']['items'][0]['span_id']})
        self.assertEqual(span['data']['span']['text'].encode(),self.text)
        self.assertIn('locator',span['data']['span'])
        self.assertEqual(invoke(self.host,'ncp_knowledge_resolve',{'source_id':'gdrive:Original'})['data']['record']['record_id'], self.record)
    def test_permissions_and_schema_are_fail_closed(self):
        for name,args in [('ncp_knowledge_search',{'query':'calibration','principal':'admin'}),
                          ('ncp_knowledge_search',{'query':'calibration','path':'/tmp'}),
                          ('ncp_evidence_search',{'query':'!!!'}),('ncp_work_authorize',{}),
                          ('ingest',{})]:
            self.assertEqual(invoke(self.host,name,args)['status'],'refused')
        self.host.corpus.ref='sha256:'+'f'*64
        self.assertEqual(invoke(self.host,'ncp_knowledge_overview',{})['reason'],'corpus_binding_changed')
    def test_metadata_only_does_not_disclose_passages(self):
        host=open_host(catalog_path=self.catalog,catalog_ref=self.ref)
        self.assertNotIn('ncp_evidence_span',{v['name'] for v in host.descriptions()})
        self.assertEqual(invoke(host,'ncp_evidence_search',{'query':'calibration'})['reason'],'unknown_tool')
        self.assertFalse(invoke(host,'ncp_system_status',{})['data']['source_execution'])
    def test_catalog_tamper_refused_on_reopen(self):
        # Pin validation is checked by the real upstream bundle loader.
        target=next(p for p in self.catalog.rglob('*') if p.is_file())
        target.write_bytes(target.read_bytes()+b'corrupt')
        with self.assertRaises(ValueError): self.open()
    def test_wrong_pin_refused(self):
        with self.assertRaises(ValueError):
            open_host(catalog_path=self.catalog,catalog_ref='sha256:'+'0'*64)
    def test_restart_and_portable_snapshot(self):
        expected=invoke(self.host,'ncp_evidence_search',{'query':'calibration'})
        self.assertEqual(expected,invoke(self.open(),'ncp_evidence_search',{'query':'calibration'}))
        new=self.root/'portable';new.mkdir()
        shutil.copytree(self.catalog,new/'catalog');shutil.copytree(self.evidence,new/'evidence')
        moved=open_host(catalog_path=new/'catalog',catalog_ref=self.ref,evidence_path=new/'evidence',evidence_ref=self.eref)
        self.assertEqual(expected,invoke(moved,'ncp_evidence_search',{'query':'calibration'}))
    def test_real_mcp_stdio_outside_checkout(self):
        expected=invoke(self.host,'ncp_evidence_search',{'query':'calibration'})
        env={**os.environ,'PYTHONPATH':str(Path(mcp.__file__).resolve().parents[1])+os.pathsep+str(Path(ciw.__file__).resolve().parents[1])}
        params=StdioServerParameters(command=sys.executable,args=['-m','ciw.net','reference','serve',
            '--catalog',str(self.catalog),'--catalog-ref',self.ref,
            '--evidence',str(self.evidence),'--evidence-ref',self.eref],env=env,cwd=str(self.root))
        async def run():
            with anyio.fail_after(20):
                async with stdio_client(params) as streams:
                    async with ClientSession(*streams) as session:
                        initialized=await session.initialize()
                        self.assertEqual(sdk_field(initialized,'serverInfo','server_info').name,'odrp-reference')
                        listed=await session.list_tools()
                        self.assertIn('ncp_evidence_span',{x.name for x in listed.tools})
                        found=await session.call_tool('ncp_evidence_search',{'query':'calibration'})
                        self.assertFalse(sdk_field(found,'isError','is_error'))
                        self.assertEqual(sdk_field(found,'structuredContent','structured_content'),expected)
                        sid=sdk_field(found,'structuredContent','structured_content')['data']['items'][0]['span_id']
                        read=await session.call_tool('ncp_evidence_span',{'span_id':sid})
                        self.assertEqual(sdk_field(read,'structuredContent','structured_content'),invoke(self.host,'ncp_evidence_span',{'span_id':sid}))
                        refused=await session.call_tool('ncp_work_authorize',{})
                        self.assertTrue(sdk_field(refused,'isError','is_error'))
                        resources=await session.list_resources()
                        self.assertGreaterEqual(len(resources.resources),2)
                        resource=await session.read_resource(resources.resources[0].uri)
                        self.assertTrue(resource.contents)
        anyio.run(run)
    def test_reference_to_existing_demo_retains_exact_citation(self):
        from unittest.mock import patch
        from ciw.retention import Store
        found=invoke(self.host,'ncp_evidence_search',{'query':'calibration'})
        citation=invoke(self.host,'ncp_evidence_span',{'span_id':found['data']['items'][0]['span_id']})['data']
        result=shared_invoke("ncp_demo")  # Existing named synthetic service; not a physical instrument.
        bundle={'schema':'odrp.synthetic-reference-journey.v1','citation':citation,
                'result':result,'citation_is_scientific_support':False,'authorizes_execution':False}
        with patch.dict(os.environ,{'NCP_RETAIN_DIR':str(self.root/'retained')}):
            store=Store('fixture:operator')
            artifact=store.keep(bundle,kind='synthetic-reference-journey',catalog={})
            reopened=Store('fixture:operator').native(artifact)
            self.assertEqual(reopened,bundle)
            self.assertEqual(reopened['citation']['span']['text'].encode(),self.text)
            with self.assertRaises(ValueError): Store('fixture:other').native(artifact)

    def test_shared_discovery_and_host_boundaries(self):
        names={x['name'] for x in reference_tools(self.host)}
        self.assertIn('ncp_knowledge_search',names)
        self.assertNotIn('ncp_work_authorize',names)
        with self.assertRaises(ValueError): shared_invoke('ncp_knowledge_search',{'query':'calibration'})
        with self.assertRaises(ValueError): reference_tools(object())
        with self.assertRaises(ValueError): shared_invoke('ncp_knowledge_unknown',{},host=self.host)

if __name__=='__main__': unittest.main(verbosity=2)
