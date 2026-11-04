# Local synthetic cross-store recovery drill

This drill restores one quiesced synthetic internal-use recovery set containing
PostgreSQL coordinator custody, the survey ledger and retained result, the
observation journal, and the original held sensor/survey workbench projection.
It uses actual isolated PostgreSQL and authenticated loopback NATS processes.
It is test infrastructure in
[`cross_store_fixture.py`](../tools/distributed-coordinator/cross_store_fixture.py),
not an MCP tool, deployment launcher, general backup service or activation API.

The [existing native coordinator restore](DISTRIBUTED_COORDINATOR.md) and
[operator inspection](INTERNAL_OPERATOR_RECOVERY.md) remain the underlying paths.
The Atelier observation-journal candidate must be separately installed or
explicitly supplied by the trusted test launcher; this file does not publish a
dependency, qualify equipment commands or make that source a released package.

## Recovery set and cutover

The test host binds every participant, tenant/principal, retention owner,
selected source/capture, journal jobs and projection occurrence. It supplies a
separate expected recovery-set hash and requires explicit quiescence of all
owned workers. The set cannot choose a database endpoint, executable, callback,
device, active principal policy or recovery command.

1. Freeze writes to the two local SQLite participants and the retained owner's
   sequence under their existing transaction/owner locks. All fixture workers
   are synchronous and quiesced by the host. These locks do not fence remote
   workers or an uncooperative process with the same uid.
2. Take the existing PostgreSQL custom dump and custody snapshot from one native
   exported SQL snapshot. Use SQLite's native backup API to include committed
   WAL data, then verify integrity, table schemas and logical row fingerprints.
   Normalize only the new backup copies to standalone SQLite files. No live
   database/WAL file copy or production setting change is used.
3. Copy the explicit bounded immutable workbench/survey evidence and retained
   result plus owner sequence. Runtime locks are recreated, not restored as
   authority. A closed manifest pins every member, byte length, both logical
   SQLite states and the participant identities. Limits are 64 members, 16 MiB
   per member and 32 MiB total.
4. Verify the externally supplied manifest pin, complete member set, paths,
   regular-file ownership/link restrictions, native database fingerprints and
   cross-store links. A well-formed journal with the same owner/binding but the
   wrong event history also refuses. Historical policy JSON is preserved only
   as inert evidence; it is never loaded as the recovered host's authority.
5. Verify that PostgreSQL, both SQLite states and all selected evidence files
   still match the checkpoint. Any advancement requires a new complete set.
   Stage create-only copies in a new private directory and verify them again.
   Only then stop the owned source services and perform the existing native
   restore into an empty destination. A failure leaves recovery held and
   preserves source, backup and partial-destination evidence. There is no
   automatic cleanup, activation or retry.
6. Supply fresh host callbacks, current principal policy and a compatible
   antirollback clock before reopening participants. Validate raw survey
   provenance, journal histories and the original projection. Reopening may
   append a fresh survey assessment or advance clock bookkeeping; it does not
   change the old capture, measurement, result or execution identity.

The capture is a coordinated **single-host, quiesced, still-current** cutover.
It is not an atomic distributed snapshot, historical disaster recovery, or
fencing of surviving remote writers. Content hashes establish local custody;
they are not signatures, authenticated time, evidence admission or protection
against an administrator rewriting the entire set and its trusted anchor.

## Uncertain outcomes and no resend

The fixture uses the explicit synthetic operation `fixture.held-projection.v1`
and same-PostgreSQL SQL effects, rather than claiming a production canonical
mutation or external-device fence. It preserves five coordinator jobs: one
completed held result, a committed SQL effect whose reply was lost, an
interrupted dispatch, and two still-queued notifications. The later queued job
also demonstrates rejection of an earlier stale checkpoint.

After restoration, completed and uncertain jobs keep their exact original
requests/history. Explicit recovery advances the expired dispatch's fence and
marks it `outcome_unknown`. The old endpoint and old adapter claim cannot settle
it. No terminal job is requeued or dispatched. The fresh broker has no old
stream state: operator reconciliation republishes only the two queued wake-ups,
and both jobs remain queued. A saved PubAck is not broker recovery evidence.
The two existing SQL effect rows remain exactly two.

