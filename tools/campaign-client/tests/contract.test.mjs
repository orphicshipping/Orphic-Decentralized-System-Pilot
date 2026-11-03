import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

if (!process.env.ODSP_CLIENT_BUILD) throw Error('Set ODSP_CLIENT_BUILD to the strict compiled build directory');
const { ContractError, LIMITS, parseJson, encodeJson, validateRequest, validateResult, ReadClient } =
  await import(pathToFileURL(resolve(process.env.ODSP_CLIENT_BUILD, 'contract.js')));
const { TOOL_METADATA } = await import(pathToFileURL(resolve(process.env.ODSP_CLIENT_BUILD, 'metadata.js')));
const bytes = text => new TextEncoder().encode(text);
const request = { name:'ncp_intake_record', arguments:{ record_id:'opc-1' } };
const envelope = data => ({ status:'ok', data });
function rpc(value=envelope({ claims:{authorizes_execution:false}, receipt_digest:'sha256:'+'a'.repeat(64) }), id='read-1') {
  return { jsonrpc:'2.0', id, result:{ content:[{type:'text',text:JSON.stringify(value)}], structuredContent:value, isError:value.status!=='ok' } };
}
const wire = value => bytes(JSON.stringify(value));
async function* chunks(raw) { for (let i=0;i<raw.length;i+=4096) yield raw.subarray(i,i+4096); }

