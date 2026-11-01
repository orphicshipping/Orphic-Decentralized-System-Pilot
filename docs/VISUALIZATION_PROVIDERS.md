# Visualization providers and retained presentation components

The folder audit at `5bde337174bdfb2add357042aceae2286bb7e56b` distinguishes
shipped clients, reusable components and incomplete projector metadata. The
[implementation follow-up](INTEGRATION_COMPLETION_20261018.md) records the new
projector, ten-family catalog and their executed gates. A render
record, presentation helper or engine pin does not establish an attached tab.
See the [complete repository audit](REPOSITORY_FOLDER_AUDIT_20261018.md).

## Present source

| Surface | Existing source | Connection and limits |
| --- | --- | --- |
| Godot inspection client | [Main client](../godot/scripts/main.gd), [project](../godot/project.godot) | Workbench and Oscillator use the existing Session protocol. Teaching samples projects the retained catalog/JSON offline and supplies no execution authority. |
| Retained geometry components | [Analog state](../godot/scripts/analog_state_view.gd), [analog strip](../godot/scripts/analog_strip.gd), [circle geometry](../godot/scripts/circle_geometry_view.gd) | Reusable components; these files are not attached to the main client. A full Analog or Circle tab is additional integration work. |
| Presentation components | [Shared kit](../godot/scripts/presentation_kit.gd), [strip](../godot/scripts/presentation_strip.gd), [schematic](../godot/scripts/presentation_schematic.gd) | Shared display helpers. The [presentation smoke](../godot/tests/presentation_smoke.gd) checks the existing kit and analog geometry component. |
| Host render writer | [Render helper](../examples/_render_json.py) | Writes retained cards/series/status and forces `may_authorize: false`. Defaults do not establish verification. |
| Bevy retained-record projector | [Application and commands](../tools/bevy-render-view/README.md) | Rust application over the existing host render JSON, with a window and `--summary`. The unchanged engine revision `0f38358f573a7dc6ea961076f6151be662142010` is fetched from `bevyengine/bevy`; Cargo.lock and Rust 1.97.1 retain its build closure. |
| Bevy bounded projectile runtime | [Runtime source](../tools/interactive-simulation/bevy/src/main.rs), [integration](../src/ciw/interactive_simulation.py) | Separate optional simulation profile with its own execution contract. |

The three retained geometry components are eligible for a bounded client
integration after choosing a render contract, binding the existing host outputs,
and testing missing/stale data and detached operation. No new scientific
operation, state owner or engine-to-engine call is needed for presentation.

## Input-backed teaching material

The [analog example](../examples/pyramid-method-gap/run_analog.py),
[circle example](../examples/gte-circle-eligibility/run_or_emit.py) and
[residual teaching emitter](../examples/residual-cusum-teach/emit_render.py)
are present. Generated `results/*_render.json` are local outputs, not shipped
measurements or evidence that a corresponding Godot tab exists.

The [template library](../examples/workflows/usecase_templates.py) now resolves
all ten owned emitters. Reviewed [inputs](../examples/workflows/teaching-inputs.json)
produce the [ten-entry catalog](../examples/usecase-index.json) using the
[generator](../examples/workflows/generate_usecase_corpus.py). The batch exporter
validates every entry before writing and can retain exact output hashes.
The [Godot catalog](../godot/scripts/usecase_catalog_view.gd) reads the same render
records that the Bevy projector accepts. These are teaching projections, not
ten newly qualified scientific providers. The
[reported exhaustion count](../examples/usecase-EXHAUSTION.md) and
[historical coverage report](../examples/workflows/coverage_report.md) retain
earlier local claims. The original 9,074 industry/verb inputs and scenario corpus
remain unrecovered; the ten-entry catalog does not claim to restore them.

Do not regenerate thousands of descriptions to repair a source seam. First
select one existing emitter, preserve its source/operation/result identities,
attach one presentation component and retain an executed journey. Restore any
missing historical source only from a separately reviewed exact source revision.

## Shared status and authority

The [retained render contract v1](RETAINED_RENDER_CONTRACT.md) freezes the
existing writer fields and documents supported display subsets. Use the
existing catalog exporter as the primary host entry point: select a scenario,
emit it, and optionally launch Godot for card inspection or Bevy for path review.
Godot's explicit retained-render mode runs the same teaching view without
connecting a Session client. Ordinary Workbench/Oscillator functionality and
the separately registered Bevy projectile computation remain available.

The kit defines `LIVE`, `STALE`, `UNAVAILABLE`, `REFUSED`, `HELD`, `RECONCILED`,
`SATISFIED`, `VIOLATED`, `REQUEST_EVIDENCE` and `HISTORICAL`.
`HISTORICAL` displays as `retained_runtime_report_requires_fresh_verification`.
The kit paints VERIFIED only when `fresh_verifier_occurrence` is the JSON boolean
`true`, after normalizing status whitespace/case; a client
does not produce a fresh verifier occurrence by drawing a record.

Engines project host-owned retained values. Meshes do not compute science,
authenticate observations, authorize equipment or admit canonical state.
The [authoring workflow notes](../examples/workflows/README.md) distinguish the
working catalog/emitters from specialty tabs that remain separate integration work.

