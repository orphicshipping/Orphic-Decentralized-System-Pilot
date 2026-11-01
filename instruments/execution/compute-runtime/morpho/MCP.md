# MCP contract

- path: `instruments/execution/compute-runtime/morpho`
- class: `unbound`
- authorizes_execution: false
- call: `none`

`morpho` is inside `instruments` (unbound). Imported subtrees. Not a substitute for a host-selected pin. Leakage and BIM refuse the vendored directory.

Tracked files here, none of them a tool call: `__init__.py`, `ast.py`, `compiler.py`, `identity.py`, `ir.py`, `lexer.py`, `parser.py`, `provenance.py`.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../../../mcp/project-map.json). The [folder index](../../../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
