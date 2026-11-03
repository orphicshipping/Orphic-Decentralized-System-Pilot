// Test-only loopback edge and real gateway/Worker/installed-adapter integration.
// DiskStorage/FileBucket replace hosted DO/R2; no Cloudflare claim is made.
// Lifecycle and faults use stdin commands. There are no HTTP test/admin routes.
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import {createWriteStream} from 'node:fs';
import {readFile, mkdir, readdir, stat} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import {generateKeyPairSync, randomBytes, sign, createHash, webcrypto} from 'node:crypto';

globalThis.crypto ??= webcrypto;
const args = {};
for (let i=2;i<process.argv.length;i+=2) {
  if (!process.argv[i].startsWith('--') || process.argv[i+1] === undefined) throw Error('invalid_fixture_arguments');
  args[process.argv[i].slice(2)] = process.argv[i+1];
}
for (const name of ['python','manifest','output-dir','panel-build-dir']) if (!args[name]) throw Error('missing_fixture_argument');
const root = resolve(args['output-dir']), manifest = resolve(args.manifest), build = resolve(args['panel-build-dir']);
const context = dirname(dirname(manifest)), backendRoot = join(context,'tools/private-mcp-host');
const here = dirname(fileURLToPath(import.meta.url)), helper = join(here,'vertical_fixture.py');
const python = resolve(args.python), fixture = join(root,'fixture');
await mkdir(fixture,{recursive:true});
const nativeFetch = globalThis.fetch;
const {DiskStorage,FileBucket,loadWorker} = await import(pathToFileURL(join(backendRoot,'storage_fixture.mjs')));
const {route:gateway} = await import(pathToFileURL(join(backendRoot,'gateway.mjs')));
const {createPanel} = await import(pathToFileURL(join(build,'worker/index.mjs')));
const assetsModule = await import(pathToFileURL(join(build,'assets.mjs')));
const assets = assetsModule.assets || assetsModule.ASSETS || assetsModule.default;
if (!assets || !assets['/source.json']) throw Error('exact_panel_build_assets_required');
process.env.PYTHON = python; // Existing SQLite fixture launches only this trusted runtime.

function emit(value) { process.stdout.write(JSON.stringify(value)+'\n'); }
function runPrepare(mode) {
  return new Promise((accept,reject)=>{
    const child=spawn(python,['-I','-B',helper,'prepare','--manifest',manifest,'--root',fixture,'--mode',mode],
      {cwd:root,stdio:['ignore','pipe','pipe'],env:{PATH:process.env.PATH,HOME:process.env.HOME}});
    let result='',error='';child.stdout.on('data',v=>result+=v);child.stderr.on('data',v=>error+=v);
    child.on('error',reject);child.on('close',code=>{
      if(code)reject(Error('installed_fixture_preparation_failed'));
      else {try{accept(JSON.parse(result));}catch(e){reject(e);}}
    });
  });
}
let configuration = await runPrepare(args.resume === 'true' ? 'inspect' : 'initial');
const principals = ['panel-alpha','panel-beta'];
const tokens = new Map(principals.map(id=>[id,randomBytes(40).toString('base64url')]));
const issuer='https://synthetic-panel.cloudflareaccess.com', audience='local-panel-fixture-audience';
const panelHost='panel.synthetic.invalid', backendHost='gateway.synthetic.invalid';
const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048});
const jwk={...publicKey.export({format:'jwk'}),kid:'ephemeral-fixture-key',use:'sig',alg:'RS256'};
const encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url');
function accessToken(principal) {
  if(!principals.includes(principal))throw Error('unknown_fixture_principal');
  const time=Math.floor(Date.now()/1000),head=encode({alg:'RS256',kid:jwk.kid,typ:'JWT'});
  const body=encode({iss:issuer,aud:audience,type:'app',iat:time,exp:time+1800,sub:'subject:'+principal});
  return head+'.'+body+'.'+sign('RSA-SHA256',Buffer.from(head+'.'+body),privateKey).toString('base64url');
}
const metrics={schema:'odsp.panel-vertical-fixture.v1',scope:'local_DO_R2_substitutes_actual_installed_MCP',
  container_starts:0,adapter_health_requests:0,adapter_transactions:0,gateway_requests:0,worker_requests:0,
  mcp_process_ids:[],process_observation_failures:[],transactions:[],browser_requests:0,started_at:new Date().toISOString()};
