import { DurableObject } from 'cloudflare:workers';
import { route, json, ROUTES, principalContext, ODRP_READ_NAMES } from './gateway.mjs';
import { expectedRelease, releaseMatches } from './release.mjs';
import { Refusal, configuration, bounded, bytes, digest, same, textBounded, snapshot,
  admit, publish, readCheckpoint, holdUnconfirmed } from './storage.mjs';

export default { fetch: route };
const READ_NAMES = new Set(['ncp_workflows_list','ncp_capabilities_list','ncp_capability_describe',
  'ncp_plan_template','ncp_plan_get','ncp_run_get','ncp_artifact_read','ncp_observe', ...ODRP_READ_NAMES]);
const refusal = (code, status = 503, effect_state = 'not_started', extra = {}) =>
  json({error: {code, effect_state, ...extra}}, status);

// This singleton serializes admitted work. Durable state, not this Promise tail,
// fences process loss. An error never causes operation re-execution or rollback.
export class NCPHost extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env); this.tail = Promise.resolve(); this.queued = 0; this.writerEpoch = null; this.readinessActive = false;
  }
  async fetch(request) {
    let config;
    try { config = configuration(this.env); expectedRelease(this.env); await principalContext(request, this.env); }
    catch { return refusal('host_configuration_or_identity_refused', 403); }
    const action = ROUTES.get(`${request.method} ${new URL(request.url).pathname}`);
    if (!action) return refusal('not_found', 404);
    if (action === 'readiness') {
      if (this.readinessActive) return refusal('readiness_busy', 429);
      this.readinessActive = true;
      try { return await this.readiness(request, config); }
      finally { this.readinessActive = false; }
    }
    if (this.queued >= config.queue_limit + 1) return refusal('queue_full', 429);
    this.queued++;
    const enqueued = Date.now(), deadline = enqueued + config.transaction_ms;
    let started = false;
    const response = this.tail.then(() => { started = true; return this.transaction(request, config, deadline); });
    this.tail = response.catch(() => {});
    // Queued callers also have bounded waits. Expired entries never execute.
    try { return await bounded(() => response, deadline + config.storage_ms, config.transaction_ms + config.storage_ms, 'queue_deadline_exceeded'); }
    catch { return refusal('queue_or_transaction_deadline_exceeded', 503, started ? 'unconfirmed' : 'not_started'); }
    finally { void response.finally(() => { this.queued--; }).catch(() => {}); }
  }
  async health(config, release, deadline, start) {
    const container = this.ctx.container;
    if (!container.running) {
      if (!start) throw new Refusal('host_not_running');
      await bounded(() => container.start({enableInternet: false}), deadline, config.health_ms);
    }
    if (start) await bounded(() => container.setInactivityTimeout(120000), deadline, config.health_ms);
    const port = container.getTcpPort(8080);
    const until = Math.min(deadline, Date.now() + config.ready_ms);
    for (;;) {
      try {
        const health = await bounded(async signal => {
          const response = await port.fetch('http://container/health', {signal});
          if (!response.ok) throw new Refusal('host_not_ready');
          return JSON.parse(await textBounded(response.body, 16384, signal));
        }, until, config.health_ms);
        if (health.owner_id !== config.owner_id || health.checkpoint_generation !== config.generation ||
            !releaseMatches(health.release, release)) throw new Refusal('host_identity_mismatch');
        if (health.ready !== true) throw new Refusal('host_not_ready');
        return {port, health};
      } catch (error) {
        if (error.code === 'host_identity_mismatch' || !start || Date.now() + 20 >= until) throw error;
        await new Promise(resolve => setTimeout(resolve, 20));
      }
    }
  }
  async readiness(request, config) {
    const deadline = Date.now() + config.ready_ms;
    try {
      await principalContext(request, this.env);
      const release = expectedRelease(this.env);
      const saved = await bounded(() => snapshot(this.ctx.storage, config), deadline, config.storage_ms);
      const objects = saved.head?.objects || 0, accounted = saved.head?.bytes || 0;
      const reserved = saved.pending && ['admitted', 'unconfirmed'].includes(saved.pending.phase) ? saved.pending.reserved_bytes : 0;
      const nextBytes = accounted + config.max_checkpoint_bytes;
      const capacityAvailable = objects < config.max_objects && Number.isSafeInteger(nextBytes) && nextBytes <= config.max_total_bytes;
      const storage_capacity = {status: capacityAvailable ? 'available' : 'exhausted',
        scope: 'encoded_checkpoint_payloads_and_reservations', objects_accounted: objects,
        objects_limit: config.max_objects, bytes_accounted: accounted, bytes_limit: config.max_total_bytes,
        reserved_bytes: Number.isSafeInteger(reserved) && reserved >= 0 ? reserved : null,
        next_checkpoint_reservation_bytes: config.max_checkpoint_bytes};
      const stale = this.writerEpoch !== null && this.writerEpoch !== (saved.head?.epoch || 1);
      let ready = false, reason = null;
      if (saved.pending) reason = 'outcome_unknown';
      else if (stale) reason = 'stale_writer';
      else if (!capacityAvailable) reason = 'retention_capacity_exceeded';
      else {
        try { await this.health(config, release, deadline, false); ready = true; }
        catch (error) { reason = error.code || 'host_not_ready'; }
      }
      return json({schema: 'ciw.private-readiness.v1', ready,
        fenced: Boolean(saved.pending), reason, storage_capacity,
        owner_id: config.owner_id, generation: config.generation, epoch: saved.head?.epoch || null,
        release, committed_transaction_id: saved.committed?.transaction_id || null,
        pending_transaction_id: saved.pending?.transaction?.id || (typeof saved.pending === 'string' ? saved.pending : null),
        authorizes_execution: false, checkpoint_written: false}, 200);
    } catch (error) { return refusal(error.code || 'readiness_unavailable'); }
  }
  async transaction(request, config = configuration(this.env), deadline = Date.now() + config.transaction_ms) {
    let binding = null, admitted = false, phase = 'before_admission';
    try {
      if (Date.now() >= deadline) throw new Refusal('queue_deadline_exceeded');
      const identity = await principalContext(request, this.env);
      const release = expectedRelease(this.env);
      const action = ROUTES.get(`${request.method} ${new URL(request.url).pathname}`);
      if (!action || action === 'readiness') throw new Refusal('invalid_transaction_action');
      const payload = request.method === 'POST' ? JSON.parse(await bounded(signal => textBounded(request.body, config.max_request_bytes, signal), deadline, config.storage_ms)) : {};
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Refusal('invalid_request');
      if (action === 'call' && !READ_NAMES.has(payload.name) && payload.confirmed !== true) return refusal('confirmation_required', 409);
      const before = await bounded(() => snapshot(this.ctx.storage, config), deadline, config.storage_ms);
      if (before.pending) return refusal('outcome_unknown', 409, 'unconfirmed', {transaction_id: before.pending.transaction?.id || before.pending});
      if (this.writerEpoch === null) this.writerEpoch = before.head?.epoch || 1;
      if (this.writerEpoch !== (before.head?.epoch || 1)) throw new Refusal('stale_writer');
      const {port} = await this.health(config, release, deadline, true);
      const state = before.committed ? await readCheckpoint(this.env.RETAINED, before.committed, config, deadline) : null;
      binding = {id: crypto.randomUUID(), owner_id: config.owner_id, generation: config.generation,
        epoch: this.writerEpoch, base_commit: before.committed?.transaction_id || null, release};
      const request_sha256 = await digest(JSON.stringify({action, payload, identity}));
      // Admission can itself commit and then fail to return. From this point any
      // ambiguous I/O must be inspected, never interpreted as rolled back.
      admitted = true;
      phase = 'admission';
      await bounded(() => admit(this.ctx.storage, config, binding, {...identity, request_sha256}, before, deadline), deadline, config.storage_ms);
      phase = 'execution';
      const result = await bounded(async signal => {
        const response = await port.fetch('http://container/transaction', {method: 'POST', signal,
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action, payload, state, identity, transaction: binding})});
        if (!response.ok) throw new Refusal('container_transaction_unconfirmed');
        return JSON.parse(await textBounded(response.body, config.max_response_bytes, signal));
      }, deadline, config.transaction_ms);
      if (!same(result.transaction, binding) || !same(result.identity, identity) || typeof result.state !== 'string' || bytes(result.state).length > config.max_checkpoint_bytes ||
          !Number.isInteger(result.status) || result.status < 200 || result.status > 599 || !result.result ||
          typeof result.result !== 'object' || Array.isArray(result.result))
        throw new Refusal('invalid_transaction_response');
      const hash = await digest(result.state);
      if (result.checkpoint_sha256 !== hash) throw new Refusal('checkpoint_digest_mismatch');
      const pointer = {schema: 'ciw.private-checkpoint.v1', owner_id: config.owner_id, generation: config.generation,
        transaction_id: binding.id, object_id: binding.id, key: `checkpoints/${config.owner_id}/${binding.id}.tar.gz.base64`,
        sha256: hash, bytes: bytes(result.state).length, release,
        request_sha256, response_sha256: await digest(JSON.stringify(result.result))};
      const acknowledgment = {...result.result, retention: {committed: true, transaction_id: binding.id, epoch: binding.epoch,
        checkpoint_sha256: hash, request_sha256, response_sha256: pointer.response_sha256}};
      if (bytes(JSON.stringify(acknowledgment)).length > config.max_response_bytes) throw new Refusal('response_budget_exceeded');
      await bounded(() => this.env.RETAINED.put(pointer.key, result.state,
        {customMetadata: {owner: config.owner_id, generation: config.generation, sha256: hash, transaction: binding.id}}), deadline, config.storage_ms);
      await bounded(() => publish(this.ctx.storage, config, binding, pointer, deadline), deadline, config.storage_ms);
      return json(acknowledgment, result.status);
    } catch (error) {
      if (!admitted) return refusal(error.code || 'request_refused');
      if (phase === 'admission' && ['stale_writer','retention_capacity_exceeded','outcome_unknown','historical_checkpoint_migration_required'].includes(error.code))
        return refusal(error.code, 409, 'not_started');
      // A single CAS diagnostic is permitted; it cannot overwrite another epoch
      // or erase a committed pointer. Failure to confirm the hold is explicit.
      let outcome = 'unconfirmed', hold = false;
      try {
        const observed = await bounded(() => holdUnconfirmed(this.ctx.storage, config, binding),
          Date.now() + config.storage_ms, config.storage_ms);
        outcome = observed.outcome; hold = observed.hold;
      } catch {}
      return refusal(outcome === 'committed_response_unconfirmed' ? outcome : 'outcome_unknown', 503, outcome,
        {transaction_id: binding?.id || null, uncertainty_hold_confirmed: hold});
    }
  }
}
