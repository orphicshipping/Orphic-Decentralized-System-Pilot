# Domain H: data systems work order

Domain H is part of the active [programme completion work order](PROGRAMME_COMPLETION.md).
This document separates implemented local data-instrument behavior from the six
requested server profiles and their external qualification. The common foundation
does not by itself complete those profiles.

## Reconciled sources and boundaries

| Component | Verified repository contract | What it establishes | What remains unestablished |
| --- | --- | --- | --- |
| PPDA and SCOUT | [`acquired_dataset.py`](../src/ciw/acquired_dataset.py), [`ppda_acquisition.py`](../src/ciw/adapters/ppda_acquisition.py); PPDA `477d6cb454423a27543b16961d3b169709c40c31`, exact `vendor/scout-retrieval-agent` gitlink `5e146d5924675cd7b6e1d1ed44fb39f5da012610` | Bounded native source/adapter registries, acquisition plans, retained offline snapshots, durable pool restoration and checkpoints. Both complete source trees are checked by the adapter. | A compliant live HTTP collector, a Bright Data binding, R2 storage, and NSL admission. SCOUT's native evidence admission is explicitly distinct from scientific/state admission. The separate `compute-runtime/scout` package is not a substitute for this gitlink. |
| SRA | [`schematic_profile.py`](../src/ciw/schematic_profile.py); `a6e79585950bb6860e5edce5ebd2cce39ea481f2` | Typed schematic eligibility and bounded retrieval, with separately pinned companion operations. | A general knowledge-graph database, an active graph service, or a connected Domain H graph store. Calling the existing package a connected general knowledge graph would overstate it. |
| TBRT | [`clocksync_profile.py`](../src/ciw/clocksync_profile.py); `40507060ca7a9126a9d641999b994a757eef3bfd` | Actual bounded affine-clock reconciliation and fresh replay of the complete receipt through the selected pinned native implementation. First-order covariance and exact anchored time-pair semantics are retained. | Source authentication, verified synchronization evidence, UTC conversion or physical traceability. The provider explicitly marks these unestablished. A replayed calculation alone cannot authenticate the timestamp it computes. |
| NSL | [`state_read_profile.py`](../src/ciw/state_read_profile.py); contract `4fef5f488694d0dd526b2a2166f5e5c90f1dd7ec` | Exact read-only replay of host-bound offline captured state RPC responses. | Actual NSL admission or upstream receipt authentication. Its metadata says `upstream_receipt_verification=not_performed`, `capture_authority=host_declared_unverified` and `fresh_upstream_read=false`. Neither the upstream implementation nor a trusted admission verifier is supplied by that adapter. |

These source pins are not interchangeable with other import/history pins.
[Native acquisition](ACQUIRED_DATASET.md), [integrated modules](INTEGRATED_MODULES.md),
[read-only adapters](READ_ONLY_ADAPTERS.md) and the source modules remain the
controlling descriptions of existing semantics.

The [instrument manifest](../instruments/manifest.json) records SRA's MIT licence
and ClockSync's MPL-2.0 licence. Their existing licence texts/notices remain in
their source trees. The [rights register](licensing/rights-register.json) records
unverified title/conversion claims and no global legal clearance; a new data
receipt does not release those holds. PPDA and the exact SCOUT gitlink need their
own pinned licence/notice review before any new distribution. This work adds
original integration code under the repository's existing AGPL-3.0-or-later
boundary; it does not relicense the engines, datasets or imported material.

## Common foundation implemented locally

[`data_instruments.py`](../src/ciw/data_instruments.py) supplies a launcher-only
`DataInstrumentLedger`. It uses the existing `campaign_contracts` canonical JSON
format, `PrincipalPolicy` identity/context checks, and `PrivateSQLite` local
transaction boundary. No new shared MCP tool, service route, principal grant,
collector endpoint or NSL writer is installed by this module.