The completed sensor recording contains two arrivals of one measurement. A
later simulated lost read reply creates a sticky journal hold. Both histories
survive, a same-key retry returns the unknown history without inspecting a
source, and journal recovery reads no source. The hold prevents a new plan.
Current grant revocation and clock rollback refuse after recovery. No saved
grant or old policy file restores permission.

The retained projection stays held for human review and retains its original
execution/result/model/source/bridge identities. It is not recomputed. Its
correlated variance is still 0.000212 m², with a sensor-minus-survey discrepancy
of approximately 0.004 m. Frames, metre units and the declared synthetic clock
and observation time are preserved. Current journal readiness is shown as held
separately from historical projection integrity by the private operator path.

## Local verification and reproduction

[`test_cross_store_recovery.py`](../tests/test_cross_store_recovery.py) exercises
actual native dump/restore and NATS PubAcks, corruption, missing/unlisted files,
symlinks/hardlinks, unsafe paths, owner/journal mismatches, externally wrong pins,
missing quiescence, occupied destinations and participant advancement. It then
reopens the full journey using fresh authority with source reads, service
invocation and numerical execution replaced by failure sentinels.

[`verify_cross_store.py`](../tools/distributed-coordinator/verify_cross_store.py)
runs in a separate interpreter with only stdlib and the PostgreSQL driver. It
does not import candidate validators, queue, acquisition, numerical or Session
code. It independently checks member bytes, immutable ledger rows, journal
chains/hold, owner and source links, original occurrence identities, request and
event hashes, coordinator states/fences and exact SQL effects. Decimal
arithmetic independently checks the correlated uncertainty. This is independent
of implementation helpers, not an external audit or physical measurement.

Provide reviewed, already available runtime binaries and their expected hashes,
the core and journal candidates, and new private scratch/evidence directories:

```sh
export ODSP_DISTRIBUTED_PG_BIN=/approved/postgresql/bin
export ODSP_DISTRIBUTED_NATS_SERVER=/approved/nats-server
export ODSP_CROSS_STORE_BINARY_PINS="$(cat /approved/runtime-sha256.json)"
export ODSP_CROSS_STORE_SCRATCH=/private/new-scratch
export ODSP_CROSS_STORE_EVIDENCE=/private/new-evidence
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:cacheprovider \
  tests/test_cross_store_recovery.py -q \
  --basetemp=/private/new-unused-pytest-path \
  --junitxml=/private/new-evidence/tests.xml
```

The runtime-pin object requires `initdb`, `postgres`, `pg_ctl`, `pg_dump`,
`pg_restore` and `nats-server` SHA-256 values. Missing runtimes fail this selected
qualification instead of silently skipping it. Use a private umask (077), no
inherited service credentials, unique output directories and explicit import
paths or installed candidates. The tests install only their own empty local
schema. Never point them at an existing database or broker.

The executed local drill used private shared-memory scratch because disk-backed
evidence volumes were close to preservation floors. Native PostgreSQL retained
`fsync`/full-page writes and SQLite retained its existing FULL/WAL contract, but
shared-memory execution **does not test storage power-loss durability**. The
native backups, final canonical SQL custody, final native SQLite backups,
immutable members, independent output, operator report and service logs are
preserved on the disk-backed evidence volume before scratch teardown. Broker
credentials and runtime configuration are excluded from the recovery set.

## Gates that remain open

This local evidence does not qualify hosted PlanetScale identity/TLS/pooling,
managed backup or restore, replicated failover, surviving writers, NATS cluster
durability, production RPO/RTO, object-store retention, long-lived backup key
custody, historical rollback or the full scientific baseline. The cross-store
lane runs against source candidates and real local services; it is not an
installed multi-host deployment or a second transaction-pooling qualification.

Company source clearance, authenticated TBRT time, NSL admission, physical
calibration and a specifically authorized read-only physical instrument pilot
remain separate. No equipment connection, command, actuation, hosted resource,
credential change, deployment or publication is performed by this drill.
