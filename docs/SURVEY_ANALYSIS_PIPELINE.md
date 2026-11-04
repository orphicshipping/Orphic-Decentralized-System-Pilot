# Synthetic survey pipeline and held analysis

`ciw.survey_analysis_pipeline.SurveyAnalysisPipeline` implements one local
Domain H journey: an existing host-admitted synthetic survey capture, a fixed
SQLite selection, the existing native analysis/uncertainty providers, durable
attempt custody, and an immutable analysis artifact held for review. It adds no
MCP tools, service routes, SQL/dbt executor, external source connector, database
grant or hosted configuration. The general six-profile programme remains wider
than this one implemented workflow. The separate [canonical principal-bound profile](SURVEY_PIPELINE_MCP.md)
now adds four fixed-plan MCP tools with current operation grants, audit scope
barriers and paged held-artifact reads. The library remains a trusted host API.

## Fixed, versioned data flow

The trusted launcher binds an existing `OfflineSurveyFlow`, exact capture,
private pipeline directory, reviewed uncertainty model and current admission
callback. The capture must match the existing closed `SURVEY_TABLE` schema,
source version, tenant/principal, frame, metre units, declared clock/time,
instrument and calibration references. Source schema/control/freshness checks
must be nominal and no sticky drift hold may exist. A host-admitted capture is
still an observation: this does not establish source truth or NSL admission.

The only registered pipeline is `survey-observation-height-summary.v1`, version
1.0.0. It accepts at most 32 source rows and 32 KiB of original bytes. A real
in-memory SQLite table retains exact canonical row payloads. Fixed SQL selects
`role = 'observation'` and orders by the point identifier, with at most 31 output
rows. No SQL, extension, file, attachment, network address or executable can be
provided by a request or saved artifact. The transform is lossless selection;
it performs no coordinate/time/unit conversion, interpolation or imputation.

The output has an explicit derived dataset version binding its complete bytes,
the unchanged table schema, input dataset and exact plan. That version is a
derived artifact identity, not a newly registered or automatically admitted
source. `DataAnalysisHost` analyzes the original capture with exactly those
selected point keys, so the source's control rows remain available for current
calibration checks without entering the observation statistic.

Actual arithmetic uses `adapters.oscillator.summarize_values` and
`numerical_workflow.execute('uncertainty.propagate.v1', ...)`, including the
existing numerical output validator. This composition does not replace those
providers with new calculation stubs. The reviewed model explicitly declares
independent height errors and source-supplied standard uncertainties. It does
not infer independence, sampling completeness, physical calibration or source
uncertainty from observed scatter.

For the self-authored example, ASSET-A and ASSET-B have heights 10.125 m and
10.25 m, with declared standard uncertainties 0.012 m and 0.015 m. Their mean
is 10.1875 m. Under the declared independence model:

```
variance(mean) = (0.012² + 0.015²) / 4 = 0.00009225 m²
```

The propagated standard uncertainty is about 0.009605 m. It is not a confidence
interval. Minimum, maximum and RMS retain explicit `not_qualified` uncertainty
statuses; sampling/source uncertainty remains unestablished. The exact retained
sample count does not establish population completeness. Binary64 results are
checked against independent Decimal arithmetic with stated numerical tolerance.

## Current admission, custody and idempotence

Existing `PrincipalPolicy`, `DataInstrumentLedger` and `DatasetBinding` enforce
current source/context checks. The current principal must already have
`statistics.v1` and `uncertainty.propagate.v1` scientific grants. The pipeline's
host callback must return exact `True` anew around dispatch, transformation,
analysis, retention, completion and disclosure. Binding/code checks also run
after callbacks and I/O. Callback errors refuse without exposing their text.
No stored grant, old policy JSON or admission-context hash supplies permission.

The plan pins the fixed definition, input/output schemas, source/capture/dataset
binding, uncertainty model, implementation files, analysis interface, numerical
runtime and Python/NumPy/SQLite declarations. Attempts record their queue job,
request hash, fence and original host-context commitment. That commitment is
historical audit data, not authority, authenticated time or ledger admission.

