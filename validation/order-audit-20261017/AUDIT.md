# NCP work-order audit — 17 October 2026

**Overall result: incomplete. PR #242 is on hold and has been returned to draft.** The earlier operational acceptance report overstates closeout. Its passing test results remain historical evidence; this audit supersedes its all-scenarios PASS interpretation and ready-for-review conclusion. No runtime fixes are included in this audit commit.

## Scope and identities

The audit reconciled all **83 open PRs** with the existing consolidation ledger, reviewed the recent metrology/numerical/operational orders and their declared limits, and performed focused source inspection and defect reproduction on the operational implementation. It is not a fresh execution or line-by-line code review of every historical subsystem. Open PRs are review candidates, not necessarily 83 independent unfinished assignments. The inventory covers repository-visible orders and the supplied operational work order; it cannot certify unseen external assignments.

- Main observed through GitHub: `62577c96a280287c5df5d54783be00df1ab7c0de`.
- Operational work order: `docs/work-orders/WO-NCP-OE-MCP-20261017.md`, revision 1.
- Audited implementation: `2370567f39fd537d12136b910724fae0c97f6c30`; evidence head: `b561f54afc237e184b38f9f88d413f409b52d089`.
- Audited wheel SHA-256: `4c368723e07892893acd72f3cc9cee779156c08cb8c4ae0dc837b5485e40fdf6`.
- Inventory snapshot: approximately 21:35–21:38 UTC. PR #242 was converted to draft at 21:37 UTC. `open-orders.json` preserves its earlier ready-state snapshot rather than silently rewriting history.

## Confirmed operational defects

| Priority | Finding and evidence | Required correction |
| --- | --- | --- |
| High | **Collector can lose an operator stop.** `Collector._save` rereads the latest revision and writes a value assembled before that read. A stop arriving between the loop's stop read and save is overwritten. A deterministic interleaving after the first retained segment collected all three segments, ended `LIMIT_CLOSED`, and reset `stop_requested` to false. | Preserve stop ownership atomically. Use a revision paired with the read, handle conflicts, and merge operator-owned state without losing requests. Cover stop during every persistence boundary, including read completion and terminal closure. |
| High | **Valid fragmented Link transport is rejected.** `link_segments` invokes finite `receive([chunk], ...)` separately for every chunk. Each call creates/finalizes its decoder. One valid native manifest-plus-observation stream split after byte 10 fails with `Incomplete or failed Link transport; no silent truncation`. | Maintain decoder/session state across arbitrary transport chunks; close bounded segments at valid semantic boundaries. Exercise split frames, manifest-only chunks, rotations, disconnects and bounded backpressure without duplicating cumulative captures. |
| High | **Generated MCP launch config does not isolate imports.** `prepare` emits `python -m ciw.net ...` without `-I`. Launching that exact config from a temporary directory containing an inert `ciw/net.py` runs the temporary marker instead of installed NCP. The installed journey driver manually adds `-I`, so it does not validate the emitted client configuration. | Generate an isolated installed launch and execute that exact generated configuration in acceptance tests from an unrelated working directory with shadow packages and ambient import settings. Rebuild and identify the replacement wheel. |

Reproduction: from the repository root run `PYTHONPATH=src python3 validation/order-audit-20261017/reproduce.py`. For the installed configuration run `python3 validation/order-audit-20261017/reproduce_generated_config.py /absolute/path/to/generated-mcp.json`. These are inert software probes; no physical command, diagnostic workload or external deployment is performed. Results are retained in the adjacent JSON files.

## Other implementation and coverage gaps

