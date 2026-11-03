# Toolbox client skills and content cache

These Python client libraries are integrated with the campaign's canonical
`services.invoke`, principal policies and optional audited host. They register no
MCP tools and install no personal ChatGPT skills. The shared argument validator
also supports the existing enum/const/composition and bounded input constraints
used by logistics and reference profiles; it adds no execution grant.

## Versioned recipes

`toolbox_recipes.builtin_skills()` returns a host-constructed `SkillRegistry` with
exact versions and content hashes:

- `reference-citation` 1.0.0 resolves an exact source, searches one matching
  evidence passage within that record and reads its exact span. Empty results
  refuse before the dependent read. The selected first match is a bounded search
  result, not a claim of completeness or scientific truth.
- `cross-profile-provenance` 1.0.0 reads the reference overview, reads the existing
  logistics catalog and retrieves that service's exact retained artifact. Its
  snapshots are independent; the recipe asserts no semantic relationship.

`list` pages bounded metadata; `describe` returns a bounded exact definition and
SHA-256; `load(..., selector)` selects existing schemas for client model context
only. Loading never admits or invokes steps. No server session load/unload API is
introduced. Definitions are supplied only by trusted host code, with at most
16 versions, eight steps each and 16 KiB per source definition. Dependencies
refer only to prior JSON results, with bounded explicit key/index selectors.
Registered service names and exact schemas come from the current source registry.
For a live local host, pass `facade=authority` to `SkillRegistry` or
`builtin_skills`. This binds one current authorized snapshot, including narrowed
host schemas. Without this argument, definitions bind the offline source schema;
any later mismatch refuses rather than widening a host's domain permissions.
Models cannot write recipes, choose code, supply runtime pins, principals,
tenants, retention owners or host admission functions.

## Execution and admission

Construct `SkillRunner` in the embedding host with its existing principal facade
or audited wrapper,
tenant, fixed source/runtime pins, replay identity and `admit_step` callback.
The callback defaults to deny. Every step receives the exact recipe hash, schema
hash, scope hash, arguments and argument hash, attempt and step identity. It must
make a fresh host admission decision; registration/loading grants nothing.
Every admitted step then calls `services.invoke(..., host=authority)`, which applies
current operation/scientific grants, argument constraints and profile scope.
A revoked grant during admission stops before the service invocation. The runner
never calls providers or profile hosts directly and never serves a step from cache.

```python
from ciw.toolbox_recipes import builtin_skills
from ciw.toolbox_skills import SkillRunner
runner = SkillRunner(facade=facade, registry=builtin_skills(facade=facade), tenant=host_tenant,
    pins=host_pins, replay_identity=host_replay, admit_step=application_admission)
summary = runner.run('reference-citation', '1.0.0',
    {'source_id': approved_source, 'query': approved_query}, attempt='review-1')
receipt = runner.receipt(summary['artifact_id'])
```

An audited wrapper remains the execution target; discovering its underlying
facade never unwraps dispatch. Its narrower allowlist and unresolved-call fence
remain active. A runner pins its constructor principal and retention owner;
rebinding the facade requires a new correctly bound runner. A recipe self-hash
does not replace host authorization or a durable audit journal.

This is a synchronous bounded client workflow, not a scheduler or sandbox.
`cancel(attempt)` sets a per-client cooperative stop flag. The runner checks it
between steps and after admission; an active synchronous service may finish.
There is no claim of provider termination, rollback or cancellation of a previously
submitted control run. Such a run must use existing `ncp_run_cancel` with current
canonical grants and admission in a separately authorized workflow.

A host deadline (up to 60 seconds) is checked before each service, not used to
preempt it. Each service retains its own native runtime limits. Four attempts per
tenant/principal/replay owner are allowed. A create-only, fsynced attempt marker
prevents a recreated runner from dispatching the same attempt. It contains a
request hash, never authorization or permission decisions. Used attempts refuse
without automatic replay. Receipt reads can be reopened under the same currently
authorized host scope, but there is no workflow resume service: hosts must inspect
retained intent and outcomes before intentionally starting a fresh attempt or
replay identity after a crash.

