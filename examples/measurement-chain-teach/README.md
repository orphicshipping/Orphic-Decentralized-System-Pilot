<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/measurement-chain-teach`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# measurement-chain-teach (presentation sample)

> Current client: use **Teaching samples** in Godot or the optional Bevy retained-render viewer. Specialty-tab descriptions below are historical authoring notes; see the [current source map](../../docs/VISUALIZATION_PROVIDERS.md).

HOST teaching of measurement-chain / RCI–FSRT–JSPT stage language for the Godot
**Measurement chain** tab. Prefer existing ops language from
[`examples/measurement-chain/`](../measurement-chain/) and
[`docs/RECONCILIATION.md`](../../docs/RECONCILIATION.md) — **does not mint a new
CIW kind** and **does not run a second FSRT solver**.

## Evidence seam

| Source | Role |
| --- | --- |
| [`examples/measurement-chain/source.json`](../measurement-chain/source.json) | Two-reservoir measurement-chain-testbed assembly |
| [`examples/measurement-chain/README.md`](../measurement-chain/README.md) | Scope: no GSIE fusion / physical traceability |
| `docs/RECONCILIATION.md` | Vocabulary: raw · calibrated · estimate · reconcile |

Marked **HOST_SYNTHETIC**. Schematic node levels are presentation only — not
solver masses or physical calibration certificates.

## Stages

1. **Raw** — retained simultaneous records → `LIVE`.
2. **Calibrated** — `rci.calibrate.v2` language → `LIVE`.
3. **Estimate** — FSRT snapshot / `not_verified` → `HISTORICAL`.
4. **Reconcile** — FSRT reconcile + JSPT propagation → `RECONCILED`.

## Caption (always)

HOST_SYNTHETIC measurement-chain-testbed. Distinct identities; native
`not_verified`; no GSIE fusion or physical traceability. Viewport does not run
RCI/FSRT/JSPT.

## Non-claims

- No physical calibration certificate or device data.
- No GSIE fusion state or physical traceability.
- No second FSRT solver; Jacobian remains caller-declared.
- No state admission.

## Emit render JSON

```sh
python examples/measurement-chain-teach/emit_render.py
# → examples/measurement-chain-teach/results/measurement_chain_render.json  (gitignored)
```

## Godot

```sh
godot --path godot
# Measurement chain tab loads ../examples/measurement-chain-teach/results/measurement_chain_render.json
```

Missing file → **STALE**. `may_authorize` stays false.
