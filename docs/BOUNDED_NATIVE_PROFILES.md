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
