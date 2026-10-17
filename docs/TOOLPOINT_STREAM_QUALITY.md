# NET tool-point QC: append-only capture and fault-preserving containment

For current installed commands, qualification scope and snapshot compatibility, see
[Native QC integration](TOOLPOINT_UPGRADES_INTEGRATION.md).

## Delivered increment

This is an additive, standard-library implementation of a stateful capture-prefix
journal for the existing NET quality-control evaluators. It preserves the old
manufacturing and QC code; it does not introduce a replacement State Ledger,
change a physical controller, or authorize any machine or shipping operation.

Four new runtime modules are provided:

| Module | Responsibility |
| --- | --- |
| `ciw.toolpoint_stream` | Validate, append, seal, restore and verify bounded capture-prefix histories; preserve fault findings. |
| `ciw.toolpoint_stream_containment` | Verify manufacturing and stream evidence, bind the exact cycles/cavities/parts, and project holds through recorded assemblies and packages. |
| `ciw.toolpoint_stream_cli` | Source-module CLI for start, append, demo, summary, verify, final capture, containment and review gates. |
| `ciw.toolpoint_stream_example` | Authored synthetic scenarios for both existing evaluator schemas. |

The new source does not require new third-party runtime dependencies. Tests require
pytest. Coverage measurement uses coverage.py, which is not a runtime dependency.

## Two existing profiles, one new journal

The journal chooses a fixed adapter from the plan schema. It does not load a
user-specified provider, execute arbitrary code, convert units implicitly, or
reinterpret an existing plan as a different physical measurement.

| Plan schema | Existing evaluator | Validation basis |
| --- | --- | --- |
| `ciw.toolpoint-control-plan.v1` | `ciw.toolpoint_quality.screen` | The previously delivered `NET_Toolpoint_Quality_Control.zip` attachment. |
| `ciw.toolpoint-monitoring-plan.v1` | `ciw.polymer_toolpoint_qc.assess` | Exact source from PR #177, commit `dd8782a9df195a79769cbe9e8d63579578dc248e`. |

The native evaluator's Git blob is
`d44a637ea217c377c12d8f80fd12838ad891f739`. Its reconstructed file was checked
against this blob identity before the compatibility tests. That module was not
modified. Its integral, arrival, residual and peak semantics remain its own; the
journal retains the actual evaluator result rather than substituting a new one.

The repository and the earlier attachment have different implementations named
`manufacturing_quality.py`. **Do not overwrite the repository using the focused
snapshot.** Apply the additive patch instead. It does not replace either version.

The legacy profile is optional in a repository containing only the newer native
QC implementation. New tests skip that adapter when its pre-existing module is
absent; they do not substitute a mock. Both adapters were installed and exercised
in the delivered focused snapshot. The native evaluator was tested directly;
the complete current manufacturing-v2 integration journey was not reproduced.

## Defect reproduced and addressed by the new path

In the earlier evaluator, an otherwise valid 18 MPa reading at 0.6 seconds could
be reported as a pressure deviation. Adding a later reading with unknown
uncertainty changed the diagnosis to `INSUFFICIENT_EVIDENCE` and removed the
reported first-alarm time. The original quality gate still held the component,
so this was loss of diagnostic evidence, not an observed false release.

The new path protects that evidence in two ways:

1. An accepted earlier deviation is latched, with its rule, sensor, cavity,
   update identity, first accepted sequence and declared receipt time.
2. A separately checked valid sampled violation can be retained even when the
   same packet also contains an unrelated invalid sample or a sampling gap.

The second check is deliberately narrow. It supports sample-band violations and
high sampled-peak witnesses. It does not infer a low peak from one low point,
prove missing arrival from incomplete coverage, infer pressure integrals from a
single reading, or classify an actual molded defect.

For a supplied reading `y`, supplied expanded uncertainty `U`, and upper limit
`H`, a high witness requires `y - U > H`. A sample-band low witness requires
`y + U < L`. The sample's entire declared timing interval must fit the rule
window and calibration validity. Required provenance must be present, timing
uncertainty within budget, and channel health declared good. Arithmetic uses a
512-digit local Decimal context on the supplied decimal representations. This
is an interval screening contract, not a claim of statistical coverage or
calibrated physical accuracy.

The original evaluator's result is retained unchanged under `latest_assessment`.
A journal finding therefore does not conceal missing data or replace its reported
uncertainty with a guessed value.

## Stateful ingestion contract

