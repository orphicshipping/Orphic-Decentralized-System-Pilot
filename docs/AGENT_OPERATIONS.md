# Agent operations

This page is how a model uses the system without a human screen. Reading it does not run a solver and does not authorize execution.

## Discover

Call `ncp_plug_in` or import `ciw.services`. Then `ncp_index_list`. A row is callable only when `mcp_profile` or `mcp_tool` is set. `authorizes_execution` stays false on every row.

`ncp_workbench_list` names every session workflow, the document that owns it, and whether it needs a host binding. `ncp_domain_list` names the synthetic solvers. Neither list picks a task.

## Compose

Pass the object a tool's schema requires. Do not add a checkout, an interpreter, or a pin. The host launch already selected those. A set of names does not opt for the easiest one: name `workflow` or `name` yourself.

A granted plan is `ncp_plan_template`, then `ncp_plan_create` with that graph, then `ncp_plan_admit` of that digest, then `ncp_run_submit` with an attempt. The same attempt returns the same run. A different request under that attempt refuses.

## Execute

Use `tools/call` or `ciw.services.invoke` with the same operation name. They call the same domain functions. An unknown name refuses. There is no shell tool.

`python -m ciw.folded_stdio` speaks MCP `2025-11-25` over stdin. `examples/consumers/` has the MCP client, the SDK program, one agent, and two workers. Run those with the installed package from a directory that is not the checkout.

`ncp_specialist_list` and `ncp_specialist_describe` name the knowledge, HOOMD, and operational hosts. `ncp_specialist_run` refuses. Their grants are not shared, and this call does not run HOOMD or open a corpus.

`ncp_logistics_list` names the ODSP logistics segment. `ncp_logistics_route` says whether an intent is a retained read, a computation that stays on the logistics host, or refused. `ncp_logistics_call` reads only when the process already has `NCP_LOGISTICS_ENDPOINT`. A tool argument cannot set that endpoint. Dispatch, booking and package delivery refuse. A retained inspection is not a shipment and not a release.

`ncp_workbench_run` uses the operator manifest when the workflow is not one of the five unbound fixtures. The manifest is process configuration (`NCP_HOST_MANIFEST` or `--host-manifest`). Do not put a checkout, a pin, or an executable in the tool arguments. A mismatch fails closed. The returned `artifact_id` is read with `ncp_retained_read` and removed with `ncp_retained_release`.


## Verify

Read the returned authority and `physical_qualification`. A PASS, a LOCAL check, a retained digest, or a completed graph is not a measurement, a booking, or a release. `proved_heat` is `ncp_ledger_read`, not an SP1 proof. Julia, Godot, Bevy, and Blender stay unbound. Leakage and BIM stay on the host-selected pins.

If the call refuses, leave the field missing. Do not retry with a weaker argument to force a success.
