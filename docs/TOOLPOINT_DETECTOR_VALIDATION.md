# NET tool-point QC: detector validation against independent outcomes

For current installed commands, qualification scope and snapshot compatibility, see
[Native QC integration](TOOLPOINT_UPGRADES_INTEGRATION.md).

## What this increment adds

The existing tool retains process traces, preserves early alarms, persists them
transactionally, and projects holds into manufacturing genealogy. This increment
adds a read-only retrospective study answering a different question:

> For this exact monitoring plan and independently inspected characteristic,
> which defects produced an alarm, which alarms arrived by the declared deadline,
> and which nondefective samples were held?

An early sensor fault can be a good reason to hold a part without being a detection
of a physical defect. This study keeps those outcomes separate. It does not fit
new thresholds, retrain a model, clear a hold, update a machine recipe, admit
canonical State Ledger state, or issue a qualification or shipment certificate.
No pre-existing source file is replaced or modified.

## Implementation

| Module | Responsibility |
| --- | --- |
| `ciw.toolpoint_validation` | Validate the frozen protocol and full inventory; replay capture journals; join inspection outcomes; score alarms and holds; retain review cases and conditional group-level bounds. |
| `ciw.toolpoint_binomial` | Exact integer binomial-tail comparisons and conservative Clopper–Pearson root brackets. |
| `ciw.toolpoint_validation_store` | Resolve exact enrolled stream heads from the existing durable store without writing its records. |
| `ciw.toolpoint_validation_example` | Authored synthetic adverse cases exercising the distinction between alarms, holds, late alarms, and missed defects. |
| `ciw.toolpoint_validation_cli` | `example`, `demo`, `evaluate`, `evaluate-store`, `verify`, and `summary`. |

Runtime code requires only the standard library and existing NET modules. Tests
require pytest; the independent SciPy comparison tests use SciPy when installed,
and are explicitly skipped when it is absent. Both existing evaluator profiles
are supported: `ciw.toolpoint-monitoring-plan.v1` and the optional legacy
`ciw.toolpoint-control-plan.v1`. The included snapshot contains both.

## Evidence structure

A `ciw.toolpoint-validation-request.v1` carries six fields:

```text
schema
protocol       exact predeclared monitoring plan and study criteria
manifest       expected samples, groups, cycles, cavities, serials and head anchors
streams        retained journal reports and declared receipt-time uncertainty
outcomes       separately supplied inspection labels and evidence references
assessed_at_s  retrospective study time on the plan's declared clock
```

The result contains individual cases, a targeted review queue, descriptive
part/cavity metrics, dependence-group summaries, conditional statistical bounds,
explicit criteria failures, and unchanged authority restrictions. The retained
report embeds the original request and carries distinct content, operation,
execution and verification identities.

### Frozen protocol

The protocol contains the **entire exact monitoring plan**, not just its friendly
name. Changing a pressure threshold, reference curve, sensor location, part,
configuration, phase, or revision changes the binding. Every supplied stream must
match that exact plan.

It also declares one target quality characteristic, inspection method and condition,
the target rule IDs whose deviations count as alarms, a decision deadline, allowed
receipt-time uncertainty, and the nominal study confidence and acceptance criteria.
The declared freeze time cannot follow an enrolled capture's start. This is a
consistency check on supplied times, not authenticated proof of preregistration.

Known development-group IDs and development-cycle evidence references are forbidden
in the evaluation inventory. A cycle cannot be split across groups. A cycle evidence
reference cannot be renamed into another cycle to inflate trial counts. These checks
cannot detect undisclosed training reuse or deliberately falsified declarations.

One study evaluates **one exact plan/configuration**. Different lots, tools, recipes
or plans require separate studies in this version. The tool does not silently pool
across those changes or claim generalization to unseen configurations.

### Enrolled inventory

Each sample binds:

```text
sample_id + group_id + cycle_id + cycle_ref + cavity_id + serial
+ started_at_s + stream_spec_ref + stream_head_ref + mapping_evidence_ref
```

Every configured cavity of every enrolled cycle must be listed. Duplicate sample
IDs, serials, cycle/cavity slots, contradictory mappings, and incompatible heads
are refused. Every supplied report and outcome must resolve to that inventory.
Missing captures or missing outcomes remain visible; they are not dropped from
the denominator by an inner join. Missing captures are precautionary stream holds.

