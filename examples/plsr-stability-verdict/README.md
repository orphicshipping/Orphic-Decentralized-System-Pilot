<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/plsr-stability-verdict`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# plsr-stability-verdict (presentation sample)

> Current client: use **Teaching samples** in Godot or the optional Bevy retained-render viewer. Specialty-tab descriptions below are historical authoring notes; see the [current source map](../../docs/VISUALIZATION_PROVIDERS.md).

HOST teaching of identified-stability / PLSR verdicts for the Godot
**Stability verdict** tab. Reuses existing `ciw.identified-stability.v1` —
**does not mint a new CIW kind**.

## Evidence seam

| Source | Role |
| --- | --- |
| [`examples/identified-stability/README.md`](../identified-stability/README.md) | Documented outcomes: identity certificate → `NUMERICAL_INCONCLUSIVE`; `level=0` → `OUTSIDE_LEVEL_SET` |
| PLSR commit `19ea6967060166ba09db6cd4563bd87bd6b3d196` | Pin cited in the identified-stability README |

Marked **HOST_FROM_DOCUMENTED_OUTCOMES** / **HOST_ANALOG**. Not a live PLSR
evaluation.

## Cases

1. **Identity certificate** — neutral conservation mode → `NUMERICAL_INCONCLUSIVE` / `HISTORICAL`.
2. **level=0** — nonzero retained state → `OUTSIDE_LEVEL_SET` / `REFUSED`.

Both retain `proof_status: NOT_CHECKED`.

## Caption (always)

Does not certify the requested inequality; parameter covariance unknown; not
physical equilibrium.

## Non-claims

- Does not certify the requested inequality.
- Parameter covariance remains unknown.
- Not physical equilibrium; no fresh proof; no state admission.

## Emit render JSON

```sh
python examples/plsr-stability-verdict/emit_render.py
# → examples/plsr-stability-verdict/results/plsr_stability_render.json  (gitignored)
```

## Godot

```sh
godot --path godot
# Stability verdict tab loads ../examples/plsr-stability-verdict/results/plsr_stability_render.json
```

Missing file → **STALE**. Quadratic contour is presentation only.
