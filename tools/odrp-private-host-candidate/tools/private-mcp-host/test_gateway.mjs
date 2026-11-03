import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {route,principalContext} from './gateway.mjs';
const base='https://private.example.test';
const tokenA='test-only-alice-token-'.repeat(3),tokenB='test-only-bob-token-'.repeat(3);
const hash=s=>createHash('sha256').update(s).digest('hex');
function fixture(){
  const start=Math.floor(Date.now()/1000)-1;
  const config={schema:'ciw.bearer-principals.v2',policy_revision:'test-1',policy_sha256:'sha256:'+'1'.repeat(64),
    principals:[{principal:'alice',enabled:true,token_sha256:hash(tokenA),credential_id:'alice-fixture-1',not_before:start,expires_at:start+3600},
      {principal:'bob',enabled:true,token_sha256:hash(tokenB),credential_id:'bob-fixture-1',not_before:start,expires_at:start+3600}]};
  const requests=[];
  const env={PUBLIC_HOSTNAME:'private.example.test',HOST_SINGLETON:'test-local-singleton',PRINCIPAL_AUTH_JSON:JSON.stringify(config),
    HOST:{getByName(name){assert.equal(name,'test-local-singleton');return {fetch(request){requests.push(request);return Response.json({ok:true});}};}}};
  return {env,config,requests,update:()=>{env.PRINCIPAL_AUTH_JSON=JSON.stringify(config);}};
}
function request(path='/api/tools',token=tokenA,options={}){
  return new Request(base+path,{...options,headers:{authorization:'Bearer '+token,...options.headers}});
}
test('anonymous, invalid, missing and revoked configuration never wakes host',async()=>{
  const {env,config,requests,update}=fixture();
  assert.equal((await route(new Request(base+'/api/tools'),env)).status,401);
  assert.equal((await route(request('/api/tools','invalid'.repeat(8)),env)).status,401);
  config.principals[0].enabled=false;update();
  assert.equal((await route(request(),env)).status,401);
  env.PRINCIPAL_AUTH_JSON='{}';assert.equal((await route(request(),env)).status,503);
  delete env.PRINCIPAL_AUTH_JSON;assert.equal((await route(request(),env)).status,503);
  assert.equal(requests.length,0);
});
test('closed config rejects duplicate principal/secret and unknown grant fields',async()=>{
  for(const mutate of [c=>c.principals.push({...c.principals[0]}),c=>c.principals[1].token_sha256=c.principals[0].token_sha256,
    c=>c.principals[0].operations=['admin'],c=>delete c.policy_sha256,c=>c.policy_revision={},c=>c.principals[0].enabled='true']){
    const {env,config,requests,update}=fixture();mutate(config);update();
    assert.equal((await route(request(),env)).status,503);assert.equal(requests.length,0);
  }
});
test('two distinct principals remain distinct and all caller headers are replaced for GET and POST',async()=>{
  const {env,requests}=fixture();
  for(const [token,principal] of [[tokenA,'alice'],[tokenB,'bob']]){
    for(const post of [false,true]){
      const headers={'x-odsp-principal':'attacker','x-odsp-owner':'attacker','x-odsp-policy-sha256':'forged',
        'x-odsp-policy-revision':'forged','x-odsp-credential-id':'forged','x-odsp-credential-sha256':'forged',
        'x-internal-admin':'yes','cookie':'secret'};
      if(post)headers['Content-Type']='application/json';
      assert.equal((await route(request(post?'/api/calls':'/api/tools',token,{headers,...post?{method:'POST',body:'{}'}:{}}),env)).status,200);
      const forwarded=requests.at(-1);const identity=await principalContext(forwarded,env);
      assert.equal(identity.principal,principal);assert.equal(identity.owner,'principal:'+hash(principal));
      assert.equal(forwarded.headers.has('authorization'),false);assert.equal(forwarded.headers.has('cookie'),false);
      assert.equal(forwarded.headers.has('x-internal-admin'),false);
    }
  }
});
test('queued internal request fails current configuration after revoke or policy change',async()=>{
  const {env,config,requests,update}=fixture();await route(request(),env);
  config.principals[0].enabled=false;update();await assert.rejects(principalContext(requests[0],env));
  config.principals[0].enabled=true;config.policy_revision='test-2';update();await assert.rejects(principalContext(requests[0],env));
});
test('version two requires distinct bounded credentials and rejects perpetual or ambiguous config',async()=>{
  const mutations=[c=>c.schema='ciw.bearer-principals.v1', c=>delete c.principals[0].credential_id,
    c=>c.principals[1].credential_id=c.principals[0].credential_id,c=>c.principals[0].credential_id='',
    c=>delete c.principals[0].not_before,c=>delete c.principals[0].expires_at,
    c=>c.principals[0].expires_at=c.principals[0].not_before,
    c=>c.principals[0].expires_at=c.principals[0].not_before+3601];
  for(const field of ['not_before','expires_at'])for(const value of [true,null,'123',-1,1.5,Number.MAX_SAFE_INTEGER+1])
    mutations.push(c=>c.principals[0][field]=value);
  for(const mutate of mutations){
    const f=fixture();mutate(f.config);f.update();
    assert.equal((await route(request(),f.env)).status,503);assert.equal(f.requests.length,0);
  }
  const f=fixture();f.env.PRINCIPAL_AUTH_JSON=f.env.PRINCIPAL_AUTH_JSON.replace('"enabled":true','"enabled":false,"enabled":true');
  assert.equal((await route(request(),f.env)).status,503);assert.equal(f.requests.length,0);
});
test('credential validity includes not_before and excludes expires_at exactly',async()=>{
  const f=fixture(),row=f.config.principals[0];
  for(const [instant,status] of [[row.not_before*1000-1,401],[row.not_before*1000,200],
    [row.expires_at*1000-1,200],[row.expires_at*1000,401],[NaN,401]]){
    assert.equal((await route(request(),f.env,undefined,()=>instant)).status,status);
  }
  assert.equal(f.requests.length,2);
  // The actual Workers fetch third parameter is an ExecutionContext, never a clock.
  assert.equal((await route(request(),f.env,{waitUntil(){}})).status,200);
});
test('queued credential binding rejects rotation or renewal even with identical principal and policy',async()=>{
  for(const change of [r=>r.credential_id='alice-fixture-2',r=>r.token_sha256=hash('new-secret'),
    r=>r.not_before+=1,r=>r.expires_at-=1,r=>r.enabled=false]){
    const f=fixture(),instant=(f.config.principals[0].not_before+5)*1000;
    assert.equal((await route(request(),f.env,undefined,()=>instant)).status,200);
    const queued=f.requests[0];
    assert.equal((await principalContext(queued,f.env,()=>instant)).principal,'alice');
    change(f.config.principals[0]);f.update();
    await assert.rejects(principalContext(queued,f.env,()=>instant),/principal_refused/);
  }
  const f=fixture();await route(request(),f.env);
  await assert.rejects(principalContext(f.requests[0],f.env,()=>f.config.principals[0].expires_at*1000));
});
test('rotation revocation and expiry during body upload refuse before host lookup',async()=>{
  for(const kind of ['rotate','revoke','expire','renew','policy']){
    const f=fixture();let instant=(f.config.principals[0].not_before+5)*1000,lookups=0;
    f.env.HOST.getByName=()=>{lookups++;throw Error('stale authorization dispatched');};
    const body=new ReadableStream({pull(controller){
      const row=f.config.principals[0];
      if(kind==='rotate'){row.credential_id='new';row.token_sha256=hash('replacement');}
      if(kind==='revoke')row.enabled=false;
      if(kind==='expire')instant=row.expires_at*1000;
      if(kind==='renew')row.expires_at-=1;
      if(kind==='policy')f.config.policy_revision='new';
      f.update();controller.enqueue(new TextEncoder().encode('{}'));controller.close();
    }},{highWaterMark:0});
    const result=await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},
      body,duplex:'half'}),f.env,undefined,()=>instant);
    assert.equal(result.status,401,kind);assert.equal(lookups,0);
  }
});
test('internal identity rechecks configuration after asynchronous credential hashing',async t=>{
  const f=fixture();await route(request(),f.env);const queued=f.requests[0];
  const original=crypto.subtle.digest.bind(crypto.subtle);
  t.mock.method(crypto.subtle,'digest',async(algorithm,input)=>{
    const value=await original(algorithm,input);
    f.config.principals[0].enabled=false;f.update();return value;
  });
  await assert.rejects(principalContext(queued,f.env),/principal_refused/);
});
test('real clock expiry invalidates an already authenticated internal request',async()=>{
  const f=fixture(),seconds=Math.floor(Date.now()/1000),row=f.config.principals[0];
  row.not_before=seconds-1;row.expires_at=seconds+2;f.update();
  assert.equal((await route(request(),f.env)).status,200);
  await new Promise(resolve=>setTimeout(resolve,Math.max(0,row.expires_at*1000-Date.now())+15));
  await assert.rejects(principalContext(f.requests[0],f.env),/principal_refused/);
  assert.equal((await route(request(),f.env)).status,401);assert.equal(f.requests.length,1);
});
test('host, origin, queries, scheme and unconfigured route are denied without dispatch',async()=>{
  const {env,requests}=fixture();
  for(const r of [request('/api/tools',tokenA,{headers:{origin:'https://evil.test'}}),request('/api/tools?principal=bob'),
    new Request('http://private.example.test/api/tools',{headers:{authorization:'Bearer '+tokenA}})])assert.equal((await route(r,env)).status,403);
  assert.equal((await route(request('/api/shell'),env)).status,404);
  delete env.PUBLIC_HOSTNAME;assert.equal((await route(request(),env)).status,503);
  assert.equal(requests.length,0);
});
test('full UTF-8 body budget and caller identity payload refuse before host',async()=>{
  const {env,requests}=fixture();
  for(const [body,status] of [['[]',400],['{',400],[JSON.stringify({principal:'bob'}),400],
    [JSON.stringify({arguments:{query:'界'.repeat(90000)}}),413],['{}',200]]){
    assert.equal((await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env)).status,status);
  }
  assert.equal(requests.length,1);
});
test('authenticated readiness is routed through same principal binding',async()=>{
  const {env,requests}=fixture();assert.equal((await route(request('/api/readiness'),env)).status,200);
  assert.equal((await principalContext(requests[0],env)).principal,'alice');
});

