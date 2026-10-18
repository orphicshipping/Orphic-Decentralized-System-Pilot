# Operational hygiene audit — 18 October 2026

Large feature work remains frozen against research candidate `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`.
The live `release/research-baseline-20261017` ref still points to that exact
commit. No hosted qualification requirement was waived or weakened. This
maintenance patch is based on main `9e04140ff07b6699042153e80e45e8513dcafe08`, which includes the previously
merged integration and evidence PRs #245/#246.

## Hosted qualification

The unchanged release-policy evaluator reports **blocked for all 14 required
workflows**: eight runs queued, six pending; 51 jobs comprise 50 queued and one
allowed skipped slow-reproduction job. There are **zero required artifacts**.
The run states were rechecked at 2026-10-18T01:27:45.459Z.
The [assessment](../validation/operational-hygiene-20261018/hosted-assessment.json)
and raw API snapshots retain the exact candidate, run, attempt, job and artifact
identities. Pending jobs were not rerun or cancelled, and no pass was inferred
from local results. Starting/finishing those existing GitHub jobs remains the
hosting-side action required for qualification; the observed statuses do not
establish why GitHub has not scheduled them.

One successful Cantera Windows worker artifact was downloaded and inspected:
13 tests passed, zero skips/failures. Its API head is the candidate, but its
payload explicitly records PR merge checkout
`1142dd33d80ae3e6742c705f38f817f5ffef0251`. It also declares full runtime qualification
unperformed. It is retained as **supplemental PR evidence**, not same-SHA baseline
qualification. The existing policy already excludes PR events from release
acceptance. See the [artifact record](../validation/operational-hygiene-20261018/supplemental-hosted-artifact.json).

## Confirmed branch cleanup and execution limit

The user follow-up authorized confirmation, archival and deletion of the prior
235 eligible heads. Every head still matches its exact ledger SHA; all are
unprotected and all proposed archive tags are absent. Of these, 169 are exact
ancestors of the frozen candidate; the other 66 retain their prior reviewed PR
reconciliation rationale. They are not relabelled as Git ancestors.

The inventory was 283 branches before creating this one active maintenance
review branch, and **284 afterward**. **Zero original branches have been deleted
and zero archive tags created.** The connected GitHub interface exposes neither
tag creation nor branch deletion, and the Git credential helper returned no
authenticated credentials. GitHub PR/commit access does not supply an
authenticated Git transport. The [exact-head record](../validation/operational-hygiene-20261018/branch-revalidation.json)
therefore records this as a capability blocker, not a request for repeated
confirmation or a completed Task 4 acceptance criterion.

`scripts/cleanup_reconciled_branches.py` makes the authorized action executable
for a maintainer with authenticated Git. Preview is read-only; execution binds
the original plan SHA-256, protects main/release refs, verifies the frozen
candidate anchor, refuses changed heads/archive conflicts and uses **one atomic
push with per-ref leases** for archive creation and exact-head deletion.
An identical concurrent archive is accepted only if its exact identity survives
the post-push check. Immutable pre-push intent and separate result receipts are
retained; missing atomic support or Git refusal never falls back to individual
deletions. A timeout requires fresh inspection before any retry.

From a complete repository checkout with authenticated `origin`:

```sh
python scripts/cleanup_reconciled_branches.py --remote origin --plan validation/research-baseline-20261017/branch-cleanup-plan.json
mkdir -p results/branch-cleanup
python scripts/cleanup_reconciled_branches.py --remote origin --plan validation/research-baseline-20261017/branch-cleanup-plan.json --execute --plan-sha256 9166b17b6062debaec3546c70850869209910e644fe2e3601c00af3593b20c8f --receipt results/branch-cleanup/receipt.json
```

Both receipt paths must be new. Review any changed-head refusal instead of
updating the plan to match a moving branch. Source objects must be available in
the explicit local checkout. The command has passed **10 actual local Git
transaction tests**, including atomic server refusal, head/tag races, frozen
anchor changes, exact archival, idempotent completion and receipt preservation.
Those tests do not claim that the GitHub cleanup was executed.

## Workflow consolidation

The current main had **157 workflow files / 193 top-level jobs**, including the
previously merged source-retirement workflow. A declaration audit found **zero
exact duplicate workflows or jobs** once inherited events, permissions,
environment, runner and matrix contexts are retained. Similar platform commands
remain distinct validation lanes; none was removed.

The simulation-only workflow was a strict subset of the existing FSRT retained
view campaign. `simulated-fsrt.yml` is retired into
`synthetic-fsrt-view.yml`, reducing the repository to **156 files / 192 jobs**.
The two original step sequences are exact ordered subsequences of the shared
job, ignoring display names only. FSRT/Godot pins, native 14-case and export
21-case zero-skip guards, source XML files, both artifact names and their paths
are preserved. Provider/engine setup and the native simulation execute once.
The two existing source test invocations remain to preserve their original XML
artifact contracts. The PR trigger preserves the retired unfiltered scope;
manual dispatch and existing read-only permissions remain. Concurrency cancels
superseded runs within each event/PR while isolating manual dispatches.

The [before](../validation/operational-hygiene-20261018/workflows-before.json)
and [after](../validation/operational-hygiene-20261018/workflows-after.json)
inventories retain literal test overlap for further deliberate review. The
[ordered-subsequence proof](../validation/operational-hygiene-20261018/workflow-retirement-proof.json)
can be rerun with the accompanying `verify_workflow_retirement.py` against this
patch. All 14 release-required workflow paths, jobs, artifact contracts and
release policy bytes remain unchanged. `workflow-contracts.yml` now executes
the cleanup tests and retains its inventory as a separate metadata artifact.

## Local verification and remaining owners

- 180 combined FSRT source tests passed; the original three-module source
  invocation also passed all 119 cases. These sets overlap.
- 10 cleanup transaction tests and 18 existing CI-policy tests passed.
- 54 release-evidence tests and 186 subtests passed; the policy remains blocked
  on the actual incomplete hosted evidence.
- Hash-verified actionlint 1.7.12 passed all 156 workflows. Projection consistency
  and Git whitespace checks passed.

The native Godot 14-case campaign and 21-case retained-view export were not
freshly executed in this maintenance pass. Their declarations and strict
requirements are preserved; configured CI is not an execution result. No
production package source, provider revision, release policy, equipment control,
physical/hardware qualification, kernel family or design-only connective-tissue
implementation changed. Previous selected-code and wheel evidence retains its
original SHA attribution. Hosted completion and authenticated remote branch
cleanup remain open; private/platform/physical and independent-human gates keep
their existing owners.
