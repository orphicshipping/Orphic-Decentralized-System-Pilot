# Published reference data for Link investigations

## Delivered scope

`net link reference` retains, reviews, audits, reopens and exports two precisely
selected published datasets through the existing CapabilityRegistry, typed graph,
Session, native artifact/covariance contracts and MCP AgentHost. It does not add
an acquisition service, calibration fitter, database, learned model or physical
state authority. The source Run's one declaration coordinate is **not sample
time**. These sources cannot be installed as another instrument's calibration.

| Profile | Original source and selection | Executed review |
|---|---|---|
| `nist-pontius` | Complete NIST Dataplot `PONTIUS.DAT`, 40 load/deflection observations in two ordered 20-point sets; units, acquisition time and joint measurement covariance unspecified | Evaluate NIST's three published quadratic coefficients and compare residual sum of squares with its certified numerical statistic; no fit performed |
| `ptb-mh44-first17` | First 17 MH44 calibration frequency rows, source lines 1–18 including the header, all 12 columns; 0.1–1.7 MHz | Retain pointwise real/imaginary covariance and use the **existing** NET `link_study._transport` for first-order magnitude variance; independent rational check |

### Exact provenance

NIST original file: [publisher source](https://github.com/usnistgov/dataplot/blob/d837c4af1282e51667ed75a02433f5e5590f1c6d/lib/data/PONTIUS.DAT),
commit `d837c4af1282e51667ed75a02433f5e5590f1c6d`, Git blob
`62e5292ae7798d61e18da8ef6cc8b9e41337b4e5`, 1,778 bytes, SHA-256
`7854389f4b3058fc1d1f03250843d2c2ef00e09162939339769e9cb9ab1e8d8c`.
[Certified statistics](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/v-Pontius.shtml)
are a separately attributed numerical transcription. The residual sum of squares
is `0.155761768796992E-05`; comparison tolerance `5e-21` is half the final printed
place. Coefficient standard errors are not a full covariance matrix or an
uncertainty budget for a current gauge. [NIST-PD](https://spdx.org/licenses/NIST-PD.html)
is declared by the publisher; its exact notice accompanies the fixtures.

MH44 [source](https://github.com/Ma-Weber/Tutorial-Deconvolution/blob/2de078bd09a58ed4063d2a6b477a94310a6e1039/HydrophoneCalibrationData/MW_MH44ReIm.csv),
Martin Weber, Volker Wilkens and Sascha Eichstädt, commit
`2de078bd09a58ed4063d2a6b477a94310a6e1039`; institutional archive
[DOI 10.5281/zenodo.10079801](https://doi.org/10.5281/zenodo.10079801).
The complete upstream CSV has blob `6acabcdb3281bed7b93563915e36b28f0e6e4801`;
**that complete file was not acquired or hash-verified**. Connector source lines
1–18 were retained with LF serialization and a final LF: 3,151 bytes, SHA-256
`598b7ac55a7835ed646a5b7defa82f057fac542a259c67a78498fbf62ca743f1`.
The full repository GPL-3.0 notice is preserved in `third_party/reference-data/`;
a distinct data-specific grant was not separately established. No upstream
algorithm implementation is copied or executed. No blanket commercial training
or redistribution conclusion is inferred from public availability.

## Scientific interpretation

Keep every original numeric token, selected line and byte range. NIST equal
loads in separate runs remain separate observations. No newtons or timestamps
are invented. Its native observation projection is explicitly withheld.

MH44 columns 4, 5 and 6 form the 2×2 covariance of real/imaginary sensitivity;
column 6 has squared-unit dimensions and is an **absolute covariance**, not a
unitless correlation coefficient. Columns 7, 10, 11 and 12 remain retained but
are not reinterpreted in this profile. Frequency conversion is explicit
`MHz × 1000000 = Hz`. Frequency is not time. Every point has its own covariance
identity and a derived magnitude covariance naming that parent. Cross-frequency
covariance remains unknown, with no block-diagonal or independence fabrication.

The independent audit uses Fraction arithmetic for
`(r*r*var_r + i*i*var_i + 2*r*i*cov_ri)/(r*r+i*i)`.
`2e-14` is a declared binary64 transport comparison tolerance. `2e-6` is a
separate relative printed-table comparison allowance, **not sensor accuracy**.
First-order propagation does not establish nonlinear confidence coverage.
Neither full waveform deconvolution nor the full academic tutorial is reproduced.

## Commands

```sh
net link reference catalog
net link reference prepare nist-pontius tests/reference_data/PONTIUS.DAT --output request.json
net link reference run request.json --output-dir investigation
net link reference inspect investigation
net link reference replay investigation --output-dir replay
net link reference export investigation --output-dir export
net link reference review-export export
net link reference report investigation --output reference.html
net link reference mcp investigation
```

Use new output paths. Source files remain unchanged. Original request, workspace,
graph and source bytes are included in the export; all derivative covariance
files are checked against original retained results. A coherently rehashed wrong
derivative refuses. Reopen and export review create no execution occurrences;
explicit replay creates fresh review and audit occurrences. MCP uses the existing
stdio server with zero execution grants; no listener or device access is added.

## Qualification and integration ownership

Run `tests/test_link_references.py` for the new cases. The existing installed
qualifier accepts `--with-references` and executes the entire inherited Link,
connectivity, scalar, measurement and pinwheel selection plus new cases and
both reference CLI journeys. CI requires the existing dependency pins.
`--allow-host-dependencies` is an explicit development-only exception recorded in
the receipt, never a supported-release pass.

The GaugeLink client calls this module; it does not copy these equations. Its
optional attachment is a supporting-reference association, not a measurement or
calibration dependency. PR200/185 Workbench/data ownership and PR206 identity /
consent remain untouched. This branch does not add a typed-data family or claim
a full PR200 integration. Existing Norris/TCLab consumers are not duplicated.

Physical instrument identity, timing, calibration, reference-standard traceability
and inspection release remain separate gates. No device, training, sharing,
calibration update, merge or deployment is part of this increment.
