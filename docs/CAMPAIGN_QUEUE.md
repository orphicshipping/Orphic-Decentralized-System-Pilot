# Opt-in local store queue

`ciw.store_queue.StoreQueue` is a bounded custody adapter for trusted Python host
code. It stores jobs separately from the existing coordinator and retention
formats. Nothing selects it automatically, and it adds no MCP operation, service
grant, credential, external broker or new retention API.

The adapter is for processes sharing one private POSIX filesystem directory with
working kernel file locks, atomic rename and file/directory `fsync`. This is not a
multi-machine queue, a network-filesystem qualification, a production durability
claim or a distributed lease service. The existing private Durable Object/R2
owner/generation/checkpoint contracts remain independent.

## Host API and authority

```python
from ciw.store_queue import QueueLimits, StoreQueue

# All of these bindings come from trusted launcher configuration.
queue = StoreQueue(
    configured_private_directory,
    owner=configured_owner,
    principal=configured_principal,
    generation=configured_generation,
    operations=configured_operation_allowlist,
    authorize=check_current_policy,
    limits=QueueLimits(),
)
job = queue.enqueue(operation, arguments, attempt)
outcome = queue.execute_one(configured_worker_id, bounded_host_dispatch)
current = queue.inspect(job["job_id"])
```

`check_current_policy(principal, operation, arguments)` must check the canonical
tool, scientific-operation and resource grants applicable to that operation. It
returns `None` or `True` on success and raises on refusal; all other returned
values refuse. Arguments are detached copies. The queue checks the current
allowlist and callback at enqueue, inspect, claim and immediately before
`execute_one` invokes its dispatcher. A revocation at the final check produces a
terminal `refused` record with zero dispatch calls. A denied queued item remains
at the head; the host must resolve its policy rather than silently consuming or
skipping it. The authorization callback must be bounded and must not re-enter the
same queue while its lock is held.

`bounded_host_dispatch(operation, arguments, fence)` is a host-selected callable.
It must invoke the established operation boundary, with current host grants,
timeouts and resource limits. Queue lock timeouts do not interrupt arbitrary
Python code. The adapter does not authenticate a model-supplied principal or
interpret a supplied function, executable, path or callback. A claim and every
returned custody record have **no independent execution authority**.

The lower-level host methods are:

| Method | Effect |
| --- | --- |
| `enqueue(operation, arguments, attempt)` | Atomically admit or replay a bound request. |
| `inspect(job_id)` | Read the current record under current operation policy. |
| `claim_next(worker)` | Return one process-local `Claim`, or `None` when no admission is available. |
| `complete(claim, result)` | Commit a bounded result and release that claim's lease. |
| `abandon(claim)` | Commit `outcome_unknown`, then release that claim's lease. |
| `recover()` | Settle orphaned running jobs as `outcome_unknown`; never dispatch them. |
| `execute_one(worker, dispatch)` | Claim, reauthorize, dispatch once and settle. |

Direct claim users must apply the same current authorization immediately before
their external dispatch; prefer `execute_one` for that seam. `Claim` is an owned,
process-local object, not a serialized ticket. It cannot be reconstructed from a
job ID, used by another queue object or inherited as authority across `fork`.
Its context manager abandons an unfinished claim on exit. The host must finish or
abandon each successful claim; there is no garbage-collection or wall-clock lease
expiry promise.

## Identity, atomicity and fencing

An immutable queue identity binds owner, principal and deployment generation.
Attempt IDs bind that identity and the full canonical operation/argument request.
Reusing an attempt with a different request refuses. Reopening the directory with
a different identity or different persistent capacity limits refuses. The
host-selected operation allowlist and callback may become more restrictive on
reopen; previously saved data does not restore old grants.

`queue.lock` serializes read/modify/publish transactions. A successful claim also
holds a distinct job lease until settlement. The lease transfers to the caller
only after running-record publication **and** enclosing transaction cleanup
succeed. Failed admission releases its own lease and does not dispatch. Cleanup
does not unlink lock files, unlock someone else's descriptor or retry an ambiguous
close. Existing repaired coordinator lease handling is reused unchanged.

The queue's monotonic integer fence advances on claim and orphan recovery. Every
settlement verifies the job's current running status, worker, fence and full
request digest under the queue lock. A live kernel lease is never stolen based
on PID, wall-clock age or a queue timeout. Concurrent recovery can settle one
orphan only once. Another attempt receives a higher fence.

This fences **queue state transitions**. It does not automatically fence a
separate database, file, device or remote service. Any dispatcher that commits
external effects must use that system's existing owner/generation/fence contract;
passing a fence number alone is not external enforcement.

