# NET tool-point QC: outcome-validation bench

For current installed commands, qualification scope and snapshot compatibility, see
[Native QC integration](TOOLPOINT_UPGRADES_INTEGRATION.md).

## Purpose and implementation boundary

This additive module set evaluates the existing QC instrument against a separately
recorded inspection endpoint. It does not add another defect classifier, retune
process limits, replace manufacturing genealogy, or release parts. The previous
capture journal, durable store, QC evaluators, manufacturing reducer and content
identity encoder are unchanged.

The implemented path is:

```text
Frozen study protocol and complete declared component cohort
    -> verified manufacturing cycle/cavity/serial/lot identities
    -> verified process journal prefixes available by the decision deadline
    -> separately declared, scope-bound post-process inspection outcomes
    -> detection / missed-nonconformance / precautionary-hold accounting
    -> conditional lot-level uncertainty bounds and review evidence
```

The purpose is to expose a detector's limitations, not make its results look good.
A final inspection result or downstream scrap MUST NOT be fed back into the
process-detector prediction being evaluated. Likewise, a missing sensor that causes
a precautionary hold is not credited as a successful process-deviation detection.

## Files and dependencies

| Added file | Responsibility |
| --- | --- |
| `src/ciw/toolpoint_outcome_bench.py` | Cohort validation, identity joins, deadline-prefix replay, independent outcomes, metrics and verification. |
| `src/ciw/toolpoint_outcome_stats.py` | Bounded one-sided exact-binomial upper-bound construction with integer-exact comparisons. |
| `src/ciw/toolpoint_outcome_example.py` | Four explicitly synthetic scenarios; no physical measurements or qualified thresholds. |
| `src/ciw/toolpoint_outcome_cli.py` | Example, demo, run, summary and verify source-module commands. |
| `tests/test_toolpoint_outcome_bench.py` | Independent numerical checks, mutation/refusal cases, real CLI and durable-store round trip. |

Runtime uses the Python standard library and existing NET modules. Pytest runs
tests. SciPy is used only for optional independent numerical reference tests; those
individual cases skip when SciPy is absent, not the entire test module.

The first bench profile accepts native `ciw.toolpoint-monitoring-plan.v1` journals,
because it binds their explicit cavity-to-serial maps to manufacturing outputs.
Both existing journal/evaluator profiles remain untouched. The legacy profile is
not silently translated into native mapping semantics.

## Study contract and cohort integrity

`ciw.toolpoint-outcome-protocol.v1` identifies the part revision, one named inspection
characteristic, its specification reference and measurement condition. A study does
not claim universal part conformity from one characteristic. Multiple characteristics
require separately scoped studies and an explicitly reviewed multiplicity policy.

The protocol also retains exact accepted plan hashes, the declared freeze time and
freeze evidence reference, the component enrollment, decision deadline, excluded
development lots/cycles/evidence, a sampling assumption, and statistical criteria.

Every formed component in the supplied manufacturing reports must be enrolled exactly
once, using the manufacturing request hash plus serial. Assemblies do not become
additional independent component outcomes. Missing/extra enrollment, duplicate cycle
evidence, contradictory cavity maps, stale configurations, a different part revision,
mixed synthetic/observational semantics, and future records are refused.

A complete supplied cohort is not proof of a complete factory census. The caller must
retain the protocol hash independently and establish that the supplied manufacturing
reports cover the intended population. A coherent replacement of both the protocol
and all source declarations cannot be detected as fraud without an external anchor.
`verify(..., expected_protocol_ref=...)` checks an independently supplied protocol
anchor; a hash copied from the same untrusted report adds no authenticity.

One material-lot ID is one declared lot across all supplied reports. A lot used for
development cannot also enter this held-out study. Acquisition and outcome evidence
references cannot be silently reused from the declared development set. Outcome
result references cannot equal the retained process features or reference basis.
These checks detect explicit overlap; they do not discover undisclosed training data
or authenticate an author's declaration that a protocol was frozen prospectively.

## Predictions use evidence available by the deadline

For each cycle, the benchmark first verifies the complete retained journal. It then
reconstructs only updates whose declared receipt time is at or before:

```text
cycle start + protocol.decision_deadline_after_start_s
```

Later updates are counted as excluded and cannot improve the earlier prediction.
The unchanged journal still owns prefix evaluation and latched fault evidence.
A prefix must be finalized and clear before the deadline to yield `CLEAR`.

| Bench decision | Meaning |
| --- | --- |
| `PROCESS_ALARM` | A valid, mapped retained sampled violation or evaluator deviation exists by the deadline. |
| `PRECAUTIONARY_HOLD` | Missing/late/incomplete evidence, uncertain mapping or a non-process fault prevents a clear view. |
| `CLEAR` | Final process evidence identifies no process hold by the deadline, within the monitor's declared scope. |