let adapter=null,adapterStart=null,adapterUrl=null,adapterLog=null,starting=null,publicationFault=false;
let scanning=null;
let lastResponse=null,responseSequence=0;
const ownedProcesses=new Map();

function observationFailure(code) {
  if(!metrics.process_observation_failures.includes(code))metrics.process_observation_failures.push(code);
}
const processGone=error=>['ENOENT','ESRCH'].includes(error.code);

async function processStat(pid) {
  const raw=await readFile(`/proc/${pid}/stat`,'utf8');
  const fields=raw.slice(raw.lastIndexOf(')')+2).split(' ');
  return {parent:Number(fields[1]),start:fields[19],state:fields[0]};
}

async function scanMcp() {
  if(!adapter?.pid||adapter.exitCode!==null||adapter.signalCode!==null)return;
  if(scanning)return scanning;
  const rootProcess=adapter,rootPid=adapter.pid,rootStart=adapterStart;
  scanning=(async()=>{
    // Some managed Linux environments omit task/<tid>/children entirely.
    // Observe parent IDs from proc stat instead, and inspect command lines
    // only for descendants of this fixture's verified adapter process.
    const pids=(await readdir('/proc')).filter(x=>/^\d+$/.test(x)).map(Number);
    if(pids.length>4096)throw Error('fixture_process_observation_capacity');
    const observed=new Map();
    for(let i=0;i<pids.length;i+=64)await Promise.all(pids.slice(i,i+64).map(async pid=>{
      try {observed.set(pid,await processStat(pid));}
      catch(error) {if(!processGone(error))observationFailure('fixture_process_stat_unreadable');}
    }));
    // A terminated process number may belong to an unrelated new process.
    // The root identity was captured at spawn and is never refreshed here.
    if(rootProcess.exitCode!==null||rootProcess.signalCode!==null)return;
    if(!rootStart||observed.get(rootPid)?.start!==rootStart) {
      observationFailure('fixture_adapter_identity_changed');return;
    }
    for(const pid of pids) {
      let ancestor=pid;
      for(let depth=0;depth<32&&ancestor!==rootPid;depth++)ancestor=observed.get(ancestor)?.parent;
      if(ancestor!==rootPid)continue;
      const info=observed.get(pid);
      if(info) {
        if(ownedProcesses.has(pid)&&ownedProcesses.get(pid)!==info.start) {
          observationFailure('fixture_owned_identity_changed');continue;
        }
        ownedProcesses.set(pid,info.start);
      }
      if(metrics.mcp_process_ids.includes(pid))continue;
      try {
        const cmd=(await readFile(`/proc/${pid}/cmdline`)).toString().split('\0');
        if(cmd.includes('ciw.principal_mcp'))metrics.mcp_process_ids.push(pid);
      } catch(error) {if(!processGone(error))observationFailure('fixture_owned_command_unreadable');}
    }
  })();
  try {await scanning;} catch(error) {
    const code=error.message==='fixture_process_observation_capacity'?error.message:'fixture_process_observation_failed';
    observationFailure(code);
  } finally {scanning=null;}
}

