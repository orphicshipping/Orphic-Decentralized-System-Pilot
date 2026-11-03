/** Bounded read-only MCP contract. No endpoint, credential or permission selection. */
import { TOOL_METADATA } from './metadata.js';

export type Json = null | boolean | number | string | Json[] | JsonObject;
export type JsonObject = { [key: string]: Json };
export type ReadOperation = keyof typeof TOOL_METADATA;
export type ReadRequest = { name: ReadOperation; arguments: JsonObject };
export type ViewState = 'disconnected' | 'loading' | 'returned' | 'denied' | 'failed' |
  'partial' | 'empty' | 'unknown' | 'held' | 'unsupported' | 'stale';
export const LIMITS = Object.freeze({ requestBytes: 65536, responseBytes: 262144,
  depth: 24, nodes: 32768, array: 4096, object: 1024, string: 65536, key: 512,
  chunkBytes: 32768, chunks: 1024, calls: 256 });
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

export class ContractError extends Error {
  constructor(readonly code: string) { super(code); this.name = 'ContractError'; }
}
function insist(condition: unknown, code: string): asserts condition {
  if (!condition) throw new ContractError(code);
}
export function record(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function unicode(value: string, maximum: number = LIMITS.string): void {
  let count = 0;
  for (const char of value) {
    const code = char.codePointAt(0)!;
    insist(code < 0xd800 || code > 0xdfff, 'invalid_unicode');
    insist(++count <= maximum, 'string_limit');
  }
}

/** JSON parser with duplicate-key and numeric checks before JSON.parse can lose them. */
export function parseJson(raw: Uint8Array, maximum: number = LIMITS.responseBytes,
  maximumString: number = LIMITS.string): Json {
  insist(raw instanceof Uint8Array && raw.byteLength > 0 && raw.byteLength <= maximum, 'byte_limit');
  let source: string;
  try { source = decoder.decode(raw); } catch { throw new ContractError('invalid_utf8'); }
  let at = 0, nodes = 0;
  const white = (): void => { while (/[\x20\t\r\n]/.test(source[at] ?? '\0')) at++; };
  const quoted = (maximumLength: number): string => {
    insist(source[at] === '"', 'invalid_json');
    const start = at++;
    while (at < source.length) {
      const char = source[at++];
      if (char === '\\') { at++; continue; }
      if (char === '"') {
        let result: unknown;
        try { result = JSON.parse(source.slice(start, at)); } catch { throw new ContractError('invalid_json'); }
        insist(typeof result === 'string', 'invalid_json'); unicode(result, maximumLength); return result;
      }
      insist(char !== undefined && char.charCodeAt(0) >= 32, 'invalid_json');
    }
    throw new ContractError('invalid_json');
  };
  const value = (depth: number): Json => {
    insist(depth <= LIMITS.depth && ++nodes <= LIMITS.nodes, 'complexity_limit'); white();
    const first = source[at];
    if (first === '"') return quoted(maximumString);
    if (first === '{') {
      at++; white(); const result = Object.create(null) as JsonObject; let count = 0;
      if (source[at] === '}') { at++; return result; }
      while (at < source.length) {
        white(); const key = quoted(LIMITS.key);
        insist(++count <= LIMITS.object && ++nodes <= LIMITS.nodes, 'complexity_limit');
        insist(!Object.hasOwn(result, key), 'duplicate_json_key'); white(); insist(source[at++] === ':', 'invalid_json');
        result[key] = value(depth + 1); white();
        if (source[at] === '}') { at++; return result; }
        insist(source[at++] === ',', 'invalid_json');
      }
    } else if (first === '[') {
      at++; white(); const result: Json[] = [];
      if (source[at] === ']') { at++; return result; }
      while (at < source.length) {
        insist(result.length < LIMITS.array, 'array_limit'); result.push(value(depth + 1)); white();
        if (source[at] === ']') { at++; return result; }
        insist(source[at++] === ',', 'invalid_json');
      }
    } else {
      for (const [literal, item] of [['true', true], ['false', false], ['null', null]] as const) {
        if (source.startsWith(literal, at)) { at += literal.length; return item; }
      }
      const token = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(source.slice(at))?.[0];
      insist(token && token.length <= 512, 'invalid_json_number'); at += token.length;
      const number = Number(token);
      insist(Number.isFinite(number) && Math.abs(number) <= 1e150, 'number_limit');
      if (!/[.eE]/.test(token)) insist(Number.isSafeInteger(number), 'integer_limit');
      if (number === 0) insist(!/[1-9]/.test(token.split(/[eE]/)[0]!), 'number_underflow');
      return number;
    }
    throw new ContractError('invalid_json');
  };
  const result = value(0); white(); insist(at === source.length, 'trailing_json'); return result;
}

/** Caller objects are bounded before serialization; no getters, prototypes or cycles. */
export function encodeJson(value: unknown, maximum: number = LIMITS.requestBytes): Uint8Array {
  let nodes = 0;
  const walk = (item: unknown, depth: number): void => {
    insist(++nodes <= LIMITS.nodes && depth <= LIMITS.depth, 'complexity_limit');
    if (item === null || typeof item === 'boolean') return;
    if (typeof item === 'number') {
      insist(Number.isFinite(item) && Math.abs(item) <= 1e150, 'number_limit');
      if (Number.isInteger(item)) insist(Number.isSafeInteger(item), 'integer_limit');
      return;
    }
    if (typeof item === 'string') { unicode(item); return; }
    insist(record(item) || Array.isArray(item), 'json_type');
    insist(Object.getPrototypeOf(item) === null || Object.getPrototypeOf(item) === Object.prototype ||
      Array.isArray(item) && Object.getPrototypeOf(item) === Array.prototype, 'json_type');
    const descriptors = Object.getOwnPropertyDescriptors(item);
    const keys = Object.keys(item);
    insist(keys.length <= (Array.isArray(item) ? LIMITS.array : LIMITS.object), 'complexity_limit');
    if (Array.isArray(item)) insist(keys.length === item.length, 'sparse_array');
    for (const key of keys) {
      unicode(key, LIMITS.key); insist(Object.hasOwn(descriptors[key]!, 'value'), 'json_accessor');
      walk(descriptors[key]!.value, depth + 1);
    }
  };
  walk(value, 0);
  let encoded: string;
  try { encoded = JSON.stringify(value); } catch { throw new ContractError('json_type'); }
  const raw = encoder.encode(encoded); insist(raw.byteLength <= maximum, 'byte_limit'); return raw;
}

function keys(value: JsonObject, required: string[], optional: string[] = []): void {
  insist(required.every(k => Object.hasOwn(value, k)) && Object.keys(value).every(k => [...required, ...optional].includes(k)), 'object_fields');
}
function validateSchema(value: Json, schema: JsonObject): void {
  if (schema['type'] === 'object') {
    insist(record(value) && record(schema['properties']), 'argument_type');
    const props = schema['properties'];
    const required = Array.isArray(schema['required']) ? schema['required'] as string[] : [];
    keys(value, required, Object.keys(props).filter(k => !required.includes(k)));
    for (const [key, item] of Object.entries(value)) {
      insist(record(props[key]), 'argument_schema'); validateSchema(item, props[key]);
    }
  } else if (schema['type'] === 'string') {
    insist(typeof value === 'string', 'argument_type');
    unicode(value, typeof schema['maxLength'] === 'number' ? schema['maxLength'] : LIMITS.string);
    insist(typeof schema['minLength'] !== 'number' || [...value].length >= schema['minLength'], 'argument_length');
  } else if (schema['type'] === 'integer') {
    insist(typeof value === 'number' && Number.isSafeInteger(value), 'argument_type');
    insist((typeof schema['minimum'] !== 'number' || value >= schema['minimum']) &&
      (typeof schema['maximum'] !== 'number' || value <= schema['maximum']), 'argument_range');
  } else throw new ContractError('argument_schema');
  if (Array.isArray(schema['enum'])) insist(schema['enum'].some(v => v === value), 'argument_enum');
}
export function validateRequest(input: unknown): ReadRequest {
  const value = parseJson(encodeJson(input), LIMITS.requestBytes);
  insist(record(value), 'request_object'); keys(value, ['name', 'arguments']);
  const name = value['name'];
  insist(typeof name === 'string' && Object.hasOwn(TOOL_METADATA, name), 'operation_not_allowed');
  const operation = name as ReadOperation;
  validateSchema(value['arguments']!, TOOL_METADATA[operation].inputSchema as unknown as JsonObject);
  insist(record(value['arguments']), 'argument_type');
  return { name: operation, arguments: value['arguments'] };
}

export type ValidatedResult = { requestId: string; request: ReadRequest; rpc: JsonObject;
  rawJson: string; backendText: string; envelope: JsonObject; data: JsonObject | null;
  state: ViewState; verification: string };
function equal(left: Json, right: Json): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((v, i) => equal(v, right[i]!));
  if (record(left) && record(right)) return Object.keys(left).length === Object.keys(right).length &&
    Object.entries(left).every(([k, v]) => Object.hasOwn(right, k) && equal(v, right[k]!));
  return false;
}
function noAuthority(value: Json): void {
  if (Array.isArray(value)) value.forEach(noAuthority);
  else if (record(value)) for (const [key, child] of Object.entries(value)) {
    if (key === 'authorizes_execution' || key === 'authorizes_state_admission') insist(child === false, 'unexpected_authority_claim');
    noAuthority(child);
  }
}
function verifiedLabel(request: ReadRequest, data: JsonObject | null, state: ViewState): string {
  if (state !== 'returned' || data === null) return 'No verification established by this response.';
  if (request.name === 'ncp_intake_verify' && data['profile'] === 'odsp.offline-intake.v1' && data['outcome'] === 'matched_host_capture')
    return 'Backend matched the host capture. Source identity remains unverified.';
  if (request.name === 'ncp_domain_read_verify' && data['verified'] === true && data['scope'] === 'exact_pinned_capture_content_and_contract_only')
    return 'Backend verified exact capture content and contract only. Source identity remains unverified.';
  if (request.name === 'ncp_proof_verify' && data['profile'] === 'odsp.proof-verification.v1' && data['outcome'] === 'accepted' && data['signature_verified'] === true) {
    if (data['verification_scope'] === 'ed25519_signature_attribution_only')
      return 'Backend verified signature attribution only. No computation or source authentication claim.';
    if (data['verification_scope'] === 'existing_registered_sp1_device_affine' && data['native_verification_performed'] === true)
      return 'Backend reports registered SP1 verification. Inspect its exact scope and remaining source limitations.';
  }
  return 'Receipt displayed only. A digest does not establish verified provenance.';
}
export function validateResult(raw: Uint8Array, request: ReadRequest, id: string): ValidatedResult {
  // Only the MCP-generated text copy may exceed a normal application string.
  // Its decoded object is independently parsed under the application limit.
  const rpc = parseJson(raw, LIMITS.responseBytes, LIMITS.responseBytes);
  insist(record(rpc), 'rpc_object'); keys(rpc, ['jsonrpc', 'id'], ['result', 'error']);
  insist(rpc['jsonrpc'] === '2.0' && rpc['id'] === id, 'rpc_identity');
  insist(Object.hasOwn(rpc, 'result') !== Object.hasOwn(rpc, 'error'), 'rpc_result');
  if (Object.hasOwn(rpc, 'error')) throw new ContractError('rpc_error');
  const result = rpc['result']; insist(record(result), 'mcp_result');
  keys(result, ['content', 'structuredContent', 'isError'], ['resultType']);
  // The pinned official SDK serializes the immediate-result discriminator.
  // Deferred tasks are outside this read client's selected contract.
  insist(result['resultType'] === undefined || result['resultType'] === 'complete', 'mcp_result_type');
  insist(typeof result['isError'] === 'boolean' && record(result['structuredContent']) &&
    Array.isArray(result['content']) && result['content'].length === 1, 'mcp_result');
  const content = result['content'][0]; insist(record(content), 'mcp_content'); keys(content, ['type', 'text']);
  insist(content['type'] === 'text' && typeof content['text'] === 'string', 'mcp_content');
  const envelope = result['structuredContent'];
  insist(equal(parseJson(encoder.encode(content['text'])), envelope), 'mcp_content_mismatch');
  noAuthority(envelope);
  const status = envelope['status'];
  insist(typeof status === 'string' && ['ok', 'refused', 'failed', 'partial', 'unknown', 'held', 'unsupported', 'stale'].includes(status), 'backend_status');
  insist(result['isError'] === (status !== 'ok'), 'mcp_status_mismatch');
  insist(status !== 'ok' || record(envelope['data']), 'backend_success_data');
  const data = record(envelope['data']) ? envelope['data'] : null;
  const error = record(envelope['error']) ? envelope['error'] : null;
  const reason = envelope['reason'] ?? error?.['code'] ?? data?.['reason'];
  let state: ViewState;
  if (reason === 'outcome_unknown' || reason === 'runtime_cleanup_uncertain' || reason === 'native_cleanup_uncertain') state = 'unknown';
  else if (status === 'refused') state = 'denied';
  else if (status !== 'ok') state = status as ViewState;
  else if (data && data['next_offset'] !== undefined && data['next_offset'] !== null) state = 'partial';
  else if (data && (Array.isArray(data['items']) && data['items'].length === 0 || data['record_count'] === 0 || data['records'] === 0)) state = 'empty';
  else state = 'returned';
  return { requestId: id, request, rpc, rawJson: decoder.decode(raw), backendText: content['text'],
    envelope, data, state, verification: verifiedLabel(request, data, state) };
}

