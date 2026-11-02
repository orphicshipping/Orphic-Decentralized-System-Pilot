// Verify the Access assertion cryptographically. Subject grants are never cached.
import {deadline, declaredLength, encoder, readBounded, uniqueJson, utf8} from './bounds.mjs';

export const ACCESS_LIMITS = Object.freeze({tokenBytes: 16384, jwksBytes: 65536,
  jwksKeys: 8, fetchMs: 5000, cacheMs: 60000, refreshCooldownMs: 30000});
const text = (v, max) => typeof v === 'string' && v.length > 0
  && encoder.encode(v).length <= max && !/[\u0000-\u001f\u007f]/u.test(v);

function decode(segment) {
  if (typeof segment !== 'string' || !/^[A-Za-z0-9_-]+$/.test(segment) || segment.length % 4 === 1)
    throw Error('invalid_access_token');
  const binary = atob(segment.replace(/-/g, '+').replace(/_/g, '/'));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  // Reject alternate encodings with nonzero trailing padding bits.
  if (btoa(binary).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_') !== segment)
    throw Error('invalid_access_token');
  return bytes;
}

function tokenParts(token) {
  if (typeof token !== 'string' || token.length > ACCESS_LIMITS.tokenBytes)
    throw Error('invalid_access_token');
  const parts = token.split('.');
  if (parts.length !== 3) throw Error('invalid_access_token');
  const header = uniqueJson(utf8(decode(parts[0]))), claims = uniqueJson(utf8(decode(parts[1])));
  if (!header || Array.isArray(header) || header.alg !== 'RS256' || !text(header.kid, 256)
      || Object.keys(header).some(k => !['alg', 'kid', 'typ'].includes(k))
      || (header.typ !== undefined && header.typ !== 'JWT')
      || !claims || typeof claims !== 'object' || Array.isArray(claims)) throw Error('invalid_access_token');
  const signature = decode(parts[2]);
  if (signature.length < 256 || signature.length > 512) throw Error('invalid_access_token');
  return {header, claims, signature, signed: encoder.encode(parts[0] + '.' + parts[1])};
}

export function validateClaims(claims, config, now) {
  const instant = Math.floor(now / 1000);
  if (!Number.isSafeInteger(instant) || claims.iss !== config.issuer || claims.type !== 'app'
      || !(claims.aud === config.audience || (Array.isArray(claims.aud)
        && claims.aud.length === 1 && claims.aud[0] === config.audience))
      || !Number.isSafeInteger(claims.iat) || !Number.isSafeInteger(claims.exp)
      || claims.iat > instant || claims.exp <= instant || claims.exp <= claims.iat
      || (claims.nbf !== undefined && (!Number.isSafeInteger(claims.nbf) || claims.nbf > instant))
      || !text(claims.sub, 512)) throw Error('invalid_access_token');
  return claims;
}

async function publicKeys(raw) {
  const document = uniqueJson(utf8(raw));
  if (!document || !Array.isArray(document.keys) || document.keys.length < 1
      || document.keys.length > ACCESS_LIMITS.jwksKeys) throw Error('invalid_access_keys');
  const keys = new Map();
  for (const jwk of document.keys) {
    if (!jwk || typeof jwk !== 'object' || jwk.kty !== 'RSA' || jwk.alg !== 'RS256'
        || jwk.use !== 'sig' || !text(jwk.kid, 256) || keys.has(jwk.kid)
        || ['d', 'p', 'q', 'dp', 'dq', 'qi', 'oth'].some(k => k in jwk)
        || (jwk.key_ops !== undefined && (!Array.isArray(jwk.key_ops)
          || jwk.key_ops.length !== 1 || jwk.key_ops[0] !== 'verify')))
      throw Error('invalid_access_keys');
    const modulus = decode(jwk.n), exponent = decode(jwk.e);
    if (modulus.length < 256 || modulus.length > 512 || modulus[0] < 128
        || exponent.length !== 3 || exponent[0] !== 1 || exponent[1] !== 0 || exponent[2] !== 1)
      throw Error('invalid_access_keys');
    keys.set(jwk.kid, await crypto.subtle.importKey('jwk',
      {kty: 'RSA', alg: 'RS256', n: jwk.n, e: jwk.e, ext: true, key_ops: ['verify']},
      {name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256'}, false, ['verify']));
  }
  return keys;
}

export function accessVerifier({fetchJwks, now}) {
  // One issuer per live configuration; rotating config cannot grow an unbounded map.
  let state;
  async function keyFor(issuer, kid) {
    if (!state || state.issuer !== issuer) state = {issuer, keys: new Map(), expires: 0, refreshAfter: 0, pending: null};
    const current = state, instant = now();
    if (instant < current.expires && current.keys.has(kid)) return current.keys.get(kid);
    if (!current.pending) {
      if (instant < current.refreshAfter) throw Error('access_keys_unavailable');
      current.refreshAfter = instant + ACCESS_LIMITS.refreshCooldownMs;
      current.pending = deadline(async signal => {
        const response = await fetchJwks(new Request(issuer + '/cdn-cgi/access/certs',
          {method: 'GET', redirect: 'manual', headers: {Accept: 'application/json'}, signal}));
        if (!(response instanceof Response) || response.status !== 200 || response.redirected
            || response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
          void response?.body?.cancel().catch(() => {});
          throw Error('access_keys_unavailable');
        }
        let raw;
        try { declaredLength(response.headers, ACCESS_LIMITS.jwksBytes); raw = await readBounded(response.body, ACCESS_LIMITS.jwksBytes, signal); }
        catch (error) { void response.body?.cancel().catch(() => {}); throw error; }
        const keys = await publicKeys(raw);
        if (signal.aborted) throw Error('access_keys_unavailable');
        current.keys = keys;
        current.expires = now() + ACCESS_LIMITS.cacheMs;
      }, ACCESS_LIMITS.fetchMs).catch(error => {
        current.keys = new Map(); current.expires = 0; throw error;
      }).finally(() => { current.pending = null; });
    }
    await current.pending;
    if (!current.keys.has(kid) || now() >= current.expires) throw Error('access_keys_unavailable');
    return current.keys.get(kid);
  }
  return async (token, config, signal) => {
    const parsed = tokenParts(token);
    validateClaims(parsed.claims, config, now());
    const key = await deadline(() => keyFor(config.issuer, parsed.header.kid), ACCESS_LIMITS.fetchMs, signal);
    const valid = await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, parsed.signature, parsed.signed);
    if (!valid || signal?.aborted) throw Error('invalid_access_token');
    return validateClaims(parsed.claims, config, now());
  };
}
