# Notations Control Protocol: continuity and evidence-driven follow-up

NCP is the continuing project formerly called Notations Engineering Terminal.
The name change does not replace the existing Session, operation registry,
production controller, Link runtime or evidence identities. This increment is
based on PR #219 at `497cd25312ea74e1f561c53645ffdb5b2c8b0664`.

## One implementation, two compatible command names

The distribution now advertises both console entry points:

```toml
net = "ciw.net:main"
ncp = "ciw.net:main"
```

Both invoke the exact same existing callable. `ciw`, Python module paths,
`ciw.*` schemas, historical `net` commands and retained signed/hash-bound records
remain unchanged. Legacy help text can still say `net`: this deliberately avoids
rewriting every parser or changing existing command output as part of a rename.
Package name, version, dependency pins and license remain unchanged. Project
repository links now point to `Notations-Control-Protocol`.

A console alias is not a new protocol version, network transport, implementation
or permission. PyPA's console-entry-point mechanism is used directly:
https://packaging.python.org/en/latest/specifications/entry-points/

## Follow-up work from actual retained findings

```sh
ncp production engineering demo --profile scalar --output-dir ../engineering-demo
# The existing demo returns exit 2 because physical evidence remains required.

ncp production engineering next ../engineering-demo/execution \
  --profile scalar --thread-ref sha256:EXTERNALLY_RETAINED_THREAD \
  --output ../follow-up.json
```

`next` uses the existing externally anchored, frozen-thread inspector. It does
not accept a free-form model answer, caller-authored PASS flag, saved status
screen or unverified report as a substitute for original records. It proposes
work only for existing non-PASS predicates; the original check order is retained.
No monetary cost, information gain, probability of failure, safety priority or
optimal experiment order is inferred.

| Original finding | Proposed follow-up |
| --- | --- |
| PASS | No task; retain its satisfied count. |
| Numerical FAIL | Review the discrepancy and original method/evidence; no physical cause is inferred. |
| Numerical INDETERMINATE | Review the source or missing analysis prerequisites; do not relabel missingness as failure. |
| Empirical evidence obligation | Assemble the identified evidence category for a qualified review. The qualified importer/adjudicator remains unimplemented. |

The existing five empirical obligations produce distinct evidence requests:
reference traceability, clock/phase qualification, uncertainty budget,
independent validation, and product-specific safety/EMC evidence. These are
internal guidance, not a normative standards checklist, calibration procedure or
claim that uploading a document would satisfy a requirement.

For the authored compliant-installation fixture, only unmet sampled frequencies
are named. The known failed 384 Hz point is not described as a failed continuous
band. Unknown timing or source-quality problems produce prerequisite review,
not a diagnosis of the mounting or the machine. Acquisition remains separate.

## Identities and authority

The worklist is a sealed `ciw.engineering-next-actions.v1` derived review. It
binds the original thread, compilation, intent, request, evidence, Session,
execution IDs and result IDs. Each item retains a content reference to its
original finding and its original source-result reference. Its action ID commits
both the item contents and source thread. The report also identifies the review
implementation bytes. The digest is an integrity identifier, not a signature or
formal computation proof.

Repeated review of the same intact history is deterministic. Exact recovery
preserves worklist identity. A fresh execution of identical source evidence has
new occurrences and therefore a different worklist identity. No scientific
execution ID is invented for the act of listing follow-up work.

Every item is non-executable. No provider or physical resource is advertised
merely because the guidance mentions it. `requested_capability` is null; no
execution plan, authorization token, automatic retry or installation is created.
Physical qualification remains HELD and standards conformance NOT_ASSESSED.

Proposals cannot be submitted as compilations. Existing `compile`, `revise` and
`run` contracts continue to require valid original inputs, unweakened criteria,
predecessor review where applicable, and a separate host compute grant. No
external evidence importer was added or replaced by prose.

`next` returns exit 0 for successful proposal generation, not successful
qualification. Malformed or mismatched history returns exit 1. Optional output is
create-only and must be outside the original retained thread. Without `--output`,
only stdout receives the proposal; the original investigation is not modified.
As with other full workspaces, the worklist may contain commercially sensitive
identities and is customer-local, not a consent-approved contribution export.

## Verification scope

The existing affected source and noneditable installed qualification gates include
`tests/test_ncp_next_actions.py`. The installed gate compares real `net` and `ncp`
executables on catalog, historical inspection and follow-up commands, checking
exact stdout, stderr and exit status rather than masking differences. It also
checks both installed entry points load the same callable.

The new tests use the original controller, Session, scalar DAG and installation
estimator on synthetic observations. They check failed versus unresolved results,
original criterion/finding bindings, cross-process determinism, exact recovery,
new-execution separation, no dispatch during review, corrupted/extra source data,
wrong external anchors and refused output overwrites or writes into history.

This remains a bounded mapping from existing predicates to review guidance, not
general autonomous graph search, Bayesian experimental design, dynamic resource
selection, calibrated uncertainty propagation, physical diagnosis, certification,
remote synchronization or hardware control. Full release qualification still
requires the unchanged exact dependency pins and independently qualified physical
evidence. No prior empirical hold is discharged by the NCP name or this command.
