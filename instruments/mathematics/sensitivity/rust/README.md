<!-- ncp-folder-entry -->
# MCP entry

- path: `instruments/mathematics/sensitivity/rust`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `sensitivity-gate`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# sensitivity-gate

The `sensitivity-gate/` directory contains an incomplete Rust crate scaffold.
It includes numerical constants and typed error definitions, but the modules
`chart`, `covariance`, `matrix`, and `structure` declared by `src/lib.rs` are
absent from the committed source. The crate therefore does not currently build.

There are no Python bindings, C ABI, Julia bindings, or CUDA backend in this
repository. The supported implementation is the Python `sensitivity` package;
see [the kernel documentation](../docs/KERNEL.md) for its numerical constraints.
