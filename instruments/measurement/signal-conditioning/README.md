<!-- ncp-folder-entry -->
# MCP entry

- path: `instruments/measurement/signal-conditioning`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `stfe_dsp`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# STFE signal-conditioning extension

This MPL-2.0 package adds bounded numerical DSP and declared pipeline composition
to the preserved STFE window-mean instrument. The original imported package at
`../signal-processing` remains byte-identical to its source snapshot. Its
covariance and causal-window contracts remain independent of these general DSP
operations.

From the monorepo root:

```sh
python -m pip install . ./instruments/measurement/signal-processing './instruments/measurement/signal-conditioning[dsp]'
net dsp pipeline demo --output-dir results/pump-001
net dsp pipeline replay results/pump-001 --output-dir results/pump-001-replay
```

See [DSP_PIPELINE.md](../../../docs/DSP_PIPELINE.md) for operation contracts,
qualification, identity boundaries and limitations. Source identity checks and
numerical replay do not establish physical leak detection or admit state.
