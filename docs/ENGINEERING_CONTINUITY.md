# Controlled engineering continuation and exact recovery

This increment extends the existing Digital Thread Compiler in PR #219 at
`15a2e5ff080c2c39e23f40310e083ab517c55177`. It adds no orchestrator, acquisition
schema, scientific engine, registry, queue, proof verifier or physical controller.
The current Link/NET numerical graphs and all five internal evidence obligations
remain unchanged. These are internal engineering controls, not certification or
an exhaustive assessment against an ISO/IEC standard.

## Execution must inspect the actual predecessor

Previously, the low-level compilation API accepted well-typed continuation fields
and a string claiming local predecessor review. With host compute permission,
`run_packet` could execute revision 2 without reopening a predecessor, including
when the references were invented. The normal `revise` route reviewed the previous
work order at proposal time; the execution route did not enforce that check.

Now a continued run requires an explicit host-selected predecessor directory.
The predecessor's expected exact-byte reference comes from the separately selected
compilation. Saved data cannot supply a filesystem path, endpoint, credential or
arbitrary executable. The existing bounded reader validates the original files,
Session, production plan, acceptance findings and source bindings before dispatch.

The current intent must retain the predecessor's identity, increment its revision
by exactly one, and preserve requirements, constraints, operation budget and
scientific decision basis. Original execution and result references must match
exactly. Equal inputs with different computation occurrences are not interchangeable.
These checks apply even when a caller bypasses the convenient `revise` interface.
New evidence and a properly rebound installation occurrence remain supported;
all calculations execute again with fresh computation occurrences. No earlier
result is relabelled as a new measurement or a new qualification.

```sh
net production engineering run next-compilation --profile scalar \
  --compiled-ref sha256:EXTERNALLY_SELECTED_COMPILATION \
  --predecessor prior-execution --allow-compute --output-dir next-execution
```

Initial revision-1 runs still need no predecessor. They reject an extraneous
predecessor rather than silently implying inherited evidence. The host compute
grant is still required and is checked before any input read. A successful
lineage check never supplies that grant or permits hardware access.

This is a check of the **immediate** predecessor's content and original occurrences,
not a digital signature, proof of genuine acquisition, human review ceremony,
recursive validation of every ancestor, or a sandbox against a hostile host.
It is not an authorization service or a replacement for separate account/node/
device identities. Normal historical inspection does not renew or replay a
predecessor claim; the new enforcement occurs when starting a continued run.
Previously created histories are not rewritten or retrospectively certified.

## Recovery is not replay

```sh
net production engineering restore retained-execution --profile scalar \
  --thread-ref sha256:EXTERNALLY_RETAINED_THREAD \
  --output-dir recovered-execution
```

Restore validates and freezes the original bounded artifacts through the existing
reader, including the exact externally anchored `thread.json` bytes. It writes
a new directory, preserves all original bytes and computation occurrences, writes
the original completion manifest last, then re-inspects the destination. It creates
no scientific execution and activates no credential, provider or permission.
The returned command receipt does not change the scientific history.

The destination must be new and outside the original thread. Missing, changed,
extra, unsafe or inconsistent source records refuse; source corruption is not
silently repaired. Failure during a copy leaves a partial destination without a
complete-history claim. A completed copy interrupted before command return can
be inspected using the original thread reference. There is no overwrite, automatic
resume, rollback of historical evidence or hidden reacquisition.

The operation reuses individual file syncs from the existing writer. It does NOT
claim atomic directory replacement, hardware power-fail durability, offsite
backup, encryption or malware resistance. The caller must retain the external
thread reference separately. A locally recomputed hash alone does not authenticate
an untrusted archive. Full workspaces can contain proprietary information; restore
is not consent to export or transmit them.

## Evidence ladder and limitations

Numerical PASS still leaves reference traceability, physical timing qualification,
uncertainty budget, independent validation and product safety/EMC evidence
INDETERMINATE. No new evidence importer, standards adjudicator or reviewer key is
introduced. Physical qualification remains HELD and formal verification identity
remains null. Coherence, simulation, signatures, lineage checks and physical
validity remain distinct.

The wrapper change does not alter the compiler identity, original operations or
scientific result formats. The published commit and exact package-byte manifests
identify this I/O implementation. Existing clean work orders remain readable and
restorable with their original bytes. A valid new revision must provide the
predecessor explicitly; that is an intentional stricter execution precondition.

## Internal verification requirements

| Requirement | Executable coverage |
| --- | --- |
| ENG-CONT-001: declared lineage is not sufficient to start a continued run | Fabricated predecessor and missing-current-review regressions |
| ENG-CONT-002: later revisions cannot waive the original decision problem | Direct-compiler identity/revision/criteria/budget/basis tampering tests |
| ENG-CONT-003: old occurrences cannot be replaced by equal-input replays | Same-input/different-occurrence rejection and original-ID comparisons |
| ENG-CONT-004: recovery is exact, read-only with respect to source, and non-executing | Byte equality, frozen-source replacement, no-clobber and no-dispatch tests |
| ENG-CONT-005: interrupted recovery is not successful qualification | Storage-failure injection and actual child-process exits around manifest publication |
| ENG-CONT-006: permission and empirical holds survive recovery and revision | No-grant test, five unchanged obligations, scalar and installation journeys |

The existing source/installed qualifier includes the new continuity tests and
an installed restore -> revise -> missing-predecessor refusal -> permitted-run
journey. Tests use genuine NET and synthetic instrument evidence. No physical
sensor, actuator, external solver, SP1 proof, safety/EMC test or customer evidence
is qualified by these software checks. Exact dependency-pin and full-monorepo
qualification remain separate requirements. Keep draft; no merge or deployment.
