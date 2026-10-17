'use strict';
const $ = id => document.getElementById(id);
const state = {token:'', tools:[], selected:null, response:null, busy:false, polling:false, unlocked:false};
const pretty = value => JSON.stringify(value, null, 2);

function notice(message, error=false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}

async function api(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 95000);
  try {
    const response = await fetch('/api/' + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: {'Authorization':'Bearer ' + state.token, ...(body === undefined ? {} : {'Content-Type':'application/json'})},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal, cache:'no-store'
    });
    const envelope = await response.json();
    if (!response.ok) throw new Error((envelope.error?.code || 'request_failed') + ': ' + (envelope.error?.message || 'Request denied.'));
    if (!envelope || !Object.hasOwn(envelope,'data')) throw new Error('Invalid console response.');
    return envelope.data;
  } catch(error) {
    if (error.name === 'AbortError' || error instanceof TypeError) {
      throw new Error('Connection lost or timed out. A write may have applied. Inspect retained state; do not submit again blindly.');
    }
    throw error;
  } finally {clearTimeout(timer);}
}

function renderStatus(s) {
  if (!s || typeof s.connection !== 'string') throw new Error('Invalid status response.');
  $('connection').textContent = s.connection === 'connected' ? 'MCP session open' : s.connection;
  $('status-dot').className = 'dot ' + (s.connection === 'connected' ? 'live' : 'error');
  $('last-seen').textContent = s.last_ok ? 'Last MCP response ' + new Date(s.last_ok).toLocaleTimeString() : 'No successful MCP response';
  $('transport').textContent = s.transport || '—';
  $('protocol').textContent = s.protocol || 'Protocol not discovered';
  $('tools-metric').textContent = s.tools;
  $('tool-count').textContent = s.tools;
  $('runs-metric').textContent = s.known_runs;
  $('profile').textContent = s.profile;
  $('profile').title = s.profile;
  $('instrument').textContent = (s.surface==='operational'?'operational engineering':s.instrument) + (s.resume ? ' · retained profile resumed' : ' · fresh host launch');
  $('updated').textContent = 'CONSOLE STATUS · ' + new Date().toLocaleTimeString();
  const available = s.connection === 'connected' || s.connection === 'degraded';
  for (const id of ['diagnose','refresh-runs','read-resource']) $(id).disabled = !available || state.busy;
}

function renderTools() {
  const query = $('search').value.toLowerCase();
  const list = $('tool-list'); list.replaceChildren();
  for (const tool of state.tools.filter(t => (t.name + ' ' + t.description).toLowerCase().includes(query))) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'tool-item';
    button.classList.toggle('selected', state.selected?.name === tool.name);
    button.setAttribute('aria-pressed', String(state.selected?.name === tool.name));
    button.append(document.createTextNode(tool.name));
    const detail = document.createElement('small');
    detail.textContent = tool.console_access === 'read' ? 'READ ONLY' : tool.console_access === 'confirm' ? 'STATE CHANGE' : 'UNSUPPORTED';
    button.append(detail); button.onclick = () => select(tool); list.append(button);
  }
  if (!list.children.length) {const p=document.createElement('p');p.className='empty';p.textContent='No matching tools.';list.append(p);}
}

function exampleFor(tool) {
  const values = {};
  const known = {baseline:'baseline', source:'source', attempt:'operator-run-001', policy:'strict', suite:'regression'};
  for (const name of tool.inputSchema.required || []) {
    const schema = tool.inputSchema.properties?.[name] || {};
    values[name] = Object.hasOwn(known,name) ? known[name] : schema.type === 'object' ? {} : schema.type === 'array' ? [] : schema.type === 'integer' ? 0 : schema.type === 'boolean' ? false : '';
  }
  return values;
}

function select(tool, initial=false) {
  if (state.busy && !initial) return;
  state.selected = tool;
  $('selected-name').textContent = tool.name;
  $('selected-description').textContent = tool.description || '';
  $('selected-access').textContent = tool.console_access === 'read' ? 'READ ONLY' : 'CONFIRM REQUIRED';
  $('selected-access').classList.toggle('caution',tool.console_access !== 'read');
  $('schema').textContent = pretty(tool.inputSchema);
  $('arguments').value = pretty(exampleFor(tool));
  $('write-warning').hidden = tool.console_access !== 'confirm';
  $('confirm-write').checked = false;
  $('call-state').textContent = 'Review arguments before calling';
  updateCall(); renderTools();
}

function updateCall() {
  const t = state.selected;
  $('call').disabled = state.busy || !t || t.console_access === 'unsupported' || (t.console_access === 'confirm' && !$('confirm-write').checked);
  $('arguments').disabled = state.busy;
  $('confirm-write').disabled = state.busy;
}

function result(value) {
  state.response = value;
  $('result').textContent = pretty(value);
  $('download').disabled = false;
}

async function operate(task) {
  if (state.busy) return;
  state.busy = true; updateCall();
  for (const id of ['diagnose','refresh-runs','read-resource']) $(id).disabled = true;
  try {await task();}
  catch(error) {notice(error.message, true);}
  finally {
    state.busy = false; updateCall();
    if (state.unlocked) await refresh();
  }
}