test('cancelled authenticated streaming body returns before host dispatch',async()=>{
  const {env,requests}=fixture();const controller=new AbortController();let cancelled=false;
  const body=new ReadableStream({pull(){},cancel(){cancelled=true;}});
  const incoming=request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body,duplex:'half',signal:controller.signal});
  const result=route(incoming,env);setTimeout(()=>controller.abort(),20);
  assert.equal((await result).status,408);assert.equal(requests.length,0);assert.equal(cancelled,true);
});

const duplicateBodies=[
  ['top-level operation',String.raw`{"name":"ncp_knowledge_overview","name":"ncp_domain_run","arguments":{}}`],
  ['nested argument',String.raw`{"name":"ncp_domain_run","arguments":{"domain":"sequence","domain":"impact"}}`],
  ['object inside array',String.raw`{"arguments":{"rows":[{"other":1},{"key":1,"key":2}]}}`],
  ['escaped ASCII key',String.raw`{"arguments":{"name":1,"\u006eame":2}}`],
  ['escaped quote key',String.raw`{"arguments":{"a\"b":1,"a\u0022b":2}}`],
  ['escaped backslash key',String.raw`{"arguments":{"a\\b":1,"a\u005cb":2}}`],
  ['escaped solidus key',String.raw`{"arguments":{"a/b":1,"a\/b":2}}`],
  ['escaped control key',String.raw`{"arguments":{"line\n":1,"line\u000a":2}}`],
  ['surrogate pair equivalent to literal Unicode',String.raw`{"arguments":{"🚀":1,"\ud83d\ude80":2}}`],
  ['empty key',String.raw`{"arguments":{"":1,"":2}}`],
  ['prototype-looking key',String.raw`{"arguments":{"__proto__":1,"__proto__":2}}`],
  ['duplicate after nested container',String.raw`{"arguments":{"key":{"rows":[{},[]]},"key":2}}`],
];
for(const [label,body] of duplicateBodies)test('duplicate JSON '+label+' refuses before host wake',async()=>{
  const {env,requests}=fixture();JSON.parse(body); // Each fixture is otherwise valid JSON.
  const result=await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env);
  assert.equal(result.status,400);assert.deepEqual(await result.json(),{error:{code:'invalid_json'}});
  assert.equal(requests.length,0);
});

