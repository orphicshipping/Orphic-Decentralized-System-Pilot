# Fixed survey pipeline with local PostgreSQL and NATS

`ciw.distributed_survey_pipeline.DistributedSurveyPipeline` composes the
[canonical principal-bound survey service](SURVEY_PIPELINE_MCP.md) with the
existing [PostgreSQL custody and JetStream notification](DISTRIBUTED_COORDINATOR.md)
interfaces. It is a trusted launcher API for one fixed retained survey plan.
It does not register another MCP tool, accept SQL or choose inputs, providers,
policies, tenants, endpoints, credentials or source versions from a request.
The generic `DistributedServices` read allowlist remains unchanged.

The trusted host supplies an existing `AuditedPrincipalHost` bound to the exact
`SurveyPipelineHost`, its current principal policy and explicit admissions. The
host also supplies an existing authorized PostgreSQL connection and generation.
No schema installation, connection credentials or broker provisioning occurs
implicitly in this adapter. The local tests create only isolated synthetic
services under the existing qualification fixtures.

```python
from ciw.distributed_survey_pipeline import DistributedSurveyPipeline

worker = DistributedSurveyPipeline(
    existing_dsn, host=existing_audited_survey_host,
    generation=operator_generation,
)
job = worker.enqueue(attempt="survey-review-1")
worker.execute_one("internal-worker")
outcome = worker.inspect(job["job_id"])
```

An existing authenticated JetStream context can be bound through
`worker.bridge(js, stream=..., durable=...)`. Provisioning is a separate explicit
host action. `worker.cycle(bridge, name)` recovers expired claims, reconciles
notifications and consumes at most one notification. The broker carries only
immutable wake-up identities. PostgreSQL owns arguments, capacity, claims,
dispatch intent, fence numbers and terminal results.

## Admission, identity and custody

Enqueue, claim, renewal and execution recheck the canonical operation grant,
argument constraints, both scientific grants, the explicit profile admission
and original pipeline admission, current source identity and source holds.
Neither admission callback can override a refusal. Result settlement and
completed-result disclosure additionally check current `ncp_pipeline_inspect`
grants and constraints, source readiness and the actual retained native result.
Saved contexts, SQL rows and notifications supply no permission.

The fixed plan, profile, audited facade, source host, principal, tenant, policy,
source bytes, interface and queue identity are bound. Requests contain only the
host-bound plan pin and bounded attempt. The queue attempt and native attempt
must be identical. Limits are sixteen total attempts, sixteen pending, one
running, 32 KiB request, 16 KiB result and 1 MiB reserved coordinator state.

A create-only `distributed-execution.json` binds the native pipeline directory
to this exact plan, adapter and coordinator generation. Once sealed, direct
local/native or canonical `ncp_pipeline_run` cannot execute without the matching
host-owned current claim scope, including after reopening that directory. A
different generation cannot reset the binding. These are host consistency
checks, not a new grant, approval field or user-selectable security policy.
Initial sealing holds the native execution lease and refuses a previously used
local queue; it cannot adopt prior local completion or uncertainty as a new PG
occurrence. A local run waiting before sealing rechecks the seal after acquiring
that same lease. The new marker and directory publication are fsynced.

The synchronous invocation carries a nonlocking current-claim check through
the existing audit barriers and native transform, analysis, retention and
completion guards. It checks the database clock lease, request digest, claim,
worker, fence, queue identity, limits and schema format. Nonlocking reads avoid
inverting the queue/policy lock order used by heartbeat renewal. The queue
rechecks authority and the lease in the transaction committing completion.
During a long synchronous canonical call, renewals reuse the already constructed
invocation scope check instead of reacquiring the foreground policy mutex. This
stores no permission decision: each renewal revalidates the pinned context
against current policy, both admissions and source holds; a changed context
refuses renewal. Other queued requests construct their own fresh scope check.

