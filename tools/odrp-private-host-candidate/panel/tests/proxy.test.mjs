// Real ephemeral RSA signatures; local service-binding/JWKS substitutes only.
// This qualifies the boundary code, not a deployed Access application.
import test, {before} from 'node:test';
import assert from 'node:assert/strict';
import {createPanel, LIMITS} from '../worker/panel.mjs';
import {ACCESS_LIMITS} from '../worker/access.mjs';

const HOST = 'panel.example.test', ISSUER = 'fixture.cloudflareaccess.com';
const NOW = 1804200000000, SUBJECT = 'synthetic-subject', TOKEN = 'synthetic-server-only-token-0123456789';
const assets = {'/': {body: '<!doctype html><p>Authenticated fixture</p>', contentType: 'text/html; charset=utf-8'},
  '/app.js': {body: '/* fixture */', contentType: 'text/javascript; charset=utf-8'},
  '/source.json': {body: '{"synthetic":true}', contentType: 'application/json; charset=utf-8'}};
const b64 = value => Buffer.from(value).toString('base64url');
let key, jwk;
before(async () => {
  key = await crypto.subtle.generateKey({name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256'}, true, ['sign', 'verify']);
  jwk = {...await crypto.subtle.exportKey('jwk', key.publicKey), alg: 'RS256', use: 'sig', kid: 'fixture-key'};
});
async function sign(claimOverrides = {}, headerOverrides = {}, rawClaims) {
  const header = {alg: 'RS256', typ: 'JWT', kid: 'fixture-key', ...headerOverrides};
  const claims = {iss: 'https://' + ISSUER, aud: 'fixture-audience', sub: SUBJECT, type: 'app',
    iat: Math.floor(NOW / 1000) - 60, exp: Math.floor(NOW / 1000) + 600, ...claimOverrides};
  const value = b64(JSON.stringify(header)) + '.' + b64(rawClaims ?? JSON.stringify(claims));
  return value + '.' + b64(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key.privateKey, Buffer.from(value)));
}
const mapping = (patch = {}) => ({schema: 'odsp.panel-principals.v2', principals: [
  {subject: SUBJECT, principal: 'fixture-principal', enabled: true, backend_token: TOKEN, credential_id: 'fixture-credential-1',
    not_before: NOW / 1000 - 1, expires_at: NOW / 1000 + 3599, ...patch}]});
function setup(options = {}) {
  const backendCalls = [], keyCalls = [];
  let instant = NOW;
  const env = {PANEL_HOSTNAME: HOST, PANEL_BACKEND_ORIGIN: 'https://gateway.example.test',
    ACCESS_ISSUER: 'https://' + ISSUER, ACCESS_AUDIENCE: 'fixture-audience',
    PANEL_PRINCIPALS_JSON: JSON.stringify(mapping()), BACKEND: {fetch: async request => {
      backendCalls.push(request);
      return options.backend ? options.backend(request) : Response.json({fixture: true, retention: {committed: true}});
    }}};
  const panel = createPanel({assets, now: options.now ?? (() => instant), fetchJwks: async request => {
    keyCalls.push(request);
    return options.keys ? options.keys(request) : Response.json({keys: [jwk]});
  }});
  return {env, panel, backendCalls, keyCalls, advance: milliseconds => { instant += milliseconds; }};
}
function request(token, path = '/api/tools', options = {}) {
  const headers = {'cf-access-jwt-assertion': token,
    ...(path.startsWith('/api/') ? {'x-odsp-panel-request': '1'} : {}), ...options.headers};
  if (options.method === 'POST') {
    headers.origin ??= 'https://' + HOST;
    headers['content-type'] ??= 'application/json';
  }
  return new Request('https://' + HOST + path, {...options, headers});
}
async function refused(response, effect = 'not_started', status) {
  if (status !== undefined) assert.equal(response.status, status);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const value = await response.json();
  assert.equal(value.error.effect_state, effect);
  assert.equal(value.authorizes_execution, false);
  assert.equal(typeof value.error.code, 'string');
  return value;
}

test('signed Access protects every asset and API route; no cookie-only identity', async () => {
  const f = setup(), token = await sign();
  for (const path of ['/', '/app.js', '/source.json', '/api/readiness']) {
    await refused(await f.panel.fetch(new Request('https://' + HOST + path,
      {headers: {Cookie: 'CF_Authorization=' + token}}), f.env), 'not_started', 401);
    const response = await f.panel.fetch(request(token, path), f.env);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.match(response.headers.get('content-security-policy'), /frame-ancestors 'none'/);
    assert.match(response.headers.get('content-security-policy'), /connect-src 'self'/);
  }
  assert.equal(f.backendCalls.length, 1);
  assert.equal(f.keyCalls.length, 1);
  assert.equal(f.keyCalls[0].url, 'https://' + ISSUER + '/cdn-cgi/access/certs');
  assert.equal(f.keyCalls[0].redirect, 'manual');
  assert.equal(f.keyCalls[0].headers.has('authorization'), false);
});

test('exact origin/host/path/method refuses before backend dispatch', async () => {
  const f = setup(), token = await sign();
  for (const item of [
    request(token, '/api/tools?x=1'), request(token, '/api/tools?'), request(token, '/api/%74ools'),
    request(token, '/api/tools', {headers: {host: 'other.example.test'}}),
    request(token, '/api/tools', {headers: {origin: 'https://other.example.test'}}),
    request(token, '/api/tools', {headers: {'sec-fetch-site': 'cross-site'}}),
    new Request('http://' + HOST + '/api/tools', {headers: {'cf-access-jwt-assertion': token}}),
    new Request('https://other.example.test/api/tools', {headers: {'cf-access-jwt-assertion': token}}),
    new Request('https://' + HOST + '/api/calls', {method: 'POST', body: '{}',
      headers: {'cf-access-jwt-assertion': token, 'content-type': 'application/json'}}),
  ]) await refused(await f.panel.fetch(item, f.env), 'not_started', 403);
  for (const item of [request(token, '/api/reset'), request(token, '/api/tools', {method: 'DELETE'}),
    request(token, '/app.js', {method: 'HEAD'})]) await refused(await f.panel.fetch(item, f.env), 'not_started', 404);
  assert.equal(f.backendCalls.length, 0);
});

test('metadata-absent simple GET and cross-origin preflight cannot initiate custody', async () => {
  const f = setup(), token = await sign();
  for (const path of ['/api/tools', '/api/readiness', '/api/status', '/api/events', '/api/runs']) {
    const simple = new Request('https://' + HOST + path, {headers: {'cf-access-jwt-assertion': token}});
    const body = await refused(await f.panel.fetch(simple, f.env), 'not_started', 403);
    assert.equal(body.error.code, 'panel_request_header_required');
  }
  await refused(await f.panel.fetch(request(token, '/api/tools', {method: 'OPTIONS', headers: {
    origin: 'https://attacker.invalid', 'access-control-request-method': 'GET',
    'access-control-request-headers': 'x-odsp-panel-request'}}), f.env), 'not_started', 403);
  await refused(await f.panel.fetch(request(token, '/api/tools', {headers: {'x-odsp-panel-request': '1, 1'}}), f.env), 'not_started', 403);
  assert.equal(f.backendCalls.length, 0);
  assert.equal((await f.panel.fetch(request(token, '/'), f.env)).status, 200);
});

test('two exact subjects select distinct server-only backend credentials', async () => {
  const f = setup(), second = {...mapping().principals[0], subject: 'second-subject',
    principal: 'second-principal', credential_id: 'second-credential', backend_token: 'second-server-only-token-0123456789'};
  f.env.PANEL_PRINCIPALS_JSON = JSON.stringify({schema: 'odsp.panel-principals.v2', principals: [mapping().principals[0], second]});
  assert.equal((await f.panel.fetch(request(await sign()), f.env)).status, 200);
  assert.equal((await f.panel.fetch(request(await sign({sub: second.subject})), f.env)).status, 200);
  assert.deepEqual(f.backendCalls.map(r => r.headers.get('authorization')),
    ['Bearer ' + TOKEN, 'Bearer ' + second.backend_token]);
  f.env.PANEL_PRINCIPALS_JSON = JSON.stringify({schema: 'odsp.panel-principals.v2', principals: [
    {...mapping().principals[0], enabled: false}, second]});
  await refused(await f.panel.fetch(request(await sign()), f.env), 'not_started', 401);
  assert.equal((await f.panel.fetch(request(await sign({sub: second.subject})), f.env)).status, 200);
  assert.equal(f.backendCalls.length, 3);
});

test('cryptographic signature, exact claims and current validity are mandatory', async () => {
  const f = setup();
  for (const claims of [{iss: 'https://other.cloudflareaccess.com'}, {aud: 'other'},
    {aud: ['fixture-audience', 'other']}, {type: 'service'}, {sub: 'unmapped'}, {sub: ''},
    {exp: NOW / 1000}, {exp: '1804200600'}, {iat: NOW / 1000 + 1},
    {iat: true}, {nbf: NOW / 1000 + 1}, {nbf: 'tomorrow'}])
    await refused(await f.panel.fetch(request(await sign(claims)), f.env), 'not_started', 401);
  const valid = await sign();
  const pieces = valid.split('.');
  const changed = JSON.parse(Buffer.from(pieces[1], 'base64url')); changed.sub = 'unmapped';
  pieces[1] = b64(JSON.stringify(changed));
  await refused(await f.panel.fetch(request(pieces.join('.')), f.env), 'not_started', 401);
  const secondKey = await crypto.subtle.generateKey({name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256'}, true, ['sign', 'verify']);
  const signed = valid.slice(0, valid.lastIndexOf('.'));
  const forged = signed + '.' + b64(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', secondKey.privateKey, Buffer.from(signed)));
  await refused(await f.panel.fetch(request(forged), f.env), 'not_started', 401);
  assert.equal(f.backendCalls.length, 0);
  assert.equal((await f.panel.fetch(request(await sign({aud: ['fixture-audience']})), f.env)).status, 200);
});

test('JWT algorithms, embedded key sources, duplicate claims and oversized tokens refuse', async () => {
  const f = setup();
  for (const header of [{alg: 'HS256'}, {alg: 'none'}, {jku: 'https://attacker.invalid/keys'},
    {x5u: 'https://attacker.invalid/cert'}, {crit: []}, {kid: ''}, {typ: 'other'}])
    await refused(await f.panel.fetch(request(await sign({}, header)), f.env), 'not_started', 401);
  const raw = JSON.stringify({iss: 'https://' + ISSUER, aud: 'fixture-audience', type: 'app', sub: SUBJECT,
    iat: NOW / 1000 - 60, exp: NOW / 1000 + 600}).replace('"sub":', '"sub":"other","s\\u0075b":');
  for (const token of [await sign({}, {}, raw), 'x'.repeat(ACCESS_LIMITS.tokenBytes + 1),
    'not.jwt', (await sign()) + '=', (await sign()) + ', ' + (await sign())])
    await refused(await f.panel.fetch(request(token), f.env), 'not_started', 401);
  assert.equal(f.backendCalls.length, 0);
  assert.equal(f.keyCalls.length, 0);
});

test('configuration has no credential fallback and binds one exact subject to one principal', async () => {
  const token = await sign();
  for (const update of [{BACKEND: undefined}, {BACKEND: {url: 'https://public.invalid'}},
    {PANEL_HOSTNAME: 'UPPER.example.test'}, {PANEL_BACKEND_ORIGIN: 'http://gateway.example.test'},
    {PANEL_BACKEND_ORIGIN: 'https://gateway.example.test/path'},
    {PANEL_BACKEND_ORIGIN: 'https://user:pass@gateway.example.test'},
    {ACCESS_ISSUER: 'https://attacker.invalid'}, {ACCESS_AUDIENCE: ''},
    {PANEL_PRINCIPALS_JSON: JSON.stringify({schema: 'wrong', principals: []})}]) {
    const f = setup(); Object.assign(f.env, update);
    await refused(await f.panel.fetch(request(token), f.env), 'not_started', 503);
    assert.equal(f.backendCalls.length, 0);
  }
  for (const rows of [[mapping().principals[0], mapping().principals[0]],
    [mapping().principals[0], {...mapping().principals[0], subject: 'other', principal: 'other'}],
    [{...mapping().principals[0], token: TOKEN}], [{...mapping().principals[0], backend_token: 'too-short'}]]) {
    const f = setup(); f.env.PANEL_PRINCIPALS_JSON = JSON.stringify({schema: 'odsp.panel-principals.v2', principals: rows});
    await refused(await f.panel.fetch(request(token), f.env), 'not_started', 503);
  }
  const f = setup(); f.env.PANEL_PRINCIPALS_JSON = JSON.stringify(mapping()).replace('"enabled":true', '"enabled":false,"enabled":true');
  await refused(await f.panel.fetch(request(token), f.env), 'not_started', 503);
});

test('disabled and remapped subjects are rechecked despite cached signing key', async () => {
  const f = setup(), token = await sign();
  assert.equal((await f.panel.fetch(request(token), f.env)).status, 200);
  f.env.PANEL_PRINCIPALS_JSON = JSON.stringify(mapping({enabled: false}));
  await refused(await f.panel.fetch(request(token), f.env), 'not_started', 401);
  await refused(await f.panel.fetch(request(token, '/'), f.env), 'not_started', 401);
  assert.equal(f.backendCalls.length, 1);
  assert.equal(f.keyCalls.length, 1);
});

test('mapping revocation and token expiry during request upload prevent dispatch', async () => {
  for (const action of ['revoke', 'expire', 'rebind']) {
    const f = setup(), token = await sign();
    const body = new ReadableStream({pull(controller) {
      if (action === 'revoke') f.env.PANEL_PRINCIPALS_JSON = JSON.stringify(mapping({enabled: false}));
      if (action === 'expire') f.advance(601000);
      if (action === 'rebind') f.env.PANEL_PRINCIPALS_JSON = JSON.stringify(mapping({principal: 'different'}));
      controller.enqueue(new TextEncoder().encode('{}')); controller.close();
    }});
    await refused(await f.panel.fetch(request(token, '/api/calls', {method: 'POST', body, duplex: 'half'}), f.env), 'not_started', 401);
    assert.equal(f.backendCalls.length, 0);
  }
});

test('version two mappings require unique identifiers and at most one-hour integer validity', async () => {
  const token=await sign();
  const mutations=[m=>m.schema='odsp.panel-principals.v1', m=>delete m.principals[0].credential_id,
    m=>m.principals[0].credential_id='', m=>delete m.principals[0].not_before,
    m=>delete m.principals[0].expires_at, m=>m.principals[0].expires_at=m.principals[0].not_before,
    m=>m.principals[0].expires_at=m.principals[0].not_before+3601,
    m=>m.principals.push({...m.principals[0],subject:'other',principal:'other',backend_token:TOKEN+'other'})];
  for(const field of ['not_before','expires_at'])for(const value of [true,null,'123',-1,1.5,Number.MAX_SAFE_INTEGER+1])
    mutations.push(m=>m.principals[0][field]=value);
  for(const mutate of mutations){
    const f=setup(),m=mapping();mutate(m);f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(m);
    await refused(await f.panel.fetch(request(token),f.env),'not_started',503);assert.equal(f.backendCalls.length,0);
  }
});

test('credential not_before and expiry are exact and protect both assets and API', async () => {
  const token=await sign();
  for(const path of ['/','/api/tools']){
    const f=setup();f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(mapping({not_before:NOW/1000+1,expires_at:NOW/1000+2}));
    await refused(await f.panel.fetch(request(token,path),f.env),'not_started',401);
    f.advance(1000);assert.equal((await f.panel.fetch(request(token,path),f.env)).status,200);
    f.advance(1000);await refused(await f.panel.fetch(request(token,path),f.env),'not_started',401);
    assert.equal(f.backendCalls.length,path==='/'?0:1);
  }
});

test('same-principal rotation renewal and credential expiry during body upload never dispatch', async () => {
  const token=await sign();
  for(const change of ['id','token','renew','expire']){
    const f=setup();f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(mapping({expires_at:NOW/1000+1}));
    const body=new ReadableStream({pull(controller){
      const m=JSON.parse(f.env.PANEL_PRINCIPALS_JSON),row=m.principals[0];
      if(change==='id')row.credential_id='replacement-id';
      if(change==='token')row.backend_token=TOKEN+'replacement';
      if(change==='renew')row.expires_at+=1;
      if(change==='expire')f.advance(1000);
      f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(m);
      controller.enqueue(new TextEncoder().encode('{}'));controller.close();
    }},{highWaterMark:0});
    await refused(await f.panel.fetch(request(token,'/api/calls',{method:'POST',body,duplex:'half'}),f.env),'not_started',401);
    assert.equal(f.backendCalls.length,0,change);
  }
});

test('mapping rotation during asynchronous JWKS verification cannot switch to a replacement credential', async () => {
  let f;
  f=setup({keys:async()=>{
    await Promise.resolve();f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(mapping({credential_id:'replacement',backend_token:TOKEN+'new'}));
    return Response.json({keys:[jwk]});
  }});
  await refused(await f.panel.fetch(request(await sign()),f.env),'not_started',401);
  assert.equal(f.backendCalls.length,0);
});

test('expiry rotation and revocation before response disclosure suppress results and preserve possible-effect uncertainty', async () => {
  const token=await sign();
  for(const phase of ['headers','body'])for(const path of ['/api/tools','/api/calls','/api/readiness'])
    for(const change of ['credential-expiry','assertion-expiry','id','token','renew','revoke','principal','issuer']){
      let f;
      const mutate=()=>{
        const m=JSON.parse(f.env.PANEL_PRINCIPALS_JSON),row=m.principals[0];
        if(change==='credential-expiry')f.advance(1000);
        if(change==='assertion-expiry')f.advance(601000);
        if(change==='id')row.credential_id='replacement';
        if(change==='token')row.backend_token=TOKEN+'replacement';
        if(change==='renew')row.expires_at+=1;
        if(change==='revoke')row.enabled=false;
        if(change==='principal')row.principal='replacement';
        if(change==='issuer')f.env.ACCESS_ISSUER='https://replacement.cloudflareaccess.com';
        f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(m);
      };
      f=setup({backend:async()=>{
        if(phase==='headers')mutate();
        return new Response(new ReadableStream({pull(controller){
          if(phase==='body')mutate();
          controller.enqueue(new TextEncoder().encode('{"private_result":"backend-sensitive","retention":{"committed":true}}'));
          controller.close();
        }},{highWaterMark:0}),{headers:{'content-type':'application/json'}});
      }});
      // A short credential permits testing its own expiry independently of the Access assertion.
      f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(mapping({expires_at:NOW/1000+1}));
      const response=await f.panel.fetch(request(token,path,path==='/api/calls'?{method:'POST',body:'{}'}:{}),f.env);
      const value=await refused(response,path==='/api/readiness'?'not_started':'unconfirmed',401);
      assert.equal(JSON.stringify(value).includes('backend-sensitive'),false,phase+':'+change);
      assert.equal(f.backendCalls.length,1);assert.equal(Object.hasOwn(value,'retention'),false);
    }
});

test('real clock expiration blocks a previously valid panel mapping without revoking its Access assertion', async () => {
  const seconds=Math.floor(Date.now()/1000),f=setup({now:()=>Date.now()});
  f.env.PANEL_PRINCIPALS_JSON=JSON.stringify(mapping({not_before:seconds-1,expires_at:seconds+2}));
  const token=await sign({iat:seconds-1,exp:seconds+600});
  assert.equal((await f.panel.fetch(request(token),f.env)).status,200);
  await new Promise(resolve=>setTimeout(resolve,Math.max(0,(seconds+2)*1000-Date.now())+15));
  await refused(await f.panel.fetch(request(token),f.env),'not_started',401);
  assert.equal(f.backendCalls.length,1);
});

test('non-Error backend rejection still preserves uncertainty without disclosing thrown values', async () => {
  const token=await sign();
  for(const thrown of [null,undefined,'private thrown value',{details:'private thrown value'}]){
    const f=setup({backend:()=>{throw thrown;}});
    const value=await refused(await f.panel.fetch(request(token),f.env),'unconfirmed',502);
    assert.equal(f.backendCalls.length,1);assert.equal(JSON.stringify(value).includes('private thrown value'),false);
  }
});

test('raw JSON and exact backend response bytes preserved; browser credentials never forwarded', async () => {
  const wire = '{ "data" : {"fixture":true}, "raw_json":"{\\"fixture\\":true}", "retention":{"committed":true} }\n';
  const f = setup({backend: async request => {
    assert.equal(request.url, 'https://gateway.example.test/api/calls');
    assert.equal(request.redirect, 'manual');
    assert.equal(request.headers.get('authorization'), 'Bearer ' + TOKEN);
    assert.deepEqual([...request.headers.keys()].sort(), ['accept', 'authorization', 'content-type']);
    assert.equal(await request.text(), '{  "name":"synthetic", "arguments":{} }\n');
    return new Response(wire, {headers: {'Content-Type': 'application/json', 'Set-Cookie': 'secret=x',
      Location: 'https://attacker.invalid', 'Access-Control-Allow-Origin': '*', 'X-Internal': 'secret'}});
  }});
  const response = await f.panel.fetch(request(await sign(), '/api/calls', {method: 'POST',
    body: '{  "name":"synthetic", "arguments":{} }\n', headers: {Authorization: 'Bearer browser-secret',
      Cookie: 'CF_Authorization=browser-secret', 'x-odsp-principal': 'attacker', 'x-odsp-owner': 'attacker',
      'x-odsp-policy-sha256': 'attacker', 'x-destination': 'https://attacker.invalid'}}), f.env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), wire);
  for (const header of ['set-cookie', 'location', 'access-control-allow-origin', 'x-internal']) assert.equal(response.headers.has(header), false);
});

test('non-2xx backend JSON and custody receipts are not reserialized or upgraded', async () => {
  for (const status of [400, 401, 403, 409, 500, 503]) {
    const wire = '{ "error":{"code":"outcome_unknown","effect_state":"unconfirmed"},"transaction_id":"fixture-id" }\n';
    const f = setup({backend: () => new Response(wire, {status, headers: {'content-type': 'application/json'}})});
    const response = await f.panel.fetch(request(await sign()), f.env);
    assert.equal(response.status, status); assert.equal(await response.text(), wire);
  }
  const f = setup({backend: () => Response.json({data: 'no retention claim'})});
  assert.deepEqual(await (await f.panel.fetch(request(await sign()), f.env)).json(), {data: 'no retention claim'});
});

test('POST validation refuses malformed/duplicate/UTF8/depth/authority before backend', async () => {
  const f = setup(), token = await sign();
  const invalid = ['{', '[]', 'null', '{"x":1,"x":2}', '{"principal":"attacker"}',
    '{"token":"attacker"}', '{"destination":"https://attacker.invalid"}',
    '{"name":"x","na\\u006de":"y"}', '{"x":' + '['.repeat(40) + '0' + ']'.repeat(40) + '}',
    new Uint8Array([123, 34, 120, 34, 58, 34, 255, 34, 125])];
  for (const body of invalid) await refused(await f.panel.fetch(request(token, '/api/calls', {method: 'POST', body}), f.env), 'not_started', 400);
  await refused(await f.panel.fetch(request(token, '/api/calls', {method: 'POST', body: '{}',
    headers: {'content-type': 'text/plain'}}), f.env), 'not_started', 415);
  assert.equal(f.backendCalls.length, 0);
});

test('request cap is measured in bytes and cannot be evaded by a short declared length', async () => {
  const f = setup(), token = await sign();
  const exactBody = '{"x":"' + 'x'.repeat(LIMITS.requestBytes - 8) + '"}';
  assert.equal(Buffer.byteLength(exactBody), LIMITS.requestBytes);
  assert.equal((await f.panel.fetch(request(token, '/api/calls', {method: 'POST', body: exactBody}), f.env)).status, 200);
  await refused(await f.panel.fetch(request(token, '/api/calls', {method: 'POST', body: exactBody + ' ',
    headers: {'content-length': '1'}}), f.env), 'not_started', 400);
  await refused(await f.panel.fetch(request(token, '/api/calls', {method: 'POST', body: '{"x":"' + 'é'.repeat(140000) + '"}'}), f.env), 'not_started', 400);
  assert.equal(f.backendCalls.length, 1);
});

test('response cap supports unchanged Worker maximum, with exact-size success and oversize hold', async () => {
  const exactBytes = new Uint8Array(LIMITS.responseBytes).fill(32);
  const f = setup({backend: () => new Response(exactBytes, {headers: {'content-type': 'application/json'}})});
  assert.equal((await (await f.panel.fetch(request(await sign()), f.env)).arrayBuffer()).byteLength, LIMITS.responseBytes);
  let cancelled = false;
  const g = setup({backend: () => new Response(new ReadableStream({start(controller) {
    controller.enqueue(new Uint8Array(LIMITS.responseBytes)); controller.enqueue(new Uint8Array([32]));
  }, cancel() { cancelled = true; }}), {headers: {'content-type': 'application/json', 'content-length': '1'}})});
  await refused(await g.panel.fetch(request(await sign()), g.env), 'unconfirmed', 502);
  assert.equal(cancelled, true);
});

test('backend redirects, non-JSON, declared oversize and streaming failures remain unknown for all custody routes', async () => {
  const token = await sign();
  for (const backend of [() => Response.redirect('https://attacker.invalid', 302),
    () => new Response('<html>not JSON</html>'),
    () => new Response('{}', {headers: {'content-type': 'application/json', 'content-length': String(LIMITS.responseBytes + 1)}}),
    () => new Response(new ReadableStream({start(c) { c.error(Error('synthetic private detail')); }}), {headers: {'content-type': 'application/json'}}),
    () => { throw Error('secret upstream transport detail'); }]) {
    const f = setup({backend});
    for (const path of ['/api/status', '/api/tools', '/api/events', '/api/runs']) {
      const body = await refused(await f.panel.fetch(request(token, path), f.env), 'unconfirmed', 502);
      assert.equal(JSON.stringify(body).includes('secret'), false);
    }
    await refused(await f.panel.fetch(request(token, '/api/readiness'), f.env), 'not_started', 502);
  }
});

test('abort before admission never dispatches; abort after backend call preserves uncertainty', async () => {
  const token = await sign(), early = new AbortController(), first = setup(); early.abort();
  await refused(await first.panel.fetch(request(token, '/api/tools', {signal: early.signal}), first.env), 'not_started', 401);
  assert.equal(first.backendCalls.length, 0);
  for (const path of ['/api/tools', '/api/readiness']) {
    const controller = new AbortController(); let observedAbort = false;
    const f = setup({backend: request => new Promise((_, reject) => {
      request.signal.addEventListener('abort', () => { observedAbort = true; reject(Error('aborted')); }, {once: true});
      controller.abort();
    })});
    await refused(await f.panel.fetch(request(token, path, {signal: controller.signal}), f.env), path === '/api/readiness' ? 'not_started' : 'unconfirmed', 502);
    assert.equal(observedAbort, true); assert.equal(f.backendCalls.length, 1);
  }
});

test('abort while reading backend body cancels that stream without retry', async () => {
  const controller = new AbortController(); let cancelled = false;
  const f = setup({backend: () => new Response(new ReadableStream({pull() { controller.abort(); },
    cancel() { cancelled = true; }}), {headers: {'content-type': 'application/json'}})});
  await refused(await f.panel.fetch(request(await sign(), '/api/events', {signal: controller.signal}), f.env), 'unconfirmed', 502);
  assert.equal(cancelled, true); assert.equal(f.backendCalls.length, 1);
});

test('JWKS failures are bounded fail-closed and never followed to a caller-selected URL', async () => {
  const token = await sign();
  for (const keys of [() => Response.redirect('https://attacker.invalid', 302),
    () => new Response('bad json', {headers: {'content-type': 'application/json'}}),
    () => Response.json({keys: [jwk, jwk]}),
    () => Response.json({keys: [{...jwk, alg: 'HS256'}]}),
    () => Response.json({keys: [{...jwk, n: b64(new Uint8Array(128).fill(255))}]}),
    () => Response.json({keys: [{...jwk, d: 'private-part'}]}),
    () => Response.json({keys: Array.from({length: 9}, (_, i) => ({...jwk, kid: 'key-' + i}))}),
    () => new Response('x'.repeat(ACCESS_LIMITS.jwksBytes + 1), {headers: {'content-type': 'application/json'}}),
    () => { throw Error('secret network error'); }]) {
    const f = setup({keys});
    const body = await refused(await f.panel.fetch(request(token), f.env), 'not_started', 401);
    assert.equal(JSON.stringify(body).includes('secret'), false);
    assert.equal(f.backendCalls.length, 0); assert.equal(f.keyCalls.length, 1);
    assert.equal(f.keyCalls[0].url, 'https://' + ISSUER + '/cdn-cgi/access/certs');
  }
});

test('JWKS cache is single-flight, cooldown-bounded and never falls back to expired keys', async () => {
  let fail = false;
  const f = setup({keys: async () => { await new Promise(resolve => setTimeout(resolve, 5));
    if (fail) throw Error('synthetic outage'); return Response.json({keys: [jwk]}); }});
  const token = await sign();
  const responses = await Promise.all(Array.from({length: 6}, () => f.panel.fetch(request(token), f.env)));
  assert(responses.every(response => response.status === 200)); assert.equal(f.keyCalls.length, 1);
  for (let i = 0; i < 4; i++) await refused(await f.panel.fetch(request(await sign({}, {kid: 'unknown-' + i})), f.env), 'not_started', 401);
  assert.equal(f.keyCalls.length, 1);
  fail = true; f.advance(ACCESS_LIMITS.cacheMs + 1);
  await refused(await f.panel.fetch(request(token), f.env), 'not_started', 401);
  await refused(await f.panel.fetch(request(token), f.env), 'not_started', 401);
  assert.equal(f.keyCalls.length, 2); assert.equal(f.backendCalls.length, 6);
});

test('real JWKS deadline aborts the fetch and refuses without a backend call', async () => {
  let signal, aborted;
  const wasAborted = new Promise(resolve => { aborted = resolve; });
  const f = setup({keys: request => {
    signal = request.signal;
    signal.addEventListener('abort', aborted, {once: true});
    return new Promise(() => {});
  }});
  const start = performance.now();
  await refused(await f.panel.fetch(request(await sign()), f.env), 'not_started', 401);
  await wasAborted;
  assert.equal(signal.aborted, true);
  assert(performance.now() - start >= ACCESS_LIMITS.fetchMs - 50);
  assert.equal(f.keyCalls.length, 1); assert.equal(f.backendCalls.length, 0);
});

test('real upload deadline cancels a stalled body before dispatch', async () => {
  let cancelled = false;
  const f = setup(), body = new ReadableStream({cancel() { cancelled = true; }});
  const start = performance.now();
  await refused(await f.panel.fetch(request(await sign(), '/api/calls', {method: 'POST', body, duplex: 'half'}), f.env), 'not_started', 400);
  assert(performance.now() - start >= LIMITS.requestMs - 50);
  assert.equal(cancelled, true); assert.equal(f.backendCalls.length, 0);
});

test('invalid constructor assets are rejected rather than exposing extra unauthenticated routes', () => {
  for (const invalid of [{'/api/tools': assets['/']}, {'/../outside.html': assets['/']},
    {'/': {...assets['/'], header: 'arbitrary'}}, {'/': {...assets['/'], contentType: 'application/octet-stream'}},
    {'/': {...assets['/'], body: 'x'.repeat(LIMITS.assetBytes + 1)}}])
    assert.throws(() => createPanel({assets: invalid}));
});
