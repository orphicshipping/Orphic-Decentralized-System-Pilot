# Public polymer temperature dataset assessment

Assessed 4 October 2026. This is a source and suitability assessment, not physical qualification of the proposed polycarbonate/thermocouple chain. Two academic archives contain inspected numerical records. NIST lists additional relevant records, but their actual bytes were not retrieved in this assessment. No dataset below establishes an independent, traceable reference for the selected installed chain; no experimental results or calibration records have been invented.

The ranking below is for the next conditional conventional-model/PINN benchmark, not for purchasing equipment or changing the pilot's physical qualification status.

The [source-byte audit manifest](data/polymer-temperature-source-manifest.json) retains computed ZIP and selected member SHA-256 hashes, byte counts, retrieval times, source identifiers and bounded import findings from a fresh reread. Its hashes identify that reread; they are not retrospective hashes of the earlier assessment. No raw dataset is bundled.

## 1. Bremen resistance welding: strongest immediately inspectable heating candidate, conditional on import audit

The [Faserinstitut/University of Bremen deposit](https://zenodo.org/records/18875080) supplies [RW_Data.zip](https://zenodo.org/records/18875080/files/RW_Data.zip?download=1), machine-process CSVs and selected edge-temperature CSVs. Archive contents were inspected in memory. Example files are `RW_250221_V13/RW_250221_V13_machine_data.csv` and `RW_250221_V13/RW_250221_V13_thermography_edge_temperature.csv`.

These contain timestamps, current/voltage and thermocouple readings, plus separate infrared time/temperature records. V13 concerns glass-fibre-reinforced Elium 151/191 acrylic resin, a 200 x 30 mm joining region, 5.3 mm adherends and a 180 degC target. A 0.1 mm type-J thermocouple senses the weld core; FLIR SC600 thermography senses the edge. Geometry fields have an explicit [metadata schema](https://zenodo.org/records/18875080/files/rw_metadata_schema_v1.0.json?download=1).

Observed import problems: [experiment metadata](https://zenodo.org/records/18875080/files/experiments_metadata.json?download=1) fails strict JSON parsing at line 170; V13 instrument clocks differ by approximately one hour; the inspected IR timestamps have median spacing 0.020 s (50 Hz) despite documented 25 Hz; temperature channels 2-4 contain 3276.7 in all 300 inspected machine rows, with no established status/sentinel interpretation. Metadata also names `RW_260116_V02`, absent from the inspected archive. Do not silently repair clocks or accept unresolved channels as valid temperatures. No calibration certificates or full uncertainty budget were located. Core and edge channels measure different locations; electrical input is not automatically heat absorbed by the polymer.

**License declarations conflict:** the [deposit API](https://zenodo.org/api/records/18875080) identifies [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), while the landing-page description and `LICENSE_CC_BY_ND_4.0.txt` state [CC BY-ND 4.0](https://creativecommons.org/licenses/by-nd/4.0/). This assessment records the disagreement and retains source links and import findings only; no original or modified dataset is bundled.

## 2. NIST ABS material extrusion: closest named MatEx candidate, download verification outstanding

The [official catalog](https://catalog.data.gov/dataset/temperature-profiles-of-acrylonitrile-butadiene-styrene-abs-during-bench-scale-material-ex) lists three temperature files, `ABS_210_3_336.txt`, `ABS_210_3_345.txt`, and `ABS_210_3_350.txt`, each with a SHA-256 sidecar. Six listed resources therefore do not mean six independent runs. These are reflection-corrected layer temperature profiles, not raw detector counts. [Example file](https://data.nist.gov/od/ds/ark:/88434/mds2-2658/ABS_210_3_336.txt). The attempted direct download returned HTTP 524; headers, rows, units and hashes remain unverified.

The [associated primary paper](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=923318) describes ABS sheets 16 layers high, approximately 0.4 mm wide and 200 mm long, printed at 210-270 degC and 3-100 mm/s. FLIR A6701sc imaging is acquired at 60 Hz with offline polymer/hot-stage radiometric calibration and reflection correction. Weld temperature is calculated from neighbouring layer temperatures rather than directly measured. It must not become independent reference truth for a model using those same layers. The paper's full experimental range also does not establish that the three catalog files cover that range.

**Use:** a conditional surface/layer-temperature benchmark once actual files and identifiers are verified. It does not reproduce the proposed 50-80 degC neat-PC installed thermocouple workflow. **License:** the catalog links to [NIST data terms](https://www.nist.gov/open/license). This assessment retains source links and catalog findings only; no dataset is bundled.

## 3. Montpellier/CNRS nylon DMTA: accessible benchmark with a different measurand

The [academic deposit](https://zenodo.org/records/7257481) contains [PA66_Temperature.zip](https://zenodo.org/records/7257481/files/PA66_Temperature.zip?download=1). The archive contains 66 temperature text files; the inspected `PA66_Temperature/313/PA_1Hz.txt` has 5,577 numerical rows across 179.872 s. Signals are spatially averaged loaded-minus-dummy temperature differences, not absolute sample temperature or detector counts.

The [paper](https://jtcam.episciences.org/11087/pdf) specifies Technyl A218 PA6.6 specimens 85 x 13 x 4 mm, Bose ELF3230 mechanical loading and CEDIP Titanium infrared acquisition at approximately 31 Hz. Nylon chamber conditions span 308-333 K. Pixelwise blackbody calibration preceded each series through the same optical path. Mechanical loading produces the thermal response; an independent electrical heat-input channel, absolute reference thermometer and complete traceability budget were not located.

**Use:** separate thermomechanical baseline/PINN benchmark, preserving the temperature-difference measurand. **License:** the deposit API identifies [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This assessment retains source links and inspection findings only; no original or modified dataset is bundled.

## 4. NIST PMMA gasification: useful later, materially different physics

The [official dataset catalog](https://catalog.data.gov/dataset/experimental-measurements-for-pyrolysis-model-validation-anaerobic-gasification-of-pmma-un) lists five repeated-test back-surface-temperature CSVs, mass records, and copper/insulation temperature checks. PMMA discs approximately 70 mm in diameter and 5.8 mm thick undergo approximately 50 kW/m2 radiation in nitrogen until decomposition. Boundary-condition measurements are described; some heat-flux resources are plots rather than numerical histories. [Example temperature CSV](https://data.nist.gov/od/ds/mds2-2940/MaCFP-PMMA_Gasification_q50_Temp_R1.csv). File bytes were not retrieved here. The catalog links to [NIST data terms](https://www.nist.gov/open/license); no dataset is bundled here.

**Use:** a dedicated pyrolysis model-validation path after retrieval and acquisition/calibration review. Do not silently apply the existing simple cooling or fixed-property heat model through decomposition, or digitize heat-flux plots and label them acquired observations. Inert-material checks are useful boundary-condition evidence, not simultaneous independent reference measurements of each PMMA temperature.

## Exclusion and next bounded work

[NIST explicitly reports](https://www.nist.gov/ambench/direct-am-bench-data-links-and-referencing-guidance) that AMB2022-06 polycarbonate/PLA results are not currently planned for publication because measurement and stability of volumetric flow rate proved unreliable. The prominently linked AMBench in-situ thermography datasets for IN718 are metal experiments and cannot be substituted as polymer evidence.

Prioritize a Bremen acquisition/import audit, while retrying the NIST ABS source. Preserve original bytes and hashes; document delimiter/decimal conventions, invalid-channel policy, clocks, exact measurand and permitted use. Reject unresolved synchronization for cross-instrument comparisons. Freeze train/validation/test groups by whole experiment and condition before fitting either model; never split adjacent frames randomly. Report input uncertainty, installation effects and model discrepancy separately. Unknown contributions remain unknown.

No training, performance comparison, physical calibration or proof qualification was performed. The present 64-observation processing bound also requires an explicit long-series/windowing design; it must not silently truncate these records or imply cross-window covariance propagation. A benchmark may proceed with conditional claims after the relevant import gaps are resolved, while installed-chain physical qualification remains not established.