test('unique keys are scoped to each object and JSON-looking strings remain opaque',async()=>{
  const {env,requests}=fixture();
  const bodies=[
    String.raw`{"arguments":{"left":{"key":1},"right":{"key":2},"rows":[{"key":3},{"key":4}]}}`,
    String.raw`{"arguments":{"a":1,"\\u0061":2,"é":3,"e\u0301":4,"__proto__":5,"constructor":6}}`,
    JSON.stringify({arguments:{text:'"key":1, "key":2, { [ } ] \\ quoted " suffix',empty:'',unicode:'界🚀'}}),
    JSON.stringify({name:'ncp_domain_run',arguments_json:'{"domain":"sequence","domain":"impact"}'}),
    ' \n { "arguments" : { "rows" : [ null, true, false, -2.5e3, { "key" : "value" } ] } } \t ',
  ];
  for(const body of bodies){
    assert.equal((await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env)).status,200);
    assert.deepEqual(await requests.at(-1).json(),JSON.parse(body));
  }
  assert.equal(requests.length,bodies.length);
});
test('duplicate scan retains strict native JSON and UTF-8 rejection',async()=>{
  const {env,requests}=fixture();
  for(const body of ['{"arguments":{"a":1,}}','{"arguments":{"a":01}}','{"arguments":{"a":"\\x61"}}',
    '{"arguments":{"a":1}} trailing','{"arguments":{"a":"unterminated}}',new Uint8Array([123,34,97,34,58,34,0xc3,0x28,34,125])]){
    const response=await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env);
    assert.equal(response.status,400);assert.deepEqual(await response.json(),{error:{code:'invalid_json'}});
  }
  assert.equal(requests.length,0);
});
test('duplicate scan preserves exact body byte boundary',async()=>{
  const {env,requests}=fixture();
  const prefix='{"arguments":{"text":"',suffix='"}}',body=prefix+'x'.repeat(262144-prefix.length-suffix.length)+suffix;
  assert.equal(Buffer.byteLength(body),262144);
  assert.equal((await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env)).status,200);
  assert.equal((await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body:body+' '}),env)).status,413);
  assert.equal(requests.length,1);
});