The marker's directory synchronization uses a persistent `DirectorySyncOwner`
within the runner. An ambiguous close blocks subsequent attempts before marker
creation; the descriptor number is diagnostic and is never retried or closed by
a destructor. Original cancellation and paired cleanup errors remain available
to trusted host code. This in-process hold does not establish power-loss
durability or lost-history restart recovery; the durable attempt marker still
prevents replay of the same attempt. No scientific operation has begun when
marker synchronization fails.

## Retained audit records

The runner uses the existing `retention.Store` with a tenant/principal/replay
owner derived by the host, with eviction disabled. It retains an initial audit,
a pending intent before each dispatch, the result and its hash after completion,
and a terminal summary. These are ordinary retained bundles using the existing
store, not evidence admission, signed receipts, SP1 proofs or verification of
source truth. Current scope and a content self-hash are checked on receipt reads.
Policy, tenant, membership, schema, capture, runtime or trust-registry changes
prevent stale receipt disclosure.

Raw facade calls check scope under the existing policy lock. Audited calls install
a context-local host scope guard at actual canonical dispatch after journal I/O
and again before disclosure. Receipt reads also recheck scope after retention
I/O. A capture rebind cannot publish a completed result under the earlier scope.

A refused admission never dispatches. A canonical uncertain outcome, interrupted
post-dispatch retention, over-budget result or unclassified post-dispatch failure
halts as `outcome_unknown`; no automatic retry occurs. The last retained intent
locator remains available. Existing upstream failure locators are preserved.
Returned unknown run states and lost coordinator workers also halt as unknown.
Queued/running work halts the recipe as `pending`; it does not count as a
completed step or trigger a dependent call. Returned failed/cancelled outcomes
remain distinct. Nested source fields named `status` are inert payload data.
Source text and results remain untrusted data, not recipe instructions.

Inputs/arguments/audit records are capped at 64 KiB; complete terminal output is
reserved before side effects and bounded by the requested 128..262144 byte budget.
The existing owner store limit (32 records / 4 MB) also applies. Capacity or I/O
failure before intent publication refuses before dispatch; a failure after a
possible effect is reported as uncertain. Hosts own explicit record release and
retention policy. Receipt locators cannot cross a tenant/principal/replay owner.

## Optional host-only content cache

`toolbox_cache` is a separate in-memory cache for schemas and explicitly approved
immutable reads. It is not used by `SkillRunner` and never memoizes admission,
writes, execution steps or fresh verification/receipt occurrences.

A fresh coherent `CacheAuthority` is required on every lookup and insertion.
Its `CacheScope` includes tenant, principal, current policy SHA-256, all relevant
source/runtime pins and exact replay identity. Schema/result namespace, content
version, exact schema hash and request hash are also part of the key. The host
must derive current schema hashes and content versions from its current catalog
and immutable source identities. These low-level inputs are not model arguments.
A source requiring fresh live data is not eligible as an immutable cached read.

An authority/scope change clears entries before returning data; a failed refresh
also clears. No permission decision persists. Result hits explicitly state
`fresh_execution=False`, `creates_receipt=False` and `authorizes_execution=False`.
Values are detached exact JSON, byte-bounded and LRU-evicted. Bounds measure
serialized entry/key/envelope bytes, not Python allocator overhead. The default
cache is 64 entries / 1 MiB, with at most 256 KiB per entry; host readers must have
their own resource bounds. No persistence or network is introduced.

## Qualification

Run the current source tests:

```sh
python -m pytest tests/test_context_selector.py tests/test_toolbox_skills.py tests/test_toolbox_cache.py tests/test_toolbox_campaign.py tests/test_toolbox_outcomes.py -q
```

The local tests execute real existing reference/evidence and logistics service
paths and campaign capture/trust profiles with synthetic fixtures. They cover
actual audited dispatch and persistent uncertain-outcome refusal, narrowed host
schemas, fresh per-step admission, revocation,
cancellation, owner separation, uncertainty, output limits, exact schema identity,
cache invalidation and unchanged discovery. They do not establish remote identity,
live provider access or production integration. The single integration owner must
run combined programme checks before any merge or deployment.
