// Real Chromium, intercepted same-origin fixtures only. No backend/provider/network.
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const WEB=path.resolve(__dirname,'../web');
const ORIGIN='https://panel.fixture.invalid';
const HOLD='odsp-terminal-uncertainty-v1';
const SHA='a'.repeat(64);
const uuid=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
const tool=(name,readOnlyHint)=>({name,description:'Synthetic fixture contract',inputSchema:{type:'object'},
  ...(readOnlyHint===undefined?{}:{annotations:{readOnlyHint}}),console_access:'read'});
const SOURCE={schema:'odsp.private-panel-source.v1',repository:'orphicsystems/Orphic-Decentralized-System-Protocol',source_commit:'a'.repeat(40),source_tree:'b'.repeat(40),assets:{'/':SHA,'/app.js':SHA,'/theme.css':SHA,'/styles.css':SHA}};
const RELEASE={schema:'odsp.private-release-identity.v1',repository:SOURCE.repository,source_commit:'c'.repeat(40),source_tree:'d'.repeat(40),wheel_sha256:SHA,package_digest:SHA};
const STATUS={connection:'connected',transport:'stdio',profile:'synthetic-profile',instrument:'builtin',known_runs:0,last_ok:'2026-11-02T00:00:00.000Z'};
let browser;
before(async()=>{const executable=process.env.ODSP_TEST_BROWSER||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined);browser=await chromium.launch({executablePath:executable,headless:true,args:['--no-sandbox']});});
after(async()=>{await browser?.close();});
async function setup(t,{cold=false,init}={}){
  const context=await browser.newContext({viewport:{width:1100,height:900},acceptDownloads:true});
  const page=await context.newPage();
  const w={context,page,requests:[],external:[],errors:[],overrides:{},sequence:0,deferred:[],
    tools:[tool('ncp_plan_template',true),tool('ncp_plan_create',false),tool('ncp_run_get',true),tool('ncp_capabilities_list',true)],
    ready:{schema:'ciw.private-readiness.v1',ready:!cold,fenced:false,reason:cold?'host_not_running':null,
      owner_id:'synthetic-owner',generation:'fixture-generation',epoch:3,release:RELEASE,storage_capacity:{status:'available'},pending_transaction_id:null,committed_transaction_id:null,authorizes_execution:false,checkpoint_written:false}};
  page.on('pageerror',error=>w.errors.push(error.message));
  if(init)await context.addInitScript(init);
  w.receipt=(data,extra={})=>({data,retention:{committed:true,transaction_id:uuid(++w.sequence),epoch:3,checkpoint_sha256:SHA,request_sha256:SHA,response_sha256:SHA},...extra});
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!==ORIGIN){w.external.push(req.url());await route.abort();return;}
    const pathname=url.pathname;
    if(pathname==='/source.json'){await route.fulfill({contentType:'application/json',body:JSON.stringify(SOURCE)});return;}
    const staticName=pathname==='/'?'index.html':pathname.slice(1);
    if(['index.html','app.js','styles.css','theme.css'].includes(staticName)){
      const file=path.join(WEB,staticName);
      const body=fs.existsSync(file)?fs.readFileSync(file):'[hidden]{display:none!important}';
      await route.fulfill({contentType:staticName.endsWith('.js')?'text/javascript':staticName.endsWith('.css')?'text/css':'text/html',body});return;
    }
    if(!pathname.startsWith('/api/')){await route.fulfill({status:404,body:'Not found'});return;}
    const body=req.postData();let parsed;try{parsed=body?JSON.parse(body):null;}catch{}
    w.requests.push({path:pathname,method:req.method(),body,parsed,headers:req.headers()});
    let reply=await w.overrides[pathname]?.({route,req,body:parsed,w});
    if(reply?.defer){w.deferred.push(route);return;}
    if(reply?.abort){await route.abort('failed');return;}
    if(!reply){
      let data;
      if(pathname==='/api/readiness')reply={body:w.ready};
      else {
        if(pathname==='/api/status'||pathname==='/api/diagnostics')data=STATUS;
        else if(pathname==='/api/tools')data=w.tools;
        else if(pathname==='/api/runs')data={items:[],complete:false,scope:'Synthetic observed runs only.'};
        else if(pathname==='/api/events')data=[];
        else if(pathname==='/api/resources')data={contents:[]};
        else data={isError:false,structuredContent:{data:{items:[]}}};
        reply={body:w.receipt(data)};
      }
    }
    await route.fulfill({status:reply.status||200,contentType:reply.contentType||'application/json',body:reply.raw??JSON.stringify(reply.body)});
  });
  t.after(async()=>{for(const route of w.deferred)await route.abort().catch(()=>{});await context.close();});
  w.idle=()=>page.waitForFunction(()=>!document.querySelector('#connect').disabled);
  w.connect=async()=>{await page.locator('#connect').click();await w.idle();};
  w.open=async()=>{await w.connect();await page.waitForFunction(()=>!document.querySelector('#open-session').disabled);await page.locator('#open-session').click();await w.idle();};
  w.select=async name=>{await page.locator('.tool-item').filter({hasText:name}).click();};
  w.call=async(name,text='{}')=>{await w.select(name);await page.locator('#arguments').fill(text);if(await page.locator('#confirm-write').isEnabled())await page.locator('#confirm-write').check();await page.locator('#call').click();await w.idle();};
  await page.goto(ORIGIN);
  return w;
}

