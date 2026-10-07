# NCP source-cited evidence assessment

This increment connects a retained supporting packet to an inspectable assessment
of its relevance and limitations. It extends the existing engineering command
family. It adds no acquisition, execution, orchestration or authorization service.
The underlying engineering findings and empirical gates remain unchanged.

## Workflow

The new command family is `ncp production engineering evidence review`:

- `template`: check an externally selected packet and its original thread, then
  return the six internal review questions with every question NOT_ASSESSED.
- `record`: accept an exact hash-selected assessment-input JSON, validate all
  source citations, and retain the input plus a derived completion record.
- `inspect`: revalidate that record against the original input, packet, thread
  and cited bytes. It never relies solely on a stored status flag.

All commands take the supporting-packet directory, `--packet-ref`, `--thread`,
`--thread-ref` and `--profile`. `record` additionally requires `--input`,
`--input-ref` and a new `--output-dir`. `inspect` requires `--assessment` and
`--assessment-ref`. `template` prints JSON; no original evidence is edited.
`net` remains the same compatible executable entry point.

The review questions are authenticity, applicability, method/conditions, timing,
uncertainty, and independence. They are internal question categories, not an
exhaustive standards checklist, universal sufficiency test, or legal-admissibility
assessment. The protocol does not infer which product standard applies.

## What the check does and does not establish

Each question contains a declared status (SUPPORTED, CONTRADICTED or
NOT_ASSESSED), bounded rationale, and at most four citations. Both SUPPORTED and
CONTRADICTED require at least one citation. No "PASS" or "APPROVED" input exists.
A citation identifies one artifact already in the selected packet, a zero-based
half-open byte range `[start_byte, end_byte)`, and the exact SHA-256 of that range.
The range is nonempty, within the artifact and at most 64 KiB. Byte positions
are not character offsets, extracted text offsets or PDF page numbers.

The inspector checks that those exact bytes exist in that exact source. It does
not check that the bytes justify the rationale, validate a certificate issuer,
authenticate the reviewer, attest competence, or establish physical truth.
Arbitrary PDF or binary content remains opaque. No OCR, parser, AI, network or
code execution is involved in evaluating a citation.

A report claiming its own authenticity therefore cannot authenticate itself by
being cited. Multiple citations to one report do not establish independent
corroboration. `cited_artifact_count` counts distinct cited byte artifacts only,
not independent experiments, instruments, reviewers or sources.

The output status is derived from the declared checks:

| Input declarations | Output status |
| --- | --- |
| Any CONTRADICTED | CONTRADICTIONS_DECLARED; unassessed dimensions remain listed |
| No contradiction, some NOT_ASSESSED | REVIEW_INCOMPLETE |
| All SUPPORTED | DECLARATIONS_COMPLETE_NOT_ADMITTED |

Every outcome retains `semantic_support_checked=false`,
`reviewer_identity=NOT_AUTHENTICATED`, `reviewer_authority=NOT_ESTABLISHED`,
`evidence_admitted=false`, `requirement_satisfied=false`, and no execution grant.
The original finding and assessment status are copied from the actual retained
thread, not overwritten with the reviewer's declaration. Physical qualification
remains HELD. Qualified empirical adjudication remains an unimplemented boundary.

## Integrity, recovery and performance

The original assessment-input bytes are retained in `assessment-input.json`.
`assessment.json` is a sealed derived record binding that exact input, the
supporting packet, original thread basis, action and finding. Its completion
record is written last. Output is create-only, outside the packet and thread,
in a directory created with mode 0700. An interrupted write cannot pass a
complete inventory check. This is not an atomic filesystem transaction,
encryption or hardware power-loss durability guarantee.

Reinspection verifies the exact external assessment hash, validates the full
packet, revalidates citations and recomputes the structural result. Rehashing a
forged summary cannot turn it into admission or hide changed source checks.
The method hash identifies the implementation recorded at creation; it is not a
signature, proof of execution or independently attested implementation identity.

After the existing full custody check, cited files are hash-checked when reread
for citation use, then cached only for that call. Six questions citing the same
artifact use one citation-phase read rather than six. This cache has no cross-run
freshness assumptions. Source replacement before this read refuses; there is no
claim of an atomic snapshot across files or malicious-host containment.

Original packet/thread byte identities are unchanged. Exact thread recovery
preserves the binding; a new execution of identical measurements does not inherit
it. The existing `next` worklist is not altered and still requests qualified
empirical evidence. The review record is not a computation plan or sharing grant.

## Qualification

`tests/test_ncp_evidence_assessment.py` exercises actual existing thread and packet
handling using synthetic reports: exact source spans, incomplete/contradicted
reviews, malformed and oversized inputs, false summary claims, source replacement,
recovery, write failures, no dispatch, and CLI routing. The installed qualification
script includes the new suite and an actual template/record/inspect journey.

The prototype is a structured, source-linked review record, not an automated
expert reviewer or a qualified importer. Software test success, package-integrity
checks and empirical qualification remain separate.
