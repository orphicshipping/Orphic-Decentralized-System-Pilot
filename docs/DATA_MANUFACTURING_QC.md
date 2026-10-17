# Retained manufacturing and QC evidence in a shared Session

This bridge lets an existing NET workspace retain complete native manufacturing
reports and tool-point capture journals, then run the existing read-only native
containment projection as a normal Session operation. It does not introduce a
second SQLite store, a new detector, a replacement manufacturing reducer or a
release authority.

## Typed inputs and real consumption

`net data retain` accepts two additional fixed families:

- `manufacturing-report`: native manufacturing report versions 1 and 2
- `toolpoint-stream-report`: the current native monitoring-plan capture journal

The native verifiers validate reports before retention. Exact original bytes,
report and execution identities, specifications, receipt heads, result references,
sensor histories and source semantics stay unchanged. Snapshot-only legacy
monitoring profiles are refused. Export a journal through the existing anchored
SQLite CLI before retaining it; the data bridge itself never opens or writes a
database.

Add each retained envelope to the existing workspace with `net data add`. The
returned Workbench source IDs identify the exact immutable source occurrences.
Then invoke:

```console
net data review-toolpoint --manufacturing-source-id SOURCE_ID --stream-source-id CYCLE_0_ID --stream-source-id CYCLE_1_ID --assessed-at-s 12 --workspace with-sources.json --output reviewed.json
```

The fixed operation ID is `data.review-toolpoint-containment.v1`. Its parameters
are full retained descriptors under `manufacturing_source`, a `stream_sources`
list, and `assessed_at_s`. It accepts zero to 64 streams, subject to the existing
Workbench source count, native cohort/size limits and an 8 MiB parameter budget.
No storage limit is widened. Missing cycles remain held; duplicate, foreign or
contradictory cycle, configuration, clock and serial mappings are refused by the
unchanged native containment logic.

The operation returns the real native containment result and exact input identity
references. Framework execution/result IDs identify this review occurrence;
repeating the same review produces a new occurrence with the same native result
identity. A successful command means the retained-data operation completed,
including when parts or packages are held. It never means shipment is authorized.

## Correction, replay and authority

Every supplied manufacturing and capture source is a dependency of the resulting
execution and result. Accepting an existing Session source correction marks only
its dependent reviews stale; unrelated reviews remain current. Old bytes, results
and native receipt history remain intact. Running against a stale source produces
a separately retained stale review, and the review CLI exits 2. It cannot silently
present that occurrence as current evidence.

Workspace reopening checks exact retained source presence and bounded local native
replay. It does not launch scientific providers, acquire signals, write SQLite,
rerun external simulations or rewrite stored runtime identities. Forged results,
changed source descriptors and missing dependencies are refused even if a caller
recomputes outer hashes.

Current manufacturing-v2 post-pack holds, assembly ancestry, inspections, scrap and
release gates remain authoritative. Imported data and accepted source corrections
do not close those holds. Source authenticity, physical detector performance,
calibration, independent inspections and product conformity still require their
own evidence. All shipped demonstrations and tests use authored synthetic data;
no report from one synthetic fixture qualifies a different product study.
