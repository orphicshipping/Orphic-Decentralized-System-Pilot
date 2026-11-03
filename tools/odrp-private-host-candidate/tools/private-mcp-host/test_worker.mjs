// Real Worker source and peer gateway/release helpers, local persistent substitutes.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {ODRP_READ_NAMES} from './gateway.mjs';
import {DiskStorage,FileBucket,environment,request,container,loadWorker,fixtureCredential} from './storage_fixture.mjs';
import {configuration,digest,snapshot,exportBackup,restoreBackup,publish} from './storage.mjs';
const NCPHost=await loadWorker();
async function fixture(t,overrides={},handler){
  const dir=await mkdtemp(join(tmpdir(),'odsp-storage-'));t.after(()=>rm(dir,{recursive:true,force:true}));
  const storage=new DiskStorage(join(dir,'state.sqlite')),bucket=new FileBucket(join(dir,'objects'));
  const env=environment(bucket,overrides),native=container(env,handler),ctx={storage,container:native};
  return {dir,storage,bucket,env,native,ctx,host:new NCPHost(ctx,env),config:configuration(env)};
}
const parsed=async response=>({status:response.status,body:await response.json()});

test('Worker confirmation list exactly matches real ControlHost annotations and ODRP source',async()=>{
  const program=`import json, sys
from pathlib import Path
root=Path(sys.argv[1])
if (root/'src/ciw/control_gateway.py').is_file(): sys.path.insert(0,str(root/'src'))
from ciw.control_gateway import ControlHost
from ciw.reference_profile import REFERENCE_NAMES
rows=ControlHost.descriptions(object.__new__(ControlHost))
print(json.dumps({'control':sorted(row['name'] for row in rows if row['annotations']['readOnlyHint'] is True),'reference':sorted(REFERENCE_NAMES)}))`;
  const expected=JSON.parse(execFileSync(process.env.PYTHON||'python',['-B','-c',program,
    fileURLToPath(new URL('../../../../',import.meta.url))],{encoding:'utf8',timeout:10000}));
  const source=await readFile(new URL('./worker.mjs',import.meta.url),'utf8');
  const declaration=source.match(/const READ_NAMES = new Set\(\[([\s\S]*?)\]\);/);
  assert.ok(declaration,'A static confirmation list must be explicitly reviewed against the source catalog');
  const literals=[...declaration[1].matchAll(/'([^']+)'/g)].map(match=>match[1]);
  assert.equal(declaration[1].replace(/'[^']+'/g,'').replace(/\.\.\.ODRP_READ_NAMES/g,'').replace(/[\s,]/g,''),'');
  assert.deepEqual([...ODRP_READ_NAMES].sort(),expected.reference);
  const actual=[...literals,...ODRP_READ_NAMES];assert.equal(new Set(actual).size,actual.length);
  assert.deepEqual(actual.sort(),[...expected.control,...expected.reference].sort());
});
test('existing retained/domain/workbench/index reads reach the host without write confirmation',async t=>{
  const f=await fixture(t);
  for(const [name,args] of [['ncp_retained_read',{artifact_id:'retained-synthetic'}],['ncp_domain_list',{}],
    ['ncp_workbench_list',{}],['ncp_index_list',{}]]){
    const result=await parsed(await f.host.fetch(await request('/api/calls',{name,arguments:args})));
    assert.equal(result.status,200,name);
  }
  assert.equal(f.native.calls.filter(url=>url.endsWith('/transaction')).length,4);
});
test('read confirmation classification cannot override a host refusal or confirm a write',async t=>{
  const f=await fixture(t,{},async()=>Response.json({error:{code:'principal_policy_refused'}},{status:403}));
  const unconfirmed=await parsed(await f.host.fetch(await request('/api/calls',{name:'ncp_domain_run',arguments:{}})));
  assert.equal(unconfirmed.status,409);assert.equal(unconfirmed.body.error.code,'confirmation_required');
  assert.equal(f.native.calls.length,0);
  const read=await parsed(await f.host.fetch(await request('/api/calls',{name:'ncp_domain_list',arguments:{}})));
  assert.equal(read.status,503);assert.equal(read.body.error.code,'outcome_unknown');assert.equal(f.bucket.puts,0);
  assert.ok(await f.storage.get('pending'));
});

test('missing trusted configuration and forged internal principal refuse before startup',async t=>{
  const f=await fixture(t);delete f.env.ODSP_STORAGE_POLICY;
  assert.equal((await f.host.fetch(await request())).status,403);assert.equal(f.native.calls.length,0);
  f.env.ODSP_STORAGE_POLICY=JSON.stringify(f.config);
  assert.equal((await f.host.fetch(new Request('https://synthetic.invalid/api/tools'))).status,403);
  assert.equal(await f.storage.get('control'),undefined);
});
test('unknown fence survives new Worker and prevents startup or mutation',async t=>{
  const f=await fixture(t);await f.storage.put('pending','historical-transaction');
  const r=await parsed(await new NCPHost(f.ctx,f.env).fetch(await request()));
  assert.equal(r.body.error.code,'outcome_unknown');assert.equal(f.native.calls.length,0);
  assert.equal(await f.storage.get('pending'),'historical-transaction');
});
test('historical checkpoint is never reinitialized',async t=>{
  const f=await fixture(t);await f.storage.put('committed',{key:'old'});
  const r=await parsed(await f.host.fetch(await request()));
  assert.match(r.body.error.code,/checkpoint|migration/);assert.equal(f.native.calls.length,0);
  assert.deepEqual(await f.storage.get('committed'),{key:'old'});
});
test('success persists request/checkpoint/response evidence then clears only its own fence',async t=>{
  const f=await fixture(t),r=await parsed(await f.host.fetch(await request()));assert.equal(r.status,200);
  const saved=await snapshot(f.storage,f.config);assert.equal(saved.pending,null);assert.equal(saved.head.revision,1);
  assert.equal(saved.committed.transaction_id,r.body.retention.transaction_id);
  const receipt=await f.storage.get('receipt:'+saved.committed.transaction_id);
  assert.equal(receipt.phase,'committed');assert.equal(receipt.identity.principal,'alice');
  assert.equal(receipt.identity.request_sha256,saved.committed.request_sha256);
  assert.equal(saved.head.bytes,saved.committed.bytes);assert.equal(saved.head.objects,1);
  assert.equal(f.bucket.puts,1);
});
test('release health mismatch refuses before durable admission',async t=>{
  const f=await fixture(t);f.native.getTcpPort=()=>({fetch:async()=>Response.json({ready:true,owner_id:f.config.owner_id,checkpoint_generation:f.config.generation,release:{}})});
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'host_identity_mismatch');
  assert.equal(await f.storage.get('pending'),undefined);assert.equal(f.bucket.puts,0);
});
test('unconfirmed execution failure leaves fence, no automatic second execution',async t=>{
  let calls=0;const f=await fixture(t,{},async()=>{calls++;throw Error('process lost');});
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'outcome_unknown');
  assert.equal(r.body.error.uncertainty_hold_confirmed,true);assert.ok(await f.storage.get('pending'));
  await new NCPHost(f.ctx,f.env).fetch(await request());assert.equal(calls,1);assert.equal(await f.storage.get('committed'),undefined);
});
test('failure before or after object publication preserves uncertainty and all uploaded bytes',async t=>{
  for(const when of ['beforePut','afterPut']){
    const f=await fixture(t);f.bucket[when]=async()=>{throw Error('synthetic bucket failure');};
    const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'outcome_unknown');
    const pending=await f.storage.get('pending');assert.ok(pending);assert.equal(await f.storage.get('committed'),undefined);
    const object=await f.bucket.get(`checkpoints/${f.config.owner_id}/${pending.transaction.id}.tar.gz.base64`);
    assert.equal(Boolean(object),when==='afterPut');assert.equal((await f.storage.get('control')).bytes,f.config.max_checkpoint_bytes);
  }
});
test('commit-after-effect exception preserves committed pointer and installs matching uncertainty hold',async t=>{
  const f=await fixture(t);let injected=false;
  f.storage.afterCommit=async(old,value)=>{if(!injected&&value.committed&&!old.committed){injected=true;throw Error('lost commit reply');}};
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'committed_response_unconfirmed');
  const saved=await snapshot(f.storage,f.config);assert.equal(saved.pending.phase,'committed_response_unconfirmed');
  assert.equal(saved.committed.transaction_id,saved.pending.transaction.id);
  assert.equal((await f.storage.get('receipt:'+saved.committed.transaction_id)).phase,'committed');
  const before=f.native.calls.length;await f.host.fetch(await request());assert.equal(f.native.calls.length,before);
});
test('audit/admission persistence failure never authorizes container work',async t=>{
  const f=await fixture(t);f.storage.beforeCommit=async()=>{throw Error('durable receipt unavailable');};
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.status,503);
  assert.equal(f.native.calls.filter(x=>x.endsWith('/transaction')).length,0);
  assert.equal(await f.storage.get('committed'),undefined);assert.equal(f.bucket.puts,0);
});
test('bounded container timeout aborts caller; late response cannot publish or reexecute',async t=>{
  let finish,signal;const f=await fixture(t,{transaction_ms:400,storage_ms:150},async(input,options)=>{
    signal=options.signal;return new Promise(resolve=>{finish=async()=>resolve(Response.json({result:{},status:200,state:'late',identity:input.identity,transaction:input.transaction,checkpoint_sha256:await digest('late')}));});
  });
  const start=Date.now(),r=await parsed(await f.host.fetch(await request()));assert.ok(Date.now()-start<1200);
  assert.ok(signal.aborted);assert.equal(r.status,503);assert.ok(await f.storage.get('pending'));
  await finish();await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(f.bucket.puts,0);assert.equal(await f.storage.get('committed'),undefined);
});
test('bounded response size and mismatched identity/digest refuse before checkpoint upload',async t=>{
  for(const mode of ['large','identity','transaction','digest']){
    const f=await fixture(t,{},async input=>{
      const state=mode==='large'?'x'.repeat(20000):'fixture';
      return Response.json({result:{},status:200,state,transaction:mode==='transaction'?{}:input.transaction,
        identity:mode==='identity'?{}:input.identity,checkpoint_sha256:mode==='digest'?'0'.repeat(64):await digest(state)});
    });
    assert.equal((await f.host.fetch(await request())).status,503);assert.equal(f.bucket.puts,0);assert.ok(await f.storage.get('pending'));
  }
});
test('finite queue rejects excess requests and rechecks current identity before execution',async t=>{
  let release;const f=await fixture(t,{queue_limit:1},async()=>new Promise(resolve=>{release=()=>resolve(Response.json({}));}));
  const first=f.host.fetch(await request());while(!release)await new Promise(resolve=>setTimeout(resolve,5));
  const second=f.host.fetch(await request());const third=await parsed(await f.host.fetch(await request()));
  assert.equal(third.status,429);assert.equal(third.body.error.effect_state,'not_started');
  release();await first;await second;assert.equal(f.native.calls.filter(x=>x.endsWith('/transaction')).length,1);
});
const changeCredential=(env,change)=>{
  const value=JSON.parse(env.PRINCIPAL_AUTH_JSON);change(value.principals[0]);env.PRINCIPAL_AUTH_JSON=JSON.stringify(value);
};
async function successfulContainerReply(input){
  const state='synthetic-credential-lifecycle-checkpoint';
  return Response.json({result:{data:{private_result:'synthetic-sensitive-result'}},status:200,state,
    transaction:input.transaction,identity:input.identity,checkpoint_sha256:await digest(state)});
}
async function waitUntil(predicate){
  const stop=Date.now()+2000;
  while(!predicate()){assert.ok(Date.now()<stop,'fixture reached the intended asynchronous boundary');await new Promise(resolve=>setTimeout(resolve,5));}
}
test('actual Worker queue refuses rotated and revoked credentials before a second container operation',async t=>{
  for(const change of [r=>r.credential_id='replacement',r=>r.token_sha256='a'.repeat(64),r=>r.enabled=false]){
    let release;
    const f=await fixture(t,{},input=>new Promise(resolve=>{release=async()=>resolve(await successfulContainerReply(input));}));
    const first=f.host.fetch(await request());await waitUntil(()=>release);
    const second=f.host.fetch(await request());await waitUntil(()=>f.host.queued===2);
    changeCredential(f.env,change);await release();
    const [completed,queued]=await Promise.all([first.then(parsed),second.then(parsed)]);
    assert.equal(completed.body.error.effect_state,'committed_response_unconfirmed');
    assert.equal(queued.body.error.effect_state,'not_started');
    assert.equal(f.native.calls.filter(url=>url.endsWith('/transaction')).length,1);
    assert.equal(f.bucket.puts,1);assert.ok(await f.storage.get('committed'));
    assert.ok(!JSON.stringify(completed.body).includes('synthetic-sensitive-result'));
  }
});
test('real credential expiry while in the Worker queue prevents execution and stale disclosure',async t=>{
  let release;
  const f=await fixture(t,{},input=>new Promise(resolve=>{release=async()=>resolve(await successfulContainerReply(input));}));
  const start=Math.floor(Date.now()/1000),credential={...fixtureCredential,not_before:start-1,expires_at:start+2};
  changeCredential(f.env,row=>Object.assign(row,credential));
  const first=f.host.fetch(await request('/api/tools',undefined,credential));await waitUntil(()=>release);
  const second=f.host.fetch(await request('/api/tools',undefined,credential));await waitUntil(()=>f.host.queued===2);
  await new Promise(resolve=>setTimeout(resolve,Math.max(0,credential.expires_at*1000-Date.now())+15));
  await release();const [completed,queued]=await Promise.all([first.then(parsed),second.then(parsed)]);
  assert.equal(queued.body.error.effect_state,'not_started');
  assert.equal(completed.body.error.effect_state,'committed_response_unconfirmed');
  assert.equal(f.native.calls.filter(url=>url.endsWith('/transaction')).length,1);
  assert.ok(await f.storage.get('committed'));assert.equal(f.bucket.puts,1);
});
test('credential rotation during health preflight refuses before durable admission',async t=>{
  const f=await fixture(t),getPort=f.native.getTcpPort.bind(f.native);
  f.native.getTcpPort=()=>{const port=getPort();return {async fetch(url,options){
    const result=await port.fetch(url,options);
    if(url.endsWith('/health'))changeCredential(f.env,row=>row.credential_id='after-health');
    return result;
  }};};
  const result=await parsed(await f.host.fetch(await request()));
  assert.equal(result.body.error.effect_state,'not_started');
  assert.equal(f.native.calls.filter(url=>url.endsWith('/transaction')).length,0);
  assert.equal(await f.storage.get('pending'),undefined);assert.equal(f.bucket.puts,0);
});
test('credential revocation after durable admission starts no container operation and preserves uncertainty',async t=>{
  const f=await fixture(t);let changed=false;
  f.storage.afterCommit=async(_,body)=>{
    if(!changed&&body.pending?.phase==='admitted'){changed=true;changeCredential(f.env,row=>row.enabled=false);}
  };
  const result=await parsed(await f.host.fetch(await request()));
  assert.ok(changed);assert.equal(result.body.error.effect_state,'unconfirmed');
  assert.equal(f.native.calls.filter(url=>url.endsWith('/transaction')).length,0);
  assert.equal((await f.storage.get('pending')).phase,'unconfirmed');
  assert.equal(await f.storage.get('committed'),undefined);assert.equal(f.bucket.puts,0);
});
test('credential rotation during commit suppresses disclosure while preserving committed bytes and receipt',async t=>{
  const f=await fixture(t,{},successfulContainerReply);let changed=false;
  f.storage.afterCommit=async(_,body)=>{
    if(!changed&&body.committed){changed=true;changeCredential(f.env,row=>row.credential_id='after-commit');}
  };
  const result=await parsed(await f.host.fetch(await request()));
  assert.ok(changed);assert.equal(result.body.error.effect_state,'committed_response_unconfirmed');
  assert.ok(!JSON.stringify(result.body).includes('synthetic-sensitive-result'));
  const pointer=await f.storage.get('committed');assert.ok(pointer);assert.ok(await f.bucket.get(pointer.key));
  assert.equal(f.native.calls.filter(url=>url.endsWith('/transaction')).length,1);
  assert.ok(await f.storage.get('receipt:'+pointer.transaction_id));
});
test('readiness rechecks credential after storage preflight without disclosing state',async t=>{
  const f=await fixture(t),original=f.storage.transaction.bind(f.storage);
  f.storage.transaction=async operation=>{
    const result=await original(operation);changeCredential(f.env,row=>row.enabled=false);return result;
  };
  const result=await parsed(await f.host.fetch(await request('/api/readiness')));
  assert.equal(result.body.error.effect_state,'not_started');assert.equal(Object.hasOwn(result.body,'storage_capacity'),false);
  assert.equal(f.native.calls.filter(url=>url.endsWith('/transaction')).length,0);assert.equal(f.bucket.puts,0);
});
test('checkpoint quota refuses before container execution and never deletes retained evidence',async t=>{
  const f=await fixture(t,{max_objects:1});assert.equal((await f.host.fetch(await request())).status,200);
  const committed=await f.storage.get('committed'),calls=f.native.calls.filter(x=>x.endsWith('/transaction')).length;
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'retention_capacity_exceeded');
  assert.equal(r.body.error.effect_state,'not_started');assert.equal(f.native.calls.filter(x=>x.endsWith('/transaction')).length,calls);
  assert.deepEqual(await f.storage.get('committed'),committed);assert.ok(await f.bucket.get(committed.key));
});
test('authenticated readiness never starts container, checkpoints or clears pending',async t=>{
  const f=await fixture(t);await f.storage.put('pending','uncertain');f.native.running=false;
  const before=await readFile(f.storage.path);const r=await parsed(await f.host.fetch(await request('/api/readiness')));
  assert.equal(r.status,200);assert.equal(r.body.fenced,true);assert.equal(r.body.checkpoint_written,false);
  assert.equal(f.native.running,false);assert.equal(f.native.calls.length,0);assert.equal(f.bucket.puts,0);
  assert.deepEqual(await readFile(f.storage.path),before);assert.equal(await f.storage.get('pending'),'uncertain');
});
test('owned backup validates bytes and owner; restore bumps external epoch and fences old live Worker',async t=>{
  const f=await fixture(t);assert.equal((await f.host.fetch(await request())).status,200);
  const backup=await exportBackup(f.storage,f.bucket,f.config),old=await f.storage.get('committed');
  for(const bad of [{...backup,owner_id:'foreign'},{...backup,generation:'different'},{...backup,state:'damaged'}])
    await assert.rejects(restoreBackup(f.storage,f.bucket,f.config,bad,1));
  const result=await restoreBackup(f.storage,f.bucket,f.config,backup,1);assert.equal(result.epoch,2);
  const restored=await f.storage.get('committed');assert.equal(restored.transaction_id,old.transaction_id);
  const refused=await parsed(await f.host.fetch(await request()));assert.equal(refused.body.error.code,'stale_writer');
  await assert.rejects(publish(f.storage,f.config,{...(await f.storage.get('receipt:'+old.transaction_id)).transaction},old,Date.now()+1000),/stale_writer/);
  assert.deepEqual(await f.storage.get('committed'),restored);
  assert.equal((await new NCPHost(f.ctx,f.env).fetch(await request())).status,200);
});
test('backup and restore refuse pending state without touching it',async t=>{
  const f=await fixture(t);await f.host.fetch(await request());const backup=await exportBackup(f.storage,f.bucket,f.config);
  await f.storage.put('pending','uncertain');const committed=await f.storage.get('committed');
  await assert.rejects(exportBackup(f.storage,f.bucket,f.config),/outcome_unknown/);
  await assert.rejects(restoreBackup(f.storage,f.bucket,f.config,backup,1),/outcome_unknown/);
  assert.equal(await f.storage.get('pending'),'uncertain');assert.deepEqual(await f.storage.get('committed'),committed);
});

