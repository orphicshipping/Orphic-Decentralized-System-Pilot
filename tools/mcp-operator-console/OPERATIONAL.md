# Operational engineering profile

The console now supports the operator-selected `--surface operational` alongside the original default `control` surface. The original control catalog, confirmation requirements and client behavior remain unchanged.

Install the candidate NCP wheel and the pinned console requirements in the selected interpreter. Prepare an operational admission and initialize it once; then launch:

```sh
python console.py --python /absolute/installed/python --profile /absolute/profile-GENERATION.json --surface operational --resume --state-dir /absolute/console-state --token-file /absolute/console.token
```

The browser discovers the real MCP catalog and exposes only the explicitly allowlisted operations for that surface. Read inspection uses the original retained records. Derivation, numerical work, proposals, annotations, alarm review and supervisory command preparation/dispatch require explicit confirmation in the console and their separate server grants. The console does not expose command approval, grant editing, collector startup, arbitrary paths/imports, shell access or equipment protection overrides.

Use `oe_catalog`, `oe_query`, `oe_change`, `net_execute`, `oe_model_support`, `oe_propose` and `oe_inspect` for the connected investigation. Resources use `ncp://investigations/{digest}` and `ncp://operational-records/{digest}`. `oe_search` retrieves structured investigation records. See [the complete operator guide](../../docs/OPERATIONAL_ENGINEERING.md) for exact methods, collector lifecycle, installation, approval and recovery.

The observed-runs panel uses native operational attempt identities and statuses from `net_capabilities` for this surface, keeping returned execution/result identities in the original responses. It does not invent control-run or plan identities. Collector and supervisory-command inventories remain separately inspectable. Transport timeouts retain unknown outcomes, with no automatic write retry.

`check_operational_browser.cjs` exercises actual Chromium → this HTTP console → real MCP SDK → installed NCP, including explicit write confirmation, configuration comparison, numerical assessment, proposed next tests, same-record resource reads and packaged offline-view selection. Synthetic browser results and screenshots do not qualify physical equipment or a remote deployment.
