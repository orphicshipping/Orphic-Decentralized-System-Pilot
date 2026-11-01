<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/hoomd`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Linear-chain particle example

`linear-chains.json` contains a reproducible, native-GSD initial state for two
four-bead WCA/harmonic chains, an explicit CPU runtime/version and seed, reduced
unit scales, and separate short equilibration/production schedules. It is a
generic unqualified model specimen, not a calibrated commercial polymer.

See [the operating guide](../../docs/HOOMD_PARTICLES.md) for the official runtime,
`net hoomd run`, retained datasets, offline inspection, independent mathematical
checks and the no-skip installed-wheel qualification command.
