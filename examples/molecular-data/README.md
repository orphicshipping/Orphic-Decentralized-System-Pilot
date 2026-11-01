<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/molecular-data`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `sources`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Molecular reference source pack

See [the operating and evidence guide](../../docs/MOLECULAR_REFERENCE_DATA.md).

This directory retains four exact-byte NIST FEASST source/license files, nine
selected NIST computed reference values and fourteen manufacturer nominal facts.
The files are pinned in `catalog.json`; `catalog.sha256` anchors those bytes.
Keep an independent copy of the anchor. Source hash checks establish integrity,
not physical truth or third-party endorsement.

The original NIST files are unmodified; derived exports are explicitly marked
as NET transformations. See `sources/FEASST-LICENSE.rst` for NIST attribution
and distribution terms. Manufacturer source documents are not included and no
model-training license is inferred. Missing lot, uncertainty and measurement
timestamps remain missing. No simulator runs occur merely by reading the pack.
