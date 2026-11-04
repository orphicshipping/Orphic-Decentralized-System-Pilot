# Private internal inspection and recovery guidance

`ciw.internal_operator.InternalOperatorInspection` binds the existing local
coordinator, survey ledger, observation journal and held projection in trusted
operator-launcher code. It adds no server, MCP tool, principal, credential,
equipment action or hosted configuration. Reports remain held even when their
historical integrity and current source checks pass.

The operator chooses exact existing objects, at most eight job identities, the
survey artifact/capture, the journal job and a separately trusted workbench
manifest pin. Requests and saved reports cannot select a DSN, authorization
callback, principal, source directory, executable or recovery operation. The
bound queue principal must match both source principals and the survey/journal
tenant must match. Identity rebinding or a revoked operator callback withholds
the report. Exact `True` is required; a cached grant or truthy integer fails.

```python
from ciw.internal_operator import InternalOperatorInspection, save_report

# Objects, selectors and pin come from the existing private trusted launcher.
reader = InternalOperatorInspection(
    queue=existing_postgres_queue, job_ids=approved_job_ids,
    survey=existing_survey_flow, survey_record=approved_survey_artifact_and_capture,
    journal=existing_observation_journal, journal_job_id=approved_journal_job,
    workbench=retained_workbench_directory,
    expected_manifest_ref=trusted_manifest_ref,
    authorize=current_operator_inspection_admission,
)
report = reader.snapshot(max_bytes=32768)
report_ref = save_report(new_private_report_path, report)
```

The admission callback receives only `inspect` and the fixed scope hash. It must
enforce current operator access independently; the example does not authenticate
a remote person. Source reads also retain their existing current policy guards.
Report exports are explicit, create-only, mode 0600 files. No parent directory or
existing file is replaced.

## What is checked

- **Coordinator:** a new repeatable-read, read-only PostgreSQL transaction checks
  the existing namespace/format, exact selected requests, hash-linked histories
  and outbox payload commitments. It reports state, fence, database-observed
  expiry, history head and publication flag. It takes no `FOR UPDATE` lock,
  constructs no queue, and never calls claim, recovery, dispatch, completion or
  notification mutation. Request arguments, result bodies, attempt names, DSNs,
  claim UUIDs and provider exception text are omitted. Current per-operation
  authorization is checked around reads. Database time is an observation, not
  authenticated TBRT time. Broker health is explicitly **not inspected**.
- **Survey:** current policy and raw capture/receipt/report bindings are checked
  without appending an assessment. An existing sticky hold or newly observed
  schema/control/freshness failure blocks the dependency in the report. A newly
  observed failure is not silently persisted or cleared by this inspector;
  normal source assessment and verified impact review remain separate actions.
- **Journal:** the existing read-authorized readiness query checks job completion
  and the binding's sticky hold. It performs no acquisition, recovery or hold
  release and appends no observation event. Its existing authorization clock
  high-water bookkeeping may advance; that anti-rollback guard is preserved.
- **Projection:** the complete pinned artifact set, original source histories,
  native observations, joint covariance and retained numerical occurrence are
  validated without creating output files or a new calculation. Current source
  bindings and holds are shown separately from historical integrity. An old
  bridge implementation identity remains the original retained identity; it is
  never relabeled as the reader's code or loaded as executable code.

`Session.inspect_workspace(path)` shares the same saved-workspace validation as
`Session.from_workspace`, returning detached data without constructing a live
session or writing files. Ordinary workspace reopening keeps its existing
behavior. `inspect_workbench(..., playback_output=None)` uses that pure path;
an explicitly supplied new playback directory still supports the prior reopen
journey.

The aggregate is bounded to 64 KiB (default 32 KiB), with explicit selected jobs
and a bounded coordinator read deadline. Host callbacks must also have their own
resource bounds. A component error is reported as unavailable with an integrity,
dependency or access hold, rather than leaked exception details or a cached
success. A late operator revocation withholds the entire report. Independent
stores are checked in sequence; this is not an atomic cross-store snapshot or
a distributed fencing guarantee.

## Operator-run recovery decisions

The report includes data-only instructions selected from these conditions. None
of them is executed by inspection or historical playback.

| Condition | Required operator decision and preserved boundary |
| --- | --- |
| Expired claim | Establish worker quiescence, preserve intent/audit and inspect custody. Existing `queue.recover()` advances fencing and records unknown outcomes; it does not requeue. |
| Unknown outcome | Reconcile the actual effect against retained intent and audit. Do not resend or change attempt identity to hide uncertainty. Any fresh attempt needs a separate explicit decision. |
| Queued outbox pending | Inspect the authorized broker separately. Existing notification reconciliation reopens queued wake-ups only; publication flags alone do not prove broker health or disaster recovery. |
| Survey drift/freshness hold | Preserve the original snapshot, examine control/schema/source changes and downstream impact, then use existing assessment and verified impact-review paths if authorized. A newer report does not make old evidence fresh. |
| Journal held or unfinished | Confirm that the local acquisition owner has exited before considering existing `recover()`. Recovery marks interrupted reads unknown, performs no new source read and resumes no plan. It sends no device stop or reset. |
| Projection pending review | Review the exact source/model/runtime identities, time/frame/unit semantics and correlated uncertainty. Keep the synthetic projection held; no approval or admission API is added. |
| Integrity/access/dependency unavailable | Preserve suspect files, verify a separately trusted pin and inspect a recovery copy. Do not overwrite evidence or substitute cached status for current authority. |