The PG result records its external fence, exact adapter/plan pins, canonical
held service summary and unchanged conservative claims. The native artifact
retains its separate local queue/fence and full original input/model/code and
uncertainty lineage. These are two custody records, not an atomic cross-store
transaction and not NSL state admission.

## Recovery and uncertainty

| Interruption | Required behavior |
| --- | --- |
| Duplicate enqueue, broker delivery or worker race | One durable PG attempt; one native calculation. A terminal replay returns the stored result under fresh authority. |
| Worker exits or lease expires | Recovery advances the fence and marks the occurrence unknown permanently; no requeue or resend. |
| Lease lost during audit or before calculation | Canonical/native gates refuse; no PG completion. |
| Lease or source authority lost after calculation/retention | The local held evidence may already exist. PG result publication is withheld; unknown custody requires review. |
| Local retention acknowledgement lost | Native and PG attempts stay unknown; retained evidence is preserved without adoption or automatic replay. |
| PG completion acknowledgement lost after commit | Inspect/history determine the committed outcome. No code overwrites possible completion with unknown or repeats the computation. |
| Broker PubAck custody lost, duplicate notification or restart | Republish the immutable wake-up, consult PG and acknowledge only a durable terminal outcome. |
| An unknown PG attempt exists | Fresh and previously queued attempts in the namespace cannot start; the original unknown remains inspectable. Native and audit holds apply independently. |

The fence cannot cancel running Python or roll back a local filesystem/SQLite
write. It governs PG completion and consistency checks around local work. A
lost worker can leave held local artifacts; they are evidence of a possible
occurrence, never proof of PG completion. Existing current-authorized native
historical inspection remains available for operator review and does not
release any hold or rerun a calculation. There is no automatic adoption,
unknown-outcome release API, generation rollover or workflow resume here.

The native pipeline source identity includes its own source bytes. Adding the
consistency hook therefore creates a new plan for fresh bindings; old queues
and artifacts are not migrated, overwritten or relabelled.

## Local qualification and its limits

`tests/test_distributed_survey_pipeline.py` uses actual local PostgreSQL and
authenticated loopback JetStream, existing canonical dispatch, native SQL and
numeric calculation, durable local audit and synthetic survey evidence. It
exercises simultaneous enqueue/worker races, active heartbeat renewal, process
exit, PG crash/restart, outbox loss and NATS restart, foreign/stale claims,
late admission/read revocation, source drift, seal/reopen behavior, retained
unknown outcomes and lost completion acknowledgements.

The dedicated cold cutover case uses the existing pinned native `pg_dump` and
`pg_restore` fixture: exact current SQL custody is captured, original services
stop, an empty owned target is restored and all SQL custody hashes match.
Completed/unknown jobs reopen without dispatch. Local survey, audit and
retention files remain present in this case; it does **not** restore those
files or expand the earlier [cross-store recovery set](CROSS_STORE_RECOVERY.md).

Provide the existing pinned runtimes and installed drivers; missing required
prerequisites fail when `ODSP_REQUIRE_DISTRIBUTED=1`. Optional
`ODSP_DISTRIBUTED_PGBOUNCER_BIN` exercises the same adapter through the existing
private transaction-pool fixture. Capture switches in the tests preserve only
synthetic artifacts, canonical SQL custody and a native dump, never broker
tokens or configuration. No broker credentials belong in source or test logs.

The current execution environment uses private volatile shared-memory scratch
for native clusters because both disk preservation floors are tight. Normal PG
fsync/WAL settings are retained; this does not test power-loss durability.
Local direct/transaction-pool compatibility is not hosted PlanetScale
qualification. Managed TLS/identity, NATS ACL/HA, multi-host shared retention
and audit anchors, surviving-writer fencing, historical rollback and complete
pipeline cross-store restore remain open. So do authenticated TBRT time, NSL
admission, real company input acceptance and physical calibration. The general
Domain H SQL/dbt profile, signed registry and shared cache remain separate.
Full scientific baseline, container and hosted CI/deployment gates stay open.
