# MCP contract

- path: `instruments/domain/flowstate/data`
- class: `unbound`
- authorizes_execution: false
- call: `none`

Vendored FlowState tree. Not the leakage pin. Do not point an adapter here. Leakage runs only when the host selected the retained checkout.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../../../mcp/project-map.json). The [folder index](../../../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