test('raw duplicate keys including escaped aliases refuse before loss', () => {
  for (const raw of ['{"id":1,"id":2}', '{"id":1,"\\u0069d":2}', '{"a":{"x":1,"x":2}}'])
    assert.throws(()=>parseJson(bytes(raw)), /duplicate_json_key/);
  const result=parseJson(bytes('{"__proto__":{"polluted":true}}'));
  assert.equal(Object.getPrototypeOf(result),null); assert.equal({}.polluted,undefined);
});
test('invalid Unicode, malformed frames and numeric limits refuse', () => {
  for(const raw of ['"\\ud800"','NaN','Infinity','1e309','1e-999','9007199254740992','1 2','{"x":1,}','[1,]','\ufeff{}'])
    assert.throws(()=>parseJson(bytes(raw)),ContractError);
  assert.throws(()=>parseJson(Uint8Array.of(0xff)),/invalid_utf8/);
  assert.throws(()=>parseJson(bytes('['.repeat(26)+'0'+']'.repeat(26))),/complexity_limit/);
  assert.equal(parseJson(bytes('1e-7')),1e-7); assert.equal(parseJson(bytes('"\\ud83d\\ude00"')),'😀');
  assert.equal(Object.is(parseJson(bytes('-0.0')),-0),true);
});
test('caller object bounds reject accessors, cycles and unsupported numbers', () => {
  let read=false; const value={};Object.defineProperty(value,'x',{enumerable:true,get(){read=true;return 1;}});
  assert.throws(()=>encodeJson(value),/json_accessor/); assert.equal(read,false);
  const cycle={}; cycle.self=cycle; assert.throws(()=>encodeJson(cycle),/complexity_limit/);
  for(const item of [NaN,Infinity,9007199254740992,'\ud800',Array(3)]) assert.throws(()=>encodeJson(item),ContractError);
});
test('allowlist is exactly ten backend read contracts and no write hint', () => {
  assert.equal(Object.keys(TOOL_METADATA).length,10);
  for(const value of Object.values(TOOL_METADATA)) {
    assert.deepEqual(value.annotations,{readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:false});
    assert.equal(value.inputSchema.additionalProperties,false);
  }
  assert.equal(validateRequest(request).name,request.name);
  for(const value of [{name:'ncp_run_submit',arguments:{}},{name:'ncp_coordinate',arguments:{action:'recover'}},
    {name:'ncp_intake_record',arguments:{record_id:'x',principal:'other'}},
    {name:'ncp_intake_records',arguments:{limit:true}}, {name:'ncp_domain_read_list',arguments:{domain:'wrong'}},
    {name:'ncp_proof_verify',arguments:{request_json:'{}'}}, {name:'ncp_intake_describe',arguments:{endpoint:'x'}}])
    assert.throws(()=>validateRequest(value),ContractError);
});
test('request and whole RPC byte budgets include Unicode and envelope', () => {
  assert.throws(()=>encodeJson({value:'😀'.repeat(20000)}),/byte_limit/);
  assert.throws(()=>validateRequest({name:'ncp_intake_verify',arguments:{record_id:'opc-1',receipt_json:'x'.repeat(32769)}}),/string_limit/);
  const value=rpc(envelope({many:Array(50).fill('x'.repeat(3000))}));
  assert.ok(wire(value).length>LIMITS.responseBytes); assert.throws(()=>validateResult(wire(value),request,'read-1'),/byte_limit/);
});
test('full duplicated text and structured content must agree', () => {
  const value=rpc();value.result.content[0].text='{"status":"ok","data":{"receipt_digest":"forged"}}';
  assert.throws(()=>validateResult(wire(value),request,'read-1'),/mcp_content_mismatch/);
  value.result.content[0].text='{"status":"ok","status":"ok","data":{}}';
  assert.throws(()=>validateResult(wire(value),request,'read-1'),/duplicate_json_key/);
  const good=rpc(envelope({a:1,b:2}));good.result.content[0].text=' {"data":{"b":2,"a":1},"status":"ok"} ';
  assert.equal(validateResult(wire(good),request,'read-1').state,'returned');
  good.result.resultType='complete';assert.equal(validateResult(wire(good),request,'read-1').state,'returned');
  good.result.resultType='task';assert.throws(()=>validateResult(wire(good),request,'read-1'),/mcp_result_type/);
});
test('exact received JSON and backend text preserve numeric spelling', () => {
  const backend='{"status":"ok","data":{"negative_zero":-0.0,"one":1.0}}';
  const value={jsonrpc:'2.0',id:'read-1',result:{content:[{type:'text',text:backend}],structuredContent:'PLACEHOLDER',isError:false}};
  const raw=JSON.stringify(value).replace('"PLACEHOLDER"',backend);
  const result=validateResult(bytes(raw),request,'read-1');
  assert.equal(result.rawJson,raw);assert.equal(result.backendText,backend);
  assert.equal(Object.is(result.data.negative_zero,-0),true);
  assert.match(result.rawJson,/"one":1\.0/);
});
test('duplicate/mismatched IDs, malformed result, status and authority refuse', () => {
  assert.throws(()=>validateResult(wire(rpc(undefined,'old')),request,'read-1'),/rpc_identity/);
  assert.throws(()=>validateResult(bytes(JSON.stringify(rpc()).replace('"id":"read-1"','"id":"read-1","id":"read-1"')),request,'read-1'),/duplicate_json_key/);
  const mismatch=rpc();mismatch.result.isError=true;assert.throws(()=>validateResult(wire(mismatch),request,'read-1'),/mcp_status_mismatch/);
  assert.throws(()=>validateResult(wire(rpc(envelope({authorizes_execution:true}))),request,'read-1'),/unexpected_authority_claim/);
  assert.throws(()=>validateResult(wire(rpc({status:'ok',data:null})),request,'read-1'),/backend_success_data/);
  const extra=rpc();extra.result.content.push({type:'text',text:'ignored'});assert.throws(()=>validateResult(wire(extra),request,'read-1'),ContractError);
});
test('digest IDs alone never earn a backend verification claim', () => {
  assert.match(validateResult(wire(rpc(envelope({verified:true,receipt_digest:'sha256:'+'a'.repeat(64)}))),request,'read-1').verification,/displayed only/);
  const verification={name:'ncp_intake_verify',arguments:{record_id:'opc-1',receipt_json:'{}'}};
  assert.match(validateResult(wire(rpc(envelope({profile:'odsp.offline-intake.v1',outcome:'matched_host_capture'}))),verification,'read-1').verification,/Backend matched/);
});
test('denied, unknown, held, partial and empty states remain distinct', () => {
  for(const [value,state] of [[{status:'refused',reason:'principal_policy_refused'},'denied'],
    [{status:'refused',error:{code:'outcome_unknown'}},'unknown'],[{status:'held',data:{reason:'runtime'}},'held'],
    [{status:'failed'},'failed'],[{status:'unsupported'},'unsupported'],[{status:'stale'},'stale'],
    [envelope({items:[1],next_offset:1}),'partial'],[envelope({items:[],next_offset:null}),'empty']])
    assert.equal(validateResult(wire(rpc(value)),request,'read-1').state,state);
});
test('client sends one exact read request with unique IDs and no endpoint', async () => {
  const seen=[];const client=new ReadClient(async function*(raw){const r=parseJson(raw);seen.push(r);yield* chunks(wire(rpc(undefined,r.id)));});
  assert.equal((await client.read(request)).state,'returned');assert.match((await client.read(request)).requestId,/:2$/);
  assert.equal(seen[0].method,'tools/call'); assert.deepEqual(JSON.parse(JSON.stringify(seen[0].params)),request);
  const count=seen.length;await assert.rejects(client.read({name:'ncp_run_cancel',arguments:{}}),/operation_not_allowed/);assert.equal(seen.length,count);
  const other=new ReadClient(async function*(raw){yield* chunks(wire(rpc(undefined,parseJson(raw).id)));});
  assert.notEqual((await other.read(request)).requestId,seen[0].id);
});
test('concurrent request denied and timeout fences without automatic retry', async () => {
  let calls=0,aborted=false;
  const client=new ReadClient(async function*(_raw,signal){calls++;await new Promise(resolve=>signal.addEventListener('abort',()=>{aborted=true;resolve();},{once:true}));},20);
  const pending=client.read(request);await assert.rejects(client.read(request),/client_unavailable/);
  await assert.rejects(pending,/transport_timeout_unknown/);assert.equal(aborted,true);
  await assert.rejects(client.read(request),/client_unavailable/);assert.equal(calls,1);
});
test('streaming response limits and stale responses fence the client', async () => {
  for(const transport of [async function*(){yield new Uint8Array(32769);},
    async function*(){for(let i=0;i<1025;i++)yield Uint8Array.of(32);},async function*(){yield* chunks(wire(rpc(undefined,'stale')));}]) {
    const client=new ReadClient(transport);await assert.rejects(client.read(request),ContractError);
    await assert.rejects(client.read(request),/client_unavailable/);
  }
});
test('bounded deterministic raw mutation corpus', () => {
  for(let i=0;i<128;i++) {
    const key='field'+i;assert.throws(()=>parseJson(bytes(`{"${key}":${i},"${key}":${i+1}}`)),/duplicate_json_key/);
  }
});
