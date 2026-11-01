# Notations Control Protocol

One MCP for models and for other software. Call a tool. A folder, a file, a line of code, a shell command, or a PASS is not a run.

Developed by **Notation Systems Inc.**

## Bind

Other software loads [mcp/plug-in.json](mcp/plug-in.json) or calls `ncp_plug_in`. That object does not authorize execution. This repository is not a human console.

Transport is MCP over stdio. Protocol versions are `2025-11-25` and `2025-06-18`. Methods are `initialize`, `ping`, `tools/list`, and `tools/call`.

```text
python -m ciw.net control serve --profile <abs-profile.json> --instrument <instrument>
```

The host launch selects the profile and the instrument. A tool argument cannot select a checkout, an interpreter, or a pin.

## Call

1. `ncp_plug_in` returns the bind contract.
2. `ncp_index_list` pages every indexed id. A row is callable only when it names `mcp_profile` or `mcp_tool`. `authorizes_execution` stays false.
3. `ncp_workbench_list`, then `ncp_workbench_run` with one workflow name. The workbench does not opt for the easiest task in a set. Five workflows run from retained fixtures. The rest stay documentation and evidence until the host selects that binding.
4. `ncp_domain_list`, then `ncp_domain_run`. Weather, impact, geomatics, and thermofluids refuse until the caller names the task.
5. `ncp_fluid_field` is wave or SPH. The field is not retained.
6. A granted plan is `ncp_capabilities_list`, `ncp_plan_template`, `ncp_plan_create`, `ncp_plan_admit`, `ncp_run_submit`, `ncp_run_get`.

The tool index is [mcp/model-catalog.json](mcp/model-catalog.json). Arguments are [docs/MCP_MODEL.md](docs/MCP_MODEL.md). Folders are [mcp/folder-index.json](mcp/folder-index.json) and [docs/MCP_PROJECT.md](docs/MCP_PROJECT.md). None of those files authorizes execution.

## What a line of code is

Source paths stay where the tests and the pins already import them. A module, a folder, and a line of code are not tools. Every tracked folder carries `MCP.md` and a rendered entry that names the call to use instead of the files. Rewriting each solver into a new function would break those imports and the retained evidence. The retrofit is this plug-in, not a second copy of the science.

## What is not a grant

`proved_heat` is `ncp_ledger_read`, not an SP1 proof. Julia, Godot, Bevy, and Blender stay unbound. Leakage and BIM stay on the host-selected pins. The vendored trees under `instruments/` are not those pins.

The same attempt is the same run. A different request under that attempt refuses. Replay is a new occurrence. Missing fields stay missing.

A completed graph, a PASS, a LOCAL check, or a held inspection is not a measurement, a booking, or a release. Tests under `tests/` stay. Do not drop a verification workflow to widen a grant.

## Retained human material

Shell install, `net start`, and the operator manual are not this product. They remain at [docs/OPERATOR_REFERENCE.md](docs/OPERATOR_REFERENCE.md) so that record is not deleted. They add no tool and no grant.
