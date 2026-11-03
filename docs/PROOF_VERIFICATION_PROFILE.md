# Bounded proof receipt verification

The `proof` profile adds two read-only operations: `ncp_proof_describe` and
`ncp_proof_verify`. Its complete local capability is **Ed25519 signature
attribution and full receipt integrity**, using the existing
[`ciw.legibility`](../src/ciw/legibility.py) verifier. A signature is not a
zero-knowledge proof, a demonstration that a scientific program ran, physical
validation, identity authentication, or permission to execute or admit evidence.
Every response keeps execution and state-admission authority false.

The profile also contains an optional binding to the existing registered SCR
SP1 device-affine verifier. No native host/guest artifacts were supplied or
qualified for this campaign. An otherwise valid signed SP1 request therefore
returns `held / trusted_sp1_runtime_unavailable`. F2 statement preparation and
the existing proved-heat workflow remain separate; neither is silently exposed
as an inline proof verifier.

## Existing verification, new admission boundary

- [`proof_profile.py`](../src/ciw/proof_profile.py) constructs the existing
  Legibility contract and verifies its complete source, projections, manifest,
  signature and statement bytes. It compares the entire independently supplied
  expected request with the receipt statement. Submitted IDs, self-reported
  verification flags, or a recomputed manifest cannot replace this comparison.
- [`proof_registry.py`](../src/ciw/proof_registry.py) accepts a host-owned trust
  snapshot. Each anchor binds Ed25519 key bytes/fingerprint, program digest,
  circuit ID, source revision, policy digest and permitted subjects. Unknown,
  substituted, disabled or wrong-scope keys refuse. Keys are not credentials
  issued by this module, and a host trust declaration does not authenticate an
  upstream source revision or a person.
- `ProofKeyRegistry.replace()` requires the same registry ID and an increasing
  integer version. A concurrent replacement invalidates an in-flight acceptance.
  The returned trust digest describes the checked snapshot, not perpetual trust.
- `PrincipalPolicy.facade(..., proof=host)` is the intended software/MCP access
  boundary. It requires the same host-bound principal and checks current tool
  grants on every call and listing. `ProofHost` alone is a trusted local API,
  not remote authentication. No resources, retention, handoff, prover, guest
  build, hardware or admission endpoint is exposed.

## Request and receipt

`ncp_proof_verify` takes exactly `request_json` and `receipt_json`, both JSON
**strings**. Parsing within the profile preserves duplicate-key detection before
information is lost. The principal facade also bounds the outer request. The
strict MCP transport is selected for this host; caller fields cannot choose a
key registry, interpreter, path, policy, endpoint or runtime.

The expected statement has exactly these fields:

```json
{
  "schema": "odsp.proof-statement.v1",
  "system": "ed25519-attribution",
  "execution_id": "execution-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "subject": "host-bound-subject",
  "program_id": "sha256:<64 lowercase hex characters>",
  "circuit_id": "host-selected-contract.v1",
  "source_revision": "<40 lowercase hex characters>",
  "policy_id": "sha256:<64 lowercase hex characters>",
  "public_inputs": {"example_only": true}
}
```

This is a shape illustration, not a valid signed receipt. The `subject` must
match the launcher principal and the registry. `public_inputs` is inert JSON for
signature attribution; its scientific meaning is not assessed. For the optional
`sp1-device-affine` system it must instead have exactly `input_b64`, `output_b64`
and `proof_digest`; the existing fixed affine reference checks the full 68-byte
input and 60-byte output before native verification.

A receipt has exactly `schema: odsp.proof-receipt.v1`, `statement`, `bundle` and
`signature`. `receipt_bundle(statement)` is a deterministic helper producing the
existing Legibility bundle; it does not verify or sign anything. The existing
`ciw.legibility.sign_bundle` format is used. An SP1 receipt additionally carries
canonical base64 `proof_b64`, whose raw hash is included in the signed statement.
No other receipt fields are admitted.

Canonical comparison uses the campaign's bounded form of existing Python JSON
sorting, compact separators and ASCII escaping. It is not RFC 8785/JCS. Inputs
reject duplicate keys, lone surrogates, nonfinite/overflowing or underflowing
numbers, integers outside the exact safe range, excessive depth and extra schema
fields. Formatting differences that preserve the canonical content are allowed.

The complete expected statement and receipt have stable content digests. A
successful check receives a fresh existing Legibility verification occurrence
ID; it preserves the submitted execution occurrence. Repeated verification does
not claim the same verification ID, freshness, replay prevention or rollback.

