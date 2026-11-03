# Audit reconciliation and release holds

This record reconciles the two user-supplied audit attachments against main
`235ee45a11f744e8c801364965c07e9e3e13854f` and the separate, uncommitted
`completion/full-programme-20261103` worktree. It is not a release certificate.
The original report describes a partial SHA-verified reconstruction, not a full
clone. Its proposed CI account/billing/policy cause was an inference.

The originals are retained as Library files
`libfile_77fe185fce308191b046c251ee260984` and
`libfile_cd2a670a48548191a6ccabe5835f9710`, with local copies under
`/tmp/odsp-completion-evidence/user-audit/`. Tests below are local unless
explicitly identified otherwise. A new implementation does not inherit the
qualification of an earlier source version or container.

## Finding-by-finding disposition

| Prior finding | Current evidence and disposition |
| --- | --- |
| 1. Stdio / no OAuth | Main's control entry point is stdio and its private candidate gateway uses bearer credentials. The working tree adds `principal_http.py`, an official-SDK Streamable HTTP resource server with host-bound issuer, audience, subject, scopes and current principal policy. Local protocol tests exist; no hosted issuer or ingress qualification is established. |
| 2. File state / leases | Main's local queue remains available. The campaign adds actual PostgreSQL claims, leases, fencing and transactional NATS outbox delivery, plus a PostgreSQL shared cache. Local PostgreSQL/NATS and transaction-pool tests ran. These are opt-in bindings; no hosted migration or failover/restore drill occurred. |
| 3. Observability | Existing audit chains retain redacted hash-linked records. They are not immutable external storage. Metrics/telemetry integration is still an explicit acceptance item; no OTel claim is made. |
| 4. Per-principal rate limits | Existing queue capacity is backpressure, not per-principal request rate limiting. This remains a separate implementation/qualification obligation. |
| 5. Container binds `0.0.0.0` | A listener address alone does not establish exposure. The intended private ingress requires Access assertion validation, principal binding and no direct public origin. Current candidate hosted routing and container qualification remain unestablished; no origin is activated by this campaign. |
| 6. Destructive annotations | Existing control annotations distinguish destructive operations. The campaign marks `ncp_work_cancel` destructive because it invalidates claims/access. Additive proposal/submission operations are not indiscriminately relabeled destructive. |
| 7. Fragmented `services.admit` | Confirmed: nine raw profile routes precede the control-only validator. Each has individual bounds. Raw in-process hosts are documented trusted administration; the HTTP verifier refuses raw hosts. This is not a demonstrated remote authorization bypass. The campaign extends shared request validation to registered profile schemas before raw routing, retaining principal and profile-specific checks. |
| 8. Six companion copies | The six intended removal directories were not supplied or established by repository history. All 21 imported trees are preserved. A complete path/tree/blob/notice inventory exists; no source or history removal is inferred. The user reported November 16 as a counsel deadline and later asked to disregard dates and proceed immediately; no scheduling delay is imposed. Exact removal scope remains required. |
| 9. Companion process boundary | Confirmed default environment and loopback access with a synthetic canary/listener. The exact approved patch clears inherited environment and requires pinned Bubblewrap policies for pinned companions, including Nvalchemi and profile overrides. Limits-only mode remains insufficient for companion execution. This environment refuses the namespace probe, so positive isolated execution remains unqualified. |
| 10. PLSR dependency | `plsr_engine.py` retains the exact optional runtime pin `19ea6967060166ba09db6cd4563bd87bd6b3d196`. An old owner slug is not evidence of a changed commit or a replacement licence. Source identity, installation and upstream compatibility must be reconciled before any pin change. |
| 11. Mermaid injection | The SRA profile blocks selected imports; this is an application guard, not an OS isolation boundary. Imported SRA remains unchanged. The companion isolation gate is separate. |
| 12. NISE mandate injection | Question text is retained as inert query context; it does not grant authority. The existing contract is preserved and must remain in combined tests. |
| 13. NISE source forgery | Structural validation and content hashes do not authenticate sources. `source_authenticated=false` remains an explicit limit. No new source-authentication claim is introduced. |
| 14. NISE output bounds | Existing process and response limits remain. Previous positive native tests do not establish current mandatory-isolation qualification. |
| 15. TBRT rounding / reference | The ClockSync profile's numeric replay and reference checks exist, with actual pinned local replay evidence. They do not authenticate UTC or source acquisition time. Domain H preserves this distinction. |
| 16. SCOUT / EDGAR / FrameMapper notices | The report's zero-GPL-notices statement is incorrect: `THIRD_PARTY.md` names GPLv3 and FrameMapper's root LICENSE contains GPLv3. Complete upstream notice and distribution clearance remains open. FrameMapper has 998 blobs; the old 1,285 count is stale. Retained person/stealth code is not proof of an active ODSP route. SCOUT's structural `person` acceptance is not an OSINT gate; Domain H explicitly excludes it. EDGAR live connector availability is not established. |
| 17. Owner slugs | Historical identifiers occur both in provenance and active configuration. Replacing all strings would corrupt pinned identities. Active checkout/dependency references need per-source resolution; historical receipts remain unchanged. |
| 18. SPDX coverage | Partial per-file coverage remains. A blanket header insertion cannot establish ownership or relicense imported code. First-party new files receive their applicable notice; the complete release inventory is still required. |
| 19. Project URLs | The earlier canonical repository URL correction is retained. It does not rewrite historical provider identities. |
| 20. CI startup failure | Verified main run `41682002605` has startup failure and zero jobs/check runs. Parent readback found 65 such runs since November 2. Local workflow lint/policy checks passed, but do not identify or repair the hosted account cause. No required checks are weakened. |
| 21. Network source offer | A source-identity manifest is not a Corresponding Source download. A release-specific source/build/dependency package and a prominent working offer to remote users are still required. No hosted legal-compliance conclusion is made. |
| 22. Secrets scan | The supplied report found none in its inspected diff. That limited result is preserved, not generalized to all files or environments. No credential values are included in these records. |

