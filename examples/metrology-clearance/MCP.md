# MCP contract

- path: `examples/metrology-clearance`
- class: `navigation`
- authorizes_execution: false
- call: `none`

Clearance fixture. This directory does not select the pin. A held inspection is not a physical clearance.

Tracked files here, none of them a tool call: `inspection.json`, `uncertain-inspection.json`.

This folder does not opt for the easiest task in a set. Name the task.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../mcp/project-map.json). The [folder index](../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
