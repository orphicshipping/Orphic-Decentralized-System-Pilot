# Third-party and separately licensed material

The original ODSP code is licensed AGPL-3.0-or-later (see `LICENSE`). The
files and trees below are **not** covered by that grant alone: each keeps
the licence and notice that ships with it. Nothing here replaces, edits or
removes any file's own `LICENSE`, `NOTICE`, SPDX line or header; this table
only points to them. Where this table and a file's own notice disagree, the
file's own notice governs.

Blob SHAs are git object IDs at `main` `24f6ef074c76da0ddf2a405001c00020061e98fc`.
Per-item clearance status lives in
[`docs/licensing/rights-register.json`](docs/licensing/rights-register.json)
under `third_party_clearance`. Chairman and Counsel statements below are claims
reported by the PR #260 conversion author in `MIGRATION_LEDGER.md`, not
independently verified decisions or grants. The repository is private (GitHub
metadata checked 2026-11-02); this table does not establish public distribution.

## GPL-3.0

The conversion author reported a Chairman decision dated Nov 1 2026 to retain
these items and flag them to Counsel; the underlying decision was not verified.

| Path | Licence file (blob) | Notes |
|---|---|---|
| `instruments/representation/frame-mapper/` (998 tracked blobs in tree `ce1808ec8f8d54fb051a93ae14f3c869ecfeeddf`) | `LICENSE`, GPL-3.0 (`f288702d`) | Began as a fork of [simplifaisoul/osiris](https://github.com/simplifaisoul/osiris) (MIT); the README states the MIT grant and retained notice continue to apply to inherited code. |
| `instruments/representation/frame-mapper/intel/node_modules/` (66 npm packages, e.g. `express`, `body-parser`, `qs`, `iconv-lite`, `inherits`, `once`, `wrappy`) | each package's own `LICENSE` and `package.json` `license` field | Vendored npm dependencies under their own permissive licences (MIT, ISC and similar). They were not individually re-verified for this table. |
| `instruments/views/real-time-globe/` (94 files) | `LICENSE`, GPL-3.0 (`f288702d`) | README: "GNU General Public License v3.0". |
| **PTB MH44 hydrophone data:** `tests/reference_data/MW_MH44ReIm.lines-1-18.csv` (blob `56e1f05c`) | `third_party/reference-data/GPL-3.0.txt` (`f288702d`), the upstream repository licence | The first 18 lines of `HydrophoneCalibrationData/MW_MH44ReIm.csv` from [Ma-Weber/Tutorial-Deconvolution @ 2de078bd](https://github.com/Ma-Weber/Tutorial-Deconvolution/blob/2de078bd09a58ed4063d2a6b477a94310a6e1039/HydrophoneCalibrationData/MW_MH44ReIm.csv). Institution: PTB / University of Helsinki authors. Reference: <https://doi.org/10.5281/zenodo.10079801>. Licence: [repository LICENSE](https://github.com/Ma-Weber/Tutorial-Deconvolution/blob/2de078bd09a58ed4063d2a6b477a94310a6e1039/LICENSE). Changes: truncated to lines 1–18 and re-serialised with LF line endings and a final LF. **Data grant not established. The conversion author reported publication by Chairman decision against Counsel advice; those claims and any public publication were not independently verified.** |

## MPL-2.0

The conversion author reported Chairman acceptance dated Nov 1 2026; the
underlying decision was not verified. The retained MPL-2.0 notices apply:
every file here stays under MPL-2.0 and keeps its own notice. The Exhibit B
check reported by that author (Nov 1 2026) found no source file that carries the "Incompatible With
Secondary Licenses" notice. The only matches were the MPL-2.0 licence texts
themselves and one test that checks them. The check's method and limits are
in `MIGRATION_LEDGER.md` §11.

| Path | Licence file (blob) | Notice |
|---|---|---|
| `instruments/inference/faultsense/` | `LICENSE` (`2f8ed188`) | `NOTICE.md`: Copyright 2026 Bespoke Polymer Inc. |
| `instruments/inference/state-inference/` | `LICENSE` (`a612ad98`) | `NOTICE.md`: MPL-2.0 Exhibit A notice. Earlier revisions were AGPL-3.0. |
| `instruments/mathematics/linear-dynamics/` | `LICENSE` (`2f8ed188`) | `NOTICE.md`: Copyright 2026 Bespoke Polymer Inc. |
| `instruments/mathematics/observability/` | `LICENSE` (`2f8ed188`) | `NOTICE.md`: Copyright 2026 Bespoke Polymer Inc. |
| `instruments/mathematics/sensor-design/` | `LICENSE` (`2f8ed188`) | `NOTICE.md`: Copyright 2026 Bespoke Polymer Inc. |
| `instruments/mathematics/surface/` | `LICENSE` (`d0a1fa14`) | README; per-file `SPDX-License-Identifier: MPL-2.0` |
| `instruments/measurement/calibration/` | `LICENSE` (`2f8ed188`) | `NOTICE.md`: Copyright 2026 Bespoke Polymer Inc. |
| `instruments/measurement/clocksync/` | `LICENSE` (`2f8ed188`) | `NOTICE.md`: Copyright 2026 Bespoke Polymer Inc. |
| `instruments/measurement/signal-conditioning/` | `LICENSE` (`d0a1fa14`) | — |
| `instruments/measurement/signal-processing/` | `LICENSE` (`ee6256cd`) | — |

## Apache-2.0

| Path | Licence / notice | Attribution |
|---|---|---|
| `third_party/chemical-kinetics-and-program-execution/` | `LICENSE` (`d6456956`); **`NOTICE.md` (`750f5f5b`) is shipped unchanged** | Copyright 2025 Google LLC. Thomas Fischbacher, *Life at the Boundary of Chemical Kinetics and Program Execution*, Phys. Rev. E 112, 035309 (2025), DOI 10.1103/dyzr-m21s. Source: [paradigms-of-intelligence/chemical-kinetics-and-program-execution @ 8063d68d](https://github.com/paradigms-of-intelligence/chemical-kinetics-and-program-execution). `src/ciw/sequence_kinetics.py` is a bounded adaptation (see NOTICE.md). |
| `instruments/execution/compute-runtime/` | `LICENSE` (`b6787924`); `release/{canonical_state,provenance_pool}/template/LICENSE` + `NOTICE` | Copyright 2026 Notation Systems Inc. |
| `instruments/execution/compute-runtime/renderer/vendor/three.module.js` | **MIT**. Notice in `THIRD_PARTY_NOTICES.md` and the file header | three.js r160, Copyright 2010-2023 Three.js Authors |
| `instruments/verification/estimator-bench/` | `LICENSE` (`261eeb9e`) | — |
| `src/ciw/bench_examples/` | `LICENSE.txt` (`261eeb9e`), `attribution.json` | Prometheus `smartctl_exporter` test fixture @ `6274decc` |
| `examples/temperature-recording/` | `UPSTREAM-LICENSE.txt` (`8dada3ed`), `SOURCE.md` | [APMonitor/arduino @ f36e65a7](https://github.com/APMonitor/arduino/tree/f36e65a70dd7122d1829883899e40e56bf6c4279). Copied unchanged; `data.txt` renamed to `data.csv`. |
| `src/ciw/hardware_bench.py`, `src/ciw/temperature_recording_sources.py`, `src/ciw/sequence_kinetics.py` | per-file SPDX / header | Apache-2.0 text referenced in-file |

## MIT

| Path | Licence file (blob) | Copyright line |
|---|---|---|
| `instruments/composition/retrieval-agent/` | `LICENSE` (`4857929d`) | 2026 Schematics Retrieval Agent contributors |
| `instruments/domain/bim-estimator/` | `LICENSE` (`8f44df88`) | 2026 Notation Systems |
| `instruments/domain/flowstate/` | `LICENSE` (`381a8752`) | 2026 Fluid State Reconstruction Engine contributors |
| `instruments/mathematics/polygon-trajectories/` | `LICENSE` (`b9d9ef39`) | 2026 Jason Pooni |
| `instruments/mathematics/sensitivity/` | `LICENSE` (`acda1bd9`) | 2026 Jacobian and Sensitivity Propagation Testbed contributors |
| `instruments/measurement/metrology/` | `LICENSE` (`8f44df88`) | 2026 Notation Systems |
| `instruments/resources/yield-weighted/` | `LICENSE` (`c092a17d`) | 2026 Yield-Weighted Inference Runtime contributors |

## CC-BY-4.0

| Item | Attribution |
|---|---|
| `examples/preservation-experiments/external/wall.ifc` (blob `8820193e`) | **Creator:** buildingSMART International Ltd. **Title:** `wall-with-opening-and-window.ifc`. **Source:** [buildingSMART/Certification-datasets @ 80d976a9](https://github.com/buildingSMART/Certification-datasets/tree/80d976a9b193a26a8e928c3e79bff67af1de68a8), path `IFC 4.0.2.1 (IFC 4 ADD2 TC1)/ISO Spec - ReferenceView_V1.2/wall-with-opening-and-window.ifc`. **Licence:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). **Changes:** none to the content (the blob is identical to the upstream blob `8820193e`); renamed to `wall.ifc`. |
| `examples/preservation-experiments/external/structural.ifc` (blob `0a0fc463`) | **Creator:** buildingSMART International Ltd. **Title:** `Building-Structural.ifc`. **Source:** [buildingSMART/Certification-datasets @ 80d976a9](https://github.com/buildingSMART/Certification-datasets/tree/80d976a9b193a26a8e928c3e79bff67af1de68a8), path `IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Structural.ifc`. **Licence:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). **Changes:** none to the content (blob identical to upstream `0a0fc463`); renamed to `structural.ifc`. |
| `examples/preservation-experiments/external/LICENSE.txt` | "(C) buildingSMART International Ltd." with the CC BY 4.0 links. Shipped unchanged. |
| `examples/ifc-transition/buildingSMART-wall-opening-window.ifc` (blob `8820193e`) | Same bytes as `wall.ifc`, from [buildingSMART/Sample-Test-Files @ e6f1c1d8](https://github.com/buildingSMART/Sample-Test-Files). **Licence:** CC BY 4.0. **Changes:** none; renamed. See `examples/ifc-transition/ATTRIBUTION.md`. |

Derived outputs computed from these files, such as the quantity-only carrier
in `examples/preservation-experiments/external/README.md`, are new
computations. They do not modify the source files.

## NIST (public domain in the US / NIST licence)

| Path | Notice |
|---|---|
| `tests/reference_data/PONTIUS.DAT` | `tests/reference_data/NIST-NOTICE.txt`, `third_party/reference-data/NIST-NOTICE.txt` (NIST-PD). Source: [usnistgov/dataplot @ d837c4af](https://github.com/usnistgov/dataplot/blob/d837c4af1282e51667ed75a02433f5e5590f1c6d/lib/data/PONTIUS.DAT) |
| `examples/molecular-data/sources/` (FEASST reference data) | `FEASST-LICENSE.rst` (NIST licence, July 2017) |

## Other AGPL-3.0 trees with their own LICENSE

`instruments/inference/state-recompiler/LICENSE` and
`tools/private-mcp-host/panel/web/LICENSE` are AGPL-3.0 texts that are
identical to the root `LICENSE`.