`CLEAR` is not product acceptance, shipping release, or machine authorization. An
alarm is not a proven defect mechanism. A precautionary hold may be operationally
necessary even when the independent inspection endpoint happens to conform.

The first-alarm timing field measures the difference between declared receipt and
cycle-start timestamps. It is NOT measured sensor latency, physical defect-onset
latency, a PLC deadline guarantee, or proof that the shot could still be corrected.

## Independent outcome records

Each record binds one enrolled unit to the same part, characteristic, specification,
condition and source semantics. It retains its inspection time, label, method,
calibration, evidence and independence basis, plus an explicit blinded-to-QC declaration.

A missing result, missing supporting reference, unblinded outcome or indeterminate
inspection is `UNKNOWN`. It is not converted to conforming and is not silently
removed from cohort reporting. Foreign units, duplicate outcomes, scope substitutions
and impossible time ordering are refused. The source outcome record remains retained.

The bench validates declarations and joins; it does not fetch external calibration
certificates, calculate a new metrology result, establish blinding, or authenticate
inspection. The label must come from an appropriately qualified independent method.
Existing manufacturing inspection results remain intact but are not the predictor.

## Metrics: detection and containment are different endpoints

The complete 3-by-3 table retains outcome rows `NONCONFORMING`, `CONFORMING`, `UNKNOWN`
and decision columns `PROCESS_ALARM`, `PRECAUTIONARY_HOLD`, `CLEAR`.

Descriptive part rates are:

```text
process-alarm sensitivity = nonconforming units with PROCESS_ALARM / known nonconforming units
process-alarm false-positive rate = conforming units with PROCESS_ALARM / known conforming units
containment-miss rate = nonconforming units with CLEAR / known nonconforming units
endpoint-specific false-hold rate = conforming units not CLEAR / known conforming units
```

Each rate reports its numerator and denominator. No relevant outcomes means no rate
(`null`), not zero. Unknown outcomes remain explicitly visible and prevent satisfying
study criteria. Holding every unit solely for missing evidence gives zero containment misses but
zero process-alarm sensitivity and 100% endpoint-specific false holds on conforming units.

### Conditional lot-level bounds

Part percentages above are descriptive: correlated cavities, repeats and prefixes do
not become independent binomial trials. Statistical upper bounds use material-lot
endpoints:

- Among lots with known nonconforming units: did any nonconforming unit receive CLEAR?
- Among lots with known conforming units: did any conforming unit receive a hold?

These are probabilities of **any such event in a sampled lot**, not per-part defect
rates. Their interpretation depends on the prespecified within-lot sampling scheme,
lot size distribution and target population. They cannot be compared casually across
different lot-sampling schemes.

Bounds require `iid_lot_sampling_declared=true` and a sampling-basis reference. That
means the caller declares independent, identically distributed lot-endpoint trials;
this software does not establish that assumption. With no such declaration the
bench provides descriptive counts but no inferential bound.

The upper endpoint solves the one-sided binomial inversion

```text
sum[i=0..k] choose(n,i) p^i (1-p)^(n-i) = alpha
```

For two simultaneous upper-rate criteria, the protocol's total error allowance is
split equally (Bonferroni). At family confidence 0.95 each endpoint uses alpha 0.025.
The code performs 72 bisections with exact integer/rational CDF comparisons and rounds
the reported decimal endpoint outward. The root is enclosed, not asserted to have an
exact finite decimal representation. The finite kernel accepts at most 128 lot trials.

Zero observed events does not imply zero risk. With zero events in 20 IID lot trials,
the upper bound at alpha 0.025 is about 0.1684. This is an illustrative calculation,
not a claim about this instrument's physical performance.

`DECLARED_CRITERIA_SATISFIED` means only that the supplied records satisfy the finite
numerical criteria under declared sampling assumptions. `qualification` ALWAYS stays
`NOT_ESTABLISHED`; synthetic sources ALWAYS remain `SYNTHETIC_ONLY`, even when loose
numerical criteria are satisfied. No thresholds, reference curves or limits change.

## Synthetic end-to-end demonstrations

The default uses the existing two-cycle manufacturing example and separately authored
internal-integrity labels. They are not fabricated physical observations.

| Serial | Process-only view by deadline | Authored independent endpoint |
| --- | --- | --- |
| p001 | CLEAR | CONFORMING |
| p002 | PROCESS_ALARM, first accepted at 0.61 seconds after declared start | NONCONFORMING |
| p003 | PRECAUTIONARY_HOLD after an early sensor fault | CONFORMING |
| p004 | CLEAR | NONCONFORMING |

The fixture deliberately reports 1/2 alarm sensitivity, 1/2 containment misses and
1/2 endpoint-specific false holds. These numbers exercise the accounting, not a
measured detector specification. All four parts share one material lot: they count
as one lot trial for each applicable lot endpoint, not four independent trials.

