# Source collection and R2 quarantine adapter

`data_collection.py` implements a source-bound collection path over the local
[data-instrument ledger](DOMAIN_H_DATA_SYSTEMS.md). It provides executable HTTP
and conditional object-storage adapters, with local fixture qualification.
No external source was fetched, no R2 object was written, and no hosted service
or MCP collection tool is claimed as deployed or qualified.

## Bindings and execution

The trusted launcher registers an exact source version through
`DataInstrumentLedger.register_source`. It then constructs `SourceCollector`
with that source identity, the actual retained licence/terms/robots document
bytes, a current review and its independent host verifier, a fixed fetcher, an
explicit observation clock, and optionally `R2QuarantineStore`.

The review binds `source_ref`, `review_ref`, `checked_at` and `expires_at` in the
source's declared reference frame. Its verifier receives the complete review,
the exact document references and current principal context on each guard. A
truthy number, a supplied `permitted` string, or a matching document hash alone
does not pass that verifier. The host must verify the review against its real
approval record; local tests use explicitly synthetic reviews. The verifier
does not establish source time, NSL admission or independently adjudicate rights.

The only collection argument is an opaque attempt identifier. Source URLs,
review authority, credentials, storage keys, principal/tenant, HTTP headers and
timeouts cannot be selected by a collection request. Current policy/source use,
document bytes, review expiry and robots rules are rechecked around blocking
operations. The existing source kind restriction remains places, assets,
infrastructure and imagery; the launcher must review actual fields for excluded
person data. There is no person resolver, stealth fetch or browser impersonation.

Before the request, a persistent rate reservation and create-only request intent
are committed locally. The intent retains the complete review and hashes of
the exact usage documents, whose bytes are retained in the same store. An
attempt cannot dispatch twice after process recreation. A failed or uncertain
request/upload leaves the intent inspectable with `retry_authorized=false`.
The code never retries it or silently starts another attempt. A host recovery
decision must inspect the retained evidence before intentionally authorizing
new work.

Successful responses become ordinary exact-byte capture occurrences. Existing
schema, known-control and nominal-freshness checks run before optional object
storage. Malformed data, failed controls, stale observations and existing sticky
holds retain local evidence and block uploads. Good data can be retained only as
**unadmitted quarantine**: both local and object receipts retain
`trusted_for_downstream=false` and `nsl_admission=not_verified`. Neither upload
success nor a host clock is authenticated TBRT/NSL evidence. The current NSL and
authenticated-time gaps remain a mandatory downstream-trust hold.

## HTTP and robots limits

`SourceHTTPS` binds one launcher-reviewed public literal IP to the registered
HTTPS hostname/path. It connects to that IP without resolving the hostname
again, verifies the actual peer address and verifies TLS against the original
hostname through the system trust store. It does not use ambient proxies,
redirects, cookies, browser identities or automatic retries. The User-Agent is
the registered `ODSP/<version> (contact=<HTTPS contact>)`.

Only ASCII URLs without credentials, query strings, fragments, percent escapes
or backslash ambiguity are accepted by this adapter. An endpoint/address change
needs a new reviewed binding. The live HTTPS/certificate path has not been
exercised here; socket-pair qualification covers the real HTTP parser and
deadline enforcement without external network access.

One deadline bounds connect/TLS/request and every response socket read, including
trickled status/header lines. The configured maximum is ten seconds. Headers
are bounded to 8 KiB before HTTP parsing and 32 parsed fields; duplicate fields,
redirects, non-200 responses, non-JSON content, compression, chunking, missing
Content-Length and truncated bodies refuse. Raw body maximum is 256 KiB. These
limits intentionally hold sources that need an additional reviewed transport.

The robots evaluator is a conservative retained-document subset: exact `ODSP`
groups take precedence over `*`, longest literal-prefix rules apply, and Allow
wins equal-length ties. The registered interval must cover Crawl-delay. A
matching group is mandatory. Wildcards/end anchors, percent/backslash paths,
unsupported directives, invalid encoding and ambiguous syntax require review;
they are never silently treated as permission. The adapter does not fetch or
refresh robots itself. A real source review must set an appropriate expiry and
preserve current robots/terms evidence.

## R2 object semantics

The launcher supplies a preconfigured S3 client for the exact account endpoint
`https://<account-id>.r2.cloudflarestorage.com`, a fixed bucket and current
authorization callback. The adapter checks SigV4, bounded connection/read
timeouts and one configured SDK attempt. It does not discover credentials,
create a client/account/bucket, change permissions or lifecycle policy, or enable
public access. The supplied client's credential handling, endpoint/redirect
behavior and actual revocation need hosted qualification before activation.

Object keys derive from the fixed tenant/principal/account/bucket scope, source
identifier and raw SHA-256; callers cannot provide keys. Writes use
`IfNoneMatch='*'`, transit Content-MD5 and explicit quarantine metadata.
Cloudflare documents conditional PutObject as atomic with a 412 response when
the object already exists. The adapter accepts that case only after exact
readback. [Cloudflare R2 S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/)

Every reported success reads the full bounded object back and checks its length,
bytes and SHA-256, with fresh authorization before and after. ETags are not used
as content identities. R2 does not advertise full-object SHA-256 support in its
checksum table, so the adapter makes no such header claim. Its SHA-256 assurance
comes from comparing actual returned bytes.
[Cloudflare R2 checksum support](https://developers.cloudflare.com/r2/api/s3/api/)

The S3 request uses the documented conditional-write and body-read interfaces.
[Boto3 PutObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html),
[Boto3 GetObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_object.html)
Any uncertain put, readback mismatch or post-write revocation returns
`CollectionOutcomeUnknown`, without retry or a rollback claim. This is
application create-only retention, not immutable storage enforced against bucket
administrators, Object Lock qualification or signed NSL admission.

## Qualification and remaining integration

Run the local collection tests with the campaign's selected Python environment:

```sh
PYTHONPATH=src /trusted/python -B -m pytest tests/test_data_collection.py -q
```

Tests execute real local SQLite capture/reopen/hold paths, source review and
revocation guards, a real stdlib HTTP parser over socket pairs (including
trickled headers and stalled bodies), and a clearly labeled fake R2 client.
Fake storage exercises create-only conflicts, corrupt readback, possible commits,
authorization changes, scope separation and body closure. It does not establish
R2 connectivity, rights clearance, TLS endpoint trust, object persistence,
credential revocation or hosted performance.

Next integration requires an actual approved source catalogue and contact,
independently verified usage reviews, a qualified HTTPS/SDK host, a separately
authorized existing R2 bucket/credential binding and bounded cost. No such live
inputs are present here. The exact PPDA/SCOUT acquisition handoff also needs its
pinned runtime and licence/notice review; this adapter does not relabel generic
collection as a tested native PPDA operation. Root owns any later MCP exposure,
principal/toolbox registration and cross-profile validation. External collection
and storage writes remain separate approval/qualification steps.
