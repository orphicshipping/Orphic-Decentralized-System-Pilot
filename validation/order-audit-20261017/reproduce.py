from pathlib import Path
import tempfile,json,copy
from ciw.agent_api import encode
from ciw.link_store import Journal
from ciw.operational_collection import Collector,link_segments
from ciw.link_profiles import example_manifest,example_observations
from ciw.link_protocol import encode as wire
from ciw.operational_cli import example,prepare,default_context,configuration
from ciw.operational_agent import OperationalHost
from ciw.reference_comparison import example_request

out={}
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp)
 device=example_manifest();obs=example_observations(device,rounds=1)
 stream=wire('manifest',encode(device))+wire('observation',encode(obs[0]))
 with Journal(root/'link.sqlite',create=True) as journal:
  try:list(link_segments([stream[:10],stream[10:]],journal,expected_manifest=device,boot_id='synthetic.boot.1'))
  except Exception as exc:out['fragmented_link_receive']={'status':'FAIL','exception':type(exc).__name__,'reason':str(exc),'input':'one valid manifest+observation stream split after byte 10'}
 with Journal(root/'collector.sqlite',create=True) as journal:
  collector=Collector(journal,'stop-race',max_segments=3,cadence_s=0)
  original=collector._save;injected=False
  def interleaving(value):
   global injected
   if not injected and value.get('status')=='RUNNING' and value.get('segments') and value.get('in_flight') is None:
    injected=True
    current=journal.state(collector.key)
    journal.compare_and_set_state(collector.key,current['revision'],dict(current['value'],stop_requested=True))
   return original(value)
  collector._save=interleaving
  result=collector.run(lambda i:b'x',source_identity={'adapter':'inert_test'})
  out['collector_stop_interleaving']={'status':'FAIL' if len(result['segments'])>1 else 'PASS','segments_after_stop':len(result['segments']),'final_status':result['status'],'stop_requested':result['stop_requested']}
 p=example(root/'host')
 with OperationalHost(p) as host:
  d=host.call('oe_catalog',{})
  out['runtime_profile_grants']=d['grants']
 # Same native-observation content supplied under different origins is accepted unchanged.
 request=example_request();raw=encode(request['right'])
 from ciw.operational_context import make_binding,rows
 for origin in ['synthetic','recorded']:
  b=make_binding(raw,'observations',default_context(clock='synthetic.clock',origin=origin),[])
  out['origin_'+origin]={'binding_origin':b['context']['origin'],'row_origin':rows(raw,b)[0]['origin'],'native_semantics':request['right']['observations'][0]['provenance']['semantics']}
print(json.dumps(out,indent=2))
