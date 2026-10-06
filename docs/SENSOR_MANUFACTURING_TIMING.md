# Manufacturing timing: observations to reviewed model updates

This increment adds `engineering.characterize-manufacturing-time.v1` to the existing
manufacturing registry, payload validation, Session, replay, export and CLI. It
consumes the original `ciw.observation-stream.v1` duration observations. No new
acquisition wire protocol, evidence ledger, manufacturing reducer or machine driver is
introduced. v1/v2 planning remains available and its returned data is preserved.

## The executable loop

Prepare a trial protocol -> collect explicitly identified elapsed durations ->
retain native observations -> identify setup/batch time -> check held-out errors ->
review a fixed-dispatch prediction update or design a more informative experiment.

The fit is `elapsed_seconds = setup_seconds + batch_count * per_batch_seconds`.
An observation represents one setup followed by the declared number of batches,
excluding queue time. Every trial must reset setup. These are protocol declarations,
not authenticated machine facts. Resource, batch size, operation definition,
measurement model, native entity, clock, frame, source semantics and procedure-byte
references must agree. Receipt timestamps are never subtracted to invent elapsed
processing time. Measurement uncertainty is retained without being silently
converted into zero error, independent noise or a regression confidence interval.

## Prepare instructions before observations exist

The existing source inventory and request remain unchanged. A new file is written
outside that inventory. Counts, source identities and error limit are explicit inputs.

```sh
python -m ciw.sensor_manufacturing_cli timing-protocol \
  demonstrations/single-identified/original-inputs \
  demonstrations/single-identified/original-request.json \
  --operation-id inspect-module \
  --resource-id PROPOSED-one-site-inspection-fixture \
  --stream-path timing-observations.json \
  --model-id elapsed-timer --clock-id local-duration-clock --frame station.elapsed \
  --semantics observed --train-batches 1 6 12 --holdout-batches 3 9 \
  --max-holdout-error-s 3 --output proposed-protocol.json
```

The command does not open a device or create observations. Choosing `observed`
is a requested acquisition mode, not evidence that measurements occurred. A
real recording source must provide its original native duration observations.
The tool requires at least three training trials with two distinct batch counts,
at least two holdout trials, training support covering the target and holdout
counts, and an explicitly eligible resource. It rejects silent extrapolation,
receipt-time semantics and an uninformative single-batch-count design.

A manually retained historical protocol with inadequate variation can still be
reviewed by the characterization operation. It reports NOT_IDENTIFIABLE instead
of inventing separately identified setup and batch time.

## Retained inputs

Add three explicitly declared artifacts to a fresh sensor-project input copy:

* `timing-protocol.json`, role `model.input`: `ciw.manufacturing-timing-protocol.v1`;
  exact original manufacturing definition hash, duration scope/setup policy and
  one to sixteen operation/resource series. Each series declares source stream,
  model/clock/frame/semantics, exact resource/operation references, fixed batch
  size, train/holdout trials and maximum held-out absolute error.
* `timing-observations.json`, role `observations.native`: unchanged NET observation
  stream. Add it to the request's existing `native_streams`. Quantity is `duration`,
  unit is exactly `s`, value is a finite nonnegative scalar. Entity identity equals
  the protocol trial ID. Every original observation must reference both the exact
  protocol bytes and original manufacturing-definition bytes in `provenance.sources`.
* `manufacturing-timing-study.json`, role `model.input`: mapping only, with fields
  `schema: ciw.manufacturing-timing-study.v1`, `protocol_path` and `bindings`, where
  each key is a planned trial ID and each value its original native record digest.

The protocol is separate from the mapping to avoid a circular hash requirement:
its bytes can exist before observations, and the mapping is filled afterwards.
Hash binding does not authenticate when the protocol was issued or make malicious
rewriting impossible. No prospective-registration service is claimed.

All dedicated timing observations must be accounted for; duplicates, missing
trials, unplanned rows or dropped outliers refuse. No record can appear in both
training and holdout. Other original non-timing streams remain available to the
existing product-inspection workflow. Total timing bindings are bounded to512,
with64 trials per series, within the inherited128-file/2MiB source limit. Estimated
or reference-valued durations cannot silently enter as observed measurements.
Observed semantics require the existing REPLAY project mode; synthetic cases stay
SIMULATION/COUNTERFACTUAL as appropriate. Provenance labels alone do not authenticate
physical measurements or supplier capabilities.

