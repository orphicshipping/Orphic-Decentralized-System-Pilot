# GaugeLink measurement views, device declarations and offline exchange

## Executable scope

The investigation is **one retained GaugeLink scalar capture to inspectable
measurement objects and an offline-restorable evidence export**. This increment
extends PR205's existing serial/scalar operations and PR204's Link Journal. It
adds no acquisition driver, calibration kernel, model, scientific ledger,
manufacturing reducer, enrollment service, network listener or machine authority.

`notations.measurement.v1` is a **versioned interpretation view**, not a replacement
for `ciw.observation.v1`, `ciw.observation-stream.v1` or `covariance-artifact.v1`.
It is currently produced only from the completed four-operation scalar study.
Seven Link profile names do not imply that their physical devices or non-scalar
scientific models are supported by this adapter.

## Device declarations

```sh
net device register manifest.json --journal devices.sqlite --create-journal --approve-declaration
net device list --journal devices.sqlite
net device inspect DEVICE_ID --manifest-ref sha256:HEX --journal devices.sqlite
```

A manifest is the existing `ciw.link-device.v1` contract. Registration appends the
exact original manifest bytes through the existing Link framing and Journal.
Equivalent repeated declarations retain the first bytes and receipt. Multiple
configurations for one device require an explicit manifest selection; no newest
or active configuration is inferred. Listing/inspection open the same Journal
through a bounded frozen copy of a closed, checkpointed journal, using SQLite
read-only and query-only modes. Inspection leaves the retained directory unchanged.
An active or residual `-wal`, `-shm` or `-journal` sidecar refuses; finish and close
the writer before inspection so committed WAL content cannot be silently omitted.
The private snapshot is removed on close. No schema migrations or new tables occur. Read-only mode does not claim an immutable physical storage medium or
race-free exclusion of another process.

Registration means **operator-selected declaration retained**, not connected,
electrically compatible, possessed key verified, authorized for control,
calibrated or qualified. It does not implement the cryptographic enrollment and
consent work owned by PR206. A transport address remains distinct from identity.
`connect`, polling, zeroing, configuration, update and actuation are not added.

## Measurement identity and native semantics

Each scalar line has two views, `indicated` and `corrected`. The measurement ID
binds the original line ID, existing result occurrence ID and stage. Thus:

- Exact offline recovery preserves the capture, line, measurement, operation,
  execution and result identities and the three original investigation files.
- Explicit numerical replay preserves original physical capture/line identity
  while creating fresh computation/result and interpretation-view identities.
- Repeated computation is never counted as a new physical sample.

Every view carries quantity/unit, original line bytes and chunk byte ranges,
manifest/device/instrument/channel/transducer identities, hardware/firmware and
optional gateware/configuration, procedure/context, clock/boot/ticks and every
contributing host receipt. Acquisition time is not invented from receipt time.
Unknown relationships remain unknown. Correlation is never inferred away.

Indicated uncertainty references the reading coordinates of the original full
joint `[readings..., gain, offset]` covariance. Corrected uncertainty references
the existing full output covariance. Matrices and ancestry travel with the
bundle; displayed diagonal entries are not an independence assumption.
Calibration applicability and arithmetic checks are inherited from the retained
operations. Neither calibration traceability nor physical inspection acceptance
is established. Both remain visibly held/unestablished.

Unsupported or unrepresentable native timing remains withheld exactly as in the
existing scalar exporter. The view retains the original row even when no native
observation can be produced. No filtering, interpolation or timestamp repair.

## CLI journey

```sh
net link study example --output scalar-request.json
net link study run scalar-request.json --output-dir scalar-study
net observation ingest scalar-study --output measurements.json
net observation list measurements.json --stage corrected
net observation inspect measurements.json MEASUREMENT_ID
net observation export measurements.json --output object-export
net observation review-export object-export
net observation restore measurements.json --output restored-study
net observation restore-export object-export --output restored-from-objects
net observation report measurements.json --output dashplate.html
net observation mcp measurements.json --journal devices.sqlite
# Explicit new numerical execution, not offline reopening:
net observation replay measurements.json --output replayed-study
```

Use fresh destinations. `ingest` reads an already retained, completed scalar
study; it is not another serial-acquisition command. `restore` preserves exact
request/workspace/graph bytes. Existing native validators may calculate
consistency checks during review, but do not run a new registered analysis,
create execution occurrences, contact an instrument or load saved runtime paths.
The report reuses the existing scalar Dashplate, not a second live front end.

Bundle validation re-derives the view from its exact three retained source files.
A coherently rehashed alteration to metadata, uncertainty, raw references,
verification status, ordering or row count refuses. Hash consistency is not
source authentication; an entirely reauthored internally consistent synthetic
investigation is still not empirical evidence.

## Existing Workbench handoff

```sh
net observation workbench measurements.json --output attached-workspace.json \
  --dataset-id operator.measurements.001 --recorded-at 2026-10-06T00:00:00Z
```

