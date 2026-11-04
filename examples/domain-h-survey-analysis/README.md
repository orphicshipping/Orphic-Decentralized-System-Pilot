# One synthetic Domain H survey analysis

Run the [local pipeline](../../docs/SURVEY_ANALYSIS_PIPELINE.md) from an installed
candidate or explicitly selected checkout:

```sh
PYTHONPATH=src python examples/domain-h-survey-analysis/run.py \
  --output /private/new-synthetic-pilot
```

Use a new private directory and private umask. The example creates self-authored
survey controls/observations, a current host-only test policy, a data-instrument
ledger, a durable pipeline queue and owner-scoped retained artifacts. It calls
no external service or device. Real rights, source authentication, clock
authentication, NSL admission and physical calibration remain unestablished.

`analysis-first.json` and `analysis-rerun.json` retain exact source and schema
identities, the fixed SQL selection, code/runtime commitments, native analysis,
declared uncertainty and fresh attempt lineage. `journey.json` records their
pins. Same-attempt replay reuses the first artifact; the explicit rerun is a
different occurrence with an identical reproducible value commitment. Both
remain held for human review.

For historical content inspection, supply the separately trusted first-artifact
pin printed by the creation command:

```sh
PYTHONPATH=src python examples/domain-h-survey-analysis/run.py \
  --inspect /private/new-synthetic-pilot/analysis-first.json \
  --expected-ref sha256:TRUSTED_FIRST_ARTIFACT
```

For independent raw custody and Decimal checks, run `verify.py` using both
trusted pins, as shown in the runbook. It needs only Python's standard library;
it does not import ODSP, restore old permissions or dispatch calculations.
Do not load a historical policy file as current authority or treat a matching
hash as evidence admission.
