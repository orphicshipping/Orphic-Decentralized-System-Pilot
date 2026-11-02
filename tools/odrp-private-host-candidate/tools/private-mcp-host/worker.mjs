import { DurableObject } from 'cloudflare:workers';
import { route, json, ROUTES } from './gateway.mjs';

export default { fetch: route };

// This named singleton is the only ingress to the container. A durable pending
// marker fences unknown outcomes across Worker/process crashes. No auto-replay.
export class NCPHost extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.tail = Promise.resolve();
  }
  fetch(request) {
    const response = this.tail.then(() => this.transaction(request));
    this.tail = response.catch(() => {});
    return response;
  }
  async ready() {
    const container = this.ctx.container;
    if (!container.running) container.start({enableInternet: false});
    await container.setInactivityTimeout(120000);
    const port = container.getTcpPort(8080);
    const until = Date.now() + 30000;
    let lastError = 'No ready response';
    while (Date.now() < until) {
      try {
        const health = await port.fetch('http://container/health', {signal: AbortSignal.timeout(1000)});
        if (health.ok) return port;
        lastError = `Health endpoint returned ${health.status}`;
      } catch (error) { lastError = String(error); }
      await scheduler.wait(200);
    }
    throw Error('Container readiness timeout: ' + lastError);
  }
  async transaction(request) {
    const action = ROUTES.get(`${request.method} ${new URL(request.url).pathname}`);
    if (!action) return json({error: {code: 'not_found'}}, 404);
    const pending = await this.ctx.storage.get('pending');
    if (pending) return json({error: {code: 'outcome_unknown', transaction_id: pending,
      message: 'A previous transaction needs retained-state inspection. Automatic replay is disabled.'}}, 409);
    const payload = request.method === 'POST' ? await request.json() : {};
    // Reject unconfirmed calls before starting or writing anything.
    const readNames = new Set(['ncp_workflows_list','ncp_capabilities_list','ncp_capability_describe',
      'ncp_plan_template','ncp_plan_get','ncp_run_get','ncp_artifact_read','ncp_observe']);
    if (action === 'call' && !readNames.has(payload.name) && payload.confirmed !== true)
      return json({error: {code: 'confirmation_required'}}, 409);
    const committed = await this.ctx.storage.get('committed');
    if (committed && committed.generation !== 'odsp-24f6ef-odrp-v3-isolated-candidate')
      return json({error: {code: 'historical_checkpoint_migration_required'}}, 409);
    let port;
    try { port = await this.ready(); }
    catch (error) { return json({error: {code: 'host_not_ready', message: String(error).slice(0,300)}}, 503); }
    const archive = committed ? await this.env.RETAINED.get(committed.key) : null;
    if (committed && !archive) return json({error: {code: 'retained_state_missing'}}, 503);
    const id = crypto.randomUUID();
    await this.ctx.storage.put('pending', id);
    try {
      const state = archive ? await archive.text() : null;
      const response = await port.fetch('http://container/transaction', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action, payload, state})});
      if (!response.ok) throw Error('MCP transaction failed');
      const result = await response.json();
      if (typeof result.state !== 'string' || result.state.length > 24 * 1024 * 1024)
        throw Error('Invalid checkpoint');
      const key = `checkpoints/${id}.tar.gz.base64`;
      await this.env.RETAINED.put(key, result.state, {httpMetadata: {contentType: 'text/plain'},
        customMetadata: {coreRevision: '24f6ef074c76da0ddf2a405001c00020061e98fc+odrp-v3', generation: 'odsp-24f6ef-odrp-v3-isolated-candidate'}});
      await this.ctx.storage.transaction(async txn => {
        await txn.put('committed', {key, at: new Date().toISOString(), transaction_id: id, generation: 'odsp-24f6ef-odrp-v3-isolated-candidate'});
        await txn.delete('pending');
      });
      return json({...result.result, retention: {committed: true, transaction_id: id}}, result.status);
    } catch {
      return json({error: {code: 'outcome_unknown', transaction_id: id,
        message: 'No durable acknowledgment. Inspect retained state; do not automatically retry.'}}, 503);
    }
  }
}

