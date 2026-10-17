# Physical measurement campaigns across instruments and media

The release remains unchanged. This is a development file-adapter path for
scalar engineering readouts. Actual measurements, applicable calibration,
independent physical references and agreed criteria are still required.
Software checks and generated templates do not establish physical qualification.

The [fluid thermal campaign](FLUID_THERMAL_MEASUREMENTS.md) and its adapter retain
their existing scope. Its liquid-water assumptions do not become defaults for
other instruments or media. This expansion does not connect to hardware,
produce predictions, authenticate laboratory evidence or change a release.

## Follow the recorded chronology

The accompanying `source-chronology.json` records the earliest addition of
each inspected current anchor path reachable from the recorded NET revision.
Entries are ordered by Git author timestamp converted to UTC; committer dates
are retained separately. A path's first-add date does not date every feature
subsequently added to it, or any hardware acquisition or calibration.

| Order | First-add author date | Recorded family and next campaign step |
| --- | --- | --- |
| 1 | 2026-09-19 | Synthetic oscillator: axial displacement; dynamic/vector work remains separate |
| 2–4 | 2026-09-20–21 | Reservoir mass, scalar telemetry and two-channel process: static mass, then multi-channel covariance |
| 5–8 | 2026-09-22 | Scalar windows, acquisition, streams and facade: stationary liquid level with retained lineage |
| 9 | 2026-09-23 | GPU energy: specialized counter/reference campaign remains pending |
| 10–11 | 2026-09-23 | Encoder position and two-capacity thermal: exported displacement and local solid contact temperature |
| 12 | 2026-09-29 | FIR processing: preserve source calibration; filtering is not a calibrated sensor |
| 13 | 2026-10-02 | Impact benchmark: specialized dynamic-force campaign |
| 14–15 | 2026-10-03 | Atmosphere and fluids: temperature, explicit pressure reference, level and actual flow |
| 16 | 2026-10-03 | Polymer: dimension, thickness, temperature, pressure, strain and wet-basis moisture |
| 17–18 | 2026-10-03 | Inventory balance and irrigation: mass/transfer scope, flow, pressure and soil water |

GPU energy retains position 9 before later position/thermal families even
while its specialized measurement work remains pending. Later scalar adapters
do not close that gap. Registry display order is not chronology.
The JSON contains exact timestamps, anchors and commits. The fluid thermal
starter has no tracked first-add date at the inspected revision.

Some entries are synthetic instruments, models or processing operations;
others retain device captures. Classify the evidence actually present.
A simulated value may exercise software but cannot be a physical reference.

## Preserve the measurand

