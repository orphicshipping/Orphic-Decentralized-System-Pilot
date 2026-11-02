import base64, hashlib, json, os, shutil, unittest
from tempfile import TemporaryDirectory
from ciw.logistics_profile import canonical_digest, selector, HTTPSReader, raw
from logistics_fixture_helpers import Fixture,host

@unittest.skipUnless(shutil.which('node'), 'OLM canonical digest requires a host-selected Node executable')
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.old=os.environ.get('NCP_RETAIN_DIR'); os.environ['NCP_RETAIN_DIR']=self.tmp.name
    def tearDown(self):
        self.tmp.cleanup()
        if self.old is None: os.environ.pop('NCP_RETAIN_DIR',None)
        else: os.environ['NCP_RETAIN_DIR']=self.old
    def test_catalog_projection_all_versions(self):
        h=host(); value=h.invoke('ncp_logistics_describe',{'capability_id':'a'})
        self.assertEqual(2,len(value['bundle']['projection']['definitions']))
        self.assertTrue(value['bundle']['whole_rpc_digest_verified']); self.assertTrue(value['synthetic'])
    def test_restart_and_owner_isolation(self):
        a=host().invoke('ncp_logistics_catalog',{})['artifact_id']
        self.assertEqual(a,host().invoke('ncp_logistics_retained_read',{'artifact_id':a})['artifact_id'])
        with self.assertRaises(ValueError): host(principal='other').invoke('ncp_logistics_retained_read',{'artifact_id':a})
    def test_refusals(self):
        for name,args in [('compute_submit',{}),('ncp_logistics_catalog',{'tenant':'bad'}),('ncp_logistics_inspect',{'source':{'kind':'package','mode':'delivery'}}),('ncp_logistics_job',{'job_id':'a','include':'submit'})]:
            f=Fixture(); h=host(f)
            with self.assertRaises(ValueError):h.invoke(name,args)
            self.assertEqual([],f.calls)
    def test_upstream_error_digest_and_http(self):
        for f in (Fixture(error=True),Fixture(mismatch=True)):
            with self.assertRaises(ValueError):host(f).invoke('ncp_logistics_catalog',{})
        def bad(b):
            status,headers,body=Fixture()(b);return 403,headers,body
        with self.assertRaises(ValueError):host(bad).invoke('ncp_logistics_catalog',{})
    def test_retained_contract_pin_refusal(self):
        h=host()
        artifact=h.store.keep({'profile':'olm.logistics.v1','adapter_contract_commit':'wrong'},kind='olm-retained-read',catalog={})
        with self.assertRaisesRegex(ValueError,'pin_mismatch'):h.invoke('ncp_logistics_retained_read',{'artifact_id':artifact})

    def test_complete_paging(self):
        h=host(Fixture(payload={'blob':'z'*300000})); v=h.invoke('ncp_logistics_catalog',{})
        self.assertNotIn('bundle',v); chunks=[]; offset=0
        while offset is not None:
            page=h.invoke('ncp_logistics_retained_read',{'artifact_id':v['artifact_id'],'offset':offset})
            self.assertLess(len(raw(page)),262144);chunks.append(base64.b64decode(page['chunk_b64']));offset=page['next_offset']
        self.assertEqual('z'*300000,json.loads(b''.join(chunks))['whole_rpc']['result']['structuredContent']['blob'])
    def test_capacity_refusal(self):
        with self.assertRaisesRegex(ValueError,'retention_capacity'):host(Fixture(payload={'blob':'z'*3900000})).invoke('ncp_logistics_catalog',{})
    def test_canonical_utf16_and_numbers(self):
        body=b'{"z":-0.0,"n":1e-7,"\\ue000":1,"\\ud800\\udc00":2}'
        expected='{"n":1e-7,"z":0,"\U00010000":2,"\ue000":1}'.encode()
        self.assertEqual(hashlib.sha256(expected).hexdigest(),canonical_digest(body,shutil.which('node')))
    def test_malformed_oversized_missing_receipt_and_wrong_id(self):
        for mode in ('malformed','oversized','receipt','id','protocol'):
            def bad(request):
                status,headers,body=Fixture()(request)
                if mode=='malformed':body=b'not json'
                if mode=='oversized':body=b'x'*(6*1024*1024+1)
                if mode=='receipt':headers.pop('x-source-read-receipt-hash')
                if mode=='protocol':headers['mcp-protocol-version']='wrong'
                if mode=='id':
                    value=json.loads(body);value['id']='wrong';body=raw(value)
                    headers['x-response-canonical-digest']=canonical_digest(body,shutil.which('node'))
                return status,headers,body
            self.assertEqual('refused',host(bad).call('ncp_logistics_catalog',{})['status'])

    def test_selectors_and_endpoint(self):
        cases=[{'kind':'workflow','mode':'plan','planId':'p'},{'kind':'workflow','mode':'run','planId':'p','runId':'r'},{'kind':'acquisition','planId':'p'},{'kind':'compute','jobId':'j','include':'status'},{'kind':'observation','observationId':'o','observationDigest':'sha256:'+'a'*64},{'kind':'catalog'},{'kind':'state','entityId':'e','validAt':'a','knownAt':'b'},{'kind':'twin','projectionId':'p'},{'kind':'package','mode':'manifest','packageId':'p','version':'1'}]
        for case in cases:self.assertEqual(case,selector(case))
        with self.assertRaises(ValueError):HTTPSReader('http://localhost/api/pilot/mcp','x','t')
if __name__=='__main__':unittest.main()
