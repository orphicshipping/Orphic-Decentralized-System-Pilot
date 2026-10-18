# Workflow lane review — 18 October 2026 UTC

This operational review is based on main `c1c9baf51adf7c0c16c54c1e015cbc57b82e9c28`, after merged PR #247.
The research baseline remains `312a37ba613a0b57ea027b8d00df5b3b4fe08d69` on its unchanged release ref.
Large feature work remains frozen. No hosted requirement was waived.

## What was reviewed

The inventory retains **156 workflow files and 192 jobs**. Shared filenames are
compared at the declared pytest invocation, rather than treated as interchangeable
executions. The former lexical inventory includes copied fixtures and dependency
installation steps; its 100 repeated patterns are not an execution count. The
earlier “106 modules” figure was not used as a job-deletion target. These counts
use different scopes and should not be substituted for one another.

The new review identifies **180 pytest declarations**, correlating **121 shared
top-level test filenames across workflows**. The [module table](../validation/workflow-lane-review-20261018/module-review.csv)
records every such file's source hash, workflows, invocation identities,
comparison dimensions and disposition. The [invocation table](../validation/workflow-lane-review-20261018/invocation-contexts.csv)
retains runner/matrix, permissions, declared secret references, checkout refs,
prior setup hashes, import-origin declarations, collection arguments and outputs.
Secret references are names from workflow declarations; no secret values are read.

| Differing declaration dimension | Shared file groups |
| --- | ---: |
| Runner or matrix | 69 |
| Earlier provider/package/setup steps | 106 |
| Environment declarations | 49 |
| Shell, defaults or working directory | 59 |
| Pytest collection/order/flags, excluding JUnit destinations | 116 |
| Artifact/evidence contracts | 116 |
| Permission declarations | 0 |

Dimensions overlap; counts are not added. A different setup fingerprint is a
declaration difference, not proof of a different scientific requirement. Equal
permissions do not establish equal provider access or execution context.

**101 groups retain distinct declared contexts; 20 also have partly computed
arguments requiring review.** No further lane was removed by this report.
The previous FSRT consolidation remains the executed declaration simplification;
that patch preserves both original ordered step sequences and artifact contracts.
No new sharing proposal is qualified solely by this static inventory.

Of the 180 declarations, 146 have the relevant arguments represented literally;
34 retain partly computed arguments. Seventeen use default or computed collections
that are not enumerated as a complete test universe. The
[computed-argument table](../validation/workflow-lane-review-20261018/computed-arguments.csv)
keeps each uncertainty visible. Literal Python subprocess arrays are parsed with
`ast`; neither Python nor shell declarations are evaluated. Basename/computed-path
correlation with repository test files does not verify copied or installed content.
The compressed full JSON retains all original context declarations for inspection.

## Hosted and cleanup disposition

All **14 same-SHA required hosted workflows remain incomplete**: eight queued,
six pending, with 51 jobs and **zero required artifacts**, observed at
2026-10-18T01:47:16.013Z.
The unchanged release evaluator remains blocked. Each run has a linked, explicit
[disposition](../validation/workflow-lane-review-20261018/hosted-dispositions.json):
keep the requirement, have the Actions maintainer resolve its hosting-side pending
state, retain the exact push/dispatch artifacts, then evaluate the same policy.
No failed-job rerun was justified and no waiver was recorded. Local results and
the earlier PR merge-source artifact do not replace this missing evidence.

All 235 authorized cleanup heads still match and are unprotected; their archive
tags are absent. The remote inventory remains 284 branches. **No tags/deletions
were performed**: connected controls still lack tag/delete operations and
authenticated Git. The existing guarded command and original plan remain the
concrete next action for an authenticated maintainer; repeated user approval is
not needed. See [the cleanup disposition](../validation/workflow-lane-review-20261018/cleanup-disposition.json)
and the [operational command](OPERATIONAL_HYGIENE_20261018.md).

## Small next increments and entry path

The requested capability/profile projection, typed composition, GaugeLink depth
and numerical/MCP operator journeys remain gated by baseline hygiene. Their
[dispositions](../validation/workflow-lane-review-20261018/development-dispositions.json)
identify existing owners and acceptance paths. They must extend current operation,
grant, execution and record identities. Hardware, OpenUSD reload, reasoning and
external instrument release remain deferred. Design-only notes were not turned
into an executable parallel system.

[Start here](BASELINE_START_HERE.md) now gives the exact baseline wheel checksum
and distinguishes the later provider extension. Both wheels use the same package
filename/version, so their retained source and byte identities select the intended
build. The first retained investigation path remains the contributor entry point.

## Verification and reproduction

Ten focused parser/context regressions passed, covering copied-file false positives,
pip mentions, literal subprocess arrays, computed paths/lists, inline environment,
non-execution of Python, basename identity limits and retained permission/platform
differences. Existing CI policy and actionlint across all 156 workflows passed.
Workflow/job counts, release requirements, source package and provider pins remain
unchanged. This is an operational declaration audit, not a new full scientific
regression or hosted qualification campaign.

```sh
python -m unittest discover -s scripts/tests -p test_workflow_inventory.py -v
python scripts/audit_workflow_inventory.py --output results/workflow-inventory.json
```

The complete review is under
[validation/workflow-lane-review-20261018](../validation/workflow-lane-review-20261018/README.md).
