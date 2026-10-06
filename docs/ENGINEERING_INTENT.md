# Engineering intent on NET's existing production graph

## Delivered boundary

This implements the first bounded part of the supplied Digital Thread Compiler
brief: **intent -> existing typed plan -> explicitly permitted computation ->
retained acceptance findings -> stop/review -> proposed next revision**.
It extends the unified Link / installation candidate at
`827cbed62b02f1a001869b0fa70daabd6491efcc` (PR #214), without another orchestrator,
queue, Session, capability registry, observation schema or numerical engine.

The existing `production.plan`, `preflight`, `run_production`, `acceptance` and
`inspect_production` own work-order execution. `control_plane.plan_graph` owns
operation ordering and dependency typing. A small compiler selects between two
explicit installed recipes. It does not perform optimal graph search, generate
code, load user-selected executables, schedule physical resources, or estimate
information gain. The same compiler/controller is exercised on both recipes:

| Host-selected profile | Existing graph | Existing capability |
| --- | --- | --- |
| `installation` | `link.characterize-installation.v1` | `reference_paired_installation` |
| `scalar` | Original decode -> affine correction -> mean -> independent audit | `link_scalar_study` |

Both process retained input only. Neither dispatches acquisition, excitation,
configuration, manufacturing, a robot, firmware, a remote service or training.
Profile names are matched against the finite installed allowlist. Saved JSON is
not a source of executable code or permission. Link remains one application.
The polymer workload proposed in the brief is not implemented by this increment.

## Intent is a controlled planning document

`ciw.engineering-intent.v1` is not a replacement acquisition/measurement schema.
It declares an intent identity, revision, profile, exact request-byte reference,
named requirements and a finite operation budget. The selected source retains
its existing scientific evidence identity: adding an intent does not turn a
recording into another acquisition. The source itself continues to bind original
units, channels, ticks, installation/procedure identities and covariance.

The `installation` recipe requires explicit sampled frequencies. Existing source
plan limits determine the gain, phase and coherence criteria at those points.
Absent or unresolved frequency coverage is INDETERMINATE; it is not interpolated
into evidence for an entire frequency band. The `scalar` recipe requires both
independent arithmetic audit PASS and a within-limit nominal comparison. These
are computational criteria, not calibrated physical acceptance.

Preflight uses the actual bound CapabilityRegistry, operation implementations,
worker allowlist, typed graph, gate implementations and operation budget. A
missing provider, merely advertised/unbound capability or insufficient budget
refuses before the compilation directory is created. The compiled document
retains the original production plan and bindings; it does not duplicate them
as a second execution format. At most one attempt of the selected recipe is
compiled. There is no implicit retry or candidate repair.

The compiler output explicitly says `authorizes_execution=false`. `run` requires
an explicit host `--allow-compute`, an exact external compilation reference and
matching current bindings. Production rechecks its existing bindings during
execution. This is a local operator approval, not production account/PKI,
role-based authorization or a defense against a privileged hostile host.

## Computational result versus industrial evidence

Five internal evidence obligations are always present in these two recipes:
reference traceability, timing qualification, uncertainty budget, independent
validation and product safety/EMC evidence. This is an initial readiness checklist,
not an exhaustive applicability or conformity assessment for a legal standard.
No ISO/IEC edition, certification, accreditation, safety integrity level or
calibration claim is inferred from the attached architectural proposal.

The present profiles have no qualified importer/adjudicator for those independent
records. These five predicates therefore return **INDETERMINATE**, including when
source files contain a calibration or matched-chain declaration. They do not
claim such evidence is false or cannot exist; it has not been admitted through a
qualified gate here. A supplied `verified=true`, certificate filename, signature
or successful simulation cannot clear them. Extending those gates requires
actual independently reviewed evidence contracts, not changing a status string.

Original acceptance outcomes bind their exact result IDs/digests and gate
implementation identities in the existing production records. A numerical FAIL
is retained, and missing evidence cannot be averaged away in an overall score.
The compiler does not run another FFT, modify covariance or independently
validate the H1 estimator against hardware. The scalar Fraction audit is the
existing independent arithmetic check; neither it nor repeat calculation by a
payload validator establishes physical validity.

The operator summary distinguishes:

| Outcome | Meaning |
| --- | --- |
| `REQUIREMENTS_NOT_MET` | At least one declared numerical requirement failed. No physical cause is inferred. |
| `ANALYSIS_UNRESOLVED` | A required computation, sample or prerequisite is unresolved; no numerical pass is fabricated. |
| `EVIDENCE_REQUIRED` | The bounded numerical requirements pass, but industrial evidence is still unadmitted. |

Every outcome keeps `physical_qualification=HELD`, `standards_conformance=NOT_ASSESSED`,
no formal verification ID, and no hardware, release, training or promotion grant.
The original production job stays held/rejected rather than falsely completed.
Raw source and computed candidates remain retained. An unavailable spectral
uncertainty remains unavailable; a highly coherent resonant gain can still FAIL.

## Offline review and controlled continuation

The create-only execution directory retains exact intent/request/compilation
bytes and the original production directory. A final `thread.json` indexes those
files with exact byte counts and hashes. It is a retention manifest, not another
ledger. A crash before it is written leaves partial production history, never a
completed thread. No automatic resume, overwrite, deletion or repair is attempted.

Review requires an externally held hash of the exact `thread.json` bytes. It
captures bounded files once, checks the complete manifest, freezes them into
scratch storage, and calls the existing production reader and Session validators.
Original plan, binding, source and occurrence relationships must agree. Extra,
missing, altered, misbound or unsafe relative paths refuse. Rehashed wrappers do
not excuse inconsistent scientific/production records. An external hash must be
obtained through trusted selection; reading it from the same untrusted directory
is not authentication. This is not an atomic filesystem snapshot, race-proof
sandbox, signed origin, hardware storage attestation or production backup system.

Historical review returns the original recorded Session/execution/result IDs,
not the helper Session identity created while reopening. No new scientific
execution occurs. Current code/gate identity must remain compatible; an old
compiler cannot be silently recertified by another implementation. Runtime
history is not active permission and cannot execute after current binding drift.

`revise` first reviews the exact predecessor thread. It keeps intent identity,
increments revision once, and requires unchanged requirement/constraint bytes
and decision basis. It accepts newly selected source evidence but cannot silently
relax gain/phase limits, reference objectives, fidelity or budgets to make a
candidate pass. A change to the engineering question needs a separately reviewed
new intent, not this continuation route. This is not a global authorization
system preventing an operator from authoring a different study.

The new compilation retains the prior thread/intent references, original
execution/result IDs and rationale. Those references document the predecessor
checked at preparation time; they are not a signed attestation or an automatic
fetch of predecessor records on a different machine. Retain the predecessor.
Running the new packet needs another explicit grant and creates fresh computation
occurrences. Original measurements/installation identities remain their own;
no claim is made that equal declarations prove an unchanged physical mounting.

## Executable usage

From the candidate checkout/installation:

```sh
net production engineering demo --profile scalar --output-dir ../engineering-scalar
net production engineering demo --profile installation --output-dir ../engineering-installation
```

Both demonstrations intentionally exit **2**, signifying a retained held/failed
assessment, not a tool crash. Scalar numerical checks pass with missing physical
evidence; the compliant installation fails the authored 384 Hz point. The
installation demo reuses the original Link acquisition fixture/demo, which first
creates its own source investigation; its computation occurrence is explicitly
separate from the subsequent one-operation production assessment. It is not an
additional physical experiment. All demonstration observations are synthetic.

A prepared retained request uses an independently selected exact input hash:

```sh
net production engineering example --profile scalar \
  --request-ref sha256:EXACT_REQUEST_DIGEST --output intent.json
net production engineering compile --profile scalar --intent intent.json \
  --request request.json --output-dir ../compiled
net production engineering run ../compiled --profile scalar \
  --compiled-ref sha256:EXACT_COMPILATION_DIGEST --allow-compute --output-dir ../executed
net production engineering inspect ../executed --profile scalar \
  --thread-ref sha256:EXACT_THREAD_DIGEST
net production engineering report ../executed --profile scalar \
  --thread-ref sha256:EXACT_THREAD_DIGEST --output ../readiness.html
net production engineering revise ../executed --profile scalar \
  --thread-ref sha256:EXACT_THREAD_DIGEST --intent revision-2.json \
  --request newly-selected-request.json --reason 'Recorded engineering review rationale' \
  --output-dir ../proposed-revision
```

Exit **0** means compilation, proposal or report-file creation completed; it is
not physical acceptance. Exit **1** means the command refused/failed. Exit **2**
means the assessment is retained but held or fails requirements. API consumers
must use the explicit status and evidence, not mistake file creation for release.

The local HTML has no network/JavaScript dependency and a collapsed Advanced view.
It is a private operational report, not an approved contribution/export. Detailed
records may contain proprietary equipment/process context. Queue time, monetary
cost and information gain remain null. Only actual operation/result and
pass/fail/unresolved counts are reported as workflow observations. No SCG
optimizer or cost-benefit selection is claimed.

## Internal acceptance trace

| Internal requirement | Test evidence |
| --- | --- |
| ENG-001: reuse the existing graph/controller on two workloads | `test_compiler_uses_existing_plan_preflight_without_execution`, original-production repair regression |
| ENG-002: plan is not permission | `test_explicit_host_grant_is_checked_before_any_source_read` |
| ENG-003: capabilities and budgets are real preflight constraints | unavailable-registry, budget and runtime-drift cases |
| ENG-004: preserve numerical failure and missing evidence separately | scalar fault, coherent resonance and unresolved-source cases |
| ENG-005: no silent acceptance-criteria relaxation during continuation | continuation objective/threshold/budget cases |
| ENG-006: original source and occurrences survive read/revision | offline review and fresh-revision occurrence cases |
| ENG-007: use the exact bytes verified during review | frozen-originals replacement and resealing/tamper cases |
| ENG-008: interrupted work cannot appear complete | retained-interruption test and create-only output checks |
| ENG-009: no seed-dependent planning identity | two actual subprocess hash-seed cases |
| ENG-010: no aggregate pass over unresolved physical requirements | scalar evidence hold and observed-count cases |

The test and installation gates extend the existing Link workflow. Repository
pins are unchanged. The local verification-only wheel can be tested with available
dependency bytes, but that does not qualify the required pinned environment,
full monorepo, all pending integration branches, physical devices, EMC/electrical
safety, field endurance, secure updates, native SP1 or production account/transport.
No merge, deployment, hardware excitation, cloud transfer or release is part of
this increment. Do not route arbitrary physical operations through these recipes.
