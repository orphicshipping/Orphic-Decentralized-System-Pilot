# MCP contract

- path: `src/ciw/operator_examples`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

The `ncp_*` gateway lives in this package, including `control_gateway.py` and `domain_surface.py`. A module path is not a grant.

Tracked files here, none of them a tool call: `materials-complex-viscosity.json`, `materials-empirical-rheology.json`, `materials-joint-residual.json`, `materials-population.json`, `materials-thermochemistry.json`, `materials-viscoelasticity.json`, `thermal-source.json`.

This folder does not opt for the easiest task in a set. Name the task.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../../mcp/project-map.json). The [folder index](../../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
