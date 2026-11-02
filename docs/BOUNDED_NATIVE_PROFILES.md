# Bounded native computation profiles

These profiles expose existing pinned implementations through trusted subprocess bindings. They do not create a second science engine, authenticate caller evidence, admit state, qualify physical measurements or grant access to equipment. They are additional SDK/MCP profiles; the private HTTPS candidate does not automatically expose them. Discovery is not a grant.

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
