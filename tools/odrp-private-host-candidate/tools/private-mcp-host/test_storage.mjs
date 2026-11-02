// Persistent local process crash/concurrency tests. Not native Cloudflare evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {mkdtemp,rm,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {DiskStorage,FileBucket,environment} from './storage_fixture.mjs';
import {configuration,exportBackup,restoreBackup} from './storage.mjs';
const script=fileURLToPath(new URL('./storage_process_fixture.mjs',import.meta.url));
let sequence=0;
async function area(t){const dir=await mkdtemp(join(tmpdir(),'odsp-process-'));t.after(()=>rm(dir,{recursive:true,force:true}));return dir;}
async function child(t,dir,stage='none'){
  const p=fork(script,[dir,stage],{stdio:['ignore','pipe','pipe','ipc'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let stderr='';p.stderr.on('data',v=>stderr+=v);
  const events=[],waiters=[];
  p.on('message',event=>{const index=waiters.findIndex(w=>w.check(event));if(index<0)events.push(event);else{const [{resolve,timer}]=waiters.splice(index,1);clearTimeout(timer);resolve(event);}});
  const wait=check=>{const index=events.findIndex(check);if(index>=0)return Promise.resolve(events.splice(index,1)[0]);return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('fixture timeout '+stderr)),10000);waiters.push({check,resolve,reject,timer});});};
  const stop=()=>new Promise(resolve=>{for(const waiter of waiters.splice(0)){clearTimeout(waiter.timer);waiter.reject(Error('fixture process stopped'));}if(p.exitCode!==null||p.signalCode!==null)return resolve();p.once('exit',resolve);p.kill('SIGKILL');});
  t.after(stop);await wait(x=>x.ready);
  return {p,stop,wait,send(op){const id=++sequence;p.send({id,op});return wait(x=>x.id===id);}};
}
async function state(dir){
  const storage=new DiskStorage(join(dir,'state.sqlite')),bucket=new FileBucket(join(dir,'objects'));
  return {storage,bucket,config:configuration(environment(bucket))};
}
test('real process loss at durable boundaries keeps evidence and refuses automatic execution',async t=>{
  for(const stage of ['after_admission','before_object','after_object','before_commit']){
    const dir=await area(t),crashed=await child(t,dir,stage);void crashed.send('run').catch(()=>{});
    await crashed.wait(x=>x.stage===stage);await crashed.stop();
    const f=await state(dir),pending=await f.storage.get('pending');assert.ok(pending);
    assert.equal(await f.storage.get('committed'),undefined);
    const object=await f.bucket.get(`checkpoints/${f.config.owner_id}/${pending.transaction.id}.tar.gz.base64`);
    assert.equal(Boolean(object),stage==='after_object'||stage==='before_commit');
    const restarted=await child(t,dir);const result=await restarted.send('run');
    assert.equal(result.value.body.error.code,'outcome_unknown');
    assert.deepEqual(await f.storage.get('pending'),pending);await restarted.stop();
  }
});
test('process loss after atomic commit retains exact receipt, never labels effects rolled back',async t=>{
  const dir=await area(t),crashed=await child(t,dir,'after_commit');void crashed.send('run').catch(()=>{});
  await crashed.wait(x=>x.stage==='after_commit');await crashed.stop();
  const f=await state(dir),pointer=await f.storage.get('committed');assert.ok(pointer);
  const receipt=await f.storage.get('receipt:'+pointer.transaction_id);assert.equal(receipt.phase,'committed');
  assert.equal(receipt.checkpoint.sha256,pointer.sha256);assert.ok(await f.bucket.get(pointer.key));
  // Delivery is unconfirmed. No caller replay or repair/rollback is performed.
});
test('two real processes racing for initial admission produce one execution/checkpoint',async t=>{
  const dir=await area(t),a=await child(t,dir),b=await child(t,dir);
  const results=await Promise.all([a.send('run'),b.send('run')]);
  assert.equal(results.filter(r=>r.value.status===200).length,1);
  const f=await state(dir),head=await f.storage.get('control');assert.equal(head.revision,1);assert.equal(head.objects,1);
  const files=await readdir(join(dir,'objects','checkpoints',f.config.owner_id));assert.equal(files.length,1);
  await a.stop();await b.stop();
});
test('old process remains alive across owned restore and cannot publish into restored epoch',async t=>{
  const dir=await area(t),old=await child(t,dir);assert.equal((await old.send('run')).value.status,200);
  const f=await state(dir),backup=await exportBackup(f.storage,f.bucket,f.config);
  const restored=await restoreBackup(f.storage,f.bucket,f.config,backup,1),pointer=await f.storage.get('committed');
  assert.equal(restored.epoch,2);process.kill(old.p.pid,0);
  assert.equal((await old.send('publish_old')).error,'stale_writer');
  assert.equal((await old.send('run')).value.body.error.code,'stale_writer');
  assert.deepEqual(await f.storage.get('committed'),pointer);
  const fresh=await child(t,dir);assert.equal((await fresh.send('run')).value.status,200);
  assert.equal((await f.storage.get('control')).epoch,2);await old.stop();await fresh.stop();
});