## Uncertainty and replay

The states are `queued`, `running`, `completed`, `refused` and
`outcome_unknown`. Terminal attempts stay terminal, including after reopen.
There is no automatic retry, requeue, lease stealing, eviction or terminal-history
deletion. A new attempt is a deliberate host decision, not recovery of the old
execution.

If the dispatcher raises, is cancelled or is lost, effects may have happened.
The record becomes `outcome_unknown` where publication succeeds. If a result is
invalid or settlement publication fails before its effect, the released orphan
can subsequently be recovered as unknown. A callback timeout or cancellation is
not a rollback declaration.

Publication and context-exit errors can occur **after** an enqueue, running or
terminal record is durable. The exception propagates; read the same attempt to
determine its current state. A completed record is never overwritten as unknown
merely because its acknowledgement, unlock or close failed. If cleanup also
raises, Python's exception context retains the original cancellation or failure;
cleanup failure may be the raised exception. The adapter does not promise that
every paired cancellation/cleanup error emerges as `KeyboardInterrupt`.

If an ambiguous close fails before its effect, its detached descriptor may still
hold a kernel lock. The adapter does not retry that close: the same descriptor
number could already belong to someone else after an after-effect error. Further
admission remains blocked while that process lives. A process restart and
readback/recovery are required; a cleanup exception is not a promise that a lock
was released. Tests cover both sides of this ambiguity.

The record includes the request SHA-256 and exact result, not raw exception text.
These hashes bind local content, not remote source authentication or attestation.
Filesystem access by the host account remains trusted.

## Hard bounds and recovery prerequisites

Default limits are 128 lifetime attempts, 16 pending jobs, one running job,
65,536 canonical bytes per bound request/result, 4,000,000 canonical bytes of
queue state and a two-second lock acquisition deadline. The request budget
includes the owner, principal, generation, operation and attempt. Unicode is
counted after canonical JSON escaping. Each request/result is limited to depth 24
and 32,768 nodes; the existing canonical JSON validator adds its numeric/string/
container limits. The queue state adds three fixed container levels and bounds
aggregate nodes by its serialized byte cap, rather than reapplying one result's
node quota to all completed jobs. A public job response reserves one wrapper
level, 128 metadata nodes and 4,096 metadata bytes around the same result bounds.
Surrogates, nonfinite values, duplicate persisted keys and malformed records
refuse. Worker/identity/operation/attempt strings are bounded inert IDs.

Hosts can lower limits within the hard implementation maxima, before creating
the queue. Persistent limits are immutable on reopen; `lock_timeout` may differ.
Each admission reserves its maximum result plus metadata so capacity cannot be
recovered by silently dropping earlier attempts. A result that exceeds its own
declared byte, node or depth contract can still refuse after dispatch; failed
result custody remains an explicit unknown outcome. Valid results do not exhaust
an unreserved aggregate structural quota. A full queue requires a separately reviewed archival/continuity design,
not deletion of old attempt history.

The job counts are ceilings, not a promise that every maximum-sized result fits:
reserved bytes can exhaust the state budget before the lifetime job count does.

At most one full state and one transient `queue.json.partial` are needed for
publication, plus a stable queue lock and at most one lease file per admitted
attempt. Budget up to twice `max_state_bytes` plus filesystem overhead. Crashed
partial publications are removed only under the queue lock. Locks are regular,
single-link files. No-follow reads and private owner-only directory permissions
are required; unsupported platforms fail closed.

This slice adds **no backup/restore endpoint**. Preserve the complete owner,
principal, generation, attempt history and fencing state. A missing state file
with remaining job leases refuses as lost history. An old copied snapshot is not
proof of continuity: restoring it while a stale writer exists or recreating an
empty directory can violate idempotency. Lost-history disaster recovery, power
loss, filesystem rollback and external storage fencing remain separate gates.

## Local qualification

Run from the repository with its qualified Python/test environment:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q \
  tests/test_store_queue.py tests/test_campaign_coordinator.py
```

The tests cover full request/identity binding, current grants and revocation,
bounded admission, stable attempts, live-worker exclusion, monotonic fencing,
real SIGINT/SIGKILL, concurrent enqueue/claim/recovery, before/after publication
failure, enclosing-context exit failure, cleanup exception chaining and
close-after-effect descriptor reuse. Faulted workers stay alive while separate
processes probe locks; process death is not accepted as evidence of cleanup.

These are local synthetic POSIX tests. Installed-wheel integration, final shared
campaign tests, hosted storage, durable operational restore and real external
side-effect fencing require their own recorded evidence. Existing coordinator
and retention regressions must remain green; those implementations are not
rewritten by this slice.
