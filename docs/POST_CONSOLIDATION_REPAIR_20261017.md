# Post-consolidation repair and qualification record

> Current selected code and final close-out evidence: [research baseline ledger](RESEARCH_BASELINE_20261017.md). The dated observations below remain historical.

Reviewed main: `8a6beb844a4a3c3944637b1e3f386d48d28507d0`.
PRs #229, #230 and #231 are merged. All 79 remaining open PR heads at this
snapshot match the earlier 80-entry consolidation ledger. Open PR state is not
an implementation inventory; the ledger retains explicit exclusions and limits.

## Observed failures and corrections

The hosted consolidated run [39048125063](https://github.com/notationsystems/Notations-Control-Protocol/actions/runs/39048125063)
failed four modules: DSP pipeline contracts (34 failures), monorepo provenance
(36), module promotion (29), and procedural integration (one). These are actual
hosted failures, distinct from the earlier local reconstruction's limitations.

| Failure | Correction | Evidence and boundary |
| --- | --- | --- |
| Imported SCR source revision was outside main ancestry and its declared tree differed from the current subtree | Retain the existing subtree as native source commit `4cbbdd61074d8cf37eba9d560bb83d2865b8e93d`, parented by the original declared import `114060739239cc245a732ac74d16d5c876eaa08e`; update only its import revision/tree fields; retain that source commit as a parent of the repair candidate | Existing verifier and runtime execution pins are unchanged. Native tree `bfd617fd602502aefed02d8b0bac9bc41cf7ff3d` is exactly the subtree already on reviewed main. Full-history qualification must run on the candidate's actual ancestry |
| Consolidated CI installed DSP source without its numerical dependencies | Install the already pinned `numerical-checks` extra | Required DSP cases execute; absence is not converted into a skip |
| A completed/refused graphics request could still hold the operation lock while its HTTP response reached the client | Release operation ownership before sending detached mutation/export responses | Deterministic response-boundary regressions; real busy operations retain their existing 429 refusal |
| Industrial Lab browser polling evaluated string predicates forbidden by CSP | Supply function predicates to Playwright | Original browser check reproduced the CSP failure; real Chromium journey passed after correction with CSP unchanged |
| Windows manufacturing qualification looked beside python.exe for net.exe | Resolve the launcher through the active interpreter's scripts installation scheme | Shared resolver for all three affected installed gates; simulated Windows base/venv layouts and real Linux installed command journeys. No PATH fallback |
| Console had explicitly invoked tests without CI or an actual browser journey | Add Linux/Windows Python/DOM CI and a Linux Chromium journey through the real HTTP adapter and MCP host | Explicit confirmations, admission, synthetic execution and retained resource identity are exercised; remote transport and physical qualification are not inferred |

The repair candidate also runs the consolidated gate on main/tag pushes and
retains the source audit alongside regression artifacts. Provenance is checked
before the broad regression. Original retained reports are historical evidence
and are not rewritten as results for this candidate.

## Local verification

The machine-readable [repair summary](../validation/consolidation-repair/summary.json)
records fresh results and report digests. Counts overlap and must not be added.

- Fourteen affected/adjacent modules: 662 passed. The subsequent final graphics
  change was checked by 315 graphics cases, including its new export regression.
- Compute-runtime source contract checks: 300 passed. These do not execute real
  GROMACS/LAMMPS engines or create device proofs.
- Three manufacturing/data gates each test source and an exact noneditable
  wheel outside the checkout, with no skipped cases. The gates retain native
  command receipts and verify the wheel's installed package bytes.
- Industrial Lab: seven real Chromium checks; no page errors.
- Console: five real Python/HTTP/MCP tests, three DOM tests, and four real
  Chromium journey checks; no browser page errors.
- CI policy tests and policy audit passed. Changed Python files compile.

Local source is reconstructed from GitHub blobs and does not contain complete
native Git history or ten unrelated large/binary historical artifacts. Those
artifacts are preserved in the remote base tree. Local tests cannot establish a
full-history pass; the hosted source audit and both original history modules
remain required. Windows behavior is not qualified by Linux layout fixtures.

## Remaining explicit gates

The [exchange run 39061333988](https://github.com/notationsystems/Notations-Control-Protocol/actions/runs/39061333988)
failed because `CIW_PROVIDER_READ_TOKEN` was unavailable, before provider checkout
or scientific tests. The credential helper's trusted-event restrictions and
fail-closed behavior are unchanged. This repair does not provision a credential,
substitute a provider, or qualify the absent provider lanes.

Installed measurement chains, calibration records, held-out experiments,
hardware commissioning, additional proof profiles, runtime attestations and
remote multi-user service qualification still require their declared evidence.
The local console has no complete job enumeration/drain contract or authenticated
remote transport; those capabilities are not silently enabled by browser tests.
The broader composition/frame/learning roadmap retains its explicit scope.

No deployment, release approval, hardware activation, original-branch deletion
or automatic closure of the historical PR stack is performed by this repair.
