# Visualization providers and retained presentation components

The folder audit at `5bde337174bdfb2add357042aceae2286bb7e56b` distinguishes
shipped clients, reusable components and incomplete projector metadata. A render
record, presentation helper or engine pin does not establish an attached tab.
See the [complete repository audit](REPOSITORY_FOLDER_AUDIT_20261018.md).

## Present source

| Surface | Existing source | Connection and limits |
| --- | --- | --- |
| Godot inspection client | [Main client](../godot/scripts/main.gd), [project](../godot/project.godot) | Connects to the existing Session protocol. Its tabs are Workbench and Oscillator; the latter contains phase and energy displays. |
| Retained geometry components | [Analog state](../godot/scripts/analog_state_view.gd), [analog strip](../godot/scripts/analog_strip.gd), [circle geometry](../godot/scripts/circle_geometry_view.gd) | Reusable components; these files are not attached to the main client. A full Analog or Circle tab is additional integration work. |
| Presentation components | [Shared kit](../godot/scripts/presentation_kit.gd), [strip](../godot/scripts/presentation_strip.gd), [schematic](../godot/scripts/presentation_schematic.gd) | Shared display helpers. The [presentation smoke](../godot/tests/presentation_smoke.gd) checks the existing kit and analog geometry component. |
| Host render writer | [Render helper](../examples/_render_json.py) | Writes retained cards/series/status and forces `may_authorize: false`. Defaults do not establish verification. |
| Bevy retained-record projector | [Metadata and status](../tools/bevy-render-view/README.md) | Cargo metadata pins `giasonpooni/bevy` at `0f38358f573a7dc6ea961076f6151be662142010`, but this checkout has no `src/main.rs`, `src/lib.rs` or explicit Cargo target. Its documented launch is unavailable. |
| Bevy bounded projectile runtime | [Runtime source](../tools/interactive-simulation/bevy/src/main.rs), [integration](../src/ciw/interactive_simulation.py) | Separate optional simulation profile. Its source does not implement the missing retained-record projector. |

The three retained geometry components are eligible for a bounded client
integration after choosing a render contract, binding the existing host outputs,
and testing missing/stale data and detached operation. No new scientific
operation, state owner or engine-to-engine call is needed for presentation.

## Existing emitters and incomplete teaching material

The [analog example](../examples/pyramid-method-gap/run_analog.py),
[circle example](../examples/gte-circle-eligibility/run_or_emit.py) and
[residual teaching emitter](../examples/residual-cusum-teach/emit_render.py)
are present. Generated `results/*_render.json` are local outputs, not shipped
measurements or evidence that a corresponding Godot tab exists.

The [template library](../examples/workflows/usecase_templates.py) names ten
teaching emitters; only the circle and residual emitter targets exist in this
snapshot. The batch emitter also needs the absent `examples/usecase-index.json`.
The corpus generator, 9,074 scenario folders, catalog document and Godot
`usecase_catalog_view.gd` are absent. The
[reported exhaustion count](../examples/usecase-EXHAUSTION.md) and
[historical coverage report](../examples/workflows/coverage_report.md) retain
earlier local claims; they cannot qualify or regenerate this checkout.

Do not regenerate thousands of descriptions to repair a source seam. First
select one existing emitter, preserve its source/operation/result identities,
attach one presentation component and retain an executed journey. Restore any
missing historical source only from a separately reviewed exact source revision.

## Shared status and authority

The kit defines `LIVE`, `STALE`, `UNAVAILABLE`, `REFUSED`, `HELD`, `RECONCILED`,
`SATISFIED`, `VIOLATED`, `REQUEST_EVIDENCE` and `HISTORICAL`.
`HISTORICAL` displays as `retained_runtime_report_requires_fresh_verification`.
The kit paints VERIFIED only when `fresh_verifier_occurrence` is true; a client
does not produce a fresh verifier occurrence by drawing a record.

Engines project host-owned retained values. Meshes do not compute science,
authenticate observations, authorize equipment or admit canonical state.
The [authoring workflow notes](../examples/workflows/README.md) remain design
instructions for optional clients and require the missing sources described above.

