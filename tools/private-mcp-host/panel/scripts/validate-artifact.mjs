import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../dist/server/index.js',import.meta.url),'utf8');
const manifest = JSON.parse(await readFile(new URL('../dist/.openai/hosting.json',import.meta.url),'utf8'));
assert.equal(manifest.project_id,'appgprj_6ad3bdd3f9108191ab78b47d61ceae35');
assert.equal(manifest.static,undefined);
const {default:worker} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const origin='https://ncp-live-workbench.notationsystems.chatgpt.site';
const page=await worker.fetch(new Request(origin),{});
assert.equal(page.status,200);
assert.match(await page.text(),/LIVE MCP CAPABILITY REGISTRY/);
assert.equal((await worker.fetch(new Request(origin+'/api/status'),{})).status,503);
assert.equal((await worker.fetch(new Request(origin+'/api/calls',{method:'POST',headers:{origin:'https://evil.example'}}),{NCP_BACKEND_TOKEN:'test-only'})).status,403);
assert.equal((await worker.fetch(new Request(origin+'/api/shell'),{})).status,404);
const originalFetch=globalThis.fetch;
globalThis.fetch=async (url, options)=>{
  assert.equal(url,'https://ncp-control.notation-systems-lab.workers.dev/api/tools');
  assert.deepEqual(options.headers,{Authorization:'Bearer test-only'});
  return Response.json({data:[{name:'ncp_capabilities_list'}]});
};
try {
  const proxied=await worker.fetch(new Request(origin+'/api/tools',{headers:{authorization:'attacker-supplied'}}),{NCP_BACKEND_TOKEN:'test-only'});
  assert.equal(proxied.status,200);
  assert.deepEqual(await proxied.json(),{data:[{name:'ncp_capabilities_list'}]});
} finally {globalThis.fetch=originalFetch;}
console.log('Site artifact and authenticated proxy boundary checks passed.');