The pre-existing manufacturing record calls p002's dimension PASS and p004 SCRAPPED.
Neither fact is allowed to turn the process prediction into the independent endpoint.
The bench therefore exposes p004 as a process miss even though later inspection and
scrap prevented it from being delivered. It does not relabel p002's dimensional result.

`missing-inspection` keeps the missing outcome visible. `late-alarm` demonstrates that
an alarm received after the cutoff does not count as timely detection. `all-clear`
demonstrates missed nonconformances when no process alarm is raised.

The tested durable-store journey imports the original two journals, reopens SQLite,
exports verified journals and obtains exactly the same benchmark result. Storage
occurrence identities may differ; scientific decisions and their receipts do not.

## Run

From the included `focused_snapshot` directory in PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m ciw.toolpoint_outcome_cli demo --output outcome-demo.json
python -m ciw.toolpoint_outcome_cli summary outcome-demo.json
python -m ciw.toolpoint_outcome_cli verify outcome-demo.json
```

The synthetic demo and summary exit 2 deliberately. Verification exits 0 on successful
replay, including truthful reports containing misses or failed study criteria. Exit 1
means malformed input, failed verification or publication failure. Existing outputs
are never overwritten. In a POSIX shell use `export PYTHONPATH=src`.

For a retained study, create an example request, replace its explicitly synthetic
records with genuinely acquired and scope-bound evidence, retain the completed
protocol hash independently, then use `run REQUEST --output REPORT`. Merely changing
`source_kind` does not establish physical evidence. The tool checks source consistency
but cannot authenticate undeclared substitutions.

## Verification, compatibility and remaining work

The deliverable includes raw focused and regression logs, numerical cross-checks,
coverage, exact source hashes and synthetic reports. Report verification replays the
underlying manufacturing report, every stream prefix and the outcome analysis. Changing
summary fields and recomputing hashes does not bypass this computation. Execution,
content, operation, protocol and verification identities remain separate.

The inspected GitHub PR #177 remains at `dd8782a9df195a79769cbe9e8d63579578dc248e`.
The focused snapshot contains the earlier manufacturing implementation and the pinned
native evaluator. This increment does not claim the current manufacturing-v2 full
repository journey, installed wheel, top-level `net` dispatcher, Windows, hosted CI,
live sensors, physical discrimination, factory sampling independence, authentic
inspection, or certified real-time response. Direct container access to GitHub failed
DNS resolution; no full checkout, push or merge was performed.

Apply the new additive patch after the prior stream and durable-store additions. The
bundle also supplies their combined prerequisite patch. Never copy the entire focused
snapshot over a newer repository: earlier manufacturing filenames overlap newer code.
The current numerical profiles, model/physics providers, dependency pins, licenses,
state-admission ownership, machine authority and shipping restrictions remain unchanged.

## Primary methodological references inspected 2026-10-05

1. NIST/SEMATECH, section 7.2.4.1, binomial confidence intervals and exact inversion:
   https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm
2. Scikit-learn, Common pitfalls, section on test-data leakage:
   https://scikit-learn.org/stable/common_pitfalls.html
3. SciPy, BinomTestResult.proportion_ci, exact binomial intervals:
   https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html

These support the statistical and evaluation methodology, not empirical validation of
NET or of any polymer process. Local SciPy is an optional test oracle, not a new
runtime requirement or a claim that the repository's pinned environment was qualified.

## Recorded local validation

- Full focused suite: **940 passed, 0 failed, 1 skipped**, in 42.25 seconds.
- New benchmark tests: **142 passed**; prior baseline: **798 passed, 1 skipped**.
- Branch-enabled coverage: **97% across the four new runtime modules only**
  (413 statements, 202 branches; not whole-repository coverage).
- 45 optional SciPy beta-quantile comparisons, independently evaluated exact
  rational CDF inequalities, closed-form zero-event limits, and finite binomial
  coverage grids exercised the confidence-bound implementation.
- Deadline, cohort, identity, missing-outcome, label leakage, mapping, observation
  history, immutable file output, actual CLI and durable-store round-trip tests ran.

Two foreground combined invocations were interrupted by the execution environment
before completion; neither produced an assertion failure. The complete suite was
then run as one monitored process and finished with exit code 0. The retained interrupted-run
log is not counted as a passing run; the first interruption is recorded in metadata. New CLI subprocess tests have an
explicit 20-second timeout. No production defect is claimed to have been fixed by
that test-harness timeout.

Environment: CPython 3.13.5 / Linux, pytest 9.0.2, SciPy 1.17.0 (test oracle),
SQLite 3.46.1. The existing full-polymer-dispatcher test is skipped because its
unrelated repository dependencies are absent from this focused snapshot.
