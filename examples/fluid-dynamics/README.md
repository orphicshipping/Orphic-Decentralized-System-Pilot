<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/fluid-dynamics`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_fluid_field`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Synthetic fluid dynamics examples

These requests exercise two separately qualified continuum models. `reservoir`
uses two hydrostatic reservoir heads, connector inertance/resistance and a
preloaded linear piston. `wave` uses periodic linear shallow-water surface
gravity waves with a depth-averaged horizontal velocity. Neither profile is a
molecular, SPH, acoustic, turbulent, experimental or general-purpose CFD model.

```bash
net fluid run --request examples/fluid-dynamics/reservoir-request.json --output-dir /tmp/reservoir-run
net fluid inspect /tmp/reservoir-run
net fluid verify /tmp/reservoir-run --output /tmp/reservoir-audit.json
net fluid export-csv /tmp/reservoir-run --output /tmp/reservoir-trace.csv
net fluid handoff /tmp/reservoir-run --sample-index 1 --output /tmp/reservoir-snapshot.json

net fluid run --request examples/fluid-dynamics/wave-request.json --output-dir /tmp/wave-run
net fluid verify /tmp/wave-run --output /tmp/wave-audit.json
net fluid export-csv /tmp/wave-run --output /tmp/wave-trace.csv
```

Output directories and exported files are create-only. Inspection checks stored
contracts without numerical replay. Verification and export perform fresh
independent verification occurrences. Export requires `LOCAL`; `EXPAND` and
`REFUSE` block export. The original bundle remains unchanged by these reads.

Reservoir CSV uses the finest retained time grid. Wave CSV uses the primary
time/space grid; cell and face coordinates are separate because the fields are
staggered. The snapshot handoff exports one simulated reservoir state with no
measured uncertainty or estimator execution. No operation admits canonical state.