test('explicit readiness and cold-start session; canonical annotation controls; no automatic transactions',{timeout:30000},async t=>{
  const w=await setup(t,{cold:true});const p=w.page;
  assert.equal(w.requests.length,0);
  await w.connect();assert.deepEqual(w.requests.map(r=>r.path),['/api/readiness']);
  assert.equal(await p.locator('#open-session').isEnabled(),true);
  await p.locator('summary').filter({hasText:'Panel source identity'}).click();
  assert.ok((await p.locator('#source-identity').innerText()).includes(SOURCE.source_commit));
  await p.locator('summary').filter({hasText:'Backend release identity'}).click();
  assert.ok((await p.locator('#release-identity').innerText()).includes(RELEASE.source_commit));
  await p.locator('#open-session').click();await w.idle();
  assert.deepEqual(w.requests.map(r=>r.path),['/api/readiness','/api/status','/api/tools']);
  assert.equal(await p.locator('#readiness-state').getAttribute('data-state'),'stale');
  assert.equal(await p.locator('#call').isDisabled(),true,'discovery is not an automatic selection or call');
  await w.select('ncp_plan_create');assert.equal(await p.locator('#call').isDisabled(),true,'console_access read cannot override canonical write annotation');
  await p.locator('#confirm-write').check();assert.equal(await p.locator('#call').isEnabled(),true);
  await p.locator('#arguments').fill('{"x":1}');assert.equal(await p.locator('#call').isDisabled(),true);
  assert.equal(await p.locator('#restart').isDisabled(),true);
  assert.ok(w.requests.every(r=>r.headers['x-odsp-panel-request']==='1'&&!r.headers.authorization));
  assert.equal(await p.evaluate(key=>sessionStorage.getItem(key),HOLD),null,'each complete receipt settles only its own in-flight intent');
  assert.deepEqual(w.errors,[]);assert.deepEqual(w.external,[]);
});

test('scientific text, full raw receipt download, UTC evidence and keyboard selection focus',{timeout:30000},async t=>{
  const w=await setup(t);await w.open();
  const scientific='{"reference":1.0,"large":9007199254740993,"label":"Ω"}';
  const raw=JSON.stringify(w.receipt({isError:false,structuredContent:{data:{}}},{raw_json:scientific,plan_arguments_json:scientific}),null,2)+'\n';
  w.overrides['/api/calls']=()=>({raw});
  await w.select('ncp_plan_create');
  assert.equal(await w.page.locator('#arguments').evaluate(el=>el===document.activeElement),true);
  await w.page.locator('#arguments').fill(scientific);await w.page.locator('#confirm-write').check();await w.page.locator('#call').click();await w.idle();
  const sent=w.requests.find(r=>r.path==='/api/calls').parsed;
  assert.equal(sent.arguments_json,scientific);assert.equal(sent.arguments,undefined);assert.equal(sent.confirmed,true);
  assert.equal(await w.page.locator('#result').textContent(),raw);
  assert.equal(await w.page.locator('#scientific-result').textContent(),scientific);
  assert.match(await w.page.locator('#receipt-at').innerText(),/ UTC$/);
  const [download]=await Promise.all([w.page.waitForEvent('download'),w.page.locator('#download').click()]);
  assert.deepEqual(fs.readFileSync(await download.path()),Buffer.from(raw));
  await w.page.locator('#prepare-plan').click();assert.equal(await w.page.locator('#arguments').inputValue(),scientific);
  assert.equal(await w.page.locator('#confirm-write').isChecked(),false);
  assert.equal(await w.page.evaluate(key=>sessionStorage.getItem(key),HOLD),null);
});

