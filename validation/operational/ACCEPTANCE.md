# Operational-engineering acceptance

Work order: WO-NCP-OE-MCP-20261017. Base: `62577c96a280287c5df5d54783be00df1ab7c0de`.

This is a software acceptance record for the retained operational-engineering profile and its connected console/MCP paths. It is not physical instrument qualification, a general-purpose SCADA qualification, a remote deployment acceptance or authorization to actuate equipment. Code identity and wheel digest are recorded in `candidate.json`; the integration commit is also identified in the pull request.

## Executed validation

- Baseline: 115 existing recovery, measured-thermal, SDK, measurement and history tests passed before the extension.
- Compatibility run: 512 passed, one explicitly opted-in live-GPU test skipped. This run includes the earlier connected operational cases; counts must not be added as independent evidence.
- Final operational/transport/audit suite: 58 passed. It includes real modern/legacy SDK clients, cryptographically signed Access-token tests, process-kill/recovery, native source adapters, configuration attribution, correlated-error comparison, proposal invalidation, grant audit, alarm review, simulated command approval/dispatch and Workbench attachment.
- Existing console: six real-client/security tests passed, including the added operational surface. Three DOM tests passed.
- Installed wheel: J1–J5 executed outside the source checkout with `-I`, using the actual installed package and packaged assets. Three SDK sessions exercised current and legacy initialization, reconnect, same-attempt retry, retained resources and fresh execution identities. CLI and MCP returned the same finding.
- Chromium: actual browser → existing console → real SDK → installed NCP. The browser selected historical intervals, compared configurations, executed the fixed assessment/verification graph, prepared a next-test proposal, inspected the same retained record/resource and rendered the mobile layout. Packaged offline assets supported source/interval/finding selection. No page errors occurred.
- Read-only environment probe: NVML library unavailable. No kernel, stress test, driver/power change or physical command was launched.

Raw summaries, machine-readable receipts, installed module/asset digests and the exact wheel digest are retained beside this report. The browser reports concern explicit synthetic fixtures. Screenshots are not needed to reproduce the scientific records.

## Acceptance scenarios

| ID | Outcome | Executed evidence and scope |
|---|---|---|
| A01 | PASS | Configuration-linked MCP journey; exact source bindings, before/after diff and retained evidence trace |
| A02 | PASS | Uncertain/overlapping effective boundaries and unknown timing hold attribution |
| A03 | PASS | Missing values, out-of-range native inputs, malformed input refusal, empty/outside intervals and stale alarms remain distinct; no zero fill |
| A04 | PASS (software) | Named clock/boot selection, preserved call/receipt context and existing history/reset rules; no inferred common wall clock or fitted drift |
| A05 | PASS | Journal duplicate-delivery retry retains the original receipt; separate equal-valued occurrence identities survive; native duplicate-order guards remain intact |
| A06 | PASS | Full common-mode joint covariance retained, unknown budget guarded; no standard-error reduction from repeated windows |
| A07 | PASS | Predeclared paired-reference AGREEMENT, DISAGREEMENT and INSUFFICIENT_EVIDENCE; real thermal prediction/estimation retains unknown-total-budget limitations |
| A08 | PASS | Multiple changed fields/confounders retained; scalar residual does not identify a unique fault |
| A09 | PASS | Frozen source/model/policy/template dependencies, changed admission and superseding-finding invalidation; proposal preparation performs no execution |
| A10 | PASS | Paths/URLs, unknown methods, provider selectors, grant escalation and invalid argument types refused before dispatch |
| A11 | PASS | Completed retries return original identities; explicit fresh replay creates new execution/result occurrences and preserves original evidence |
| A12 | PASS (software) | Actual provider and collector subprocess termination; unknown outcomes persist across restart without automatic repeat; SDK reconnect and console disconnect do not own collector accounting |
| A13 | PASS | Segment quota stops without overwrite, explicit gap/backpressure state and reserved journal control headroom; explicit collector restart keeps prior occurrence receipts |
| A14 | PASS | Provider-disabled inspection, query, trace, reopening and model-result inspection; offline validation allocates no verification occurrence |
| A15 | PASS (local authenticated boundary) | Wrong subject/audience/investigation, Host/Origin and worker substitution denied; revocation denies subsequent calls; real RSA signatures, local fixture JWKS |
| A16 | PASS | CLI, real SDK, actual console, resource read and retained Workbench/offline view inspect the same underlying record identities |
| A17 | PASS | Installed wheel imports under the selected environment prefix, SDK child uses `-I`, packaged assets hashed and complete journey executed outside checkout |
| A18 | PASS for unavailability handling | Explicit synthetic/recorded scope preserved; actual NVML unavailable; physical acquisition fidelity remains unestablished |
| A19 | PASS for software boundary | 80°C equality, higher, invalid and missing prerequisites block; collectors are read-only and no workload launch tool exists; live readiness remains unverified |
| A20 | PASS (affected regressions) | Existing AgentHost recovery, frozen MeasurementHost/Link, Board refit, thermal/reference, temperature, NVML and operator-workbench regressions preserved |

## Delivery and remaining qualification

Implemented local software journeys are usable through the installed wheel, resolved generated MCP configuration and existing interactive console. Bounded collection and new-source admission extend the existing Link Journal rather than adding a competing event ledger. Numerical execution remains owned by AgentHost/Session/OperationRegistry; native thermal and joint-reference arithmetic remain in their existing implementations. The source family/provider matrix is in the operator guide and `inventory.json`.

The supervisory path prepares and structurally validates exact Link configuration plans, retains separate operator approval and dispatch attempts, and delegates supported synthetic changes to the existing DeviceRuntime. That runtime refuses physical configuration/calibration/update/CONTROL. Real equipment control therefore remains explicitly unavailable, rather than being simulated as an installed physical capability.

A physical device and connection, applicable calibration/reference evidence, an installed optional scientific provider, and selected laptop/worker credentials are separate qualification prerequisites. Their absence does not prevent the implemented local software journey. Actual remote Access/TLS/network deployment and physical GaugeLink/GPU/EGM acquisition are unestablished. Earlier verified profiles, metrology drafts and local equipment protections are unchanged; no merge, release, public hosting or physical permission is implied by this report.
