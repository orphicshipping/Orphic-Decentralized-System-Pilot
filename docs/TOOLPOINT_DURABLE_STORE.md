# NET tool-point QC: transactional local capture storage

For current installed commands, qualification scope and snapshot compatibility, see
[Native QC integration](TOOLPOINT_UPGRADES_INTEGRATION.md).

## Delivered capability and boundary

This additive increment persists the existing append-only QC capture journal in a
local SQLite database. It preserves the earlier journal, both QC evaluators,
manufacturing reducer, genealogy projection, canonical content encoder and all
physical/release authority boundaries. No existing source file is modified.

The practical chain is now:

```text
Accepted tool-point capture
  -> unchanged QC evaluation and latched findings
  -> atomic local database commit
  -> acknowledgement carrying the original journal receipt
  -> process restart / complete replay
  -> existing component, assembly and package containment
```

This database is a bounded local evidence cache. It is NOT a replacement canonical
State Ledger, an authenticated source, a distributed consensus system, a machine
controller, a quality certification authority, or a high-frequency sensor driver.

## Implementation

| New module | Responsibility |
| --- | --- |
| `ciw.toolpoint_stream_codec` | Lossless cumulative-to-delta storage and exact reconstruction of the original native update. |
| `ciw.toolpoint_stream_store` | Create-only database initialization, stream registration, transactional append/import, restart replay, audit, provenance and external-head checks. |
| `ciw.toolpoint_store_cli` | Directly executable local-store command-line interface. |

Runtime dependencies are Python's standard library and the existing NET modules.
No new third-party runtime package or dependency pin is introduced. The locally
exercised environment is CPython 3.13.5, SQLite 3.46.1, Linux and pytest 9.0.2.

The implementation composes with both existing plan profiles:

- `ciw.toolpoint-control-plan.v1`, through the previously delivered legacy evaluator.
- `ciw.toolpoint-monitoring-plan.v1`, through the unchanged native evaluator from
  PR #177 at `dd8782a9df195a79769cbe9e8d63579578dc248e`.

The native evaluator's Git blob remains
`d44a637ea217c377c12d8f80fd12838ad891f739`. The native evaluator itself is exercised,
not the entire current manufacturing-v2 repository journey. Both adapters and the
attached manufacturing foundation are present in the focused demonstration snapshot.

## Atomic append and retry semantics

A writer starts `BEGIN IMMEDIATE`, loads and replays the accepted history, checks
its exact expected specification and prior receipt, evaluates the next update with
the existing journal, and inserts its delta plus acknowledgement in one transaction.
The API returns the acknowledgement only after `COMMIT` completes.

The update's `previous_receipt_ref` is its expected-parent condition. In the
convenience `append_capture` interface, the caller supplies `expected_head_ref`
explicitly; `None` denotes only the initial parent. The code never silently replaces
a stale parent with a newer head.

An identical retry returns the exact original acknowledgement, including its stored
write-execution identity. A conflicting retry cannot replace the accepted capture.
A retry of an older accepted sequence may succeed even after later updates; that
acknowledgement attests to its sequence, NOT to the current database head.

A committed HELD capture is not a storage failure. Storage status, QC status,
evaluation identity, verification identity and physical authority remain separate.
The API provides no release, rewrite, hold-closure, delete or physical-action method.

### Interruption cases exercised

| Interruption | Observed recovery behavior |
| --- | --- |
| Writer killed before commit, with a nonempty rollback journal | The uncommitted update is absent after reopening; the previous accepted prefix remains valid. |
| Writer killed after commit but before acknowledgement | Reopening finds the accepted update; an identical retry returns its original receipt without a second row. |
| Two processes submit different updates against one parent | Exactly one update is accepted; the conflicting update is rejected. |
| Two processes submit identical updates against one parent | Both obtain the same acknowledgement, with one accepted row. |
| Commit function raises an injected error | The transaction is rolled back and no success acknowledgement is returned. |
| Another connection holds the writer lock | The configured lock timeout fails closed rather than accepting an unpersisted update. |

The process-kill tests use real subprocesses and SIGKILL. The pre-commit test forces
a small SQLite page cache and confirms a rollback journal exists before terminating
the writer. These are process-interruption tests, NOT an electrical power-cut,
controller, storage-device, operating-system crash or filesystem qualification.

## Durability profile and filesystem assumptions

The store requires rollback-journal DELETE mode, `synchronous=EXTRA`, foreign keys,
an application ID and a fixed supported schema version. It checks the exact table
and immutability-trigger definitions before operating. SQLite's EXTRA mode adds a
directory sync when the rollback journal is unlinked. An unsupported profile is
refused instead of silently using weaker durability settings.

Database publication is create-only: initialize a temporary database, complete it,
flush it, and publish it with an exclusive hard link. Existing paths are never
replaced. On POSIX, the containing directory is also synced. A failure after
publication may leave a valid database: an error response is not proof that no
commit or publication occurred. Inspect the store or retry the identical update.

