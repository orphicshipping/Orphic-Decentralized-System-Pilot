# Manufacturing planning: bounded search, bottlenecks and robust comparison

The original `engineering.manufacturing-plan.v1` remains available with its original
schema, greedy dispatch and nominal inspection semantics. Its returned data is not
replaced. The explicit opt-in `engineering.manufacturing-plan.v2` includes that same
baseline and adds a bounded schedule search, a separately implemented constraint
checker, fixed-dispatch sensitivity and optional declared timing envelopes. It uses
the existing sensor-project input, capability registry and NET Session. No device
call, store, ledger, manufacturing reducer or controller is added.

## Run and compare

```sh
python -m ciw.sensor_manufacturing_cli run inputs request.json \
  --analyze --output-dir analyzed-plan
python -m ciw.sensor_manufacturing_cli report analyzed-plan --output plan.html
python -m ciw.sensor_manufacturing_cli compare baseline-plan alternative-plan \
  --output comparison.html
```

Omit `--analyze` for the unchanged v1 result. Existing inspect/replay/export-inputs
commands handle both versions. Replay preserves the operation version, original
inputs and evidence, with fresh execution and result identities. Inspection may
recompute the bounded analysis to validate its result; it creates no new native
execution or physical acquisition. Historical v1 workspaces remain inspectable.
Comparison is a read-only derived view, not another native scientific result or
formal verification occurrence. It contains original snapshot, evidence, execution
and result references; its `view_ref` identifies the comparison content.

## Exact scope of optimization

For a fixed validated product/process definition, each operation has duration
`setup_s + ceil(quantity / batch_size) * per_batch_s`. The objective is minimum
makespan, not minimum cost, energy, fixture investment or physical defect rate.
Only resources already allowed by the original definition and meeting its declared
capabilities, numeric envelopes, units and batch capacity are eligible.

The deterministic search branches over every precedence-ready operation and its
eligible resource, appending it to that resource's order. Earliest start is the
maximum of resource availability and all predecessor finishes. All operations
are nonpreemptive and whole-lot; durations do not depend on selected resource.
No supplier calendars, transport, machine failures, setup-sequence dependence,
intermediate buffers, unit-transfer batching or dynamic replanning are modeled.

The original greedy choices seed a valid incumbent. Exact rational arithmetic,
using the original decimal spelling of numeric declarations, supplies search times
and bounds. Readable floating-point seconds accompany canonical rational pairs;
the constraint checker checks that the two agree. The search is bounded by node
count, not wall time, so a machine's speed does not change which nodes are visited.

A lower bound propagates precedence completion ignoring future resource contention
and includes mandatory workloads on singly eligible resources. For any fixed
resource ordering, shifting operations to earliest feasible starts cannot increase
makespan. Every feasible ordering has a topological ordering of its precedence and
resource edges, which the complete enumeration covers. Thus exhaustive completion,
or equality of a valid lower bound and incumbent, establishes an optimum **only for
this declared scheduling model**. This argument is not a machine-checked proof.

A node-budget stop instead reports BEST_FOUND_NOT_PROVEN_OPTIMAL, an incumbent,
lower bound and absolute gap. It never calls an unfinished search optimal merely
because a feasible schedule was found. An infeasible definition retains missing
resource reasons and no fabricated successful schedule. Partial search returns a
schedule no worse than the original incumbent; it does not discard the baseline.

`audit_schedule` independently checks operation coverage, explicit resource
selection, capability/envelope/batch constraints, exact durations, predecessor
completion and exclusive-resource occupancy. It does not call the search's bound
or eligibility logic. It is a separate implementation of schedule constraints,
not an optimality verifier, physical calibration, third-party audit or SP1 proof.

## Optional timing declarations

Add the following `model.input` artifact to the unchanged sensor-project request:

```json
{
  "schema": "ciw.manufacturing-analysis-options.v1",
  "definition_ref": "sha256:<exact manufacturing.json hash>",
  "node_budget": 8192,
  "basis": "declared_duration_intervals_not_probability",
  "duration_bounds": {
    "operation-id": {"setup_s": [24, 36], "per_batch_s": [32, 48]}
  }
}
```

