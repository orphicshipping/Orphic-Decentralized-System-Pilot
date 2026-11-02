# Bounded native computation profiles

These profiles expose existing pinned implementations through trusted subprocess bindings. They do not create a second science engine, authenticate caller evidence, admit state, qualify physical measurements or grant access to equipment. They are additional SDK/MCP profiles; the private HTTPS candidate does not automatically expose them. Discovery is not a grant.

Cancellation and setup failures must stop and reap the owned subprocess even if both output pipes have already closed. If cleanup cannot confirm termination, the affected in-memory runtime binding remains held: later process launches refuse `RUNTIME_CLEANUP_UNCERTAIN`, exposed by these profiles as `runtime_cleanup_uncertain` and by the principal SDK as `PrincipalOutcomeUnknown`. Requests cannot clear that hold, and elapsed time or later observed process exit does not automatically reset it. Cleanup failures retain their diagnostic exception chain; they do not mean rollback or successful cancellation. Trusted operators must resolve process custody before deliberately creating a new binding. This local binding hold is not a durable cross-process recovery registry.

## NSR affine reconciliation

The [recompiler profile](../src/ciw/recompiler_profile.py) preserves the existing `calibrated-observable-runtimes.json` CBSR pin `daf43fc870ba926289c3bc4908db784691c7addd`. A trusted launcher supplies an absolute approved checkout, interpreter and principal to `open_recompiler`. Requests cannot choose these bindings. The kernel is imported only inside the fixed child bootstrap, using the existing pinned process adapter.

| Tool | Contract |
| --- | --- |
| `ncp_recompiler_describe` | Describe the bound runtime, request contract, limits and non-authorizing claims. |
| `ncp_recompiler_reconcile` | Run the original affine kernel on a bounded request with a caller-supplied stable `execution_id`. |
| `ncp_recompiler_verify_receipt` | Replay the full retained request and compare every canonical receipt field and digest. Submitted digest IDs alone are insufficient. |

Use `ciw.services.invoke` with the bound host, or the current principal facade. A facade requires the tool grant and, for reconciliation or replay, the separate `cbsr.affine-exact.v1` scientific-operation grant. The host's bound principal must match. Revocation is checked on each call and after reconnect; raw in-process administration is not a sandbox. The existing official MCP server can serve the same bound facade.

The four scientific outcomes remain distinct: `accepted`, `held`, `refused` and `invalid_contract`. A scientific hold/refusal is a returned result, not an operating-system failure. The raw CLI's exit 3 therefore cannot be treated as an undifferentiated subprocess error; the fixed bootstrap calls the original function and preserves its receipt. Transport, host binding and budget refusals remain separate.

Exact replay preserves the entire caller request and its occurrence ID, so a deterministic receipt may retain its original `result_id`. This is not an authenticated idempotency registry: caller IDs and source bindings remain assertions. `source_binding_verification` stays `caller_assertion_only`; all physical-validity, fault-isolation, evidence-admission and stability claims remain false. Matching canonical content establishes replay consistency, not upstream source authentication or independent scientific validation.

Admission bounds the complete name/arguments JSON to 65,536 bytes. Every issued receipt must also fit a complete verification request under that same cap. The complete duplicated MCP response is limited to 262,144 bytes. The fixed POSIX child has a five-second wall timeout, three CPU seconds, 256 MiB address space, 1 MiB file limit and 32 open descriptors; one host call holds the existing interruption-safe lease at a time. No cap is raised for larger inputs or outputs.

The underlying pinned adapter inherits environment variables other than its established path exclusions. This profile is **not a secret-scrubbing or network sandbox**. No live endpoint is used, and no claim of network denial follows from a local process test. Native Windows is unqualified. Installed operation requires the approved checkout/interpreter and the exact pinned revision; missing prerequisites remain explicit refusals or qualification skips, never passes.

Required qualification includes the real pinned kernel, all scientific outcomes, changed-content receipts with recomputed digest IDs, stable replay IDs, issued-receipt verification closure, whole-envelope Unicode budgets, process limits, surviving-worker lease cleanup, SDK/MCP parity, principal mismatch and current-grant revocation. The tests use synthetic inputs; their receipts do not establish physical validity.

## SRA schematic assessment

The [schematic profile](../src/ciw/schematic_profile.py) retains the existing `schematic-assessment` runtime pin `a6e79585950bb6860e5edce5ebd2cce39ea481f2`. A trusted launcher binds the approved checkout, absolute interpreter and principal with `open_schematic`. This profile retrieves declared graph neighborhoods; it does not search web pages or an ODRP corpus.

| Tool | Contract |
| --- | --- |
| `ncp_schematic_describe` | Describe the fixed runtime, bounded operations and non-authorizing claims. |
| `ncp_schematic_validate` | Validate a closed declared schematic using the pinned SRA parser. |
| `ncp_schematic_retrieve` | Retrieve at most 16 declared two-hop neighborhoods. |
| `ncp_schematic_assess` | Reuse the existing declared-workload assessment, including stale-result invalidation and eligibility decisions. |
| `ncp_schematic_verify_receipt` | Validate the complete public receipt, then freshly reproduce the existing numerical-result projection. |

The principal facade requires current tool grants and the separate `ciw.schematic-assessment.v1` scientific-operation grant for every operation except describe. The bound principal must match. Its request policy recognizes the fixed assessment configuration and schema-specific certificate/decision `owner` fields as scientific content; these fields cannot select a principal, retention owner, host policy or companion permission. Current grants are rechecked after reconnect.

The original `schematics.agent.run` is not the no-companion path: its behavior can invoke companions even when one call flag is false. This profile reuses the existing declared assessment's pinned submodules and fixed bootstrap. An explicit package namespace bypasses the upstream broad initializer; the child refuses imports of agent, companion adapters, kernels and rendering/projection modules. No companion execution or Mermaid/USD projection is exposed. The existing workflows and imported tree are unchanged.

