# Private host operations and release holds

This runbook applies to the proposed isolated Linux/POSIX single-writer profile in the [release contract](PRIVATE_RELEASE_CONTRACT.md). It does not authorize infrastructure changes or deployment. The historical paused host and its unresolved transaction remain outside this profile. Use the [executable local qualification runner](../scripts/check_private_host_qualification.py) and [gap inventory](../release/private-host-gaps.json) before making an acceptance claim.

## Entry points and trust boundaries

| Boundary | Implementation | Required binding |
| --- | --- | --- |
| Private HTTPS ingress | [gateway.mjs](../tools/odrp-private-host-candidate/tools/private-mcp-host/gateway.mjs) | Explicit hostname/singleton and distinct enabled principal bearer digests; current policy revision and digest |
| Serialized admission and custody | [worker.mjs](../tools/odrp-private-host-candidate/tools/private-mcp-host/worker.mjs), [storage.mjs](../tools/odrp-private-host-candidate/tools/private-mcp-host/storage.mjs) | Deployment owner, generation, exact release, epoch, transaction and prior checkpoint |
| Installed execution wrapper | [adapter.py](../tools/odrp-private-host-candidate/tools/private-mcp-host/adapter.py) | Verified installed release, current local policy and unique transaction workspace |
| Model/tool clients | [principal_mcp.py](../src/ciw/principal_mcp.py) | Trusted launcher chooses profile, principal, policy and retention root; official MCP stdio |
| Deterministic software clients | [principal_policy.py](../src/ciw/principal_policy.py), [services.py](../src/ciw/services.py) | Same current-policy facade and operation/resource checks |
| Reference disclosure | [reference_profile.py](../src/ciw/reference_profile.py) | Approved immutable catalog/evidence references, separate disclosure grants |
| External-work service | [external_work_http.py](../src/ciw/external_work_http.py) | Existing Access JWT/JWKS verification and task grants; a separate service, not implied private-wrapper authentication |

The operator HTTPS wrapper is not a native MCP Streamable HTTP server. Its internal scientific client launches the installed principal MCP service. Direct SDK access to raw local hosts is trusted administration, not a sandbox against someone who can replace package or policy files.

Gateway configuration uses `PUBLIC_HOSTNAME`, `HOST_SINGLETON`, `PRINCIPAL_AUTH_JSON`, `ODSP_RELEASE_IDENTITY`, `ODSP_OWNER_ID`, `ODSP_CHECKPOINT_GENERATION` and `ODSP_STORAGE_POLICY`. `PRINCIPAL_AUTH_JSON` has schema `ciw.bearer-principals.v1`, a policy revision/digest and closed principal entries containing enabled state and a token SHA-256. No shared default secret or old production hostname is supplied. Approved credential issuance, secret storage, rotation and revocation propagation still require operational qualification; fixture values are not credentials for deployment.

The adapter requires `ODSP_RELEASE_MANIFEST`, an absolute `ODSP_PRINCIPAL_POLICY`, the same owner/generation and `NCP_TRANSACTION_STATE` ending in `/state`. Policy and release files remain outside checkpointed state. Optional `ODRP_CATALOG`/`ODRP_CATALOG_REF` and `ODRP_EVIDENCE`/`ODRP_EVIDENCE_REF` select already-approved local immutable bundles. They never come from tool arguments. Do not expose the adapter port independently of its trusted ingress/container boundary.

## Reproducible local release

Use a clean committed checkout and an approved existing build environment. The builder runs two offline clean-source exports, checks all imported tree pins and packages a source/closure binding. Its receipt scopes reproducibility to the observed interpreter, backend and platform; it does not promise reproduction across arbitrary toolchains.

```sh
python -B scripts/build_private_release.py --source-root /absolute/checkout --output-dir /absolute/new-build
```

Install the retained wheel and exact dependencies in an isolated environment with bytecode compilation disabled. Launch with `-B`; `-I` alone ignores `PYTHONDONTWRITEBYTECODE`. Verify from outside the checkout:

```sh
/absolute/runtime/bin/python -I -B -c "from ciw.private_release import verify_release; print(verify_release('/absolute/new-build/wheel/release-manifest.json'))"
python -B scripts/check_private_host_qualification.py --candidate-root /absolute/checkout --python /absolute/runtime/bin/python --wheel /absolute/new-build/wheel/computational_instrumentation_workbench-0.1.0-py3-none-any.whl --manifest /absolute/new-build/wheel/release-manifest.json --output-dir /absolute/new-evidence
```

The runner refuses a dirty candidate, mismatched installed source/tree, missing/failed/skipped required checks, altered evidence or resource-limit breaches. It preserves each failed attempt. Use a new output directory for a later run; never relabel an earlier failure as a pass. Container builds additionally need an approved pinned base and sealed dependency wheelhouse, or a specifically inspected cached image with its different dependency inventory recorded. Retain the post-build image receipt. Do not substitute one environment's evidence for another.