async function ownedAlive() {
  const result={alive:[],unknown:false};
  for(const [pid,start] of ownedProcesses)try {
    const current=await processStat(pid);
    if(current.start===start&&!['Z','X'].includes(current.state))result.alive.push(pid);
  } catch(error) {if(!processGone(error)) {
    result.unknown=true;observationFailure('fixture_owned_process_state_unknown');
  }}
  return result;
}
async function waitOwned(deadline) {
  const until=Date.now()+deadline;
  while(Date.now()<until) {
    const status=await ownedAlive();if(!status.alive.length&&!status.unknown)return status;
    await new Promise(done=>setTimeout(done,100));
  }
  return ownedAlive();
}
async function signalOwned(pid,signal) {
  try {
    const current=await processStat(pid),start=ownedProcesses.get(pid);
    if(!start||current.start!==start) {observationFailure('fixture_owned_identity_changed');return;}
    if(!['Z','X'].includes(current.state))process.kill(pid,signal);
  } catch(error) {if(!processGone(error))observationFailure('fixture_owned_signal_unconfirmed');}
}
async function startAdapter() {
  if(adapter&&adapter.exitCode===null&&adapterUrl)return;
  if(starting)return starting;
  starting=(async()=>{
    metrics.container_starts++;
    adapterLog=createWriteStream(join(root,'adapter-stderr.log'),{flags:'a',mode:0o600});
    adapter=spawn(python,['-I','-B',helper,'serve','--manifest',manifest,'--root',fixture],
      {cwd:root,stdio:['ignore','pipe','pipe'],env:{PATH:process.env.PATH,HOME:process.env.HOME}});
    adapterStart=(await processStat(adapter.pid)).start;
    ownedProcesses.set(adapter.pid,adapterStart);
    adapter.stderr.pipe(adapterLog);
    const lines=createInterface({input:adapter.stdout});
    const hello=await new Promise((accept,reject)=>{
      const timer=setTimeout(()=>reject(Error('adapter_start_timeout')),20000);
      adapter.once('error',reject);adapter.once('exit',()=>reject(Error('adapter_start_failed')));
      lines.once('line',line=>{clearTimeout(timer);try{accept(JSON.parse(line));}catch(error){reject(error);}});
    });
    adapterUrl=hello.url;
    for(let count=0;count<200;count++) {
      if(adapter.exitCode!==null)throw Error('actual_adapter_exited');
      try {const value=await nativeFetch(adapterUrl+'/health',{signal:AbortSignal.timeout(1000)});
        if(value.ok&&(await value.json()).release.source_commit===configuration.release.source_commit)return;
      } catch {}
      await new Promise(accept=>setTimeout(accept,50));
    }
    throw Error('actual_adapter_not_ready');
  })();
  try {await starting;} finally {starting=null;}
}
async function stopAdapter() {
  await scanMcp();
  if(adapter&&adapter.exitCode===null&&adapter.signalCode===null) {
    const child=adapter;
    const exited=new Promise(done=>child.once('exit',done));
    const observing=setInterval(()=>void scanMcp(),250);
    await signalOwned(child.pid,'SIGTERM');
    await Promise.race([exited,new Promise(done=>setTimeout(done,8000))]);
    if(child.exitCode===null&&child.signalCode===null){
      await signalOwned(child.pid,'SIGKILL');
      await Promise.race([exited,new Promise(done=>setTimeout(done,2000))]);
    }
    clearInterval(observing);
  }
  // Only captured descendants with the same process start time are ours to
  // terminate. Preserve all saved state; stopping a process implies no rollback.
  for(const pid of (await ownedAlive()).alive)await signalOwned(pid,'SIGTERM');
  for(const pid of (await waitOwned(2000)).alive)await signalOwned(pid,'SIGKILL');
  const remaining=await waitOwned(2000);
  if(remaining.alive.length||remaining.unknown)throw Error('fixture_owned_process_cleanup_failed');
  adapter=null;adapterStart=null;adapterUrl=null;adapterLog?.end();adapterLog=null;
}
const storage=new DiskStorage(join(fixture,'worker.sqlite'));
const bucket=new FileBucket(join(fixture,'objects'));
bucket.beforePut=()=>{if(publicationFault){publicationFault=false;throw Error('synthetic_checkpoint_publication_failure');}};
const container={
  get running(){return Boolean(adapter&&adapter.exitCode===null&&adapterUrl);},
  async start(){await startAdapter();},setInactivityTimeout(){},
  getTcpPort(){return {fetch:async(url,options={})=>{
    const path=new URL(url).pathname;
    if(path==='/health')metrics.adapter_health_requests++;
    else if(path==='/transaction') {
      metrics.adapter_transactions++;
      const input=JSON.parse(options.body);
      metrics.transactions.push({action:input.action,tool:input.payload?.name||null,
        principal:input.identity.principal,transaction_id:input.transaction.id});
    }
    if(!adapterUrl)throw Error('actual_adapter_not_running');
    const interval=setInterval(()=>void scanMcp(),250);
    try{return await nativeFetch(adapterUrl+path,options);}finally{clearInterval(interval);await scanMcp();}
  }};}
};
const backendEnv={RETAINED:bucket,ODSP_OWNER_ID:configuration.owner_id,
  ODSP_CHECKPOINT_GENERATION:configuration.generation,ODSP_RELEASE_IDENTITY:JSON.stringify(configuration.release),
  ODSP_STORAGE_POLICY:JSON.stringify({queue_limit:4,transaction_ms:90000,storage_ms:5000,ready_ms:20000,health_ms:3000,
    max_request_bytes:262144,max_checkpoint_bytes:24*1024*1024,max_response_bytes:32*1024*1024,
    max_objects:256,max_total_bytes:256*1024*1024}),
  PUBLIC_HOSTNAME:backendHost,HOST_SINGLETON:'panel-local-single-writer'};
