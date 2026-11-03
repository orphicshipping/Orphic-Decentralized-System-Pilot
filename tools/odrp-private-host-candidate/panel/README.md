<!-- ncp-folder-entry -->
# MCP entry

- path: `tools/odrp-private-host-candidate/panel`
- class: `navigation`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `scripts`, `tests`, `web`, `worker`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Private operator-panel candidate

The existing console composition uses the retained [Orphic design kit](DESIGN.md)
and an authenticated server proxy to the existing private gateway. The browser
receives no backend credentials and cannot select grants or clear a hold.
This source is separate from the paused historical portal.

See the [setup, identity and operations contract](../../../docs/PRIVATE_OPERATOR_PANEL.md).
Build a clean committed tree with `node scripts/build.mjs /absolute/new-output`
from this directory. The build writes exact-source modules and a receipt; it
does not install dependencies, publish, configure Access or deploy.

`tests/build.test.mjs` and `tests/proxy.test.mjs` use Node's test runner.
`tests/ui.test.cjs` uses preprovisioned Playwright and Chromium with controlled
responses. `tests/browser_journey.py` connects Chromium through the actual proxy,
gateway and Worker to an installed adapter/MCP process. Its local SQLite/fsync
storage and signed synthetic identities do not qualify hosted services. Keep
the exact command, source identities, screenshots and failures with each run.
