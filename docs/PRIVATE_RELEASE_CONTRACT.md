# Private ODSP release contract

This contract defines a candidate private deployment profile. It does not declare a release qualified, grant an operation, select an endpoint, change access or authorize deployment. Its machine-readable companion is [the private-host contract](../release/private-host-contract.json). The existing [research qualification policy](../release/qualification-policy.json) and individual capability holds remain in force for their respective claims.

The proposed initial topology is a private Linux/POSIX ODSP/ODRP host behind authenticated ingress, with a single serialized writer and storage isolated from every historical deployment. Approved software, deterministic clients and model/agent clients use the same principal-bound operations. Cloudflare Worker, Durable Object and private object storage are the existing candidate implementation, not evidence that a running deployment has been qualified. The original paused portal, its storage and its uncertain transaction are outside this candidate.

## Scope and authority

- Release the existing bounded software and reference profiles, with explicit per-principal tool, scientific-operation and resource grants. Discovery, reference text and successful tests do not create grants.
- A trusted launcher selects installed release, deployment owner, compatibility generation, identity bindings, current policy, approved corpus pins and hard limits. Tool arguments cannot select them. Missing or inconsistent configuration refuses readiness and work.
- Local package administration has filesystem/process authority. The intended software boundary is the principal-bound facade and its actual MCP server; the raw in-process API is not a sandbox against an administrator who can replace Python code or policy files.
- Host-selected NSL captures remain exact offline replay with unverified provenance. OLM adapter contract pins and canonical response digests do not attest a live upstream revision or identity. ODRP disclosure and scientific execution use separate grants.
- This initial topology is one deployment/tenant with logically isolated principals and one writer. Do not claim independent multi-tenant infrastructure isolation, horizontal write scaling, industrial measurement validity or equipment-control certification.
- PostgreSQL, Kubernetes, a prompt-management service and Windows are not requirements of this private profile. Select additional technologies or platforms only for a demonstrated deployment requirement. Existing broader research requirements are not silently removed.

## Gate 1: immutable build and release evidence

Build a clean, immutable source revision with all 21 imported trees and the exact workcell capsule intact. Retain source commit/tree, build-tool and dependency identities, wheel digest, package-file digests, image digest, policy/configuration digests and test commands. An image digest is captured after building; an image cannot attest its own final digest by embedding that digest in itself.

The adapter verifies the selected manifest against actual installed files and retained wheel bytes outside a source checkout. The Worker compares the origin's exact release identity before writing an admission marker. Release identity and checkpoint compatibility generation are separate: a newer release must neither claim an older source identity nor silently reinterpret historical state.

The release evidence collector must bind the current canonical repository, full SHA, latest eligible run/attempt, required jobs and nonempty unexpired artifacts. Keep existing required jobs and explicit exclusions intact. A source/runtime mismatch, missing receipt, skipped required gate, unknown outcome or failed newer run blocks the relevant release claim. An offline metadata snapshot is not authenticated GitHub evidence; artifact presence is not validation of artifact contents.

Acceptance: deterministic repeated builds have identical wheel bytes or report the exact reproducibility limitation; altered/missing installed files or manifests refuse; actual installed MCP journeys pass; full source inventories survive two generator runs without changes; selected hosted evidence exists for the exact release before claiming hosted qualification.

## Gate 2: current principal and resource authorization

Authenticate the request before waking the execution host, bind one principal from trusted configuration and discard untrusted identity headers. Check current enabled state and grants on every operation, discovery and resource read. Recheck authorization immediately before queued execution begins. A revoked or reduced policy cannot be restored from a checkpoint because policy is outside retained execution state.

Tool permission and underlying scientific-operation permission are separate. Permitting plan creation, admission or submission does not grant every operation in a prepared profile. Validate each plan's operation IDs against the principal's current permitted set. Once an operation has begun, later revocation cannot be described as rolling its effects back; it denies later admissions and accesses according to current policy.

Bind plans, runs, journals and retained artifacts to the selected principal's owner namespace. Status/events/discovery must not enumerate another principal's private records. Handoff requires current sender and recipient permissions and preserves original grant identity; claim/replay/reopen must not revive revoked authority. Reference scopes and resources are explicitly allowed and cannot inherit execution permission from nearby tools.

Acceptance: deterministic SDK and external MCP clients agree on allowed results and refusals; missing, wrong, revoked and reduced grants produce zero unauthorized operation calls; cross-owner reads/releases, forged principal/grants, changed corpus pins and unauthorized handoffs refuse before disclosure or execution; reconnect honors current policy.

## Gate 3: durable state, fencing and owned restore

One deployment-owned checkpoint contains principal-isolated profile/console/journal directories and a shared retention root whose stores and handoff grants retain owner isolation. The private archive never becomes a client response. Policy, credentials and authoritative writer epoch are not restored from that archive.

