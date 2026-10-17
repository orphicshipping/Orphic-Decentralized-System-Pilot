# GaugeLink / NET retained-review integrity

This increment extends PR #209 at `626f33848df97f829f473f4857f709e03fbbca3a`.
It strengthens the existing Digital Inspection, scalar-study, offline export,
replay and MCP boundaries. No new acquisition/measurement schema, operation,
provider, ledger, trust service or hardware authority is introduced.

## Check the same bytes that are used

The previous operator view read `request.json` to check its external reference,
then the existing reader reopened that path. A complete different, internally
valid investigation could replace it between those reads. The external reference
could therefore check A while the returned view described B.

The optional external reference is now passed into the existing scalar reader.
That reader reads the request once, checks those exact bytes, and builds the
validated source from them. The workspace and graph must still agree with that
source through the unchanged Session and graph validators. An incoherent mixed
read refuses. The operator view has no separate preliminary request read.

The MCP factory previously validated a workspace, then separately reread its path
to initialize AgentHost. It now hands AgentHost the exact bytes already parsed
and validated. Later filesystem replacement cannot change the frozen artifact.
The existing eight-MiB MCP document bound and zero-operation grants remain in
force. AgentHost, its stdio protocol and shared Session implementation are
unchanged.

This is a coherent in-memory read of individually bounded artifacts, **not** an
atomic filesystem/directory snapshot, malicious-process sandbox, authenticated
source origin, fresh device attestation or proof of storage durability. A request
reference identifies exact input bytes, not one particular replay occurrence.
The workspace/graph checks retain their separate occurrence-binding rules.

## Same workflow, optional stronger source selection

The existing `net link inspection run/status --request-ref` interface is unchanged.
The engineering-level scalar readers now accept the same optional reference:

```sh
net link study inspect investigation --request-ref sha256:EXTERNAL_DIGEST
net link study replay investigation --request-ref sha256:EXTERNAL_DIGEST \
  --output-dir ../fresh-replay
net link study export investigation --request-ref sha256:EXTERNAL_DIGEST \
  --output-dir ../native-export
net link study report investigation --request-ref sha256:EXTERNAL_DIGEST \
  --output ../report.html
net link study mcp investigation --request-ref sha256:EXTERNAL_DIGEST
```

`EXTERNAL_DIGEST` is obtained from trusted selection of the exact original request
bytes, including formatting. Reading a hash out of an untrusted directory does
not authenticate it. No anchor is restored as authority, embedded into scientific
result identities, or accepted as an MCP tool argument. Existing unanchored
engineering calls remain available; they establish internal consistency only.
The four-value `link_study_io.read` return contract is preserved for existing
native/Workbench consumers. This does not qualify combined pending PRs.

## Failed audit means no accepted headline reading

An arithmetic audit can FAIL even though its Session operation completed.
The original native-export gate already refuses this case. Previously Digital
Inspection and the detailed HTML report still displayed a numerical candidate
in the principal reading fields.

Now a missing or failed audit withholds the headline corrected mean, and the
detailed report also withholds corrected table entries and headline standard
uncertainty. A known failed audit marks the operator view's nominal comparison
and uncertainty `WITHHELD_FAILED_AUDIT`. The page names the failed audit and the
next review action. All raw readings, computed candidates, covariance, checks
and execution identities remain unchanged in the retained workspace and Advanced
view. Nothing is dropped, clipped, repaired, relabelled as a measurement or
converted into a qualified part-rejection/release decision.

Passing examples keep their original numerical values. HELD physical qualification
and unavailable first-qualified-measurement timing remain unchanged for every
example. These controls do not confer ISO certification, laboratory accreditation,
calibration traceability or IEC hardware/EMC compliance.

## Internal verification trace

| Internal requirement | Reproducing/verification test |
| --- | --- |
| GL-REVIEW-001: external selection and interpretation use the same request bytes | `test_input_anchor_cannot_check_one_record_then_display_another` |
| GL-REVIEW-002: read-only MCP exposes only the validated workspace snapshot | `test_mcp_freezes_the_workspace_bytes_it_actually_validated` |
| GL-REVIEW-003: failed-audit candidates are retained but not headlined as accepted results | `test_failed_audit_withholds_default_readout_but_keeps_original_candidate` |
| GL-REVIEW-004: optional anchors grant no authority and refuse before side effects | Anchored reader/CLI refusal and actual MCP stdio tests |
| GL-REVIEW-005: historical bytes and scientific occurrences are not rewritten | Compatibility/replay tests and installed qualification |

The tests use genuine Session/AgentHost code, explicit synthetic file replacement,
real subprocess stdio and deliberately perturbed primary covariance arithmetic.
They do not replace a Session, proof verifier or independent arithmetic audit
with a successful stub. The source and noneditable-installed qualification reuse
the existing test workflow. Exact dependency pins and full-monorepo/hardware
qualification remain separate gates. No private product code or customer data
is copied into this increment, and no merge/deployment is authorized here.