Use a trusted local directory and a filesystem with working locks and sync semantics.
Do not put this database on NFS/SMB/network storage, change its journal mode, remove
a hot journal, or copy a live database with ordinary filesystem tools. This increment
does not provide an online backup/restore service. Symlink paths, hard-linked
existing database files, unsupported WAL sidecars and nonregular inputs are refused.
Hostile directory replacement races and administrators with unrestricted filesystem
access are outside the protection boundary.

Primary implementation references inspected 2026-10-05:

- SQLite, Transactions: https://www.sqlite.org/lang_transaction.html
- SQLite, PRAGMA synchronous: https://www.sqlite.org/pragma.html#pragma_synchronous
- SQLite, Atomic Commit and filesystem assumptions: https://www.sqlite.org/atomiccommit.html
- Python, sqlite3 interface: https://docs.python.org/3/library/sqlite3.html

## Lossless delta storage, without changing scientific identity

The persisted representation stores each sensor's newly appended samples plus its
sample-free metadata, the prior sample count and the expected original update hash.
Reconstruction checks the previous update reference, sensor identities, sample
counts and exact full-update content identity. Only then does the unchanged journal
reevaluate the original cumulative update and verify its receipt.

This is not lossy compression, resampling, interpolation, unit conversion, a new
feature evaluator, or a changed canonical hash algorithm. It does not allow old
samples to be edited, erased or backfilled into an acknowledged interval.

In the retained synthetic storage exercise (3 channels, 81 samples/channel,
16 prefixes):

| Quantity | Result |
| --- | --- |
| Repeated sample copies in cumulative inputs | 2,088 |
| Sample copies stored in deltas | 243 |
| Cumulative input JSON payload | 169,595 bytes |
| Delta JSON payload | 63,521 bytes |
| Delta JSON payload reduction | About 62.5% |
| Final QC result and original receipts | Exactly equal to the existing journal |

The comparison is JSON payload, not total database size or runtime speed. SQLite
pages, receipts and indexes have overhead; small captures can have a larger delta
payload than their cumulative inputs. Export still reconstructs the original
cumulative report. Its existing 2 MiB/tree/update/sample bounds remain unchanged.
Repeated-prefix replay and export still have potentially quadratic work/memory;
this is not a claim of unlimited capture capacity or real-time throughput.

## Migration and source provenance

`import_report` accepts an existing journal report, checks caller-supplied source
specification/head anchors, and verifies every accepted prefix before import.
Registration, all newly needed entries and the import provenance record commit in
one transaction. A mid-import error cannot leave a newly registered partial capture.

The original report's content identity, original execution identity, original head,
original result identity and a distinct import-execution identity are retained in
an immutable import record. Provenance verification reconstructs the original
report, including its original occurrence identity, and checks its exact hash.

Reimporting the same source is idempotent. Importing an older valid prefix into a
newer local stream does not roll back the newer head or remove a later hold. A
changed plan/start declaration cannot create another stream in the same declared
configuration/clock/cycle/source slot. This guards local history consistency; it
does not authenticate those declared physical identities.

## Integrity, rollback and authority

Every export reads a transaction-consistent database snapshot, reconstructs all
native updates and freshly replays them. Cached statuses are not trusted. Changed
source bytes, altered sample offsets, missing middle entries, modified receipts,
recomputed-hash hold clearance and corrupted import provenance are rejected.

An audit during implementation reproduced a Python-equality pitfall: changing a
receipt sequence from integer `1` to Boolean `True` could compare equal as a Python
dictionary. Stored acknowledgement verification now compares exact canonical
content identities. Tests also reject the numeric alias `1.0`. This was caught and
fixed in the new storage code before this package was delivered.

A coherent replacement of an entire database or a valid historical prefix cannot
be distinguished from the intended source by unanchored hashes alone. Callers can
supply separately retained expected specification, journal-head and storage-commit
references. Taking an anchor from the same untrusted database does not establish
freshness or authenticity. SQL immutability triggers are accidental-modification
guards, not protection against an administrator who can rewrite the database.

The following remain invariant:

```text
hardware_actuation      = not_performed
physical_containment    = not_performed
physical_qualification  = not_established
state_admission         = not_performed
source_authentication   = not_established
shipment_release        = not_authorized
action_authorized      = false
```

## Demonstrated manufacturing connection

The retained demonstration imports two synthetic cycle journals, closes and reopens
the database, exports their unchanged report schemas, and invokes the existing
manufacturing containment projection. In cycle 0, an early cavity-2 sensor fault
recovers later. The final native signature alone is acceptable, but its accepted
fault history remains latched.

