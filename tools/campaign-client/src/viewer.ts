/** Optional human inspection. Same backend tools, no new permission or workflow. */
import { ContractError, ReadClient, encodeJson, record, validateRequest } from './contract.js';
import type { JsonObject, ReadRequest, ValidatedResult, ViewState } from './contract.js';

export type ViewerOptions = { client: ReadClient; request: ReadRequest; sourceLabel: string };
export function mountReceiptViewer(root: HTMLElement, options: ViewerOptions): { setRequest: (request: ReadRequest) => void; destroy: () => void } {
  if (typeof options.sourceLabel !== 'string' || options.sourceLabel.length > 256) throw new ContractError('source_label_limit');
  encodeJson(options.sourceLabel, 2048);
  let request = validateRequest(options.request), current: ValidatedResult | null = null, busy = false, generation = 0;
  root.classList.add('orphic'); root.dataset['orphicTheme'] = 'odsp-terminal';
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', className = ''): HTMLElementTagNameMap[K] => {
    const node = document.createElement(tag); node.textContent = text; node.className = className; return node;
  };
  const shell = el('main', '', 'os-shell'); const header = el('header', '', 'os-masthead');
  header.append(el('h1', 'ODSP evidence'), el('p', options.sourceLabel, 'os-code'));
  const notice = el('p', 'Disconnected — no response has been received.', 'os-notice'); notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
  const state = el('span', 'DISCONNECTED', 'os-status'); state.dataset['state'] = 'disconnected';
  const grid = el('div', '', 'os-grid'); const work = el('section', '', 'os-panel');
  const heading = el('h2', 'Receipt inspection', 'os-panel-heading'); work.setAttribute('aria-label', 'Receipt inspection');
  const operation = el('p', request.name, 'os-code'); const args = el('pre', JSON.stringify(request.arguments, null, 2), 'os-code'); args.setAttribute('aria-label', 'Read arguments');
  const read = el('button', 'Read selected evidence'); read.type = 'button';
  const verify = el('p', 'No verification established.', 'os-muted');
  const payload = el('pre', 'No response yet.', 'os-code'); payload.tabIndex = 0; payload.setAttribute('aria-label', 'Complete backend response');
  const raw = el('pre', 'No MCP response yet.', 'os-code'); raw.tabIndex = 0; raw.setAttribute('aria-label', 'Complete MCP envelope');
  const detail = el('details'); detail.append(el('summary', 'Complete MCP envelope, including text copy'), raw);
  work.append(heading, operation, args, read, verify, el('h2', 'Backend response (parsed)'),
    el('p', 'Parsed numbers use JavaScript binary64. The expanded MCP envelope preserves the exact received JSON.', 'os-muted'), payload, detail);
  const evidence = el('aside', '', 'os-panel'); evidence.setAttribute('aria-label', 'Supporting evidence');
  const provenance = el('pre', 'Not reported.', 'os-code'); provenance.tabIndex = 0; provenance.setAttribute('aria-label', 'Reported provenance');
  const observed = el('p', 'Local observation time: not observed.', 'os-code');
  const identity = el('p', 'Request identity: none.', 'os-code');
  evidence.append(el('h2', 'Supporting evidence', 'os-panel-heading'), state, observed, identity,
    el('h3', 'Reported provenance'), provenance,
    el('p', 'This view grants no execution, admission or source authenticity. Receipt digests are not authentication.', 'os-muted'));
  grid.append(work, evidence); shell.append(header, notice, grid); root.replaceChildren(shell);
  const phase = (value: ViewState, message: string): void => { state.textContent = value.toUpperCase(); state.dataset['state'] = value; notice.textContent = message; };
  const show = (result: ValidatedResult): void => {
    current = result;
    const data = result.data;
    const nested = data && record(data['receipt']) ? data['receipt'] : null;
    const source: JsonObject | null = data && record(data['provenance']) ? data['provenance'] :
      nested && record(nested['provenance']) ? nested['provenance'] : null;
    provenance.textContent = source ? JSON.stringify(source, null, 2) : 'No dedicated provenance object reported; inspect the complete response.';
    payload.textContent = JSON.stringify(result.envelope, null, 2);
    raw.textContent = result.rawJson;
    verify.textContent = result.verification;
    observed.textContent = `Local observation time: ${new Date().toISOString()} (UTC; not source measurement time).`;
    identity.textContent = `Request identity: ${result.requestId}`;
    phase(result.state, result.state === 'partial' ? 'Partial page — additional records remain; no automatic follow-up was sent.' : `Backend response: ${result.state}.`);
  };
  const onRead = async (): Promise<void> => {
    if (busy) return; busy = true; read.disabled = true;
    const selected = generation; phase('loading', 'Waiting for the selected read-only backend operation.');
    if (current) verify.textContent = 'Prior response remains visible; no new verification has returned.';
    try { const result = await options.client.read(request); if (selected === generation) show(result); }
    catch (error) {
      if (selected === generation) {
        const code = error instanceof ContractError ? error.code : 'transport_unknown';
        phase('unknown', `Response unavailable or invalid (${code}). No automatic retry was sent. The host must reconnect before another read.`);
        verify.textContent = 'No new verification established. Prior evidence, if present, remains visible as stale.';
        if (current !== null) identity.textContent += ' — prior response, stale after this failure.';
      }
    } finally { busy = false; if (selected === generation) read.disabled = !options.client.available; }
  };
  read.addEventListener('click', onRead);
  return {
    setRequest(next: ReadRequest): void {
      if (busy) throw new ContractError('viewer_busy'); request = validateRequest(next); generation++;
      operation.textContent = request.name; args.textContent = JSON.stringify(request.arguments, null, 2);
      if (current) {
        phase('stale', 'Selection changed. Displayed evidence belongs to the prior request.');
        verify.textContent = 'Stale prior response. Any reported verification applies only to that prior request.';
      }
      else phase('disconnected', 'No response for the selected request.');
    },
    destroy(): void { generation++; read.removeEventListener('click', onRead); root.replaceChildren(); }
  };
}
