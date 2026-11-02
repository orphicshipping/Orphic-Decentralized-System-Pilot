export const HOSTNAME = 'ncp-control.notation-systems-lab.workers.dev';
export const ROUTES = new Map([
  ['GET /api/status', 'status'], ['GET /api/tools', 'tools'],
  ['GET /api/events', 'events'], ['GET /api/runs', 'runs'],
  ['POST /api/calls', 'call'], ['POST /api/resources', 'resource'],
  ['POST /api/diagnostics', 'diagnostics'],
]);
export function json(value, status = 200) {
  return Response.json(value, {status, headers: {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}});
}
export async function route(request, env) {
  const url = new URL(request.url);
  if (url.hostname !== HOSTNAME || request.headers.has('origin'))
    return json({error: {code: 'host_or_origin_denied'}}, 403);
  // Compare fixed-size digests; do not publish credentials to the browser.
  const supplied = request.headers.get('authorization') || '';
  const expected = `Bearer ${env.ACCESS_TOKEN}`;
  const digest = async text => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
  const [a, b] = await Promise.all([digest(supplied), digest(expected)]);
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  if (!env.ACCESS_TOKEN || env.ACCESS_TOKEN.length < 32 || difference)
    return json({error: {code: 'unauthorized'}}, 401);
  if (!ROUTES.has(`${request.method} ${url.pathname}`))
    return json({error: {code: 'not_found'}}, 404);
  if (request.method === 'POST') {
    if (request.headers.get('content-type')?.split(';')[0] !== 'application/json')
      return json({error: {code: 'invalid_content_type'}}, 415);
    const reader = request.body?.getReader();
    const chunks = []; let length = 0;
    if (reader) for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      length += value.length;
      if (length > 262144) { await reader.cancel(); return json({error: {code: 'body_too_large'}}, 413); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    let body;
    try { body = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes)); }
    catch { return json({error: {code: 'invalid_json'}}, 400); }
    if (!body || typeof body !== 'object' || Array.isArray(body))
      return json({error: {code: 'invalid_request'}}, 400);
    request = new Request(url, {method: request.method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  }
  return env.HOST.getByName('odsp-odrp-v3-isolated-candidate').fetch(request);
}

