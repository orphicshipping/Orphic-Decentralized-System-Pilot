<!-- ncp-folder-entry -->
# MCP entry

- path: `tools/campaign-client`
- class: `navigation`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `scripts`, `src`, `tests`, `web`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Bounded read client and evidence view

This optional TypeScript component inspects the existing intake, domain-read and
proof-verification profiles. It does not add a service, endpoint, login, credential,
tool-selection workflow, storage, execution permission or deployment. The embedding
application supplies an already-authenticated transport and one selected request.
Existing backend grants remain authoritative on every call.

The fixed allowlist is the four `ncp_intake_*`, four `ncp_domain_read_*` and two
`ncp_proof_*` tools in `src/metadata.ts`. That file snapshots their exact input and
output schemas and read-only annotations. The Python metadata regression compares
every field with the current backend. Unsupported names, extra arguments, identity
or endpoint rebinding, absent required fields and type/range errors refuse before
the transport is called. This client is deliberately narrower than the full ODSP
console; host-side selection/skills/cache remain separate existing work.

## Contract and limits

`ReadClient` constructs one JSON-RPC `tools/call` request with a unique client nonce
and monotonic sequence. It admits one in-flight request and at most 256 calls per
instance. The nonce is request correlation, not a credential or authority. The
transport returns an async iterable of raw `Uint8Array` chunks, each at most 32,768
bytes, at most 1,024 chunks and 262,144 complete response bytes. Requests, including
their JSON-RPC envelope, are limited to 65,536 bytes. The default timeout is ten
seconds; trusted construction may select 1–30,000 milliseconds. These are local
client limits, not an operational SLO.

Raw JSON validation rejects duplicate keys (including escaped aliases), malformed
UTF-8, BOM, lone surrogates, invalid numbers, nonfinite/overflow/nonzero-underflow,
integer literals outside ±(2^53−1), depth over 24, excessive nodes/containers and
trailing documents. Arbitrary objects are bounded before serialization and getters,
custom prototypes, cycles and sparse arrays refuse. Decimal JSON numbers use the
JavaScript binary64 value; this is not decimal metrology or a new cross-language
canonical digest algorithm.

`ValidatedResult.rawJson` and `backendText` preserve the exact received JSON and
the backend's original text copy. The expanded MCP inspector shows `rawJson`
unchanged. The parsed view may change numeric spelling (`1.0` to `1`, for example);
do not reconstruct a canonical Python receipt by reserializing that parsed view.
Pass the host's original receipt JSON to `receipt_json` without changing it.

Responses must bind the exact outstanding ID and contain one text item plus
structured content. Both copies must agree in complete parsed content; status and
`isError` must agree. The entire duplicated response is bounded. Authority claims
cannot turn true anywhere in these read-profile results. No local digest comparison
is presented as provenance verification. The view labels only the specific existing
backend verification response and keeps its limited scope explicit: matching host
capture, signature attribution, or a reported registered native verifier result.

On malformed, mismatched, failed or timed-out transport, the client aborts and
fences itself. It never automatically retries. Abort is a request to the host
transport, not proof of remote termination or rollback. The host must close its
transport before deliberately constructing a replacement client; these read-only
requests cannot clear any existing backend uncertainty hold. A backend refusal is
shown as denied; unknown, held, unsupported, failed, stale, partial, empty, loading
and returned states remain distinct. Additional page offsets are displayed but
never followed automatically.

## Embedding and design

Build output exports `contract.js` and `viewer.js`:

```js
import {ReadClient} from './contract.js';
import {mountReceiptViewer} from './viewer.js';

const client = new ReadClient(authenticatedTransport);
const viewer = mountReceiptViewer(document.querySelector('#receipt-viewer'), {
  client,
  request: {name: 'ncp_intake_record', arguments: {record_id: 'approved-record'}},
  sourceLabel: 'Host-declared environment label'
});
// A trusted parent workflow may select another allowed read request:
viewer.setRequest({name: 'ncp_intake_records', arguments: {limit: 16}});
```

`authenticatedTransport(requestBytes, abortSignal)` returns an async byte stream
for exactly one complete JSON-RPC response. It must apply the existing service
authentication, current principal grants and bounded I/O. No default URL, header,
token, fetch fallback or unauthenticated direct-origin adapter is supplied here.
The static `index.html` is honestly disconnected and has `connect-src 'none'`;
production embedding belongs inside the existing approved application contract.

`web/theme.css` is byte-identical to the retained ODSP Terminal kit in
`tools/odrp-private-host-candidate/panel/web/theme.css`. Layout uses its scoped
`orphic`/`os-` primitives, square panels and existing colors. Amber denotes action,
not success or authority. Every source string is rendered through `textContent`,
including complete untruncated response/provenance JSON. No response-derived HTML,
script, URL, image or external font is inserted. Request identity and local UTC
observation time are separate from source measurement timestamps. After failure,
prior evidence remains visible and explicitly stale.

## Reproducible local checks

Runtime JavaScript has no npm dependencies. The development compiler is the exact
official `typescript@6.0.3` package, pinned with registry SHA-512 integrity and an
independent archive SHA-256 in `compiler-lock.json`. Version 6.0.3 is selected as
the dependency-free JavaScript compiler; it is not described as the latest release.
Its measured archive plus extracted footprint is 28,862,681 bytes. No install
scripts or global install are needed. The compiler's actual `strict` family and
additional checks are enabled; stripping TypeScript alone is not type validation.
See [official installation](https://www.typescriptlang.org/download/) and
[strict mode](https://www.typescriptlang.org/tsconfig/strict.html).

With Node 24 and this directory as the working directory:

```sh
npm ci --ignore-scripts
node node_modules/typescript/lib/tsc.js --project tests/tsconfig.json
node scripts/build.mjs "$(realpath node_modules/typescript/lib/tsc.js)" /tmp/odsp-read-client-build
ODSP_CLIENT_BUILD=/tmp/odsp-read-client-build node --test tests/contract.test.mjs
```

The build destination must be new and outside this source directory. An existing
verified compiler can be passed by absolute path instead of installing another.
The build receipt records compiler and emitted-file identities; the integration
owner binds these to the final combined source commit. Do not package a mutable
working-tree build as an already qualified release.

From the repository root, using its pinned Python environment:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B -m unittest discover -s tests -p test_campaign_client_metadata.py -v
```

The optional browser check needs an existing Playwright Python installation,
Chromium, and a separate Python executable with the pinned MCP 2.3.0 dependency:

```sh
python tools/campaign-client/tests/browser_journey.py \
  --build /tmp/odsp-read-client-build --output /tmp/odsp-read-client-browser \
  --mcp-python /absolute/pinned/environment/bin/python --browser /usr/bin/chromium
```

That test launches a loopback-only disposable static server, then actual Chromium
interactions call a host-supplied **test** bridge into the official MCP subprocess
and current principal policy with synthetic captures. It tests complete receipts,
backend verification, denied/partial/empty/unknown states, reconnect, markup
injection resistance, focus, reduced motion and 320/375/768/1440 layouts. Its 200%
check uses CSS zoom, not native browser zoom. It establishes neither deployed
authentication nor a new production transport. The bridge is test-only and is
never part of the production build.
