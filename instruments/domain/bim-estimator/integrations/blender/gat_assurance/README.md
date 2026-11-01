<!-- ncp-folder-entry -->
# MCP entry

- path: `instruments/domain/bim-estimator/integrations/blender/gat_assurance`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# GAT Evidence and Assurance for Blender/Bonsai

This extension is the read-only presentation edge of the GAT workflow.
It loads an `acceptance` or validated `beam_assurance` response produced by
`gat-headless`, shows the case disposition, evidence request or capacity
change, method/oracle identity, and colors matching Blender or Bonsai objects.

It does not edit IFC data, execute transformations, approve field work, or
recompute probabilities inside Blender. Objects are matched by name or by a
`gat_entity_name` custom property.

Build with Blender 4.2 or newer:

```text
blender --command extension build --source-dir integrations/blender/gat_assurance
```

Install the resulting ZIP through **Preferences > Get Extensions > Install
from Disk**. The extension requests only local file access.
