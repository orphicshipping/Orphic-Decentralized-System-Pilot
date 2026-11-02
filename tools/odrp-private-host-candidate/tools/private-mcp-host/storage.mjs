// DO/R2 custody protocol. No retry, reset, garbage collection or remote admin route.
export class Refusal extends Error {
  constructor(code) { super(code); this.code = code; }
}
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HEX = /^[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$/;
const encoder = new TextEncoder();
export const bytes = value => encoder.encode(value);
export async function digest(value) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes(value)))].map(x => x.toString(16).padStart(2, '0')).join('');
}
export function same(a, b) {
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return a === b;
  const ak = Object.keys(a).sort(), bk = Object.keys(b).sort();
  return ak.length === bk.length && ak.every((k, i) => k === bk[i] && same(a[k], b[k]));
}
export function configuration(env) {
  if (typeof env.ODSP_OWNER_ID !== 'string' || typeof env.ODSP_CHECKPOINT_GENERATION !== 'string' ||
      !ID.test(env.ODSP_OWNER_ID) || !ID.test(env.ODSP_CHECKPOINT_GENERATION)) throw new Refusal('storage_configuration_required');
  let p;
  try { p = JSON.parse(env.ODSP_STORAGE_POLICY); } catch { throw new Refusal('storage_configuration_required'); }
  if (typeof env.ODSP_STORAGE_POLICY !== 'string' || env.ODSP_STORAGE_POLICY.length > 4096 ||
      env.ODSP_STORAGE_POLICY.replace(/\s/g, '') !== JSON.stringify(p)) throw new Refusal('invalid_storage_policy');
  const bounds = {queue_limit: [0, 64], transaction_ms: [50, 300000], storage_ms: [10, 60000],
    ready_ms: [50, 30000], health_ms: [10, 5000], max_request_bytes: [128, 262144],
    max_checkpoint_bytes: [128, 24 * 1024 * 1024], max_response_bytes: [1024, 32 * 1024 * 1024],
    max_objects: [1, 1000000], max_total_bytes: [128, Number.MAX_SAFE_INTEGER]};
  if (!p || Object.keys(p).sort().join() !== Object.keys(bounds).sort().join()) throw new Refusal('invalid_storage_policy');
  for (const [key, [min, max]] of Object.entries(bounds))
    if (!Number.isSafeInteger(p[key]) || p[key] < min || p[key] > max) throw new Refusal('invalid_storage_policy');
  if (p.max_total_bytes < p.max_checkpoint_bytes || p.max_response_bytes <= p.max_checkpoint_bytes) throw new Refusal('invalid_storage_policy');
  return Object.freeze({owner_id: env.ODSP_OWNER_ID, generation: env.ODSP_CHECKPOINT_GENERATION, ...p});
}
export async function bounded(operation, deadline, milliseconds, code = 'deadline_exceeded') {
  const remaining = Math.min(milliseconds, deadline - Date.now());
  if (remaining <= 0) throw new Refusal(code);
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(() => operation(controller.signal)),
      new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Refusal(code)); }, remaining); }),
    ]);
  } finally { clearTimeout(timer); }
}
export async function textBounded(stream, maximum, signal) {
  if (!stream?.getReader) throw new Refusal('missing_response_body');
  const reader = stream.getReader();
  const chunks = []; let length = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener('abort', cancel, {once: true});
  try {
    for (;;) {
      if (signal?.aborted) throw new Refusal('deadline_exceeded');
      const {value, done} = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maximum) throw new Refusal('response_budget_exceeded');
      chunks.push(value);
    }
    if (signal?.aborted) throw new Refusal('deadline_exceeded');
    const result = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
    return new TextDecoder('utf-8', {fatal: true}).decode(result);
  } catch (error) { cancel(); throw error; }
  finally { signal?.removeEventListener('abort', cancel); reader.releaseLock(); }
}
export function checkHead(head, config) {
  if (!head || head.schema !== 'ciw.private-storage.v1' || head.owner_id !== config.owner_id || head.generation !== config.generation ||
      !Number.isSafeInteger(head.epoch) || head.epoch < 1 || !Number.isSafeInteger(head.revision) || head.revision < 0 ||
      !Number.isSafeInteger(head.objects) || head.objects < 0 || !Number.isSafeInteger(head.bytes) || head.bytes < 0)
    throw new Refusal('storage_identity_mismatch');
  return head;
}
export function checkPointer(pointer, config) {
  if (!pointer || pointer.schema !== 'ciw.private-checkpoint.v1' || pointer.owner_id !== config.owner_id ||
      pointer.generation !== config.generation || !UUID.test(pointer.transaction_id) || !HEX.test(pointer.sha256) ||
      !Number.isSafeInteger(pointer.bytes) || pointer.bytes < 1 || pointer.bytes > config.max_checkpoint_bytes ||
      pointer.key !== `checkpoints/${config.owner_id}/${pointer.object_id}.tar.gz.base64` || !UUID.test(pointer.object_id))
    throw new Refusal('checkpoint_identity_mismatch');
  return pointer;
}
function checkBinding(binding, config) {
  if (!binding || binding.owner_id !== config.owner_id || binding.generation !== config.generation ||
      !UUID.test(binding.id) || !Number.isSafeInteger(binding.epoch) || binding.epoch < 1 ||
      (binding.base_commit !== null && !UUID.test(binding.base_commit)) || !binding.release || typeof binding.release !== 'object')
    throw new Refusal('transaction_binding_mismatch');
}
export async function snapshot(storage, config) {
  return storage.transaction(async tx => {
    const pending = await tx.get('pending');
    const head = await tx.get('control');
    const committed = await tx.get('committed');
    if (head) checkHead(head, config);
    if (committed) checkPointer(committed, config);
    if (!head && committed) throw new Refusal('historical_checkpoint_migration_required');
    return {pending: pending || null, head: head || null, committed: committed || null};
  });
}
export async function admit(storage, config, binding, identity, expected, deadline, kind = 'operation') {
  checkBinding(binding, config);
  return storage.transaction(async tx => {
    if (Date.now() >= deadline) throw new Refusal('deadline_exceeded');
    if (await tx.get('pending')) throw new Refusal('outcome_unknown');
    let head = await tx.get('control');
    const committed = await tx.get('committed');
    if (head) checkHead(head, config);
    else {
      if (committed || expected.head) throw new Refusal('historical_checkpoint_migration_required');
      head = {schema: 'ciw.private-storage.v1', owner_id: config.owner_id, generation: config.generation,
        epoch: 1, revision: 0, bytes: 0, objects: 0};
    }
    if (committed) checkPointer(committed, config);
    if (head.epoch !== binding.epoch || head.revision !== (expected.head?.revision || 0) ||
        (committed?.transaction_id || null) !== binding.base_commit || !same(committed || null, expected.committed)) throw new Refusal('stale_writer');
    if (head.objects >= config.max_objects || !Number.isSafeInteger(head.bytes + config.max_checkpoint_bytes) ||
        head.bytes + config.max_checkpoint_bytes > config.max_total_bytes) throw new Refusal('retention_capacity_exceeded');
    const pending = {schema: 'ciw.private-pending.v1', transaction: binding, identity, kind,
      phase: 'admitted', reserved_bytes: config.max_checkpoint_bytes, deadline_at: deadline};
    if (Date.now() >= deadline) throw new Refusal('deadline_exceeded');
    // Reservation is retained when publication is uncertain. No automatic GC.
    head = {...head, objects: head.objects + 1, bytes: head.bytes + config.max_checkpoint_bytes};
    await tx.put('control', head);
    await tx.put('pending', pending);
    await tx.put(`receipt:${binding.id}`, pending);
    return pending;
  });
}
export async function publish(storage, config, binding, pointer, deadline) {
  checkBinding(binding, config);
  checkPointer(pointer, config);
  return storage.transaction(async tx => {
    const head = checkHead(await tx.get('control'), config);
    const pending = await tx.get('pending');
    if (Date.now() >= deadline) throw new Refusal('deadline_exceeded');
    if (head.epoch !== binding.epoch || !same(pending?.transaction, binding)) throw new Refusal('stale_writer');
    if (!same(pointer.release, binding.release) || (pending.kind === 'owned_restore' ? pointer.restored_by !== binding.id :
        pointer.transaction_id !== binding.id || pointer.object_id !== binding.id || 'restored_by' in pointer))
      throw new Refusal('transaction_binding_mismatch');
    await tx.put('committed', pointer);
    await tx.put('control', {...head, revision: head.revision + 1,
      bytes: head.bytes - pending.reserved_bytes + pointer.bytes});
    await tx.put(`receipt:${binding.id}`, {...pending, phase: 'committed', checkpoint: pointer});
    await tx.delete('pending');
  });
}
export async function holdUnconfirmed(storage, config, binding) {
  checkBinding(binding, config);
  return storage.transaction(async tx => {
    const head = await tx.get('control');
    if (!head || checkHead(head, config).epoch !== binding.epoch) return {outcome: 'unconfirmed', hold: false};
    const pending = await tx.get('pending'), receipt = await tx.get(`receipt:${binding.id}`);
    if (pending && !same(pending.transaction, binding)) return {outcome: 'unconfirmed', hold: false};
    if (!receipt || !same(receipt.transaction, binding)) return {outcome: 'unconfirmed', hold: false};
    const committed = await tx.get('committed');
    const completed = receipt.phase === 'committed' &&
      (committed?.transaction_id === binding.id || committed?.restored_by === binding.id);
    // Do not place an old uncertainty marker over a newer terminal outcome.
    if (!pending && !completed) return {outcome: 'unconfirmed', hold: false};
    const outcome = completed ? 'committed_response_unconfirmed' : 'unconfirmed';
    await tx.put('pending', {...(pending || receipt), phase: outcome});
    await tx.put(`receipt:${binding.id}`, {...receipt, response: 'unconfirmed'});
    return {outcome, hold: true};
  });
}
export async function readCheckpoint(bucket, pointer, config, deadline) {
  checkPointer(pointer, config);
  const object = await bounded(() => bucket.get(pointer.key), deadline, config.storage_ms);
  if (!object) throw new Refusal('retained_state_missing');
  const value = await bounded(signal => textBounded(object.body, config.max_checkpoint_bytes, signal), deadline, config.storage_ms);
  if (bytes(value).length !== pointer.bytes || await digest(value) !== pointer.sha256) throw new Refusal('checkpoint_digest_mismatch');
  return value;
}

