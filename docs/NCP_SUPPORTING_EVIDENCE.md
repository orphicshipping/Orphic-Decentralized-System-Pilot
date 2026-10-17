# NCP supporting evidence: custody before admission

This increment continues the existing Digital Thread Compiler and `next` worklist.
It adds local supporting-file assembly for one original unmet action. It does not
add an orchestrator, numerical operation, device driver, upload service or release
authority. All existing predicates remain unchanged.

## Working path

```sh
ncp production engineering next retained-execution \
  --profile scalar --thread-ref sha256:ORIGINAL_THREAD_BYTES

ncp production engineering evidence prepare retained-execution \
  --profile scalar --thread-ref sha256:ORIGINAL_THREAD_BYTES \
  --action-id sha256:ORIGINAL_UNMET_ACTION \
  --attach reference-report.pdf sha256:EXACT_REPORT_BYTES \
  --attach procedure.txt sha256:EXACT_PROCEDURE_BYTES \
  --note "Supporting material awaiting independent applicability review" \
  --output-dir ../supporting-evidence

ncp production engineering evidence inspect ../supporting-evidence \
  --packet-ref sha256:EXACT_PACKET_JSON_BYTES \
  --thread retained-execution --thread-ref sha256:ORIGINAL_THREAD_BYTES \
  --profile scalar
```

`net` remains an equivalent entry point. These are deliberate local retention and
review commands, not automatic remote retrieval. The host selects each pathname,
its exact expected SHA-256, the original thread and the original action.
A filename, a document's own claim of approval or a model answer grants nothing.

The existing production dispatcher routes this bounded workflow. It reuses the
original frozen-thread reader, `next_actions`, JSON bounds, record seals, safe
paths and regular-file readers. The existing engineering reader and its method
identity do not change; previous follow-up outputs remain byte-identical.

## Retained data

The packet binds the original thread, compilation, intent, request, evidence,
Session, execution and result identities through the unchanged follow-up basis.
It copies the selected original action, including its finding, predicate and any
unmet sampled frequencies. The operator cannot substitute another finding or an
invented action merely by sealing a new packet.

Each file is read once, checked against the host's selected hash, and retained
unchanged. Artifact slots have fixed names (`artifacts/0000.bin`, etc.); original
basenames are descriptive labels only. Input absolute paths are not retained.
Identical basenames with different bytes remain separate slots. Repeated bytes
within a packet refuse instead of increasing its artifact count. Repeating an
identical retention operation produces the same content identity, not another
physical measurement or independent experiment.

The optional note is explicitly unreviewed context. No operator name, review
signature, calibration expiry, uncertainty, physical sample time or document
approval is invented. The retention-method reference identifies source-file
bytes, not cryptographic proof of execution or an authenticated publisher.

## Authority and scientific semantics

Preparation returns `RETAINED_NOT_ADMITTED`. Inspection returns
`INTEGRITY_CHECKED_NOT_ADMITTED`. Successful command exit means only that the
requested local file handling completed.

```text
evidence_admitted          false
requirement_satisfied      false
authorizes_execution       false
new_scientific_executions   0
active_authority_restored  false
physical_qualification     HELD
standards_conformance      NOT_ASSESSED
```

PDFs, tables, images, executable scripts and embedded approval claims are all
opaque bytes here: no parsing, execution, OCR, model invocation, extraction,
trust-anchor discovery or external URL retrieval occurs. A valid file hash does
not establish that a report is authentic, relevant, independent or sufficient.
The qualified empirical importer/adjudicator remains a separate open capability.
No document receipt changes the existing requirement's PASS/FAIL/INDETERMINATE
status, removes a follow-up task or supplies a compute grant.

A packet cannot be submitted as a compilation, even with separately supplied
compute permission. Whole packets can contain sensitive customer information;
they stay local and are not a consent-approved sharing/export format.

## Bounds and failure behavior

There are at most 16 nonempty regular files, at most 8 MiB per file and 32 MiB
in total. Metadata is bounded to 64 KiB. Symlinks, unexpected inventory, unsafe
slots, duplicate content, wrong hashes and scope/identity substitutions refuse.
No packet is written into its source thread, and no existing output is replaced.

The destination and artifact directory are created with mode 0700. Original
bytes are written before the create-only completion manifest. Failed writes
leave their partial files; an incomplete packet cannot pass inspection. Actual
process-termination tests cover both sides of the manifest write. These controls
are not atomic directory transactions, hardware durability attestation, encryption
or containment of a malicious host. The selected local directory hierarchy and
running implementation remain trusted.

Inspection requires the independently selected packet hash and a freshly checked
original thread. It does not reopen the original attachment path. Exact recovery
of that thread preserves the binding; a new execution of the same inputs does
not inherit it. Existing original history is never edited to insert the packet.

## Internal verification requirements

| Requirement | Executable evidence |
| --- | --- |
| NCP-EV-01: exact selected bytes remain bound to the original unmet action | Opaque/binary round trip, explicit hash checks, same-name slots, source replacement/removal, exact recovery |
| NCP-EV-02: custody must not imply empirical admission or execution | Self-asserted approval text, resealed scope/authority attacks, no Session/controller dispatch, packet-as-compilation refusal |
| NCP-EV-03: failed or incomplete retention cannot look complete | Byte and inventory corruption, symlinks, capacity limits, create-only outputs, process termination around final manifest |
| NCP-EV-04: previous workflows keep their semantics | Unchanged original next worklist, original failure/unknown status, fresh-execution mismatch, source/installed regression and legacy command parity |

The existing source and installed qualification suites include the new tests.
An installed journey attaches a synthetic report to the original scalar worklist,
removes the input file, verifies retained bytes and confirms that empirical holds
remain. There is no physical calibration, EMC test, standards certification,
customer-data import, device action or model training in this demonstration.
