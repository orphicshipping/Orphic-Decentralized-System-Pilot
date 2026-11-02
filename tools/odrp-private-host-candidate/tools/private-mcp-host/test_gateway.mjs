import test from 'node:test';
import assert from 'node:assert/strict';
import { route } from './gateway.mjs';

test('anonymous requests cannot wake the private Python host', async () => {
  let calls = 0;
  const env = { ACCESS_TOKEN: 'a'.repeat(64), HOST: { getByName() { calls++; throw Error('must not wake'); } } };
  const response = await route(new Request('https://ncp-control.notation-systems-lab.workers.dev/api/tools'), env);
  assert.equal(response.status, 401);
  assert.equal(calls, 0);
});

test('foreign browser origins and unknown routes are rejected without waking', async () => {
  const env = {ACCESS_TOKEN: 'a'.repeat(64), HOST: {getByName() {throw Error('must not wake');}}};
  const headers = {authorization: `Bearer ${env.ACCESS_TOKEN}`, origin: 'https://evil.example'};
  assert.equal((await route(new Request(`https://ncp-control.notation-systems-lab.workers.dev/api/tools`, {headers}), env)).status, 403);
  delete headers.origin;
  assert.equal((await route(new Request(`https://ncp-control.notation-systems-lab.workers.dev/api/shell`, {headers}), env)).status, 404);
});

test('authenticated, bounded requests reach only the named singleton', async () => {
  let calls = 0;
  const env = {ACCESS_TOKEN: 'a'.repeat(64), HOST: {getByName(name) {
    assert.equal(name, 'odsp-odrp-v3-isolated-candidate');
    return {fetch(request) {calls++; return Response.json({ok: true});}};
  }}};
  const headers = {authorization: `Bearer ${env.ACCESS_TOKEN}`, 'content-type': 'application/json'};
  const base = 'https://ncp-control.notation-systems-lab.workers.dev';
  for (const [body, status] of [['[]', 400], ['{', 400], ['x'.repeat(262145), 413], ['{}', 200]]) {
    assert.equal((await route(new Request(base + '/api/diagnostics', {method: 'POST', headers, body}), env)).status, status);
  }
  assert.equal(calls, 1);
});