function renderRuns(data) {
  const list = $('run-list'); list.replaceChildren();
  if (!data || !Array.isArray(data.items)) throw new Error('Invalid run response.');
  if (!data.items.length) {const p=document.createElement('p');p.className='empty';p.textContent='No runs observed. This is not evidence that the server is idle.';list.append(p);}
  $('runs-metric').textContent=data.items.length;
  for (const run of data.items) {
    const row=document.createElement('div'); row.className='run-row';
    const text=document.createElement('div'), id=document.createElement('code'), small=document.createElement('small'), badge=document.createElement('span');
    if(run.record_kind==='operational_attempt'){id.textContent=run.attempt;small.textContent='Numerical attempt · '+(run.execution_outcome||'retained outcome')+' · '+(run.result_ids?.length??0)+' result identities returned in this console';}
    else{id.textContent=run.run_id;small.textContent='Attempt: '+run.attempt+' · Plan: '+run.plan_id + (run.cancel_requested ? ' · Cancellation requested' : '');}
    badge.className='tag';badge.textContent=run.state||run.status; text.append(id,small);row.append(text,badge);list.append(row);
  }
}

function renderEvents(events) {
  if (!Array.isArray(events)) throw new Error('Invalid events response.');
  const list=$('event-list'); list.replaceChildren();
  for (const e of events.slice().reverse()) {
    const row=document.createElement('div');row.className='event-row';
    const at=document.createElement('time'), action=document.createElement('span'), outcome=document.createElement('span');
    at.dateTime=e.at;at.textContent=new Date(e.at).toLocaleTimeString();
    action.textContent=e.tool || e.action;outcome.textContent=e.outcome + (e.code ? ' · '+e.code : '') + (e.elapsed_ms != null ? ' · '+e.elapsed_ms+' ms' : '');outcome.className='outcome';
    row.append(at,action,outcome);list.append(row);
  }
}

async function refresh() {
  if (state.polling || !state.unlocked || state.busy || document.hidden) return;
  state.polling = true;
  try {
    renderStatus(await api('status'));
    renderEvents(await api('events'));
  } catch(error) {
    $('connection').textContent='Console unreachable';$('status-dot').className='dot error';
    $('last-seen').textContent='Displayed values may be stale';
    notice(error.message,true);
  } finally {state.polling=false;}
}

$('connect-form').onsubmit = event => {
  event.preventDefault();
  operate(async () => {
    state.token=$('token').value.trim();
    const status=await api('status');
    const tools=await api('tools');
    if (!Array.isArray(tools) || tools.some(t => typeof t.name!=='string' || !t.inputSchema)) throw new Error('Invalid tool discovery response.');
    state.tools=tools;state.unlocked=true;renderStatus(status);renderTools();
    $('token').value='';document.querySelector('.access').classList.add('unlocked');
    notice('Connected to the local console. Tool descriptions and responses come from the configured MCP server.');
    if (state.tools.length) select(state.tools[0],true);
    renderRuns(await api('runs'));
  });
};
$('search').oninput=renderTools;
$('confirm-write').onchange=updateCall;
$('arguments').oninput=() => {$('confirm-write').checked=false;updateCall();};
$('call').onclick=() => operate(async () => {
  const args=JSON.parse($('arguments').value);
  if (!args || Array.isArray(args) || typeof args!=='object') throw new Error('Arguments must be a JSON object.');
  const name=state.selected.name;
  $('call-state').textContent='Waiting for MCP response…';
  try {
    const response=await api('calls',{name,arguments:args,confirmed:$('confirm-write').checked});
    result(response);
    const refused=Boolean(response.isError);
    $('call-state').textContent=refused ? 'Server refused this call' : 'Server response received';
    notice(refused ? 'The MCP server refused this call. Inspect the response below.' : 'MCP response received. Execution completion and scientific qualification remain separate.',refused);
    renderRuns(await api('runs'));
  } catch(error) {$('call-state').textContent='Call not confirmed successful';throw error;}
  finally {$('confirm-write').checked=false;}
});
$('diagnose').onclick=() => operate(async () => {
  const data=await api('diagnostics',{});$('diagnostic-result').textContent=pretty(data);
  state.tools=await api('tools');renderTools();notice(data.warnings?.length ? data.warnings.join(' ') : 'MCP ping and discovery completed. Provider and scientific qualification were not tested.',Boolean(data.warnings?.length));
});
$('refresh-runs').onclick=() => operate(async () => {renderRuns(await api('runs'));notice('Observed run states refreshed from MCP. This is not a complete job inventory.');});
$('resource-form').onsubmit=event => {event.preventDefault();operate(async () => {result(await api('resources',{uri:$('resource-uri').value.trim()}));notice('Retained resource received from MCP.');$('result').scrollIntoView({block:'nearest'});});};
$('download').onclick=() => {
  const url=URL.createObjectURL(new Blob([pretty(state.response)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='ncp-console-response.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
for (const link of document.querySelectorAll('nav a')) link.onclick=() => {for(const other of document.querySelectorAll('nav a'))other.classList.toggle('active',other===link);};
setInterval(refresh,5000);