The host owns the private SQLite path, fixed tenant/principal, current policy,
admission callback, explicit reference clock and impact-review verifier. Admission
must return exact `True` for each operation and source; it is rechecked around
database I/O. Existing principal-policy changes invalidate an in-flight operation.
No callback, policy, filesystem path, credential or reviewer authority is a model
argument. Any later MCP wrapper must enter the current principal and audit
facades and cannot expose the host registration/review constructors.

Implemented behavior:

- Sources have immutable, sequential versions and exact licence, terms, robots,
  scope-review and calibration evidence references. Evidence identifies a host
  review; it does not independently prove rights, robot permission, classification
  or physical calibration. Unknown or held usage prevents intake/reservations.
- Source kinds are exactly `places`, `assets`, `infrastructure`, and `imagery`.
  There is no person kind, person resolver, stealth mode, arbitrary header input
  or browser identity option. Honest identification is a fixed ODSP product,
  explicit version and HTTPS contact. The host must review the actual source and
  permitted fields for person data; this validator is not a semantic PII detector.
- An atomic persistent rate reservation enforces the registered interval across
  processes/restarts and refuses regressed or mismatched clocks. It records no
  network request and grants no permission to make one. Recovery inspection may
  continue during a drift hold; downstream trust cannot.
- Each bounded retained snapshot keeps its original bytes and SHA-256. A dataset
  version binds source version, raw content reference and table contract. A capture
  occurrence separately binds an idempotency attempt and declared observation.
  Equivalent JSON with different bytes has a different raw identity; an explicit
  new occurrence cannot overwrite an old one.
- The table vocabulary has one unique string/integer key, closed named columns,
  exact primitive types and explicit nullability. It does not coerce booleans to
  numbers, strings to numbers, or missing values to defaults. Duplicate JSON keys,
  duplicate record keys and undeclared columns fail. Size/complexity limits apply.
- Calibration compares registered known-good control rows in every capture.
  These are explicit controls, not a claim that arbitrary live records should
  equal historical data. Numeric absolute tolerances use exact rational
  comparisons of the supplied JSON numbers; nonnumeric controls compare exact
  canonical values. Missing controls and out-of-tolerance values cause a hold.
- Capture and assessment check nominal freshness against an explicit matching
  reference frame. An unmatched device frame requires a mapping. The host clock
  is labeled as host time, never TBRT. Age limits are nominal policy checks;
  declared time uncertainty is preserved, not converted into invented confidence.
- Schema, control-sample and freshness drift create durable sticky holds. A good
  later capture or new source version never clears one. Changing the source
  contract creates a hold. Host impact review must identify the exact active hold,
  fresh recovery capture, approval evidence, reviewer and downstream dispositions.
  A separate current host review verifier must accept that exact intent, including
  after a database wait. The review clears local drift only.
- Clock linkage invokes the actual `ncp_clocksync_replay` through a bound existing
  `PrincipalFacade` or `AuditedPrincipalHost`, with actual `ClockSyncHost` runtime.
  The receipt's observation must bind the exact raw dataset hash, observed device
  value/frame and independently registered reference frame. Full replay equality
  is required. Stored links distinguish that verified calculation from a fresh
  subsequent verification and from an authenticated source timestamp.
- Receipts are create-only application records in a hash chain; old data, source
  versions and hold/review events remain inspectable. SQLite FULL synchronous WAL
  gives the existing local durability contract. This is not a distributed/R2
  store, physical power-loss qualification, or protection against a same-UID
  administrator rewriting an entire unanchored database.

`capture`, `receipt`, `snapshot`, `inspect_capture`, `assess`, `reserve_collection`, `register_source`,
`link_clocksync`, `review_hold` and `verify_chain` are host/library methods. There
is no generic fetch/execute/approve method. Store limits are fixed on reopening
and failures do not evict prior evidence. The initial qualification uses synthetic
review documents and data, with a real pinned TBRT calculation/replay where noted.
If authority is revoked after a local mutation commits, disclosure is withheld
with `InstrumentOutcomeUnknown` and `local_commit_completed_disclosure_withheld`.
The event remains durable; callers must inspect it after authorization is restored
and cannot infer rollback or retry a review/reservation automatically.