## Limits and custody

| Boundary | Limit |
| --- | --- |
| Outer arguments | 65,536 serialized bytes; principal/MCP envelope may impose a stricter effective maximum |
| Parsed receipt | 49,152 bytes; depth 20; 12,288 JSON nodes |
| Expected/supplied statement | 8,192 bytes; depth 12; 2,048 JSON nodes |
| Registry | 65,536 bytes; 32 entries; at most 64 subjects per entry |
| Native proof bytes | 16,384; larger existing proof artifacts explicitly refuse |
| Complete MCP result | 262,144 bytes, including escaped text and structured duplication |
| Native subprocess | At most 30 wall seconds per launch, 30 CPU seconds per process, 2 GiB address space, 16 MiB written-file limit, 128 descriptors, 65,536 combined output bytes |

Signature verification processes one fixed-size Ed25519 signature over bounded
content; it is in process, not a preemptive CPU/memory sandbox or a hard wall-time
service guarantee. Cancellation returns a hold, never an accepted signature.

`NativeSP1` binds the existing `ExistingSP1Verifier.verify` implementation and
`PinnedSubprocessAdapter` with a host-created `ProcessPolicy` **before** source
or runtime probes. It does not call `prove`. The registry still verifies the
signed statement, and the existing SCR registered-guest gate still verifies the
actual proof. Interpreter, source commit, dependencies, host and guest hashes
come only from launcher configuration. The host's program digest must match the
existing device-affine program descriptor.

Process limits are per process/launch; the existing verifier performs several
bounded source/runtime probes. These numbers are not an aggregate workflow SLO
or a cgroup memory/process budget. The optional process policy's `limits` mode
is **not network or filesystem isolation**. Bubblewrap isolation requires its
own successful platform probe; no namespace restriction is bypassed. The
current host did not qualify that stronger mode.

The shared subprocess boundary kills/reaps its owned process group and handles
held descendant pipes. Cleanup uncertainty is sticky for that binding: another
verification cannot start through it. Cancellation or timeout is not proof of
rollback. Resetting a held native binding requires host-side diagnosis; it is
not an RPC operation. Key rotation likewise remains a host operation.

## Outcomes and evidence

- `accepted`: the described verification scope actually succeeded. For Ed25519,
  the scope is `ed25519_signature_attribution_only` and native verification is
  explicitly false.
- `refused`: malformed content, a mismatch, an invalid signature or denied trust.
- `held`: a missing or refused native runtime, native timeout/output limit,
  cancellation, concurrent verification or unresolved process cleanup.
- `unsupported`: a well-formed request asks for a system this profile does not
  implement. This is an MCP error, not a success with an unverifiable claim.

`ProofHost.call` preserves these outcomes; `mcp_result` sets `isError` for every
nonaccepted verification. Trusted SDK helper `invoke_proof` raises `ProofRefusal`
for nonaccepted calls, as other native profile helpers do; callers needing the
structured held/unsupported distinction use `host.call`. No error emits caller
content, raw key bytes, a native command, private paths or upstream diagnostics.

Focused validation is in
[`test_proof_registry.py`](../tests/test_proof_registry.py) and
[`test_proof_profile.py`](../tests/test_proof_profile.py). It uses deterministic,
public synthetic Ed25519 test keys, real cryptography, real external MCP
processes, current grant revocation and actual subprocess timeout/flood/signal
cleanup. Custody fixtures are explicitly not fake SP1 proof verifiers and never
return accepted native proof results. The locked existing setup is Python
3.12.14, `cryptography==46.0.0`, `mcp==2.3.0`, and `pytest==9.0.2` from the
repository's `legibility`, `mcp-control` and `dev` extras. No new dependency,
credential, provider pin, source import or hosted service is introduced.

The existing import manifest retains SCR runtime revision
`a59aba283b0304faeeb3e5d305087e7709e171ca` and import tree
`bfd617fd602502aefed02d8b0bac9bc41cf7ff3d`; this addition changes neither.

Native qualification still needs reviewed SCR checkout/source identity,
registered SP1 CPU v6.1.0 host and guest ELF hashes, pinned interpreter and
runtime dependencies, successful process-policy probes, representative genuine
proofs within this transport cap, and independently checked positive/negative
native proof cases. It is not closed by the signature or custody tests. A larger
proof protocol, aggregate production SLO, deployed identity, hosted containment,
physical qualification and source-to-binary attestation are not established.