Each assessment has a fresh session, execution and result occurrence. Verification preserves the submitted receipt and requires a new reproduction occurrence; it compares the existing numerical-result projection rather than claiming that the entire new bundle digest is deterministic. The public `odsp.sra.receipt.v1` schema removes launcher paths and their dependent internal identifiers, then binds all public content with its own canonical digest. It is not a native bundle digest, source authentication or an independent verification claim. The host reconstructs path-dependent native bindings solely from its own runtime before invoking the original full bundle validator. Recomputing a submitted digest cannot make altered scientific content pass reproduction.

Limits are 256 nodes, 1,024 edges, 16 queries, a 65,536-byte complete request and a 262,144-byte complete MCP envelope. Every issued receipt must fit a subsequent verification request. A host admits one call at a time; the total call deadline is 20 seconds and each child is bounded to five wall seconds, three CPU seconds, 256 MiB address space, 1 MiB files and 32 descriptors. Existing serializer limits can refuse a value earlier. Failed cleanup retains its exception context and does not imply rollback.

As with NSR, the existing pinned adapter's environment behavior is unchanged: it is not a network or secret-scrubbing sandbox. Only local Linux/POSIX with the approved runtime is qualified by the supplied tests. Required checks include actual pinned submodules, no agent/companion/projection imports, complete rehashed receipt tampering, public path redaction and relocated-host verification, distinct occurrence identities, request/envelope limits, live-process cancellation and lease recovery, and actual external MCP plus SDK authorization/reconnect journeys. All graph/certificate evidence remains synthetic or caller asserted; no observation, companion execution, physical validation or state admission follows.

## ClockSync affine event mapping

The [ClockSync profile](../src/ciw/clocksync_profile.py) computes one declared affine event mapping with the existing TBRT subprocess. It preserves the calibrated runtime pin `40507060ca7a9126a9d641999b994a757eef3bfd`. The distinct instrument-CI pin `edb4e5b99ec0ce384437e1c0f1c820ef04598e33` and reviewed upstream revision `e0c69d3b4b756ad1597e1cd7921caa05066e4871` are not substituted: their matching `clock.py` does not establish identical packaging or contracts. The imported trees and runtime manifests are unchanged.

| Tool | Contract |
| --- | --- |
| `ncp_clocksync_describe` | Describe the selected runtime, request contract, comparison policy and limits. |
| `ncp_clocksync_reconcile` | Map one declared timestamp and full joint covariance using an independently supplied `expected_reference`. |
| `ncp_clocksync_replay` | Validate the complete supplied request and result, then compare a new computation under the same bound runtime and canonical comparison policy. |

A trusted launcher passes the absolute checkout, interpreter and principal to `open_clocksync`. The principal facade requires the current tool grant and, for reconcile or replay, the separate `tbrt.affine-clock-reconcile.v1` scientific-operation grant. This is a narrow profile permission identifier, not a newly registered default engine. Caller fields cannot select runtime paths, principals, policies or pins. Nested argument constraints can restrict the expected reference, and revocation applies to existing facades and reconnects. Discovery creates no host binding. The private HTTPS candidate does not automatically provision or expose this profile.

The request requires explicit source/reference frames, anchored model parameters, nominal applicability interval, caller evidence references, joint-covariance order and cross-covariance policy. `expected_reference` is supplied separately and checked against the model; the wrapper does not infer it. Event, receipt and knowledge timestamps remain distinct declared fields. Integer-to-binary64 conversions that lose input precision are refused. Already-rounded floating-point input cannot recover discarded precision.

Results retain `reference_origin` and `event_time_delta` separately. Their sum, `event_time`, is labelled a rounded binary64 convenience value and can lose a small delta at a large origin. The covariance calculation remains the upstream first-order propagation, including declared cross terms; the profile does not perform synchronization, access hardware clocks, convert UTC, establish traceability or authenticate evidence. `source_binding_verification` remains `caller_assertion_only`, and every execution/state-admission and source-authentication claim remains false.

Receipts preserve the caller's explicit `execution_id`, complete request, complete numerical result, runtime identity, comparison policy and non-authority statements. Replay compares full canonical content, not just submitted hash IDs. It requires the same source/interpreter identity and dependency versions; dependency versions are not dependency-byte attestation. A changed runtime refuses verification, and no cross-platform bitwise guarantee is claimed. Replay preserves the submitted occurrence rather than minting an ID and claiming a deterministic new occurrence. A changed explicit occurrence ID changes the receipt even when the numerical result agrees.

The complete request, including a replay receipt, must fit 65,536 bytes, and the complete duplicated MCP envelope must fit 262,144 bytes. A host admits one call at a time with a 20-second total deadline. Runtime probes and the fixed child use five wall seconds, three CPU seconds, 256 MiB address space, 1 MiB file size and 32 descriptors. The established pinned adapter environment inheritance is unchanged: this is not a network or secret-scrubbing sandbox. No hardware, system-clock, batch, retention, SET export or cross-ledger write surface is added.

Required qualification includes independent numerical cases, correlated/singular covariance, frame and applicability refusals, lossy convenience-scalar cases, precision refusal, full rehashed tampering, runtime drift, surviving-process lease/deadline checks and actual SDK/MCP grant/reconnect journeys. Runtime-dependent tests require an approved `CIW_CLOCKSYNC_CHECKOUT`; a missing runtime is an explicit prerequisite, never a passing qualification. Only local Linux/POSIX and synthetic declared evidence are covered by these checks; hosted, live, physical and native Windows qualification remain separate.
