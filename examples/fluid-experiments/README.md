<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/fluid-experiments`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_fluid_field`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Synthetic observation fixture

These values are declared synthetic observations for exercising ingestion and
comparison. They are not measurements and establish no experimental support.
Run the default reservoir request, then use:

```sh
net fluid experiment ingest --csv examples/fluid-experiments/synthetic-observations.csv --metadata examples/fluid-experiments/synthetic-metadata.json --output-dir observations
net fluid experiment compare --model-dir reservoir-run --measurement-dir observations --output-dir comparison
net fluid experiment verify comparison --output fresh-audit.json
```

The example uncertainty, calibration references, and clock are synthetic operator
declarations. Edit a generated template for an actual held-out dataset; retain
its exact SI units, baseline, split, covariance, content hashes, and provenance.
See [the experiment contract](../../docs/FLUID_EXPERIMENTS.md).
