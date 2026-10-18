# Related repositories and latent provider boundaries

This audit is tied to selected code `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`. All 21 imported module
paths, source repository declarations and licences in the supplied map match
[`instruments/manifest.json`](../instruments/manifest.json). The unchanged
source/ancestry audit passed for all 21. Exact import commits, import trees,
runtime revisions, additional history roots and all nine runtime manifests are
retained in the [machine-readable audit](../validation/research-baseline-20261017/provider-status.json).

“Latent” describes a qualification or availability boundary; it does not mean
every listed adapter lacks executable code. Keep the catalog's built-in kernel,
optional pinned adapter, client integration, planning/contract-only and pending
gate vocabulary. Import, binding, actual execution and qualification remain
separate states. Installing the main wheel does not activate these providers.

## Imported modules

The manifest owns the exact import and execution identities. Repository strings
below preserve its source attribution; historical `atomtrapping` links elsewhere
are not silently repinned or rewritten. Licence and notice files remain with
their original modules. This table records execution pins, not blanket readiness.

| Module / role | Co-located path | Declared source repository | Licence | Build boundary | Manifest runtime revision |
|---|---|---|---|---|---|
| `calibration` / `mcur` | `instruments/measurement/calibration` | [giasonpooni/Notations-Calibration-Runtime](https://github.com/giasonpooni/Notations-Calibration-Runtime) | MPL-2.0 | python | `49405ecd623474ddf601989b0a8195be401544e0` |
| `clocksync` / `tbrt` | `instruments/measurement/clocksync` | [giasonpooni/Notations-ClockSync](https://github.com/giasonpooni/Notations-ClockSync) | MPL-2.0 | python | `40507060ca7a9126a9d641999b994a757eef3bfd` |
| `observability` / `oit` | `instruments/mathematics/observability` | [giasonpooni/Notations-Observability-Testbed](https://github.com/giasonpooni/Notations-Observability-Testbed) | MPL-2.0 | python | `db4c564bddbe1911f96585bd18f58659a0026fb7` |
| `state-inference` / `gsie` | `instruments/inference/state-inference` | [giasonpooni/Notations-State-Inference-Engine](https://github.com/giasonpooni/Notations-State-Inference-Engine) | MPL-2.0 | python | `5241eee6dab434533bdf0cf0e824bc43b4a79831` |
| `state-recompiler` / `cbsr` | `instruments/inference/state-recompiler` | [giasonpooni/Notations-State-Recompiler](https://github.com/giasonpooni/Notations-State-Recompiler) | AGPL-3.0 | python | `daf43fc870ba926289c3bc4908db784691c7addd` |
| `faultsense` / `fdir` | `instruments/inference/faultsense` | [giasonpooni/Notations-FaultSense-RunTime](https://github.com/giasonpooni/Notations-FaultSense-RunTime) | MPL-2.0 | python | `29e4b306492b793487d47a96b97a56548217aa12` |
| `estimator-bench` / `set` | `instruments/verification/estimator-bench` | [giasonpooni/Notations-Estimator-Bench](https://github.com/giasonpooni/Notations-Estimator-Bench) | Apache-2.0 | python | `5e7bda36f521a5c1b0082b512f35e29803bffafc` |
| `sensor-design` / `edspt` | `instruments/mathematics/sensor-design` | [giasonpooni/Notations-SensorDesign-RunTime](https://github.com/giasonpooni/Notations-SensorDesign-RunTime) | MPL-2.0 | python | `89d5ea52f46676eabf13e906166e3ab701451680` |
| `linear-dynamics` / `sidt` | `instruments/mathematics/linear-dynamics` | [giasonpooni/Notations-Linear-Dynamics-Testbed](https://github.com/giasonpooni/Notations-Linear-Dynamics-Testbed) | MPL-2.0 | python | `b7e81402d5f4e854b3c8bb0d0354afe4de9269c6` |
| `sensitivity` / `jspt` | `instruments/mathematics/sensitivity` | [giasonpooni/Notations-Sensitivity-Testbed](https://github.com/giasonpooni/Notations-Sensitivity-Testbed) | MIT | python | `d910f5a1d7f6dd5f2dd87dfca66990f714f97b18` |
| `metrology` / `rci` | `instruments/measurement/metrology` | [giasonpooni/Notations-Metrology-Adapter](https://github.com/giasonpooni/Notations-Metrology-Adapter) | MIT | python | `f863bdd69d49224e0cdc871943bbb052e5b0a975` |
| `signal-processing` / `stfe` | `instruments/measurement/signal-processing` | [giasonpooni/Notations-Signal-Processing-RunTime](https://github.com/giasonpooni/Notations-Signal-Processing-RunTime) | MPL-2.0 | python | `54dd43b657b92d87c2fc263dfd99437c664be353` |
| `polygon-trajectories` / `tsde` | `instruments/mathematics/polygon-trajectories` | [giasonpooni/Polygon-Trajectory-Experiments](https://github.com/giasonpooni/Polygon-Trajectory-Experiments) | MIT | python | `5e72693e7e5722c47883ef1a68b2959afdf2e215` |
| `surface` / `csg` | `instruments/mathematics/surface` | [giasonpooni/Notations-Surface-RunTime](https://github.com/giasonpooni/Notations-Surface-RunTime) | MPL-2.0 | python | `bbc535af29c30997e56fd120320c570830676462` |
| `retrieval-agent` / `sra` | `instruments/composition/retrieval-agent` | [giasonpooni/Notations-Retrieval-Agent](https://github.com/giasonpooni/Notations-Retrieval-Agent) | MIT | python | `a6e79585950bb6860e5edce5ebd2cce39ea481f2` |
| `yield-weighted` / `ywir` | `instruments/resources/yield-weighted` | [giasonpooni/Notations-Yield-Weighted-Runtime](https://github.com/giasonpooni/Notations-Yield-Weighted-Runtime) | MIT | python | `e1f4a8c128a9c14e5b82acad76696219a5b5b6a9` |
| `bim-estimator` / `cse` | `instruments/domain/bim-estimator` | [giasonpooni/Notations-Estimator-for-BIM](https://github.com/giasonpooni/Notations-Estimator-for-BIM) | MIT | python | `4b74abda40bba3277de69bf61e9e09283ae2d5b3` |
| `real-time-globe` / `gsv` | `instruments/views/real-time-globe` | [giasonpooni/Notations-Real-Time-Globe](https://github.com/giasonpooni/Notations-Real-Time-Globe) | GPL-3.0 | npm | None (npm module) |
| `frame-mapper` / `framemapper` | `instruments/representation/frame-mapper` | [giasonpooni/Notations-FrameMapper-RunTime](https://github.com/giasonpooni/Notations-FrameMapper-RunTime) | GPL-3.0 | npm | None (npm module) |
| `compute-runtime` / `scr` | `instruments/execution/compute-runtime` | [giasonpooni/Notations-Compute-Runtime](https://github.com/giasonpooni/Notations-Compute-Runtime) | Apache-2.0 | python-source | `a59aba283b0304faeeb3e5d305087e7709e171ca` |
| `flowstate` / `fsrt` | `instruments/domain/flowstate` | [giasonpooni/Notations-FlowState](https://github.com/giasonpooni/Notations-FlowState) | MIT | python | `d7c181fb9967883e085b3728d38096f59e54efbd` |

Eighteen modules have independent Python package boundaries, SCR is retained
as Python source, and the globe/FrameMapper modules have independent npm build
boundaries. Their null Python runtime revisions stay null. Their source tests,
web builds and exact-source gates are independent of root-wheel installation.

The installed operational native gate executed two existing calibrated-window
and residual-monitor tests using exact TBRT/MCUR/STFE/GSIE/SET/OIT/FDIR pins
(roles `tbrt`, `mcur`, `stfe`, `gsie`, `set`, `oit`, `fdir`). Its synthetic
observations do not establish physical calibration. Other provider/profile
executions must be read from their specific reports; a source audit is not an
execution receipt. Bounded BIM's integrated code retains the exact CSE subtree
`f91c982b580ad4f16a27ea149eedf0e338f6d764`; surveyed-frame and physical geometry
qualification remain separate.

Profile-specific revisions must stay separate: for example, GSIE's manifest
runtime is `5241eee6dab434533bdf0cf0e824bc43b4a79831`, while calibrated-window
requires `de38873db1ba98744d395dfa7df1d3b5b7bb3059`. SET likewise has current,
calibrated-window and historical replay pins. Do not merge these into one
global provider checkout merely because their roles have the same name.

## Surrounding systems and external tools

All 26 independent systems in the supplied surrounding-system list match the
[systems catalog](SYSTEMS_CATALOG.md). Their source README links, NCP roles and
existing status text are retained in the machine-readable audit. That list is
not identical to the 21 imports: PPDA, PLSR, ESM, several geometry references and
the conformance harness have separately provisioned boundaries.

| Family | Integrated boundary and remaining gate |
|---|---|
| Godot / Bevy / Blender | Existing inspection/projector or optional authoring boundaries; general simulation and qualified authoring jobs remain separate. |
| SP1 | Registered selected proof path only; this close-out does not re-execute native proof qualification or qualify other profiles. |
| Catalyst / Cantera; ControlSystemsBase; Tsit5; JuMP/HiGHS; JuliaIntervals | Existing bounded native profiles; require their exact interpreter, source, dependency and replay gates. |
| NVIDIA ALCHEMI | Optional bounded atomic Lennard-Jones energy/forces pilot; native CPU/CUDA qualification remains open. |
| OpenTelemetry / OpenObserve | Operational-observability planning contract; no exporter, backend deployment or new scientific evidence authority is enabled here. |
| ModelingToolkit / RxInfer / OpenFOAM / FreeCAD / TensorFlow Lattice / FPGA | Candidate providers; no new bindings or implementations are added by this audit. |
| GaugeLink | Independent instrument-acquisition repository and contracts; NCP consumes permitted observations and retains downstream evidence. No physical command authority is inferred. |

The existing provider-availability guide requires exact local checkouts,
unchanged index/tracked bytes and explicit environment bindings. Missing private
access remains a refusal or named gate. PLSR's optional Git extra, exchange
SET/PPDA/SCR checkouts, the nested ESM graph and PPDA's SCOUT gitlink retain
their distinct provisioning and licence obligations.

## Files that own each boundary

| File | Responsibility |
|---|---|
| [manifest](../instruments/manifest.json) / [monorepo guide](MONOREPO.md) | Import ancestry, source identity, licence and independent package boundaries. |
| [systems catalog](SYSTEMS_CATALOG.md) / [stack](STACK.md) | Surrounding-system roles, status vocabulary and authority boundaries. |
| [integration coverage](INTEGRATION_COVERAGE.md) | Implemented paths, declared evidence categories and remaining gates. |
| [provider availability](PROVIDER_AVAILABILITY.md) | Local checkout provisioning and restricted-access limits. |
| [runtime manifest directory](../src/ciw/) | Exact profile-specific pins; the dated audit preserves all nine JSON snapshots. |
| [unimplemented directions](UNIMPLEMENTED_DIRECTIONS.md) / [consolidation](CONSOLIDATION.md) | Deferred work and proposed connective tissue. |
| [cloud development](CLOUD_DEVELOPMENT.md) / [start here](BASELINE_START_HERE.md) | Development setup and a short installed-wheel entry path. |
| [selected baseline](RESEARCH_BASELINE_20261017.md) / [risks](BASELINE_RISKS_20261018.md) | Actual checks, qualification limits, owners and cleanup decisions. |

Future integration should add one bounded profile at a time through existing
Session/MCP/console operations, retain inputs/results/refusals/replay, and qualify
that exact binding. This audit changes no provider pin, source licence,
execution grant, private-token boundary or equipment interlock.

The subsequent main merge of #139 adds native-history transport and recovery bytes. Its separate 23-module restoration target is not a new entry in the selected or observed-main 21-module manifest. No restoration/publication script was executed, no source repository was retired and no qualification was inferred from that merge. See the [repository refresh](../validation/research-baseline-20261017/repository-state-refresh.json).
