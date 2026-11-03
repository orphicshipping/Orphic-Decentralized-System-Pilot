# Bounded process policy and durable audit barrier

This campaign slice is opt-in host infrastructure. It grants no tool, resource,
scientific operation, credential, or state-admission permission. Existing
`PinnedSubprocessAdapter` bindings with `process_policy=None` retain their former
environment and runtime-identity contract. The integration owner chooses which
host constructors accept the optional policy and audit barrier.

## Process boundary

`ciw.process_policy.ProcessPolicy` is a frozen, host-created configuration.
Pass it at `PinnedSubprocessAdapter(..., process_policy=policy)` so source and
interpreter probes receive the same policy as later execution. Low-level users
can pass `process_policy=policy` to `_bounded_process`. Tool arguments cannot
supply a policy, interpreter, environment, executable digest, or mount list.

The host must independently pin the installed `prlimit` executable SHA-256.
`executable_sha256()` is a local measurement helper, not provenance attestation.
The policy rechecks this binary before every launch. It uses an explicit copied
environment, rejects loader/path injection variables, resolves commands through
the selected absolute PATH and applies resource limits without `preexec_fn`.
Canonical policy identity hashes the complete configuration but exposes only
environment key names, selected mode and limits. Hosts must avoid putting
credentials in the explicit environment or granted directories.

Default limits are 30 wall seconds per launch, 262,144 bytes each for input and
combined live stdout/stderr, 30 CPU seconds, 2 GiB address space, 16 MiB per file,
128 descriptors and RLIMIT_NPROC 512. They are configurable within closed bounds.
The lower of the existing adapter and policy wall/output limits wins. Combined
output overflow refuses; it does not return silently truncated success. The
existing process-group kill, bounded wait, pipe drain and sticky uncertain-custody
hold are retained. Cleanup after a cancellation does not prove operation rollback.

These limits have specific scopes:

- CPU, address space, file size and descriptors are per process, not aggregate
  workflow or descendant consumption. RLIMIT_NPROC is Linux real-UID accounting,
  not a private job cgroup. A privileged target can have different OS semantics.
- Wall timeout is per launch plus bounded cleanup, not an entire multi-launch
  scientific workflow budget.
- Limits mode does **not** isolate network or filesystems. A forked child that
  creates another session and closes inherited pipes can escape process-group
  cleanup. An actual synthetic test demonstrates that boundary and explicitly
  kills its own escaped child using a pidfd. Limits mode is unsuitable as a
  substitute when containment of untrusted/escaping children is required.
- Removing the ambient environment is not proof that mounted source/runtime
  directories contain no secrets. Neither mode attests dependency file bytes.

Strict `mode='bubblewrap'` requires a pinned Bubblewrap binary and explicit real
read-only roots. The constructed namespace grants those roots, a writable working
directory, private `/tmp`, minimal `/dev` and `/proc`; it requests network/PID and
other namespaces using `--unshare-all`, clears the environment and uses
`--die-with-parent`. Read-only mounts take precedence over the working directory.
Broad host-root mounts/working directories and parent-traversing paths are refused. A real namespace probe
must succeed before the requested command launches; there is no fallback to
limits mode. Root selection and compatible runtime paths remain host duties.

The current environment has Bubblewrap 0.12.0 and util-linux prlimit 2.41.5.
The actual strict probe fails with `bwrap: setting up uid map: Read-only file
system`. Strict target execution is held with `PROCESS_POLICY_HELD` /
`namespace_probe_refused`. No host settings, privileges or namespace permissions
were changed. The positive isolation test is explicitly skipped for that reason.
Strict containment requires an approved Linux host that permits the namespaces,
followed by real filesystem/network/escaped-descendant qualification there.