- **Source provenance needs clearer projection.** The same native simulated observation stream can be bound with origin `synthetic` or `recorded`, and projected row origin follows that declaration. Native simulated semantics survive in lineage, and promotion to `measured` is refused. This is a conflation of recording status with acquisition origin, not demonstrated fabrication of a physical measurement. Preserve both dimensions explicitly and test every supported family.
- **Model integration is bounded to two adapters.** `operational_engineering.make_source` connects thermal and paired-reference engines. Existing calibrated-window, residual-monitor, scalar-study and Board/design paths are not thereby connected to this operational profile. Some families are inspectable while their model status is explicitly unsupported. Finish the relevant bindings or obtain an explicit work-order scope revision before declaring the full target complete.
- **Alternatives and proposed tests are largely generic.** `change` and `model_support` emit fixed alternative lists; `propose` copies an operator template and those alternatives. Dependency invalidation is implemented, but this does not establish that each explanation has evidence-specific discriminating predictions, observable targets and uncertainty/resource requirements. Review WP06–WP07 against actual per-hypothesis cases, including an explicit inability to predict distinguishing evidence.
- **Unknown-time rows have a discoverability gap.** Source inspection shows interval queries expose only an `unplaced_count` for unknown-time rows; the catalog has counts/channels, while row trace requires a row ID. An explicit bounded row-discovery/read path is needed to inspect those observations without fabricating timestamps. This is a source-inspection finding, not a reproduced end-to-end failure in this audit.
- **Remote evidence is local boundary evidence.** Signed fixture-token/TestClient checks exercise the SDK HTTP server boundary. They do not establish an actual selected remote worker, managed identity/TLS/network deployment, or a complete real HTTP SDK client journey.
- **Physical supervisory control is unavailable.** The added path prepares exact plans, records approval and can dispatch supported simulated changes. The underlying runtime refuses physical configuration/calibration/update/CONTROL. No physical closed-loop investigation/change/outcome acceptance is established. This is a stated implementation boundary; it remains short of the user's broader SCADA target.

## Operational work packages

| Package | Audited status | Remaining work |
| --- | --- | --- |
| WP01 Inventory | Delivered candidate inventory and baseline | Keep supported, unsupported and deployment-unqualified lanes explicit; reconcile the broader target with actual adapters. |
| WP02 Bindings | Implemented subset | Resolve provenance projection and review all family-specific contradictory/missing declarations. |
| WP03 Collection/admission | **Failed acceptance** | Repair stop race and fragmented ingress; qualify segment rotation and continuous acquisition limits. |
| WP04 History/diffs | Implemented snapshot subset | Complete collector integration and unknown-time observation discovery; extend failure coverage. |
| WP05 Engineering bindings | Partial | Two adapters implemented; remaining applicable native model/design integrations and evidence prerequisites remain open. |
| WP06 Findings | Partial | Retained deterministic findings exist; evidence-specific competing explanations need completion. |
| WP07 Next experiments | Partial | Frozen dependencies/staleness exist; discrimination and applicability require case-specific acceptance. |
| WP08 Personal MCP | Implemented candidate; **launch defect** | Correct generated client configuration and rerun complete journeys using it. |
| WP09 Laptop/worker | Local access and authenticated boundary candidate | Real HTTP client/worker/deployment acceptance remains open; deployment credentials/environment are separate prerequisites. |
| WP10 Operator view | Selected synthetic interface tested | Same-record browser evidence exists; broader operational/physical usability is unestablished. |
| WP11 Verification | **Incomplete** | Existing passing suite misses confirmed failures. Add meaningful regressions and rerun corrected source/wheel/client/console paths. Hosted operational CI was still queued. |
| WP12 Delivery | Wheel, guide and evidence delivered; **closeout rejected** | Replace affected artifacts after fixes; produce an accepted report with honest capability and scenario outcomes. |

## Acceptance evidence correction

The previously retained baseline 115, affected 512 passed/one live-GPU skip, final 58, console six, DOM three, installed J1–J5 and Chromium results are real results for their selected inputs. Counts overlap and must not be summed. This audit reran the focused reproductions, not those whole suites.

| Scenarios | Current assessment |
| --- | --- |
| A01–A07 | Existing selected software fixtures passed; this audit does not independently requalify all source families or physical applicability. Provenance coverage needs extension. |
| A08–A09 | Selected confounding/staleness cases passed; generic alternatives/templates leave broader WP06–WP07 discrimination acceptance incomplete. |
| A10–A11 | Existing refusal, retry and fresh-replay evidence retained; no new blanket qualification inferred. |
| A12–A13 | **Open/failed collector acceptance.** Existing kill/quota tests pass for their inputs, but stop ownership and continuous Link segmentation fail. These defects do not erase the narrower kill/quota results. |
| A14 | Existing retained inspection evidence retained; unknown-time observation discovery requires completion. |
| A15 | Local authenticated boundary evidence only; selected external deployment remains unestablished. |
| A16 | Same-record selected CLI/SDK/browser evidence retained; regenerate and retest after corrections. |
| A17 | **Incomplete.** Isolated test-driver imports/asset hashes pass, but the actual generated client config admits working-directory package substitution. |
| A18–A19 | Unavailability and software workload-gate cases passed; actual physical acquisition/GPU readiness remains unqualified. |
| A20 | Affected regression evidence retained; full hosted/native/provider qualification is not established by it. |

