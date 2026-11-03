# Database construction and maintenance

`ciw.data_database` implements the `ciw.data-database.v1` profile. Its seven
operations inspect one host-bound PostgreSQL namespace, propose closed DDL plans,
validate those plans in an empty private local database with rollback, and verify
host-supplied backup bytes. It does not apply operational changes or confer source,
scientific, NSL, or TBRT admission.

| Operation | Executed behavior |
| --- | --- |
| `ncp_db_plan` | Produce exact schema, index, or nullable-column addition SQL from the reviewed vocabulary, pinned to the observed database identity and schema. |
| `ncp_db_dry_run` | Require that exact plan reference, execute its DDL on the independently identified local scratch database, roll back, then verify cleanup through a new connection. |
| `ncp_dbops_health` | Read database identity, server version, recovery status, and the enforced read-only transaction status. |
| `ncp_dbops_schema` | Read structural catalog metadata, hashing default, constraint, and index expressions instead of returning their literals. |
| `ncp_dbops_drift` | Compare structural metadata with the exact host-reviewed baseline. Namespace existence is included. |
| `ncp_dbops_advice` | Return table statistics, absent reviewed index candidates, invalid indexes, and activity for explicitly bound query IDs. Suggestions do not establish an index's benefit. |
| `ncp_dbops_backup_verify` | Verify supplied immutable custom-format backup bytes and the exact pinned manifest. This is not a restore drill. |

There are no SQL, DSN, path, namespace, principal, credential, apply, or approve
request parameters. `ncp_db_dry_run` correctly declares `readOnlyHint=false`:
scratch DDL executes even though it must roll back. All operational database
connections are read-only. The profile's descriptions are a source inventory;
the normal principal facade must filter discovery and independently authorize
each call before a launcher exposes it through MCP.

## Trusted launcher contract

Construct `DatabaseVocabulary(value)` from a detached, reviewed, closed document:

```json
{
  "namespace": "reviewed_assets",
  "tables": {
    "assets": {
      "name": "assets",
      "columns": {
        "id": {"name": "id", "type": "integer", "nullable": false},
        "label": {"name": "label", "type": "string", "nullable": false}
      },
      "primary_key": ["id"]
    }
  },
  "indexes": {
    "by_label": {"name": "assets_by_label", "table": "assets", "columns": ["label"], "unique": false}
  },
  "changes": {
    "note": {"table": "assets", "column": {"name": "note", "type": "string", "nullable": true}}
  }
}
```

Physical identifiers are restricted lowercase PostgreSQL identifiers, quoted
by the implementation, and never selected directly by a model. Logical IDs
select reviewed entries only. Types are `string`→`text`, `integer`→`bigint`,
`number`→`double precision`, and `boolean`→`boolean`. Primary keys use non-null
string/integer columns. DDL permits schema/table creation, btree indexes, and
nullable column additions. Arbitrary expressions, defaults, functions, SQL,
destructive migrations, and concurrent index creation are absent.

The host calls `capture_schema(dsn, namespace)`, reviews that actual catalog
snapshot, then constructs:

```python
host = DataDatabaseHost(
    dsn=host_dsn, vocabulary=reviewed_vocabulary, baseline=reviewed_snapshot,
    principal=bound_principal, tenant=bound_tenant, source_ref=exact_source_ref,
    read_authority=read_current_authority,
    scratch=optional_local_scratch,
    backups=host_backup_objects, query_ids=host_query_ids,
)
```

`read_current_authority()` returns a fresh coherent JSON document with exactly
`principal`, `tenant`, `source_ref`, `revision`, `enabled`, and `operations`.
Those bindings must match, `enabled` must be exactly `true`, and the operation
must be present. Revision must change when the underlying reviewed authority
changes. Callback I/O belongs to the trusted host and must itself be bounded.
This source authority does not replace principal policy or establish source
license compliance. An unavailable/held source must fail this callback or
return disabled authority.

`request_scope_guard(name, arguments)` returns a callable that returns exactly
`True` while its original source/authority, host binding, module bytes, tool
contract, and requested backup bytes remain current. The integrated audited
facade must recheck it after admission and terminal audit/anchor I/O. Guards are
trusted launcher plumbing, never request arguments. Host callbacks are not a
sandbox against a local administrator.

Success receipts hash the exact canonical request and result and bind source,
module code, MCP interface, vocabulary, and baseline references. Plans additionally
bind the observed PostgreSQL system identifier, database OID, and database name
through a digest. The DSN, physical connection path, password, raw database identity,
query text, and driver exception text are not returned. These content hashes
establish byte identity, not signatures, independent evidence custody, or truth.
The existing outer audit/retention system provides its own custody contract.

## Transactions, pooling, and bounds

Inspection uses explicit `REPEATABLE READ READ ONLY` transactions. Each transaction
sets and reads back statement timeout 1s, lock timeout 500ms, idle-in-transaction
timeout 2s, synchronous commit on, and `search_path=pg_catalog`. Automatic prepared
statements are disabled (`prepare_threshold=None`). Settings are transaction-local;
there is no reliance on session affinity, session locks, `LISTEN`, extensions,
or a pooler resetting the session. Scratch DDL uses read-write transactions with
the same limits and an unconditional rollback savepoint.

