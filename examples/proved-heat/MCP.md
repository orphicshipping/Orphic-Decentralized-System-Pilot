# MCP contract

- path: `examples/proved-heat`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_ledger_read`

Fixture for the registered heat profile. `proved_heat` is a ledger read, not an SP1 proof.

Tracked files here, none of them a tool call: `README.md`, `emit_proof_render.py`, `run.py`, `source.json`.

This folder does not opt for the easiest task in a set. Name the task.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../mcp/project-map.json). The [folder index](../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
