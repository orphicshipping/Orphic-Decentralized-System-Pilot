# Repository folder audit — 18 October 2026 UTC

Audited immutable main **`5bde337174bdfb2add357042aceae2286bb7e56b`**, tree
`de4f941486886bb4c94546118c93915c9928c4bb`, after merged PR #248. The research baseline remains
`312a37ba613a0b57ea027b8d00df5b3b4fe08d69`; this audit does not waive its hosted,
platform, provider or physical gates.

**All 5,355 tracked files and 788 folders were inventoried.** The
[folder register](../validation/repository-folder-audit-20261018/folder-inventory.csv)
contains **789 rows including the repository root**, with owner, purpose,
direct/subtree counts, related findings and integration action. Each file has a
Git object/mode and SHA-256 in the
[compressed immutable snapshot](../validation/repository-folder-audit-20261018/snapshot.json.xz).
Ignored build products, local environments, empty directories and Git's own
object store are outside the committed repository audit.

## Coverage by folder

| Top-level folder | Tracked files | Folders including intermediate paths |
| --- | ---: | ---: |
| `.github/` | 157 | 3 |
| `containers/` | 2 | 2 |
| `delivery/` | 13 | 4 |
| `deploy/` | 2 | 1 |
| `docs/` | 338 | 7 |
| `examples/` | 284 | 104 |
| `experiments/` | 4 | 1 |
| `godot/` | 36 | 3 |
| `instruments/` | 2,779 | 565 |
| `qualification/` | 2 | 1 |
| `release/` | 5 | 1 |
| `requirements/` | 1 | 1 |
| `runtimes/` | 35 | 14 |
| `scripts/` | 160 | 2 |
| `src/` | 724 | 14 |
| `tasks/` | 2 | 2 |
| `tests/` | 524 | 5 |
| `third_party/` | 5 | 3 |
| `tools/` | 62 | 20 |
| `validation/` | 204 | 35 |

The remaining **16 root files** are included in the root inventory row. Folder
subtree counts overlap; sum direct-file counts, not subtree counts. No tracked
folder is omitted, including nested provider dependencies and release templates.

The scan parsed **2,409 Python files without syntax errors**, mapped **666
packaged Python modules**, and checked **79 CLI navigation entries with no
missing module targets**. All **103 modules without direct product imports**
have an explicit [dispatch/API disposition](../validation/repository-folder-audit-20261018/sparse-module-review.csv).
Lazy CLI dispatch, subprocess workers, fixed fluid-profile imports, data-route
strings and standalone module CLIs are not evidence of orphanhood.

## Concrete gaps and eligible integrations

The [33-finding register](../validation/repository-folder-audit-20261018/findings.csv)
and [evidence-backed JSON](../validation/repository-folder-audit-20261018/findings.json)
name existing owners, exact source excerpts or absent targets, prerequisites and
the smallest useful next action. Priority is integration/maintenance urgency,
not a scientific qualification level.

| Finding | Surface | Disposition |
| --- | --- | --- |
| F06 | Bevy retained-render projector has metadata but no Rust target | conditional source required |
| F07 | Teaching-corpus tools lack their input corpus and eight emitters | conditional source required |
| F09 | Atomic ALCHEMI pilot lost its profile-specific native gate entry | conditional implementation required |
| F05 | Unattached retained Godot geometry and presentation components | eligible after baseline hygiene |
| F08 | Pinned glTF qualification tool has no documented/root-CI entry | eligible with explicit prerequisites |
| F11 | Metrology inspection ingress is a tested host utility, not a mounted grant | eligible with explicit host binding |
| F20 | Standalone JuMP design study is eligible for a scoped retained-operation seam | eligible after baseline hygiene |
| F15 | FrameMapper preserves tracked dependencies and Python bytecode | conditional upstream hygiene review |
| F14 | Twenty-one imported providers retain separate execution and qualification pins | conditional qualification required |
| F21 | Bounded OpenUSD SDK reload is implemented; general interchange remains open | existing connection preserved |

The Bevy retained-render folder has Cargo metadata but no Rust target. Its
separate bounded projectile runtime cannot substitute for the missing projector.
The teaching template map resolves only **2 of 10 emitter targets**; the
generator, index, scenario directories, catalog and client catalog tab are
absent. Earlier **9,074** figures remain labelled historical local reports,
not the contents of this tree. Restore exact reviewed source or choose one
bounded existing example; do not manufacture corpus completeness.

The atomic ALCHEMI guide pointed at a script now serving the distinct CPU
`nvalchemi.lj-single-point.v1` contract. The original
`atomic.lj-energy-forces.v1` adapter/contract/worker remain present but its native
gate entry is missing. The guide now records that gap without transferring CPU
evidence or changing either profile.

Several apparent latent files are already connected. The affine temperature
recording bridge is a fixed data-inventory consumer; fluid solvers use a fixed
profile map; the local-frame spatial module has its own documented CLI and
installed gate; the columnar qualification file outside default test discovery
is explicitly selected by its workflow. Bounded **OpenUSD SDK export/reload is
already implemented** for oscillator displacement and local-frame spatial
profiles. The ASCII inspect writer is separate. General scene import and
binary/package paths retain their open gates; another reload implementation
would duplicate existing work.