/** Transport is an already-authenticated, host-supplied bounded byte stream. */
export type ReadTransport = (request: Uint8Array, signal: AbortSignal) => AsyncIterable<Uint8Array>;
export class ReadClient {
  private readonly sessionId = crypto.randomUUID();
  private sequence = 0;
  private busy = false;
  private fenced = false;
  constructor(private readonly transport: ReadTransport, private readonly timeoutMs = 10000) {
    insist(typeof transport === 'function' && Number.isInteger(timeoutMs) && timeoutMs >= 1 && timeoutMs <= 30000, 'invalid_host_binding');
  }
  get available(): boolean { return !this.busy && !this.fenced && this.sequence < LIMITS.calls; }
  async read(input: ReadRequest): Promise<ValidatedResult> {
    insist(!this.busy && !this.fenced && this.sequence < LIMITS.calls, 'client_unavailable');
    const request = validateRequest(input);
    const id = `${this.sessionId}:${++this.sequence}`;
    const bytes = encodeJson({ jsonrpc: '2.0', id, method: 'tools/call', params: request });
    this.busy = true;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const collect = async (): Promise<Uint8Array> => {
        const chunks: Uint8Array[] = []; let total = 0;
        for await (const part of this.transport(bytes, abort.signal)) {
          insist(!abort.signal.aborted, 'transport_aborted');
          insist(part instanceof Uint8Array && part.byteLength > 0 && part.byteLength <= LIMITS.chunkBytes, 'transport_chunk');
          total += part.byteLength;
          insist(total <= LIMITS.responseBytes && chunks.length < LIMITS.chunks, 'byte_limit'); chunks.push(part.slice());
        }
        const result = new Uint8Array(total); let offset = 0;
        for (const part of chunks) { result.set(part, offset); offset += part.byteLength; }
        return result;
      };
      const expired = new Promise<never>((_, reject) => {
        timer = setTimeout(() => { abort.abort(); reject(new ContractError('transport_timeout_unknown')); }, this.timeoutMs);
      });
      return validateResult(await Promise.race([collect(), expired]), request, id);
    } catch (error) {
      // Never relaunch after ambiguous transport/custody. The embedding host
      // must close its transport and construct a new client deliberately.
      this.fenced = true; abort.abort();
      throw error instanceof ContractError ? error : new ContractError('transport_unknown');
    } finally { if (timer !== undefined) clearTimeout(timer); this.busy = false; }
  }
}
