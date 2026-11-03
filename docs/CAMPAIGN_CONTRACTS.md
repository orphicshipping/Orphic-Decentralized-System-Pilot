# Campaign data and receipt contracts

These additive helpers define bounded data contracts for new campaign profiles.
They do not replace existing scientific serialization, principal authorization,
provider pins, execution holds or retention rules. The caller supplies the current
host identity and applies the operation's permission and scientific contract.

## Strict data boundary

[`campaign_contracts.py`](../src/ciw/campaign_contracts.py) exposes `parse`,
`canonical`, `closed_object`, `integer`, `text` and `mcp_envelope`. `Limits` is
host-selected configuration; requests cannot choose limits.

| Boundary | Default |
| --- | --- |
| Raw request / encoded response | 65,536 / 262,144 bytes |
| Value depth / total nodes including object keys | 24 / 32,768 |
| Array items / object fields | 4,096 / 1,024 |
| String / key length | 65,536 / 512 Unicode code points |
| Integer range | ±9,007,199,254,740,991; booleans are not integers |
| Floating-point magnitude | Finite binary64, at most 1e150 |

Raw input must be UTF-8 JSON bytes. Duplicate decoded keys, invalid Unicode,
nonfinite numbers, oversized integers, floating overflow and nonzero values that
underflow to zero refuse. A quote-aware depth scan runs before JSON decoding;
bounded traversal and serialization follow. SDK values must contain exact JSON
built-in types; custom objects, cycles and invalid keys refuse before copying.
Closed record helpers reject missing and additional fields.

The named format `ciw.python-json.v1` preserves CIW's sorted-key, compact,
ASCII-escaped Python JSON bytes. It is **not RFC 8785/JCS**. It keeps `1`, `1.0`
and `-0.0` distinct and does not normalize Unicode. Binary64 parsing is not an
arbitrary-precision decimal claim. Profiles needing another established science
format retain that format and its own replay contract.

`mcp_envelope` requires an application object and checks both it and the final serialized MCP
envelope containing text and structured copies. A generated text copy can span
multiple valid application strings, but cannot escape the whole-envelope cap.
An over-budget response refuses; it is never silently truncated.

## Full-content reproduction receipts

[`campaign_receipts.py`](../src/ciw/campaign_receipts.py) provides:

```python
receipt = build_receipt(binding=host_binding, request=request,
                        outcome="returned", result=result)
verification = verify_receipt(raw_receipt_bytes,
    expected_binding=current_host_binding,
    expected_request=current_request,
    recompute=host_selected_bounded_recompute)
```

The closed binding contains `operation`, `execution_id`, the complete current
`ciw.principal-context.v1` identity, the complete existing private release identity,
`runtime_digest` and `comparison_policy_digest`. The policy owner must match the
principal's existing owner derivation. Hash references use `sha256:` plus 64
lowercase hexadecimal characters; release Git and wheel identities retain their
existing formats. All bindings are selected by the host, never adopted from the
receipt being checked.

The receipt carries the complete request and result, an outcome from `returned`,
`held`, `refused`, or `invalid_contract`, fixed non-authorizing claims, the format
version, and hashes of the request, result and complete receipt content. Structural
validation checks these hashes but **does not establish reproduction**. Verification
first compares the full host binding and expected request, then calls the trusted
recomputation function once and compares the complete canonical receipt. Forged
content fails even if an attacker recomputes every submitted hash.

The recomputation callback returns exactly `{"outcome": ..., "result": {...}}`.
The host must enforce its time, memory, process, output and cleanup limits through
the existing execution boundary; this generic helper does not provide a sandbox
or deadline. Exceptions, cancellation and uncertain cleanup propagate. They are
not converted to a match, a mismatch, rollback or a retry. Inputs given to the
callback are isolated copies. No callable or executable is selected from data.

`execution_id` is the host's existing occurrence identity. The helper mints no
fresh identity and makes no stable-identity claim across new occurrences. Replaying
the same bound content preserves the same receipt digest. A profile with
nondeterministic observations must provide and identify an appropriate comparison
contract; it cannot claim full-content replay of a different occurrence.