## Repairs made with this review

- Connected the existing atmospheric reference/measurement-preparation
  installed gate and all 20 atmospheric test modules to its existing
  Linux/Windows lane, including change routing and retained artifacts.
- Repaired the Godot presentation smoke to use the existing retained geometry
  component and required it in the existing live-client gate.
- Added the declared LICENSE to the backend container context/COPY and checked
  its actual rebuilt wheel bytes.
- Repaired stale relative paths, cold-chain Markdown newlines, visualization
  source claims and OpenUSD/ALCHEMI status descriptions. Historical receipts
  remain historical; missing source is not labelled implemented.
- Added a reproducible read-only folder auditor and eight real-Git boundary
  regressions to workflow-contracts, retaining its report in the existing
  artifact. **156 workflow files / 192 jobs remain.**

The source catalog's corrected Bevy boundary is also projected into the existing
packaged discovery JSON; **21 imports / 26 surrounding systems / 19 external
boundaries** remain. No second registry, execution path, result store, provider
pin, numerical kernel or execution grant was introduced.

## Independent sources, generated bytes and retained evidence

All **21** imported source/ancestry/tree and actual clean-byte checks passed on a
fresh exact-source checkout. The
[provider table](../validation/repository-folder-audit-20261018/provider-boundaries.json)
keeps original revisions, licences, build boundaries and runtime declarations.
Nineteen roles already connect to Workbench workflows; Globe and FrameMapper
retain independent npm builds. Installing the core wheel activates none of them.

FrameMapper's import contains **589 tracked dependency files and 15 bytecode
files**. These are inventoried without being loaded. Source hygiene should be
reviewed upstream and reimported at a new exact tree after lock/notice/build
checks; deleting them in place would invalidate the preserved import. The
**27 nested upstream workflow definitions** are source/template material, not
27 additional root Actions jobs.

FlowState data/results, qualification JSON, validation XML/logs, foundry handoff
archives and split source-retirement bundles are intentional retained evidence
or transport. They are not default runtime inputs. No bundle restore,
publication, source retirement, hardware actuation or branch transaction ran.
The 52 equal-byte groups mostly reflect licences, reference/source copies and
package fixtures; equal bytes alone do not justify deletion or identity changes.

## Executed verification and limits

The [verification receipt](../validation/repository-folder-audit-20261018/verification.json)
and [hashed raw archive](../validation/repository-folder-audit-20261018/archive-manifest.json)
retain executed checks and their scope:

| Check | Result |
| --- | --- |
| Immutable folder audit regressions | 8 passed, zero skips |
| Expanded existing atmospheric source lane | 1,617 passed, zero skips |
| Source catalog / optional atomic contract regressions | 107 passed, zero skips |
| Isolated installed catalog / inspection / grant checks | 25 passed, zero skips |
| Existing installed atmospheric reference lifecycle | PASS; intentional tight-policy disagreement preserved |
| Pinned Godot 4.5.2 | 9 presentation assertions plus existing import/live-protocol/channel/adapter/experiment gates passed |
| Exact-source monorepo import audit | 21 passed; source/tree/ancestry/clean bytes |
| Actionlint / event policy | All 156 workflows and unchanged required trigger policy passed |
| Wheel identity / licence / isolated pip check | 724 files match candidate bytes; licence retained; dependencies consistent |

The rebuilt wheel is **`90f2116bd08f8aca557702ffd396ee4895ef711a4eff65081a85922ad96320e4`**.
All **723 other packaged files** match the earlier selected wheel; only
`ciw/integration-sources.json` changes. The
[identity manifest](../validation/repository-folder-audit-20261018/wheel-byte-identity.json)
binds every source byte. Atmospheric numerical execution used the previously
selected wheel, whose unchanged code is attributed separately in the receipt.
The wheel itself is included in the downloadable review bundle, not added to
the source tree.

This is a full **folder/file inventory with static cross-reference and targeted
semantic review**, not a line-by-line proof or complete dynamic reachability
analysis. Markdown checks resolve local file targets, not remote URLs/anchors.
No full source-suite rerun, Docker/Compose run, fresh OpenUSD SDK/ALCHEMI/glTF
native qualification, Windows execution, hardware/physical test or hosted CI
completion is claimed. New workflow definitions require fresh hosted evidence.
Existing source-bound hosted and authorized branch-cleanup blockers remain
those in the [preceding operational ledger](WORKFLOW_LANE_REVIEW_20261018.md).

To repeat the immutable inventory without importing repository/provider code:

```sh
python scripts/audit_repository_folders.py --repository . \
  --revision 5bde337174bdfb2add357042aceae2286bb7e56b --output /tmp/ncp-folder-audit.json
```

The command reads committed Git objects, including symlink bytes, and refuses
to overwrite an existing output. Untracked caches or dirty worktree edits
cannot masquerade as the selected revision.