// These helpers are for an explicitly invoked, owned, offline maintenance tool.
// They are intentionally not exposed as HTTP routes or automatic recovery.
export async function exportBackup(storage, bucket, config) {
  const deadline = Date.now() + config.transaction_ms;
  const before = await bounded(() => snapshot(storage, config), deadline, config.storage_ms);
  if (before.pending) throw new Refusal('outcome_unknown');
  if (!before.head || !before.committed) throw new Refusal('no_owned_checkpoint');
  const state = await readCheckpoint(bucket, before.committed, config, deadline);
  const after = await bounded(() => snapshot(storage, config), deadline, config.storage_ms);
  if (!same(before, after)) throw new Refusal('backup_changed_during_read');
  return {schema: 'ciw.private-backup.v1', owner_id: config.owner_id, generation: config.generation,
    head: before.head, checkpoint: before.committed, state};
}
export async function restoreBackup(storage, bucket, config, backup, expectedEpoch) {
  if (backup?.schema !== 'ciw.private-backup.v1' || backup.owner_id !== config.owner_id || backup.generation !== config.generation)
    throw new Refusal('backup_identity_mismatch');
  checkHead(backup.head, config); checkPointer(backup.checkpoint, config);
  if (typeof backup.state !== 'string' || bytes(backup.state).length !== backup.checkpoint.bytes || await digest(backup.state) !== backup.checkpoint.sha256)
    throw new Refusal('backup_digest_mismatch');
  const deadline = Date.now() + config.transaction_ms, id = crypto.randomUUID();
  let binding;
  await bounded(() => storage.transaction(async tx => {
    if (await tx.get('pending')) throw new Refusal('outcome_unknown');
    const head = checkHead(await tx.get('control'), config);
    if (head.epoch !== expectedEpoch || !Number.isSafeInteger(expectedEpoch + 1)) throw new Refusal('stale_writer');
    if (head.objects >= config.max_objects || !Number.isSafeInteger(head.bytes + config.max_checkpoint_bytes) ||
        head.bytes + config.max_checkpoint_bytes > config.max_total_bytes) throw new Refusal('retention_capacity_exceeded');
    const committed = checkPointer(await tx.get('committed'), config);
    // Restoring an older snapshot could forget later attempt identities. This
    // bounded workflow only rehomes the currently committed owned checkpoint.
    if (head.revision !== backup.head.revision || !same(committed, backup.checkpoint)) throw new Refusal('stale_backup_refused');
    binding = {id, owner_id: config.owner_id, generation: config.generation, epoch: head.epoch + 1,
      base_commit: committed.transaction_id, release: backup.checkpoint.release};
    const pending = {schema: 'ciw.private-pending.v1', transaction: binding, identity: null, kind: 'owned_restore',
      phase: 'admitted', reserved_bytes: config.max_checkpoint_bytes, deadline_at: deadline};
    await tx.put('control', {...head, epoch: binding.epoch, objects: head.objects + 1, bytes: head.bytes + config.max_checkpoint_bytes});
    await tx.put('pending', pending); await tx.put(`receipt:${id}`, pending);
  }), deadline, config.storage_ms);
  // A crash or any error after reservation leaves the evidence/fence intact.
  const pointer = {...backup.checkpoint, object_id: id, key: `checkpoints/${config.owner_id}/${id}.tar.gz.base64`, restored_by: id};
  try {
    await bounded(() => bucket.put(pointer.key, backup.state), deadline, config.storage_ms);
    await bounded(() => publish(storage, config, binding, pointer, deadline), deadline, config.storage_ms);
  } catch (error) {
    try { await bounded(() => holdUnconfirmed(storage, config, binding), Date.now() + config.storage_ms, config.storage_ms); }
    catch {} // The original error propagates; durable effect is never called rollback.
    throw error;
  }
  return {schema: 'ciw.private-restore.v1', owner_id: config.owner_id, generation: config.generation,
    epoch: binding.epoch, checkpoint: pointer, authorizes_execution: false};
}