`inspect_capture(source_id, capture_ref)` returns the exact receipt, original
bytes and a live local assessment in one current-authority read transaction. It
appends no event, so pipeline guards can repeat it after blocking operations.
Superseded sources, usage holds and revoked principals refuse. An out-of-tolerance
inspection must stop derived work; `assess` persists the resulting sticky hold.
Inspection never supplies a downstream-trust grant.

The common table interface for downstream adapters is:

```python
table = {
    "key": "record_id",
    "columns": {"record_id": "string", "height": "number", "active": "boolean"},
    "nullable": ["height"],
}
# validate_table(raw_bytes, table) checks a closed array of exact typed rows.
# content_ref(raw_bytes) identifies original bytes.
# version_ref(value) uses the existing ciw.python-json.v1 canonical format.
```

Images and geodata can be referenced by immutable content identities from such
tables; this core does not implement image decoding, geospatial transformations,
clustering or metrology engines. Database vocabulary adapters can map these four
primitive types exactly while keeping physical SQL names in host configuration.

## Trust gate that remains open

Every common-core assessment returns `trusted_for_downstream=false`,
`source_authenticated=false`, and `nsl_admission=not_verified`. These are actual
capability limits, not a completed NSL profile. Even a valid TBRT numerical replay
does not satisfy source authentication. A local receipt, host wall clock, signed
recipe, offline NSL capture, supplied `admitted` field or impact-review approval
cannot replace either missing verification.

To close this gate, a separately reviewed integration must bind authenticated
source/time evidence and an actual NSL admission receipt to the exact dataset
version, source version, timestamp receipt, tenant and principal. It must verify
against the current trusted upstream authority and check retractions/supersession
before disclosure or derived use. This module intentionally has no callback that
accepts a caller's `verified=true` or synthesizes an NSL admission. All six server
profiles must carry held/pending status until that integration is qualified.

## Six-profile implementation and acceptance map

The names below are requested profile families. Proposed entry points are an
integration contract to implement and qualify, not a claim that they are now
registered server tools. Root integration owns shared dispatch, policy and MCP
catalog changes.

| Family | Required behavior and reuse | Executable work now | Next implementation/acceptance |
| --- | --- | --- | --- |
| `ncp_collect_*` | Registered source/usage evidence, honest identified rate-limited fetching, immutable snapshots in R2; reuse PPDA/SCOUT acquisition semantics | Common registry/captures/holds; [`data_collection.py`](../src/ciw/data_collection.py) adds exact usage-document/expiry guards, a fixed HTTPS adapter, durable request intents and conditional R2 quarantine/readback. Qualification uses local fixtures and fake storage only. | [Collection contract](DATA_COLLECTION.md): actual approved source/TLS/credential/R2 qualification, native PPDA handoff, source/licence review and principal/MCP exposure remain open. No local or uploaded receipt establishes NSL admission. |
| `ncp_pipeline_*` | Declared SQL/dbt transforms, exact input/code/output versions, idempotent runs, lineage, closed schema contracts | Common dataset/version and drift records only | Fixed versioned transform registry, exact runtime identity, transactional run claims/unknown-outcome handling, immutable lineage and output receipts. Independent rerun comparison; schema-breaking and input-retraction refusal. SQL/dbt execution is not implemented by the common core. |
| `ncp_analysis_*` | Bounded queries/statistics/charts with dataset lineage and uncertainty for every derived number | Existing ODSP scientific engines and common dataset inspection inputs | Bind existing statistics/metrology operations through canonical services; declare units, missingness and covariance/model assumptions. Derived outputs with unsupported uncertainty must remain unqualified. Charts must carry receipt/dataset refs and uncertainty, not recompute numbers in a new engine. |
| `ncp_db_*` | Shared-vocabulary schemas/indexes/additive migrations, exact plan and dry run; model-facing writes only produce plans | Separate database worker owns `data_database.py`, tests and `DATA_DATABASE.md`; acceptance belongs to that deliverable | Host-reviewed closed logical/physical vocabulary, schema identity, plan hash and isolated rollback-only local PostgreSQL dry runs. Applying a migration is a separately authorized human action; no common-core apply operation exists. |
| `ncp_dbops_*` | Read-only health, slow-query/index advice, backup/restore checks, declared/live schema drift; destructive operations need approval | Separate database worker; source/version receipts can bind inspection evidence | Fixed bounded read-only PostgreSQL SQL, current role/database binding, explicit observation timing, reproducible advice. Backup existence is not a restore drill. Destructive maintenance and user-data restore cannot be inferred from a health check. |
| `ncp_mining_*` | Anomalies, clustering, change detection over geodata/imagery and state-ledger patterns; evidence and defensible confidence; no automatic action | Existing bounded scientific/domain readers and common exact dataset identities | Fixed approved algorithms routed to existing engines, frozen input features/model versions, appropriate oracle/fixture evaluation, explicit false-positive/uncertainty characterization and evidence links. Offline state patterns must not imply authenticated NSL state. No fabricated confidence constant or empty algorithm stub closes this profile. |

