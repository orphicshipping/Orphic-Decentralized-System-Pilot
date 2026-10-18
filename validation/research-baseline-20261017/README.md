# Selected research-baseline evidence

Code under test: `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`; tree
`862785f15628cb3d1c2ec0c620e357dbd1ddb876`. Later evidence-only commits do not
change that executable identity. See [the ledger](../../docs/RESEARCH_BASELINE_20261017.md)
and [task audit](work-order-audit.json). This is locally verified software;
required hosted CI is blocked, and this is not a globally qualified release.

`candidate.json` records gates and counts. The module summary and explicit skip
reasons retain the full sweep. Inventories retain actual PR closures, the original 282
branch heads, the pinned 235-head cleanup plan, 156 tracked workflows and all
21 imported provider boundaries. `hosted-ci-report.json` is an offline policy
evaluation of an authenticated API observation, not a live collector pass.
The later run-status observation is retained separately; queued/pending runs
still do not satisfy release acceptance.

The archives retain raw reports without screenshots or credentials. Verify each
archive with `archive-digests.json` before extraction. From this directory:

```sh
mkdir -p reports
tar -xJf selected-reports.tar.xz -C reports
tar -xJf operator-journeys.tar.xz
tar -xJf consolidated-reports.tar.xz
tar -xJf earlier-attempts.tar.xz
```

The selected reports contain the original 176-case history JUnit, installed
NCP/priority/native JUnit, source audit, wheel byte proof, console/DOM logs and
actual Chromium report/log. The journey archive retains all three correction
precursors plus operational/HTTP report evidence; its embedded run directories
retain the original names. Their synthetic checks do not establish physical
validity or independent-human acceptance. Earlier failed/interrupted attempts
remain separate and never qualify the selected SHA.

The downloadable handoff ZIP additionally includes the exact wheel and the
13 MB `operator-quickstart.tar.xz` containing the full 26-command/750-check
installed quickstart and first-use outputs. The compressed summary
`quickstart-report.json.gz` and first-use receipt are retained here as well.

For branch cleanup, review [the pinned human-readable list](branch-cleanup-review.md).
Task 4 requires confirmation before archival/deletion. Preserve archive tags and
recheck each expected head; changed heads must refuse cleanup. No original branch
has been deleted. PRs #244 and #139 subsequently merged outside the evidence publication. The final repository observation records their state and the added history transport; this evidence follow-up remains a separate review.