The fixed claims explicitly say `authorizes_execution=false`,
`authorizes_state_admission=false`, `binding_authority=host_selected_unverified`
and `source_authentication=not_established`. Verification proves comparison under
the supplied host binding, not a signature, remote identity, scientific validity,
source authorship or admission authority. Domain-specific result schemas and
current permissions remain the integrating host's responsibility.

## Locked build inventory

[`write_locked_sbom.py`](../scripts/write_locked_sbom.py) writes deterministic
[CycloneDX 1.6 JSON](https://cyclonedx.org/schema/bom-1.6.schema.json) from an
existing source-bound private release, its independently selected identity,
an independently selected hashed runtime lock, the exact matching wheelhouse,
and its preserved-source record. It performs no downloads or installation.

```sh
python -B scripts/write_locked_sbom.py \
  --manifest /absolute/release/wheel/release-manifest.json \
  --identity /absolute/release/identity.json \
  --lock /absolute/dependencies/runtime.lock \
  --expected-lock-sha256 "$EXPECTED_LOCK_SHA256" \
  --wheelhouse /absolute/dependencies/runtime-wheels \
  --pins /absolute/release/pins.json \
  --output /absolute/evidence/release.cdx.json
```

`EXPECTED_LOCK_SHA256` is the digest of the reviewed lock selected by the trusted
build operator. It is not a value submitted by a model or an untrusted artifact.
The supported lock grammar is one exact normalized `name==version` plus one
SHA-256 per line. URLs, markers, extras, directives, duplicate packages, unhashed
or multiple-hash lines refuse. The wheelhouse must contain exactly the selected
runtime archives: missing, extra, duplicate or changed files refuse. Build-tool
wheels belong in a separate directory. Existing output files are never replaced.

Every archive is hashed in full, including native dependency bytes. Its bounded
metadata must identify the locked name and version. Archives are not imported or
executed. The release verifier checks the owned wheel, all owned roots including
`ciw` and `net_geomatics`, the source/closure marker and service-file closure. The
inventory binds all 21 imported tree records, the exact workcell capsule, runtime
pin files, source commit/tree, lock, manifest and wheel hashes. It does not edit,
repin or inspect an unrelated checkout's source trees.

Archive limits are 256 archives, 128 MiB each, 512 MiB aggregate, 20,000 entries
and 512 MiB declared expanded bytes per archive, 256 KiB metadata per archive and
an 8 MiB final inventory. The metadata cap admits the already locked pydantic
2.14.2 archive's 137,593-byte metadata. Local files and parents must not be
symlinks; platforms without `O_NOFOLLOW` refuse this artifact reader explicitly.
This tool operates on host-controlled immutable build inputs, not a concurrent
adversarial filesystem or a network registry.

The inventory describes the selected runtime closure and owned files, not an
independently resolved transitive graph or a vulnerability/license assessment.
Matching retained archive hashes is **not installed dependency byte attestation**;
the existing launch verifier's dependency version check remains distinct. Neither
the lock nor the SBOM authenticates a registry or source publisher. Independent
identity selection and distribution integrity remain release responsibilities.

## Local acceptance

```sh
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 python -B -m pytest \
  -q -p no:cacheprovider tests/test_campaign_contracts.py \
  tests/test_campaign_receipts.py tests/test_locked_sbom.py
```

The suite uses fixed random seeds for canonical roundtrips and malformed raw
mutations, exact boundary negatives, original-ID and resealed receipt forgeries,
current-binding denial before recomputation, cancellation propagation, complete
Unicode/MCP budgets, real ZIP archive tampering, all owned package roots, service
drift and outside-checkout CLI execution. It needs existing pytest only; production
helpers add no dependency. Integration tests must still exercise actual profiles,
current principal grants and installed MCP transport. Local checks do not establish
hosted operation, Windows support, source authentication or production readiness.