The [coordinator restore contract](DISTRIBUTED_COORDINATOR.md) remains the existing
local cold-current-checkpoint drill: quiesce owned workers, stop the source, use
an empty destination and compare exact native archive/custody pins. Historical
disaster recovery, a surviving remote writer, production RPO/RTO and cross-store
restoration of survey/journal/workbench/audit artifacts are not performed by this
inspection path. The separate [local synthetic cross-store drill](CROSS_STORE_RECOVERY.md)
now restores the selected coordinator, survey, journal and workbench set together,
then uses this inspector to show preserved uncertainty and current holds. That
drill's quiesced single-host scope does not clear historical/hosted disaster
recovery, arbitrary audit stores or production RPO/RTO gates.

## Historical CLI

The CLI accepts only a private saved report and a trusted byte pin. It cannot
open a live coordinator, select a source or execute a recovery instruction:

```sh
python -m ciw.internal_operator --report /private/operator-report.json \
  --expected-ref sha256:THE_RETURNED_REPORT_HASH
```

Output is explicitly labeled `retained_report_only_current_sources_not_checked`
and `current_authority_restored=False`. A saved report is historical evidence;
its old component states and clocks must not be presented as current status.
The file pin checks integrity, not an external signature or source authenticity.

## Qualification boundaries

The local tests use a new private PostgreSQL cluster with TCP disabled and
synthetic source journals. They check read-only SQL, unchanged custody, expiry
guidance without recovery, unknown outcomes without retry, redaction, current
holds/revocation, pure playback, byte pins and output/selection bounds. Capacity
settings apply only to that disposable qualification cluster; no existing
database or security configuration is changed.

An operator report is not hosted or physical qualification. PlanetScale identity,
TLS, pooling/failover/restore, live NATS recovery, full scientific baseline,
positive companion namespaces/trusted `lsblk`, authenticated time, NSL admission,
company source clearance and a calibrated physical read-only instrument retain
their separate gates. The runtime and dependency hashes in the qualification
receipt identify the actual local environment; test counts do not clear these
gates.

The PostgreSQL source lane loaded NumPy 2.4.3 from the baseline environment and
Psycopg 3.3.7 from an existing driver installation; recorded workspaces and an
explicit module-path/version probe confirm those identities. The installed
core/journal/workbench lane also used NumPy 2.4.3. The driver virtualenv has a
different default environment when launched directly, so its name alone must
not be used as proof of the loaded core dependencies. Verify actual imports
when combining already installed dependencies. Live PostgreSQL inspection was
tested against source modules; the installed reader was byte-matched and tested
for historical/held-workspace inspection rather than claimed as another live
PostgreSQL deployment.

## Local package-build provenance

The earlier Atelier wheel attempt used `--no-index --no-build-isolation` without
an installed `hatchling.build` backend. The application source was not the cause.
The local follow-up retrieved exact wheels from official PyPI HTTPS metadata and
`files.pythonhosted.org`, checked each declared size and SHA-256 before use, then
installed the build dependencies into a task-owned directory with
`--no-index --no-deps --no-compile --require-hashes`. No source distribution,
alternate backend, credentials, access-policy change or network bypass was used.

The [build lock](../tools/internal-operator-build/requirements-hatchling.lock)
records the actual fixed versions and wheel hashes. Hatchling 1.27.0 is a
compatibility pin, not a latest-version recommendation. It was successfully used
with the recorded packaging, Pluggy, Pathspec and trove-classifiers versions.
Original registry metadata, artifact URLs, metadata hashes, installation logs,
candidate wheel hashes and byte-verified installed files accompany the local
qualification receipt. ODSP used its already installed Setuptools backend; the
application's existing runtime requirements were not changed.

For an authorized local rebuild from an already approved wheelhouse:

```sh
python -m pip install --no-index --no-deps --no-compile --require-hashes \
  --find-links /approved/wheelhouse --target /private/new-build-deps \
  -r tools/internal-operator-build/requirements-hatchling.lock
```

Build isolated source copies with the declared backend, then install the
resulting candidate wheels into a private test directory. Qualification must
verify import paths and actual package bytes rather than silently using source
checkouts. Local installed observation/workbench/read-only inspection checks do
not qualify Atelier's equipment-command runtime, OAuth/remote access, or a
published dependency. Do not treat a build backend as an application permission.