For the native profile, the cavity/serial mapping is checked against the actual
retained mapping. Unresolved mapping produces abstention and a cohort hold, not
selectively credited local detections. The legacy profile requires an explicit
mapping reference but cannot independently resolve its external backing artifact.

The manifest itself is content-addressed. `expected_manifest_ref` and
`expected_protocol_ref` may be supplied from a separately trusted record. These
anchors detect changes to the enrolled inventory or criteria. They do not prove
that the enrollment included every physical part ever made. Removing an entire
cycle and coherently replacing an unanchored manifest remains unauthenticated.

### Independent inspection outcomes

An outcome binds the exact enrolled sample, target characteristic, method,
measurement condition, source semantics, assessor and inspection timestamp. It has
one of `DEFECT_PRESENT`, `DEFECT_ABSENT` or `INDETERMINATE`.

Known labels are scored only when there is an explicit `independent_measurement`
declaration and non-null inspection-evidence and calibration references. A missing
label, indeterminate measurement, missing evidence or undeclared independence is
`UNRESOLVED`, never a negative example. No unresolved label may silently improve a
review criterion.

The tool also rejects as usable ground truth a reference that is actually a known
QC result, receipt, source signal or assessment artifact from that same stream.
This catches obvious circular labeling even when the independence declaration
claims otherwise. It cannot authenticate external inspection files or determine
whether an inspector really worked independently. A new hash wrapping copied
information does not establish independence; external audit remains necessary.

The inspection time must be no earlier than the retained capture extent and no
later than the study assessment. All timing is on supplied declared clocks.

## What is counted

| Prediction | Meaning |
| --- | --- |
| `ALARM` | A retained sampled-limit violation or evaluator deviation from a selected rule applies to this mapped cavity, including shared nozzle rules. |
| `NO_ALARM` | The final covered stream has no applicable process hold and no selected alarm. This does not imply product conformance. |
| `ABSTAIN` | Missing/open evidence, unresolved mapping, or a precautionary hold without a creditable selected-rule deviation. |

The separate `stream_hold` field preserves the journal's fail-closed containment
recommendation. It is not a machine action, physical sorting result, complete
manufacturing release gate, or proof that a part was physically contained.

An alarm that subsequently acquires a missing-uncertainty sample remains an alarm
when the existing journal has retained a valid earlier witness. A sensor-health
fault with no process deviation remains an abstention even if it correctly causes
a hold. A later healthy sensor declaration does not erase the hold.

Descriptive part-level rates retain all usable independently labeled cases in their
denominators, including abstentions:

```text
alarm sensitivity             = alarmed defect cases / all known defect cases
timely alarm sensitivity      = timely-alarmed defect cases / all known defect cases
nondefect alarm rate           = alarmed nondefect cases / all known nondefect cases
nondefect hold rate            = held nondefect cases / all known nondefect cases
defect without stream-hold rate = defect cases without a stream hold / all known defect cases
```

A zero denominator is reported as `null`, not zero or perfect performance. Unknown
labels are counted separately. Missing captures and open captures are explicit.
Cavity-specific metrics remain descriptive; they do not receive unannounced extra
confidence claims. “Nondefective” refers only to the selected inspected
characteristic, not every property of the product. A hold may be justified for
another reason even when counted against that target characteristic.

## Timing: use alarm availability, not a retrospectively discovered sample

The timely-alarm calculation uses the **first accepted receipt time** of an
applicable retained finding, relative to the cycle start. It does not backdate
that receipt to the timestamp of a pressure sample found in a later packet.

For relative receipt time `t`, supplied expanded relative-time uncertainty `u`,
and deadline `d`:

```text
[t-u, t+u] entirely at or before d  -> TIMELY
[t-u, t+u] entirely after d        -> LATE
interval crosses d               -> AMBIGUOUS
unknown/over-budget uncertainty  -> UNKNOWN
```

Only `TIMELY` counts as a timely detection. Decimal arithmetic with 512 digits
preserves small nonzero time margins at the boundary. The supplied uncertainty
must cover the relative receipt/cycle-start clock relationship, not merely the
pressure-sensor timestamp. This is arithmetic on declared timestamps, not measured
software scheduling, database commit, network, actuator or electrical response
latency. There is no same-shot control claim.

## Groups and statistical inference

Multiple cavities and successive parts from a run can be dependent. The tool does
not use each part as an independent trial for its primary confidence claims.
Instead, the declared independence group is the unit:

* A group containing a known defect succeeds on detection only if every known
  defective case is timely detected and all its enrolled labels are usable.