The adapter starts with exported engineering readouts and a supplied affine
calibration justified for the specified configuration and conditions.
A calibration may instead require a curve, table or other measurement model.
[JCGM VIM, calibration](https://jcgm.bipm.org/vim/en/2.39.html)

| Profile | Meaning and context to preserve |
| --- | --- |
| `displacement.axial.v1` | Signed displacement; axis, datum and mounting |
| `mass.static.v1` | Nonnegative true or conventional mass; tare, loading and buoyancy status; no conversion between conventions |
| `level.liquid.v1` | Nonnegative height above a declared lower datum; geometry and surface condition |
| `temperature.contact.v1` | Positive temperature on the declared ITS-90 or thermodynamic scale; no implicit scale conversion; contact, attachment/immersion and equilibration |
| `pressure.absolute.v1` | Nonnegative pressure relative to vacuum; reference, port, elevation and temperature |
| `pressure.gauge.v1` | Signed pressure relative to a declared gauge reference; port, elevation and temperature |
| `pressure.differential.v1` | Positive-port minus negative-port pressure; port identities, elevation difference and temperature |
| `dimension.length.v1` | Nonnegative feature length; reference temperature, correction status and measurement force |
| `dimension.thickness.v1` | Nonnegative surface separation; feature, temperature convention and measurement force |
| `flow.volume_actual.v1` | Nonnegative flow magnitude in the declared direction at actual local conditions; section, pressure, temperature and composition |
| `strain.axial.v1` | Signed scalar axial strain; definition, axis, gauge length, zero reference and temperature compensation |
| `water_content.volumetric.v1` | Water volume/bulk soil volume; sampling volume, soil, temperature and salinity |
| `moisture.mass_fraction.v1` | Wet-basis water mass/total sample mass; conditioning, water selectivity and sampling |

Use `catalog` or generated `profile.json` for admitted media, units and exact
condition keys. Equal units do not establish an equal measurand. No conversion
is supplied between pressure references, mass conventions, actual/standard
volume flow, or volumetric/wet-basis/dry-basis moisture. Loss on drying is not
automatically water content. A local probe does not establish a root-zone mean.

A measurand definition includes the object's or substance's relevant state and
components. Measurement conditions may require a correction model.
[JCGM VIM, measurand](https://jcgm.bipm.org/vim/en/2.3.html)

## Initialize and populate the campaign

Run from an environment with this checkout installed. The mass example begins
the earliest existing calibrated measurement-chain family after the oscillator.
Every output directory must be new. Run `adapt` after completing its inputs;
running it on an untouched draft intentionally reports missing evidence.

```console
python -m ciw.physical_measurements catalog
python -m ciw.physical_measurements init --profile mass.static.v1 --medium liquid --output-dir work/mass-campaign
python -m ciw.physical_measurements init-all --output-dir work/physical-campaigns
python -m ciw.physical_measurements adapt --manifest work/mass-campaign/campaign.json --output-dir work/mass-bundle
```

`init` emits `campaign.json`, `profile.json`, `calibration.template.json`,
`run.template.json`, `criteria.template.json`,
`held-out-policy.template.json`, `uncertainty-budget.template.json`,
`readings.template.csv` and `estimates.template.csv`.
`init-all` emits one directory per scalar profile, `catalog.json`, and ten
`specialized/<family>/intake-draft.json` worksheets. Specialized drafts record
missing specs/evidence and are not accepted by the scalar evaluator.
Unfilled values remain `null`; no hardware, limits or agreement are inferred.

The manifest schema is `ciw.physical-measurements.v1`. Copy completed run,
criteria, held-out-policy and uncertainty-budget objects into their manifest
fields; the adapter does not discover companion templates automatically.
Create separate completed calibration files for observation and reference.
Identify sensor, instrument, readout and channel for each chain.
Preserve original exports and record mapping, software, time base, units and
device-side processing. Do not apply an already-applied correction twice.

File-valued fields use exactly `{"file": "relative/path", "sha256": "sha256:…"}`.
Replace the ellipsis with the full lowercase SHA-256 digest of the exact bytes.
All descriptor paths, including those inside calibration files, are relative
to the campaign manifest directory and must remain within it. Descriptors
cover CSVs, calibration files, certificates, instrument specifications,
validity policy, model artifact and uncertainty-budget evidence. A descriptor
binds bytes; it does not authenticate the author or establish physical validity.

## CSV contracts

Reading files require these exact ordered column names:

```csv
timestamp_s,value,standard_uncertainty,quality
```

Use `quality=valid` with finite value and nonnegative **standard** uncertainty
in the channel's input unit. For `quality=missing`, leave value and uncertainty
empty. Retain the missing row on the declared sample grid. Expanded uncertainty,
resolution and tolerance are not substitutes for standard uncertainty.

Supplied prediction files require these exact ordered column names:

```csv
timestamp_s,estimate
```

Estimates are finite values in the profile's **canonical unit**, within its
physical domain and expressing the same measurand/reference convention as the
comparison. The adapter does not generate predictions or recalibrate values.
JSON/CSV nonfinite numbers and nonzero numeric tokens that underflow to zero
are refused rather than silently changing the measurement.

Timestamps are strictly increasing seconds from the run acquisition start.
The prescribed sample grid includes both endpoints, from zero through the
acquisition duration; duration/sample interval must be an integer of at least
one, with exactly that many steps plus one row. Keep all missing rows, including
leading/trailing ones. Observation, held-out reference and estimate timestamps
must match exactly. No interpolation, resampling or clock conversion occurs.
Each file permits 20000 rows; a campaign permits 100 runs and one day per run.
Acquisition/freeze timestamps require explicit UTC.

## Complete the specification worksheet

`scope.minimum`, `maximum` and `resolution` use canonical units.
Supply positive resolution, sampling interval and bandwidth from reviewed
instrument/use specifications. The adapter checks observation intervals against
`sampling_interval_s`; it does not infer bandwidth, verify anti-alias filtering,
or establish a response model from the sample rate.

`scope.conditions` has the profile's exact keys. Values are nonempty strings
describing reviewed values, units, conventions and evidence context; they are
not automatically evaluated environmental corrections. Enumerated conventions
must match the catalog, including pressure type, mass convention and wet basis.
Attach the specification document through `instrument_specification`.

| Specification to establish | Campaign entry |
| --- | --- |
| Actual sensor/readout/channel IDs, configuration, firmware and export software | Unassigned |
| Measurand, medium, phase, composition, location, datum and orientation | Unassigned |
| Operating/calibrated ranges, resolution and saturation behavior | Unassigned |
| Bandwidth, filtering, averaging, sample interval and clock synchronization | Unassigned |
| Required conditions and environmental applicability | Unassigned |
| Calibration chain, certificate, dates and validity policy | Unassigned |
| Uncertainty components, covariance and review evidence | Unassigned |
| Independent reference, whole-run partition and custody/freeze record | Unassigned |
| Criteria, limits, coverage, decision rule and agreement record | Unassigned |

Range, resolution, repeatability and uncertainty describe different properties.
A small display increment does not establish a small uncertainty.

## Calibration binding and uncertainty

Calibration schema `ciw.scalar-readout-calibration.v1` binds the exact profile,
chain IDs, input/canonical units and scope. Use `scope_sha256 =
digest(dump(manifest["scope"]))`, with `digest` and `dump` imported from
`ciw.measurement_evidence`. Hash this canonical helper serialization, not an
arbitrary JSON fragment. Recompute the binding only after reviewing any scope
change, then update the calibration-file descriptor hash.

The model is `affine_canonical_readout`. First normalize the input readout and
standard uncertainty to canonical units, obtaining `x, u`; then calculate
`y = a*x + b`. `input_min/input_max` are bounds in the channel's **input unit**.
`scale=a`, `offset=b`, the coefficient covariance ordered `[a,b]`, and
`residual_standard_uncertainty` refer to the **canonical calculation basis**.
A coefficient covariance fitted against raw Celsius, grams or millimetres
cannot be copied unchanged when the regression basis changes.

Pointwise variance combines `(a*u)^2`, `[x,1] C [x,1]^T` and residual variance.
Reference rows additionally include the supplied `placement`, `timing`,
`environment` and `other` standard-uncertainty terms in canonical units.
These additions require reviewed, disjoint independent components. The
uncertainty-budget assumption supported here is `independent_disjoint_components`,
with reviewer and evidence descriptor; other cross-component covariance blocks
eligibility. Unknown uncertainty is not zero.

The supplied 2×2 coefficient covariance must be symmetric positive semidefinite.
Shared coefficient uncertainty is not reduced by row count. No aggregate
confidence interval or coverage probability is established. Material temporal,
cross-channel or reference/candidate correlation requires a suitable specialized
model; record shared standards, environment, clocks and processing explicitly.

Calibration requires certificate and `validity_policy` evidence descriptors,
a traceability statement, fit-run IDs and an applicability period covering the
whole acquisition. The dates implement the supplied policy; do not invent an
annual expiry. [NIST calibration FAQ](https://www.nist.gov/pml/sensor-science/optical-radiation/faqs-spectral-responsivity-calibrations)
Traceability belongs to a result and requires the calibration chain and its
uncertainties; it alone does not establish fitness.
[JCGM VIM, traceability](https://jcgm.bipm.org/vim/en/2.41.html)

## Independent reference custody and freezes

Assign whole runs to `development` or `held_out`, preserving repeat samples
together. Identify condition and specimen. The policy requires `split_unit`
of `run` or `specimen`; specimen partitioning refuses a specimen shared across
development and held-out runs. A run split may reuse specimens and supports
only the narrower reviewed claim, not generalization to unseen specimens.
`min_held_out_runs` counts runs, not independent specimens; repeated runs on one
installed specimen do not establish population coverage.
Development runs have no reference or estimate descriptors in this evaluator;
keep development targets separately.
Observation/reference sensor, instrument and readout identities must differ,
and held-out run IDs must not appear in calibration fitting.

Retain the independent reference's original exports, calibration, processing
and custodian records. Distinct IDs or bytes do not prove independent errors.
Record shared effects in the independence review and uncertainty assessment.

The frozen policy binds run partitions, model artifact, prediction hashes and
criteria hash. Compute the latter as `digest(dump(manifest["criteria"]))`.
The required time order is `split <= model <= predictions < reference unsealed`;
the split and agreed criteria precede held-out acquisition, the model is frozen
no later than its start, and predictions are frozen no earlier than its end.
Reference targets must not have been used for fitting.

Hash and timestamp declarations are operator assertions with local consistency
checks, not trusted custody authentication. If evaluation changes the model or
thresholds, retain that history and obtain a fresh held-out assessment.

## Agree criteria and interpret the result

Leave numerical limits blank until scope, capability and intended use are
agreed. Criteria must use canonical units and cover minimum whole-run count,
required condition labels, observed/reference fractions, reference span,
bias/RMSE/maximum-error limits and reference expanded-uncertainty limit.
The agreement record identifies who agreed and when; the adapter cannot
authenticate that authority.

The implemented rule is `conservative_pointwise_reference_guard`: guarded
point errors are `abs(estimate-reference) + k*u_reference`; guarded RMSE uses
those values, and guarded absolute bias adds `k*mean(u_reference)` to absolute
bias. These are declared reference-uncertainty guards, not confidence bounds
on prediction error. No numerical limits or coverage factor are pre-agreed.
[JCGM 106:2012](https://www.bipm.org/en/doi/10.59161/jcgm106-2012)

| CLI result | Meaning |
| --- | --- |
| Exit 0 for catalog/init/init-all | Requested software operation completed |
| Adapt exit 0; `passed_declared_criteria` | Supplied evidence passed the declared checks/limits; physical validation remains unestablished |
| Adapt exit 2; `not_evaluated` | Missing evidence, synthetic evidence or another eligibility blocker; inspect `blockers` |
| Adapt exit 2; `failed_declared_criteria` | Eligible supplied evidence failed one or more declared limits |
| Exit 1; stderr `status: refused` | Invalid schema, hash, binding, numeric input, chronology or filesystem operation |

A valid adaptation retains `campaign.json`, hash-named exact inputs under
`raw/`, `inference-input.json`, `reference-evaluation.json` and `report.json`.
The report is written last. Use a fresh output directory after a refused write.
`processing_status: valid` describes parsing/processing; `evidence_status` is
`blocked_missing_evidence` or `eligible_for_review`. The latter alone does
not mean limits passed. Every report keeps `physical_validation: not_established`
and `release_status: unchanged`.

## Specialized families remain pending

GPU counters need boundary, reset/wrap, clock and independent-energy reference
work. Vibration/RMS/waveforms need dynamic response, sample/filter conventions,
axes and covariance; vision needs calibrated geometry. Optical/spectral/IR,
electrical DC/AC and acoustics need their own transfer models and references.
Raw resistance, voltage, counts, pulses and force/strain bridge outputs are not
engineering-readout conversions in this adapter.

NIST force calibration may bind the entire readout chain or a voltage ratio;
dynamic accelerometers need frequency-dependent sensitivity and phase.
[NIST force](https://www.nist.gov/programs-projects/calibration-force-transducers),
[NIST vibration](https://www.nist.gov/programs-projects/dynamic-mechanical-metrology-acceleration-force-and-acoustics/vibration)
Optical responsivity depends on wavelength, and flow conversion can require
composition, pressure, temperature and an equation of state.
[NIST optical response](https://www.nist.gov/programs-projects/spectral-responsivity-measurements-and-spatial-spectral-power-responsivity),
[NIST gas flow](https://www.nist.gov/laboratories/tools-instruments/gas-flow-standards)

Report software completion, physical evidence availability, reference review
and agreed-criteria assessment separately. None authorizes a release change.