The file is named `manufacturing-analysis.json`. Use entries for **every** operation,
not just favorable ones. Each nonnegative interval must contain the corresponding
nominal value. Null `duration_bounds` explicitly means unknown timing variation.
An absent options file uses a 2,048-node budget and unknown timing variation; it
never silently invents exact duration certainty. Allowed budgets are 1–20,000.
The options must bind the exact manufacturing-definition bytes, not only a name.
Changing a definition requires rebinding its options.

After nominal optimization, resource assignment and resource order are frozen.
Replaying that fixed dispatch with all lower and all upper durations gives the
monotone timing envelope for the declared box. It is **not** an interval for the
optimal adaptive schedule, a probability distribution, confidence interval or an
empirically validated performance bound. Unknown physical correlations are not
filled with an independence assumption; the box is conservative over all admitted
combinations, including combinations that may not co-occur physically.

Bottleneck analysis adds resource-order edges to precedence and reports total
slack. A separate one-at-a-time 10% duration reduction reports the resulting
makespan reduction under that same dispatch. A zero-slack task is not necessarily
a profitable intervention: equal parallel critical paths can make an individual
reduction yield no route improvement. The test suite covers that case. These are
finite model interventions, not mathematical derivatives or promised achievable
machine improvements.

## Process-variant comparison without changing product meaning

Each alternative is an ordinary retained product/process definition with its own
analysis Session. There is no generic patch language that can silently modify
requirements, original observations or native CAD. The comparison admits 2–8
distinct snapshots and uses the first as baseline.

A speedup is reported only when native/nonprocess artifact bytes, selected BOM and
requirements, project mode, product, order and inspection associations agree.
Changing the selected requirements file is detected even when both files were
already present in the same source inventory. Scope changes yield
CHANGED_SCOPE_NO_SPEEDUP_CLAIM. This supports process/fixture alternatives for a
fixed deliverable; automatic equivalence across native product redesign is not
claimed. Resource and process changes remain original, retained scenario inputs.

For baseline interval `[L0,U0]` and candidate `[L1,U1]`, the saving interval is
`[L0-U1,U0-L1]`. A strictly positive lower bound indicates a conditional guaranteed
improvement inside those boxes; overlap produces INCONCLUSIVE_OVERLAPPING_BOUNDS.
No cross-scenario correlation, achieved physical saving or economic payback is
inferred. Unknown costs and currencies remain unknown, and minimizing makespan
can select a more expensive resource. No purchase or fabrication action follows.

## Executed MEMS investigation and evidence

The retained 12-unit MEMS A1 cases preserve all 23 original input files; the only
new input is the explicit analysis declaration. The original greedy durations
1,490 s and 1,130 s are already optimal for their declared six-operation models.
The new code correctly does not manufacture another speedup. Their nominal
four-site-fixture difference remains 360 s.

With authored ±20% timing ranges, the baseline is 1,192–1,788 s and the alternative
904–1,356 s. The saving range is -164 to 884 s: nominally faster does not establish
robust improvement. A separate hypothetical ±5% investigation yields a 229–491 s
saving range. These narrower assumptions are not measured knowledge or a claim
that the process already has that repeatability. They illustrate which timing
assumptions require empirical characterization.

The one-site route's inspection operation gives 51 s modeled route reduction for
a 10% duration reduction; fabrication and assembly each give 42 s. In the four-site
route those latter operations become the leading equal opportunities, while
inspection gives 15 s. The existing incompatible-fabricator scenario remains
infeasible. All original inspections are still pending; GaugeLink is unchanged.

The added tests include independent exhaustive Decimal oracles for 32 small
scheduling problems, a greedy allocation trap, budget/gap handling, exact decimal
arithmetic, input order determinism, tampered schedules, critical parallel paths,
comparison scope and real native Session/CLI/replay/reconstruction cases. Source,
noneditable installed and clean-patch qualification are separate executions of the
same cases, not inflated unique-test counts. The retained source tree is the exact
complete PR215 Python package plus these changes, not a full monorepo checkout.
The strict installed qualifier and the dedicated Linux Python3.11/3.13 workflow
require unchanged repository pins. Development-host dependency sharing must be
explicitly opted into and never establishes pinned-release qualification.
