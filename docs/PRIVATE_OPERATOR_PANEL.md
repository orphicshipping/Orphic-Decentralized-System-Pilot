# Private NCP operator panel

This candidate completes the existing operator-console composition with a
principal-bound backend path. It lives in
[`tools/odrp-private-host-candidate/panel`](../tools/odrp-private-host-candidate/panel).
The historical panel in `tools/private-mcp-host/panel` and its paused deployment
configuration remain separate.

## Request and authority boundaries

The browser uses same-origin paths only. An authenticated server proxy verifies
the Access `Cf-Access-Jwt-Assertion` signature, issuer, application audience,
time claims and exact subject binding. Its server-only mapping selects one
enabled ODSP principal and that principal's existing backend bearer token.
Browser arguments cannot select a principal, token, provider or destination.
The proxy uses a mandatory Worker service binding to the unchanged private
gateway; it has no public-backend fetch fallback. The gateway continues to
reject requests carrying a browser Origin header.

The gateway reconstructs trusted principal context. The existing serialized
Worker, durable transaction protocol, installed adapter and official MCP
stdio service enforce current tool, scientific-operation and resource grants.
Confirmation in the panel expresses operator intent and is not a grant.
Discovery, successful transport and amber emphasis do not mean execution
authority, scientific verification or physical qualification.

The proxy authenticates static assets as well as API requests. Backend bearer
tokens and Access assertions are never copied into assets, browser storage,
error text or logs. Missing configuration and invalid signatures fail closed.
Every API request also requires the fixed same-origin panel request header;
this is a cross-site request guard, not a credential or an authorization grant.
No Access application, account policy, credential or service binding is created
by the source or its build command.

## Interaction and retained evidence

Connection checks read the authenticated panel source identity and backend
`/api/readiness`. Readiness does not start the container, create a checkpoint,
clear a hold or promise that a particular operation will be authorized.
Opening the session and fetching discovery are explicit actions. The remaining
backend API routes are checkpointed transactions, including GET status, tools,
runs and events. The browser does not poll these routes or automatically retry.

The workbench displays the server's tool descriptions, annotations and schemas.
It sends the editor's JSON argument string unchanged as `arguments_json`;
JavaScript parsing is not used to rewrite scientific numeric inputs. State
changes require a fresh confirmation after editing the tool or arguments.
The full HTTP envelope and original response text remain available for
inspection and download, including transaction, checkpoint, request and response
hashes. A run marked completed is not a qualification result.

An interrupted or malformed response after dispatch may have committed effects.
The panel preserves typed `effect_state`, transaction ID and hold evidence and
records a bounded pending marker before dispatch, so uncertainty survives a
page reload during the call as well as a failed response. It retains no request
arguments in browser storage. Only a complete acknowledgment or trustworthy
before-dispatch refusal can clear that request's own pending marker.
Readiness remains available for inspection, but cannot clear that local lost-
response warning. The local latch is an operator safeguard, not the backend's
authoritative fence. Other clients remain subject to durable host state and
current policy. There is no browser reset, retry, restart, grant-edit or
clear-fence endpoint. Follow the
[recovery runbook](PRIVATE_HOST_OPERATIONS.md#recovery-and-owned-backup).

## Build from committed source

Use the selected Node runtime and a clean committed checkout. This command
performs no network call, dependency installation, publishing or deployment:

```sh
node tools/odrp-private-host-candidate/panel/scripts/build.mjs \
  /absolute/new-panel-build
```

The output contains the Worker module entry `index.mjs`, its source modules,
embedded local assets and `build-receipt.json`. The receipt binds every input
and output byte to the exact source commit and tree. Authenticated `/source.json`
identifies the panel source separately from the backend release reported by
readiness. Rebuilding the backend uses the existing
[private release procedure](PRIVATE_HOST_OPERATIONS.md#reproducible-local-release).
Select the exact panel build and backend release together; an older test receipt
does not qualify a later source revision.

## Host-selected configuration

| Binding | Meaning |
| --- | --- |
| `PANEL_HOSTNAME` | Exact HTTPS panel hostname, without a port or path |
| `PANEL_BACKEND_ORIGIN` | Exact HTTPS backend origin used for the gateway's host check |
| `BACKEND` | Private Worker service binding to the existing gateway |
| `ACCESS_ISSUER` | Approved `https://<team>.cloudflareaccess.com` issuer |
| `ACCESS_AUDIENCE` | Exact approved Access application audience |
| `PANEL_PRINCIPALS_JSON` | Secret server mapping with schema `odsp.panel-principals.v1` and exact subject/principal/enabled/backend_token rows |

The mapping must match the gateway's current `PRINCIPAL_AUTH_JSON` and the
installed adapter's principal policy. Do not invent those bindings from a
browser session, use a shared-token fallback, or increase grants to make a
journey pass. Backend release identity, owner, generation, storage policy and
retained-checkpoint bindings remain required as documented in the operating
runbook. No deploy-ready configuration with real destinations or secrets is
included.

The proxy bounds request bodies to 262,144 bytes and responses to 32 MiB, the
existing Worker's maximum supported transport response. The complete MCP
operation envelope retains its smaller existing limit. A transport overflow
refuses without truncation; if dispatch already occurred its effect is
unconfirmed. The proxy's 365-second backend deadline accommodates the existing
maximum transaction and storage deadlines; readiness has its separate bounded
deadline. Timeouts never imply rollback. These are limits, not service-level
commitments or a reason to raise backend budgets.

## Verification and deployment gates

The local acceptance harness connects a real Chromium browser through the
production proxy, gateway and Worker source to an independently installed
adapter and actual official MCP subprocess. Synthetic Access signing keys and
subjects exist only in the test harness. SQLite transactions and fsync-backed
objects substitute for hosted Durable Object/R2 storage and are labelled as
such. Process restart is a harness action, not a new public API.

Acceptance must cover authenticated discovery and execution, current denials,
principal isolation, complete receipts, unchanged argument bytes, retained
inspection after restart and uncertainty without a second dispatch. Browser
checks include keyboard focus, status text independent of colour, reduced
motion, 320/375/768/1440-pixel viewports, 200% zoom and long identifiers.
An exact-head report records executed checks and any gaps; this document alone
is not a pass receipt.

Publication and deployment still require the exact source/PR decision, selected
private host and image delivery, approved existing Access application and subject
mapping, private service binding, matching runtime/owner/storage identities,
and deployed verification. Preserve the historical unknown transaction and
paused portal. Local signatures, browser tests and filesystem substitutes do
not establish deployed identity, hosted storage semantics, availability,
production safety or numerical/physical validity.

The authentication design follows the official
[Access assertion verification guide](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
and the Worker-to-Worker boundary follows
[Cloudflare's service-binding guidance](https://developers.cloudflare.com/workers/best-practices/workers-best-practices/).