Primary mechanism references: [Bubblewrap documentation](https://github.com/containers/bubblewrap/blob/main/README.md),
[Linux resource-limit semantics](https://man7.org/linux/man-pages/man2/setrlimit.2.html).

## Audit and integration API

`AuditChain(path, chain_id=..., allowed_operations=..., max_records=4096,
max_bytes=2097152, lock_timeout=1.0)` writes canonical newline-delimited records
under a stable POSIX flock. Each record binds schema, chain identity, sequence,
previous hash and the complete canonical event. Reads, records, event fields,
lock waiting and total storage are bounded. Writes loop over partial writes,
fsync data and directory, and transfer the append result only after lock-context
exit succeeds. The lock file is never unlinked. Cleanup detaches its descriptor
before close and never retries an ambiguous close of a possibly reused fd.

Events accept only bounded host-generated correlation/operation identifiers,
principal/request/result digests, phase, effect state, UTC timestamp, fixed error
code and an optional closed reconciliation record. They reject raw request/result
bodies, credentials and arbitrary exception text. Hashes correlate evidence;
they do not attest source authentication, authorize work or conceal low-entropy
input values. Hosts choose appropriate protected audit storage.

The host must retain the chain head **independently**. Full canonical verification
and an independent expected head detect mutation, a recomputed forged chain,
deletion and truncation. A journal with no trusted external head cannot prove its
own history. This implementation is not WORM storage, a remote witness or a
solution to loss of both journal and trusted anchor. Directory ancestry is
host-controlled; no claim is made against a malicious administrator replacing
the storage namespace or both custody records.

```python
from ciw.audit_chain import AuditChain, AuditedCall, standard_result_outcome

chain = AuditChain(host_audit_path, chain_id=host_chain_id,
                   allowed_operations=host_operation_names)
auditor = AuditedCall(
    chain, principal_ref=host_principal_digest,
    read_anchor=host_durable_anchor_read,
    compare_and_set_anchor=host_durable_anchor_cas,
    result_outcome=standard_result_outcome,
)
value = auditor.invoke(
    operation=authorized_tool_name, call_id=host_correlation_id,
    request_ref=full_canonical_request_digest,
    call=authorized_host_call,
    result_ref=full_canonical_result_digest_function,
)
```

The callbacks above are required host infrastructure, not supplied implementations
or tool-call arguments. The anchor CAS must durably persist and return the exact
boolean `True` only on success. Tests use an actual separate SQLite database with
FULL synchronous transactions to exercise this contract. Production custody and
backup placement still require qualification; callback presence alone is not
proof of durability.

Enforce current PrincipalPolicy before admission and again inside the dispatched
host call if authorization can change. The audit layer never widens permissions.
Standard result classification records refused/denied envelopes as `refused`,
failed/error as `failed`, and partial/unknown as `unknown`. It never interprets a
returned refusal as proof that effects were rolled back. Hosts can instead supply
a closed `result_outcome` callback that returns exactly `phase` and `effect_state`.
Invalid classification after a return preserves uncertainty and fences the wrapper.

## Failure, restart and reconciliation

Admission is journaled and independently anchored before dispatch. Sink, context
exit or anchor failure prevents that dispatch. A later sink failure preserves
already-returned/committed effects and raises `AuditOutcomeUncertain` with its
effect-state/result reference and chained diagnostics. Operation cancellation plus
audit cleanup error retains both exceptions; an unambiguous audit append never
replaces the original cancellation. No response claims cancellation means rollback.

An existing correlation ID yields `AuditReplay(event)`, including after restart;
the caller must retrieve existing evidence, never reexecute the operation.
In addition, any latest `admitted` or `unknown` event, or an event with `unknown`
or `may_have_effects` effect state, fences **new IDs**. A failed/refused presentation
phase cannot hide uncertain effects. An explicit `reconciled` event is the only
exception for host-acknowledged uncertainty. The check
occurs both when reopening and atomically under the append lock. Admission
reserves space for its terminal and possible reconciliation records. Exhausted
capacity refuses before dispatch; it never evicts old audit records.

The host may call `auditor.reconcile_call(call_id, resolution=record,
validate=trusted_independent_validator)` only after reviewing custody. The closed
resolution contains schema `ciw.audit-reconciliation.v1`, a review ID, the full
pending record hash, decision `preserve_effects_no_retry`, custody
`confirmed_stopped` or `transferred`, effect state `returned`, `committed` or
`unknown`, an independent evidence reference and a UTC reviewed-at timestamp.
The validator receives that full record, stored/observed anchors and any
unanchored suffix; a digest match alone is insufficient custody validation.

The stored anchor must be an exact existing prefix of the canonical journal.
Tampered history cannot be adopted through reconciliation. After validation,
the host CAS acknowledges a surviving append if needed, and a full reconciliation
event is appended and anchored. Failure remains held; retrying this exact reviewed
resolution can acknowledge a surviving reconciliation without adding another one.
`resume_after_reconciliation()` alone cannot clear unresolved admissions/outcomes.
An acknowledged unknown outcome stays `unknown`; the original ID remains replay
only. No automatic retry, rollback, lease release or provider restart is performed.

Normal concurrent writers can encounter anchor conflicts between journal append
and independent CAS. They fail closed; this is not a cross-store atomic transaction
or a throughput guarantee. Prefer the declared single-writer topology and treat
anchor discrepancies as custody work. Timeout of a trusted callback is also a host
responsibility; the journal's own lock and I/O sizes are bounded, but no syscall
deadline is claimed for an unavailable filesystem.

## Executed local evidence

Evidence is recorded in `/tmp/odsp-campaign-evidence/process-audit`. Tests exercise
actual prlimit execution, combined floods, file/CPU/address-space limits, real
SIGINT with the owner surviving, descendant-held pipes, failed kill/reap custody,
after-effect cleanup errors, durable independent anchors, six-process contention,
crash before/after a synthetic committed effect, reopen fencing, tamper/truncation,
partial writes, unlock failure and close-after-effect descriptor reuse.

The unchanged adapter/cancellation regression set passed 74 tests before the new
audit restart cases. See final test receipts for exact current focused counts and
source hashes. Native solver, Windows, hosted storage, deployed strict namespaces,
live identity and production qualification are not established by these tests.