A `ciw.toolpoint-stream-spec.v1` binds the exact plan to one cycle ID, cycle
reference, source semantics and start time. A `ciw.toolpoint-stream-update.v1`
contains an ordered sequence, the previous receipt hash, a declared receipt time,
a finalization flag and the original evaluator's native capture object.

Each accepted update produces a content-addressed receipt binding the specification,
update, native assessment, derived stream state and previous receipt. A report
retains all accepted updates and receipts plus an execution occurrence identity.
Fresh verification has its own occurrence identity. Content, operation, execution
and verification identities are not interchangeable.

The following invariants are enforced:

- Previously accepted samples cannot be edited, removed, reordered or rehashed
  into a different history. A previously observed channel cannot disappear.
- New samples must be strictly later than the prior acknowledged capture
  watermark. Late backfilling is refused, not silently used to improve a prior
  assessment. This is a strict aligned-prefix profile, not a network reorder buffer.
- Sensor identity, calibration, alignment and source provenance are immutable
  within a capture. Dynamic sample hashes are checked by the existing adapter.
  A changed calibration requires a separately identified capture, not a quiet rewrite.
- A channel-health change may be recorded, but a prior health fault remains a
  quality hold even after a later `good` declaration.
- An exact duplicate sequence is an idempotent retry and returns the same receipt.
  A conflicting duplicate, stale parent, sequence gap or backward clock is refused.
- The capture watermark cannot be later than its declared receipt timestamp.
  Reported capture-to-receipt delay is arithmetic on supplied times, not measured
  hardware latency or a demonstrated real-time deadline.
- A final capture seals that journal instance. Identical retries still work;
  new updates do not. Unfinalized or incomplete evidence cannot become a final
  clear process result.
- Validation, evaluation and retained-size checks finish before in-memory state
  changes. A refused append leaves the session unchanged.

There is no automatic release or hold-closure operation. `HELD` can persist even
when the most recent native assessment alone looks acceptable. That is intentional:
a later healthy sensor state must not erase an earlier fault occurrence.

## Checkpoints, replay and rollback anchors

`CaptureJournal.restore(report)` validates and reevaluates every retained prefix;
it does not trust cached receipt conclusions or the final cached result. The
restored execution has a new occurrence identity, while deterministic receipt
and result identities remain reproducible.

A valid older checkpoint is still a valid historical artifact. To reject it when
a newer head is already known, provide `expected_head_ref`. To bind verification
to a separately retained specification, also provide `expected_spec_ref`:

```python
verification = verify(
    report,
    expected_head_ref=previously_retained_head,
    expected_spec_ref=previously_retained_specification,
)
```

The returned receipt states which anchors were checked. The hashes must come
from the caller's trusted retained state; supplying a hash copied from an
untrusted report does not authenticate that report. No external ledger root,
signature service or multi-writer consensus is supplied by this increment.
A coherent replacement of all source declarations remains unauthenticated.

## Containment and manufacturing integration

The read-only containment projection accepts a verified manufacturing report,
stream reports and an assessment time. It requires exact configuration, part,
cycle, source and clock bindings and checks the actual recorded cavity-to-serial
mapping. A capture cannot extend beyond the corresponding manufacturing cycle;
future receipts or manufacturing events are refused.

Every recorded forming cycle requires stream evidence. Missing streams, open
captures or affected cavity states produce holds. Shared nozzle findings apply
to the cycle cohort; local findings apply to the associated cavity output.
Holds propagate forward through recorded descendant assemblies and packages.
The manufacturing history is not rewritten to pretend prior assembly or packaging
did not occur.

Existing inspection failures, quarantine and scrap remain protected. The projection
also preserves the current manufacturing-v2 `quality_holds` and `process_quality`
fields when supplied by its verified reducer; that v2 end-to-end journey still
requires repository-level qualification. A clear stream is never permitted to
clear an independent recorded manufacturing hold.

The demonstrated health-recovery case is:

```text
cavity-2 sensor fault in an early accepted prefix
    -> later sensor recovery and acceptable final native signature
    -> stream hold remains on p002
    -> p002's dimensional PASS remains recorded
    -> assembly a001 inherits the hold
    -> package held; shipment remains unauthorized
```

Unrelated component `p003` remains eligible for review, and the pre-existing
scrapped component `p004` remains scrapped.

Review gates support only `assemble`, `pack` and `release_review`. Consumed,
scrapped, held or otherwise ineligible records are blocked. Even an eligible
result has `action_authorized=false`. BOM, order, physical and approval owners
retain their existing authority. This projection is not a transaction interceptor
for the legacy manufacturing endpoint or a machine-control enforcement mechanism.

## Run the delivered snapshot

