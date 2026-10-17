const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('web/index.html','utf8');
const script = fs.readFileSync('web/app.js','utf8');
const status = {connection:'connected',transport:'stdio',protocol:'2026-07-28',profile:'profile.json',instrument:'builtin',tools:2,known_runs:0};
const tools = [
  {name:'ncp_capabilities_list',description:'Read bound capabilities',inputSchema:{type:'object',properties:{}},console_access:'read'},
  {name:'ncp_plan_admit',description:'Admit a plan',inputSchema:{type:'object',required:['plan_id','expected_digest'],properties:{plan_id:{type:'string'},expected_digest:{type:'string'}}},console_access:'confirm'}
];
async function setup() {
  const dom = new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'outside-only',pretendToBeVisual:true});
  const calls=[];
  dom.window.fetch=async (url,options)=>{
    calls.push({url,options});
    const data=url.endsWith('/status')?status:url.endsWith('/tools')?tools:url.endsWith('/runs')?{items:[]}:url.endsWith('/events')?[]:{isError:false,content:[{type:'text',text:'<img src=x onerror=alert(1)>'}]};
    return {ok:true,json:async()=>({data})};
  };
  dom.window.eval(script);
  const doc=dom.window.document;
  doc.getElementById('token').value='t'.repeat(40);
  doc.getElementById('connect-form').dispatchEvent(new dom.window.Event('submit',{cancelable:true}));
  await new Promise(resolve=>setTimeout(resolve,30));
  return {dom,doc,calls};
}

test('unlock selects the first discovered tool without executing it',async()=>{
  const {dom,doc,calls}=await setup();
  try {
    assert.equal(doc.getElementById('selected-name').textContent,'ncp_capabilities_list');
    assert.equal(doc.getElementById('call').disabled,false);
    assert.equal(calls.some(c=>c.url.endsWith('/calls')),false);
    assert.equal(doc.getElementById('token').value,'');
  } finally {dom.window.close();}
});

test('changing arguments invalidates confirmation; response content is rendered as text',async()=>{
  const {dom,doc,calls}=await setup();
  try {
    doc.querySelectorAll('.tool-item')[1].click();
    assert.equal(doc.getElementById('call').disabled,true);
    doc.getElementById('confirm-write').click();
    assert.equal(doc.getElementById('call').disabled,false);
    doc.getElementById('arguments').value='{"plan_id":"id","expected_digest":"digest"}';
    doc.getElementById('arguments').dispatchEvent(new dom.window.Event('input'));
    assert.equal(doc.getElementById('call').disabled,true);
    doc.getElementById('confirm-write').click();
    doc.getElementById('call').click();
    await new Promise(resolve=>setTimeout(resolve,30));
    assert.equal(calls.filter(c=>c.url.endsWith('/calls')).length,1);
    assert.equal(doc.getElementById('result').querySelector('img'),null);
    assert.match(doc.getElementById('result').textContent,/<img/);
    assert.equal(doc.getElementById('confirm-write').checked,false);
  } finally {dom.window.close();}
});

test('network failure does not automatically retry a write',async()=>{
  const {dom,doc,calls}=await setup();
  try {
    doc.querySelectorAll('.tool-item')[1].click();
    doc.getElementById('confirm-write').click();
    let writes=0;
    const original=dom.window.fetch;
    dom.window.fetch=async(url,options)=>{
      if(url.endsWith('/calls')){writes++;throw new dom.window.TypeError('network');}
      return original(url,options);
    };
    doc.getElementById('call').click();
    await new Promise(resolve=>setTimeout(resolve,30));
    assert.equal(writes,1);
    assert.match(doc.getElementById('notice').textContent,/may have applied/);
    assert.equal(doc.getElementById('confirm-write').checked,false);
  } finally {dom.window.close();}
});
