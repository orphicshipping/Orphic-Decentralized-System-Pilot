<!-- ncp-folder-entry -->
# MCP entry

- path: `instruments/domain/bim-estimator/rust/sp1_guest`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `src`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# SP1 guest shape (v0)

This crate is the program a zkVM guest would run. Today it runs on the
host. `sp1_zkvm` remains `allowed: false`.

```bash
cargo test --manifest-path rust/Cargo.toml -p cse-fixedpoint-kernel
cargo run --manifest-path rust/Cargo.toml -p cse-sp1-guest -- chain_jvp
cargo run --manifest-path rust/Cargo.toml -p cse-sp1-guest -- lyapunov
```

When the gate opens, replace this `main` with SP1's `sp1_zkvm::io` read/
commit of the same i32 values and measure cycles with `cargo prove`.
Do not add that job to default CI.
