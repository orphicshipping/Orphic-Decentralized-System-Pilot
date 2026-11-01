# MCP contract

- path: `runtimes/julia-oscillator-kernel`
- class: `unbound`
- authorizes_execution: false
- call: `none`

Julia kernel. Unbound until a host selects the real executable. Do not invent one.

Tracked files here, none of them a tool call: `abi.c.in`, `cpp_probe.cpp`, `export.jl`, `oscillator.jl`, `oscillator_abi.h`, `oscillator_cpp.hpp`, `rust_binding.rs`.

A file in this folder is not a tool call. Use `ncp_index_list` and the [project map](../../mcp/project-map.json). The [folder index](../../mcp/folder-index.json) lists every tracked folder. Reading this contract does not run a solver, bind a provider, or qualify a result. A PASS, a LOCAL check, or a completed graph is not a physical release.
