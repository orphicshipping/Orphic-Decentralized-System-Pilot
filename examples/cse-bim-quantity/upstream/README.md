<!-- ncp-folder-entry -->
# MCP entry

- path: `examples/cse-bim-quantity/upstream`
- class: `navigation`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `sp1`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Upstream cites (MCP-read)

Copied / listed via GitHub MCP `get_file_contents` from
`giasonpooni/Construction-State-Estimator-for-BIM` @
`4b74abda40bba3277de69bf61e9e09283ae2d5b3`.

- `sp1/` — CSE has an `sp1/` tree at this pin (`sp1/beam`, `sp1/kernel`);
  see `sp1/Cargo.toml` cite and kernel listing. Not imported as a live prover.
- Disposition vocabulary from CSE README: SATISFIED → ACCEPT recommendation;
  VIOLATED → REJECT; UNRESOLVED/evidence missing → REQUEST_EVIDENCE.
- `--demo` / demo IFC fixtures are not field evidence.
- JSPT owns sensitivity; do not put `sensitivity` in the guest.

`git clone` 403; MCP read succeeded. Full CSE package not vendored here.
In-tree CIW surface: `ciw.bim-quantity.v1`, `examples/bim-quantity/`.