From `focused_snapshot/` in the ZIP, in a POSIX shell:

```sh
export PYTHONPATH=src
python -m ciw.toolpoint_stream_cli demo --case late-unknown --output new-stream.json
python -m ciw.toolpoint_stream_cli verify new-stream.json
python -m ciw.toolpoint_stream_cli summary new-stream.json
python -m ciw.toolpoint_stream_cli final new-stream.json --output new-final-capture.json
```

For PowerShell, set `$env:PYTHONPATH="src"`, then run the same `python -m ...`
commands. This syntax is provided for the user's Windows environment; Windows
execution has not been performed here.

The stateful capture journey uses the supplied native-profile example files:

```sh
python -m ciw.toolpoint_stream_cli start examples/stream-quality/spec.json examples/stream-quality/capture-02.json --received-at-s 0.21 --output first.json
python -m ciw.toolpoint_stream_cli append first.json examples/stream-quality/capture-06.json --received-at-s 0.61 --output second.json
python -m ciw.toolpoint_stream_cli append second.json examples/stream-quality/capture-08.json --received-at-s 0.81 --final --output final.json
python -m ciw.toolpoint_stream_cli verify final.json
```

A later append can use `--expected-head-ref` to reject a checkpoint inconsistent
with the caller's separately retained head.

For containment:

```sh
python -m ciw.toolpoint_stream_cli contain examples/stream-quality/containment-request.json --output new-containment.json
python -m ciw.toolpoint_stream_cli verify-containment new-containment.json
python -m ciw.toolpoint_stream_cli gate new-containment.json --action release_review --serial a001
```

Exit code 0 means the software action completed. It is not product release.
A `HELD` or `PENDING` screening/review result exits 2. A truthful report containing
a hold can still verify successfully and exit 0. Malformed input or failed
publication exits 1. Output files are atomic and create-only through NET's existing
IO helper; use new filenames. Parent-directory trust and filesystem support remain
deployment responsibilities.

No top-level `net` routing was changed. The new CLI is directly executable as a
module, so it can be tested without replacing either existing dispatcher.

## Validation actually performed

Combined local test run: **638 passed, 0 failed, 1 skipped**.
This includes **304 new tests** and **334 passing tests from the attached QC and
manufacturing foundation**. The skip is the original full polymer dispatcher,
whose unrelated NET dependencies are absent from the focused snapshot.

The added tests include independent Fraction interval comparisons, 80 packetization
cases, pre-existing API regression, signal-health recovery, mixed-validity packets,
calibration/timing boundaries, shared versus local containment, idempotent retry,
conflicting updates, rollback anchors, complete checkpoint replay, altered-and-
rehashed reports, strict JSON, create-only publication, injected failures and
actual CLI subprocesses. Exact native reference-source identity was separately
checked against the Git blob above.

The coverage run reports **95% combined statement/branch coverage across the four
new modules only**. It is not repository-wide coverage or physical qualification.
The exact source checksums, raw test output, JUnit XML, coverage data, retained
synthetic reports and verification receipts are included under `validation/`.

## Bounds and remaining limitations

This is a single-writer in-memory retained-data journal, with file checkpoints.
It is not a durable acquisition daemon, concurrent database transaction service,
industrial protocol adapter, streaming transport or hard-real-time controller.
It uses cumulative capture snapshots; history therefore has bounded but potentially
quadratic storage cost in the number of prefixes. Work refuses before crossing
the existing 2 MiB / JSON traversal bounds. More frequent or longer captures need
a separately qualified delta/chunk store, not a raised budget without review.

The maximum configured update count is 64. Per-sensor sample limits remain capped
at 512 for the legacy evaluator and 2048 for the native evaluator, with a 32768
aggregate sample ceiling. Effective capacity also depends on the existing global
JSON size and traversal budgets. These are upper bounds, not guaranteed throughput.

Sensor mappings are immutable within a stream. Late serial assignment, mapping
resolution transitions, reorder buffers, authenticated calibration replacement,
rework/hold closure and cross-process synchronization are not implemented.
No automatic baseline learning, SPC capability indices, model retraining, physical
reject sorting, protective interlock, same-shot correction, certificate or shipment
release is added.

Full-repository regression, the full current manufacturing-v2 journey, installed
wheel/top-level `net` entry, Windows, hosted CI, live sensor acquisition, physical
false-hold/missed-defect rates and controller timing remain unverified. Synthetic
18 MPa / 15 MPa values are test fixtures, not process settings or qualified limits.

No repository write, push or merge was performed for this increment. The additive
patch and the focused runnable snapshot are the delivered artifacts.
