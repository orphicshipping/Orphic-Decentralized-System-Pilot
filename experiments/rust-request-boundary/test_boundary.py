"""Independent actual-Server differential tests, not just a copied oracle."""
import sys
sys.dont_write_bytecode=True
import hashlib
import asyncio
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
from ciw import request_boundary_transport as transport
import unittest
from unittest.mock import patch
from ciw.agent_mcp import Server,MAX_MESSAGE,MAX_REQUESTS
from ciw import request_boundary as boundary
from ciw.request_boundary_contracts import classify,wire_output,SCHEMA

BINARY=Path(os.environ.get('CIW_RUST_BOUNDARY_BINARY',
    str(Path(__file__).resolve().parent/'target/release/odsp-request-boundary')))
class Host:
    def descriptions(self):return []
    def call(self,*args):raise AssertionError('prefix tests must not dispatch tools')

class BoundaryTests(unittest.TestCase):
    cases=0;matched=0;fallback=0
    @classmethod
    def setUpClass(cls):
        cls.digest=hashlib.sha256(BINARY.read_bytes()).hexdigest()
    def active(self):return boundary.RequestBoundary(enabled=True,binary=BINARY,binary_sha256=self.digest)
    def test_positive_native_observation_must_match(self):
        observer=self.active()
        self.assertEqual(observer.prefix(b'{"jsonrpc":"2.0","id":1,"method":"ping"}')['decision'],'candidate')
        self.assertEqual(observer.last_observation,{'status':'matched','reason':'exact_result','execution_authorized':False})
        observer.wire_output(MAX_MESSAGE-1024)
        self.assertEqual(observer.last_observation['status'],'matched')

    def test_official_client_and_real_rust_keep_current_principal_grants(self):
        import ciw
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        program='''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import ciw
from ciw.agent_mcp import demo_config,from_profile,serve
from ciw.control_gateway import ControlHost
from ciw.principal_policy import PrincipalPolicy,owner_for
from ciw.request_boundary import RequestBoundary
observer=RequestBoundary(enabled=True,binary=Path(sys.argv[2]),binary_sha256=sys.argv[3])
grant={'principal':'alice','enabled':True,'operations':[{'name':'ncp_capabilities_list','constraints':{}}],
       'scientific_operations':[],'resources':[],'reference_scopes':[],'handoff_recipients':[]}
policy=PrincipalPolicy(lambda:{'schema':'ciw.principal-policy.v1','revision':'native-shadow-client','principals':[grant]})
with from_profile(demo_config(Path.cwd()/'profile')) as owner,ControlHost(owner,retained_owner=owner_for('alice')) as control:
    serve(policy.facade('alice',control=control),sys.stdin.buffer,sys.stdout.buffer,request_boundary=observer)
    summary={'observation':observer.last_observation,'executions':owner.capabilities()['execution_budget']['used'],'package':ciw.__file__}
Path(sys.argv[4]).write_text(json.dumps(summary))
'''
        with tempfile.TemporaryDirectory() as directory:
            summary=Path(directory)/'native-client.json'
            package_parent=str(Path(ciw.__file__).resolve().parent.parent)
            async def journey():
                params=StdioServerParameters(command=sys.executable,args=['-I','-B','-c',program,
                    package_parent,str(BINARY),self.digest,str(summary)],cwd=directory,
                    env={'NCP_RETAIN_DIR':str(Path(directory)/'retained')})
                async with stdio_client(params) as (reader,writer):
                    async with ClientSession(reader,writer,read_timeout_seconds=30) as client:
                        await client.initialize()
                        self.assertEqual([row.name for row in (await client.list_tools()).tools],['ncp_capabilities_list'])
                        self.assertFalse((await client.call_tool('ncp_capabilities_list',{})).is_error)
                        self.assertTrue((await client.call_tool('ncp_run_submit',{})).is_error)
                        self.assertTrue((await client.call_tool('arbitrary_shell',{'command':'inert'})).is_error)
            asyncio.run(journey())
            result=json.loads(summary.read_text())
            self.assertEqual(result['observation'],{'status':'matched','reason':'exact_result','execution_authorized':False})
            self.assertEqual(result['executions'],0)
            self.assertEqual(Path(result['package']).resolve(),Path(ciw.__file__).resolve())
    def check(self,raw,seen=frozenset()):
        original=set(seen);expected,_=classify(raw,seen)
        server=Server(Host());server.seen=set(seen)
        actual=server.handle(raw)
        if expected['decision']=='reject':
            self.assertEqual(actual['error']['code'],expected['code'],raw[:100])
            self.assertIs(type(actual['id']),type(expected['response_id']))
            self.assertEqual(actual['id'],expected['response_id'])
            self.assertEqual(server.seen,original)
        elif expected['decision']=='notification':
            self.assertIsNone(actual);self.assertEqual(server.seen,original)
        else:
            self.assertEqual(len(server.seen),len(original)+1)
            self.assertIn((type(expected['response_id']),expected['response_id']),server.seen)
        adapter=self.active();self.assertEqual(adapter.prefix(raw,seen),expected)
        self.assertEqual(set(seen),original)
        self.assertIn(adapter.last_observation['status'],('matched','fallback','python_only'))
        self.assertIs(adapter.last_observation['execution_authorized'],False)
        BoundaryTests.cases+=1
        if adapter.last_observation['status']=='matched':BoundaryTests.matched+=1
        else:BoundaryTests.fallback+=1
    def test_raw_negative_and_header_vectors(self):
        for raw in [b'',b'{',b'[]',b'null',b'{}',b'NaN',b'\xff',b'{"id":1,"id":2}',
                    b'{"id":1,"\\u0069d":2}',b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"n":1e999}}',
                    b'{}'+b' '*MAX_MESSAGE]:self.check(raw)
        for ident in [None,True,False,0,-1,2**53-1,-(2**53-1),2**53,-2**53,2**100,
                      1.0,-0.0,1e150,'','1','x'*128,'x'*129,'\U00010000'*128,'\ud800']:
            self.check(json.dumps({'jsonrpc':'2.0','id':ident,'method':'ping'}).encode())
        for message in [
            {'jsonrpc':'2.0','method':'tools/call'},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','method':'ping','params':None},
            {'jsonrpc':'2.0','id':1,'method':'ping','params':None},
            {'jsonrpc':'2.0','id':1,'method':'ping','extra':0},
            {'jsonrpc':'2.0','id':1,'method':''},
            {'jsonrpc':'2.0','id':1,'method':' '},
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
            {'jsonrpc':2.0,'id':1,'method':'ping'},
            {'jsonrpc':'2.0','id':1,'method':'ping','params':{'n':1e150,'big':2**100}},
            {'jsonrpc':'2.0','id':1,'method':'ping','params':{'n':10**150}},
        ]:self.check(json.dumps(message).encode())
    def test_seen_state_and_budget(self):
        for seen in [frozenset(),frozenset({(int,1)}),frozenset({(str,'1')}),
                     frozenset((int,i)for i in range(MAX_REQUESTS))]:
            for ident in [1,'1',9000]:
                self.check(json.dumps({'jsonrpc':'2.0','id':ident,'method':'ping'}).encode(),seen)
        self.check(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":false}',{(int,1)})
    def test_seeded_and_mutated_actual_server(self):
        rng=random.Random(0xcef7d007)
        for _ in range(200):
            message={'jsonrpc':rng.choice(['2.0','wrong']),
                     'id':rng.choice([True,None,rng.randrange(-2**54,2**54),str(rng.randrange(10))]),
                     'method':rng.choice(['ping','initialize','','tools/list']),
                     'params':rng.choice([{},[],None,{'x':[True,2**100,1e150]}])}
            self.check(json.dumps(message).encode())
        for _ in range(100):
            raw=bytearray(b'{"jsonrpc":"2.0","id":42,"method":"ping"}')
            raw[rng.randrange(len(raw))]=rng.randrange(256);self.check(bytes(raw))
    def test_output_budget_actual_encoding(self):
        for count in [0,MAX_MESSAGE-1025,MAX_MESSAGE-1024,MAX_MESSAGE-1023,2**64-1,2**64]:
            active=self.active();self.assertEqual(active.wire_output(count),wire_output(count))
            self.assertNotEqual(active.last_observation['status'],'mismatch')
        for value in [True,-1,1.0]:
            with self.assertRaises(ValueError):self.active().wire_output(value)
        # Actual existing serializer determines count; Rust never re-encodes.
        for n in [MAX_MESSAGE-1026,MAX_MESSAGE-1025]:
            encoded=json.dumps('a'*n,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()
            active=self.active();result=active.wire_output(len(encoded))
            self.assertEqual(result['decision']=='output_candidate',len(encoded)<=MAX_MESSAGE-1024)
    def test_disabled_and_bad_child_fail_closed(self):
        raw=b'{"jsonrpc":"2.0","id":1,"method":"ping"}';expected,_=classify(raw,frozenset())
        with patch.object(boundary,'_exchange',side_effect=AssertionError('disabled')):
            self.assertEqual(boundary.RequestBoundary().prefix(raw),expected)
        replies=[b'{}',b'null',b'invalid',b'{"schema":"wrong","status":"ok"}',
                 b'{"schema":"odsp.request-boundary.reply.v1","status":"unsupported"}',
                 json.dumps({'schema':'odsp.request-boundary.reply.v1','status':'ok','result':{'execution_authorized':True}}).encode()]
        for output in replies:
            with patch.object(boundary,'_exchange',return_value=output):
                active=self.active();self.assertEqual(active.prefix(raw),expected)
                self.assertIn(active.last_observation['status'],('fallback','mismatch'))
        for failure in [OSError(),boundary.ShadowFailure('timeout'),boundary.ShadowFailure('output_budget')]:
            with patch.object(boundary,'_exchange',side_effect=failure):
                self.assertEqual(self.active().prefix(raw),expected)
        bad=self.active();bad.binary_sha256='0'*64
        self.assertEqual(bad.prefix(raw),expected);self.assertEqual(bad.last_observation['reason'],'binary_binding')
        with patch.dict(boundary.SOURCE_SHA256,{'agent_mcp':'0'*64}):
            active=self.active();self.assertEqual(active.prefix(raw),expected)
            self.assertEqual(active.last_observation['reason'],'source_binding')
    def test_transport_real_caps_and_reaping(self):
        with tempfile.TemporaryDirectory() as folder:
            for name,body,reason in [
                ('timeout','import time;time.sleep(5)','timeout'),
                ('output','import os;os.write(1,b"x"*65536)','output_budget')]:
                path=Path(folder)/name
                path.write_text('#!'+sys.executable+'\n'+body+'\n');path.chmod(0o700)
                children=[];real=transport.subprocess.Popen
                def launch(*args,**kwargs):
                    child=real(*args,**kwargs);children.append(child);return child
                with patch.object(transport.subprocess,'Popen',side_effect=launch):
                    with self.assertRaisesRegex(transport.ShadowFailure,reason):
                        transport._exchange(path,'boundary-v1',b'{}',0.1)
                self.assertTrue(children);self.assertIsNotNone(children[0].poll())
    def test_cli_contract_negatives(self):
        valid={'schema':SCHEMA,'operation':'wire_output','encoded_bytes':0}
        cases=[b'',b'{}',b'\xff',b'{',b'NaN',b'{"schema":"a","schema":"b"}',
               json.dumps(dict(valid,schema='odsp.request-boundary.v2')).encode(),
               json.dumps(dict(valid,encoded_bytes=True)).encode(),
               json.dumps(dict(valid,extra=1)).encode(),b' '* (boundary.MAX_INPUT+1)]
        for raw in cases:
            proc=subprocess.run([str(BINARY),'boundary-v1'],input=raw,stdout=subprocess.PIPE,check=True)
            self.assertLessEqual(len(proc.stdout),4096)
            reply=json.loads(proc.stdout);self.assertEqual(reply['status'],'unsupported')
            self.assertEqual(reply['fallback'],'python_required')
            self.assertIs(reply['execution_authorized'],False)
        raw=json.dumps(valid).encode()
        proc=subprocess.run([str(BINARY),'boundary-v1'],input=raw+b' '*(boundary.MAX_INPUT-len(raw)),stdout=subprocess.PIPE,check=True)
        self.assertEqual(json.loads(proc.stdout)['status'],'ok')
        proc=subprocess.run([str(BINARY),'wrong-v2'],input=raw,stdout=subprocess.PIPE,check=True)
        self.assertEqual(json.loads(proc.stdout)['status'],'unsupported')
    @classmethod
    def tearDownClass(cls):print(json.dumps({'actual_server_differential_cases':cls.cases,'rust_matches':cls.matched,'python_only_or_fallback':cls.fallback}))
if __name__=='__main__':unittest.main()
