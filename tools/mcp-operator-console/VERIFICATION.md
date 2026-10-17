# Verification — 2026-10-17

Target NCP source: `cc5d7f31cfeaeb4d897e0864e0fcc560355a0d39`.

The console was developed independently with the baseline NCP checkout unchanged. This submission adds `tools/mcp-operator-console/` and the root ignore-list exceptions needed to track it. It does not modify existing NCP core source, repair branches, release metadata, or hosting configuration.

## Passing checks

- **5 Python tests passed** with Python 3.12.14 and installed NCP wheel, MCP SDK 2.3.0.
- **3 DOM tests passed** with Node 24.19.0 and jsdom 30.1.3.
- JavaScript syntax check passed.

The Python checks launched an actual local HTTP server and its actual stdio NCP child. They verified static asset responses, authentication refusal, real discovery of 14 control tools, a real bound-capability call through HTTP, invalid JSON/type/body-size rejection, and shutdown. A separate real-MCP journey verified schema/confirmation refusals, plan creation, resource reading, admission, synthetic run completion, retained execution IDs, same-attempt retry returning the same run, observed-run inventory, and diagnostic behavior.

The DOM checks verified initial tool selection without execution, resetting confirmation after argument edits, treating server-returned markup as text, and not retrying a write after a network failure. Their HTTP responses were test doubles; they are not browser end-to-end tests.

## Observed server limitation

The pinned server returns MCP `-32601` (method not found) for `ping`, while `tools/list` works. Diagnostics report `ping_ok: null`, `discovery_ok: true`, and an explicit warning. This console does not patch the server or represent unsupported ping as successful.

## Not established

- Browser rendering, accessibility audit, responsive screenshot verification, or embedded-pane behavior.
- A mounted live pane in this chat, a public endpoint, or a connection from the previous hosted static site.
- Remote MCP authentication/transport, lifecycle draining/restarts, or complete server job enumeration.
- Physical instrument qualification, external/private/native provider readiness, release qualification, or passing the repository-wide suite. Separate pre-existing audit work is not console acceptance evidence.

All console test fixtures were synthetic and temporary. Test-owned server processes were stopped after verification. No production profile was restarted or altered.


## Post-consolidation verification

The follow-up [repair record](../../docs/POST_CONSOLIDATION_REPAIR_20261017.md)
records a real Chromium journey through this console, HTTP, the actual MCP host,
explicit plan admission, synthetic execution and retained resource inspection.
`tests/browser_journey.py` is the repeatable browser gate. The console workflow
runs the existing five Python/real-HTTP tests and three DOM tests on Linux and
Windows, plus the browser journey on Linux. Hosted outcomes belong to the
reviewed commit and are not inferred from local results. Remote transport,
complete job enumeration/drain and physical qualification remain separate work.
