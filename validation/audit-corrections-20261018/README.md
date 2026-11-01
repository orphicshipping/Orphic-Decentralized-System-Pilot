<!-- ncp-folder-entry -->
# MCP entry

- path: `validation/audit-corrections-20261018`
- class: `evidence`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Audit corrections evidence

Implementation SHA: `6f783dd8a37c4509a91cc86f8cfccba6ca2579e1`.
Base main: `861e29fa8f78e3f953991ca7f5f62790a3088df7`.
Frozen qualification reference: `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`.

See [the audit ledger](../../docs/AUDIT_CORRECTIONS_20261018.md) and
`summary.json`. The raw archive contains final regression reports/logs,
native projector inputs/screenshots/launch receipts, installed MCP/HTTP receipts,
the re-executed atomic CPU gate (12 cases / 58 checks),
source/wheel identity checks, independent review and hosted observations/logs.
All counts overlap and must not be added together.

The 519-module isolated sweep completed: 18,950 passed, 1,173 skipped,
13 failures and 136 setup errors. The overall gate is NOT_PASSED. Nonpassing
cases are confined to the two unchanged Git-history modules: missing ancestry
and three absent backup blob objects prevent their required checks/clones.
The other 517 modules have no failures/errors. Nothing was reclassified as a
skip, weakened, grafted, replaced or waived to obtain a pass.

The local worktree was byte-matched to the published implementation before and
after checks (5,427 matching materialized files; three unchanged backup pieces
absent). Its local Git HEAD is recorded separately; it is not a complete-history
checkout of the published commit. The installed wheel's 717 package files
byte-match the tested source and its SHA-256 is retained in `wheel-identity.json`.

`archive-manifest.json` binds every raw member; `SHA256SUMS` binds the retained
top-level files. Seventeen original baseline hosted archives were separately
downloaded and all archive/member hashes retained. Those originals and the
tested wheel accompany the downloadable evidence export; they are not duplicated
into this Git directory. Their inspection is read-only and does not close an
unfinished workflow. Eleven successful-job logs are in the raw archive.

As of 05:38:46 UTC, six required baseline runs succeeded, one failed SP1 resource
preflight and seven remained queued. This increment neither waives those gates
nor establishes physical validation. Temporary authority profiles, private key
fixtures, intermediate failed development probes and engine binaries are omitted.