At the latest GitHub check, operational run [39071336223](https://github.com/notationsystems/Notations-Control-Protocol/actions/runs/39071336223) and consolidated run [39071336403](https://github.com/notationsystems/Notations-Control-Protocol/actions/runs/39071336403) on the evidence head were queued, not successful. The retained workflow snapshot is adjacent. Future results require a new check rather than assuming a pass.

## Other orders and integration state

| Order/group | Repository state and remaining acceptance |
| --- | --- |
| Historical consolidation | All 79 older currently open heads match the earlier consolidation ledger exactly. Their per-order dispositions are reproduced below. Retained/reconciled source does not establish native provider, physical or deployment qualification. Do not reimplement them merely because their original PR remains open. |
| Consolidation repairs #229–#231 | Merged; source/history and CI repairs have local evidence. The repair record explicitly retains required hosted full-history, Windows, credential-dependent provider, physical and remote gates. Its earlier failed hosted run and missing provider token are historical evidence, not proof of the latest main run's outcome. |
| [#232](https://github.com/notationsystems/Notations-Control-Protocol/pull/232) BIM | Draft, unmerged to main; head `585bfcca43122227e4355e473c9c7fa9742de2da`. Bounded software candidate exists; independent physical geometry/calibration/held-out acceptance remains open. |
| #236–#239 metrology | Marked merged **into intermediate branches**, not main: #236 → bounded-BIM branch; #237 → metrology-contract; #238 → metrology-sampled; #239 → metrology-review. Merge labels do not establish delivery to main or propagation of all later increments into #232. |
| [#240](https://github.com/notationsystems/Notations-Control-Protocol/pull/240) numerical operations | Ready, unmerged; head `c47a8621cd97f8d46afe526ed15534111fc6e347`. PR reports 556 focused and 72 installed cases. Full hosted/Windows acceptance remains pending; exploratory repository-wide timeouts/skips are not passes. Not included in #242's base. |
| [#241](https://github.com/notationsystems/Notations-Control-Protocol/pull/241) operational specification | Draft, documentation only, head `ee33170fbfca8022602468637073901d61ea5b22`. A defined order is not an implemented or accepted capability. |
| [#242](https://github.com/notationsystems/Notations-Control-Protocol/pull/242) operational implementation | Now draft; three confirmed blocking software defects, partial engineering integration, and physical/deployment qualifications open. |

The later metrology status document distinguishes T1–T4 candidate contracts/ingress/BIM/MCP from physical acceptance. T6 sampled least-squares plane residual, T7 bounded decisions/reference comparison, T9 frame-aligned freight envelope/rounding, and T10 visible principal-axis lean have software candidates. They do not qualify independent manufacturing, tariff, gravity/inclinometer, latency or dropout measurements. T5 physical BIM and T7 held-out physical uncertainty campaigns remain open. T8 implements a semantic perpendicularity association parser, not normative datum/control calculation or GD&T compliance. T11 is an authenticated HTTP software pilot without managed production deployment. T12 provides non-executing stale-bound action drafts; action adapters remain future work. The older task checklist is stale where this later implementation-status record supersedes it. The local status document is supporting context; remote PR heads and merge targets are the authoritative integration snapshot.

## Completion order

1. Repair and regress the three reproducible operational defects without relaxing retention, stop, identity or authorization contracts.
2. Complete the applicable missing operational model bindings, evidence-specific alternatives/proposals and unknown-time inspection; record source-family unavailable/unsupported cases.
3. Rebuild the wheel; execute its generated MCP configuration, complete installed SDK/console journeys and affected failure/regression checks; obtain hosted results for the exact candidate.
4. Review and integrate the numerical/metrology stacks using their actual bases and declared acceptance gates. Do not equate intermediate merges with main delivery.
5. Qualify each selected remote and physical profile using its actual credentials/environment, calibrated acquisition, independent references and approval/interlock requirements. Keep genuinely unavailable prerequisites separate from fixable software defects.

No merge, release, original-branch closure, physical action or deployment is authorized by this audit. The broader private SCADA/modelling/design/research workbench target remains an unfinished integration and qualification programme.

## Complete open-order inventory

The following snapshot is reconciled against `validation/consolidation/scope.json`. A retained disposition records earlier consolidation, not fresh independent acceptance. Exact heads and original draft states are in `open-orders.json`.

| PR | Order | Earlier consolidation disposition |
| --- | --- | --- |
| [#242](https://github.com/notationsystems/Notations-Control-Protocol/pull/242) | Connect operational records to engineering investigations through MCP and console | Outside earlier consolidation; now draft after confirmed audit defects. |
| [#241](https://github.com/notationsystems/Notations-Control-Protocol/pull/241) | Define extended work order for operational engineering investigations through MCP | outside earlier consolidation |
| [#240](https://github.com/notationsystems/Notations-Control-Protocol/pull/240) | Add seven built-in numerical operations through NCP and MCP | outside earlier consolidation |
| [#232](https://github.com/notationsystems/Notations-Control-Protocol/pull/232) | Add bounded BIM clearance inspections through retained execution and local MCP | outside earlier consolidation |
| [#228](https://github.com/notationsystems/Notations-Control-Protocol/pull/228) | Gate NCP gateway startup with read-only preflight | Already in the #228 ancestry; retained. |
| [#227](https://github.com/notationsystems/Notations-Control-Protocol/pull/227) | Integrate Board refit and frozen corpus coverage through MCP | Already in the #228 ancestry; retained. |
| [#226](https://github.com/notationsystems/Notations-Control-Protocol/pull/226) | Integrate GaugeLink MCP workflows and reviewed metrology evidence | Already in the #228 ancestry; retained. |
| [#225](https://github.com/notationsystems/Notations-Control-Protocol/pull/225) | Connect MCP planning to admitted durable engineering runs | Already in the #228 ancestry; retained. |
| [#224](https://github.com/notationsystems/Notations-Control-Protocol/pull/224) | Provision provider stacks and integrate atomic native Link data | Already in the #228 ancestry; retained. |
| [#223](https://github.com/notationsystems/Notations-Control-Protocol/pull/223) | Unify NCP corpus profiles and harden retained Windows evidence | Already in the #228 ancestry; retained. |
| [#222](https://github.com/notationsystems/Notations-Control-Protocol/pull/222) | Integrate GaugeLink, retained evidence and pinned Proto-Corpus through MCP | Already in the #228 ancestry; retained. |
| [#221](https://github.com/notationsystems/Notations-Control-Protocol/pull/221) | Build NCP with indexed corpus search, cited evidence and scoped MCP work | Reconciled with hardened corpus and MCP integration in #223–228. |
| [#219](https://github.com/notationsystems/Notations-Control-Protocol/pull/219) | Compile engineering intent into existing NET production plans and retained evidence gates | Already in the #228 ancestry; retained. |
| [#218](https://github.com/notationsystems/Notations-Control-Protocol/pull/218) | Propagate correlated timing uncertainty into model and held-out review | Reconciled into the combined subsystem; qualification gates retained. |
| [#217](https://github.com/notationsystems/Notations-Control-Protocol/pull/217) | Connect manufacturing timing observations to setup/batch identification and held-out review | Reconciled into the combined subsystem; qualification gates retained. |
| [#216](https://github.com/notationsystems/Notations-Control-Protocol/pull/216) | Strengthen manufacturing planning with bounded optimization, bottlenecks and robust process comparison | Reconciled into the combined subsystem; qualification gates retained. |
| [#215](https://github.com/notationsystems/Notations-Control-Protocol/pull/215) | Compile native sensor definitions into capability-bound manufacturing plans | Reconciled into the combined subsystem; qualification gates retained. |
| [#214](https://github.com/notationsystems/Notations-Control-Protocol/pull/214) | Connect reference-paired installation response to unified Link and retained NET execution | Already in the #228 ancestry; retained. |
| [#213](https://github.com/notationsystems/Notations-Control-Protocol/pull/213) | Build graph-first Industrial Lab on existing System Board and NET simulation lifecycle | Reconciled into the combined subsystem; qualification gates retained. |
| [#212](https://github.com/notationsystems/Notations-Control-Protocol/pull/212) | Connect retained KiCad and MEMS records to NET Blender/Godot evidence views | Reconciled into the combined subsystem; qualification gates retained. |
| [#211](https://github.com/notationsystems/Notations-Control-Protocol/pull/211) | Integrate pinned NIST and academic calibration reference data for GaugeLink | Reconciled with the pinned reference integration in #226. |
| [#210](https://github.com/notationsystems/Notations-Control-Protocol/pull/210) | Connect GaugeLink evidence to five-blade pinwheel review and uncertainty-improvement proposals | Reconciled with the later GaugeLink evidence/refit integration in #226. |
| [#209](https://github.com/notationsystems/Notations-Control-Protocol/pull/209) | Simplify GaugeLink Digital Inspection and harden signed local delivery | Already in the #228 ancestry; retained. |
| [#208](https://github.com/notationsystems/Notations-Control-Protocol/pull/208) | Add retained device and measurement views, Workbench handoff and evidence export | Reconciled with the retained Link/device integration in #226. |
| [#207](https://github.com/notationsystems/Notations-Control-Protocol/pull/207) | Connect retained device observations to Session comparison and read-only MCP | Reconciled into the combined subsystem; qualification gates retained. |
| [#206](https://github.com/notationsystems/Notations-Control-Protocol/pull/206) | Add recorded device coherence and permissioned network identity/consent lifecycle | Reconciled into the combined subsystem; qualification gates retained. |
| [#205](https://github.com/notationsystems/Notations-Control-Protocol/pull/205) | Connect Link serial acquisition to calibration, model, arithmetic audit and MCP | Already in the #228 ancestry; retained. |
| [#204](https://github.com/notationsystems/Notations-Control-Protocol/pull/204) | Implement Device Runtime, Link protocol, durable ingress and offline instrument Dashplate | Already in the #228 ancestry; retained. |
| [#203](https://github.com/notationsystems/Notations-Control-Protocol/pull/203) | Document OpenTelemetry and OpenObserve as an optional operational-observability plane | Reconciled into the combined subsystem; qualification gates retained. |
| [#201](https://github.com/notationsystems/Notations-Control-Protocol/pull/201) | Connect retained glTF references to existing mesh and Godot consumers | Reconciled into the combined subsystem; qualification gates retained. |
| [#200](https://github.com/notationsystems/Notations-Control-Protocol/pull/200) | Integrate retained data, native workflows and manufacturing QC evidence | Reconciled into the combined subsystem; qualification gates retained. |
| [#199](https://github.com/notationsystems/Notations-Control-Protocol/pull/199) | Procure NIST and industrial polymer data with checked GROMACS/LAMMPS input integration | Reconciled into the combined subsystem; qualification gates retained. |
| [#198](https://github.com/notationsystems/Notations-Control-Protocol/pull/198) | Connect retained CAREX references to native OIT rank diagnostics | Reconciled into the combined subsystem; qualification gates retained. |
| [#197](https://github.com/notationsystems/Notations-Control-Protocol/pull/197) | Add usable bounded LAMMPS external tooling with retained native artifacts and qualification gates | Reconciled into the combined subsystem; qualification gates retained. |
| [#196](https://github.com/notationsystems/Notations-Control-Protocol/pull/196) | Qualify academic corpus through existing NET data and numerical consumers | Reconciled into the combined subsystem; qualification gates retained. |
| [#195](https://github.com/notationsystems/Notations-Control-Protocol/pull/195) | Add root security, contributing, and citation policies | Reconciled into the combined subsystem; qualification gates retained. |
| [#194](https://github.com/notationsystems/Notations-Control-Protocol/pull/194) | Integrate durable QC capture and retrospective validation studies | Reconciled into the combined subsystem; qualification gates retained. |
| [#193](https://github.com/notationsystems/Notations-Control-Protocol/pull/193) | Execute retained materials investigations and covariance-dependent comparisons through Session | Reconciled into the combined subsystem; qualification gates retained. |
| [#192](https://github.com/notationsystems/Notations-Control-Protocol/pull/192) | Harden existing GROMACS provider and add an explicit native qualification gate | Reconciled into the combined subsystem; qualification gates retained. |
| [#191](https://github.com/notationsystems/Notations-Control-Protocol/pull/191) | Integrate bounded HOOMD CPU particle studies and native GSD evidence | Reconciled into the combined subsystem; qualification gates retained. |
| [#190](https://github.com/notationsystems/Notations-Control-Protocol/pull/190) | Qualify trusted scientific extension result relationships | Reconciled into the combined subsystem; qualification gates retained. |
| [#189](https://github.com/notationsystems/Notations-Control-Protocol/pull/189) | Integrate versioned preliminary product studies into NET evidence and replay | Reconciled into the combined subsystem; qualification gates retained. |
| [#188](https://github.com/notationsystems/Notations-Control-Protocol/pull/188) | Qualify physics/chemistry inputs and isolate held-out thermal evidence | Reconciled into the combined subsystem; qualification gates retained. |
| [#187](https://github.com/notationsystems/Notations-Control-Protocol/pull/187) | Qualify the isolated NVIDIA ALCHEMI CPU single-point adapter | Reconciled into the combined subsystem; qualification gates retained. |
| [#185](https://github.com/notationsystems/Notations-Control-Protocol/pull/185) | Integrate retained native datasets and cross-domain NET consumers | Reconciled into the combined subsystem; qualification gates retained. |
| [#184](https://github.com/notationsystems/Notations-Control-Protocol/pull/184) | Add retained-result dependency contracts and optional private domain-provider dispatch | Reconciled into the combined subsystem; qualification gates retained. |
| [#183](https://github.com/notationsystems/Notations-Control-Protocol/pull/183) | Connect engineering study to lot/part genealogy, inspections and dispositions | Reconciled into the combined subsystem; qualification gates retained. |
| [#182](https://github.com/notationsystems/Notations-Control-Protocol/pull/182) | Make one empirical rheology investigation source-bound and uncertainty-aware | Reconciled into the combined subsystem; qualification gates retained. |
| [#181](https://github.com/notationsystems/Notations-Control-Protocol/pull/181) | Add bounded ALCHEMI energy/force pilot with retained evidence and qualification gates | Reconciled into the combined subsystem; qualification gates retained. |
| [#180](https://github.com/notationsystems/Notations-Control-Protocol/pull/180) | feat: bounded sequence kinetics and Markov diagnostics from Fischbacher reference | Reconciled into the combined subsystem; qualification gates retained. |
| [#179](https://github.com/notationsystems/Notations-Control-Protocol/pull/179) | Model–Reality thermal identification: integrity hardening and separate-experiment assessment | Reconciled into the combined subsystem; qualification gates retained. |
| [#178](https://github.com/notationsystems/Notations-Control-Protocol/pull/178) | Add production dossiers with distinct manufacturing namespace and retained identity checks | Reconciled into the combined subsystem; qualification gates retained. |
| [#177](https://github.com/notationsystems/Notations-Control-Protocol/pull/177) | Add Polymer Microfab + Discrete Manufacturing Runtime with tool-point QC | Reconciled into the combined subsystem; qualification gates retained. |
| [#175](https://github.com/notationsystems/Notations-Control-Protocol/pull/175) | Extend polymer rheology with covariance-aware identification and independent scientific audits | Reconciled into the combined subsystem; qualification gates retained. |
| [#174](https://github.com/notationsystems/Notations-Control-Protocol/pull/174) | Add tested polymer viscoelasticity, molar-mass moments and benchmark inspection | Reconciled into the combined subsystem; qualification gates retained. |
| [#173](https://github.com/notationsystems/Notations-Control-Protocol/pull/173) | Add bounded physics–chemistry coupling with independent numerical verification | Reconciled into the combined subsystem; qualification gates retained. |
| [#172](https://github.com/notationsystems/Notations-Control-Protocol/pull/172) | Fix cross-platform roundoff rejection in retained temporal-study validation | Reconciled into the combined subsystem; qualification gates retained. |
| [#171](https://github.com/notationsystems/Notations-Control-Protocol/pull/171) | Add bounded reference comparison and transport timing tools | Reconciled into the combined subsystem; qualification gates retained. |
| [#170](https://github.com/notationsystems/Notations-Control-Protocol/pull/170) | Carry published metrology benchmark harness onto current main | Reconciled into the combined subsystem; qualification gates retained. |
| [#169](https://github.com/notationsystems/Notations-Control-Protocol/pull/169) | Carry bounded public temperature recording import onto current main | Reconciled into the combined subsystem; qualification gates retained. |
| [#166](https://github.com/notationsystems/Notations-Control-Protocol/pull/166) | Retain KRISS and BIPM metrology benchmark harness and current-wheel results | Reconciled into the combined subsystem; qualification gates retained. |
| [#165](https://github.com/notationsystems/Notations-Control-Protocol/pull/165) | Add physical and laboratory measurement workflows with public-source replays | Reconciled into the combined subsystem; qualification gates retained. |
| [#164](https://github.com/notationsystems/Notations-Control-Protocol/pull/164) | Run retained NIST, Oxford, CWRU and EPA data against an installed NET wheel | Reconciled into the combined subsystem; qualification gates retained. |
| [#163](https://github.com/notationsystems/Notations-Control-Protocol/pull/163) | Import retained temperature recordings through CLI and Session lifecycle | Reconciled into the combined subsystem; qualification gates retained. |
| [#162](https://github.com/notationsystems/Notations-Control-Protocol/pull/162) | Add offline tooling catalog visualizer with browser validation | Reconciled into the combined subsystem; qualification gates retained. |
| [#161](https://github.com/notationsystems/Notations-Control-Protocol/pull/161) | Add uncertainty-aware comparisons, timing reports and operator guidance | Reconciled into the combined subsystem; qualification gates retained. |
| [#151](https://github.com/notationsystems/Notations-Control-Protocol/pull/151) | Keep Foundry water production usable with current 1792 source | Reconciled into the combined subsystem; qualification gates retained. |
| [#148](https://github.com/notationsystems/Notations-Control-Protocol/pull/148) | Add qualified DSP conditioning and replayable pump diagnostics | Reconciled into the combined subsystem; qualification gates retained. |
| [#146](https://github.com/notationsystems/Notations-Control-Protocol/pull/146) | Add algebraic graphics workbench with retained mesh and texture replay | Reconciled into the combined subsystem; qualification gates retained. |
| [#145](https://github.com/notationsystems/Notations-Control-Protocol/pull/145) | Add five bounded heat-transfer and thermofluids tools | Reconciled into the combined subsystem; qualification gates retained. |
| [#143](https://github.com/notationsystems/Notations-Control-Protocol/pull/143) | Add qualified fluid multiscale dynamics, conservative coupling, and held-out experiment comparison | Reconciled into the combined subsystem; qualification gates retained. |
| [#142](https://github.com/notationsystems/Notations-Control-Protocol/pull/142) | Add 16 course-aligned geomatics and Earth observation tools | Reconciled into the combined subsystem; qualification gates retained. |
| [#141](https://github.com/notationsystems/Notations-Control-Protocol/pull/141) | Add bounded weather, hydrometeorology and cryosphere tools to NET | Reconciled into the combined subsystem; qualification gates retained. |
| [#139](https://github.com/notationsystems/Notations-Control-Protocol/pull/139) | Restore the complete 23-instrument NET workspace — native history transport | History-transport procedure excluded from execution: original PR explicitly says do not merge. Existing remote trees/history preserved; no native refs rewritten. |
| [#114](https://github.com/notationsystems/Notations-Control-Protocol/pull/114) | feat: verified impact and atmospheric engine with measurement preparation | Reconciled into the combined subsystem; qualification gates retained. |
| [#100](https://github.com/notationsystems/Notations-Control-Protocol/pull/100) | feat: spatial and mesh realization adapters V1 | Reconciled into the combined subsystem; qualification gates retained. |
| [#99](https://github.com/notationsystems/Notations-Control-Protocol/pull/99) | feat: typed representation realization adapters V1 | Reconciled into the combined subsystem; qualification gates retained. |
| [#98](https://github.com/notationsystems/Notations-Control-Protocol/pull/98) | feat: evidence-bound representation expansion V1 | Expansion intent retained through current verification/promotion gates; older permissive CLI implementation not restored. |
| [#78](https://github.com/notationsystems/Notations-Control-Protocol/pull/78) | feat: unattended local coding/vision agent with bounded workcell edits and usage accounting | Reconciled into the combined subsystem; qualification gates retained. |
| [#66](https://github.com/notationsystems/Notations-Control-Protocol/pull/66) | docs: invert institutional framing around Notation Systems commons and Cartesian commercialization | Reconciled into the combined subsystem; qualification gates retained. |
| [#64](https://github.com/notationsystems/Notations-Control-Protocol/pull/64) | feat: qualified GSC raster inspection and NDVI through the NET workbench | Reconciled into the combined subsystem; qualification gates retained. |
| [#63](https://github.com/notationsystems/Notations-Control-Protocol/pull/63) | feat: agent-driven simulation campaigns with bounded branching and replay | Reconciled into the combined subsystem; qualification gates retained. |
| [#53](https://github.com/notationsystems/Notations-Control-Protocol/pull/53) | docs: make README easier to read and get started with | Current main README retained; stale default-branch acceptance claims replaced with this review scope. |