## Additional findings

| ID | Disposition |
| --- | --- |
| N1 | Hosted CI remains blocked as described in finding 20; cause unverified. |
| N2 | The report's main-tree qualification gap is valid. The campaign has additional exact-source local evidence, including metrology engines, PostgreSQL/NATS and TBRT. This does not complete all companion, container, hosted or physical gates. Mandatory isolation creates an explicit current execution prerequisite; old positives remain separately retained. |
| N3 | Confirmed and covered by the exact approved eight-file isolation patch. Actual positive namespace qualification is held in this environment. |
| N4 | Correction: disabling a principal already revokes access; changing the configured token also changes accepted credentials. Lifetime and queued-request rotation binding were missing. Local v2 gateway/panel work adds credential IDs, maximum one-hour validity, exact row binding and fresh checks around asynchronous admission/dispatch/disclosure. No real token is generated or installed. |
| N5 | Common request validation is being reconciled as in finding 7. Authentication, grant checks and profile checks remain distinct. |
| N6 | Unknown-key refresh and failed JWKS retrieval affect availability. Never bypass signature/issuer/audience/expiry checks or accept stale/unverified assertions to conceal that failure. A specific bounded refresh/cache test remains required. |
| N7 | `ncp_work_cancel` is destructive; additive writes remain individually classified. |
| N8 | Action tag references remain a supply-chain hardening item. Exact upstream commit identities must be resolved before replacing tags; no guessed SHA is acceptable. |
| N9 | Woodpecker's local backend is not an isolation boundary. Do not treat its passing result as a substitute for the companion process policy or a container qualification receipt. |
| N10 | The first-party campaign client receives `AGPL-3.0-or-later` package metadata, consistent with the repository's existing first-party grant. Imported component licences are untouched. |
| N11 | Confirmed: a Python import refusal is not a filesystem/network sandbox. |
| N12 | Hosted source offer remains release-specific and unqualified, as in finding 21. |

## Evidence and remaining gates

The original default-inheritance reproduction is sealed at
`/tmp/odsp-containment-independent-review/initial/receipt.json`, SHA-256
`037b68c2c8d502dc4ce9a209215ec213490fa653fdbc7f4119a157995d3ea818`.
It used only synthetic data and a local listener. An actual HTTP verifier
construction check refused a raw LogisticsHost before identity/network use.

The approved isolation proposal is retained unchanged at
`/tmp/odsp-completion-evidence/user-audit/pending-isolation/proposed-isolation.patch`,
SHA-256 `d11faf3a30c41a7a2d9d42599f7f679767e70133afd96f56f80ba2671e1d4759`.
Its initial broad approval request was rejected for nonspecific scope. The
exact subsequent apply-and-test request was approved; application is local only.
Its executable verification is recorded separately, without replacing the
original exposure evidence. The subsequent approved plumbing passes host policies
through investigation, covariance, geodesic, window, residual and metrology
host/CLI/replay paths; see [policy configuration](COMPANION_POLICY_CONFIGURATION.md).
Uncovered workflows and callers without a qualified isolating policy remain held.
Historical receipts do not supply launch authority, and exact replay requires
matching current policy history.

The pre-change baseline reconciliation covers all 585 recursive test files.
Latest distinct JUnit case outcomes are 20,020 passed, 1,514 skipped and one
unresolved `trusted_collector_unavailable` failure. This counts class/name cases,
not subtest assertions. The collector's installed `lsblk` ownership does not
meet the trusted-root prerequisite; the trust check was not relaxed. An initial
bytecode-fixture failure caused by the harness environment was rerun successfully
with that variable unset; both results remain. The aggregate is
`/tmp/odsp-completion-evidence/baseline-coverage-reconciliation.json`, SHA-256
`3d473a7d310a4293a8b3eba9e00820d65e92b71014bec1b62e843c46f40b7a48`.
This is pre-change local coverage, not a passing final campaign release gate.

Release promotion/deployment still requires current combined checks, installed package
and container evidence, hosted CI resolution, notice/removal-scope review,
source-offer verification and the separately specified protected registry,
identity, storage and spending approvals. No credential, access grant, paid
service, operational migration or live device action is authorized by this file.
