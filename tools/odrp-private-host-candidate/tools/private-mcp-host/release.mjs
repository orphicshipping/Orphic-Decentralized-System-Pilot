// The launcher's expected release is a trust input, never a health-response default.
const fields = ['schema', 'repository', 'source_commit', 'source_tree', 'wheel_sha256', 'package_digest'];
export function validateRelease(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join('|') !== [...fields].sort().join('|')
      || value.schema !== 'odsp.private-release-identity.v1'
      || typeof value.repository !== 'string'
      || !/^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value.repository)
      || !['source_commit', 'source_tree'].every(key => typeof value[key] === 'string' && /^[0-9a-f]{40}$/.test(value[key]))
      || !['wheel_sha256', 'package_digest'].every(key => typeof value[key] === 'string' && /^[0-9a-f]{64}$/.test(value[key]))) {
    throw new Error('private_release_identity_refused');
  }
  return Object.freeze(Object.fromEntries(fields.map(key => [key, value[key]])));
}
export function expectedRelease(env) {
  const raw = env?.ODSP_RELEASE_IDENTITY;
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > 2048) throw new Error('private_release_identity_required');
  // Require the documented compact JSON representation. This also refuses
  // duplicate keys, trailing content and alternate escaped key spellings.
  let value, parsed;
  try { parsed = JSON.parse(raw); value = validateRelease(parsed); } catch { throw new Error('private_release_identity_refused'); }
  let compact = '', quoted = false, escaped = false;
  for (const character of raw) {
    if (quoted || !/\s/.test(character)) compact += character;
    if (escaped) escaped = false;
    else if (quoted && character === '\\') escaped = true;
    else if (character === '"') quoted = !quoted;
  }
  if (compact !== JSON.stringify(parsed)) throw new Error('private_release_identity_refused');
  return value;
}
export function releaseMatches(actual, expected) {
  try {
    const a = validateRelease(actual), b = validateRelease(expected);
    return fields.every(key => a[key] === b[key]);
  } catch { return false; }
}