test('known predispatch auth denial permits explicit reconnect; reduced discovery invalidates old selection',{timeout:30000},async t=>{
  const w=await setup(t);await w.open();
  w.overrides['/api/calls']=()=>({status:401,body:{error:{code:'unauthorized'}}});
  await w.call('ncp_plan_create');
  assert.equal(await w.page.locator('#session-state').innerText(),'DENIED');
  assert.equal(await w.page.locator('#receipt-effects').innerText(),'not_started');
  assert.equal(await w.page.locator('#call').isDisabled(),true);
  assert.equal(await w.page.locator('#hold-warning').isHidden(),true);
  assert.equal(await w.page.evaluate(key=>sessionStorage.getItem(key),HOLD),null);
  delete w.overrides['/api/calls'];w.tools=[tool('ncp_run_get',true),tool('unannotated_tool')];await w.open();
  assert.equal(await w.page.locator('.tool-item').filter({hasText:'ncp_plan_create'}).count(),0);
  await w.select('unannotated_tool');assert.equal(await w.page.locator('#call').isDisabled(),true);
  await w.select('ncp_run_get');w.tools=[];
  await w.page.locator('#diagnose').click();await w.idle();
  assert.equal(await w.page.locator('#session-state').innerText(),'EMPTY');
  assert.equal(await w.page.locator('#call').isDisabled(),true);
  assert.equal(w.requests.filter(r=>r.path==='/api/calls').length,1);
});

test('structured unknown survives readiness, disconnect and reload without retaining arguments',{timeout:30000},async t=>{
  const w=await setup(t);await w.open();
  w.overrides['/api/calls']=()=>({status:503,body:{error:{code:'outcome_unknown',effect_state:'unconfirmed',transaction_id:uuid(99),uncertainty_hold_confirmed:true}}});
  await w.call('ncp_plan_create','{"sentinel":"do-not-retain-this-argument"}');
  assert.equal(await w.page.locator('#hold-warning').isVisible(),true);
  const raw=await w.page.evaluate(key=>sessionStorage.getItem(key),HOLD);
  assert.ok(Buffer.byteLength(raw)<2048);assert.equal(raw.includes('do-not-retain-this-argument'),false);
  assert.equal(JSON.parse(raw).transaction_id,uuid(99));
  await w.page.locator('#refresh-readiness').click();await w.idle();assert.equal(await w.page.locator('#open-session').isDisabled(),true);
  await w.page.locator('#disconnect').click();assert.equal(await w.page.locator('#hold-warning').isVisible(),true);
  const before=w.requests.filter(r=>r.path!=='/api/readiness').length;
  await w.page.reload();assert.equal(await w.page.locator('#hold-warning').isVisible(),true);
  await w.connect();assert.equal(await w.page.locator('#open-session').isDisabled(),true);assert.equal(await w.page.locator('#call').isDisabled(),true);
  assert.equal(w.requests.filter(r=>r.path!=='/api/readiness').length,before);
});

test('reload while checkpoint request is outstanding restores persisted uncertainty before any new dispatch',{timeout:30000},async t=>{
  const w=await setup(t);await w.open();w.overrides['/api/calls']=()=>({defer:true});
  await w.select('ncp_plan_create');await w.page.locator('#confirm-write').check();await w.page.locator('#call').click();
  await w.page.waitForFunction(key=>JSON.parse(sessionStorage.getItem(key)||'null')?.reason==='request_in_flight',HOLD);
  const checkpointCount=()=>w.requests.filter(r=>r.path!=='/api/readiness').length;
  await w.page.waitForTimeout(30);const before=checkpointCount();assert.equal(w.requests.filter(r=>r.path==='/api/calls').length,1);
  await w.page.reload();assert.equal(await w.page.locator('#hold-warning').isVisible(),true);
  await w.connect();assert.equal(await w.page.locator('#open-session').isDisabled(),true);
  assert.equal(checkpointCount(),before);assert.equal(await w.page.locator('#restart').isDisabled(),true);
});

