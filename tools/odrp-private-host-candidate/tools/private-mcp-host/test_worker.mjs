import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {ROUTES,json} from './gateway.mjs';
const source=(await readFile(new URL('./worker.mjs',import.meta.url),'utf8'))
 .replace("import { DurableObject } from 'cloudflare:workers';",'const DurableObject=class { constructor(ctx,env){this.ctx=ctx;this.env=env;} };')
 .replace("import { route, json, ROUTES } from './gateway.mjs';",`const {json,ROUTES}=globalThis.__testGateway; const route=()=>{};`);
globalThis.__testGateway={json,ROUTES};
const {NCPHost}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
function host(initial={}) {
 const entries=new Map(Object.entries(initial));let writes=0;
 const storage={get:async k=>entries.get(k),put:async(k,v)=>{writes++;entries.set(k,v);},delete:async k=>entries.delete(k),transaction:async fn=>fn(storage)};
 const h=new NCPHost({storage},{RETAINED:{get:async()=>null,put:async()=>{}}});
 h.ready=async()=>{throw Error('must not start');};return {h,entries,writes:()=>writes};
}
const request=()=>new Request('https://ncp-control.notation-systems-lab.workers.dev/api/tools');
test('uncertain fence remains, no startup or mutation',async()=>{const {h,entries,writes}=host({pending:'historical-transaction'});const r=await h.transaction(request());assert.equal(r.status,409);assert.equal(entries.get('pending'),'historical-transaction');assert.equal(writes(),0);});
test('historical checkpoint cannot be auto-restored',async()=>{const {h,entries,writes}=host({committed:{key:'old'}});const r=await h.transaction(request());assert.equal((await r.json()).error.code,'historical_checkpoint_migration_required');assert.equal(entries.get('committed').key,'old');assert.equal(writes(),0);});
test('failed transaction retains uncertainty fence',async()=>{const {h,entries}=host();h.ready=async()=>({fetch:async()=>{throw Error('interrupted');}});const r=await h.transaction(request());assert.equal(r.status,503);assert.ok(entries.get('pending'));assert.equal(entries.has('committed'),false);});
test('success commits generation then clears fence',async()=>{const {h,entries}=host();h.ready=async()=>({fetch:async()=>Response.json({state:'synthetic-checkpoint',result:{data:{}},status:200})});const r=await h.transaction(request());assert.equal(r.status,200);assert.equal(entries.get('committed').generation,'odsp-24f6ef-odrp-v3-isolated-candidate');assert.equal(entries.has('pending'),false);});
