# MCP contract

- path: `tools/private-mcp-host/panel`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

`panel` is inside `tools` (navigation). Operator consoles. They do not add a grant the control tools lack.

Tracked files here, none of them a tool call: `.gitignore`, `README.md`, `package-lock.json`, `package.json`.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../../mcp/project-map.json). The [folder index](../../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
