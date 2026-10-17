# Private hosted MCP control adapter

Purpose: connect the existing owner-private NCP workbench to the genuine installed
Python control MCP server, with restart-safe retained records. The scientific core
is unchanged from `c47a8621cd97f8d46afe526ed15534111fc6e347` (PR #240).

## Boundaries

`gateway.mjs` authenticates a fixed server-only bearer secret, denies browser
cross-origin requests, bounds request bodies, and routes only the seven operator
API paths to a single named Durable Object. `worker.mjs` serializes transactions,
opens one private container, and commits immutable checkpoints to private R2
storage before acknowledgment. A durable pending marker fences unknown outcomes;
there is no automatic retry, rollback, or rerun. No public bucket is enabled.

`adapter.py` reuses the existing operator-console MCP client. Every transaction
restores the last committed checkpoint to its disposable state directory, starts
the actual installed NCP stdio child, dispatches the existing MCP tool, closes the
child, and returns the next checkpoint. Submitted numerical runs finish before
the child closes. No scientific calculation is implemented in this adapter.

The unchanged example profile discovers nine operations and grants seven, with
four execution attempts and no editable candidate parameter domains. This is a
bounded synthetic profile, not a general physical instrument or user-data service.
Checkpoints are bounded to 16 MiB compressed and expanded; arbitrary providers,
long-running external jobs, profile editing and shell access are not exposed.
Observed run IDs and metadata-only events survive restarts in the checkpoint.

JSON number spelling matters to NCP's frozen record identities. A JavaScript hop
can change `1.0` to `1` or round a large integer. Clients must send `arguments_json`
as a JSON **string**, not reserialize parsed scientific objects. Responses include
`raw_json` for exact JSON decoding and download. Template responses include
`plan_arguments_json`, which fills the UI editor without changing numbers. The
ordinary `data` field is useful for navigation but must not be used to reconstruct
hashed scientific inputs in JavaScript. Core identity validation is not weakened.

## Deployment

The Dockerfile consumes the already-tested wheel at `wheel/` in a prepared build
context, plus this directory, the original console module/requirements and the
numerical profile preparation example. It runs Python 3.12 as UID 10001. The base
image is digest-pinned. Supply the local CA through the `proxy_ca` BuildKit secret;
no proxy CA or registry credential is added to image layers.

Cloudflare resources are dedicated: `ncp-control`, `ncp-control-container` and
`ncp-control-retained`. The container maximum is one, size `basic`, with private
networking, outbound Internet disabled, and a 120-second inactivity timeout.
Cloudflare usage charges apply; no subscription upgrade is performed. Workers
and Containers are a hosted prototype, not an HA or certified service.

Worker metadata binds `HOST` to `NCPHost`, `RETAINED` to the private R2 bucket and
`ACCESS_TOKEN` as a secret. Only the private Site gets the corresponding
`NCP_BACKEND_TOKEN` secret; the browser never receives it. Preserve Site audience.
All 14 existing MCP tools are discovered; this is an HTTP operator adapter to
stdio MCP, not a new native Streamable HTTP MCP endpoint.

## Verification

`panel/` contains a reproducible source copy of the owner-private Site, including
its retained offline explorer. Site source revision:
`a72d4bfd13f71c69843ff2f73bc4f372f2110506`. Run `npm ci --ignore-scripts`,
`npm run build`, and `npm test` in that directory. Push changes to the existing
Site source repository before publishing the matching commit/archive; the GitHub
copy alone does not deploy a Site. No secret value is present in either source.

```sh
node --test tools/private-mcp-host/test_gateway.mjs
NCP_TRANSACTION_STATE=/absolute/task-owned-test/state python -m unittest discover \
  -s tools/private-mcp-host -p test_adapter.py
```

Use an isolated environment with the installed candidate wheel, the console
requirements and `httpx==0.28.1` for the ASGI test. Tests execute the real MCP child.
`acceptance.py` probes the private HTTPS API, executes all seven operations, reads
their artifacts, and compares retained results after restart. Credentials are
supplied through hidden stdin and are not written into receipts. It does not
automatically retry state-changing failures.

## Recovery and maintenance

A `host_not_ready` response precedes a transaction and may be rechecked. An
`outcome_unknown` response leaves a persistent fence. Do not clear it or rerun
blindly: inspect the pending transaction ID, committed checkpoint pointer, any
matching uploaded checkpoint and container-owned records. Preserve all evidence;
adopt a checkpoint only after verifying its outcome and identities. This manual
recovery remains administrator work, not a browser button.

Before a rollout or maintenance stop, verify no pending transaction. Afterward,
use the receipt's run ID to verify exact retained recovery without new execution.
The normal idle shutdown is safe after an acknowledged checkpoint. Browser restart,
upgrade and profile-edit controls remain deliberately unavailable.

The unchanged core's native/provider qualification gates are not replaced by these
hosting checks. Visual browser verification requires an available browser-control
facility; DOM and API checks are not claimed as a browser screenshot or click test.