const NCPHost=await loadWorker();
const worker=new NCPHost({storage,container},backendEnv);
backendEnv.HOST={getByName(name){if(name!==backendEnv.HOST_SINGLETON)throw Error('wrong_singleton');
  return {fetch(request){metrics.worker_requests++;return worker.fetch(request);}};}};
const env={PANEL_HOSTNAME:panelHost,PANEL_BACKEND_ORIGIN:'https://'+backendHost,
  ACCESS_ISSUER:issuer,ACCESS_AUDIENCE:audience,
  BACKEND:{fetch(request){metrics.gateway_requests++;return gateway(request,backendEnv);}}};
const credentialStart = Math.floor(Date.now()/1000)-1;
function refreshBindings() {
  const credential = row => ({credential_id:'local-fixture:'+row.principal,
    not_before:credentialStart,expires_at:credentialStart+3600});
  backendEnv.PRINCIPAL_AUTH_JSON=JSON.stringify({schema:'ciw.bearer-principals.v2',
    policy_revision:configuration.policy_revision,policy_sha256:configuration.policy_sha256,
    principals:configuration.principals.map(row=>({...row,...credential(row),token_sha256:createHash('sha256').update(tokens.get(row.principal)).digest('hex')}))});
  env.PANEL_PRINCIPALS_JSON=JSON.stringify({schema:'odsp.panel-principals.v2',principals:
    configuration.principals.map(row=>({...row,...credential(row),subject:'subject:'+row.principal,backend_token:tokens.get(row.principal)}))});
}
refreshBindings();
const panel=createPanel({assets,fetchJwks:async request=>{
  const url=typeof request==='string'?request:request.url;
  if(url!==issuer+'/cdn-cgi/access/certs')throw Error('unexpected_fixture_network_request');
  return Response.json({keys:[jwk]});
}});
// Initialize fixture SQLite outside the measured read-only readiness operation.
await storage.get('control');
let address;
const server=createServer(async(req,res)=>{
  metrics.browser_requests++;
  try {
    const chunks=[];let size=0;
    for await(const chunk of req){size+=chunk.length;if(size>262144)throw Error('fixture_request_too_large');chunks.push(chunk);}
    const headers=new Headers();
    for(const [key,value] of Object.entries(req.headers))if(value!==undefined&&key!=='host')headers.set(key,Array.isArray(value)?value.join(','):value);
    // This adapter is a test ingress, not a production branch: it represents
    // TLS+Access at the edge, then invokes the exact HTTPS-only BFF/gateway.
    const cookie=(req.headers.cookie||'').split(';').map(x=>x.trim()).find(x=>x.startsWith('CF_Authorization='));
    if(cookie)headers.set('Cf-Access-Jwt-Assertion',decodeURIComponent(cookie.slice('CF_Authorization='.length)));
    else headers.delete('Cf-Access-Jwt-Assertion');
    if(headers.get('origin')===address)headers.set('origin','https://'+panelHost);
    headers.delete('cookie');
    const input=new Request('https://'+panelHost+req.url,{method:req.method,headers,
      ...(chunks.length?{body:Buffer.concat(chunks)}:{})});
    const result=await panel.fetch(input,env);
    const bytes=Buffer.from(await result.arrayBuffer());
    if(new URL(input.url).pathname.startsWith('/api/'))lastResponse={sequence:++responseSequence,
      fixture_pid:process.pid,path:new URL(input.url).pathname,status:result.status,
      bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
    res.writeHead(result.status,Object.fromEntries(result.headers));
    res.end(bytes);
  } catch {res.writeHead(500,{'content-type':'application/json'});res.end('{"error":"fixture_transport_failed"}');}
});
await new Promise(done=>server.listen(Number(args.port||0),'127.0.0.1',done));
address='http://127.0.0.1:'+server.address().port;
emit({ready:true,url:address,pid:process.pid,release:configuration.release,scope:metrics.scope});

async function files(root) {
  const result=[];
  try {for(const entry of await readdir(root,{withFileTypes:true})) {
    const path=join(root,entry.name);if(entry.isDirectory())result.push(...await files(path));
    else result.push({path:path.slice(fixture.length+1),bytes:(await stat(path)).size});
  }} catch(error){if(error.code!=='ENOENT')throw error;}
  return result;
}
const commands=createInterface({input:process.stdin});
let chain=Promise.resolve(),shutdownPromise=null;
function shutdown(){
  shutdownPromise??=(async()=>{
    await stopAdapter();
    await new Promise(done=>{server.close(done);server.closeAllConnections();});
    return {stopped:true,process_scope:'observed_PID_starttime_adapter_descendants',
      owned_processes_seen:ownedProcesses.size,owned_survivors:0,
      process_observation_failures:metrics.process_observation_failures};
  })();
  return shutdownPromise;
}
commands.on('line',line=>{chain=chain.then(async()=>{
  let message;
  try {
    message=JSON.parse(line);let value;
    if(message.op==='session')value={assertion:accessToken(message.principal)};
    else if(message.op==='last-response')value=lastResponse;
    else if(message.op==='snapshot')value={metrics:structuredClone(metrics),control:await storage.get('control')||null,
      committed:await storage.get('committed')||null,pending:await storage.get('pending')||null,
      adapter_running:container.running,objects:await files(join(fixture,'objects')),state:await files(join(fixture,'state'))};
    else if(message.op==='policy') {configuration=await runPrepare(message.mode);refreshBindings();value={updated:true};}
    else if(message.op==='fail-publication'){publicationFault=true;value={armed:true};}
    else if(message.op==='strict-gateway-probe') {
      const request=new Request('http://'+backendHost+'/api/readiness',{headers:{Authorization:'Bearer '+tokens.get(principals[0]),Origin:'https://'+panelHost}});
      const result=await gateway(request,backendEnv);value={status:result.status,body:await result.json()};
    } else if(message.op==='shutdown'){value=await shutdown();}
    else throw Error('unknown_fixture_command');
    emit({id:message.id,value});
    if(message.op==='shutdown')process.exit(0);
  } catch {emit({id:message?.id,error:'fixture_command_failed'});}
});});
function stopSignal(){void shutdown().then(()=>process.exit(0),()=>{
  process.stderr.write('fixture_shutdown_failed\n');process.exit(1);
});}
process.on('SIGTERM',stopSignal);
process.on('SIGINT',stopSignal);
