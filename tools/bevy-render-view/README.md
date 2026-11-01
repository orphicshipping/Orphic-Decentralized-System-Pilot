<!-- ncp-folder-entry -->
# MCP entry

- path: `tools/bevy-render-view`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `.cargo`, `src`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# bevy-render-view

Thin **Bevy** projector over the same host `*_render.json` records that Godot tabs read.

**Source gap — audited 18 October 2026:** this folder contains metadata and
instructions, but no Rust application target (`src/main.rs`, `src/lib.rs` or an
explicit target in `Cargo.toml`). The commands below describe the intended
interface and cannot run from this snapshot. The separate projectile runtime
in `tools/interactive-simulation/bevy` does not supply this projector. Completing
the source and qualifying its pinned engine/toolchain is a future integration
candidate; no source or engine pin has been substituted.

| Item | Value |
| --- | --- |
| Bevy pin | `0f38358f573a7dc6ea961076f6151be662142010` (`bevyengine/bevy`, `0.20.0-dev`) |
| Engine source | Exact upstream Git revision; engine code is not vendored or patched here |
| Role | Second stacked presentation provider |
| Schema | **Same** render JSON as Godot — no second schema |
| Science | **None** — cards / trajectories / status only |
| CIW kind | **None** for the viewport |
| Cross-calls | Godot ↛ Bevy and Bevy ↛ Godot |

```sh
# Window: night clear, path gizmos, card HUD
cargo run --manifest-path tools/bevy-render-view/Cargo.toml -- \
  examples/pyramid-method-gap/results/analog_render.json

# Stdout only
cargo run --manifest-path tools/bevy-render-view/Cargo.toml -- --summary \
  examples/pyramid-method-gap/results/analog_render.json
```

Missing file → `STALE`. Paint **VERIFIED** only when JSON has `fresh_verifier_occurrence: true`.
`HISTORICAL` is retained as `retained_runtime_report_requires_fresh_verification`
in the summary and displayed as a readable historical/fresh-verification label
in the window. The viewer fits supplied path points for display only.

`--frames N` exits the actual window after N frames for native smoke checks.
Malformed/oversized JSON and invalid path coordinates refuse with exit code 2.
Missing inputs produce a `STALE` presentation. The 16 MiB input, 100,000-point,
64-trajectory and 256-card bounds do not qualify a numerical workload.

See [`docs/VISUALIZATION_PROVIDERS.md`](../../docs/VISUALIZATION_PROVIDERS.md).

## Toolchain

The engine declares Rust 1.96.0; the retained Cargo.lock includes dependencies
requiring **Rust 1.97.1**, selected in rust-toolchain.toml. Use `cargo +1.97.1`
from outside this directory and `--locked` for repeatable dependency selection.

```sh
cargo +1.97.1 test --locked --manifest-path tools/bevy-render-view/Cargo.toml --no-default-features
cargo +1.97.1 build --locked --manifest-path tools/bevy-render-view/Cargo.toml
```

The summary/tests can run without window support using `--no-default-features`.
The default build supplies the actual X11/desktop viewer. The
[execution receipts](../../docs/INTEGRATION_COMPLETION_20261018.md) identify the
tested software-rendering platform; hosted/platform qualification stays separate.
