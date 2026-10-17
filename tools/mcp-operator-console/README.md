# NCP operator console — connected local increment

A separate operator interface for the existing NCP **control** MCP server. No existing NCP core source files are changed. All scientific actions travel through the official MCP client over stdio; the console never calls `Session`, `ControlHost`, or an instrument implementation directly.

Built against `notationsystems/Notations-Control-Protocol` at `cc5d7f31cfeaeb4d897e0864e0fcc560355a0d39`. Retained in `tools/mcp-operator-console/` as an optional local client; core repairs can be integrated independently. It does not change the installed NCP package or its CLI entry points.

## What works

- Actual MCP discovery and protocol status, with the last successful MCP response timestamp.
- Searchable tool inventory with server-published descriptions and input schemas.
- JSON argument editing, schema validation, explicit confirmation for every state-changing call, and original structured responses.
- Run inspection and polling of runs observed during this console process; a known run ID can be queried using `ncp_run_get`.
- Retained plan/run/artifact resource reads and response downloads.
- Real MCP ping and discovery diagnostics, separate from scientific/provider qualification. The pinned host returns method-not-found for ping; the console reports that warning and uses successful tool discovery as the available liveness check.
- Bounded metadata-only activity display and rotating local event logs. Tool arguments, result data, and access tokens are not written to those events.
- Loopback binding, private bearer token, exact Host/Origin checks, bounded HTTP request body, and a restrictive content security policy.

## Launch beside the Python host

Requires Python 3.12+, an **installed** NCP package with its `mcp-control` extra, and a local browser. The selected NCP interpreter is launched with `-I`; use an installed wheel/package, not a source-only `PYTHONPATH` arrangement. It can be the same interpreter as the console.

From this directory, create a console environment and install the adapter dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Install your NCP checkout into the interpreter you intend to launch. For example (substitute the actual repository path):

```sh
.venv/bin/python -m pip install '/absolute/path/Notations-Control-Protocol[mcp-control]'
```

Prepare a **synthetic** builtin profile and a private token. These commands create new local files, not production instrument bindings:

```sh
.venv/bin/python -m ciw.net control demo-config --output-dir "$PWD/runtime/profile"
.venv/bin/python create_token.py --output "$PWD/runtime/console.token"
```

Start the console (substitute absolute paths, or run these commands from this directory):

```sh
.venv/bin/python console.py \
  --python "$PWD/.venv/bin/python" \
  --profile "$PWD/runtime/profile/profile.json" \
  --state-dir "$PWD/runtime/console" \
  --token-file "$PWD/runtime/console.token"
```

Open **http://127.0.0.1:8765** on the **same machine**. Paste the token file's contents into the console. It stays in page memory, not browser storage or the URL. Refreshing the page requires unlocking again.

For an existing retained profile, add `--resume` deliberately. Do not run two host processes against the same profile output. Starting this console launches its own stdio child; it does not attach to an already-running stdio process. Use `--instrument polymer`, `temperature`, or `link-scalar` only with a matching profile and its prerequisites.

The console stays available if startup fails; inspect `runtime/console/server-stderr.log` locally. That file may contain sensitive provider diagnostics and is not served to the browser. Manage its retention locally; the separate event journal rotates at 2 MiB with three backups.

## First operating journey

1. Call `ncp_capabilities_list` to inspect the actual bound contracts and grants. The wider workflow catalog is not a list of executable grants.
2. Call `ncp_plan_template` with `{"baseline":"baseline"}`.
3. Copy the returned graph into `ncp_plan_create` arguments with `source` and `baseline` set to the granted names. Review and confirm.
4. Inspect the returned plan and digest. Call `ncp_plan_admit` with that exact `plan_id` and `expected_digest` after review.
5. Call `ncp_run_submit` with the same identifiers and a deliberate `attempt` name. A new attempt intentionally creates new execution; reuse the original attempt only when resolving the same intent.
6. Inspect `ncp_run_get` and its retained evidence. Completed execution is not automatically PASS or physical qualification.

Transport timeouts have **unknown outcomes**. The console does not automatically retry calls. Inspect retained state first. Cancellation can record intent without terminating a running provider. Closing the console process closes its owned stdio child; avoid closing it during active or uncertain work.

## Deliberately unavailable

- Restart/drain, upgrades, dependency installation, grant edits, profile edits, arbitrary shell, and physical device control.
- A complete job inventory: the present MCP control server has per-ID lookup but no enumerate-all-runs operation. The UI explicitly labels its run list as incomplete.
- Native leakage-provider launch parameters; this increment supports the four listed local profile types only.
- Board refit, knowledge, simulation, and external-work server families: their tools have separate authority contracts and are not silently mapped onto this control profile.
- Remote Streamable HTTP attachment and embedded MCP Apps rendering. No unauthenticated bridge or public tunnel is created.

Restart remains disabled until the server exposes a complete job inventory and a drain/acknowledgment contract. Profile changes should likewise require a server-side lifecycle contract, not browser-side file editing.

## Hosting and side-by-side use

This Python-backed console must run alongside the NCP host. The previously published static Sites page is **not** connected to it and is not changed by this package. A sandbox loopback URL is not reachable from your own computer without supported forwarding. No working forwarding/mounting capability was available in the build session, so this package is **not claimed to be mounted or browser-verified**.

Once the separate remote-transport repair provides an authenticated reachable MCP endpoint, replace the bridge's stdio session factory with that transport while preserving the same tool contracts and grants. A hosted frontend also needs authenticated backend routing; publishing these assets alone will not provide live operation. Do not bypass the localhost/Origin checks or bind this prototype publicly to achieve remote access.

## Tests

```sh
# Real installed MCP child: discover, validate, create, admit, submit,
# observe completion, retry same attempt, read resource, ping, close.
.venv/bin/python -m unittest discover -s tests -v

# DOM-level tests, not browser/layout verification. Node 24+.
npm ci --ignore-scripts
npm test
```

The Python integration test requires the installed NCP package in the test interpreter. Fixtures are synthetic and temporary. UI unit tests use a fake HTTP layer to verify confirmation, text-safe rendering, and no automatic write retry; they do not establish a live browser-to-server end-to-end result.

## Small, explicit integration boundary

`GET /api/status`, `/api/tools`, `/api/events`, `/api/runs` return `{data: ...}`. `POST /api/calls` accepts `{name, arguments, confirmed}`; `/api/resources` accepts `{uri}`; `/api/diagnostics` accepts `{}`. Errors consistently return `{error: {code, message?}}` with appropriate HTTP status. All API routes require `Authorization: Bearer <token>`.

Only the 14 known control tools are callable, and only if the running server actually advertises them. Eight read-only tools are allowlisted explicitly; six state-changing tools require confirmation. Server annotations are displayed metadata, not permission. Writes are not generically HTTP-idempotent; NCP's own plan digest / run attempt semantics remain authoritative. The event journal is diagnostic metadata, not a scientific evidence store or a durable command ledger.

Source layout: `console.py` (HTTP boundary and MCP actor), `web/` (buildless interface), `tests/` (real-MCP and DOM checks). Runtime data and secrets belong outside source control.