```text
cavity-2 sensor fault -> p002 HELD despite dimensional PASS
                     -> descendant assembly a001 HELD
                     -> associated package HELD
p003 remains ELIGIBLE_FOR_REVIEW
p004 remains SCRAPPED
shipment is not authorized
```

This is a read-only projection over retained manufacturing records. It does not
physically stop a machine, sort a part, intercept every manufacturing transaction,
release a shipment, or authenticate a human approval.

## Run the included focused snapshot

From `focused_snapshot/` in the ZIP, using PowerShell:

```powershell
$env:PYTHONPATH="src"
$checkpoint = Get-Content examples/durable-quality/checkpoint.json -Raw | ConvertFrom-Json
$specRef = $checkpoint.spec_ref
$headRef = $checkpoint.receipts[-1].receipt_ref

python -m ciw.toolpoint_store_cli init qc.sqlite3
python -m ciw.toolpoint_store_cli import qc.sqlite3 cycle0 examples/durable-quality/checkpoint.json --expected-spec-ref $specRef --expected-head-ref $headRef
python -m ciw.toolpoint_store_cli verify qc.sqlite3 cycle0 --expected-spec-ref $specRef --expected-head-ref $headRef
python -m ciw.toolpoint_store_cli provenance qc.sqlite3 cycle0
python -m ciw.toolpoint_store_cli export qc.sqlite3 cycle0 --output reopened-stream.json
```

PowerShell syntax is provided for the user's environment; Windows execution has not
been performed. On a POSIX shell set `export PYTHONPATH=src` and use the same module
commands. Use fresh database and output filenames.

For new captures, `register DATABASE STREAM SPEC` creates an empty, explicitly
`AWAITING_EVIDENCE` stream. `append DATABASE STREAM UPDATE --expected-spec-ref REF`
accepts an existing native journal update. `append-capture` instead takes the native
capture, explicit parent/spec anchors, receipt time and finalization flag. Use the
original receipt returned from the previous commit as the next parent, not a
silently refreshed head after a failure.

`head` shows current evidence status. `verify` checks one stream; `audit` checks
SQLite structure, foreign keys and all registered histories in a single snapshot.
`provenance` returns the verified import-origin records. `export` produces the
original report schema usable by `ciw.toolpoint_stream_cli verify` and containment.

Mutating commands emit their acknowledgement only to stdout; they do not add a
second file-publication step that might fail after a successful commit. Export and
read-only output files retain the existing atomic/create-only file helper.

Exit 2 means HELD, PENDING or AWAITING_EVIDENCE, even when storage status is COMMITTED.
Exit 0 from verification/audit means that software verification completed, not that
the product is conforming. Exit 1 is a refusal/error; inspect or retry identically
when the outcome is uncertain. No response should be interpreted as machine release.

## Qualification and finite limits

Combined focused test run: **798 passed, 0 failed, 1 skipped**, comprising 160 new
storage tests and all 638 previously passing foundation/QC/journal tests. The skip
is the pre-existing full-polymer-dispatcher test whose unrelated NET dependencies
are absent from the focused snapshot. The real new CLI and subprocess paths ran.

Branch-enabled coverage rounds to **92% across the three new runtime modules only**
(485 statements, 180 branches). This is not repository-wide or physical coverage.
Raw test/JUnit output, coverage data, checksum manifests, synthetic databases,
reopened reports, import provenance, containment and storage metrics are retained.
A clean baseline patch-application check and rerun are included separately.

Local limits are 128 streams/database, 16 imported source checkpoints/stream,
128 MiB database-page budget and the unchanged journal maxima (at most 64 updates,
512 legacy or 2,048 native samples/sensor, with tighter effective global tree/byte
bounds). The lock timeout is configurable from 0 to 30 seconds; it is not a timing
qualification. Rejected requests are returned as errors, not stored as a separate
security audit log. There is no background ingest daemon or automatic recovery
scheduler.

## Repository delivery

PR #177 was inspected at `dd8782a9df195a79769cbe9e8d63579578dc248e`; it remains draft
and unmerged. This increment was built locally. No push or merge was performed.
Direct container access to GitHub could not resolve the host; no full checkout was
obtained. All pre-existing focused source files are unchanged and hash-compared.

The incremental patch applies after `NET_QC_Stream_Upgrade.patch`. The ZIP also
contains the earlier dependency patch and a combined additive patch for the
inspected PR #177 baseline. **Do not overwrite the live repository with the focused
snapshot:** its earlier manufacturing files are included for reproducibility and
have overlapping names with newer repository implementations.

Remaining unverified boundaries: full repository/current manufacturing-v2 journey,
installed wheel/top-level `net` dispatcher, Windows, hosted CI, live acquisition,
physical defect performance, electrical power-cut durability, deployment filesystem,
controller/robot timing, authenticated approvals and actual part containment. No
new process limit, physical qualification or safety-controller authority is claimed.
