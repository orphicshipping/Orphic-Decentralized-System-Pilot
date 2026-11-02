import base64,io,json,os,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
import adapter
from reference_fixture_helpers import make_catalog

class Recovery(unittest.TestCase):
 def test_historical_archive_refused_without_deleting_state(self):
  with tempfile.TemporaryDirectory() as d,patch.object(adapter,'STATE',Path(d)/'state'):
   adapter.STATE.mkdir();(adapter.STATE/'sentinel').write_text('preserve')
   stream=io.BytesIO()
   with tarfile.open(fileobj=stream,mode='w:gz') as t:
    raw=b'{}';info=tarfile.TarInfo('operator.json');info.size=len(raw);t.addfile(info,io.BytesIO(raw))
   with self.assertRaisesRegex(ValueError,'Historical checkpoint'): adapter.restore(base64.b64encode(stream.getvalue()).decode())
   self.assertEqual((adapter.STATE/'sentinel').read_text(),'preserve')
 def test_bad_archive_preserves_state(self):
  with tempfile.TemporaryDirectory() as d,patch.object(adapter,'STATE',Path(d)/'state'):
   adapter.STATE.mkdir();(adapter.STATE/'sentinel').write_text('preserve')
   with self.assertRaises(Exception): adapter.restore('not-base64')
   self.assertEqual((adapter.STATE/'sentinel').read_text(),'preserve')

class ReferenceJourney(unittest.IsolatedAsyncioTestCase):
 async def test_reference_binding_is_host_only_and_survives_checkpoint(self):
  with tempfile.TemporaryDirectory() as d,patch.object(adapter,'STATE',Path(d)/'state'):
   root=Path(d);corpus,record,path=make_catalog(root/'input',b'Synthetic calibration reference.\n')
   env={'ODRP_CATALOG':str(root/'input/catalog-inputs/bundle'),'ODRP_CATALOG_REF':corpus.ref}
   with patch.dict(os.environ,env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter.app),base_url='http://container') as client:
     r=(await client.post('/transaction',json={'state':None,'action':'tools','payload':{}})).json()
     self.assertIn('ncp_knowledge_search',{x['name'] for x in r['result']['data']})
     r=(await client.post('/transaction',json={'state':r['state'],'action':'call','payload':{'name':'ncp_knowledge_search','arguments':{'query':'calibration'}}})).json()
     self.assertFalse(r['result']['data']['isError'])
     r=(await client.post('/transaction',json={'state':r['state'],'action':'call','payload':{'name':'ncp_knowledge_search','arguments':{'query':'calibration','path':'/other'}}})).json()
     self.assertEqual(r['result']['error']['code'],'reference_refused')
 async def test_no_binding_has_no_reference_disclosure(self):
  with tempfile.TemporaryDirectory() as d,patch.object(adapter,'STATE',Path(d)/'state'),patch.dict(os.environ,{},clear=True):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter.app),base_url='http://container') as client:
    r=(await client.post('/transaction',json={'state':None,'action':'call','payload':{'name':'ncp_knowledge_search','arguments':{'query':'calibration'}}})).json()
    self.assertEqual(r['result']['error']['code'],'reference_not_configured')
