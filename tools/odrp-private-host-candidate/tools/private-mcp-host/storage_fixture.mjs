// Local test-only DO transaction and R2 substitutes backed by SQLite and fsync.
// Real processes/files are used; this does not qualify Cloudflare semantics.
import {spawn} from 'node:child_process';
import {readFile, mkdir, open} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {Refusal, digest} from './storage.mjs';
export const ROOT = dirname(fileURLToPath(import.meta.url));
export const release = {schema:'odsp.private-release-identity.v1',repository:'example/synthetic',
  source_commit:'a'.repeat(40),source_tree:'b'.repeat(40),wheel_sha256:'c'.repeat(64),package_digest:'d'.repeat(64)};
export const policy = {queue_limit:2,transaction_ms:5000,storage_ms:1500,ready_ms:1000,health_ms:500,
  max_request_bytes:4096,max_checkpoint_bytes:4096,max_response_bytes:16384,max_objects:40,max_total_bytes:163840};
export function environment(bucket, overrides = {}) {
  return {RETAINED:bucket,ODSP_OWNER_ID:'synthetic-deployment',ODSP_CHECKPOINT_GENERATION:'synthetic-generation-v1',
    ODSP_RELEASE_IDENTITY:JSON.stringify(release),ODSP_STORAGE_POLICY:JSON.stringify({...policy,...overrides}),
    PRINCIPAL_AUTH_JSON:JSON.stringify({schema:'ciw.bearer-principals.v1',policy_revision:'fixture-v1',
      policy_sha256:'sha256:'+'e'.repeat(64),principals:[{principal:'alice',enabled:true,token_sha256:'f'.repeat(64)}]})};
}
export async function request(path='/api/tools', payload) {
  return new Request('https://synthetic.invalid'+path,{method:payload===undefined?'GET':'POST',headers:{
    'Content-Type':'application/json','x-odsp-principal':'alice','x-odsp-owner':'principal:'+await digest('alice'),
    'x-odsp-policy-revision':'fixture-v1','x-odsp-policy-sha256':'sha256:'+'e'.repeat(64)},
    ...(payload===undefined?{}:{body:JSON.stringify(payload)})});
}
function sqlite(path, action, input) {
  return new Promise((resolveResult,reject)=>{
    const child=spawn(process.env.PYTHON || 'python',[resolve(ROOT,'storage_fixture.py'),path,action],
      {stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
    let out='',err='';child.stdout.on('data',v=>out+=v);child.stderr.on('data',v=>err+=v);
    child.on('error',reject);child.on('close',code=>{if(code)reject(Error(err));else {try{resolveResult(JSON.parse(out));}catch(e){reject(e);}}});
    child.stdin.end(JSON.stringify(input));
  });
}
export class DiskStorage {
  constructor(path){this.path=path;this.beforeCommit=null;this.afterCommit=null;}
  async get(key){return (await sqlite(this.path,'read',{})).body[key];}
  async transaction(fn){
    const {revision,body}=await sqlite(this.path,'read',{}), staged=structuredClone(body);
    const tx={get:async k=>structuredClone(staged[k]),put:async(k,v)=>{staged[k]=structuredClone(v);},delete:async k=>delete staged[k]};
    const value=await fn(tx);
    if(JSON.stringify(body)===JSON.stringify(staged)) return value;
    if(this.beforeCommit) await this.beforeCommit(body,staged);
    const result=await sqlite(this.path,'cas',{revision,body:staged});
    if(!result.changed) throw new Refusal('stale_writer');
    if(this.afterCommit) await this.afterCommit(body,staged);
    return value;
  }
  async put(k,v){return this.transaction(tx=>tx.put(k,v));}
}
export class FileBucket {
  constructor(root){this.root=root;this.beforePut=null;this.afterPut=null;this.puts=0;}
  path(key){const value=resolve(this.root,key);if(!value.startsWith(resolve(this.root)+'/'))throw Error('unsafe fixture key');return value;}
  async put(key,value){
    if(this.beforePut)await this.beforePut(key,value);
    const path=this.path(key);await mkdir(dirname(path),{recursive:true});const fd=await open(path,'wx',0o600);
    try{await fd.writeFile(value);await fd.sync();}finally{await fd.close();}
    const directory=await open(dirname(path),'r');try{await directory.sync();}finally{await directory.close();}
    this.puts++;if(this.afterPut)await this.afterPut(key,value);
  }
  async get(key){try{return {body:new Response(await readFile(this.path(key))).body};}catch(e){if(e.code==='ENOENT')return null;throw e;}}
}
export async function loadWorker(){
  let source=await readFile(resolve(ROOT,'worker.mjs'),'utf8');
  source=source.replace("import { DurableObject } from 'cloudflare:workers';",'const DurableObject=class{constructor(ctx,env){this.ctx=ctx;this.env=env;}};');
  for(const name of ['gateway','release','storage']){
    const override=name==='storage'?null:process.env['ODSP_TEST_'+name.toUpperCase()];
    source=source.replace(`'./${name}.mjs'`,JSON.stringify(pathToFileURL(override||resolve(ROOT,name+'.mjs')).href));
  }
  return (await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))).NCPHost;
}
export function container(config, handler){
  const calls=[];
  const port={fetch:async(url,options)=>{
    calls.push(url);
    if(url.endsWith('/health'))return Response.json({ready:true,owner_id:config.ODSP_OWNER_ID,
      checkpoint_generation:config.ODSP_CHECKPOINT_GENERATION,release:JSON.parse(config.ODSP_RELEASE_IDENTITY)});
    const input=JSON.parse(options.body);
    if(handler)return handler(input,options);
    const state=Buffer.from(JSON.stringify({synthetic:true,transaction:input.transaction})).toString('base64');
    return Response.json({result:{data:{synthetic:true}},status:200,state,transaction:input.transaction,
      identity:input.identity,checkpoint_sha256:await digest(state)});
  }};
  return {calls,running:true,start(){this.running=true;},setInactivityTimeout(){},getTcpPort(){return port;}};
}