* A fully labeled all-nondefective group has a false-hold event if any enrolled
  case has a stream hold.

The user must choose groups appropriate to the sampling design; naming groups does
not establish independence. Different group sizes or populations need a defensible
exchangeability design, not an assumption that grouping alone solves correlation.
A group containing unresolved labels is not silently counted as an all-nondefective
group or a successful detection group.

Inference is emitted only for the explicit
`independent_exchangeable_groups` declaration with a sampling-design reference.
`descriptive_only` or a missing design reference suppresses the bounds and prevents
statistical review eligibility. Any bounds remain conditional on independence,
exchangeability, a fixed sampling design, correct labels, and no undisclosed reuse.
The implementation does not validate those empirical assumptions. When outcome or
capture completeness criteria fail, numerical bounds describe the currently scored
aggregates only; they are not an eligible population-performance claim.

### Exact arithmetic and multiplicity

The lower Clopper–Pearson endpoint is found by inverting the binomial upper tail.
Every bisection comparison uses exact integers. The root is bracketed on a grid
with spacing `2**-48`; the reported lower bound rounds outward downward. The
upper endpoint is derived by complement symmetry and rounds outward upward.
This avoids underflowed floating tails or an inward-rounding success claim.

Two primary decisions are predeclared: a lower bound on group timely-detection
probability and an upper bound on nondefective-group any-hold probability. The
family tail `1-confidence` is split equally between them by Bonferroni. Thus a
95% family-confidence declaration uses a 0.025 tail for each selected bound.
No simultaneous coverage claim is added for the secondary opposite endpoints
or descriptive cavity metrics. The bounds are conservative under the stated
binomial model, not a distribution-free guarantee for correlated production.

Zero observed failures do not imply zero failure probability. For `n` zero-failure
trials, the one-sided upper bound at tail `alpha` is `1-alpha**(1/n)`. A bounded
72-group synthetic capacity test (36 successful defective groups, 36 nondefective
groups with no holds) demonstrates that the default 90% detection / 10% false-hold
criteria are reachable within the implementation's finite profile. Its success
remains `SYNTHETIC_ONLY`, not evidence about a physical detector.

## Review criteria and authority

A study checks minimum defective/nondefective group counts, both conservative
primary confidence bounds, finalized captures, usable independent outcomes, and
positive/negative class coverage in every configured cavity.

`CRITERIA_MET_FOR_REVIEW` means only that the supplied study meets its declared
numerical/evidence criteria. Synthetic studies always have overall status
`SYNTHETIC_ONLY`, including those that meet their numerical criteria. Declaring
`retained_observation` does not authenticate a source or establish commissioning.
A failing study remains visible, including a review queue for unresolved labels,
defects without timely alarms and nondefective-target samples on hold.

Every result keeps:

```text
physical_qualification = not_established
source_authentication  = not_established
hardware_actuation     = not_performed
physical_containment   = not_performed
state_admission        = not_performed
shipment_release       = not_authorized
action_authorized      = false
```

Changing/re-hashing cached metrics or an apparent perfect score fails verification:
the study and every supplied capture are recomputed. A coherent rewriting of all
unanchored sources remains outside authenticity protection.

## Authored adverse demonstration

The default fixture deliberately contains 16 enrolled parts from eight two-cavity
cycles. Seven cycle journals are supplied; the eighth is visibly missing. Labels
include four defects, eleven target-nondefective parts, and one indeterminate part.

Among the four independently *declared synthetic* defective cases:

| Case | Journal evidence | Study consequence |
| --- | --- | --- |
| `case.0.2` | Valid early pressure deviation followed by unknown uncertainty | Alarm, timely, held. |
| `case.2.2` | Sensor fault followed by recovery, with no creditable process deviation | Abstention, held, not counted as successful defect detection. |
| `case.4.1` | Normal process signature but an independently declared defective outcome | No alarm, no stream hold; explicit missed-defect review item. |
| `case.5.2` | Valid pressure deviation received after the deadline | Alarm, late, held. |

Two of four defect cases have an alarm; only one of four has a timely alarm. Three
of four are held by the stream, which must not be misreported as 75% timely defect
detection. Among eleven nondefective-target cases, two have process alarms and
four are held (including the two missing-capture cases). These are deliberately
adverse software fixtures, **not physical performance estimates**.

