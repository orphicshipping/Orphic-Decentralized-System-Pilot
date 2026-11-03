// Authenticated browser boundary for the private gateway. No retries.
import {accessVerifier, validateClaims} from './access.mjs';
import {deadline, declaredLength, encoder, exact, readBounded, uniqueJson, utf8} from './bounds.mjs';

export const LIMITS = Object.freeze({requestBytes: 262144, responseBytes: 32 * 1024 * 1024,
  requestMs: 5000, backendMs: 365000, readinessMs: 35000, assetBytes: 1024 * 1024,
  allAssetBytes: 4 * 1024 * 1024});
const ROUTES = new Set(['GET /api/status', 'GET /api/tools', 'GET /api/events', 'GET /api/runs',
  'GET /api/readiness', 'POST /api/calls', 'POST /api/resources', 'POST /api/diagnostics']);
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$/;
const SECURITY = Object.freeze({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
  'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'none'"});
const errorResponse = (error, status, effect = 'not_started') => Response.json(
  {error: {code: error, effect_state: effect}, authorizes_execution: false}, {status, headers: SECURITY});
const hostname = value => typeof value === 'string' && value.length <= 253
  && value.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
function origin(value) {
  if (typeof value !== 'string' || value.length > 512) throw Error('panel_configuration_refused');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.origin !== value || !hostname(url.hostname)
      || url.port || url.username || url.password) throw Error('panel_configuration_refused');
  return value;
}
function configuration(env) {
  if (!hostname(env?.PANEL_HOSTNAME) || typeof env.BACKEND?.fetch !== 'function'
      || typeof env.ACCESS_AUDIENCE !== 'string' || !env.ACCESS_AUDIENCE
      || encoder.encode(env.ACCESS_AUDIENCE).length > 128 || /[\u0000-\u001f\u007f]/u.test(env.ACCESS_AUDIENCE))
    throw Error('panel_configuration_refused');
  const issuer = origin(env.ACCESS_ISSUER), backend = origin(env.PANEL_BACKEND_ORIGIN);
  if (!new URL(issuer).hostname.endsWith('.cloudflareaccess.com')
      || typeof env.PANEL_PRINCIPALS_JSON !== 'string'
      || encoder.encode(env.PANEL_PRINCIPALS_JSON).length > 65536) throw Error('panel_configuration_refused');
  const mapping = uniqueJson(env.PANEL_PRINCIPALS_JSON);
  if (!exact(mapping, ['schema', 'principals']) || mapping.schema !== 'odsp.panel-principals.v2'
      || !Array.isArray(mapping.principals) || mapping.principals.length < 1 || mapping.principals.length > 64)
    throw Error('panel_configuration_refused');
  const subjects = new Set(), principals = new Set(), tokens = new Set(), credentials = new Set();
  for (const row of mapping.principals) {
    if (!exact(row, ['subject', 'principal', 'enabled', 'backend_token', 'credential_id', 'not_before', 'expires_at'])
        || typeof row.subject !== 'string' || !row.subject || encoder.encode(row.subject).length > 512
        || /[\u0000-\u001f\u007f]/u.test(row.subject) || typeof row.principal !== 'string' || !ID.test(row.principal)
        || typeof row.enabled !== 'boolean' || typeof row.backend_token !== 'string'
        || !/^[\x21-\x7e]{32,512}$/.test(row.backend_token)
        || typeof row.credential_id !== 'string' || !ID.test(row.credential_id)
        || !Number.isSafeInteger(row.not_before) || row.not_before < 0 || !Number.isSafeInteger(row.expires_at)
        || row.expires_at <= row.not_before || row.expires_at - row.not_before > 3600
        || subjects.has(row.subject) || principals.has(row.principal) || tokens.has(row.backend_token)
        || credentials.has(row.credential_id)) throw Error('panel_configuration_refused');
    subjects.add(row.subject); principals.add(row.principal); tokens.add(row.backend_token); credentials.add(row.credential_id);
  }
  return {hostname: env.PANEL_HOSTNAME, issuer, audience: env.ACCESS_AUDIENCE, backend,
    binding: env.BACKEND, rows: mapping.principals};
}
function principal(config, subject, now) {
  const row = config.rows.find(row => row.subject === subject);
  const seconds = Math.floor(now / 1000);
  if (!Number.isSafeInteger(now) || now < 0 || !row?.enabled
      || seconds < row.not_before || seconds >= row.expires_at) throw Error('panel_access_denied');
  return row;
}
function sameCredential(a, b) {
  return ['subject', 'principal', 'backend_token', 'credential_id', 'not_before', 'expires_at']
    .every(key => a[key] === b[key]);
}
function prepareAssets(assets) {
  if (!assets || typeof assets !== 'object' || Array.isArray(assets) || Object.keys(assets).length > 32)
    throw Error('invalid_panel_assets');
  const prepared = new Map(); let total = 0;
  for (const [path, asset] of Object.entries(assets)) {
    if (!/^\/(?:[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\.(?:html|js|css|json))?$/.test(path)
        || path.startsWith('/api/') || !exact(asset, ['body', 'contentType']) || typeof asset.body !== 'string'
        || !['text/html; charset=utf-8', 'text/javascript; charset=utf-8', 'text/css; charset=utf-8',
          'application/json; charset=utf-8'].includes(asset.contentType)) throw Error('invalid_panel_assets');
    const body = encoder.encode(asset.body); total += body.length;
    if (body.length > LIMITS.assetBytes || total > LIMITS.allAssetBytes) throw Error('panel_asset_budget');
    prepared.set(path, {body, contentType: asset.contentType});
  }
  return prepared;
}
function sameBinding(a, b) {
  return ['hostname', 'issuer', 'audience', 'backend', 'binding'].every(key => a[key] === b[key]);
}

export function createPanel({assets, fetchJwks = globalThis.fetch, now = () => Date.now()}) {
  if (typeof fetchJwks !== 'function' || typeof now !== 'function') throw Error('invalid_panel_runtime');
  const files = prepareAssets(assets), verify = accessVerifier({fetchJwks, now});
  return {async fetch(request, env) {
    let config, url;
    try { config = configuration(env); url = new URL(request.url); }
    catch { return errorResponse('panel_configuration_refused', 503); }
    const expectedOrigin = 'https://' + config.hostname;
    if (url.protocol !== 'https:' || url.host !== config.hostname || url.username || url.password
        || url.search || url.hash || /[?#]/.test(request.url) || url.pathname.includes('%')
        || (request.headers.has('host') && request.headers.get('host') !== config.hostname)
        || (request.headers.has('origin') && request.headers.get('origin') !== expectedOrigin)
        || (request.headers.has('sec-fetch-site') && !['same-origin', 'none'].includes(request.headers.get('sec-fetch-site')))
        || (request.method === 'POST' && request.headers.get('origin') !== expectedOrigin))
      return errorResponse('panel_origin_refused', 403);
    let claims, authenticated;
    try {
      claims = await verify(request.headers.get('cf-access-jwt-assertion'), config, request.signal);
      authenticated = principal(config, claims.sub, now());
    } catch { return errorResponse('panel_access_denied', 401); }
    // Cookie-authenticated browser navigation/simple cross-origin requests must
    // not start even a GET custody transaction. Cross-origin scripts cannot add
    // this header without a preflight, for which no CORS permission is emitted.
    if (url.pathname.startsWith('/api/') && request.headers.get('x-odsp-panel-request') !== '1')
      return errorResponse('panel_request_header_required', 403);
    const api = ROUTES.has(request.method + ' ' + url.pathname), asset = files.get(url.pathname);
    if (!api && !(request.method === 'GET' && asset)) return errorResponse('panel_not_found', 404);
    let body;
    if (request.method === 'POST') {
      if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json')
        return errorResponse('panel_invalid_content_type', 415);
      try {
        declaredLength(request.headers, LIMITS.requestBytes);
        body = await deadline(signal => readBounded(request.body, LIMITS.requestBytes, signal), LIMITS.requestMs, request.signal);
        const payload = uniqueJson(utf8(body));
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)
            || ['principal', 'owner', 'identity', 'grants', 'policy', 'policy_revision', 'policy_sha256',
              'token', 'backend_token', 'destination', 'endpoint', 'credential_id', 'not_before', 'expires_at']
              .some(key => Object.hasOwn(payload, key)))
          throw Error('invalid_request');
      } catch { return errorResponse('panel_request_refused', 400); }
    }
    // Do not cache subject grants. Recheck after every asynchronous preflight, so
    // disabling a mapping or changing an issuer cannot dispatch a stale identity.
    function currentCredential() {
      try {
        const current = configuration(env), instant = now();
        if (!sameBinding(config, current)) throw Error('panel_configuration_changed');
        validateClaims(claims, current, instant);
        const row = principal(current, claims.sub, instant);
        if (!sameCredential(row, authenticated) || request.signal.aborted) throw Error('panel_access_denied');
        return {current, row};
      } catch { throw Error('panel_access_denied'); }
    }
    try { currentCredential(); }
    catch { return errorResponse('panel_access_denied', 401); }
    if (!api) return new Response(asset.body, {headers: {...SECURITY, 'Content-Type': asset.contentType}});
    const readiness = url.pathname === '/api/readiness';
    let dispatched = false;
    try {
      return await deadline(async signal => {
        const {current, row} = currentCredential();
        const headers = {Accept: 'application/json', Authorization: 'Bearer ' + row.backend_token};
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        const forwarded = new Request(current.backend + url.pathname,
          {method: request.method, headers, ...(body === undefined ? {} : {body}), redirect: 'manual', signal});
        dispatched = true;
        const response = await current.binding.fetch(forwarded);
        if (!(response instanceof Response) || response.redirected || response.status < 200 || response.status >= 300 && response.status < 400
            || response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
          void response?.body?.cancel().catch(() => {});
          throw Error('backend_response_refused');
        }
        let bytes;
        try {
          currentCredential();
          declaredLength(response.headers, LIMITS.responseBytes);
          bytes = await readBounded(response.body, LIMITS.responseBytes, signal);
          // Backend completion and body streaming are asynchronous. Suppress all
          // result bytes if this exact mapping or the Access assertion expired.
          currentCredential();
        }
        catch (error) { void response.body?.cancel().catch(() => {}); throw error; }
        // Preserve the complete JSON bytes/status, including non-2xx error and
        // retention envelopes. No claim of commitment is added by this boundary.
        return new Response(bytes, {status: response.status, headers: {...SECURITY,
          'Content-Type': 'application/json; charset=utf-8'}});
      }, readiness ? LIMITS.readinessMs : LIMITS.backendMs, request.signal);
    } catch (error) {
      if (error?.message === 'panel_access_denied')
        return errorResponse('panel_access_denied', 401, dispatched && !readiness ? 'unconfirmed' : 'not_started');
      return errorResponse('panel_backend_unconfirmed', 502, dispatched && !readiness ? 'unconfirmed' : 'not_started');
    }
  }};
}