The existing research [qualification policy](../release/qualification-policy.json) retains its required jobs. The additive [private policy](../release/private-qualification-policy.json) requires the exact private-host job/artifact. Hosted collection must inspect the latest eligible run and attempt at the exact candidate SHA. Offline metadata and artifact checksums do not prove authenticated hosted execution. A zero-job startup failure remains a hosted blocker, with cause unknown until diagnostic evidence is available.

## Limits and observability

The gateway bounds a request to 262,144 serialized bytes and five seconds to read it. The installed principal facade bounds requests to 65,536 bytes and the complete duplicated MCP envelope to 262,144 bytes. The adapter has one active writer, at most eight inflight requests, a two-second queue wait, a 90-second transaction bound and a five-second bounded cleanup wait. These are software limits, not availability guarantees; a timed-out provider may still be running and must remain fenced.

The adapter bounds archive bytes/members, expanded files, local workspaces, journals and result envelopes. It allows at most eight retained transaction workspaces and 128 MiB of local workspace data; full or uncertain evidence is not silently garbage-collected. Journals have closed redacted fields and explicit event/run limits. Principal-bound retention refuses additional evidence beyond 32 combined success/failure records or 4,000,000 serialized bytes until explicit authorized release. Raw trusted-local Store behavior keeps its existing eviction default. The OLM 3.8 MB conservative bundle bound remains; 4–6 MiB upstream results are not supported by this owner-store design.

`ODSP_STORAGE_POLICY` must explicitly select the bounded queue, transaction/storage/readiness/health deadlines, request/checkpoint/response caps and object/total-byte limits accepted by `storage.configuration`. Reservations remain charged after an uncertain upload. This avoids treating a late object write as free space. No automatic orphan deletion or historical checkpoint pruning is implemented.

Use authenticated `GET /api/readiness` for read-only release/fence readiness. It does not wake a stopped container, write a checkpoint or clear a pending record. `/health` validates local configuration and installed release; it is not proof of numerical or hosted qualification. `/api/status` is an ordinary checkpointed transaction. Monitor refusal codes, queue/capacity exhaustion, pending transaction identity, unknown outcomes and release mismatches. Tokens, raw scientific arguments, corpus contents and private filesystem paths must stay out of operational logs. Durable required audit failures fence work; losing a diagnostic response cannot undo the committed transaction.

## Recovery and owned backup

1. Stop new admissions through the approved operational control. Do not invent a route that clears a fence. Preserve the authoritative pending record, committed pointer, transaction receipts, object bytes and local uncertain workspaces.
2. Inspect the exact transaction and owner/epoch. `committed_response_unconfirmed` means storage may already be committed; it is not a rollback. A lost response, timeout, cancellation or process death does not authorize repeating the operation.
3. The trusted `storage.exportBackup` helper reads the owned committed checkpoint and verifies it stayed current. `storage.restoreBackup` accepts only that same current checkpoint/revision, refuses pending work and foreign identity, increments the authoritative epoch and publishes a new owned object. It is deliberately not an externally exposed admin route.
4. Keep the old process alive during a restore drill. Verify its prior epoch cannot admit, publish or rewrite new diagnostics, while the new owner reopens the identical retained records. Adapter working roots are unique; never restore over a pathname reachable by the old process.
5. A stale historical backup, missing authoritative store or lost custody history needs a separate reviewed recovery design. The current helper refuses those cases because restoring old attempt identities could cause duplicate execution. Do not describe current-checkpoint rehoming as complete disaster recovery.

Declare production backup retention, RPO and RTO and perform an approved drill against the actual storage service before acceptance. Local SQLite/fsync substitutes establish only the local algorithm and failure behavior. Cloudflare Durable Object, R2 and container lifecycle behavior still require hosted tests.

## Upgrade, rollback and capability holds

Quiesce admissions, verify no unresolved work, export the owned checkpoint and retain exact release/image/policy receipts before any separately approved change. A changed release SHA is not a changed compatibility generation. Current code rejects incompatible state; it provides no automatic cross-generation importer. Rollback may use only a previously qualified release that can interpret the current generation and preserved attempt history. Reverting to an old checkpoint to make errors disappear is prohibited.

No capability becomes qualified solely because discovery, admission or a synthetic fixture succeeds. The seven synthetic numerical-operation journeys demonstrate software plumbing. NSL capture replay, OLM local-handler compatibility, ODRP disclosure, native provider qualification, live identity and physical safety-control evidence are separate claims. Preserve their existing holds. Independent operator acceptance, production objectives, actual hosted evidence, approved identity/corpus bindings and any physical validation remain explicit release decisions.