This optional command requires the **existing typed-data integration** from
PR185/200. Without it, the command refuses explicitly and creates no output.
`data_sources.py`, `workbench.py`, the shared Session/runner and covariance
implementations are not modified or copied into this increment.

Attachment checks the original Session evidence and **every exact retained
execution/result occurrence**, not just equal source inputs. It stages changes
using the existing Workbench under the existing Session lock. Any conflict leaves
the original Workbench unchanged. Identical dataset/time retries are idempotent;
changed content under the same dataset revision refuses.

It retains four linked native sources when covariance is supplied:

1. Existing artifact family containing the complete recoverable measurement bundle.
2. Original input covariance, depending on that artifact.
3. Original output covariance, depending on both prior sources.
4. Original native observation stream, depending on the bundle/output covariance.

With unknown covariance, only artifact and observation stream are retained.
Workbench source, dataset-envelope, native observation, measurement and execution
identities stay separate. No `measurement` family is registered in the owner's
closed schema table. Generic artifact retention alone does not grant scientific
validity or activate a new model. Correction propagation continues to be owned by
the existing Workbench; this increment does not duplicate its correction journal.

## Read-only MCP

The host uses the existing `ciw.agent_mcp.Server`/stdio service. Five tools inspect
an explicitly frozen, validated bundle and optionally a frozen device roster:
`devices_list`, `device_inspect`, `measurements_query`, `measurement_inspect`,
`evidence_trace`. There are no execution grants or hardware operations.

Queries are bounded and report retained line/stage order, not a presumed latest
physical acquisition. Inspection returns the appropriate full covariance.
Evidence tracing returns original line/chunk references and distinct source,
operation/execution/result identities. Unknown tools and unexpected fields refuse.
This does not implement remote identity, transport encryption or a fleet service.

## Initial data plane

`export` writes a create-only local content-addressed object directory, with a
complete manifest written last. Objects include exact original investigation
files, lossless bundle, native observations/mapping, full covariance and a CSV
analytical index with exact canonical record JSON. Review verifies every hash,
length and derivative against the bundle and refuses missing/unaccounted objects.
All outputs remain local. No S3 credentials, bucket, server or retention policy
is provisioned.

A fixed PostgreSQL staging DDL is included as an object. Its companion CSV is
suitable for operator-controlled `COPY ... FROM STDIN ... CSV HEADER`; values are
not interpolated into SQL. Record JSON stays text to preserve its exact numeric
representation. This is **generated staging input, not a migrated/loaded or tested
PostgreSQL deployment**. The source archive is still required for full covariance
and evidence recovery. Do not open untrusted CSV fields as spreadsheet formulas.

`export --parquet` is an optional candidate requiring the unchanged
`pyarrow==25.0.1` pin. Its columns are text metadata plus exact record JSON, not
lossy Float64 clock/covariance flattening. The complete native companion objects
remain mandatory. The implementation checks actual Arrow schema, metadata and
record round trip; absent/mismatched PyArrow refuses before output creation.
The dedicated opt-in native test must pass before claiming Parquet qualification.
No successful Parquet execution is inferred from testing this refusal path.
`restore-export` recovers primary evidence without importing PyArrow: the optional
derivative is byte-checked, but its semantic review is explicitly NOT_PERFORMED.
Primary native/covariance/source/CSV validation is never skipped.

## Qualification and physical definition of done

Core tests: `tests/test_link_measurements.py` plus the unchanged inherited Link,
scalar and connectivity suites. `scripts/check_link_installation.py
--with-measurements` qualifies a noneditable wheel outside source with byte
provenance. Exact pins are the default; `--allow-host-dependencies` explicitly
records a non-pinned local check, not a supported release.

Optional native gates:

```sh
NET_MEASUREMENT_WORKBENCH=1 python -m pytest tests/test_link_measurement_workbench.py
NET_MEASUREMENT_PARQUET=1 python -m pytest tests/test_link_measurement_parquet.py
```

The Workbench gate requires the actual owner modules, not fixtures or stubs. A
two-module integration assembly does not qualify the entire owner branch. The
first implementation was tested against exact owner blobs:
`workbench.py=e3326204e5d1b1a6bc4a894dcb2a8bbc9b30660b` and
`data_sources.py=62d12da77adbbbbc4c04a8c4d901c62651134c35` from PR200 commit
`cf5342a4e0996bcbe04671572f0c7e6da21c4b7e`, outside the delivered source tree.

The user's complete physical loop remains open until an actual serialized unit,
gauge/cable/electrical interface, firmware/board identity, independent reference,
clock behavior and uncertainty evidence are supplied and tested. Do not label
planned GL1-EVT serials as manufactured units. Physical BOM/lot/assembly/repair
and release belong to the existing manufacturing runtime, not this view schema.
No physical test, new firmware, FPGA, calibration certificate authentication,
production release, network deployment, migration, merge or licensing change is
performed by this increment.
