import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {route,principalContext} from './gateway.mjs';
const base='https://private.example.test';
const tokenA='test-only-alice-token-'.repeat(3),tokenB='test-only-bob-token-'.repeat(3);
const hash=s=>createHash('sha256').update(s).digest('hex');
function fixture(){
  const config={schema:'ciw.bearer-principals.v1',policy_revision:'test-1',policy_sha256:'sha256:'+'1'.repeat(64),
    principals:[{principal:'alice',enabled:true,token_sha256:hash(tokenA)},{principal:'bob',enabled:true,token_sha256:hash(tokenB)}]};
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
        'x-odsp-policy-revision':'forged','x-internal-admin':'yes','cookie':'secret'};
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
