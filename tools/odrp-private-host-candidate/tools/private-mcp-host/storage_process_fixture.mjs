// Local subprocess harness; no network/Cloudflare service is involved.
import {join} from 'node:path';
import {DiskStorage,FileBucket,environment,request,container,loadWorker} from './storage_fixture.mjs';
import {configuration,publish} from './storage.mjs';
const [root,stage='none']=process.argv.slice(2),storage=new DiskStorage(join(root,'state.sqlite'));
const bucket=new FileBucket(join(root,'objects')),env=environment(bucket),config=configuration(env);
const NCPHost=await loadWorker(),native=container(env),host=new NCPHost({storage,container:native},env);
const pause=async observed=>{process.send({stage:observed});await new Promise(()=>{});};
if(stage==='after_admission')storage.afterCommit=async(old,value)=>{if(!old.pending&&value.pending)await pause(stage);};
if(stage==='before_object')bucket.beforePut=()=>pause(stage);
if(stage==='after_object')bucket.afterPut=()=>pause(stage);
if(stage==='before_commit')storage.beforeCommit=async(old,value)=>{if(!old.committed&&value.committed)await pause(stage);};
if(stage==='after_commit')storage.afterCommit=async(old,value)=>{if(!old.committed&&value.committed)await pause(stage);};
let previous;
process.on('message',async message=>{
  try{
    let value;
    if(message.op==='run'){
      const response=await host.fetch(await request());value={status:response.status,body:await response.json()};
      const pointer=await storage.get('committed');
      if(pointer)previous={pointer,binding:(await storage.get('receipt:'+pointer.transaction_id)).transaction};
    }else if(message.op==='publish_old'){
      await publish(storage,config,previous.binding,previous.pointer,Date.now()+3000);value={published:true};
    }else throw Error('unknown fixture command');
    process.send({id:message.id,value});
  }catch(error){process.send({id:message.id,error:error.code||error.message});}
});
process.send({ready:true});
