// Authentication configuration is host supplied. No default credentials or route.
export const ROUTES = new Map([
  ['GET /api/status', 'status'], ['GET /api/tools', 'tools'],
  ['GET /api/events', 'events'], ['GET /api/runs', 'runs'],
  ['GET /api/readiness', 'readiness'],
  ['POST /api/calls', 'call'], ['POST /api/resources', 'resource'],
  ['POST /api/diagnostics', 'diagnostics'],
]);
export const ODRP_READ_NAMES = new Set(['ncp_system_status', 'ncp_knowledge_overview',
  'ncp_knowledge_search', 'ncp_knowledge_resolve', 'ncp_knowledge_source',
  'ncp_knowledge_relations', 'ncp_knowledge_review_queue', 'ncp_evidence_overview',
  'ncp_evidence_search', 'ncp_evidence_representations', 'ncp_evidence_source', 'ncp_evidence_span']);
export const REQUEST_BODY_TIMEOUT_MS = 5000; // Local bound, not a deployed SLO.
export const MAX_CREDENTIAL_SECONDS = 3600;
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$/;
const REF = /^sha256:[0-9a-f]{64}$/;
const encoder = new TextEncoder();
const exact = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('\0') === [...fields].sort().join('\0');
export function json(value, status = 200) {
  return Response.json(value, {status, headers: {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}});
}
async function digest(value) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value)))].map(x => x.toString(16).padStart(2,'0')).join('');
}
function parseUniqueJson(text) {
  // Native parsing owns JSON syntax/values. Before reserialization can erase
  // duplicates, scan the already byte-bounded input for object key tokens.
  // This iterative scan does not recurse or parse JSON-looking string values.
  const value = JSON.parse(text), containers = [];
  for (let i = 0; i < text.length;) {
    const character = text[i];
    if (character === '{' || character === '[') {
      containers.push(character === '{' ? new Set() : null); i++;
    } else if (character === '}' || character === ']') {
      containers.pop(); i++;
    } else if (character === '"') {
      const start = i++;
      while (i < text.length) {
        if (text[i] === '\\') { i += 2; continue; }
        if (text[i++] === '"') break;
      }
      let next = i;
      while (next < text.length && /[ \t\r\n]/.test(text[next])) next++;
      // In syntactically valid JSON only an object key is followed by ':';
      // JSON.parse decodes escaped-equivalent spellings for Set comparison.
      if (text[next] === ':') {
        const keys = containers.at(-1), key = JSON.parse(text.slice(start, i));
        if (!keys || keys.has(key)) throw Error('duplicate_json_key');
        keys.add(key);
      }
    } else i++;
  }
  return value;
}
function authConfig(env) {
  if (typeof env.PRINCIPAL_AUTH_JSON !== 'string' || encoder.encode(env.PRINCIPAL_AUTH_JSON).length > 65536)
    throw Error('authentication_configuration_refused');
  const config = parseUniqueJson(env.PRINCIPAL_AUTH_JSON);
  if (!exact(config, ['schema','policy_revision','policy_sha256','principals']) || config.schema !== 'ciw.bearer-principals.v2'
      || typeof config.policy_revision !== 'string' || !ID.test(config.policy_revision)
      || typeof config.policy_sha256 !== 'string' || !REF.test(config.policy_sha256)
      || !Array.isArray(config.principals) || config.principals.length < 1 || config.principals.length > 64)
    throw Error('authentication_configuration_refused');
  const ids = new Set(), hashes = new Set(), credentials = new Set();
  for (const row of config.principals) {
    if (!exact(row, ['principal','enabled','token_sha256','credential_id','not_before','expires_at'])
        || typeof row.principal !== 'string' || !ID.test(row.principal)
        || typeof row.enabled !== 'boolean' || typeof row.token_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(row.token_sha256)
        || typeof row.credential_id !== 'string' || !ID.test(row.credential_id)
        || !Number.isSafeInteger(row.not_before) || row.not_before < 0 || !Number.isSafeInteger(row.expires_at)
        || row.expires_at <= row.not_before || row.expires_at - row.not_before > MAX_CREDENTIAL_SECONDS
        || ids.has(row.principal) || hashes.has(row.token_sha256) || credentials.has(row.credential_id))
      throw Error('authentication_configuration_refused');
    ids.add(row.principal); hashes.add(row.token_sha256); credentials.add(row.credential_id);
  }
  return config;
}
function configuredPrincipal(config, principal, now) {
  const row = config.principals.find(row => row.principal === principal);
  const seconds = Math.floor(now / 1000);
  if (!Number.isSafeInteger(now) || now < 0 || !row || !row.enabled
      || seconds < row.not_before || seconds >= row.expires_at) throw Error('principal_refused');
  return row;
}
function credentialKey(row) {
  return JSON.stringify(['ciw.bearer-credential.v2', row.principal, row.credential_id,
    row.token_sha256, row.not_before, row.expires_at]);
}
function recheck(env, config, row, now) {
  const current = authConfig(env), active = configuredPrincipal(current, row.principal, now);
  if (config.policy_revision !== current.policy_revision || config.policy_sha256 !== current.policy_sha256
      || credentialKey(active) !== credentialKey(row)) throw Error('principal_refused');
}
async function context(config, principal) {
  return {schema:'ciw.principal-context.v1', principal, owner:'principal:' + await digest(principal),
    policy_revision:config.policy_revision, policy_sha256:config.policy_sha256};
}
// Only call on the internal binding. route() reconstructs every header first.
// The Worker rechecks current auth configuration after waiting in its queue.
export async function principalContext(request, env, now = () => Date.now()) {
  const config = authConfig(env), principal = request.headers.get('x-odsp-principal');
  if (typeof principal !== 'string' || !ID.test(principal)) throw Error('principal_refused');
  const row = configuredPrincipal(config, principal, now());
  const [value, credentialHash] = await Promise.all([context(config, principal), digest(credentialKey(row))]);
  recheck(env, config, row, now());
  if (request.headers.get('x-odsp-owner') !== value.owner
      || request.headers.get('x-odsp-policy-revision') !== value.policy_revision
      || request.headers.get('x-odsp-policy-sha256') !== value.policy_sha256
      || request.headers.get('x-odsp-credential-id') !== row.credential_id
      || request.headers.get('x-odsp-credential-sha256') !== credentialHash) throw Error('principal_refused');
  return value;
}
export async function route(request, env, _executionContext, now = () => Date.now()) {
  const url = new URL(request.url);
  if (typeof env.PUBLIC_HOSTNAME !== 'string' || !/^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$/.test(env.PUBLIC_HOSTNAME)
      || typeof env.HOST_SINGLETON !== 'string' || !ID.test(env.HOST_SINGLETON))
    return json({error:{code:'authentication_configuration_refused'}}, 503);
  if (url.protocol !== 'https:' || url.host !== env.PUBLIC_HOSTNAME || url.search || request.headers.has('origin'))
    return json({error: {code: 'host_or_origin_denied'}}, 403);
  let config;
  try { config = authConfig(env); }
  catch { return json({error:{code:'authentication_configuration_refused'}},503); }
  const binding = env.HOST, singleton = env.HOST_SINGLETON, hostname = env.PUBLIC_HOSTNAME;
  const supplied = request.headers.get('authorization') || '';
  const match = /^Bearer ([\x21-\x7e]{32,512})$/.exec(supplied);
  if (!match) return json({error:{code:'unauthorized'}},401);
  const hash = await digest(match[1]);
  let authenticated = null;
  // Compare all fixed-size digests. Config prevents ambiguous shared secrets.
  for (const row of config.principals) {
    let difference = 0;
    for (let i=0; i<64; i++) difference |= hash.charCodeAt(i) ^ row.token_sha256.charCodeAt(i);
    if (difference === 0) authenticated = row;
  }
  try {
    if (authenticated === null) throw Error('principal_refused');
    recheck(env, config, authenticated, now());
  } catch { return json({error:{code:'unauthorized'}},401); }
  if (!ROUTES.has(`${request.method} ${url.pathname}`)) return json({error:{code:'not_found'}},404);
  let body, serializedBody;
  if (request.method === 'POST') {
    if (request.headers.get('content-type')?.split(';')[0] !== 'application/json')
      return json({error: {code: 'invalid_content_type'}}, 415);
    const reader = request.body?.getReader();
    const chunks = []; let length = 0;
    if (reader) {
      let timer, aborted;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(Error('request_timeout')), REQUEST_BODY_TIMEOUT_MS);
        aborted = () => reject(Error('request_cancelled'));
        request.signal.addEventListener('abort', aborted, {once:true});
        if (request.signal.aborted) aborted();
      });
      try {
        for (;;) {
          const {done, value} = await Promise.race([reader.read(), timeout]);
          if (done) break;
          length += value.length;
          if (length > 262144) { void reader.cancel().catch(()=>{}); return json({error: {code: 'body_too_large'}}, 413); }
          chunks.push(value);
        }
      } catch {
        void reader.cancel().catch(()=>{});
        return json({error:{code:'request_timeout_or_cancelled'}},408);
      } finally {
        clearTimeout(timer);
        request.signal.removeEventListener('abort', aborted);
      }
    }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    try { body = parseUniqueJson(new TextDecoder('utf-8', {fatal: true}).decode(bytes)); }
    catch { return json({error: {code: 'invalid_json'}}, 400); }
    if (!body || typeof body !== 'object' || Array.isArray(body)
        || ['principal','owner','identity','grants','policy','policy_revision','policy_sha256',
          'credential_id','not_before','expires_at'].some(key => key in body))
      return json({error: {code: 'invalid_request'}}, 400);
    // Native parsing can accept nesting the serializer cannot safely traverse.
    // Refuse before identity/host resolution and reuse the checked wire body.
    try { serializedBody = JSON.stringify(body); }
    catch { return json({error: {code: 'invalid_json'}}, 400); }
  }
  let identity, credentialHash;
  try {
    [identity, credentialHash] = await Promise.all([context(config, authenticated.principal), digest(credentialKey(authenticated))]);
    // Recheck after body upload and both async digests, immediately before dispatch.
    recheck(env, config, authenticated, now());
    if (request.signal.aborted || env.HOST !== binding || env.HOST_SINGLETON !== singleton
        || env.PUBLIC_HOSTNAME !== hostname) throw Error('principal_refused');
  } catch { return json({error:{code:'unauthorized'}},401); }
  // Neither credentials nor caller internal headers are forwarded, even on GET.
  const headers = {'x-odsp-principal':identity.principal,'x-odsp-owner':identity.owner,
    'x-odsp-policy-revision':identity.policy_revision,'x-odsp-policy-sha256':identity.policy_sha256,
    'x-odsp-credential-id':authenticated.credential_id,'x-odsp-credential-sha256':credentialHash};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const forwarded = new Request(url, {method:request.method,headers,
    ...(serializedBody === undefined ? {} : {body:serializedBody})});
  return binding.getByName(singleton).fetch(forwarded);
}
