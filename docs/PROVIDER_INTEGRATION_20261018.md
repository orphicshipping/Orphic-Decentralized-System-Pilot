# Provider integration audit — 18 October 2026

NCP now exposes the supplied source map through the installed CLI, control MCP
and local console: **21 imports, 26 surrounding systems and 19 external
boundaries**. Two read-only tools connect source records to existing workflow
pins and provisioning prerequisites. The console can also launch the existing
measured-thermal, native leakage and native BIM-clearance profiles through the
same AgentHost/ControlHost operations, admissions, durable jobs and evidence.

Selected implementation: `0e4aeafd44fcf3ae3e25808550903b32260eeaa1`; source tree `e4a277693b63dde7d53cb319efdc490edde58c0a`.
[PR #245](https://github.com/notationsystems/Notations-Control-Protocol/pull/245)
was merged externally at `6c7c46231757b7ce79f95e07c35840268140a4e5` on 18 October 2026 at 01:03:59 UTC.
Its merge tree is byte-identical to the selected implementation tree. The
following evidence belongs to that selected code and exact installed wheel,
rather than to the later evidence commit.

## Audit findings and implemented connections

The missing links were installed source navigation and trusted native launch
options in the existing console/control interface. Scientific execution already
had its own registered operations. Discovery now projects the existing import
manifest, systems catalog and **nine runtime files**, with source hashes and a
CI consistency check. It retains separate per-workflow pins, original licences
and notices. Nineteen imported roles already connect to Workbench workflows;
the globe and FrameMapper retain separate npm/client build boundaries and null
Python runtime revisions.

| Import / role | Licence | Existing workflow or explicit instrument connection |
| --- | --- | --- |
| bim-estimator / cse | MIT | bim-quantity; native bim-clearance |
| calibration / mcur | MPL-2.0 | acquired-calibrated-window, calibrated-observable, calibrated-window, identified-design |
| clocksync / tbrt | MPL-2.0 | acquired-calibrated-window, calibrated-observable, calibrated-window, identified-design |
| compute-runtime / scr | Apache-2.0 | numerical-heat, proved-heat |
| estimator-bench / set | Apache-2.0 | acquired-calibrated-window, calibrated-observable, calibrated-window, identified-design, instrument-exchange, telemetry |
| faultsense / fdir | MPL-2.0 | calibrated-observable, identified-design, residual-monitor |
| flowstate / fsrt | MIT | calibrated-observable, identified-design, measurement-chain; native leakage |
| frame-mapper / framemapper | GPL-3.0 | Independent npm client/build |
| linear-dynamics / sidt | MPL-2.0 | identified-design |
| metrology / rci | MIT | measurement-chain |
| observability / oit | MPL-2.0 | calibrated-observable, identified-design, residual-monitor |
| polygon-trajectories / tsde | MIT | translation-flow |
| real-time-globe / gsv | GPL-3.0 | Independent npm client/build |
| retrieval-agent / sra | MIT | schematic-assessment, schematic-companions |
| sensitivity / jspt | MIT | measurement-chain, schematic-companions |
| sensor-design / edspt | MPL-2.0 | identified-design |
| signal-processing / stfe | MPL-2.0 | acquired-calibrated-window, calibrated-window, telemetry |
| state-inference / gsie | MPL-2.0 | acquired-calibrated-window, calibrated-observable, calibrated-window, identified-design, telemetry, variational-free-energy |
| state-recompiler / cbsr | AGPL-3.0 | calibrated-observable, identified-design, telemetry |
| surface / csg | MPL-2.0 | curved-path-transfer, variational-free-energy |
| yield-weighted / ywir | MIT | identified-design |

The [full connection matrix](../validation/provider-integration-20261018/integration-connections.json)
contains exact import revisions, runtime pins, source trees, external bindings
and the other 26/19 source records. A declared connection requires an explicit
host binding and the existing grant before execution. Listing it does not load
or qualify its provider. Optional/candidate providers remain behind their
original independent contracts.

Native BIM clearance uses original CSE revision
`f1ceeae28168de32ca9aea44f7b6d67d2090677e` and registered tree
`f91c982b580ad4f16a27ea149eedf0e338f6d764`. Its quantity-conditioning workflow
retains distinct runtime revision `4b74abda40bba3277de69bf61e9e09283ae2d5b3`.
Leakage keeps fixed FSRT revision `09a756dd9cdd3a9bb6cb14b5cd498f6259937ac2`
and tree `c79c44f90d5ff3920e58a53e9c277104e7e07bd3`; revision overrides refuse.
Both were exercised with unchanged exact original source checkouts. Browser
and MCP arguments cannot select arbitrary provider paths or add execution grants.

## Executed verification

| Gate | Observed result |
| --- | --- |
| Isolated complete source regression | **516 modules; 19,172 passed, 1,170 explicit skips, zero failures/errors** |
| Original history modules, within that sweep | 132 monorepo and 44 module-update tests passed, zero skips |
| Noneditable installed wheel contracts | **394 passed, zero skips/failures/errors** |
| Installed original operational native paths | Two tests passed, zero skips |
| Real provisioning | Eight original calibrated providers created and source-validated; one native provisioning test passed |
| Installed source/wheel byte identity | **724 package files matched** selected Git source, wheel and installed copy |
| Real modern/legacy MCP | Native control BIM and leakage; agent BIM resume/retry/fresh occurrence passed |
| Chromium console | Core five checks and native BIM six checks passed; no page errors |
| Console through installed SDK | Native leakage 47 calls; measured-thermal 12 calls; retained retry reused run |
| Console / DOM / private adapter | Seven / three / four tests passed |

These gates overlap; their case counts are not added together. All 1,170 skips
remain explicit in [skip-reasons.json](../validation/provider-integration-20261018/skip-reasons.json).
Separate installed native runs exercised selected paths that the broad optional
sweep skipped. They do not convert the entire skipped set into passes. Native
BIM correctly retained its **UNRESOLVED** scientific outcome with physical
qualification unestablished.

Wheel: `computational_instrumentation_workbench-0.1.0-py3-none-any.whl`.
SHA-256: `71cc7c881705b7e22cae2b6f5e9b0fb058deaefa986059d11220f6699c540d09`.
The [toolchain](../validation/provider-integration-20261018/toolchain.json),
[byte proof](../validation/provider-integration-20261018/wheel-byte-identity.json),
[module summary](../validation/provider-integration-20261018/regression-summary.json)
and compressed raw reports are retained beside the
[audit attestation](../validation/provider-integration-20261018/implementation.json).

One post-selection correction makes the new console test's trusted fixture paths
absolute on Windows using `tempfile.gettempdir()`. All seven console tests passed
again after that fixture correction. Production code and the packaged wheel
were unchanged; the full sweep remains attributed to `0e4aeafd44fcf3ae3e25808550903b32260eeaa1`.
Windows itself was not executed in this local campaign.

## Availability and remaining gates

Final read-only provisioning preflight checked all **29 workflows** from a clean
checkout of the selected code: **15 ready to provision, 14 blocked**. The earlier
14/15 snapshot preceded exact FSRT legacy-tree recovery; measurement-chain is
now ready. The [final plans](../validation/provider-integration-20261018/provisioning-preflight.json)
retain each missing revision or external prerequisite. Readiness establishes
local source availability, not dependency loadability, execution or qualification.
Remaining blockers cover unretained PPDA/CGGT/FTR/GTE/ISGT/PLSR or selected
historical SET/CBSR/JSPT sources and explicit Julia, engine, runtime and proof
bindings. They remain subject to their existing provisioning/access gates.

Hosted CI was observed at **2026-10-18T01:07:00.389Z** for the selected code:
106 runs, 96 queued, eight pending, one success and one skipped. This complete
two-page status observation is metadata, not retained required-job artifacts or
a qualified `ci_pass`. Required hosted/platform/private-provider, physical and
independent-human gates remain open. No equipment command, private deployment
or continuous-acquisition qualification was performed.

The earlier [research baseline ledger](RESEARCH_BASELINE_20261017.md) remains
separate and frozen at `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`. Original
235-branch cleanup candidates still require Task 4 confirmation, archive
preservation and current-head checks. No original branch has been deleted.
The existing two workflows were extended; no workflow was added. Transport
files were materialized byte-for-byte to complete this checkout; no transport
restore, unbundle, source publication or repository retirement was performed.

Earlier draft and harness failures are retained separately with their resolutions
in [earlier-attempts.json](../validation/provider-integration-20261018/earlier-attempts.json).
They are not used as qualifying pass evidence.