Queries are fixed and bound, with limits of 16 relations, 512 columns, 128
constraints, 64 indexes, and 32 active-query rows. Exceeding a limit refuses the
whole response. Snapshots are capped at 128KiB and complete duplicated MCP output
at 256KiB. A vocabulary has at most 16 tables, 32 columns per table, 32 index
templates, and 32 nullable-column additions; one plan selects at most 16 entries.
Inputs are capped at 64KiB; backups at eight objects of at most 8MiB each.

Connections have a 3s connect timeout. Inspections check an 8s elapsed deadline
before returning; scratch checks the same deadline before each DDL statement.
The deadline is a checked bound, not asynchronous termination of arbitrary host
callbacks. Individual SQL statements retain their independent server timeout.
Driver failures return fixed codes and never trigger automatic retry or replay.

`pg_control_system()` and the required catalogs must be readable; otherwise the
profile refuses. No privilege is granted by this module. Active-query advice
requires host-selected numeric PostgreSQL query IDs and server query-ID collection;
it sees only that database and the connected role. It reports currently active
queries over one second, not historical slow-query analysis. The query text column
is never selected. Catalog statistics are estimates and may be stale.

These choices follow the [PostgreSQL control identity contract](https://www.postgresql.org/docs/17/functions-info.html#FUNCTIONS-CONTROLDATA),
[PostgreSQL activity/statistics contract](https://www.postgresql.org/docs/17/monitoring-stats.html),
and [PgBouncer transaction-pooling feature contract](https://www.pgbouncer.org/features.html).
Local PostgreSQL/PgBouncer qualification does not establish PlanetScale credentials,
catalog privileges, hosted behavior, failover, backup restoration, or deployment.

## Scratch isolation and failure custody

`ScratchDatabase(dsn, root=...)` binds an already-created empty local database; it
does not provision one. Its DSN accepts only host/port/database/user fields. The
database name must start `odsp_scratch_`. The UNIX socket and data directory must
lie beneath the same host-selected private root; directories must be owned by
the current OS user with mode 0700 and no symlink substitution. Server readback
must show TCP disabled and an actual UNIX socket connection. The observed system
identifier/database OID/name are pinned, and equality with the operational
database identity refuses before DDL.

Scratch must contain no user schema, relation, function, type, event trigger,
foreign server, publication, subscription, or extension other than the default
`plpgsql`. The module serializes calls on each
bound scratch object. Index/add-column tests build only the reviewed empty
synthetic base tables. They never copy operational rows into scratch. DDL is
rolled back, emptiness is checked inside the transaction and independently after
connection release. If cleanup cannot be established after DDL began, the result
is `outcome_unknown` and the scratch binding remains held. A later successful
read does not clear that hold. Host reconciliation is required; there is no reset
or recovery MCP operation.

Validation proves the selected DDL can execute on that empty synthetic baseline
under the local PostgreSQL version. It does not prove operational uniqueness,
data conversion, lock duration, capacity, availability, or migration suitability.
Human approval of the exact target/plan is still required for operational writes,
and this implementation has no operational application path.

## Backup bytes and qualification

`BackupArtifact(raw, metadata, manifest_ref=...)` requires exact bytes and a
separately pinned canonical manifest with schema `ciw.database-backup.v1`, format
`postgres-custom`, source/schema references, byte hash, byte count, and a supplied
capture-time label. It verifies `PGDMP` magic and exact hash/size bindings. It does
not parse every archive entry, attest the supplied timestamp, run `pg_restore`,
or claim a successful restore. Every response says `restore_drill=not_performed`
and `archive_semantics_verified=false`.

`tests/test_data_database.py` reuses the pinned private PG17.11/PgBouncer harness.
It executes native DDL and actual `pg_dump`, checks operational rows stay unchanged,
verifies rollback after backend errors and authority revocation, checks sticky
unknown cleanup, rejects the same operational database as scratch, and observes
a real active query without disclosing its text. It also exercises real read-only
enforcement, statement timeout, catalog privilege refusal, transaction-pool setting
isolation, row caps, reference mismatch, invalid vocabulary and injected fields.

```sh
PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 ODSP_REQUIRE_DISTRIBUTED=1 \
ODSP_DISTRIBUTED_PG_BIN=/path/to/pinned/postgresql/17/bin \
ODSP_DISTRIBUTED_NATS_SERVER=/path/to/pinned/nats-server \
ODSP_DISTRIBUTED_PGBOUNCER_BIN=/path/to/pinned/pgbouncer \
ODSP_DISTRIBUTED_EVIDENCE=/tmp/odsp-data-database/runtime \
python -m pytest -p no:cacheprovider tests/test_data_database.py -q
```

The fixture requires 1GiB free in `/tmp` and 512MiB in `/workspace`. Logs and failed
qualification receipts remain available. No source licensing hold, PPDA/SRA
assessment, NSL replay, or TBRT evidence requirement is discharged by a database
plan, dry run, or content hash. All successful profile receipts explicitly retain
`source_license_verified=false`, `nsl_admission=not_established`,
`tbrt_evidence=not_bound`, and `authorizes_state_admission=false`.
