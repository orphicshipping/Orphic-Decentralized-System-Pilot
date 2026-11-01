# MCP contract

- path: `src/ciw/viewer_assets`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

The `ncp_*` gateway lives in this package, including `control_gateway.py` and `domain_surface.py`. A module path is not a grant.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../../mcp/project-map.json). The [folder index](../../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