test('admission error after its durable effect keeps audit receipt but never executes',async t=>{
  const f=await fixture(t);let injected=false;
  f.storage.afterCommit=async(old,value)=>{if(!injected&&!old.pending&&value.pending){injected=true;throw Error('admission acknowledgement lost');}};
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'outcome_unknown');
  const pending=await f.storage.get('pending');assert.ok(pending);assert.ok(await f.storage.get('receipt:'+pending.transaction.id));
  assert.equal(f.native.calls.filter(x=>x.endsWith('/transaction')).length,0);
});
test('hung object read is a bounded pre-effect refusal and preserves committed pointer',async t=>{
  const f=await fixture(t,{storage_ms:150});await f.host.fetch(await request());const saved=await f.storage.get('committed');
  f.bucket.get=()=>new Promise(()=>{});
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.effect_state,'not_started');
  assert.deepEqual(await f.storage.get('committed'),saved);assert.equal(await f.storage.get('pending'),undefined);
});
test('late R2 publication after timeout cannot advance pointer or erase uncertainty',async t=>{
  const f=await fixture(t,{storage_ms:150});let finish;
  f.bucket.afterPut=()=>new Promise(resolve=>{finish=resolve;});
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'outcome_unknown');
  const pending=await f.storage.get('pending');assert.ok(pending);assert.equal(f.bucket.puts,1);
  finish();await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(await f.storage.get('committed'),undefined);assert.deepEqual(await f.storage.get('pending'),pending);
});
test('commit timeout after durable effect retains receipt and late completion cannot clear its hold',{timeout:10000},async t=>{
  const f=await fixture(t,{storage_ms:150});let finish,injected=false,resolveCommitted;
  const committed=new Promise(resolve=>{resolveCommitted=resolve;});
  f.storage.afterCommit=async(old,value)=>{if(!injected&&value.committed&&!old.committed){injected=true;resolveCommitted();await new Promise(resolve=>{finish=resolve;});}};
  // Persist the intended boundary before advancing its deadline. Other tests
  // retain real elapsed-time deadlines; slow fixture I/O cannot select another
  // phase in this exact committed-receipt regression.
  t.mock.timers.enable({apis:['Date','setTimeout'],now:Date.now()});
  try{
    const pendingResponse=f.host.fetch(await request());await committed;
    assert.equal(injected,true);
    const durable=await snapshot(f.storage,f.config);assert.ok(durable.committed);assert.equal(durable.pending,null);
    const receipt=await f.storage.get('receipt:'+durable.committed.transaction_id);
    assert.equal(receipt.phase,'committed');assert.equal(receipt.checkpoint.sha256,durable.committed.sha256);
    assert.equal(f.native.calls.filter(x=>x.endsWith('/transaction')).length,1);assert.equal(f.bucket.puts,1);
    t.mock.timers.tick(151);
    const r=await parsed(await pendingResponse);assert.equal(r.body.error.code,'committed_response_unconfirmed');
    assert.equal(r.body.error.uncertainty_hold_confirmed,true);
    const saved=await snapshot(f.storage,f.config);assert.deepEqual(saved.committed,durable.committed);
    assert.equal(saved.pending.phase,'committed_response_unconfirmed');
    finish();await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(await snapshot(f.storage,f.config),saved);
    assert.equal((await f.storage.get('receipt:'+durable.committed.transaction_id)).phase,'committed');
  }finally{finish?.();t.mock.timers.reset();}
});
test('uncertainty diagnostic cannot fence or overwrite a newer owner epoch',async t=>{
  const f=await fixture(t);await f.host.fetch(await request());const pointer=await f.storage.get('committed');
  const binding=(await f.storage.get('receipt:'+pointer.transaction_id)).transaction;
  const backup=await exportBackup(f.storage,f.bucket,f.config);await restoreBackup(f.storage,f.bucket,f.config,backup,1);
  const {holdUnconfirmed}=await import('./storage.mjs');const before=await snapshot(f.storage,f.config);
  assert.deepEqual(await holdUnconfirmed(f.storage,f.config,binding),{outcome:'unconfirmed',hold:false});
  assert.deepEqual(await snapshot(f.storage,f.config),before);
});

