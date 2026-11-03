# Server toolbox, signed workflows and persistent content cache

The server toolbox adds registered MCP operations to the existing principal
facade. It executes existing canonical services; it does not import another
domain engine or grant permissions through a recipe. The earlier client context
selector and in-memory cache remain available and unchanged.

## Supported lifecycle

The launcher configures an allowlist of component identifiers and existing
canonical operation names. A client with the corresponding explicit management
grant can inspect a component, load its exact digest, invoke one operation, and
unload it. Activation is persisted in SQLite and shared by server processes for
that tenant. Loading does not grant the underlying operation. Calls recheck the
current management grant, component generation, exact visible schemas, and the
independent canonical operation/scientific grants at dispatch.

`tools/list` always exposes the same configured management surface, filtered by
current principal authorization. Load/unload affects server routing through
`ncp_toolbox_call`; it does not add connection-specific tools to discovery. This
follows the [official MCP tools discovery requirement](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
that discovery must not change as a side effect of other requests on a
connection. The existing transport's supported protocol versions remain
unchanged. This is server activation of already approved components, not code
installation, plugin downloading, arbitrary modules, or a new MCP protocol.

| MCP operation | Behavior |
| --- | --- |
| `ncp_toolbox_list` | List currently visible approved components and activation generations. |
| `ncp_toolbox_describe` | Read the exact component schema set and digest. |
| `ncp_toolbox_load` | Activate the exact approved component digest for the tenant. |
| `ncp_toolbox_unload` | Disable future calls; existing provider effects are not undone. |
| `ncp_toolbox_call` | Dispatch a member operation through current canonical authorization and audit. |
| `ncp_skill_list` | List persisted, currently signed/trusted workflow versions. |
| `ncp_skill_describe` | Verify and read an exact workflow envelope. |
| `ncp_skill_run` | Claim an attempt once and run its signed canonical steps. |
| `ncp_skill_cancel` | Persist a cooperative cancellation request, visible to other server processes. |
| `ncp_skill_receipt` | Reopen the final or specified retained receipt within that attempt. |

The surface is registered through `PrincipalPolicy`, `PrincipalFacade`,
`services.invoke`, the source catalog, and the existing MCP server. Raw-host
`services.invoke` cannot bypass the principal facade for these operations.

## Host composition and audit

`MCPToolboxHost` requires a host-selected private SQLite path, `WorkflowRegistry`,
`PrincipalPolicy`, principal, tenant, runtime/source pins, replay identity,
component allowlist, and per-step admission callback. Admission defaults to
deny. No constructor binding, path, callback, signing key, credential or grant
is accepted as a model argument.

The launcher creates two audit boundaries:

1. An inner `AuditedPrincipalHost` wraps a facade containing the approved
   canonical profile hosts. Its audit allowlist excludes all toolbox and skill
   management operations.
2. A management facade binds `toolbox=toolbox_host`; an independent outer
   `AuditedPrincipalHost` can audit its management operations.

Both facades use the same principal and the **same** `PrincipalPolicy` instance.
Call `toolbox_host.bind_dispatch(inner_audited_host)` once. Separate audit
journals and independent durable CAS anchors are necessary: the existing audit
chain deliberately refuses another admission while an earlier call on that
chain is unresolved. This implementation preserves that rule.

```python
registry = WorkflowRegistry(workflow_db, read_current_host_trust)
# host imports a previously signed envelope; no MCP publication operation exists
registry.publish(approved_signed_envelope)
toolbox = MCPToolboxHost(
    path=toolbox_db, registry=registry, policy=current_policy,
    principal=authenticated_principal, tenant=host_tenant,
    pins=qualified_public_pins, replay_identity=host_replay,
    components={"reference": ["ncp_knowledge_resolve", "ncp_evidence_search", "ncp_evidence_span"]},
    admit_step=host_step_admission,
).bind_dispatch(inner_audited_host)
management = current_policy.facade(authenticated_principal, toolbox=toolbox)
# Wrap management with its own existing audit chain/anchor, then serve normally.
```

An admission includes the exact compiled recipe, schema, scope and argument
hashes. Signer trust and definition identity are refreshed before admission,
after admission, after retained intent I/O, at canonical audit dispatch, and
before disclosure. Native execution remains the existing canonical path.

The outer audit obtains a host-only request scope guard through the principal
facade. The guard binds the detached request, current management policy,
workflow signature and trust generation, validity window, exact visible
schemas, runtime pins, and receipt-attempt identity. Component calls bind their
activation generation as well as the underlying operation grants. Guards run
after audit admission I/O and after terminal anchor I/O. Signer or grant
revocation, expiry, or rebinding then withholds returned data. A terminal event
already committed remains in the journal; the caller receives an unknown
outcome rather than a claim that the earlier read or effect was prevented.
Cancellation still checks its own current grant and can stop a workflow whose
signer has been revoked. No guard callback is accepted through MCP arguments.

The selected `PrincipalPolicy` implementation owns `authorize_context`. The
toolbox invokes that virtual method directly, including for a host-selected
native policy, and never reconstructs a Python policy as an authorization
fallback.

## Signed workflow registry

`WorkflowRegistry` stores immutable `(tenant, workflow_id, version)` envelopes.
The payload binds the full `ncp.client-skill.v1` definition, tenant, revision,
validity interval, exact tool schema hashes, and runtime/source pins. Ed25519
signing bytes are `signing_bytes(payload, key_id)`, with a fixed ODSP domain
prefix and the repository's deterministic Python JSON contract. This is not a
claim of RFC 8785/JCS serialization.

The trust document has schema `odsp.workflow-trust.v1`, registry identity,
monotonic generation, scoped public keys, and revoked payload digests. A key
entry binds its SHA-256 public-key fingerprint, tenants, workflow identifiers,
enabled state and minimum accepted revision. Every read verifies the signature,
current scope/revocation, validity window and persisted row binding. A narrower
host schema may be signed; execution requires an exact match to the current
principal-visible schema. Workflows cannot call the toolbox/skill management
surface recursively.

Validity is checked again after the final host trust read. A verified envelope
is rechecked after policy/schema I/O before disclosure; verification performed
before a blocking read cannot keep a revoked or expired definition usable.

The trusted reader must reload current independently persisted configuration.
Reusing a generation with different contents, or regressing generations within
a process, is refused. Persisted minimum revisions and revoked digests prevent
old signed definitions from becoming usable after a server restart. Restoring
both the database and the external trust configuration still requires an
independent monotonic trust anchor to detect rollback; this module does not
claim to solve that with SQLite alone. Signing keys are never generated or
stored by the registry.

The registry and activation/attempt store currently share state across
processes on **one machine** through their private SQLite files. Signed
envelopes can be verified on other hosts, but that is not a shared multi-host
registry or distributed workflow scheduler. No network filesystem or PostgreSQL
registry backend is claimed. The PostgreSQL cache described below has a
separate storage contract and does not turn workflow execution into a cached
operation.

## Attempt custody and cancellation

The shared attempt table records the exact request and signed workflow digest
before execution. Duplicate attempts refuse across processes and restarts.
Each attempt has a deterministic host-owned retention scope. The existing
`SkillRunner` retains pre-dispatch intents, results and final receipts. Another
attempt or principal cannot read those records by guessing a locator.

`ncp_skill_receipt({"attempt": ...})` reopens the final retained record. If a
process stopped before persisting the final locator, inspection returns
`outcome_unknown`, `receipt_available=false`, and `retry_authorized=false`.
It never automatically replays the attempt. Existing retained evidence remains
in its owner store for host reconciliation.

Cancellation is cooperative. Another server process can persist it while the
current synchronous request waits at admission. The runner rechecks it before
dispatch and between steps. It cannot forcibly interrupt a native provider or
promise rollback. The sequential stdio transport cannot process a second
request while a call is active; cancellation then requires another authorized
server process sharing the same state. This is not a detached job scheduler.

Unknown step outcomes and pending execution propagate as top-level uncertainty
for `ncp_skill_run`, including the retained locator when available. Outer audit
does not record uncertain execution as success. A pre-effect refusal is tagged
`effect_state=not_started` only before an application write or canonical
dispatch was attempted. Later failures remain unknown, even when SQLite might
have rolled back. The tag concerns this request, not an earlier attempt.

## Persistent cache

`PersistentCache(path, read_authority, ...)` implements the existing schema and
immutable-result cache API using shared SQLite transactions. Every operation
requires a fresh exact `CacheAuthority` snapshot. Keys bind tenant, principal,
policy digest, all host runtime/source pins, replay identity, namespace, schema
hash, content version and request hash. Authority is checked again after
storage I/O and before returning data. An authority change removes the old
owner partition on the next successful access. Failed authorization discloses
no content.

Only explicit host `immutable_reads` are eligible; `readOnlyHint` alone is not
classification. The cache is not wired into workflow step execution. Hits say
`fresh_execution=false`, `creates_receipt=false`, and
`authorizes_execution=false`. Permission decisions, admissions, writes and
verification occurrences are never cached. Host-authorized immutable results
are data, not a substitute for a fresh invocation or scientific receipt.

Global LRU bounds are applied in the same transaction as each insertion.
Database metadata fixes the limits across processes; reopening with wider
limits refuses. Defaults are 64 entries, 1 MiB total serialized key/envelope
bytes, and 256 KiB per entry. Values are detached JSON and carry corruption
checksums; those checksums are not signatures against a malicious administrator.

All three SQLite databases require a private same-uid directory (mode 0700)
and private regular files (0600), with symlinks and hardlinked database files
refused. WAL with FULL synchronization is used; physical power-loss behavior
has not been qualified. The backend is **single-machine shared persistence**.
Do not place SQLite on a network filesystem or advertise it as a distributed
cache.

### PostgreSQL backend

`PostgresPersistentCache(dsn, read_authority, cache_id=...)` supplies the same
public methods and exact content contract over a shared PostgreSQL database.
It inherits the authority, key, encoding and corruption checks from
`PersistentCache`; execution paths and permission decisions are never cached.
The launcher owns the DSN and cache identifier. They are not model arguments,
and connection errors expose only a fixed diagnostic. Every operation opens a
fresh connection; no connection is inherited across a process fork.

Runtime connections disable named prepared statements. Each operation starts
an explicit read/write, read-committed transaction and applies bounded query,
lock and idle timeouts, synchronous commit and a fixed search path using
`SET LOCAL`. All settings are read back before storage access. Commit or
rollback releases these settings before the pooled backend can serve another
client. This avoids relying on libpq startup options or session state.

The local qualification uses actual PgBouncer transaction pooling and proves
backend reuse, settings cleanup after commit and rollback, process sharing,
database crash recovery and the original authority/content invariants. It is
compatibility evidence, not a connection to PlanetScale or a hosted failover
test. [PlanetScale documents transaction pooling for application traffic](https://planetscale.com/docs/postgres/connecting/pgbouncer)
and recommends the direct connection for schema changes. Use separate host
bindings for migration and runtime endpoints; never route custody through a
read replica. Hosted TLS hostname/CA verification, role restrictions, backup
restore and uncertain-commit recovery must be qualified before activation.

An explicit `ciw.postgres_cache.install_schema(dsn)` migration creates the fixed
`odsp_persistent_cache_v1` SQL namespace in an already authorized database.
Importing the module and constructing a runtime cache do not install schema,
create accounts or generate credentials. PostgreSQL 14 or later is required;
the migration checks `fsync` and `full_page_writes`, and connections require
synchronous commits. The migration role needs schema creation rights. Runtime
roles need `USAGE` on that schema, `SELECT` on its `format` table, and
`SELECT/INSERT/UPDATE/DELETE` on `caches`, `entries` and `scopes`. Hosted role
provisioning, TLS verification and DSN secret binding remain deployment work.

```python
from ciw.postgres_cache import PostgresPersistentCache, install_schema
install_schema(authorized_admin_dsn)  # explicit trusted migration
cache = PostgresPersistentCache(
    existing_runtime_dsn, read_current_cache_authority,
    cache_id="approved-shared-content", max_entries=64, max_bytes=1024 * 1024,
)
```

Every read/write locks the fixed cache's metadata row before reading content,
advancing LRU order, refreshing an owner partition or evicting entries. That
transaction applies one count/byte budget across all clients using the same
cache identifier. Different cache identifiers are separate host-configured
budgets. Schema format and configured limits must match on every reopen; an
existing client also rechecks the limits under its lock. A digest index avoids
PostgreSQL index-size limits for escaped JSON keys; the full stored key is
compared before disclosure or replacement. Data values remain SQL parameters.

These rules use PostgreSQL's [transaction-held row locks](https://www.postgresql.org/docs/current/explicit-locking.html)
and psycopg's [transaction context commit/rollback behavior](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).
Authority is refreshed after acquiring the lock, before commit and again before
returning. A failed reader or storage operation refuses without an automatic
retry. Every host must provide a current coherent authority reader; the cache
cannot repair an externally stale policy source. Serialized budget limits
exclude PostgreSQL indexes, MVCC dead tuples, WAL and allocator overhead;
database capacity and maintenance are host responsibilities.

## Executed local qualification

```sh
PYTHONPATH=src:tests python -m pytest \
  tests/test_persistent_cache.py tests/test_workflow_registry.py \
  tests/test_mcp_toolbox.py -q

# With the repository's pinned local runtime paths and drivers configured:
ODSP_REQUIRE_DISTRIBUTED=1 PYTHONPATH=src:tests python -m pytest \
  tests/test_postgres_cache.py -q
```

The tests use actual local SQLite databases, separately spawned processes,
concurrent writers, Ed25519 signatures, canonical reference/evidence services,
existing retained storage, separate hash-chained audit journals and durable
SQLite CAS anchors. A parent JSON-RPC client talks to the real existing MCP
server in a separate process. Failure checks cover signer/grant revocation,
tampering, duplicate/restarted attempts, cancellation, uncertain audit custody,
scope separation and invalid arguments. The fixed signing key in tests is a
public synthetic fixture and is not suitable for operational trust.

The independent `tests/test_server_toolbox_review.py` exercises the official
MCP SDK over the local Streamable HTTP/OAuth fixture, concurrent signed
publication and storage-lock revocation. Its original signer-revocation
failures are repaired: later policy I/O cannot disclose a previously verified
definition, and terminal audit-anchor I/O cannot disclose a revoked workflow
receipt. Additional tests cover expiry at both trust and audit boundaries,
underlying component-grant revocation, native-policy denial without fallback,
and preserving the terminal audit event when returned data is withheld.
Optional imported-native-profile checks still require their explicit pinned
checkout/interpreter bindings; a skipped provider check is not qualification.

PostgreSQL checks use the repository's private local runtime harness, actual
PostgreSQL 17.11 and psycopg 3.3.7. Separate spawned clients share persisted
values; four contending writer processes exercise the common budget. Tests
also crash/restart the real database, verify byte-contract parity with SQLite,
reject forged execution flags and corrupt content, and revoke authority during
lock acquisition or after storage reads. The harness disables PostgreSQL TCP
and cleans up its local processes. These runs establish database-backed sharing
between independent clients; they do not qualify hosted networking or an
operational multi-host deployment.

These results establish local implementation behavior only. They do not
establish hosted authorization, a deployed registry/cache, OAuth qualification,
native scientific kernel equivalence, physical device operation, or a complete
release qualification.
