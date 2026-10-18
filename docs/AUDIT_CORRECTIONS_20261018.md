# Code audit corrections and retained projector path — 18 October 2026

Implementation: `6f783dd8a37c4509a91cc86f8cfccba6ca2579e1`, based on main
`861e29fa8f78e3f953991ca7f5f62790a3088df7`. This increment does not change the
qualification reference `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`.

The prior code review reproduced five defects. The claim that all remaining
work was operational was therefore too strong. This patch corrects those
defects and reuses the existing teaching exporter for optional projector launch.

## Corrected defects

| ID | Reproduced behavior | Correction and regression evidence |
| --- | --- | --- |
| CR01 | Retrying an old finding repointed its scope's current head and could revive a stale, approved synthetic configuration command. | Immutable journal CAS distinguishes first publication from retrieval. Historical retries cannot promote a finding. Missing heads fail closed. Tests cover approval, supersession, reopen, repeated refusal, interrupted publication and competing identical publications. |
| CR02 | `approve-command --command REF` overwrote the subparser's action and failed before approval. | CLI action and command reference use separate attributes. Both direct and actual CLI approval paths complete the existing synthetic dispatch/retry journey. |
| CR03 | A resealed recording operation could reuse an uncatalogued verification reproduction's execution ID on workspace reopen. | All validated reproduction identities enter the existing Workbench claim set. Session checks that complete set, independently of public catalog projections. Legacy and modern recording regressions refuse before output creation. |
| CR04 | Godot could display VERIFIED for whitespace-padded status or an unguarded card verification field without fresh verification. | Shared normalization precedes strict boolean freshness checks for both status fields. The actual Godot labels pass 122 adverse checks. Truthy strings/numbers cannot enable verification labels. |
| CR05 | Bevy converted retained coordinates to binary32 before fitting bounds; a short path translated by `1e12` disappeared. | Bounds, center and scale use binary64 before final GPU conversion. Native origin and translated views both retain 742 visible path pixels and identical screenshot hashes. Summaries preserve original binary64 coordinates. |

## Operator simplification

The [existing exporter](../examples/workflows/emit_all_usecases.py) supports
`--scenario` and optional `--client godot|bevy --binary /absolute/path`.
Executable selection is an explicit host argument; no catalog or render field
selects code or authority. The same receipt retains input/output and executable
hashes, arguments, process outcome and input stability. Failed/time-limited
launches retain explicit outcomes. A successful exit alone does not assert
visible content or scientific qualification.

The [render contract v1](RETAINED_RENDER_CONTRACT.md) documents existing
fields, extensibility, supported display subsets, status semantics and
binary64 retention. Godot's retained mode instantiates its existing teaching
view without constructing or connecting the Session client. Native Bevy and
Godot journeys display the same exact JSON; the Session listener records zero
connections. The ordinary Godot Workbench/Oscillator protocol and the separately
registered bounded Bevy projectile computation remain available.

No workflow is added. The existing Godot checker runs the new status regression;
the existing workflow-contract lane installs the declared schema-validation
extra. Current inventory: **156 workflows, 194 jobs, zero exact duplicate
groups**. Repeated modules across distinct contexts are not a deletion proof.

## Executed evidence

Counts below overlap and are not additive.

