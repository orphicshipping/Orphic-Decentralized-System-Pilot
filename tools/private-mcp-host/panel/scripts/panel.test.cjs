const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {JSDOM}=require('jsdom');
const names=['statistics.v1','spectrum.periodogram.v1','uncertainty.propagate.v1','uncertainty.budget.v1','calibration.fit.v1','calibration.residuals.v1','sensitivity.local.v1','observability.rank.v1','model.discrepancy.v1'];
test('live discovery renders all nine operations without shipping backend credentials',async()=>{
  const dom=new JSDOM(readFileSync('web/index.html','utf8'),{url:'https://ncp-live-workbench.notationsystems.chatgpt.site',runScripts:'outside-only',pretendToBeVisual:true});
  const calls=[];
  dom.window.HTMLElement.prototype.scrollIntoView=function(){};
  dom.window.fetch=async(url,options)=>{
    calls.push({url,options});
    const data=url.endsWith('/status')?{connection:'connected',tools:14,known_runs:0,profile:'profile.json',instrument:'builtin'}:
      url.endsWith('/tools')?[{name:'ncp_capability_describe',description:'Describe',inputSchema:{type:'object',required:['capability_id'],properties:{capability_id:{type:'string'}}},console_access:'read'}]:
      url.endsWith('/calls')?{structuredContent:{data:{items:names.map((name,i)=>({operation_id:name,capability_id:name,authorized:i>1}))}}}:
      url.endsWith('/runs')?{items:[]}:[];
    return {ok:true,json:async()=>({data})};
  };
  dom.window.eval(readFileSync('web/app.js','utf8'));
  await new Promise(resolve=>setTimeout(resolve,60));
  try {
    const doc=dom.window.document;
    assert.equal(doc.querySelectorAll('#operation-list article').length,9);
    assert.equal(doc.querySelector('#operation-count').textContent,'9');
    assert.ok(calls.every(c=>!Object.hasOwn(c.options.headers,'Authorization')));
    doc.querySelector('#operation-list button').click();
    assert.deepEqual(JSON.parse(doc.querySelector('#arguments').value),{capability_id:'statistics.v1'});
    assert.equal(calls.filter(c=>c.url==='/api/calls').length,1,'only read-only capability discovery is automatic');
  } finally {dom.window.close();}
});

test('scientific JSON is submitted verbatim instead of coercing float identity',async()=>{
  const dom=new JSDOM(readFileSync('web/index.html','utf8'),{url:'https://ncp-live-workbench.notationsystems.chatgpt.site',runScripts:'outside-only',pretendToBeVisual:true});
  const calls=[];
  dom.window.fetch=async(url,options)=>{
    calls.push({url,options});
    const body=options.body?JSON.parse(options.body):null;
    const data=url.endsWith('/status')?{connection:'connected',tools:1,known_runs:0,profile:'profile.json',instrument:'builtin'}:
      url.endsWith('/tools')?[{name:'ncp_plan_create',description:'Create',inputSchema:{type:'object'},console_access:'confirm'}]:
      url.endsWith('/calls')&&body.name==='ncp_capabilities_list'?{structuredContent:{data:{items:[]}}}:
      url.endsWith('/runs')?{items:[]}:url.endsWith('/events')?[]:{isError:false,structuredContent:{data:{}}};
    return {ok:true,json:async()=>({data})};
  };
  dom.window.eval(readFileSync('web/app.js','utf8'));
  await new Promise(resolve=>setTimeout(resolve,50));
  try {
    const doc=dom.window.document;
    doc.querySelector('#arguments').value='{"reference":1.0,"large":9007199254740993}';
    doc.querySelector('#confirm-write').click();
    doc.querySelector('#call').click();
    await new Promise(resolve=>setTimeout(resolve,30));
    const sent=calls.filter(c=>c.url==='/api/calls').map(c=>JSON.parse(c.options.body)).find(c=>c.name==='ncp_plan_create');
    assert.equal(sent.arguments_json,'{"reference":1.0,"large":9007199254740993}');
    assert.equal(sent.arguments,undefined);
    assert.equal(sent.confirmed,true);
  } finally {dom.window.close();}
});
