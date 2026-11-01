<!-- ncp-folder-entry -->
# MCP entry

- path: `instruments`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `composition`, `domain`, `execution`, `inference`, `mathematics`, `measurement`, `representation`, `resources`, `verification`, `views`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Engineering instruments

These 21 public modules retain their original package paths beneath each import
directory, licences, tests and release identities. Python packages, native
release builders and the two npm applications keep separate build boundaries.

[`manifest.json`](manifest.json) owns exact repository, source-tree and historical
execution identities. The [superrepo guide](../docs/MONOREPO.md) maps every role
to its directory and records operator commands, selected snapshots and
qualification scope. Run `python scripts/superrepo.py list` or
`python scripts/superrepo.py audit` from the repository root.

Builds and tests run in temporary copies or exact detached provider worktrees.
Keep imported source trees byte-identical to their manifest revisions; a module
change requires an explicit new source identity and compatibility review.