## Run, inspect and recover

```sh
python -m ciw.sensor_manufacturing_cli run \
  demonstrations/single-identified/inputs \
  demonstrations/single-identified/request.json \
  --characterize-timing --output-dir new-timing-session
python -m ciw.sensor_manufacturing_cli report new-timing-session \
  --output timing-review.html
python -m ciw.sensor_manufacturing_cli inspect new-timing-session
python -m ciw.sensor_manufacturing_cli replay new-timing-session \
  --output-dir replayed-timing-session
python -m ciw.sensor_manufacturing_cli export-inputs new-timing-session \
  --output-dir original-timing-inputs
```

`--characterize-timing` and `--analyze` are mutually exclusive explicit selections.
Omitting both flags preserves v1. Characterization retains the original v2 baseline/optimization
and adds a separate timing result; it does not reinterpret an old v2 result. Native
semantic refusals remain retained in the existing Session. No-clobber and optional
snapshot anchors remain available. Replay preserves source evidence and data while
creating fresh execution/result occurrences. Offline inspection creates no new run.
The ordinary v2 process comparator still requires v2 results; it is not weakened to
call a prediction correction a process speedup.

## Numerical and scientific interpretation

The two-parameter fit uses exact rational arithmetic on the source decimal values.
Rank deficiency is explicit. Negative fitted setup/batch parameters are retained
as model failures, not clamped into plausible-looking values. Exact residual
normal-equation checks accompany the calculation. Separate Decimal-centered
regression tests check the arithmetic. These are software checks, not an independent
physical audit or a formal verification identity.

All training and held-out observations and their original uncertainty records remain
attached. Held-out observations do not enter fitting. The result includes residuals,
training MSE, baseline/fitted holdout bias, MAE, MSE, RMSE and maximum absolute error.
At least two held-out trials are required before a candidate is eligible. Eligibility
also requires nonnegative parameters, support containing all prediction counts, the
predeclared maximum error criterion, no worse held-out MSE than the original model,
and the exact characterized resource selected by the baseline dispatch.

This is an unweighted descriptive model. Sample dependence is not assumed absent;
noise distribution, coefficient covariance, propagated metrological uncertainty,
prediction intervals and population coverage remain unspecified. Supplied native
covariance is preserved but not propagated. Do not mistake sample spread or low
RMSE for a guarantee, a calibrated timing instrument, or empirical future bounds.
Selecting models repeatedly against the same holdout can bias evaluation; a genuinely
new confirmatory experiment is needed for stronger generalization claims.

Eligible series produce proposed setup/per-batch values and a fixed-dispatch route
prediction. Untouched operations retain their original declared durations; the report
labels mixed fitted/declared provenance. The dispatch is not reoptimized, parameters
are not promoted into the source definition, and no fitted law transfers to another
resource or batch size. Timing correction is not a production improvement. The result
may predict a *slower* route than originally declared.

Inadequate data or failed checks produce a next-experiment recommendation, prioritized
using the existing route's10% duration-intervention leverage. This is not a computed
value of information, experiment cost, optimal sampling plan or equipment instruction.
The next step is a new explicit protocol, not deletion of outliers, silently relaxed
limits or reusing the old held-out subset as fresh evidence.

## Demonstrations and remaining boundaries

The handoff preserves actual retained MEMS A1 CAD/BOM/requirements, but authors
**synthetic duration observations** to exercise the loop. It does not represent
these as shop-floor data. Twelve module inspections remain pending and no physical
units are established. Synthetic cases include identifiable setup/batch laws,
held-out drift and constant batch count. Product files are not published in generic
NET source. No real gauge, electrical article, machine action, calibration campaign,
procurement, shipment release or physical process saving is established here.

The existing installed qualifier and workflow are extended to include the new tests;
no parallel test pipeline or dependency-pin change is introduced. Actual executed
test counts, wheel hashes, package comparison and platform limits are recorded in
the build report. Native source retention is not fresh native EDA qualification.

## Optional correlated measurement uncertainty

The opt-in `--timing-uncertainty` operation preserves this characterization and adds
full-joint covariance propagation, held-out error bands and a local route contribution.
See [MANUFACTURING_TIMING_UNCERTAINTY.md](MANUFACTURING_TIMING_UNCERTAINTY.md). This
original unweighted characterization, its outputs and its invocation remain unchanged.
