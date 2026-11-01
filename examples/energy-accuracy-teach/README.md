<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/energy-accuracy-teach`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# energy-accuracy-teach (presentation sample)

> Current client: use **Teaching samples** in Godot or the optional Bevy retained-render viewer. Specialty-tab descriptions below are historical authoring notes; see the [current source map](../../docs/VISUALIZATION_PROVIDERS.md).

HOST teaching split of energy-accuracy qualification for the Godot
**Energy accuracy** tab. Reuses existing `ciw.energy-accuracy.v1` language —
**does not mint a new CIW kind**.

## Evidence seam

| Source | Role |
| --- | --- |
| [`examples/energy-accuracy/baseline.json`](../energy-accuracy/baseline.json) | Qualified amortized cost |
| [`examples/energy-accuracy/under-target.json`](../energy-accuracy/under-target.json) | Accuracy target not met |
| [`examples/energy-accuracy/missing.json`](../energy-accuracy/missing.json) | Missing endpoint brackets → null energy |
| [`docs/ENERGY_ACCURACY.md`](../../docs/ENERGY_ACCURACY.md) | Vocabulary: KL nats, joules, qualification |

Marked **HOST_FROM_PUBLISHED_FIXTURES**. Metrics come from
`ciw.energy_records.analyze` on the synthetic fixtures — not a live GPU/NVML
capture.

## Cases

1. **Baseline** — target met, complete brackets → `LIVE` / qualified amortized cost.
2. **Under-target** — KL above target → `REFUSED` / `accuracy_target_not_met_by_every_batch`.
3. **Missing** — incomplete endpoint brackets → `HELD` / `missing_endpoint_brackets`; energy null.

## Caption (always)

Synthetic fixtures; no calibrated uncertainty budget or whole-machine energy
claim. Statistical free energy (nats), physical energy (joules), and elapsed
time remain separate.

## Non-claims

- No calibrated uncertainty budget or whole-machine / wall-plug energy.
- No hardware authentication or physical NVML verification.
- No new energy CIW kind; Godot does not capture or analyze logs.

## Emit render JSON

```sh
python examples/energy-accuracy-teach/emit_render.py
# → examples/energy-accuracy-teach/results/energy_accuracy_render.json  (gitignored)
```

## Godot

```sh
godot --path godot
# Energy accuracy tab loads ../examples/energy-accuracy-teach/results/energy_accuracy_render.json
```

Missing file → **STALE**. `may_authorize` stays false.