test('stale backup cannot roll back later completed attempt identities',async t=>{
  const f=await fixture(t);await f.host.fetch(await request());const backup=await exportBackup(f.storage,f.bucket,f.config);
  await f.host.fetch(await request());const before=await snapshot(f.storage,f.config),puts=f.bucket.puts;
  await assert.rejects(restoreBackup(f.storage,f.bucket,f.config,backup,1),/stale_backup_refused/);
  assert.deepEqual(await snapshot(f.storage,f.config),before);assert.equal(f.bucket.puts,puts);
});
test('storage admission independently refuses foreign deployment binding before writes',async t=>{
  const f=await fixture(t),{admit}=await import('./storage.mjs');
  const binding={id:crypto.randomUUID(),owner_id:'foreign',generation:f.config.generation,epoch:1,
    base_commit:null,release:JSON.parse(f.env.ODSP_RELEASE_IDENTITY)};
  await assert.rejects(admit(f.storage,f.config,binding,{}, {head:null,committed:null},Date.now()+1000),/transaction_binding_mismatch/);
  assert.equal(await f.storage.get('control'),undefined);assert.equal(await f.storage.get('pending'),undefined);
});

test('readiness has its own single-flight bound without blocking or starting operations',async t=>{
  const f=await fixture(t);let release;
  f.native.getTcpPort=()=>({fetch:()=>new Promise(resolve=>{release=()=>resolve(Response.json({ready:true,
    owner_id:f.config.owner_id,checkpoint_generation:f.config.generation,release:JSON.parse(f.env.ODSP_RELEASE_IDENTITY)}));})});
  const first=f.host.fetch(await request('/api/readiness'));while(!release)await new Promise(resolve=>setTimeout(resolve,5));
  const second=await parsed(await f.host.fetch(await request('/api/readiness')));assert.equal(second.body.error.code,'readiness_busy');
  release();assert.equal((await first).status,200);assert.equal(await f.storage.get('control'),undefined);
});