| Check | Result |
| --- | --- |
| Control, retention and affected workflow regression | 265 passed, 61 explicit provider-gated skips |
| Teaching export, launcher failures and schema conformance | 23 passed |
| Installed-wheel affected regression | 121 passed, 14 explicit provider-gated skips |
| Installed distribution smoke | Generate, analyze, teach, inspect and replay PASS |
| Installed MCP J1–J5 | PASS; modern and legacy protocols, reconnect and fresh replay |
| Installed SDK HTTP | PASS; three sessions, separate worker, restart, interruption and revocation |
| Complete Godot checker | Import, presentation, strict status, teaching catalog, live protocol, channel generality, adapter and experiment boundaries PASS |
| Rust projector unit checks | 7 passed; pinned Rust 1.97.1 viewer build PASS |
| Native projector journey | Same input hash in both windows; zero Session contacts; origin/translated Bevy views PASS |
| Atomic ALCHEMI through the corrected installed wheel | 12 actual CPU cases / 58 checks PASS, including finite differences, transformations, offline reopen, explicit replay, refusal and tamper checks; worker pin `c7bf306d891b5ce603325fea9918d17a1da483c3`, float64; CUDA and physical validation not established |
| Independent review | No required defects; reviewer independently ran 33 passing focused cases |
| Full isolated regression | 519 modules completed: 18,950 passed, 1,173 skipped, 13 failures and 136 setup errors confined to the two Git-history modules; overall gate NOT_PASSED |

The new wheel SHA-256 is
`031b7e25c813a51948c5a6fdd32f498247b38c785a1794ab07d7d8b43eaec4c9`;
717 package files byte-match source. The published implementation tree has
5,430 files across 794 folders. All 5,427 materialized files byte-match their
published Git blobs. Three unchanged binary source-retirement backup pieces
are absent locally; they are preserved in the published tree. Root Git ancestry
is incomplete locally. No full-history claim, graft, replacement or waiver is
made.

`test_monorepo.py` reports 13 failures, 92 setup errors and 27 passes;
`test_module_update.py` reports 44 setup errors. Missing backup blob objects
prevent their disposable clones from checking out, and missing original commits
prevent ancestry/runtime-source checks. The other 517 modules completed without
failures or errors. The unchanged history tests must pass in a complete checkout;
this evidence does not substitute byte snapshots for their ancestry gate.

## Hosted baseline update

Observation at **2026-10-18T05:20:28.545Z** supersedes earlier zero-artifact
snapshots. Of the same 14 required baseline runs: nine were queued, one pending,
one running, two completed successfully and one failed. Ten available artifacts
were downloaded, checksummed and inspected without executing their contents.

`release-operator.yml` passed on Linux and Windows: 43 tests per platform,
zero failures/errors/skips, and three installed synthetic correction journeys
per platform. Both retained candidate records bind the exact baseline SHA;
715 wheel package files per platform byte-match that baseline's Git tree.
Platform wheel hashes and archive/member hashes are retained separately.
`workflow-contracts.yml` also completed successfully; its baseline revision
does not emit an artifact. Other artifacts from unfinished workflows remain
partial evidence.

The later observation at **2026-10-18T05:38:46.218Z** records **six successful
runs, one failed run and seven queued runs**. Seventeen artifacts and eleven
successful-job logs are now retained. Newly completed software lanes include
declared workloads (38 tests plus installed replay), calibrated observables
(50 tests each on Python 3.12 and 3.13), identified design (91 tests plus the
installed eleven-pin journey), and the four-platform hardware software
qualification matrix. These executed scopes supersede their earlier pending
status. They do not establish physical hardware validation or qualify this
post-baseline implementation SHA. Overall baseline qualification remains open.

The SP1 job failed its unchanged resource preflight: 7,035,379,712 bytes
available RAM and 13,635,911,680 bytes free disk were below its required 7 GiB
RAM and 20 GiB disk. Its archive contains only `resources.json`; native builds
and proving did not execute. This is a failed qualification gate, not evidence
of native proof success. No requirement was reduced and no waiver was issued.

The [authorized branch cleanup](OPERATIONAL_HYGIENE_20261018.md) remains
unexecuted: the connected interface has no tag-creation or branch-deletion
control and no authenticated Git transport was recovered. The existing atomic
cleanup script and original exact-head plan remain the execution path for a
maintainer with those controls. No repeated permission request is needed.

Physical acquisition/validation, private-provider provisioning, broader SP1 and
platform qualification remain separate gates. The ten-family teaching catalog
does not restore the historical 9,074-description corpus. This patch adds no
scientific kernel, state owner, measurement store or engine-to-engine call.
