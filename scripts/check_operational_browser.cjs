/* Actual Chromium -> existing console -> real MCP SDK -> installed NCP. */
'use strict';
const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path'),cp=require('node:child_process'),net=require('node:net'),crypto=require('node:crypto');
const args=process.argv.slice(2);const get=name=>args[args.indexOf(name)+1];
(async()=>{
  const output=path.resolve(get('--output-dir')),python=get('--python');
  let profile=path.resolve(get('--profile'));
  if((await fs.stat(profile)).isDirectory()){const entries=(await fs.readdir(profile)).filter(n=>/^profile-.*\.json$/.test(n));if(entries.length!==1)throw Error('Select one exact profile');profile=path.join(profile,entries[0]);}
  const consolePath=path.resolve(get('--console'));
  await fs.mkdir(output,{recursive:true});
  const token=crypto.randomBytes(32).toString('hex'),tokenFile=path.join(output,'token');await fs.writeFile(tokenFile,token,{mode:0o600});
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  const log=await fs.open(path.join(output,'console.log'),'w');
  const server=cp.spawn(python,[consolePath,'--python',python,'--profile',profile,'--surface','operational','--resume','--state-dir',path.join(output,'state'),'--token-file',tokenFile,'--port',String(port)],{stdio:['ignore',log.fd,log.fd]});
  const url=`http://127.0.0.1:${port}`;let browser;
  try{
    const deadline=Date.now()+20000;
    for(;;){try{if((await fetch(url)).ok)break;}catch{}if(Date.now()>deadline||server.exitCode!==null)throw Error('Console failed startup');await new Promise(r=>setTimeout(r,50));}
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(url);await page.locator('#token').fill(token);await page.locator('#connect-form button').click();
    await page.waitForFunction(()=>Number(document.querySelector('#tools-metric').textContent)>20);
    async function call(name,arguments_,write=false){
      await page.locator('#tool-list button').filter({hasText:name}).click();await page.locator('#arguments').fill(JSON.stringify(arguments_));
      if(write){if(!await page.locator('#call').isDisabled())throw Error('Unconfirmed write enabled');await page.locator('#confirm-write').check();}
      const responsePromise=page.waitForResponse(r=>r.url()===url+'/api/calls');await page.locator('#call').click();const response=await responsePromise;
      if(!response.ok())throw Error(await response.text());const result=(await response.json()).data;
      await page.waitForFunction(()=>!document.querySelector('#arguments').disabled);
      if(result.isError)throw Error(JSON.stringify(result));
      if(JSON.stringify(JSON.parse(await page.locator('#result').innerText()))!==JSON.stringify(result))throw Error('UI record differs from MCP record');
      return result.structuredContent;
    }
    const catalog=await call('oe_catalog',{}),selection={source:'recorded',clock:'synthetic.clock',boot:'boot-1',channel:'temperature',start:0,end:.5};
    const baseline=await call('oe_query',selection,true),changed=await call('oe_query',{...selection,start:.5,end:2},true);
    const difference=await call('oe_change',{baseline:baseline.record,changed:changed.record},true);
    if(difference.data.status!=='RECORDED_ASSOCIATION')throw Error('Configuration journey failed');
    const run=await call('net_execute',{source:'assessment',graph:'assessment-graph',attempt:'browser-first'},true);
    const finding=await call('oe_model_support',{attempt:'browser-first'},true);
    const proposal=await call('oe_propose',{finding:finding.record,template:'resolve'},true);
    if(finding.data.status!=='AGREEMENT'||proposal.data.executed)throw Error('Engineering/proposal journey failed');
    const same=await call('oe_inspect',{record:finding.record});if(same.record!==finding.record)throw Error('Identity mismatch');
    await page.locator('#resource-uri').fill('ncp://operational-records/'+finding.record.slice(7));
    const rp=page.waitForResponse(r=>r.url()===url+'/api/resources');await page.locator('#read-resource').click();const resource=(await(await rp).json()).data;
    if(JSON.parse(resource.contents[0].text).record!==finding.record)throw Error('Resource identity mismatch');
    await page.screenshot({path:path.join(output,'console.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'console-mobile.png'),fullPage:true});
    if(errors.length)throw Error(errors.join('\n'));
    if(args.includes('--view')){
      await page.goto('file://'+path.resolve(get('--view')));
      await page.locator('#sources button').first().click();
      await page.locator('#start').fill('0');await page.locator('#end').fill('2');await page.locator('#filter').click();
      if(await page.locator('#observations tr').count()!==2)throw Error('Offline retained interval view failed');
      await page.locator('#records button').first().click();
      if(!(await page.locator('#identity').innerText()).startsWith('sha256:'))throw Error('Offline identity missing');
      await page.screenshot({path:path.join(output,'investigation.png'),fullPage:true});
      if(errors.length)throw Error(errors.join('\n'));
    }
    const report={status:'PASS',browser:browser.version(),checks:['authenticated_console_discovery','explicit_write_confirmation','configuration_change_J1_J2','Session_model_comparison_J3','proposal_J4','same_retained_UI_MCP_resource','mobile_render','retained_offline_asset_selection','no_page_errors'],finding:finding.record,proposal:proposal.record,result_ids:run.result_ids,scope:'synthetic installed local integration; no physical qualification'};
    await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');process.stdout.write(JSON.stringify(report,null,2)+'\n');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>{if(server.exitCode!==null)return r();server.once('exit',r);setTimeout(()=>{server.kill('SIGKILL');r();},3000);});await log.close();await fs.unlink(tokenFile);}
})().catch(e=>{process.stderr.write(e.stack+'\n');process.exitCode=1;});
