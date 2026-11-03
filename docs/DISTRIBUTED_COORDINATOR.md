# PostgreSQL and NATS coordinator

`ciw.distributed_queue.PostgresQueue` is an optional distributed custody adapter.
It uses real PostgreSQL transactions and database-clock leases; it does not use
the local POSIX queue as a persistence substitute. `ciw.distributed_nats.JetStreamBridge`
delivers durable notifications through the official NATS Python client. The
default local coordinator and all existing MCP/service contracts remain available.

The trusted host binds the database connection, principal, generation, allowed
operations and current authorization callback. Nothing here creates a grant,
selects a provider, upgrades a receipt to scientific truth or authorizes physical
action. No MCP tools, accounts, credentials or hosted services are created on
import. This implementation has local real-service qualification; multi-host
deployment, replicated failover, backup/restore and operational acceptance require
their own evidence.

## State and execution contract

PostgreSQL is the execution custody authority. Each immutable namespace binds
`owner`, `principal`, `generation` and saved limits. An attempt identifies the
complete canonical request. Concurrent identical enqueue calls produce one job
and one outbox record; changing that request under the same attempt refuses.

Every state transition locks the namespace row, then the job row. The namespace
lock serializes capacity and monotonically increasing fences; it does not remain
held while ordinary host dispatch runs. Different namespaces have independent
locks. Workers may execute concurrently up to the saved running limit. Row locks
and transactions establish atomicity, not the notification broker. See the
[PostgreSQL locking contract](https://www.postgresql.org/docs/17/explicit-locking.html).

The normal path is:

```
queued -> claimed -> dispatching -> completed
                     |                (bounded result committed)
                     +-> outcome_unknown
claimed -> refused | outcome_unknown
```

`begin_dispatch` rechecks current authority and a live lease, then commits an
immutable intent before calling host code. A callback is entered only after the
intent transaction has returned successfully. Completion commits the result and
the terminal transition atomically. If a completion acknowledgement is lost, the
caller must read custody; the adapter does not overwrite a possible committed
completion with uncertainty.

A claim is bound to one adapter instance and process. Its UUID prevents accidental
cross-claim settlement; it is not an authorization token. Each claim and orphan
recovery increments the namespace fence. Database time determines expiry.
Renewal checks the original claim, fence, worker, current policy and expiry; an
expired lease cannot be revived. The convenience executor renews at one third of
the lease interval. Its callback must still have its own runtime bounds.

Expired claims become terminal `outcome_unknown`, including a crash before a
provider was actually reached. No automatic requeue or execution retry exists.
Interrupted dispatch, oversized output and post-effect custody failure are
uncertain outcomes. A new attempt is a separate explicit host decision after
reconciliation; it must not be used to hide an unknown earlier effect.

Lease loss cannot terminate arbitrary Python, a subprocess, a device operation
or an external API request. The detached `Claim.fence` includes the namespace,
job, request digest, generation and fence number. Downstream systems must reject
stale operations using that full identity. Generation changes create a new
namespace; operators must revoke the old generation rather than compare bare
numbers from unrelated namespaces.

For effects in the **same PostgreSQL database**, `queue.fenced_transaction(claim)`
provides a transaction holding the queue/job locks. It validates the current
claim and authority before yielding, then checks the lease again before commit.
SQL effects roll back if the lease expired. Only SQL committed in that transaction
is protected; making a network call inside it does not fence that network call.

## Canonical host integration

`DistributedServices` binds one exact `AuditedPrincipalHost`, its underlying
`PrincipalFacade`, current policy, provider identities and selected schemas. It
uses the existing `services.invoke` path with the host's admission/terminal audit
and scope guards. Policy is revalidated at enqueue, claim, heartbeat, dispatch
intent, actual invocation and result disclosure, including after blocking audit
I/O. Observed identity rebinding permanently refuses the adapter. Queue ownership
comes from the bound principal; callers cannot supply another principal or owner.

```python
from ciw.distributed_services import DistributedServices

# Schema provisioning is a separate approved direct-PostgreSQL operation.
service = DistributedServices(
    runtime_dsn, host=existing_audited_principal_host,
    generation=host_release_generation,
    operations=["ncp_domain_read_record"],
)
submitted = service.enqueue(
    "ncp_domain_read_record", {"domain": approved_domain, "record_id": approved_record_id},
    attempt="operator-review-1",
)
bridge = service.bridge(existing_authenticated_jetstream,
                        stream=host_stream, durable=host_consumer)
await bridge.bind()  # Existing, previously provisioned broker resources only.
outcome = await service.cycle(bridge, "host-worker-1")
```

The fixed read-operation allowlist is narrower than metadata discovery. A
`readOnlyHint` alone never admits a service. This read composition refuses
mutations before job insertion or dispatch intent even if the principal has a
grant. Its lease checks
inside canonical scope guards use nonlocking database reads to avoid inverting
the policy/heartbeat lock order. They cannot fence an arbitrary external effect.

The separate `DistributedNumerical` composition below carries the claim through
actual bounded native computation. Arbitrary network/device effects still need
their own downstream fence; neither composition qualifies those effects.

Queue outcomes and the independent existing audit are both retained. Audit call
IDs keep their existing format; the canonical operation/argument hash correlates
records without inventing a new receipt type. Audit refusal, retention failure or
lease loss after durable intent becomes an unknown queue outcome and is never
automatically replayed. A successful read does not admit scientific evidence.

The lower-level `PostgresQueue` remains a trusted host API with an explicit
current-authority callback and dispatcher. `PrincipalPolicy.authorize` returns a
grant dictionary; an adapter must discard it and return `None` or exact `True`
after successful authorization. This low-level API is not itself a qualified
canonical mutation adapter. Bind neither interface as model-selectable DSNs,
principals, callbacks or executables. Current grants and source/licensing/execution
holds remain in force.

The API also exposes `inspect`, `claim_next`, `renew`, `begin_dispatch`, `complete`,
`abandon`, `recover` and `history`. Manual users must establish bounded heartbeat
and lifecycle handling themselves. `history` verifies the hash-linked transitions
and their binding to the current intent/result; read and state-transition paths
also verify this history before trusting saved custody. These hashes detect
inconsistent records, not a malicious database administrator rewriting all history.
No external signature, immutable ledger or evidence admission is asserted.

### Native computation and private result custody

`ciw.distributed_numerical.DistributedNumerical` accepts only the existing seven
pure `RustNumericalBackend` operations in an already-admitted plan. The exact
audited principal host, current scientific grants, native runtime and immutable
source policy are bound by the trusted launcher. It creates a fresh fixed native
registry; an arbitrary callback with matching runtime metadata is not accepted.

```python
from ciw.distributed_numerical import DistributedNumerical

# The host must be unused, explicitly allowed only the seven native operations,
# and its existing principal/audit policy must allow submit, run-get and artifact-read.
compute = DistributedNumerical(
    runtime_dsn, host=existing_native_audited_host, backend=verified_native_backend,
    generation=host_release_generation,
)
job = compute.enqueue(admitted_plan_id, admitted_plan_digest, attempt="native-review-1")
bridge = compute.bridge(existing_authenticated_jetstream,
                        stream=host_stream, durable=host_consumer)
await bridge.bind()
outcome = await compute.cycle(bridge, "native-worker-1")
```

The source owner's output directory is permanently marked with its queue/budget
binding. Canonical local submit and direct execute/replay on that source owner
then refuse. Restoring its checkpoint without the same live host binding cannot
remove the marker. Requests and checkpoints cannot supply or reset the guard.
The optional host guard leaves existing unmarked hosts unchanged.

PostgreSQL's saved total-job cap cannot exceed the source host's frozen
`max_executions`. Every enqueued attempt, including refusal or uncertainty,
consumes that durable budget. Identical frozen source/catalog/policy and principal
share the same generation budget across worker paths. A new occurrence directory
cannot reset it. A different generation is an explicit trusted host change;
the existing source directory refuses rebinding to it.

After a durable dispatch intent, the adapter creates one private occurrence
directory, reconstructs the exact admitted plan and binds a host-only live claim
guard to its new AgentHost. It invokes the canonical audited `ncp_run_submit`,
waits outside the principal-policy lock for the actual worker to finish, then
invokes canonical audited run/artifact reads. The PostgreSQL heartbeat remains
active through that terminal computation and result publication. Current policy
and claim are checked before and after each native kernel, before local artifact
registration, after audit I/O, and in the atomic PostgreSQL completion transaction.
Every later result read rechecks its canonical run/artifact/scientific grants.

The original host never registers the occurrence's generated artifacts or jobs.
Their existing scientific receipts and workspace files stay private evidence;
only the bounded completed PostgreSQL result is disclosed. A kernel may finish
after its lease or grant is lost, but the old worker cannot publish that late
result. Its attempt remains consumed, and recovery advances the fence and records
`outcome_unknown` without replay. Grant revocation stops renewal; if private
evidence cleanup outlasts the lease, the recovery path, not the stale worker,
records that final unknown state.

The result envelope identifies the completed canonical run, full validated
scientific result records, workspace binding, native runtime digest and claim
fence. Existing scientific record formats are preserved. The usual 64 KiB queue
result limit still applies; over-budget results become unknown with private
receipts retained. The audit chain/anchor mutex and sticky unresolved-audit hold
are shared across occurrences. No new occurrence may reset that hold.

This supports bounded pure computation, not arbitrary external mutations or
physical qualification. Hosted latency, multiple-machine storage/audit bindings,
provider failover and restore remain separate release gates. This composition
does not create schemas, streams, credentials, network access or deployment.

The local numerical checks use the fixed native backend's pinned `prlimit`
policy: CPU, wall time, memory, file and pipe limits with its minimal environment.
Its runtime explicitly declares that this `limits` mode provides no network or
filesystem isolation. The mandatory Bubblewrap policy for companion adapters is
a separate contract; these numerical checks do not qualify that sandbox or
silently satisfy its prerequisites.

## Broker delivery and recovery

Each enqueue transaction creates an outbox entry containing only a version,
namespace ID, job ID and request digest. Arguments, credentials, policy and grants
are never included. Publishing uses the immutable payload digest as `Nats-Msg-Id`
and waits for a JetStream `PubAck` before marking the outbox record published.
Duplicate publication is safe even after the broker's finite deduplication window:
the database attempt and claim remain authoritative. The distinction follows
the official [JetStream acknowledgement and deduplication semantics](https://github.com/nats-io/nats.docs/blob/master/using-nats/jetstream/model_deep_dive.md).

One exact queue subject is bound to a file-backed work-queue stream with
`DiscardNew`, bounded message count/bytes, no message expiry and one durable
pull consumer with explicit acknowledgements. `provision()` is an explicit
administrative operation on an already authorized connection; it verifies existing
resources and never updates incompatible ones. Runtime workers use `bind()`.
Extra subjects, mirrors, sources, republishing or incompatible consumer policies
refuse binding.

`process_one` fetches one notification and reads the corresponding database job.
It acknowledges only after a current-authorized durable terminal read. Concurrent
ownership, a revoked grant or an unavailable database retains/nacks the wake-up.
A malformed notification cannot supply arguments, authorize work or change a
job's status. Operators must inspect recurring malformed/refused notifications;
there is no silent poison-message deletion or unauthorized dead-letter destination.
Cancellation of the async consumer does not terminate its Python dispatch thread:
durable custody and downstream fencing must protect any still-running callback.

`cycle(worker, dispatch)` performs recovery, queued-only outbox reconciliation,
publication and one consumption. Reconciliation republishes wake-ups for jobs
that are **still queued**, even if a prior broker acknowledgement was observed.
This covers missing notifications after broker loss. It never requeues claimed
or terminal executions. Missing streams/consumers require explicit authorized
reprovisioning; the runtime does not silently create them.

The [official NATS Python client](https://nats-io.github.io/nats.py/modules.html)
owns TLS/authentication/reconnection. Pass an already configured context. The
adapter does not generate credentials or weaken transport settings.

## Database and operational requirements

`install_schema` requires PostgreSQL 14+ and refuses disabled `fsync` or
`full_page_writes`. Each connection opens an explicit read-write, READ COMMITTED transaction and
sets synchronous commit, a five-second statement timeout, two-second lock timeout,
five-second idle transaction timeout and fixed `pg_catalog` search path locally
to that transaction, with readback. Automatic named prepared statements are disabled.
Host SQL inside `fenced_transaction` must qualify its own table names. The v1 schema digest is fixed; incompatible installed formats refuse
instead of silently migrating custody. Connection establishment is bounded by
the driver. Pool assignment before a transaction starts additionally needs a
bounded pooler query wait; local qualification sets it to five seconds. DNS,
network availability, connection-loss latency and failover remain deployment
concerns and must fit the selected lease/heartbeat budget.

The runtime role needs schema `USAGE`, `SELECT` on `format`, `SELECT/INSERT/UPDATE`
on `queues`, `jobs`, `outbox`, and `SELECT/INSERT` on `events`. It needs no database
creation, schema ownership, superuser, event update or deletion permission.
Provision and runtime roles should be separate. These SQL permissions are a
trusted service boundary, not row-level isolation against hostile workers with
direct database access. Separate role/database isolation or a reviewed RLS policy
is required before treating such workers as mutually untrusted tenants.

`QueueLimits` preserves the local queue's conservative bounds: at most 1,024
lifetime attempts, 128 pending and 16 running; defaults are 128, 16 and 1.
Individual requests/results remain at most 64 KiB. Reservation additionally covers
intent, outbox and four bounded history records. Logical serialized custody is
bounded by the saved state budget; PostgreSQL page/WAL/index overhead and broker
replication storage are separate operational capacity requirements. No automatic
eviction, history deletion, result truncation or fence reset is provided.

An operator still needs approved database/broker endpoints, identities and TLS,
network isolation, persistent storage, backup/restore, a replica count, supervision,
monotonic-generation restore procedures and SLO/RPO/RTO decisions. This patch
does not bind Cloudflare/ODSP hostnames, create access, deploy or authorize spending.
Local single-node crash tests do not establish replicated quorum failover,
cross-region availability or power-loss durability.

## Reproduction and evidence

`tools/distributed-coordinator/runtime-lock.json` pins the actual official Debian
archives and PyPI wheels used by this track. PostgreSQL is **17.11-0+deb13u1**;
NATS server is **2.10.27-1+b2**; psycopg/psycopg-binary are **3.3.7** and nats-py
is **2.16.0**. Archives were size/hash-checked against Debian's HTTPS package index
and wheels against official PyPI release metadata. This is not an assertion that
a Debian archive signature was independently verified. System shared libraries
and existing ODSP numerical dependencies remain separate environment inputs.

On Linux amd64/Debian 13 with the qualified ODSP Python 3.12 environment:

```sh
python tools/distributed-coordinator/bootstrap.py --destination /tmp/odsp-distributed-reproduce
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
ODSP_REQUIRE_DISTRIBUTED=1 \
ODSP_DISTRIBUTED_PG_BIN=/tmp/odsp-distributed-reproduce/root/usr/lib/postgresql/17/bin \
ODSP_DISTRIBUTED_NATS_SERVER=/tmp/odsp-distributed-reproduce/root/usr/sbin/nats-server \
ODSP_DISTRIBUTED_EVIDENCE=/tmp/odsp-distributed-reproduce/evidence \
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
/tmp/odsp-distributed-reproduce/venv/bin/python -m pytest \
  tests/test_distributed_coordinator.py tests/test_distributed_services.py \
  tests/test_distributed_connections.py tests/test_store_queue.py -q \
  --junitxml=/tmp/odsp-distributed-reproduce/qualification.xml
```

The helper downloads/extracts without installing system services, then creates an
isolated optional-driver environment inheriting the selected application runtime.
Tests start a private PostgreSQL Unix socket with TCP disabled and an authenticated
NATS server bound only to loopback. The test token stays in a mode-0600 private
configuration file and is never part of the qualification report. Each run retains
command outcomes and server logs and stops both process groups. Missing services
or drivers fail when `ODSP_REQUIRE_DISTRIBUTED=1`; ordinary source test discovery
otherwise reports explicit prerequisite skips.

Tests execute competing worker processes, running-capacity admission, renewal,
expiry and stale-fence refusal, actual current PrincipalPolicy revocation, a
fenced SQL effect and rollback, unknown outcomes without replay, intent/completion
acknowledgement loss, database outage, PostgreSQL/NATS SIGKILL recovery, duplicate
notifications, lost publication acknowledgement, test-stream loss reconstruction,
least-privilege SQL and history corruption. All effects are synthetic local
database records. No live scientific provider, device or hosted deployment is
qualified by these tests.

### Local current-checkpoint restore drill

`tests/test_distributed_restore.py` and the test-only
[`restore_fixture.py`](../tools/distributed-coordinator/restore_fixture.py) add a
native PostgreSQL checkpoint, custom-format `pg_dump` and single-transaction
`pg_restore` journey. The source and destination are newly initialized private
local services containing synthetic custody/effect rows. This is a cold cutover:
the fixture host owns and quiesces every worker, and the source PostgreSQL/NATS
processes stop before the separate empty destination starts. The helper is not
registered as an MCP operation or operational restore API.

One exported repeatable-read snapshot binds all coordinator tables, the fixed
schema-format record and the synthetic downstream SQL effects to the dump.
Archive and logical-snapshot digests are supplied independently by the drill's
host. Corrupted bytes, a changed expected digest, another source identity or
custody that advanced after backup refuse before either service is stopped.
An existing destination is never overwritten. These checks record local custody;
they are not an independent signature or approval of an arbitrary SQL archive.
Only dumps created from this owned fixture are restored. PostgreSQL documents
the [snapshot/archive contract](https://www.postgresql.org/docs/17/app-pgdump.html)
and [native restore behavior](https://www.postgresql.org/docs/17/app-pgrestore.html).

The drill verifies complete custody/history equality immediately after restore,
preserved completed results and consumed attempts, and the following recovery:

- A fresh host callback supplies current authorization. A revoked callback
  cannot claim or dispatch queued work; authorization is never read from backup.
- The prior source endpoint is stopped and a saved old claim cannot settle on
  the new queue instance. Expired restored claims advance the fence and become
  terminal `outcome_unknown`, without repeating a possible effect.
- A fresh empty JetStream broker receives only reconstructed queued wake-ups.
  Previously saved PubAck metadata does not stand in for broker state. The queued
  job dispatches once, while completed and uncertain jobs remain terminal.
- The restored synthetic effect table has exactly the original completed effect,
  the original uncertain effect and the one newly completed queued effect. It
  contains no effect for the interrupted claim and no duplicate row.

Run the restore test file with the pinned PostgreSQL/NATS inputs and drivers from
the reproduction section. Keep `ODSP_DISTRIBUTED_EVIDENCE` short, for example
`/tmp/odp-dr`, because native Unix socket paths have a platform length limit.
The drill temporarily needs two PostgreSQL clusters plus backup/log space beyond
the evidence reserve. On a constrained host, run test files in separate batches
and preserve stopped synthetic clusters in byte-verified archives between batches.
The JUnit properties and `restore-result.json` retain exact identities and the
observed restoration duration; that duration is not a production RTO commitment.

The dump covers this coordinator database and synthetic same-database effects.
It does not restore external retained workspaces, audit anchors, object storage,
broker credentials, identity policy or cross-system effects. Source-stop checks
in a fully owned fixture do not establish fencing of a surviving remote writer.
Restoring a stale backup after authoritative history is lost, an online takeover,
replicated failover and a hosted restore remain unqualified. A production design
must establish external custody/generation continuity, actual storage and TLS
identities, separately approved access, and explicit RPO/RTO before acceptance.

## Managed PostgreSQL and transaction pooling

The target architecture uses PostgreSQL for custody/cache, a separate NATS
JetStream service for wake-ups, and separately supervised compute workers. The
current SQL is PostgreSQL-specific; it does not implement a MySQL/Vitess backend.
Choosing a managed provider does not establish hosted qualification.

| Requirement | Source-derived contract and local evidence |
| --- | --- |
| PostgreSQL | Version 14+ is enforced; actual local qualification uses 17.11. Fixed schemas, `bytea`, UUID, timestamps, `ON CONFLICT`, `RETURNING`, aggregate filters and row locks are required. No extension is installed or required. |
| Atomicity | Claims, fence increments, state/history and outbox changes use single read-write transactions. Namespace row locks serialize capacity and fences; `FOR UPDATE SKIP LOCKED` selects claimable jobs. No broker notification is execution authority. |
| Advisory locks | Schema installation uses **transaction-scoped** `pg_advisory_xact_lock`. Runtime custody/cache uses row locks. No session advisory locks, `LISTEN`, session temporary tables, holdable cursors or session variables are used. |
| Connection mode | Short transactions may use a primary PgBouncer **transaction** pool or direct PostgreSQL. Statement pooling is unsupported. DDL/installation uses the direct primary connection. Replica/read-only routes cannot serve custody or the cache, whose reads update LRU/scope records. |
| Driver state | `prepare_threshold=None`; no named prepared statement requirement. Bounds, search path, synchronous commit and isolation are applied inside each transaction. Unsupported settings fail closed; startup parameters are not ignored to make a connection succeed. |
| Privileges | A separate approved installation identity needs schema/table/index creation. The runtime role has only the fixed table privileges above. The cache additionally needs deletion for invalidation/eviction in its separate `odsp_persistent_cache_v1` schema. Neither runtime needs superuser or database creation. |
| Transport | The trusted launcher supplies the exact database endpoint, authentication and TLS policy. Production requires validated server identity and an approved trust store; no TLS downgrade or credential creation is performed here. Local Unix sockets do not qualify hosted TLS. |

PlanetScale's current documentation describes transaction pooling on port `6432`
and recommends direct connections on `5432` for DDL. Its PostgreSQL examples use
`sslmode=verify-full`, an appropriate trust store and direct TLS negotiation.
Those documented connection options are a candidate configuration, not evidence
that an ODSP database, role or endpoint exists. See
[PlanetScale PgBouncer](https://planetscale.com/docs/postgres/connecting/pgbouncer).
The client deliberately avoids session-only features listed by
[PgBouncer](https://www.pgbouncer.org/features.html) and disables preparation as
recommended for unqualified poolers by
[psycopg](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).

The original connection options reproduced a real PgBouncer rejection:
`unsupported startup parameter in options: statement_timeout`. The fix moves
settings into the transaction; it does not ignore rejected startup parameters or
remove runtime bounds. The separately pinned optional pooler is PgBouncer 1.24.1
from Debian, with prepared-statement tracking disabled and no forced session reset.
Tests verify settings disappear after commit/abort while sequential clients reuse
the **same** server process, exercise transaction advisory-lock release, and run
the coordinator and canonical audited service suite through the actual pooler.

To reproduce pooled qualification, bootstrap with `--with-pooler`, set
`ODSP_DISTRIBUTED_PGBOUNCER_BIN` to the resulting `root/usr/sbin/pgbouncer`, and run
the distributed test command above. Omitting that variable tests direct PostgreSQL.
The helper starts a private Unix-socket pooler with TCP disabled. Its test-only
trust authentication within a mode-0700 directory is never a hosted configuration.
The optional package lock is separate from the preserved original runtime lock.

### Failover and restore gates

No connection exception causes an automatic execution retry. A failed claim or
intent commit never hands work to a dispatcher. After an ambiguous completion
commit, read durable custody before settling; never overwrite a possible completed
result. Reconnection can publish duplicate outbox messages, which cannot bypass
permanent attempt/claim custody. Expired in-flight work becomes `outcome_unknown`.
NATS recovery only reconstructs notifications for still-queued work.

Local SIGKILL/restart and pooler tests do not prove provider failover, synchronous
replica acknowledgements, regional availability or backup/restore. A database
restore can rewind `next_fence` and forgotten outcomes; restoring then blindly
resuming the same generation is unsafe. A release needs an independently retained
cutover record, reconciliation of pre-restore effects/audit, revocation or quiescence
of old writers, and a fresh host generation respected by any downstream fence.
The current generic adapter cannot enforce those steps on an external device.

Still unknown until an explicitly authorized hosted qualification: selected
PlanetScale database/region/branch and PostgreSQL version, permitted DDL/settings,
role grants, connection/transaction limits, exact TLS chain, broker endpoint and
replicas, credentials/secret bindings, latency under lease budgets, failover and
restore behavior, RPO/RTO and cost. No provider provisioning, migration, external
connection, new credential, access expansion or spending was performed.
