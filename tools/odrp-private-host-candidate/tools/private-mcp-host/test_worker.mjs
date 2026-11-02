// Real Worker source and peer gateway/release helpers, local persistent substitutes.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DiskStorage,FileBucket,environment,request,container,loadWorker} from './storage_fixture.mjs';
import {configuration,digest,snapshot,exportBackup,restoreBackup,publish} from './storage.mjs';
const NCPHost=await loadWorker();
async function fixture(t,overrides={},handler){
  const dir=await mkdtemp(join(tmpdir(),'odsp-storage-'));t.after(()=>rm(dir,{recursive:true,force:true}));
  const storage=new DiskStorage(join(dir,'state.sqlite')),bucket=new FileBucket(join(dir,'objects'));
  const env=environment(bucket,overrides),native=container(env,handler),ctx={storage,container:native};
  return {dir,storage,bucket,env,native,ctx,host:new NCPHost(ctx,env),config:configuration(env)};
}
const parsed=async response=>({status:response.status,body:await response.json()});

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
test('commit timeout after durable effect retains receipt and late completion cannot clear its hold',async t=>{
  const f=await fixture(t,{storage_ms:150});let finish,injected=false;
  f.storage.afterCommit=async(old,value)=>{if(!injected&&value.committed&&!old.committed){injected=true;await new Promise(resolve=>{finish=resolve;});}};
  const r=await parsed(await f.host.fetch(await request()));assert.equal(r.body.error.code,'committed_response_unconfirmed');
  const saved=await snapshot(f.storage,f.config);assert.ok(saved.committed);assert.ok(saved.pending);
  finish();await new Promise(resolve=>setTimeout(resolve,30));assert.deepEqual(await snapshot(f.storage,f.config),saved);
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
