# Synthetic offline survey intake

This runnable internal-use flow retains a self-authored survey export through
`DataInstrumentLedger` and the existing owner-bound `retention.Store`. One control
point and two infrastructure observations carry an explicit local engineering
frame, metre units, source-declared standard height uncertainty, sample clock,
instrument identity and synthetic control-review reference. No Earth coordinate
reference system, geographic transformation or physical instrument is implied.

From the repository with its test environment installed:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B examples/offline-survey/run.py --output /tmp/odsp-survey-example
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B examples/offline-survey/run.py --output /tmp/odsp-survey-example --inspect
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B -m pytest tests/test_offline_survey.py -q
```

The first command requires a new output directory and never overwrites one. The
second reopens the same raw capture, receipts and retained result in a separate
process, under the current local principal/source guard. All addresses are inert
`example.invalid` declarations. The flow opens no network connection, invokes no
companion process and touches no physical device.

`manifest.json` identifies every emitted input and receipt by exact byte hash.
The directory contains the raw `capture.json`, `known-good.json`, source and
binding declarations, synthetic instrument/usage/control evidence, principal
policy, capture and assessment receipts, held result, current inspection view,
local hash-chain check, private SQLite source ledger and owner-retained result.
These are ordinary local custody records. The manifest has no independent trust
anchor; its hashes cannot authenticate a source or resist an administrator
rewriting the entire store.

The launcher-only `ciw.offline_survey.OfflineSurveyFlow` binds source, frame,
clock, instrument and calibration metadata before a capture is inspected.
`capture` checks the approved raw-byte pin, uses the existing immutable attempt
and dataset identities, appends a source assessment and retains a held result.
The same intake attempt returns the same capture receipt; each invocation has a
separate assessment/result occurrence. A conflicting attempt refuses. A full
result store refuses without evicting prior records; the capture and assessment
remain available in the source ledger. Revocation after result retention reports
that disclosure is withheld and supplies a local recovery identity; it does not
claim that the write rolled back or automatically retry.

Schema/control/freshness drift uses the existing persistent source hold. A later
passing capture cannot clear that hold. Survey frame, unit, instrument,
clock and uncertainty mismatches suppress the point view and appear as semantic
issues in the retained result; they do not create a new generic ledger hold type.
Malformed original bytes remain inspectable by their authorized owner. Every
saved result is reconstructed against the exact ledger capture and assessment
before disclosure, with a separate current freshness/hold assessment.

The output is always **held**. Coordinates and uncertainty are reproduced as
reported; no derived measurement, confidence interval, fusion, map projection,
calibration adjustment or geographic conversion is calculated. The synthetic
control comparison and fixed reference clock demonstrate local checks only.
Authenticated source/time evidence, TBRT linkage, verified NSL admission,
traceable physical calibration, actual company exports, human impact-review
acceptance, distributed retention, hosted operation and physical qualification
remain open. No review or admission operation is added by this flow.

Related contracts: [data instruments](../../docs/DOMAIN_H_DATA_SYSTEMS.md),
[offline observation formats](../../docs/OFFLINE_INTAKE.md),
[metrology operations](../../docs/METROLOGY_OPERATIONS.md), and the
[internal-use sequence](../../docs/PROGRAMME_COMPLETION.md).
