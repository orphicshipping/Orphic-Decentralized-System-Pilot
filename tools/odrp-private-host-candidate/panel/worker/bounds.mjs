// Bounded local parsing/I/O. These limits do not undo a dispatched transaction.
export const encoder = new TextEncoder();
export const exact = (value, fields) => value !== null && typeof value === 'object'
  && !Array.isArray(value) && Object.keys(value).sort().join('\0') === [...fields].sort().join('\0');

export function uniqueJson(text, maxDepth = 32) {
  const value = JSON.parse(text), containers = [];
  for (let i = 0; i < text.length;) {
    const c = text[i];
    if (c === '{' || c === '[') {
      containers.push(c === '{' ? new Set() : null);
      if (containers.length > maxDepth) throw Error('json_depth');
      i++;
    } else if (c === '}' || c === ']') { containers.pop(); i++; }
    else if (c === '"') {
      const start = i++;
      while (i < text.length) {
        if (text[i] === '\\') { i += 2; continue; }
        if (text[i++] === '"') break;
      }
      let next = i;
      while (/[ \t\r\n]/.test(text[next] || '\0')) next++;
      if (text[next] === ':') {
        const keys = containers.at(-1), key = JSON.parse(text.slice(start, i));
        if (!keys || keys.has(key)) throw Error('duplicate_json_key');
        keys.add(key);
      }
    } else i++;
  }
  return value;
}

export function utf8(bytes) { return new TextDecoder('utf-8', {fatal: true}).decode(bytes); }

export async function deadline(operation, milliseconds, parentSignal) {
  const controller = new AbortController();
  let timer, onAbort;
  const interrupted = new Promise((_, reject) => {
    const refuse = () => { controller.abort(); reject(Error('deadline_or_cancelled')); };
    onAbort = refuse;
    timer = setTimeout(refuse, milliseconds);
    parentSignal?.addEventListener('abort', onAbort, {once: true});
    if (parentSignal?.aborted) refuse();
  });
  try {
    return await Promise.race([interrupted, Promise.resolve().then(() => {
      if (controller.signal.aborted) throw Error('deadline_or_cancelled');
      return operation(controller.signal);
    })]);
  } finally {
    clearTimeout(timer);
    parentSignal?.removeEventListener('abort', onAbort);
  }
}

export async function readBounded(stream, maximum, signal) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader(), chunks = [];
  let length = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener('abort', cancel, {once: true});
  try {
    for (;;) {
      if (signal?.aborted) throw Error('body_cancelled');
      const {done, value} = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) throw Error('invalid_body_chunk');
      length += value.byteLength;
      if (length > maximum) throw Error('body_budget');
      chunks.push(value);
    }
    if (signal?.aborted) throw Error('body_cancelled');
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes;
  } catch (error) { cancel(); throw error; }
  finally { signal?.removeEventListener('abort', cancel); reader.releaseLock(); }
}

export function declaredLength(headers, maximum) {
  const length = headers.get('content-length');
  if (length !== null && (!/^(0|[1-9][0-9]*)$/.test(length)
      || !Number.isSafeInteger(Number(length)) || Number(length) > maximum)) throw Error('body_budget');
}