test('readiness refuses cached stale epoch without refreshing it or changing durable state',async t=>{
  const f=await fixture(t);assert.equal((await f.host.fetch(await request())).status,200);
  const backup=await exportBackup(f.storage,f.bucket,f.config);await restoreBackup(f.storage,f.bucket,f.config,backup,1);
  const before=await snapshot(f.storage,f.config),database=await readFile(f.storage.path),puts=f.bucket.puts,calls=f.native.calls.length;
  assert.equal(f.host.writerEpoch,1);
  const stale=await parsed(await f.host.fetch(await request('/api/readiness')));
  assert.equal(stale.status,200);assert.equal(stale.body.ready,false);assert.equal(stale.body.reason,'stale_writer');
  assert.equal(stale.body.epoch,2);assert.equal(stale.body.storage_capacity.status,'available');
  assert.equal(f.host.writerEpoch,1);assert.equal(f.native.calls.length,calls);assert.equal(f.bucket.puts,puts);
  assert.deepEqual(await snapshot(f.storage,f.config),before);assert.deepEqual(await readFile(f.storage.path),database);
  const fresh=new NCPHost(f.ctx,f.env),valid=await parsed(await fresh.fetch(await request('/api/readiness')));
  assert.equal(valid.body.ready,true);assert.equal(valid.body.reason,null);assert.equal(valid.body.epoch,2);
  assert.equal(fresh.writerEpoch,null);assert.equal(f.host.writerEpoch,1);assert.equal(f.bucket.puts,puts);
  assert.deepEqual(await snapshot(f.storage,f.config),before);assert.deepEqual(await readFile(f.storage.path),database);
});
test('readiness reports object or byte exhaustion and preserves the full store without waking container',async t=>{
  for(const limits of [{max_objects:1},{max_total_bytes:4096}]){
    const f=await fixture(t,limits);assert.equal((await f.host.fetch(await request())).status,200);
    const before=await snapshot(f.storage,f.config),database=await readFile(f.storage.path),puts=f.bucket.puts,calls=f.native.calls.length;
    f.native.running=false;
    const r=await parsed(await f.host.fetch(await request('/api/readiness'))),capacity=r.body.storage_capacity;
    assert.equal(r.status,200);assert.equal(r.body.ready,false);assert.equal(r.body.reason,'retention_capacity_exceeded');
    assert.equal(capacity.status,'exhausted');assert.equal(capacity.objects_accounted,before.head.objects);
    assert.equal(capacity.bytes_accounted,before.head.bytes);assert.equal(capacity.reserved_bytes,0);
    assert.equal(capacity.next_checkpoint_reservation_bytes,f.config.max_checkpoint_bytes);
    assert.equal(f.native.running,false);assert.equal(f.native.calls.length,calls);assert.equal(f.bucket.puts,puts);
    assert.deepEqual(await snapshot(f.storage,f.config),before);assert.deepEqual(await readFile(f.storage.path),database);
  }
});
test('readiness includes pending capacity reservations while preserving uncertainty priority',async t=>{
  const f=await fixture(t,{max_total_bytes:4096},async()=>{throw Error('lost process');});
  assert.equal((await f.host.fetch(await request())).status,503);
  const before=await snapshot(f.storage,f.config),database=await readFile(f.storage.path),calls=f.native.calls.length;
  const r=await parsed(await f.host.fetch(await request('/api/readiness')));
  assert.equal(r.body.ready,false);assert.equal(r.body.reason,'outcome_unknown');assert.equal(r.body.fenced,true);
  assert.equal(r.body.storage_capacity.status,'exhausted');assert.equal(r.body.storage_capacity.reserved_bytes,f.config.max_checkpoint_bytes);
  assert.equal(r.body.storage_capacity.bytes_accounted,f.config.max_checkpoint_bytes);
  assert.equal(f.native.calls.length,calls);assert.deepEqual(await snapshot(f.storage,f.config),before);
  assert.deepEqual(await readFile(f.storage.path),database);assert.equal(f.bucket.puts,0);
});
