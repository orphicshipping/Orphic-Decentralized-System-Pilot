<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/acquired-window`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Acquired calibrated windows

`make_source.py` builds synthetic acquisition snapshots and explicit mappings
from native PPDA observation, record and document identities. It derives values
only from retained snapshot rows. The complete source snapshot bytes survive.

The mapping source selects each row in its first acquisition snapshot and
declares the clock map, affine calibration, quantity frame and full joint
covariance. `unknown` covariance or mismatched metadata refuses execution.
Raw device times and acquisition cursor order are never rewritten as event time.

Each mapping executes the existing TBRT → MCUR → STFE → GSIE workflow. Its
unchanged child bundle retains SET's numerical replay receipt; the outer receipt
checks mapping and child identities and does not claim independent verification.
Replay preserves the exact selected acquisition occurrence.

Repeated windows declare their own independent reference priors. They do not
silently consume the previous posterior or establish cross-window independence.
