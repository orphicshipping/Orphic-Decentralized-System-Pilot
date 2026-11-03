'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const MAX_RESPONSE = 32 * 1024 * 1024;
  const MAX_REQUEST = 262144;
  const HOLD_KEY = 'odsp-terminal-uncertainty-v1';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const HASH = /^[0-9a-f]{64}$/;
  const SHA = /^[0-9a-f]{40}$/;
  const encoder = new TextEncoder();
  const state = {busy:false, sessionOpen:false, phase:'disconnected', source:null, readiness:null,
    tools:[], selected:null, generation:0, receipt:null, planArguments:null, hold:null, storageReady:false};
  const pretty = value => JSON.stringify(value, null, 2);
  const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const utc = value => typeof value === 'string' && Number.isFinite(Date.parse(value))
    ? new Date(value).toISOString().replace('T',' ').replace('Z',' UTC') : 'Not reported';
  const errorFields = envelope => record(envelope?.error) ? envelope.error
    : {code: typeof envelope?.error === 'string' ? envelope.error : null, effect_state:envelope?.effect_state};
  const access = tool => tool?.annotations?.readOnlyHint === true ? 'read'
    : tool?.annotations?.readOnlyHint === false ? 'confirm' : 'unsupported';
  const currentTool = () => state.selected && state.tools.find(t => t.name === state.selected.name) === state.selected;
  const canOpen = () => state.readiness && state.readiness.fenced === false && !state.readiness.pending_transaction_id
    && (state.readiness.ready === true || (state.readiness.reason === 'host_not_running' && state.readiness.storage_capacity.status === 'available'));
  const usable = () => state.sessionOpen && canOpen() && !state.hold && state.storageReady;

  function notice(message, kind='success') {
    $('notice').textContent = message;
    $('notice').dataset.state = kind;
    $('notice').classList.toggle('error', ['denied','failed','unknown','stale'].includes(kind));
  }
  function phase(value, message) {
    state.phase = value;
    $('session-state').textContent = value.toUpperCase();
    $('session-state').dataset.state = value;
    $('connection').textContent = message;
    $('status-dot').className = 'dot ' + (value === 'success' ? 'live' : value === 'disconnected' ? '' : 'error');
  }
  function holdView() {
    $('hold-warning').hidden = !state.hold;
    if (state.hold) {
      $('hold-metadata').textContent = pretty(state.hold);
      phase('unknown', 'Earlier outcome remains unknown');
    }
  }
  function safeHold(value) {
    return record(value) && Object.keys(value).every(key=>['schema','effect_state','transaction_id','recorded_at','reason','request_id'].includes(key))
      && value.schema === 'odsp.ui-uncertainty.v1'
      && ['unconfirmed','committed_response_unconfirmed'].includes(value.effect_state)
      && (value.transaction_id === null || (typeof value.transaction_id === 'string' && UUID.test(value.transaction_id)))
      && typeof value.recorded_at === 'string' && value.recorded_at.length <= 32 && Number.isFinite(Date.parse(value.recorded_at))
      && typeof value.reason === 'string' && /^[a-z][a-z0-9_]{0,79}$/.test(value.reason)
      && (value.request_id === undefined || (typeof value.request_id === 'string' && UUID.test(value.request_id)));
  }
  function latch(reason, envelope=null, ownMarker=null) {
    state.sessionOpen = false;
    $('confirm-write').checked = false;
    if (!state.hold) {
      let existing = null;
      try { existing = sessionStorage.getItem(HOLD_KEY); } catch { state.storageReady = false; }
      if (existing && existing !== ownMarker) {
        try { const previous = existing.length <= 2048 ? JSON.parse(existing) : null; if (safeHold(previous)) state.hold = previous; } catch {}
      }
      const error = errorFields(envelope);
      const transaction = error.transaction_id ?? envelope?.retention?.transaction_id ?? envelope?.pending_transaction_id;
      state.hold ||= {schema:'odsp.ui-uncertainty.v1',
        effect_state:error.effect_state === 'committed_response_unconfirmed' || validRetention(envelope?.retention)
          ? 'committed_response_unconfirmed' : 'unconfirmed',
        transaction_id:typeof transaction === 'string' && UUID.test(transaction) ? transaction : null,
        recorded_at:new Date().toISOString(),
        reason:typeof reason === 'string' && /^[a-z][a-z0-9_]{0,79}$/.test(reason) ? reason : 'response_unknown'};
      if (ownMarker && (!existing || existing === ownMarker)) state.hold.request_id = JSON.parse(ownMarker).request_id;
      try { if (!existing || existing === ownMarker) sessionStorage.setItem(HOLD_KEY, JSON.stringify(state.hold)); }
      catch { state.storageReady = false; }
    }
    holdView();
    notice('Outcome unknown. Do not resubmit. Readiness cannot clear this hold.' +
      (state.storageReady ? '' : ' Hold persistence is unavailable; keep this tab open.'), 'unknown');
    controls();
  }
  function beginIntent() {
    // A navigation can destroy the pending Promise without running its catch.
    // Persist a bounded, non-secret intent before any checkpoint dispatch.
    let marker;
    try {
      if (sessionStorage.getItem(HOLD_KEY) !== null) {
        latch('existing_uncertainty_hold');
        throw new Refusal('An earlier request remains uncertain. Nothing was sent.','unknown');
      }
      marker = JSON.stringify({schema:'odsp.ui-uncertainty.v1',effect_state:'unconfirmed',
        transaction_id:null,recorded_at:new Date().toISOString(),reason:'request_in_flight',request_id:crypto.randomUUID()});
      sessionStorage.setItem(HOLD_KEY,marker);
      if (sessionStorage.getItem(HOLD_KEY) !== marker) throw new Error('Intent was not retained.');
    } catch (error) {
      if (error instanceof Refusal) throw error;
      state.storageReady=false;state.sessionOpen=false;
      phase('failed','Cannot retain a request intent; no checkpoint request was sent.');
      throw new Refusal('Request intent could not be retained. Nothing was sent.');
    }
    return marker;
  }
  function settleIntent(marker, envelope) {
    try {
      if (!state.hold && sessionStorage.getItem(HOLD_KEY) === marker) {
        sessionStorage.removeItem(HOLD_KEY);
        if (sessionStorage.getItem(HOLD_KEY) === null) return;
      }
    } catch { state.storageReady=false; }
    latch('uncertainty_marker_changed',envelope,marker);
    throw new Refusal('This response cannot clear a different or unconfirmed request marker.','unknown');
  }
  function restoreHold() {
    try {
      const probe = HOLD_KEY + '.storage-check';
      sessionStorage.setItem(probe,'1'); sessionStorage.removeItem(probe);
      state.storageReady = true;
      const raw = sessionStorage.getItem(HOLD_KEY);
      if (raw !== null) {
        let value;
        try { value = raw.length <= 2048 ? JSON.parse(raw) : null; } catch { value = null; }
        if (safeHold(value)) state.hold = value;
        else latch('stored_uncertainty_invalid');
      }
    } catch { latch('uncertainty_persistence_unavailable'); }
    holdView();
  }
  function controls() {
    const active = usable() && !state.busy;
    for (const id of ['connect','refresh-readiness','disconnect']) $(id).disabled = state.busy;
    $('open-session').disabled = state.busy || Boolean(state.hold) || !state.storageReady || !state.source || !canOpen();
    const mode = access(state.selected);
    $('call').disabled = !active || !currentTool() || mode === 'unsupported' || (mode === 'confirm' && !$('confirm-write').checked);
    $('arguments').disabled = !active || !currentTool();
    $('confirm-write').disabled = !active || !currentTool() || mode !== 'confirm';
    for (const id of ['diagnose','refresh-runs','read-resource','refresh-events']) $(id).disabled = !active;
    $('load-capabilities').disabled = !active || !state.tools.some(t => t.name === 'ncp_capabilities_list' && access(t) === 'read');
    $('prepare-plan').disabled = !active || !state.tools.some(t => t.name === 'ncp_plan_create' && access(t) !== 'unsupported');
    for (const button of document.querySelectorAll('.tool-item')) button.disabled = !active;
    $('restart').disabled = true;
  }
  function invalidate(message, next='stale') {
    state.sessionOpen = false;
    state.generation++;
    state.selected = null;
    state.tools = [];
    $('confirm-write').checked = false;
    $('selected-name').textContent = 'Select a tool';
    $('selected-access').textContent = 'NO CURRENT SELECTION';
    $('selected-description').textContent = 'No current authenticated tool selection.';
    $('schema').textContent = '{}';
    renderTools();
    phase(next, message);
    holdView(); controls();
  }
  async function operate(task) {
    if (state.busy) return;
    state.busy = true; controls();
    try { await task(); }
    catch (error) {
      if (!state.hold) notice(error.message || 'Request failed.', error.kind || 'failed');
    } finally { state.busy = false; holdView(); controls(); }
  }
  class Refusal extends Error {
    constructor(message, kind='failed') { super(message); this.kind = kind; }
  }
  function validRetention(value) {
    return record(value) && value.committed === true && typeof value.transaction_id === 'string' && UUID.test(value.transaction_id)
      && Number.isSafeInteger(value.epoch) && value.epoch >= 1
      && ['checkpoint_sha256','request_sha256','response_sha256'].every(key => typeof value[key] === 'string' && HASH.test(value[key]));
  }
  function clearReceipt() {
    state.receipt = null; state.planArguments = null;
    $('download').disabled = true; $('prepare-plan').hidden = true;
    $('receipt-state').textContent = 'LOADING · Awaiting a complete response.'; $('receipt-state').dataset.state = 'loading';
    $('receipt-summary').textContent = '';
    $('receipt-effects').textContent = 'Not reported'; $('receipt-transaction').textContent = 'Not reported';
    $('receipt-at').textContent = 'Not received'; $('receipt-at').removeAttribute('datetime');
    $('result').textContent = 'Waiting for this explicit request. No earlier result is substituted.';
    $('scientific-result').textContent = 'Not reported.';
  }
  function showReceipt(path, bytes, text, envelope, status) {
    const error = errorFields(envelope);
    const committed = validRetention(envelope?.retention);
    const effect = error.effect_state || (committed ? 'committed' : 'unconfirmed');
    const at = new Date().toISOString();
    state.receipt = {path,bytes,text,envelope,status,at};
    state.planArguments = typeof envelope?.plan_arguments_json === 'string' ? envelope.plan_arguments_json : null;
    $('result').textContent = text;
    $('download').disabled = false;
    $('scientific-result').textContent = typeof envelope?.raw_json === 'string' ? envelope.raw_json : 'No separate raw_json supplied; inspect the complete response above.';
    $('prepare-plan').hidden = !state.planArguments;
    $('receipt-effects').textContent = effect;
    $('receipt-transaction').textContent = error.transaction_id || envelope?.retention?.transaction_id || 'Not reported';
    $('receipt-at').dateTime = at; $('receipt-at').textContent = utc(at);
    $('receipt-summary').textContent = `${path} · HTTP ${status} · ${bytes.length} response bytes · Observation time is local to this page.`;
    const refused = Boolean(envelope?.error || envelope?.data?.isError);
    const kind = committed ? (refused ? 'partial' : 'success') : 'failed';
    $('receipt-state').dataset.state = kind;
    $('receipt-state').textContent = `${kind.toUpperCase()} · ${committed ? (refused ? 'Checkpoint committed; operation refused. Inspect the complete response.' : 'Durable acknowledgment received; scientific qualification is separate.') : 'No valid durable acknowledgment.'}`;
  }
  async function readBody(response, maximum) {
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Response body unavailable.');
    const chunks = []; let total = 0;
    try {
      for (;;) {
        const {done,value} = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > maximum) { await reader.cancel(); throw new Error('Response exceeds the declared byte limit; no truncated receipt is accepted.'); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(total); let offset = 0;
    for (const part of chunks) {bytes.set(part,offset);offset += part.byteLength;}
    try { return {bytes,text:new TextDecoder('utf-8',{fatal:true}).decode(bytes),utf8Valid:true}; }
    catch { return {bytes,text:'Invalid UTF-8 response. Download preserves the complete received bytes.',utf8Valid:false}; }
  }
  async function request(path, {body,checkpoint=true,validate=()=>true}={}) {
    if (checkpoint && state.hold) throw new Refusal('Unknown outcome is still held. Only readiness inspection is available.','unknown');
    const serialized = body === undefined ? undefined : JSON.stringify(body);
    if (serialized !== undefined && encoder.encode(serialized).length > MAX_REQUEST)
      throw new Refusal('Request exceeds the 262,144-byte envelope limit. Nothing was sent.');
    const controller = new AbortController();
    const timeout = path === '/source.json' ? 10000 : checkpoint ? 375000 : 45000;
    const timer = setTimeout(()=>controller.abort(),timeout);
    let response, raw, envelope, marker = null, dispatched = false;
    if (checkpoint) clearReceipt();
    try {
      if (checkpoint) marker = beginIntent();
      dispatched = true;
      response = await fetch(path,{method:body === undefined?'GET':'POST',
        headers:{...(path.startsWith('/api/')?{'X-ODSP-Panel-Request':'1'}:{}),...(body === undefined?{}:{'Content-Type':'application/json'})},body:serialized,
        credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      raw = await readBody(response,path === '/source.json'?65536:MAX_RESPONSE);
      if (!raw.utf8Valid) { if (checkpoint) showReceipt(path,raw.bytes,raw.text,null,response.status); throw new Error('Response is not valid UTF-8.'); }
      try { envelope = JSON.parse(raw.text); }
      catch { if (checkpoint) showReceipt(path,raw.bytes,raw.text,null,response.status); throw new Error('The complete response is not valid JSON.'); }
      if (checkpoint) showReceipt(path,raw.bytes,raw.text,envelope,response.status);
      if (!record(envelope)) throw new Error('The response envelope is not an object.');
      const error = errorFields(envelope);
      const knownAuthRefusal = (response.status === 401 && error.code === 'unauthorized') || (response.status === 403 && error.code === 'host_or_origin_denied');
      const uncertain = ['outcome_unknown','committed_response_unconfirmed'].includes(error.code)
        || ['unconfirmed','committed_response_unconfirmed'].includes(error.effect_state);
      if (uncertain) {
        latch(error.code || 'response_unknown',envelope,marker);
        throw new Refusal('Outcome unknown. Inspect readiness and retained state; do not resubmit.','unknown');
      }
      if (!response.ok) {
        const notStarted = error.effect_state === 'not_started' || knownAuthRefusal;
        if (checkpoint && !notStarted && !validRetention(envelope.retention)) {
          latch('response_unknown',envelope,marker);
          throw new Refusal('No trustworthy effect disposition was received.','unknown');
        }
        if (checkpoint) settleIntent(marker,envelope);
        if (checkpoint && notStarted) {
          $('receipt-effects').textContent='not_started';
          $('receipt-state').dataset.state='denied';
          $('receipt-state').textContent='DENIED · Known pre-dispatch refusal. No durable acknowledgment is claimed.';
        }
        if (response.status === 401 || response.status === 403) invalidate('Session denied. Reconnect after server authorization is restored.','denied');
        else if (checkpoint && !validRetention(envelope.retention)) invalidate('Request refused before dispatch. Recheck readiness.','failed');
        const message = typeof error.code === 'string' ? error.code : `HTTP ${response.status}`;
        throw new Refusal(`${message} · ${notStarted?'not started':validRetention(envelope.retention)?'checkpoint committed; inspect refusal':'request failed'}`,
          response.status === 401 || response.status === 403 ? 'denied':'failed');
      }
      if (checkpoint && (!Object.hasOwn(envelope,'data') || !validRetention(envelope.retention)))
        throw new Error('Successful HTTP status lacks a valid complete durable receipt.');
      const data = checkpoint ? envelope.data : envelope;
      if (!validate(data)) throw new Error('Response does not match this interface contract.');
      if (checkpoint) settleIntent(marker,envelope);
      if (checkpoint) $('last-seen').textContent = 'Last complete response '+utc(new Date().toISOString());
      return {data,envelope,text:raw.text,bytes:raw.bytes};
    } catch (error) {
      if (error instanceof Refusal) throw error;
      if (checkpoint && dispatched) {
        latch('transport_or_response_unknown',envelope,marker);
        $('receipt-state').dataset.state = 'unknown';
        $('receipt-state').textContent = 'UNKNOWN · A complete usable acknowledgment was not received.';
        throw new Refusal('Connection, response or receipt failed after dispatch. Do not resubmit.','unknown');
      }
      throw new Refusal(error.name === 'AbortError' ? 'Read-only request timed out; no checkpoint request was sent.' : error.message);
    } finally {clearTimeout(timer);}
  }
  const validSource = s => s.schema === 'odsp.private-panel-source.v1' && s.repository === 'orphicsystems/Orphic-Decentralized-System-Protocol'
    && typeof s.source_commit === 'string' && SHA.test(s.source_commit) && typeof s.source_tree === 'string' && SHA.test(s.source_tree)
    && record(s.assets) && Object.keys(s.assets).length > 0 && Object.entries(s.assets).every(([path,hash])=>path.startsWith('/')&&typeof hash==='string'&&HASH.test(hash));
  const validReadiness = s => s.schema === 'ciw.private-readiness.v1' && typeof s.ready === 'boolean' && typeof s.fenced === 'boolean'
    && s.authorizes_execution === false && s.checkpoint_written === false && typeof s.owner_id === 'string'
    && (s.epoch === null || (Number.isSafeInteger(s.epoch)&&s.epoch>=1)) && record(s.release) && record(s.storage_capacity);
  const validStatus = s => record(s) && typeof s.connection === 'string';
  const validTools = list => Array.isArray(list) && list.length <= 1024 && new Set(list.map(t=>t?.name)).size === list.length
    && list.every(t=>record(t)&&typeof t.name==='string'&&t.name.length>0&&t.name.length<=200&&record(t.inputSchema));
  const validMCP = result => record(result) && typeof result.isError === 'boolean';

  async function readiness() {
    $('readiness-state').textContent = 'LOADING · Checking readiness without a checkpoint.';
    $('readiness-state').dataset.state = 'loading';
    try {
      const response = await request('/api/readiness',{checkpoint:false,validate:validReadiness});
      state.readiness = response.data;
      const s = response.data;
      $('readiness-result').textContent = response.text;
      $('owner-id').textContent = s.owner_id;
      $('writer-epoch').textContent = s.epoch === null ? 'Not reported' : String(s.epoch);
      $('pending-transaction').textContent = s.pending_transaction_id || 'None reported';
      $('committed-transaction').textContent = s.committed_transaction_id || 'None reported';
      $('capacity-state').textContent = s.storage_capacity.status || 'Not reported';
      $('release-identity').textContent = pretty(s.release);
      $('updated').textContent = 'READINESS OBSERVED · '+utc(new Date().toISOString());
      const kind = s.fenced ? 'unknown' : s.ready ? 'success' : 'partial';
      $('readiness-state').dataset.state = kind;
      $('readiness-state').textContent = `${kind.toUpperCase()} · ${s.ready?'Ready for an explicit session request':s.reason || 'Host is not ready'}. Readiness grants no execution authority.`;
      if (s.fenced || s.pending_transaction_id || s.reason === 'outcome_unknown') latch('server_uncertainty_hold',s);
      else if (!s.ready) {state.sessionOpen=false;phase('partial','Host readiness incomplete');}
      else if (!state.hold) phase(state.sessionOpen?'success':'disconnected',state.sessionOpen?'MCP session discovered':'Readiness checked; session not opened');
      holdView(); controls();
    } catch(error) {
      state.readiness = null; state.sessionOpen = false;
      $('readiness-state').dataset.state = error.kind === 'denied'?'denied':'failed';
      $('readiness-state').textContent = `${error.kind === 'denied'?'DENIED':'FAILED'} · Readiness unavailable; displayed identity may be stale.`;
      if (!state.hold) phase(error.kind === 'denied'?'denied':'stale','Readiness unavailable');
      throw error;
    }
  }
  async function connect() {
    state.sessionOpen = false; $('confirm-write').checked = false;
    phase('loading','Checking panel identity and read-only readiness');
    let sourceFailure = null;
    try {
      const response = await request('/source.json',{checkpoint:false,validate:validSource});
      state.source = response.data;
      $('source-identity').textContent = response.text;
    } catch(error) {
      state.source = null; sourceFailure = error;
      $('source-identity').textContent = 'Unavailable. No panel source identity is assumed.';
    }
    await readiness();
    if (sourceFailure) {if(!state.hold)phase('partial','Panel source identity unavailable');throw sourceFailure;}
    if (!state.hold) notice('Readiness and panel source received. Open the session explicitly to discover currently permitted tools.');
  }
  function renderStatus(s) {
    $('transport').textContent = s.transport || 'Not reported';
    $('protocol').textContent = s.protocol || 'Protocol not reported';
    $('profile').textContent = typeof s.profile === 'string' ? s.profile : 'Not reported';
    $('instrument').textContent = typeof s.instrument === 'string' ? s.instrument : 'Not reported';
    $('runs-metric').textContent = Number.isSafeInteger(s.known_runs)&&s.known_runs>=0 ? String(s.known_runs) : '—';
    if (s.last_ok) $('last-seen').textContent = 'Last MCP response '+utc(s.last_ok);
  }
  function replaceTools(tools) {
    state.generation++; state.tools = tools; state.selected = null;
    $('confirm-write').checked = false;
    $('selected-name').textContent = 'Select a tool'; $('selected-access').textContent = 'NO SELECTION';
    $('selected-description').textContent = 'Choose a tool from the current principal-filtered discovery.';
    $('schema').textContent = '{}'; $('arguments').value = '{}'; $('write-warning').hidden = true;
    $('tools-metric').textContent = String(tools.length); $('tool-count').textContent = String(tools.length);
    $('call-state').textContent = tools.length ? 'Select and review a current tool' : 'No tools granted in this discovery';
    renderTools(); controls();
  }
  async function openSession() {
    if (!state.source || !canOpen() || state.hold || !state.storageReady) return;
    invalidate('Opening session and current discovery','loading');
    const status = await request('/api/status',{validate:validStatus});
    renderStatus(status.data);
    if (status.data.connection !== 'connected') {phase('partial','MCP session is not connected');return;}
    const tools = await request('/api/tools',{validate:validTools});
    replaceTools(tools.data); state.sessionOpen = true;
    if (state.readiness.reason === 'host_not_running') {
      $('readiness-state').dataset.state = 'stale';
      $('readiness-state').textContent = 'STALE · This readiness observation preceded the explicit session start. Refresh readiness for the current fence and capacity.';
    }
    phase(tools.data.length?'success':'empty',tools.data.length?'MCP session open · current tools discovered':'Session open · no tools returned');
    notice(tools.data.length?'Current permitted tools received. No tool was automatically called.':'EMPTY · This discovery grants no visible tools.','success');
  }
  function renderTools() {
    const query = $('search').value.toLowerCase(); const list = $('tool-list'); list.replaceChildren();
    for (const tool of state.tools.filter(t=>(t.name+' '+(t.description||'')).toLowerCase().includes(query))) {
      const button = document.createElement('button'); button.type='button';button.className='tool-item';
      button.setAttribute('aria-pressed',String(state.selected===tool));button.classList.toggle('selected',state.selected===tool);
      button.append(document.createTextNode(tool.name));
      const detail=document.createElement('small');const mode=access(tool);
      detail.textContent=mode==='read'?'READ ONLY':mode==='confirm'?'CONFIRM REQUIRED · NOT A GRANT':'UNSUPPORTED ANNOTATION';
      button.append(detail);button.disabled=!usable()||state.busy;button.onclick=()=>select(tool);list.append(button);
    }
    if (!list.children.length) {const p=document.createElement('p');p.className='empty';p.textContent=state.tools.length?'EMPTY · No matching tools.':'EMPTY · No current tool discovery.';list.append(p);}
  }
  function select(tool) {
    if (!usable() || state.busy || !state.tools.includes(tool)) return;
    state.selected=tool;
    $('selected-name').textContent=tool.name;$('selected-description').textContent=tool.description||'No description supplied.';
    const mode=access(tool);$('selected-access').textContent=mode==='read'?'READ ONLY':mode==='confirm'?'CONFIRM REQUIRED':'UNSUPPORTED';
    $('selected-access').classList.toggle('caution',mode!=='read');$('schema').textContent=pretty(tool.inputSchema);
    $('arguments').value='{}';$('confirm-write').checked=false;$('write-warning').hidden=mode!=='confirm';
    $('call-state').textContent='Review the exact arguments before calling';renderTools();controls();
    if (!$('arguments').disabled) $('arguments').focus();
    else [...$('tool-list').querySelectorAll('button')].find(button=>button.getAttribute('aria-pressed')==='true')?.focus();
  }
  async function callTool(name, argumentText, confirmed) {
    if (!usable()) throw new Refusal('Session is not available. Recheck readiness and discovery.','stale');
    const tool=state.tools.find(t=>t.name===name);const mode=access(tool);
    if (!tool || mode==='unsupported' || (mode==='confirm'&&confirmed!==true)) throw new Refusal('Current discovery and fresh confirmation are required. Nothing was sent.','denied');
    let parsed;
    try {parsed=JSON.parse(argumentText);} catch {throw new Refusal('Arguments must be a JSON object. Nothing was sent.');}
    if (!record(parsed)) throw new Refusal('Arguments must be a JSON object. Nothing was sent.');
    // The parsed value is used only for shape validation. Scientific text is sent unchanged.
    const response=await request('/api/calls',{body:{name,arguments_json:argumentText,confirmed:mode==='confirm'&&confirmed===true},validate:validMCP});
    $('call-state').textContent=response.data.isError?'DENIED · Server refused the operation; inspect its committed receipt.':'SUCCESS · Server response received; qualification remains separate.';
    notice(response.data.isError?'Operation refused. The complete receipt remains available.':'Complete response received. Execution and qualification remain separate.',response.data.isError?'denied':'success');
    return response.data;
  }
  function renderRuns(data) {
    const list=$('run-list');list.replaceChildren();$('runs-metric').textContent=String(data.items.length);
    $('run-scope').textContent=typeof data.scope==='string'?data.scope:'Only runs observed in this response; not a complete server inventory.';
    if(!data.items.length){const p=document.createElement('p');p.className='empty';p.textContent='EMPTY · No runs returned. This is not evidence that the server is idle.';list.append(p);}
    for(const run of data.items.slice(0,200)){
      const row=document.createElement('div');row.className='run-row';const text=document.createElement('div'),id=document.createElement('code'),small=document.createElement('small'),badge=document.createElement('span');
      id.textContent=run.run_id||run.attempt||'Identity not reported';small.textContent='Attempt: '+(run.attempt||'Not reported')+' · Plan: '+(run.plan_id||'Not reported');
      badge.className='tag os-status';badge.textContent=run.state||run.status||'Not reported';text.append(id,small);row.append(text,badge);list.append(row);
    }
    if(data.items.length>200)$('run-scope').append(' PARTIAL VIEW · First 200 shown; the complete returned list is in the downloadable receipt.');
  }
  function renderEvents(events) {
    const list=$('event-list');list.replaceChildren();
    if(!events.length){const p=document.createElement('p');p.className='empty';p.textContent='EMPTY · No events returned.';list.append(p);}
    if(events.length>300){const p=document.createElement('p');p.textContent='PARTIAL VIEW · Latest 300 of '+events.length+' returned events. Download the complete response for all entries.';list.append(p);}
    for(const event of events.slice(-300).reverse()){
      const row=document.createElement('div');row.className='event-row';const at=document.createElement('time'),action=document.createElement('span'),outcome=document.createElement('span');
      const stamp=typeof event.recorded_at==='string'?event.recorded_at:event.at;at.textContent=utc(stamp);if(utc(stamp)!=='Not reported')at.dateTime=stamp;
      action.textContent=event.operation||event.tool||event.action||'Operation not reported';outcome.textContent=(event.outcome||'Outcome not reported')+(event.transaction_id?' · '+event.transaction_id:'');
      outcome.className='outcome';row.append(at,action,outcome);list.append(row);
    }
  }
  function disconnect() {
    invalidate('Disconnected; existing uncertainty is preserved.','disconnected');
    state.readiness=null;state.source=null;state.receipt=null;state.planArguments=null;
    $('arguments').value='{}';$('download').disabled=true;$('prepare-plan').hidden=true;
    $('result').textContent='Disconnected. Prior response bytes were removed from this page.';
    $('scientific-result').textContent='Not reported.';$('receipt-state').textContent='DISCONNECTED · No current response.';$('receipt-state').dataset.state='disconnected';
    $('receipt-summary').textContent='';$('receipt-effects').textContent='Not reported';$('receipt-transaction').textContent='Not reported';
    $('receipt-at').textContent='Not received';$('receipt-at').removeAttribute('datetime');
    $('operation-list').replaceChildren();$('operation-count').textContent='—';$('run-list').replaceChildren();$('event-list').replaceChildren();
    $('last-seen').textContent='Disconnected; reconnect before another checkpointed request.';
    notice(state.hold?'Disconnected. The earlier unknown outcome remains held.':'Disconnected. Connect explicitly to inspect readiness again.',state.hold?'unknown':'disconnected');
    holdView();controls();
  }

  $('connect-form').onsubmit=event=>{event.preventDefault();operate(connect);};
  $('open-session').onclick=()=>operate(openSession);
  $('refresh-readiness').onclick=()=>operate(readiness);
  $('disconnect').onclick=disconnect;
  $('search').oninput=renderTools;
  $('confirm-write').onchange=controls;
  $('arguments').oninput=()=>{$('confirm-write').checked=false;controls();};
  $('call').onclick=()=>operate(async()=>{
    if(!currentTool()||!usable())return;
    const name=state.selected.name,text=$('arguments').value,confirmed=$('confirm-write').checked;
    $('call-state').textContent='LOADING · Awaiting the complete checkpointed response…';
    try {await callTool(name,text,confirmed);}
    catch(error) {$('call-state').textContent=state.hold?'UNKNOWN · Do not resubmit.':`${(error.kind||'failed').toUpperCase()} · ${error.message}`;throw error;}
    finally {$('confirm-write').checked=false;}
  });
  $('prepare-plan').onclick=()=>{
    const text=state.planArguments;const tool=state.tools.find(t=>t.name==='ncp_plan_create');
    if(!text||!tool||!usable())return;
    select(tool);$('arguments').value=text;$('confirm-write').checked=false;controls();
    notice('The exact server-supplied template text is loaded. Review and confirm before creating a plan.');
  };
  $('load-capabilities').onclick=()=>operate(async()=>{
    const response=await callTool('ncp_capabilities_list','{}',false);
    if(response.isError)return;
    const items=response.structuredContent?.data?.items;
    if(!Array.isArray(items)){latch('capability_response_invalid',state.receipt?.envelope);return;}
    $('operation-count').textContent=String(items.length);$('operation-list').replaceChildren();
    if(!items.length){const p=document.createElement('p');p.className='empty';p.textContent='EMPTY · No capabilities returned.';$('operation-list').append(p);}
    for(const item of items.slice(0,300)){
      const card=document.createElement('article'),title=document.createElement('h3'),grant=document.createElement('p');
      title.textContent=item.operation_id||'Operation identity not reported';grant.textContent=item.authorized===true?'GRANTED BY CURRENT RESPONSE · Provider qualification separate':'DISCOVERED · No execution grant reported';
      card.append(title,grant);$('operation-list').append(card);
    }
    if(items.length>300){const p=document.createElement('p');p.textContent='PARTIAL VIEW · First 300 shown; all returned entries remain in the receipt.';$('operation-list').append(p);}
  });
  $('refresh-runs').onclick=()=>operate(async()=>{if(!usable())return;const r=await request('/api/runs',{validate:d=>record(d)&&Array.isArray(d.items)&&d.items.every(record)});renderRuns(r.data);notice('Observed runs refreshed. This is not a complete server job inventory.');});
  $('refresh-events').onclick=()=>operate(async()=>{if(!usable())return;const r=await request('/api/events',{validate:d=>Array.isArray(d)&&d.every(record)});renderEvents(r.data);notice('Activity received. Missing historical metadata remains missing.');});
  $('resource-form').onsubmit=event=>{event.preventDefault();operate(async()=>{if(!usable())return;const uri=$('resource-uri').value.trim();if(!uri)throw new Refusal('Enter a retained resource URI. Nothing was sent.');await request('/api/resources',{body:{uri},validate:d=>record(d)&&Array.isArray(d.contents)});notice('Retained resource received with its complete checkpoint receipt.');});};
  $('diagnose').onclick=()=>operate(async()=>{
    if(!usable())return;
    const r=await request('/api/diagnostics',{body:{},validate:validStatus});$('diagnostic-result').textContent=pretty(r.data);
    state.sessionOpen=false;state.selected=null;$('confirm-write').checked=false;
    const tools=await request('/api/tools',{validate:validTools});replaceTools(tools.data);state.sessionOpen=r.data.connection==='connected';
    phase(state.sessionOpen?(tools.data.length?'success':'empty'):'partial',state.sessionOpen?'Diagnostics and current discovery received':'MCP session is not connected');
    notice('Diagnostics completed. Tool selection and confirmation were reset. Provider qualification was not tested.');
  });
  $('download').onclick=()=>{
    if(!state.receipt)return;
    const url=URL.createObjectURL(new Blob([state.receipt.bytes],{type:'application/json'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download='odsp-complete-response.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  for(const link of document.querySelectorAll('nav a'))link.onclick=()=>{for(const other of document.querySelectorAll('nav a'))other.classList.toggle('active',other===link);};
  restoreHold();controls();
  // No automatic connection, backend polling, execution, retry, or hold clearing.
})();