test('decoded duplicate keys cover every ASCII code unit and Unicode edge cases',async()=>{
  const {env,requests}=fixture();
  for(const key of [...Array.from({length:128},(_,i)=>String.fromCharCode(i)),'é','e\u0301','🚀','\ud800','\udfff']){
    const unicode='"'+key.split('').map(c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0')).join('')+'"';
    const body='{"arguments":{'+JSON.stringify(key)+':{"nested":[{},[]]},'+unicode+':2}}';
    assert.equal((await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env)).status,400,JSON.stringify(key));
  }
  assert.equal(requests.length,0);
});
test('duplicate scan uses an iterative stack for deeply nested valid input',async()=>{
  const {env,requests}=fixture();
  const body='{"arguments":'+'['.repeat(10000)+'{"key":1,"key":2}'+']'.repeat(10000)+'}';
  JSON.parse(body);assert.ok(Buffer.byteLength(body)<262144);
  const result=await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env);
  assert.equal(result.status,400);assert.deepEqual(await result.json(),{error:{code:'invalid_json'}});assert.equal(requests.length,0);
});

for(const [kind,open,close] of [['arrays','[',']'],['objects','{"unique":','}']])
test('unserializable deeply nested unique '+kind+' return invalid_json before host wake',async()=>{
  const {env,requests}=fixture();let lookups=0;
  env.HOST.getByName=()=>{lookups++;throw Error('host must not be resolved');};
  const body='{"arguments":'+open.repeat(10000)+'null'+close.repeat(10000)+'}';
  JSON.parse(body);assert.ok(Buffer.byteLength(body)<262144);
  const result=await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env);
  assert.equal(result.status,400);assert.deepEqual(await result.json(),{error:{code:'invalid_json'}});
  assert.equal(lookups,0);assert.equal(requests.length,0);
});

test('serialization guard preserves opaque nested-looking strings and valid siblings',async()=>{
  const {env,requests}=fixture();
  const opaque='{"unique":'.repeat(10000)+'null'+'}'.repeat(10000);
  const bodies=[
    JSON.stringify({arguments:{text:opaque,left:{key:1},right:{key:2}}}),
    JSON.stringify({arguments:{rows:Array.from({length:10000},()=>({key:null}))}}),
    '{"arguments":'+'['.repeat(1000)+'{"unique":true}'+']'.repeat(1000)+'}',
  ];
  for(const body of bodies){
    assert.ok(Buffer.byteLength(body)<262144);
    assert.equal((await route(request('/api/calls',tokenA,{method:'POST',headers:{'Content-Type':'application/json'},body}),env)).status,200);
    assert.equal(await requests.at(-1).text(),JSON.stringify(JSON.parse(body)));
  }
  assert.equal(requests.length,bodies.length);
});