Each transaction binds deployment owner, principal, compatibility generation, writer epoch, transaction identity, prior committed checkpoint and release. Check authoritative epoch/transaction atomically with admission and publication. Publish the complete bounded checkpoint and receipt before acknowledging durable completion. Keep pending/uncertain records when effects cannot be established; never reset to empty state, clear a historical fence or re-execute an uncertain operation automatically.

A provider may commit and then return an error. Preserve the committed receipt/pointer and classify response uncertainty separately from definite failure. Timeout and cancellation do not prove rollback. Diagnostic or reconciliation writes must compare the exact owner/epoch/transaction so an old handler cannot change a newer owner's fence.

Restore uses a new authoritative epoch and a unique transaction workspace, never a reused pathname still reachable by an old writer. A surviving old child may finish only in its quarantined old directory; it must not alter the restored active bytes or publish a late result. Preserve uncertain workspaces for inspection and refuse capacity exhaustion instead of deleting their evidence. Raw same-path directory replacement is not a supported restore procedure.

Acceptance: real local processes and durable files prove restart, checkpoint corruption refusal, before/after-effect failures, owner continuity, current-grant checks and stale-writer rejection. Keep failed/old workers alive while other processes probe recovery. Separately qualify the actual selected hosted transaction/object-storage runtime; a Map or filesystem stand-in does not establish Cloudflare behavior. Declare production backup retention, RPO and RTO before operational release, then measure a restore drill against them.

## Gate 4: bounded service and operational evidence

Declare and enforce queue, concurrency, request/response/envelope, execution-time, archive, journal, workspace and retention limits. Bound serialized bytes, including Unicode escaping and duplicated transport envelopes. Refuse overflow explicitly; do not silently truncate retained results, aggregate permissions or increase the existing owner retention budget. Bound every network/child wait and preserve uncertainty after timeout when effects may have occurred.

Readiness verifies release/configuration and reports fencing/capacity without execution, checkpointing or clearing state. Liveness, configuration presence, readiness and capability qualification are different facts. `/api/status` must not be presented as a side-effect-free health probe if it checkpoints.

Retain bounded redacted phase/outcome/transaction evidence. Never log tokens, assertions, raw operation arguments, corpus content or private filesystem paths. Audit sink failure must neither authorize work nor erase durable evidence. A failed durable required audit write blocks new work; a best-effort diagnostic failure cannot rewrite the underlying transaction outcome. Define which class each sink belongs to.

Acceptance: queue floods, missing configuration, malformed/duplicate JSON, oversized escaped output, timeout, cancellation, full storage and audit failures all produce defined bounded outcomes. Verify no duplicate execution, leaked child/lease or unauthorized disclosure. Operator runbooks identify alert signals, inspection, owned backup/restore, safe upgrade/rollback, and cases requiring an explicit human decision. Record production latency/availability/retention targets as decisions, not inferred guarantees.

## Gate 5: independent clients and capability qualification

Exercise the actual intended installed MCP service with an official external MCP client and a separate deterministic SDK client, outside the source checkout. Run discovery, allowed operations, denied calls, paging, handoff, current-policy reconnect, process restart and recovery through the same principal boundary. An HTTPS wrapper that calls stdio is not a native Streamable HTTP MCP endpoint; qualify each claimed transport separately.

Declared local fixture targets are three clients with eight concurrent discovery/read requests each, plus a separate measured 60-second soak with at least 100 completed reads/discovery calls across three clients. Individual operations have a 60-second test deadline; the soak process has 90 seconds including setup and teardown. Cap aggregate process-tree RSS at 1 GiB and process count at 96. Exercise traffic above the configured queue bound. Retain measured timings, outcomes and resource observations, including failures. These are local engineering targets, not customer latency or availability SLOs.

Fault coverage spans owner/job/exclusive locks, handoff intent/artifact/receipt publication, admission/execution/terminal publication and every new restore/epoch boundary. Inject before effect, after effect and on enclosing-context exit. A process dying and releasing kernel resources is not proof that a surviving process cleans up correctly. Never retry an ambiguous close or close a reused descriptor.

Only selected capabilities with their required numerical/provider/reference evidence may be advertised as qualified. Synthetic software fixtures, source inspection, local process tests, installed-container tests, hosted storage/authentication and physical validation are distinct evidence levels. Missing approved native binaries, live credentials, hosted startup, independent operator acceptance or physical evidence remain explicit blockers rather than skips converted to passes.

## Release decision and external holds

One receipt set binds all gate results to the exact final source, package/image, configuration and topology. A local pass is not approval to publish, deploy, change access, remove a hold or act on equipment. The release owner reviews unresolved gates and approves only the scope established by evidence.

Hosted Actions startup failures with zero jobs remain unresolved until the platform supplies diagnostic evidence. A storage-quota email alone is not proof of that cause. Do not bypass a Forbidden diagnostic, manufacture credentials or reinterpret the failure as a source test failure. Continue independent local qualification while that external gate remains blocked.

Rights-register, Chairman and Counsel statements are attributed claims, not independently verified clearance. Approved input/corpus grants, distribution obligations and production identity mappings require their own evidence. This contract expands no rights.
