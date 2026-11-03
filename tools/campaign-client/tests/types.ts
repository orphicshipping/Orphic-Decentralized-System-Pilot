import type { ReadRequest, ReadTransport } from '../src/contract.js';
import { ReadClient } from '../src/contract.js';

const valid: ReadRequest = { name: 'ncp_intake_record', arguments: { record_id: 'known-record' } };
// @ts-expect-error Mutations are outside the read client's public type contract.
const forbidden: ReadRequest = { name: 'ncp_run_submit', arguments: {} };
// @ts-expect-error A string response is not the bounded byte-stream transport.
const invalidTransport: ReadTransport = async () => 'unbounded';
const acceptedTransport: ReadTransport = async function* (_request, signal) {
  if (signal.aborted) return;
  yield new Uint8Array([32]);
};
void valid; void forbidden; void invalidTransport; void new ReadClient(acceptedTransport);
