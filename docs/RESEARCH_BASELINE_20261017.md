# Research baseline candidate — close-out evidence

Current hosted and branch-cleanup disposition: [operational hygiene audit](OPERATIONAL_HYGIENE_20261018.md). The later user follow-up authorized cleanup; live execution remains blocked by unavailable tag/delete transport.

This historical ledger records the earlier frozen candidate. The subsequent [provider integration audit](PROVIDER_INTEGRATION_20261018.md) records the implementation merged through PR #245 and its separate validation.

Selected immutable code: `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`. Source tree:
`862785f15628cb3d1c2ec0c620e357dbd1ddb876`. The candidate is retained on
`release/research-baseline-20261017`; [PR #244](https://github.com/notationsystems/Notations-Control-Protocol/pull/244)
merged that selected code into main. This follow-up retains the final evidence-only documentation. Evidence was
collected on 2026-10-17/18 UTC for the 2026-10-17 work order.

Main base `cb97dbbee70fc487d80b1311b107e150eec667d0` includes numerical #240
and operational #242. Bounded BIM #232 is integrated into this candidate through
existing Session/AgentHost/MCP operations, retaining CSE tree
`f91c982b580ad4f16a27ea149eedf0e338f6d764`. #231's console is already in main.
The console and MCP share the same operation implementation and evidence.

This is a **research baseline candidate with verified local software checks**.
The unchanged release-policy evaluation is **blocked**: required hosted jobs
remain queued/pending and have no retained artifacts. It is not a qualified
release, independent-human acceptance, physical validation or authorization to
actuate equipment. The existing private and platform gates remain required.

## Executed evidence

| Gate | Selected-code result |
|---|---|
| Isolated regression sweep | **514 modules; 19,155 passed cases; 1,170 explicit skips; zero failures/errors** |
| Original source-history and promotion suites | **176 passed; zero skips**; also 132 and 44 passed independently in the sweep |
| Retained-source audit | All **21 modules** pass the unchanged integrity and ancestry verifier |
| Installed NCP contract | **531 passed; zero skips** outside source imports |
| Installed numerical/BIM/operational increments | **209 passed; zero skips** |
| Installed native calibrated-window/residual operational checks | **2 passed; zero skips**, with exact original provider pins |
| Wheel/source/installation identity | All **722 package files** byte-match selected Git source, wheel and installed copy |
| Three original operator precursors | All three pass **22 checks each**, including correction, preserved history and fresh replay |
| Operational transport | Installed stdio J1–J5, modern/legacy reconnect/retry, plus separate authenticated HTTP worker and interrupted-claim recovery pass |
| Existing console | **6 Python, 3 DOM and 9 actual Chromium checks** pass; no browser page errors |
| Installed quickstart | Existing operator gate passes 26 commands/750 checks; first-use core receipt completes with retained diagnostic comparison states |
| Required public CI metadata | Collected at the exact SHA; **blocked queued/pending**, no artifacts; offline snapshot evaluation only |

These counts overlap and must not be added. Explicit skips are retained by
reason in the evidence package and do not qualify optional providers. The
first-use receipt preserves intentional FAIL/INDETERMINATE comparisons rather
than relabelling them as successful scientific agreement.

Wheel SHA-256: `40c6b0410fd6f28f03023cf292553c10fd293b767d66743310616b8b6306ca9e`.
The dated [candidate attestation](../validation/research-baseline-20261017/candidate.json),
[full module summary](../validation/research-baseline-20261017/consolidated-summary.json),
compressed logs/JUnit and all three operator journey outputs retain the checks.
The downloadable evidence bundle additionally contains the exact wheel and
full installed quickstart outputs. Browser screenshots and credentials are omitted.

## Corrections and provenance

The first combined sweep caught a missing `fail-fast: false` in #232's CI matrix.
Installed NCP checks caught a missing operational command review record and its
outdated command count. Both were fixed before selecting the SHA above; original
failed/interrupted attempts are retained separately. Installed pytest runs
allow existing sibling test fixtures while disabling project-source imports.
The packaged `ciw` origin is verified under the installed environment.

The local repository retains all 1,975 exact selected-ancestry commit objects,
the complete current tree including ten previously unavailable historical
artifacts, and exact required trees/blobs for 29 selected native roots. Original
SHA identities and pins are verified; source-integrity gates were not weakened.
Unselected historical file-tree closure was not materialized locally. The hosted
full-clone workflow remains queued; the actual original history modules have
now passed locally using the exact required source closure.

## PRs, branches and remaining owners

The original 82 PRs have all been dispositioned: **79 superseded PRs were
closed**, #240 and #242 merged during close-out, and #139 subsequently merged
outside this audit's deferred-transport disposition. Integration PR #244 also
merged selected code `312a37b` at `f5269e1278e56772d400dc87ead3ae5bf461d7bd`.
No PRs were open at the final pre-publication observation; this evidence-only
follow-up is a new review. The
[closure receipts](../validation/research-baseline-20261017/pr-closure-receipts.json)
and [dispositions](../validation/research-baseline-20261017/pr-dispositions.json)
record the actual mutations, without asserting that all scopes are qualified.

The **282-head branch inventory** predates the protected evidence follow-up branch; no original branch has been deleted. The
[cleanup plan](../validation/research-baseline-20261017/branch-cleanup-plan.json)
pins 79 reconciled PR heads and 156 additional exact ancestors: **235 branches**
eligible for archival/deletion **after confirmation**, as Task 4 expressly
requires. Preserve archive tags and recheck each head before deletion; advanced
heads must refuse cleanup. The eight inventoried current/release/transport branches remain retained, alongside the new evidence branch;
39 other heads are explicitly preserved for individual scope review. No original
branch has been archived or deleted. The available GitHub interface does not
expose tag creation or branch deletion; the maintainer's authorized Git/branch
controls are required for the approved cleanup.

Codex owns the completed integration, local checks and evidence. The repository
maintainer owns follow-up review, branch-cleanup confirmation and the 39-head scope
review. The maintainer/runner administrator owns queued hosted jobs. Trusted
provider operators own restricted credentials and missing private qualification.
Native Windows, container/hardware environments, acquired-data campaigns,
SP1 beyond the registered heat profile, physical validation and remote multi-user
operation retain their original owners and evidence requirements.

The selected tree has **156 workflow files and 192 top-level jobs**, including
14 required release workflows and 13 referring to the private-provider token.
There are no exact semantic duplicates ignoring display names. A total of 106
test modules appear explicitly in multiple workflows; differing platforms,
permissions, provider and installed-package purposes require review before
consolidation. The approximate 183 registered workflows reported separately
could not be verified through the available collection endpoint. See
[risks and owners](BASELINE_RISKS_20261018.md), the workflow/branch inventories,
and [the short first-use path](BASELINE_START_HERE.md).

The optional [connective-tissue design note](CONNECTIVE_TISSUE_READINESS_20261018.md)
adds no implementation or grants. Physical equipment, OpenUSD reload, learning
interfaces, new kernel families and general sensor fusion remain later work.

## Main changed during the audit

The [final repository observation](../validation/research-baseline-20261017/repository-state-refresh.json)
records main `484fea941999caeeca132fc33f622e00e7b8a24b`. Relative to the selected
tested SHA it adds nine native-history transport files, including one guarded
publication workflow and three bundle pieces. It changes no packaged source,
existing tests, runtime pin or 21-module import manifest. Main therefore contains
21 imported modules; the transport's separate 23-module target is not imported
or qualified merely because the transport PR was merged.

The selected workflow inventory remains **156 files / 192 jobs**. The observed
main tree has **157 files / 193 jobs**, adding the transport publication job.
That job retains historical repository/ref restrictions and a separately scoped
publication credential. This audit neither provisions that credential nor runs
the restore/publish scripts, changes native refs, or retires source repositories.
Its guard and publication/retirement obligations remain a named maintainer gate.
The follow-up evidence commit is based on the observed main and preserves these
new files; all numerical/test results above still belong only to `312a37b`.

## Work-order and related-repository audit

The [task-by-task audit](../validation/research-baseline-20261017/work-order-audit.json)
records completed integration, local checks, evidence and documentation. Overall
acceptance remains open for required same-SHA hosted CI and confirmed branch
count reduction. The required runner/provider/platform roles are named above.

The supplied related-repository map matches all **21 imported paths, source
repository declarations and licences**, plus the **26 surrounding-system catalog
entries**. The [provider boundary audit](LATENT_PROVIDER_SURFACE_20261018.md)
and [exact JSON inventory](../validation/research-baseline-20261017/provider-status.json)
retain source/import/runtime identities independently. The core wheel does not
activate all imported providers. The two npm modules retain null Python runtime
pins, SCR retains its source-only build boundary, and profile-specific current
and historical revisions remain distinct. No licence, native pin or permission
has changed during this audit.

## Original PR disposition ledger

The historical integration/exclusion rationale is preserved below. Actual
closure/merge state is in the receipts linked above.

| PR | Pinned head | Disposition | Rationale |
|---|---|---|---|
| [#242](https://github.com/notationsystems/Notations-Control-Protocol/pull/242) | `3c343bf9aae3c84a096671d553381c28d96f8108` | already merged | Merged on main during this close-out; actual merged main is the candidate base. |
| [#240](https://github.com/notationsystems/Notations-Control-Protocol/pull/240) | `b115a5ecce8fe3e9eb28003fbd83a12cbf11cd49` | already merged | Merged on main during this close-out; actual merged main is the candidate base. |
| [#232](https://github.com/notationsystems/Notations-Control-Protocol/pull/232) | `585bfcca43122227e4355e473c9c7fa9742de2da` | close superseded | Bounded BIM clearance, sampled metrology contracts, retained CSE execution and explicit local MCP instrument are integrated in this candidate. Historical task scratchpads are replaced by the current work order. Later metrology directions remain separate scope. |
| [#228](https://github.com/notationsystems/Notations-Control-Protocol/pull/228) | `75bad7a8a6b5770c2d68c866f7b035c723c88ad7` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#227](https://github.com/notationsystems/Notations-Control-Protocol/pull/227) | `751d25cdc3b5210fd115ab92a41cb4330e429573` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#226](https://github.com/notationsystems/Notations-Control-Protocol/pull/226) | `721b45bdaee33d9241a7deb90f9e9b0399c0d75e` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#225](https://github.com/notationsystems/Notations-Control-Protocol/pull/225) | `5d07803ceba4c9e280bdedeaecc106451a2e3408` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#224](https://github.com/notationsystems/Notations-Control-Protocol/pull/224) | `d242abd94f55dc415f1919a22dcddca337fa9485` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#223](https://github.com/notationsystems/Notations-Control-Protocol/pull/223) | `1ed585695c887601f936f2efc58cd0d59253f426` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#222](https://github.com/notationsystems/Notations-Control-Protocol/pull/222) | `fe558935452f7de699c79b7e35003e08b56b497e` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#221](https://github.com/notationsystems/Notations-Control-Protocol/pull/221) | `70c515ba97b1b39693d9446626bf7879800efe03` | close superseded | Reconciled with hardened corpus and MCP integration in #223–228. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#219](https://github.com/notationsystems/Notations-Control-Protocol/pull/219) | `26ffb9c06dec36c2357300b581fff7f415a1d1a0` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#218](https://github.com/notationsystems/Notations-Control-Protocol/pull/218) | `e6f17ff7152c76095d67128b46b107795eeaf2e9` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#217](https://github.com/notationsystems/Notations-Control-Protocol/pull/217) | `7485ef056e07afc9fe08e8d0a900f7f24de34cd8` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#216](https://github.com/notationsystems/Notations-Control-Protocol/pull/216) | `3a3167e1b1a8b87dbb896510c0f9cd4c19147665` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#215](https://github.com/notationsystems/Notations-Control-Protocol/pull/215) | `fe3626da05cbb4508c12c8d7d4f2e8a4cfe5bb89` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#214](https://github.com/notationsystems/Notations-Control-Protocol/pull/214) | `827cbed62b02f1a001869b0fa70daabd6491efcc` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#213](https://github.com/notationsystems/Notations-Control-Protocol/pull/213) | `6477059eb24c982017953d4f619fc6dca142cc9d` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#212](https://github.com/notationsystems/Notations-Control-Protocol/pull/212) | `801b25d453eaeda04910339bef1c4713026ba070` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#211](https://github.com/notationsystems/Notations-Control-Protocol/pull/211) | `28644a197f82ca1c5169fd210516a2c9e8aa6dab` | close superseded | Reconciled with the pinned reference integration in #226. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#210](https://github.com/notationsystems/Notations-Control-Protocol/pull/210) | `0066a4733645ff39289232075ea31af1e0e8e47e` | close superseded | Reconciled with the later GaugeLink evidence/refit integration in #226. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#209](https://github.com/notationsystems/Notations-Control-Protocol/pull/209) | `128436b99bb45cb0a7904de1df6dab012173952d` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#208](https://github.com/notationsystems/Notations-Control-Protocol/pull/208) | `be20bd2a953ba296a90d009d19d542890948cb5b` | close superseded | Reconciled with the retained Link/device integration in #226. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#207](https://github.com/notationsystems/Notations-Control-Protocol/pull/207) | `80d3d6d339fe32c2da367d8c63e00b559a8c6bd2` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#206](https://github.com/notationsystems/Notations-Control-Protocol/pull/206) | `1be80e0ae3085f684b8b6ac6c39bfb4b20ee2ae9` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#205](https://github.com/notationsystems/Notations-Control-Protocol/pull/205) | `99137e0ddea057eacb9e0c934d9f249d277f432e` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#204](https://github.com/notationsystems/Notations-Control-Protocol/pull/204) | `03bb19e3c6dac6c30ae0f1ee684a8f9a96611a70` | close superseded | Already in the #228 ancestry; retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#203](https://github.com/notationsystems/Notations-Control-Protocol/pull/203) | `f2126a6f9d2627cbf823577b6473080c444234e6` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#201](https://github.com/notationsystems/Notations-Control-Protocol/pull/201) | `2e26200d6b70859fdfc07e64bf99c7cbbb8dbeac` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#200](https://github.com/notationsystems/Notations-Control-Protocol/pull/200) | `cf5342a4e0996bcbe04671572f0c7e6da21c4b7e` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#199](https://github.com/notationsystems/Notations-Control-Protocol/pull/199) | `a7160e8922c6bd0f482fb603b318837f50479d27` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#198](https://github.com/notationsystems/Notations-Control-Protocol/pull/198) | `a03af213aa5df22e58f77a3293b68244441ef5e8` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#197](https://github.com/notationsystems/Notations-Control-Protocol/pull/197) | `8569ac95f234c9334224832df15e91c002bfae20` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#196](https://github.com/notationsystems/Notations-Control-Protocol/pull/196) | `1293b8cefbab7111c962cd48a3e388106e787c4c` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#195](https://github.com/notationsystems/Notations-Control-Protocol/pull/195) | `ef84c048ee4cdef627f8575c89d855adb2fc7121` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#194](https://github.com/notationsystems/Notations-Control-Protocol/pull/194) | `3465f76468b106e6e12366d6c99f2cc920ffe431` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#193](https://github.com/notationsystems/Notations-Control-Protocol/pull/193) | `076eefe96c0d20c055a4df46fbe50a4d5c9bab2d` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#192](https://github.com/notationsystems/Notations-Control-Protocol/pull/192) | `00ea251ef9574148105283443e0f8a3bdf38bf2e` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#191](https://github.com/notationsystems/Notations-Control-Protocol/pull/191) | `49e3d38ee43ede6db9b0ccf46328739e1c80a047` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#190](https://github.com/notationsystems/Notations-Control-Protocol/pull/190) | `0be25f7843ea086aa18e6079ab24074b9c8b22e5` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#189](https://github.com/notationsystems/Notations-Control-Protocol/pull/189) | `b2f712ed583f3042195531228c65948ee893a197` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#188](https://github.com/notationsystems/Notations-Control-Protocol/pull/188) | `2e986b1d6e3037b2bd4947b75e9b20ab75dba57a` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#187](https://github.com/notationsystems/Notations-Control-Protocol/pull/187) | `992fc57f6e8288c26c0cfc075c8ace02707d46cf` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#185](https://github.com/notationsystems/Notations-Control-Protocol/pull/185) | `a7724105b5c3f59cabaef7085c9329bb7dfe1279` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#184](https://github.com/notationsystems/Notations-Control-Protocol/pull/184) | `4265eb2382d694f07fd22e1701c107f992297b42` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#183](https://github.com/notationsystems/Notations-Control-Protocol/pull/183) | `9a3e6c619dedc532fb774a8907eaf1b3784f7799` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#182](https://github.com/notationsystems/Notations-Control-Protocol/pull/182) | `d4924247ae7a8b14d2bd608405b1a2f919b82c83` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#181](https://github.com/notationsystems/Notations-Control-Protocol/pull/181) | `55d73b4ea488c3f88978d07ed41342b46102824a` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#180](https://github.com/notationsystems/Notations-Control-Protocol/pull/180) | `894e4d780fa62811422d5d91428270c1f3c8c2eb` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#179](https://github.com/notationsystems/Notations-Control-Protocol/pull/179) | `4f47714d094966705c7ef94d847a06fe6a1a7d7b` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#178](https://github.com/notationsystems/Notations-Control-Protocol/pull/178) | `fad73e28aab9882fd6878eb7a4ed1e192753fb9d` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#177](https://github.com/notationsystems/Notations-Control-Protocol/pull/177) | `c47295ad9d0abc4a2bbb4030abbc7857e6528bda` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#175](https://github.com/notationsystems/Notations-Control-Protocol/pull/175) | `505bd406e0ab26447c6ac4ac3d13d506a94fa28d` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#174](https://github.com/notationsystems/Notations-Control-Protocol/pull/174) | `3f22b1483bd156f1263d337b271d58bb6cb71ffe` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#173](https://github.com/notationsystems/Notations-Control-Protocol/pull/173) | `367d72f5a2794b0a6ee97224e72e957b3e6862ae` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#172](https://github.com/notationsystems/Notations-Control-Protocol/pull/172) | `5ebccdeb0f890d73aa6e86a142b51a54146c9481` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#171](https://github.com/notationsystems/Notations-Control-Protocol/pull/171) | `0c2fb09d6b285f7b3d08ddc85a5e0d8f04390cf7` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#170](https://github.com/notationsystems/Notations-Control-Protocol/pull/170) | `ba82f41c0f9602ff3422f0b895e45f06b1984f83` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#169](https://github.com/notationsystems/Notations-Control-Protocol/pull/169) | `5405dd488cd5a87d410595e051a8ca2e3205622c` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#166](https://github.com/notationsystems/Notations-Control-Protocol/pull/166) | `334fbbb26702cdc6f3f89e277cd68d81e9ef58b0` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#165](https://github.com/notationsystems/Notations-Control-Protocol/pull/165) | `f9a109f5cc29bb457279403d5905e4d724196f99` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#164](https://github.com/notationsystems/Notations-Control-Protocol/pull/164) | `276d4afdcfe73e22c8e9125407a5849cf61c0b6b` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#163](https://github.com/notationsystems/Notations-Control-Protocol/pull/163) | `5746ab3bb5d7e165278aab593deecafc5962341e` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#162](https://github.com/notationsystems/Notations-Control-Protocol/pull/162) | `fc28a5e50db0dbef20a1aab1fbb7db4bcb1e2a8a` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#161](https://github.com/notationsystems/Notations-Control-Protocol/pull/161) | `4dfd95cd3739551d8e8bef18969c87edcab3ac65` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#151](https://github.com/notationsystems/Notations-Control-Protocol/pull/151) | `0081cbfef3bfda973e460ba265e7ee94d1be8efc` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#148](https://github.com/notationsystems/Notations-Control-Protocol/pull/148) | `5accef9dc4ae94034ebeed31e241a52945292f23` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#146](https://github.com/notationsystems/Notations-Control-Protocol/pull/146) | `ed5c7d4360b8aefa9873b28424cbdc6a19bca49c` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#145](https://github.com/notationsystems/Notations-Control-Protocol/pull/145) | `256cda305557ef127f78b89c1421ccf8c12b6d5f` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#143](https://github.com/notationsystems/Notations-Control-Protocol/pull/143) | `6915d10ce62e5a8da8385d3dac24c9986cb35389` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#142](https://github.com/notationsystems/Notations-Control-Protocol/pull/142) | `ff146529146d8a0f31ac80f41ce592c6b9eed1d4` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#141](https://github.com/notationsystems/Notations-Control-Protocol/pull/141) | `25e9a245a1a341f8c592f3257b787adcdf333bb2` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#139](https://github.com/notationsystems/Notations-Control-Protocol/pull/139) | `7fd822bea1f62257f8e95ffbdb7139c923375310` | deferred by audit; subsequently merged externally | Original transport instructions prohibited merging. Remote merge `484fea941999caeeca132fc33f622e00e7b8a24b` is recorded in the refresh; native publication/retirement remains independently gated. No transport execution or native ref rewrite was performed by this audit. |
| [#114](https://github.com/notationsystems/Notations-Control-Protocol/pull/114) | `2b4cc74a447660941ef072c55e57c1a8dceba5c2` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#100](https://github.com/notationsystems/Notations-Control-Protocol/pull/100) | `2fd8d418d7c9604dc7eb731d1798a4826228f84c` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#99](https://github.com/notationsystems/Notations-Control-Protocol/pull/99) | `74b2fcf3fc7c317ae5a1782ef5eef58bdbc9f451` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#98](https://github.com/notationsystems/Notations-Control-Protocol/pull/98) | `09b4007eae59d4bb0f9183e9fb3794070165c1da` | close superseded | Expansion intent retained through current verification/promotion gates; older permissive CLI implementation not restored. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#78](https://github.com/notationsystems/Notations-Control-Protocol/pull/78) | `48e62565dd25046a14920874556fa585964495a8` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#66](https://github.com/notationsystems/Notations-Control-Protocol/pull/66) | `3100894149779823727b48e0b46e72633f2427a5` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#64](https://github.com/notationsystems/Notations-Control-Protocol/pull/64) | `e584e364e3494202e07a865ee4772846ea6bb828` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#63](https://github.com/notationsystems/Notations-Control-Protocol/pull/63) | `ba48ba82098ef0274bea471775fd9dcd7f9b743b` | close superseded | Reconciled into the combined subsystem; qualification gates retained. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
| [#53](https://github.com/notationsystems/Notations-Control-Protocol/pull/53) | `ded994c711d7c9a8a9e4c99eb758c323365157fb` | close superseded | Current main README retained; stale default-branch acceptance claims replaced with this review scope. Superseded by the merged consolidation and this baseline candidate; original qualification exclusions remain in force. |