The SQLite example imports the seven supplied journals, reopens the store, resolves
their exact enrolled heads, and reproduces the same study result without changing
the database bytes. Uncertainty declarations and inspection records remain separate
from the stored process evidence. Missing enrollment members remain missing.

## Run

From `focused_snapshot/` in the ZIP, in PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m ciw.toolpoint_validation_cli demo --output new-validation.json
python -m ciw.toolpoint_validation_cli verify new-validation.json
python -m ciw.toolpoint_validation_cli summary new-validation.json
```

On a POSIX shell, use `export PYTHONPATH=src` before the same commands. PowerShell
syntax is provided for the user's environment; Windows execution was not tested.

To supply actual retained study records:

```text
python -m ciw.toolpoint_validation_cli example --output study-request.json
# Replace the authored synthetic study with genuinely retained, correctly labeled evidence.
python -m ciw.toolpoint_validation_cli evaluate study-request.json --output study-report.json
```

Use the optional `--expected-protocol-ref` and `--expected-manifest-ref` arguments
with anchors obtained from separately trusted retained state. Copying an anchor
from the same untrusted artifact does not establish freshness or authenticity.

To evaluate enrolled captures already in a durable store:

```text
python -m ciw.toolpoint_validation_cli evaluate-store examples/detector-validation/store-template.json --database validation/detector-validation/synthetic-captures.sqlite3 --bindings examples/detector-validation/store-bindings.json --output new-store-study.json
```

Each selected store head is independently replayed with its exact specification
and head anchor. This is a pinned retrospective cohort, not a multi-stream live
snapshot or a claim that no newer captures exist. Newer conflicting heads are
refused rather than silently replacing the enrolled source.

Output JSON uses the existing atomic/create-only file helper. Exit 2 means a
synthetic-only study or unmet criteria, even when a truthful report was saved.
Verification exits 0 for successful replay, not product conformance. Malformed
input, mismatched anchors and publication failure exit 1. Use new output names.

## Validation and limits

Local validation results are retained under `validation/`. New tests exercise:
independent SciPy confidence comparisons, exact enumeration of Bernoulli outcomes,
a published NIST example, zero failures, packet timing, tiny uncertainty margins,
missing labels/captures, target-rule selection, data leakage, circular labeling,
group aggregation, mapping faults, altered/rehashed reports, SQLite reopen/export,
and actual subprocess CLI journeys. The old baseline tests are rerun unchanged.

Limits: 256 enrolled parts, 128 retained streams, 256 group trials in the standalone
binomial primitive, and the existing 2 MiB / tree / journal bounds. Effective
capacity depends on retained trace length and packet history. A bounded request
can still exceed the output budget after report wrapping or pretty-printing;
publication refuses rather than truncating evidence. No existing budget is raised.
The module continues to replay cumulative histories and does not claim real-time
throughput, unlimited cohort capacity, or certified statistics software.

No physical dataset was obtained in this increment. All demonstration inspection
labels are authored synthetic data. Full current manufacturing-v2 integration,
full repository regression, installed wheel/top-level `net` routing, Windows,
hosted CI, live acquisition, external label authentication, actual false-hold and
missed-defect rates, physical sorting and controller timing remain unverified.

## Repository integration

This is an **additive change set after the durable-store upgrade**. No original
module, license, dependency pin, native evaluator or canonical identity encoder
was edited. Use the incremental patch on a checkout containing that dependency.
The bundle also carries the prior combined stream/store addition patch for the
inspected PR #177 baseline and a combined addition patch including this increment.

Do not copy the focused snapshot over a newer repository: its earlier manufacturing
files are included for reproducibility and have overlapping names with newer
implementations. PR #177 was inspected at `dd8782a9df195a79769cbe9e8d63579578dc248e`;
it was draft, unmerged and reported not mergeable during this increment. No push,
merge or attempt to overwrite the repository was made.

## Primary methodological references

These inform the statistics and experimental-design boundaries, not the fixture's
physical validity. Inspected 2026-10-05:

- NIST/SEMATECH e-Handbook, 7.2.4.1, exact binomial confidence intervals and its
  4/20, 90% example: https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm
- SciPy `BinomTestResult.proportion_ci`, exact Clopper–Pearson method:
  https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html
- scikit-learn, cross-validation and leakage from threshold/parameter selection;
  grouped evaluation for dependent samples:
  https://scikit-learn.org/stable/modules/cross_validation.html

The implementation's integer bisection is original code; it does not copy a
third-party numerical routine or introduce a SciPy runtime dependency.