Dependency order is common source/receipt contracts; authenticated time and NSL
binding; compliant collection and immutable storage; pipeline claims/lineage;
analysis and mining through existing scientific engines. Database inspection and
plan-only isolated dry runs can be implemented independently in parallel, retaining
their own current principal and review boundaries. Local exploratory results must
remain visibly held and must not acquire an admitted-state label.

## Connectors and hosted qualification

The integration owner executed supported read-only authentication checks:
Cloudflare `whoami` and Vercel `get_auth_user` succeeded; the latter reported a
Hobby account. These checks prove callable authenticated accounts only. They do
not prove an R2 bucket, source access, PlanetScale database, image registry access,
deployment entitlement or a right to create resources.

Tool discovery found zero callable PlanetScale and zero Bright Data tools in the
current session. Their availability is therefore unverified/unavailable here,
regardless of the proposed architecture. PlanetScale-compatible PostgreSQL remains
the programme preference; NATS and compute remain separate. No resource, DNS
binding, secret, external collection, teammate message, migration, live device
action or paid allocation is created by this work order.

Before external qualification, identify approved source URLs/licence/terms/robots
evidence, source scope and honest contact; exact R2 account/bucket and immutable
object policy; actual database/broker/identity endpoints; trusted NSL verifier and
authenticated clock evidence; secret storage and rotation; bounded costs and
required migration/destructive-action approvals. Existing release and licensing
holds continue to apply.

## Reproduction and evidence

```sh
PYTHONPATH=src CIW_CLOCKSYNC_CHECKOUT=/trusted/exact-40507060-checkout \
  /trusted/python -B -m pytest tests/test_data_instruments.py -q
```

The selected interpreter must satisfy the existing ClockSync host and its pinned
runtime and process-isolation checks. Positive native linkage evidence was sealed
before the campaign made qualified Bubblewrap mandatory. This environment refuses
the required namespaces, so that earlier run does not qualify the final isolation
boundary; a qualified isolated host must rerun native checks. No unsandboxed
fallback is permitted. Without the exact checkout, native integration tests explicitly
skip and do not qualify TBRT linkage. Tests cover exact raw identities, sequential
source versions, strict schemas/control samples, intake usage holds, sticky drift,
review freshness/compare-and-set, independent-process idempotency, rate reservations,
current principal revocation, capacity refusal, corruption, and actual pinned
clock reconciliation/replay plus dataset/tampering/grant negatives. These are
local software checks, not hosted collection, rights clearance, authenticated
physical time, NSL admission, scientific baseline or release qualification.
