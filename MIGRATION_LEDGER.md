# ODSP legacy conversion: migration ledger

PR [#260](https://github.com/orphicsystems/Orphic-Decentralized-Systems-Protocol/pull/260)
publishes branch `odsp-legacy-conversion`. On 2026-11-02 the remote head was
`b8eec0949ebd1a9a6dac4e9ef242b0db34dd4f0c` and main was
`26a437ef1ec664069a948ab9b3f546ac7c3e1e35`; their merge base was
`24f6ef074c76da0ddf2a405001c00020061e98fc`. Main already includes PR #259.
The branch copied that tree through single-parent commits without integrating
its ancestry. Local repair merge `41a3478d85e5235b65383fdbdc4af69c8b02222c`
has the PR head and current main as its two parents, with the indexes regenerated
from the complete checkout. This repair has not been pushed.

The repository is private (GitHub repository metadata, checked 2026-11-02).
Earlier snapshot counts, test reports, local commit IDs and statements attributed
to the Chairman or Counsel below are retained conversion records from PR #260,
not independent verification by this repair. The original record cites
`ODSP_LEGACY_CONVERSION_DIRECTIVE.md`; that directive and the underlying decision
transcripts were not supplied for this review. Nothing here creates rights,
records a public release, or authorizes execution.

## 1. Original partial-snapshot inventory

This historical inventory describes the conversion author's snapshot at 24f6ef0.
The ancestry repair contains all 6,852 tracked paths: 6,842 from current main
plus the conversion's ten additions. The subsequent response-policy and
claim-recovery regressions add two test files, bringing the integrated repair
to 6,854 tracked paths. No tracked path was removed.

| Measure | Count |
|---|---|
| Upstream blobs at 24f6ef0 | 6822 |
| Upstream paths in this snapshot | 2708 (2669 byte-identical to upstream, blob-SHA verified; 39 changed by this conversion) |
| Upstream blobs NOT in this snapshot | 4114 |
| Files added by this conversion | 10: this ledger, THIRD_PARTY.md, 5 tests, tests/support/coordinator_child.py and its generated MCP.md + README.md |

Missing by top-level folder: instruments 3058, validation 306, examples 227, .github 163, docs 145,
tools 73, runtimes 65, godot 57, delivery 20.
`src/`, `mcp/`, `tests/`, `scripts/`, top-level files, `third_party/` and licence/notice files are complete.

The original snapshot reportedly lacked shared Git history. That historical
replay instruction does not apply to this repair, which merges the actual
published PR head with main and preserves both ancestries.

## 2. Per-capability matrix

| Capability | Disposition | Callers | Replacement / evidence tests | Removal condition |
|---|---|---|---|---|
| Request policy (tool name, 64 KiB budget, pin/checkout refusal, identity refusal, input schema) | **rewritten**: one `ciw.services.admit` | `services.invoke` (SDK), `ControlHost.call` (MCP control host, stdio server), `coordinator.submit` (inner op) | test_shared_policy_boundary.py (6), test_mcp_sdk_parity_process.py | Inline copies removed in the same commit (4f61eea) |
| Coordinator liveness | **rewritten**: kernel file leases (`idem-*.lease`) instead of PID probes | `coordinator.submit/recall/recover` | test_coordinator_multiprocess.py (crash, PID reuse, timeout, reconnect) | PID liveness deleted in ef86a6a |
| Lost-worker settlement | **rewritten**: settled under the owner transaction lock | recall, recover | crash + 4 concurrent recoverers | — |
| Exclusive resources | **rewritten**: never-unlinked flock files, bounded wait | submit(exclusive=) | exclusive not stolen / wait timeout / freed on crash; test_transformation_ledger busy-resource | pid-file holds deleted in ef86a6a |
| Handoff / claim | **rewritten**: durable grant-specific intent fixes artifact identity before publication; receipt replay does not recreate released artifacts | handoff, claim | test_claim_recovery.py: six real process-exit boundaries, concurrent reconnect, malformed receipt and partial-write refusals; Linux local filesystem only | — |
| Idempotency keys | **rewritten**: conflicting reuse refused (`idempotency_key_conflict`) | submit | key-conflict test, coordinator journey | — |
| Retained release | **rewritten**: holds owner transaction across processes | Store.release | concurrent release x5 | — |
| Cancellation | **rewritten**: KeyboardInterrupt/SystemExit during domain execution recorded `cancelled` before lease release | submit | hang-cancel test; interruption during exclusive acquisition remains a separate gap | — |
| sequence_kinetics numerics | **retained_and_verified** | ncp_domain_run sequence | test_sequence_kinetics_numerical.py (13; mutation-checked) | — |
| electronics_repair brand | **rewritten**: ODSP pixel wordmark | web UI | render-checks.json (8 screens, desktop + mobile) | Legacy "N∶" glyph deleted in f390414 |
| tooling_explorer brand overlap | **rewritten** | web UI | render-checks.json | 7f3b7b9 |
| Workbench workflows (calibrated-observable, identified-design, telemetry, …) | **blocked** | ncp_workbench_run | — | Need host-selected native runtime pins: INCOMPLETE |
| Native control profiles | **blocked (pre-existing upstream)** | test_native_control_profiles | bim-estimator tree is now complete; the test still fails on the upstream `NATIVE_TREE` pin (computed 9b241ddd… ≠ pinned f91c982b…), identically on the 24f6ef0 baseline | Upstream must re-pin; not changed here |
| Operator docs (README.md, docs/AGENT_OPERATIONS.md, docs/MCP_CONTROL.md) | **rewritten**: describe the shared control-tool `services.admit` policy, separate ODRP `KnowledgeHost` validation, identity refusal, file-lease liveness (PID diagnostic only), `idempotency_key_conflict`, `exclusive_resource_busy` | readers, agents | checked against src/ciw/services.py and coordinator.py | — |
| ODRP reference profile (PR #259) | **retained**: separate read-only `KnowledgeHost` validation before control admission | `ncp_system_status`, `ncp_knowledge_*`, `ncp_evidence_*` | test_reference_profile.py; reference dispatch and host bindings preserved from main | — |
| agent (net_*) and semantic surfaces | **retained** (own tool families, own `validate_arguments`) | ciw.net agent/semantic serve | test_mcp_sdk.py (live stdio) | Not migrated to `admit`: different tool set |

## 3. Supersession map

| Superseded | Superseded by |
|---|---|
| Inline checks in `services.invoke` | `services.admit` |
| Budget and schema checks in `ControlHost.call` | `services.admit` (also adds pin and identity refusal to the host) |
| `coordinator._checked_arguments` (identity + schema only; admitted oversized requests and consumed the key) | `services.admit("ncp_domain_run", …)` before admission |
| `coordinator._alive` PID liveness | `_Lease` file leases |
| `exclusive/<name>` pid files | `_hold_exclusive` / `_release_exclusive` |
| electronics_repair `<b>N∶</b>` | `svg.odsp-wordmark` |

## 4. Removals (git rm)

None. No path qualified: the upstream transformation ledger lists 0 removed/replaced/relocated
paths, and every superseded mechanism above was a code path inside a retained file, deleted in
the commit that replaced it. No file was removed without a verified replacement.

## 5. Consumer journeys (real input)

Input: the binary 3-block law of the bits of `LICENSE` (circular windows, declared distribution,
`source_ref` = file sha256). test_consumer_journeys.py:
deterministic software (SDK), agent (ControlHost), LLM (MCP stdio, separate process),
coordinator (submit, replay, conflict refusal, recall, handoff, claim, duplicate delivery, intruder).
Each checks agreement with the SDK; the SDK value is checked against an independent
H(3-blocks) − H(2-blocks) computation from raw counts.

## 6. Platform statement

Previously reported software-test environments: Linux x86_64, CPython 3.13.5 (dev venv, editable) and CPython 3.11.17
(clean venv, wheel install), numpy 2.4.3, websockets 16.0, mcp 2.3.0. Cross-process locking uses
`fcntl.flock` on POSIX and `msvcrt.locking` on Windows; **the Windows path was not run**.
macOS was not run. Locks are advisory and assume a local filesystem (not NFS).
The integrated repair explicitly refuses retained reads when `O_NOFOLLOW` is
unavailable. Lock support on Windows does not establish safe-read support or
Windows qualification.

## 7. Historical conversion evidence matrix

These are earlier reported results at the revisions named below, not results
for the repaired head. The full suite has not been rerun by this repair.

| Gate | Command | Result |
|---|---|---|
| Full local suite (dev venv, py3.13), tree just before acc69db | `.venv/bin/python -m pytest -q -p no:cacheprovider tests --junitxml …` | 18175 passed, 149 failed, 81 errors, 1315 skipped (34m38s). Same command on the 24f6ef0 baseline tree: 18141 passed, 149 failed, 81 errors. 229 failures/errors are common to both; 1 branch-only (repo-URL test, fixed in acc69db, 19 passed); 1 base-only (hard-coded /usr/bin/python3.11, fixed on this branch). Not rerun at acc69db or later (no src/ change since) |
| Ledger and folder contracts | `scripts/write_transformation_ledger.py`, `scripts/write_mcp_folder_contracts.py` with `git ls-files` = upstream tree + additions (68ba4e2) | 6832 rows = upstream 6822 + 10 added; `document(publish tree) == ledger`; no instruments/ or runtimes/ file changed |
| Coordinator multi-process | `pytest tests/test_coordinator_multiprocess.py` | 9 passed (6 failed against pre-fix code) |
| Shared policy boundary | `pytest tests/test_shared_policy_boundary.py` | 6 passed (old coordinator wrote job + lease for an oversized request) |
| Numerical | `pytest tests/test_sequence_kinetics_numerical.py` | 13 passed; 2 injected mutations caught |
| MCP vs SDK parity (separate process) | `pytest tests/test_mcp_sdk_parity_process.py` | 1 passed |
| Consumer journeys | `pytest tests/test_consumer_journeys.py` | 5 passed |
| Wheel | `pip wheel --no-deps --no-build-isolation .` | computational_instrumentation_workbench-0.1.0-py3-none-any.whl, sha256 `7022a848feb302ecbd0ba41b36104017d428e6a12ce8666355512501272c7b74` |
| Clean-venv install (py3.11.17) | `pip install wheel[mcp-control,dev] jsonschema`, run copied tests outside the repo | 40 passed (mcp_sdk 6, parity 1, numerical 13, policy 6, coordinator 9, journeys 5) |
| Interface render | `node render/wordmark.cjs` | 8 screens x 2 viewports: no overflow, no header or brand overlaps |
| Hosted CI | — | Not run (local only) |

## 8. Historical failures and INCOMPLETE stages

- Previously, test_transformation_ledger::test_every_tracked_path_has_one_disposition and
  test_mcp_project_map (x2): compare against the local `git ls-files`; 4114 upstream paths are absent
  from this snapshot, so they fail here. The ledger, document map, folder index and folder contracts were
  regenerated in 68ba4e2 for the publish tree (upstream + additions). These checks now pass
  in the complete repair checkout after regeneration.
  `mcp/project-map.json` (hand-maintained, top-level folders only) needed no change.
- test_native_control_profiles and test_metrology_clearance (23): upstream `NATIVE_TREE` pin mismatch,
  identical on the 24f6ef0 baseline. Pre-existing; not changed here.
- test_capabilities::test_registered_operations_match_the_default_registry: upstream inconsistency
  (`workbench.fold.v1` is catalogued as a registered operation but `default_registry()` does not register
  it); identical on the baseline. Not changed here.
- 229 failures/errors common to branch and baseline; classified in §8a.
- Workbench workflows that need native runtimes: INCOMPLETE.

### 8a. Classification of the 229 common failures (branch = baseline)

| Cause | Count | Tests |
|---|---|---|
| Upstream files absent from the snapshot | 127 | monorepo 60 (instrument pyprojects; also needs upstream git history), module_update 37, teaching_corpus 16, mcp_project_map 2, molecular_reference_data 2, capabilities docs 1 (now passes: docs placed), laboratory_profiles 1 (docs/REACTION_BENCHMARK.md), .github workflows 3 (ci_recovery collection, builtin_numerical_mcp, public_provider_gate), data_integration_matrix 1, exchange 1, physical_measurement_profiles 1, release_evidence 1, transformation_ledger 1 |
| Native runtime or native source unavailable | 74 | oscillator_kernel 49 (runtimes/), data_rank_bridge 19, norris_affine 4, missing_surfaces 1 (Julia), native_interop 1 |
| Pre-existing upstream pin / code inconsistency | 25 | metrology_clearance 23 + native_control_profiles 1 (NATIVE_TREE pin), capabilities registry 1 (`workbench.fold.v1`) |
| Environment | 3 | PyYAML absent from the dev venv at run time (1; passes now), foundry_childhood cleanup-error x2 (pass on rerun with the py3.11 venv) |
| Regressions | 0 | — |

Total 229. Classified by test module and by the failure message; the rerun spot-checks were run on 2026-11-01 at about 22:30 EST.

## 9. Licence audit (third_party/ and PLSR)

The following licence inventory and decisions were reported by the conversion
author. Notices are retained; the repair does not independently verify title,
legal compatibility, decision authority or distribution permissions.

| Item | Licence | Flag |
|---|---|---|
| Project | AGPL-3.0-or-later | — |
| third_party/chemical-kinetics | Apache-2.0 (Google 2025), NOTICE.md present | Keep NOTICE |
| third_party/reference-data (PTB MH44 hydrophone data via Ma-Weber/Tutorial-Deconvolution) | GPL-3.0 (upstream repository notice; data grant not established) | Retained in this private repository. The conversion author reported a Chairman decision and contrary Counsel advice; the underlying evidence and any public publication were not verified (see §11). |
| integration-sources: real-time-globe, frame-mapper | GPL-3.0 | **Kept by Chairman decision, Nov 1 2026; flagged to Counsel** (see §10) |
| integration-sources: state-recompiler | AGPL-3.0 | OK |
| frame-mapper node_modules | MIT | OK |
| instruments (many, e.g. inference/faultsense) | MPL-2.0 | **Accepted by Chairman decision, Nov 1 2026** (see §10) |
| bim-estimator / sensitivity | MIT | OK |
| compute-runtime, temperature-recording | Apache-2.0 | OK |
| preservation-experiments (buildingSMART) | CC-BY-4.0 | Attribution required |
| FEASST (NIST), NIST Dataplot | NIST licence / public domain | OK |
| PLSR (Parameterized-Lyapunov-Stability-Runtime @19ea696) | MIT; deps numpy>=2.0, jsonschema>=4.23,<5 | OK |
| src SPDX headers | 25 AGPL-3.0-or-later, 1 Apache-2.0 | Apache text in hardware_bench.py, temperature_recording_sources.py, sequence_kinetics.py |
| docs/licensing/rights-register.json | Owner declaration: the Chairman's statement of Nov 1 2026 (Orphic Systems); 32 per-item third-party rows; `global_legal_clearance` stays false | Recorded (see §11) |

No new dependencies were added.

## 10. Reported Chairman decisions (Nov 1 2026)

The conversion author attributed the following decisions to the Chairman and
reported separate local commits. Those decision transcripts and local commit IDs
were not independently verified. The conversion is now published in PR #260;
these records do not establish a third-party licence grant or legal clearance.

| # | Decision | Applied in |
|---|---|---|
| 1 | Use the ODSP pixel wordmark in `procedural_workbench`; re-render desktop and mobile. | `e3582db` (screenshots kept outside the repo, in the interface-candidate `screenshots/wordmark-final/`) |
| 2 | **Keep the GPL-3.0 items** (`instruments/representation/frame-mapper`, `instruments/views/real-time-globe`). Kept by the Chairman and **flagged to Counsel**. GPL-3.0 code may be combined with AGPL-3.0 code (GPL-3.0 s.13 / AGPL-3.0 s.13); each tree keeps its own GPL-3.0 `LICENSE` and the combined work's obligations still need Counsel's review. | this commit |
| 3 | **MPL-2.0 instruments are accepted.** MPL-2.0 is file-level copyleft: each MPL file keeps its own notice and stays MPL-2.0. AGPL-3.0 is an MPL "Secondary License" unless a file carries the Exhibit B "Incompatible With Secondary Licenses" notice (checked separately; see §11). | this commit |
| 4 | Point the `pyproject.toml` project URLs at `https://github.com/orphicsystems/Orphic-Decentralized-Systems-Protocol`. | `a18fe95` |

## 11. Reported Counsel statements and owner declaration (Nov 1 2026)

**Reported owner declaration.** The conversion author attributed this statement
to the Chairman on Nov 1 2026: *"Orphic Systems holds the copyright in the original
ODSP code and can license it AGPL-3.0-or-later"*. The source transcript and authority
were not independently verified for this repair.
- It is recorded as `owner_declaration` in `docs/licensing/rights-register.json`.
- The earlier Notation Systems Inc. declaration is kept under `prior_owner_declarations`.
- Independent title verification is still `false`. No assignment is created by the record.

| Item | Result | Commit |
|---|---|---|
| (a) Third-party licence table | `THIRD_PARTY.md` at the repo root covers all of the following, and every file keeps its own notice untouched. **GPL-3.0:** frame-mapper (plus its vendored npm packages), real-time-globe and the PTB data. **MPL-2.0:** 10 instrument trees. **Apache-2.0:** chemical-kinetics (with `NOTICE.md` shipped unchanged), compute-runtime, estimator-bench, the smartctl fixture and APMonitor TCLab. **MIT:** 7 instrument trees and three.js. **CC-BY-4.0:** the buildingSMART IFC files, each with creator, title, source link, licence link and changes made (content unmodified, renamed only). **NIST:** PONTIUS and FEASST. | `1ca8cdc` |
| (b) MPL Exhibit B check | **No hits in any source file.** See the method and limits below. | — |
| (c) PTB removal | **Cancelled by the Chairman.** No commit was made and nothing was dropped. `/workspace/odsp/internal/ptb-hydrophone` was verified absent. | — |
| (d) Per-item clearance rows | `rights-register.json` `third_party_clearance` has 32 rows, one per third-party item, using the `enrollment_fields` plus `git_blob`. `content_sha256` is computed locally where the file was fetched; otherwise the declared value is used, or null with a note. | `82b73fd` |
| PTB hydrophone data | It remains in this private repository with its own `THIRD_PARTY.md` entry and register row (`tp-ptb-mh44-hydrophone`). GPL-3.0 identifies the upstream repository notice; the data grant remains unestablished. The reported Chairman/Counsel statements and any public publication were not independently verified. The conversion author reported a matching `content_sha256` (`598b7ac5…`). | reported `1ca8cdc`, `82b73fd` |

**(b) Method and limits.** GitHub code search was run on `main` (24f6ef0) with the query `"Incompatible With Secondary Licenses" repo:orphicsystems/Orphic-Decentralized-Systems-Protocol`. It returned 11 results (`incomplete_results: false`):
- the 10 MPL-2.0 `LICENSE` files, where the phrase is the licence's own definition (§1.5, §3.3, §10.4) and the Exhibit B template;
- `instruments/mathematics/surface/tests/test_licensing.py`, which only asserts that the licence text contains the Exhibit B heading.

The conversion author reported matching fetched copies and a second search for
`"Mozilla Public License"`. Those scoped search results do not independently
establish legal compatibility or permission to combine or distribute the files.

Limit: GitHub code search does not index files larger than 384 KB, nor binary or vendored files. The 402 MPL files were not all fetched and grepped locally. Only three MPL-tree files exceed 384 KB, so they were outside the search: `surface/figures/jacobi-testbed-v1.png`, `surface/figures/surfaces-testbed-v1.png` and the generated `surface/validation/report-v2-surfaces.json`. None of them is source code.


Local confirmation (2026-11-01, about 22:30 EST): `git grep -I -l "Incompatible With Secondary Licenses" HEAD` over this
snapshot (2718 tracked files, including every instrument tree it holds) hits only the same 10 MPL `LICENSE` files and
`surface/tests/test_licensing.py`, plus this ledger and `docs/licensing/rights-register.json`, which quote the phrase.
Files not in the snapshot rely on the code search above.
