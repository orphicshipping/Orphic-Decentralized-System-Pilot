# MCP project map

This page is how a model walks the repository. It adds no solver. Reading it is not a grant,
not a test run, and not a qualification.

Machine copy: [mcp/project-map.json](../mcp/project-map.json).
Call contract: [MCP_MODEL.md](MCP_MODEL.md).
Tool index: [mcp/model-catalog.json](../mcp/model-catalog.json).

## Call order

1. `ncp_index_list` pages every indexed id and every release-ledger id. `callable` means `mcp_profile` or `mcp_tool` is set. `authorizes_execution` stays false.
2. `ncp_capability_describe` reads one of those ids. It does not grant it on the selected profile.
3. `ncp_capabilities_list` is what the selected profile will actually run.
4. A granted graph is `ncp_plan_template`, `ncp_plan_create`, `ncp_plan_admit`, `ncp_run_submit`, `ncp_run_get`.
5. Direct calls skip that graph. `ncp_domain_list` then `ncp_domain_run` is the provider-free synthetic set. `ncp_fluid_field` is wave or SPH. `ncp_ledger_read` reads a ledger row.

The same attempt returns the same run. A different request under that attempt refuses. Replay is a new occurrence. Missing fields stay missing.

## Folders

Do not move these trees to “make them MCP.” Imports, pins, and tests use the paths below.

| Folder | What a model should do with it |
| --- | --- |
| `src/` | Implementation. `ciw.control_gateway` is the `ncp_*` surface. A module path is not permission to run it. |
| `mcp/` | Machine indexes. `model-catalog.json` names tools. `project-map.json` names folders. Neither authorizes execution. |
| `docs/` | Contracts, workflows, and mandates. The page that owns a claim is the one linked from [the docs index](README.md). |
| `tests/` | Verification. Keep it. Do not delete or weaken a test so a call looks successful. A handler test is not physical qualification. |
| `examples/` | Frozen inputs for profile preparation and synthetic examples. Not measurements. |
| `release/` | Capability ledger. Read with `ncp_ledger_read`. `qualified` stays `not_assessed`. |
| `instruments/` | Vendored provider trees. Not a substitute for a host-selected pin. Leakage and BIM refuse this directory. |
| `runtimes/` | Optional Julia, native, and proof workers. Unbound until a host selects the real executable. |
| `godot/` | Godot project. Not a grant. |
| `validation/` | Retained audits, including the 2026-10-18 folder inventory. Historical evidence, not a new result and not a waiver. |
| `qualification/` | Local check records. Not a release. |
| `scripts/` | Operator and check scripts. Not model authority. |
| `tools/` | Operator consoles. They do not add a grant. |
| `deploy/`, `containers/`, `delivery/` | Packaging and retained delivery. A built image is not a qualified installation. |
| `experiments/` | Research scripts. Not profile grants. |
| `tasks/` | Work orders. Not execution. |
| `requirements/`, `third_party/` | Locks and notices. Not scientific authority. |

Root files that matter first: [README.md](../README.md), [AGENTS.md](../AGENTS.md), [CONTRIBUTING.md](../CONTRIBUTING.md). [TECHNICAL_REFERENCE.md](../TECHNICAL_REFERENCE.md) keeps older material. Later guides own later claims.

## What is operational

Plan profiles: builtin, numerical, analysis, polymer, temperature, measured thermal, link scalar, leakage, BIM clearance, system, legibility, data, fluid, fluid-molecular, fluid-fsi.

Direct tools: doctor, demo, linear preview, lesson, hypergraph, polymer cycle, toolpoint, wave/SPH field, domain list, domain run, ledger read.

`ncp_domain_run` covers dry and moist atmosphere columns, twelve weather profiles, impact contact, plate, and crush, irrigation, sixteen geomatics references, five thermofluids balances, a Markov sequence, and thermal identification. The ids live in `src/ciw/domain_surface.py` and are indexed. A LOCAL or PASS check is not a measurement, a forecast, or a calibration. The plate field and geomatics outputs are not retained.

Leakage and BIM run only when the host selected the retained pin named in [MCP_MODEL.md](MCP_MODEL.md). A completed balance is not a leak finding. A completed inspection can stay held and is not a physical clearance.

## What stays unbound

- SP1. `proved_heat` is a ledger read, not a proof.
- Julia, Godot, Bevy, and Blender.
- Session workflows whose catalog row says `explicit_bindings_required`, until the host has selected that pin. `ncp_workflows_list` is navigation.
- Shared discovery, typed composition, frame transformations, the device gateway, and private provider discovery. They are named as not indexed.
- External work, board refit, and knowledge. Separate hosts. Not extra arguments on `ncp_run_submit`.

## Mandates the tests protect

- Advertised, bound, and granted stay different.
- A completed graph, a PASS, a spread, or a view does not book freight, verify physics, or release a part.
- Pins are not overridden. The vendored copy is not the leakage or BIM checkout.
- Synthetic solvers stay labeled synthetic.
- Verification workflows stay in `tests/`. The MCP contract tests are `tests/test_control_gateway.py`, `tests/test_capabilities.py`, and `tests/test_mcp_project_map.py`. Domain workflows keep their own suites under `tests/`. This page does not run them.

Scientific workflow pages remain the detailed record: [atmospheric engine](ATMOSPHERIC_ENGINE.md), [weather](WEATHER_SCIENCE.md), [impact](IMPACT_CONTACT_BENCHMARK.md), [irrigation](IRRIGATION.md), [geomatics](GEOMATICS.md), [thermofluids](THERMOFLUIDS.md), [fluids](FLUID_DYNAMICS.md), [leakage](leakage.md), [BIM clearance](METROLOGY_CLEARANCE.md), [proved heat](PROVED_HEAT.md). Those pages do not become grants by being linked here.