The existing private `StoreQueue` and kernel leases provide durable local
custody. At most 16 attempts are allowed, with one pending/running attempt.
An additional pipeline-owner lease prevents simultaneous dispatch. A repeated
completed attempt reopens the exact retained artifact, without new SQL,
calculation, source assessment or retention occurrence. An explicitly requested
new attempt is a new audit occurrence; its reproducible value commitment stays
equal when inputs, model and implementation stay equal.

An interrupted worker becomes `outcome_unknown`. Lost retention or completion
acknowledgements must be inspected; a completion may already be committed.
Unknown attempts are never resumed, and they hold fresh attempts pending
operator review. No clear/retry/approve endpoint is provided. Do not conceal an
unknown outcome by selecting a new directory or identity. A new host binding
after reconciliation requires its own explicit operator decision. Kernel leases
and the local owner store do not establish multi-host fencing or physical
exactly-once effects.

Artifacts use the existing owner-scoped `Store` with eviction disabled. They
retain original raw bytes and capture receipt, source/schema/binding/model/code
identities, exact SQL output/version, native analysis receipt, covariance and
unsupported-uncertainty declarations. The reproducible value commitment excludes
attempt identity and fresh assessment occurrences; the complete artifact keeps
both. Store capacity failure never evicts earlier evidence. Every output stays
`held` with `pending_human_review` and all source/time/state authority claims
unestablished.

## Reproduce and independently inspect

The [example](../examples/domain-h-survey-analysis/README.md) creates only a new
private synthetic fixture. Its fixed test policy is supplied by fresh launcher
code; saved policy JSON is inert evidence, never recovered authority. It creates
two deliberate attempts and proves that same-attempt replay creates no third
execution. No external source or device is connected.

```sh
PYTHONPATH=src python examples/domain-h-survey-analysis/run.py \
  --output /private/new-synthetic-pilot
```

`validate_artifact(value, expected_ref=...)` performs pure retained-content and
algebraic checks. Historical inspection validates the exported canonical byte
pin before parsing and labels its result as historical, with no restored current
authority. It opens no live source, queue, provider or authorization callback.

The separate stdlib-only `verify.py` imports no ODSP modules. Given separately
trusted first/rerun artifact pins, it checks raw capture/event chains, retained
owner bundles, both queue requests/completions, schemas, source/plan/code links,
SQL selection via a separate Python predicate, original time/frame/unit fields,
reproducible commitments and Decimal statistics/uncertainty. Optional explicit
code-root checks hash the actual implementation files without executing them.
This independent check is local evidence, not an external audit or signature.

```sh
python examples/domain-h-survey-analysis/verify.py \
  --pilot-root /private/new-synthetic-pilot \
  --expected-first-ref sha256:TRUSTED_FIRST_ARTIFACT \
  --expected-rerun-ref sha256:TRUSTED_RERUN_ARTIFACT \
  --code-root /approved/checkout/src/ciw
```

The tests exercise the real providers, same-attempt restart, explicit reruns,
concurrency, lost-worker/acknowledgement uncertainty, current revocation, late
admission loss, schema/control/freshness refusal, historical inspection and
contradictory rehashed output rejection. All test sources, policies and review
declarations are synthetic. Existing host security settings are unchanged.

## Remaining qualification boundaries

This is one local workflow verified against source modules. Its fixed-plan
canonical MCP profile is locally exercised; it is not a deployed server, arbitrary
SQL/dbt engine, signed workflow registry, shared cache or hosted Domain H platform.
Its local queue does not replace the separately
qualified PostgreSQL/NATS coordinator or expand the prior cross-store drill.
The new pipeline's retention/custody has not been added to that recovery set.
The [dedicated distributed adapter](DISTRIBUTED_SURVEY_PIPELINE.md) subsequently
adds local PostgreSQL/JetStream custody for its canonical execution, with claim
checks through computation/retention and no resend after an unknown outcome.
Its SQL-only cold cutover retains existing local evidence; it does not establish
an atomic cross-store transaction, shared remote retention or hosted recovery.

Actual company-source clearance, authenticated TBRT time, NSL admission,
physically traceable uncertainty, hosted operation/restore, object storage and
physical instrument qualification remain open. The full scientific baseline
and earlier programme gates are not cleared by these selected tests. Nothing
here authorizes equipment action, publication or deployment.