test('late valid receipt cannot clear a different uncertainty marker',{timeout:30000},async t=>{
  const w=await setup(t);await w.open();
  const other={schema:'odsp.ui-uncertainty.v1',effect_state:'unconfirmed',transaction_id:uuid(777),recorded_at:'2026-11-02T00:00:00.000Z',reason:'another_request_uncertain',request_id:uuid(778)};
  w.overrides['/api/calls']=async()=>{await w.page.evaluate(({key,value})=>sessionStorage.setItem(key,JSON.stringify(value)),{key:HOLD,value:other});return {body:w.receipt({isError:false,structuredContent:{data:{}}})};};
  await w.call('ncp_plan_create');assert.equal(await w.page.locator('#hold-warning').isVisible(),true);
  assert.deepEqual(JSON.parse(await w.page.evaluate(key=>sessionStorage.getItem(key),HOLD)),other);
  assert.equal(await w.page.locator('#open-session').isDisabled(),true);
});

test('malformed and missing-retention replies stay unknown and preserve complete received response bytes',{timeout:30000},async t=>{
  for(const raw of ['[]\n','not json\n','{"data":{"isError":false}}\n']){
    await t.test(JSON.stringify(raw),async st=>{
      const w=await setup(st);await w.open();w.overrides['/api/calls']=()=>({raw});
      await w.call('ncp_plan_create');assert.equal(await w.page.locator('#hold-warning').isVisible(),true);
      assert.equal(await w.page.locator('#result').textContent(),raw);
      const [download]=await Promise.all([w.page.waitForEvent('download'),w.page.locator('#download').click()]);
      assert.deepEqual(fs.readFileSync(await download.path()),Buffer.from(raw));
    });
  }
});

test('lost checkpoint GET replies latch, while lost readiness remains read-only and retryable',{timeout:45000},async t=>{
  for(const target of ['status','tools','events','runs']){
    await t.test(target,async st=>{
      const w=await setup(st);
      if(['events','runs'].includes(target))await w.open();else await w.connect();
      w.overrides['/api/'+target]=()=>({abort:true});
      if(target==='status'||target==='tools')await w.page.locator('#open-session').click();
      else await w.page.locator(target==='events'?'#refresh-events':'#refresh-runs').click();
      await w.idle();assert.equal(await w.page.locator('#hold-warning').isVisible(),true);
      assert.equal(w.requests.filter(r=>r.path==='/api/'+target).length,1);
    });
  }
  await t.test('readiness',async st=>{
    const w=await setup(st);w.overrides['/api/readiness']=()=>({abort:true});await w.connect();
    assert.equal(await w.page.locator('#hold-warning').isHidden(),true);
    assert.equal(await w.page.evaluate(key=>sessionStorage.getItem(key),HOLD),null);
    assert.equal(await w.page.locator('#refresh-readiness').isEnabled(),true);
  });
});

test('storage failure refuses checkpoint dispatch; explicit empty, stale and UTC event views',{timeout:30000},async t=>{
  const w=await setup(t);await w.open();
  w.overrides['/api/events']=()=>({body:w.receipt([{operation:'ncp_plan_create',recorded_at:'2026-11-02T01:02:03.000Z',outcome:'refused',transaction_id:uuid(22)},{action:'legacy',outcome:'observed'}])});
  await w.page.locator('#refresh-events').click();await w.idle();
  assert.match(await w.page.locator('#event-list').innerText(),/2026-11-02 01:02:03.000 UTC/);assert.match(await w.page.locator('#event-list').innerText(),/Not reported/);
  await w.select('ncp_plan_create');await w.page.locator('#confirm-write').check();
  await w.page.evaluate(()=>{Storage.prototype.setItem=function(){throw new DOMException('blocked','SecurityError');};});
  await w.page.locator('#call').click();await w.idle();
  assert.equal(w.requests.filter(r=>r.path==='/api/calls').length,0);
  assert.equal(await w.page.locator('#call').isDisabled(),true);assert.match(await w.page.locator('#notice').innerText(),/Nothing was sent/);
});

test('UTF-8 request envelope bounds refuse before checkpoint intent or dispatch',{timeout:30000},async t=>{
  const w=await setup(t);await w.open();
  await w.call('ncp_plan_create',JSON.stringify({text:'Ω'.repeat(131073)}));
  assert.equal(w.requests.filter(r=>r.path==='/api/calls').length,0);
  assert.equal(await w.page.evaluate(key=>sessionStorage.getItem(key),HOLD),null);
  assert.equal(await w.page.locator('#hold-warning').isHidden(),true);
  assert.match(await w.page.locator('#call-state').innerText(),/^FAILED/);
  assert.match(await w.page.locator('#notice').innerText(),/262,144-byte/);
});
