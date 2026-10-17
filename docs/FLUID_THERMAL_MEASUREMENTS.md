# Fluid thermal measurements: first physical campaign

This is a proposed measurement campaign and an experimental data-intake path.
The release remains unchanged. No real measurements, calibration certificates,
independent physical reference results, or agreed acceptance criteria have been
supplied. Software checks and example fixtures do not close those gaps.

The first increment imports temperature files, applies supplied calibration
information, and compares supplied estimates with measured references. It does
not introduce a thermal solver, connect to a device, calibrate an instrument by
itself, or establish physical validity. The existing reservoir and wave checks
remain numerical checks of their declared mechanical models; this campaign does
not add heat transport to them.

## Proposed first scope

Start with liquid water in one identified installation and a **local fluid
temperature at a specified probe location and observation time**. Record probe
position, immersion, mounting, sample averaging window, and response behavior.
A preliminary operating envelope is 20–60 °C at ambient pressure; the generated
draft uses 293.15–333.15 K and 90–110 kPa. These are planning assumptions,
adjustable before campaign agreement, not validated limits or acceptance
requirements. The adapter accepts declared temperature bounds only within
278.15–353.15 K (5–80 °C) and pressure bounds within 80–120 kPa. These wider
software bounds also do not establish physical validation. Record actual
pressure and its basis rather than replacing it with an assumed standard
atmosphere.

This initial target is deliberately narrow. A local reading becomes evidence
about bulk water temperature only after spatial surveys, mixing conditions,
sensor placement, and their uncertainty support that interpretation. Changes to
fluid composition, installation, flow regime, geometry, phase, or envelope need
an explicit scope review. Thermal imaging of the wall, heater set points, and
ambient temperature do not by themselves measure the same local fluid target.

For this campaign, write down what the estimate claims to predict: probe-local
temperature, a defined temporal average, or a later justified bulk quantity.
Use the same definition for the reference comparison. JCGM GUM-6 distinguishes
the measurand from model inputs and treats measurement effects and model
adequacy as part of developing the measurement model. Its approach supports
making this definition explicit before interpreting a residual as an error.
[JCGM GUM-6:2020](https://www.bipm.org/en/doi/10.59161/jcgmgum-6-2020)

## Stage 1 — establish the measurement chain

Identify the sensing probe and its readout/logger as a measurement system.
Record serial numbers, channel identities, firmware/configuration, conversion
settings, units, timestamps, and any filtering already applied by the logger.
Use a separate reference probe and separate reference logger with their own
identities and calibration evidence. Record any shared standards, bath,
calibration provider, timing source, power supply, or processing code that could
create common effects; separate devices do not guarantee independent errors.

Obtain actual calibration evidence appropriate to the proposed temperature
range. A usable record includes the calibrated configuration, date, stated
reference and traceability chain, calibration points, correction convention,
valid range, uncertainty and coverage information, and relevant environmental
conditions. Document whether the calibration applies to the complete
probe/logger system or how separately calibrated components are combined.
Pre-campaign and post-campaign checks can reveal drift; define how a failed or
changed check will affect already collected runs before collecting those runs.

Traceability belongs to a measurement result. It requires a documented chain
of calibrations with uncertainty contributions, and cannot be established from
a brand name, certificate filename, accreditation label, or a calibrated probe
alone. Application conditions and the rest of the measurement process still
matter. [NIST, Metrological Traceability: Frequently Asked Questions and NIST
Policy, TN 2156](https://www.nist.gov/metrology/metrological-traceability)

The first adapter accepts the logger's temperature readout in `K` or `degC`.
Here “raw” means the retained, unaltered temperature export before this
adapter's correction, not an unprocessed electrical signal. Resistance, voltage,
thermocouple millivolts, bridge ratios, and ADC counts need a separately defined
and tested conversion chain and are outside this increment. Record any
instrument-side conversion and correction to avoid correcting twice. Do not
fit a convenient correction from the held-out reference run.

## Stage 2 — characterize the installation

Use development runs to learn which effects dominate before setting
confirmatory run counts or claiming uncertainty coverage. The proposed
characterization should include:

- Repeated steady plateaus across the candidate envelope, approached from
  warming and cooling, with the actual stability window retained.
- Modest warming and cooling transients to characterize sensor lag and timing
  alignment; retain the unshifted observations as well as derived comparisons.
- Separate restarts and, where practical, different days or probe reinsertions
  to reveal between-run variation hidden by a single long trace.
- A spatial check around the intended probe location and, if bulk temperature
  will be claimed, representative positions in the water under the intended
  mixing or flow conditions.
- Logged ambient conditions, pressure, fill/mass or level, geometry, mixing or
  flow setting, heater input if used, and any intervention that changes the
  thermal conditions. Retain units and measurement provenance for each.

Set acquisition rate and duration from observed dynamics and the intended
prediction use. A large row count is not a large number of independent trials.
Choose settling criteria, transient windows, time-alignment method, missing-data
handling, and exclusion rules using the development material, then freeze them
before confirmation. Keep excluded and interrupted runs with reasons.

If density, heat capacity, enthalpy, or other water properties later enter a
model, record the property formulation, implementation/version, input state,
validity domain, and associated uncertainty. IAPWS-95 is a formulation for
water's thermodynamic properties; it supplies neither the actual apparatus
temperature nor an independent physical validation record. Agreement with a
property table does not validate heat losses, mixing, sensor lag, or inferred
temperature in this installation.
[IAPWS R6-95(2018)](https://www.iapws.org/relguide/IAPWS-95.html)

## Stage 3 — freeze development and reserve whole runs

Keep instrument calibration, model development, and confirmatory evaluation
distinct. Record every run's role at collection or before its results are used.
Hold out whole independently initiated runs; do not randomly split neighboring
time samples from a single thermal trace into development and validation sets.
Where generalization across days or configurations is part of the claim, reserve
those complete groups as well.

The reference should measure the same defined target closely enough that
position and response differences can be quantified, without using the
estimator's temperature output as its measurement. Retain reference readings in
their own raw export and calibration chain. A synthetic trace, analytical
solution, estimator output, or second calculation using the same inputs is not
this physical reference.

Before anyone developing the estimate opens confirmatory reference values:

1. Freeze the estimator implementation, parameters, calibration treatment,
   preprocessing, scope, and comparison protocol with version identities and
   content hashes.
2. Record the chosen held-out run identifiers before those runs begin and keep
   their reference data unavailable to development. The adapter requires its
   split-freeze timestamp to precede reserved acquisition. An independent
   custodian or restricted storage provides evidence stronger than an unchecked
   declaration.
3. Generate and retain predictions using only inputs allowed by the intended
   operating use. Seal their files and timestamps before releasing reference
   values for evaluation. For an online claim, also establish that future input
   observations were unavailable to each prediction.
4. Retain who released the reference data, when, and which frozen predictions
   and protocol were evaluated.

Hashes can identify files and reveal subsequent changes; a hash alone proves
neither data authenticity nor that a developer never saw the reference. Record
the actual access separation. If reference results drive a model, calibration,
threshold, lag, or filtering change, those runs become development evidence.
Use fresh held-out runs to confirm the revised version.

The current module does **not** implement that access separation. Its `adapt`
operation reads the observations, reference data, and predictions in one
evaluation. The resulting `inference-input.json` excludes reference targets,
but the same bundle contains `reference-evaluation.json` and retained raw
reference bytes. Exporting the inference file is not a blinded workflow and
does not establish that inference preceded reference access. Collect,
preprocess, and freeze the allowed inputs and predictions under the external
separation procedure before unsealing references and running this evaluator.
There is no separate pre-unsealing intake command in this increment. For an
observation-only preparatory export, leave the run's `reference` and `estimates`
fields `null` and leave unavailable policy/criteria evidence `null`. `adapt`
then retains and, when calibration is supplied, corrects the allowed
observations while producing a blocked readiness report. Keep reference files
outside that preparation directory. Freeze the predictions under the external
procedure, then supply the released reference files in a subsequent campaign
manifest and a new output bundle. A blocked intake report is expected during
this preparation stage.

## Stage 4 — agree criteria before confirmatory evaluation

Acceptance remains **unagreed**. Unset numeric limits are `null`, never zero,
and no example value is an approved requirement. The following worksheet is
for discussion; it is not a completed acceptance specification.

| Decision item | Current value | What must be settled |
| --- | --- | --- |
| Intended use and local measurand | Proposed only | Location, time/window, operational purpose, and supported scope |
| Temperature and operating envelope | Proposed 20–60 °C, ambient pressure | Agreed bounds and excluded regimes |
| Signed temperature bias limit | `null` | Absolute units, population/strata, interval and decision rule |
| Temperature error magnitude limit | `null` | MAE/RMSE/quantile or another chosen metric, aggregation, and bound |
| Transient response requirement | `null` | Lag/dynamic metric, relevant windows, and allowable behavior |
| Reference uncertainty adequacy | `null` | Uncertainty budget and comparison fitness for the chosen limits |
| Prediction-interval claim | `null` | Whether made, intended coverage, width, and evaluation method |
| Confirmatory sample size | `null` | Independent run count and allocation derived from pilot variability |
| Incomplete-run treatment | `null` | Missingness, exclusions, required strata, and minimum usable evidence |
| Decision rule and review record | Unagreed | Treatment of uncertainty, inconclusive results, and recorded agreement |

Use characterization results and the intended effect/error margins to design
the confirmatory sample size. Count independent runs or justified groups in
that calculation; preserve within-run correlation. Avoid choosing the sample
size, tolerances, preferred metric, or successful subset after viewing the
confirmatory result. Additional collection after an inconclusive result needs
a recorded rule, including how repeated looks are handled.

The criterion must state how uncertainty affects the decision and what an
inconclusive result means. JCGM 106 discusses conformity decisions against
specified requirements, including acceptance limits and risks associated with
measurement uncertainty. It does not supply the intended-use tolerances for
this apparatus. Those remain to be agreed by the people responsible for the
claim. [JCGM 106:2012](https://www.bipm.org/en/doi/10.59161/jcgm106-2012)

## Uncertainty and correlation

Maintain a component budget for each measurement chain. Candidate contributors
include calibration/correction uncertainty, readout resolution, repeatability,
drift, probe immersion and stem conduction, spatial mismatch, response time,
timestamp alignment, and environmental effects. Include a contributor only
once, and explain how its value was obtained. Unknown contributions remain
gaps rather than being silently assigned zero.

JCGM 100 distinguishes statistical (Type A) and other (Type B) uncertainty
evaluations, combines uncertainty through a measurement model, and includes
covariances where inputs are correlated. Expanded uncertainty must retain its
coverage factor and interpretation. These principles support an explicit
budget; they do not make an instrument specification or a residual standard
deviation a complete uncertainty statement.
[JCGM 100:2008, sections 4–7](https://www.bipm.org/en/doi/10.59161/jcgm100-2008e)

For a difference `d = estimate - reference`, a compatible uncertainty model
has `u(d)^2 = u(estimate)^2 + u(reference)^2 - 2 cov(estimate, reference)`.
That covariance needs a basis, especially when the chains share calibration
standards, ambient effects, timestamps, or model inputs. Separate hardware is
useful but does not justify setting covariance to zero automatically. Report
both marginal uncertainty and the assumptions used for the comparison.

Calibration offsets and slow drift can correlate many samples. Repeating a
sample does not remove a shared calibration contribution. Temporal correlation
also affects confidence intervals on bias, RMSE, and coverage. Use a justified
run-level or dependence-aware analysis for claims about repeated operation;
report how many independent runs support it. A per-sample uncertainty propagated
by this adapter is not a complete covariance model or a confidence interval for
campaign-wide performance. Any simplifications in the adapter's calculation
must remain visible in the report.

The adapter currently implements only an affine temperature correction. Let
`x = raw_temperature + 273.15` for `degC` input, or `x = raw_temperature` for
`K` input. Then `temperature_k = scale * x + offset_k`. The calibration's
`input_min` and `input_max` apply to the original readout unit; coefficient
fitting and `offset_k` use the Kelvin form of this equation. A fit originally
written as `a * temperature_degC + b_degC` must be converted to this convention
before use; its intercept cannot simply be copied.

With `a = scale`, coefficient covariance `C` ordered as `(scale, offset_k)`,
and row standard uncertainty `u_raw`, the propagated pointwise variance is
`[x, 1] C [x, 1]^T + (a * u_raw)^2 + u_residual^2`. Reference rows additionally
receive the squared `installation`, `timing`, and `dynamic` standard
uncertainties from the run. Temperature differences in K and °C have equal
numerical size. The code retains the scale–offset covariance but assumes the
raw-readout, coefficient-fit, residual, and extra components are disjoint and
mutually uncorrelated. Supply a reviewed budget consistent with that assumption;
do not double count calibration residuals or hide unknown contributions as
zero. Unsupported dependence requires further modeling, not a relabelled input.

The supplied prediction CSV carries no prediction uncertainty, and the module
does not propagate a joint estimate–reference covariance. Its pointwise
reference guard is therefore not the general uncertainty-of-difference formula
above, a prediction interval, an empirical coverage result, or a confidence
bound on run-level bias/RMSE. Shared coefficient uncertainty is not divided by
sample count.

Report residuals by run, temperature range, direction of change, and operating
condition as applicable. Separate stable and transient behavior if their
measurement models differ. Prediction-interval coverage, if claimed, needs its
own held-out assessment; a confidence interval for mean bias is a different
quantity.

## Retained evidence and meaning of a result

Retain original device exports unchanged; calibration records and correction
provenance; setup descriptions and probe locations; run logs; clock checks;
development/held-out assignments; all rejected rows and exclusion reasons;
frozen model and prediction identities; reference release records; agreed
criteria if any; normalized data; and report/code versions. Preserve content
hashes with the bundle and retain the underlying evidence, not just paths to
files that may later disappear. Assign an evidence owner, storage location,
access policy, and retention period as campaign decisions. No fixed retention
period has been agreed here.

An adapter success means the supplied files met its implemented intake checks
and a reproducible report could be produced. It cannot verify a certificate's
authenticity, reconstruct undocumented device processing, prove physical
independence, establish a complete uncertainty budget, or infer acceptance
criteria. Descriptive error metrics remain descriptive while criteria or
evidence are missing. Even a future comparison against agreed criteria applies
only to the frozen configuration and declared scope; it is not an automatic
physical-validity or release pass.

## Run the development adapter

Use an environment in which this checkout's `ciw` package is available:

```sh
python -m ciw.fluid_thermal_measurements init --output-dir water-campaign
python -m ciw.fluid_thermal_measurements adapt --manifest water-campaign/campaign.json --output-dir water-bundle
```

Both output directories must be new; existing directories are not overwritten.
`init` creates an unapproved skeleton with no measured runs and CSV headers
without fabricated observations. Populate it with real evidence before drawing
conclusions. Running `adapt` on the untouched skeleton can produce a blocked
report showing the missing evidence. `init` exits `0`; `adapt` exits `0` only for
`passed_declared_criteria`, `2` for other processed assessment states, and `1`
for refused input or an I/O error. Exit `0` still does not establish physical
validation or authorize a release change.

The bundle contains `campaign.json`, `report.json`, `inference-input.json`,
`reference-evaluation.json`, and original evidence bytes under
`raw/<sha256-hex>.bin`. Calibration absence can leave observation rows in raw
form; consumers must not assume every inference row is calibrated. The report
always states `physical_validation: "not_established"` and
`release_status: "unchanged"`. A valid processed report may be blocked by
missing evidence; `eligible_for_review` is a review state, not certification.

## File contract and examples

Manifest schema is `ciw.fluid-thermal-measurements.v1`. Its exact top-level
fields are `schema`, `campaign_id`, `evidence_kind`, `scope`, `channels`, `runs`,
`held_out_policy`, `criteria`, and `uncertainty_budget`. `evidence_kind` is
`measured` or `synthetic`; synthetic inputs always block physical-evidence
eligibility. `scope` contains `fluid: "water"`, `phase: "liquid"`,
`measurand: "local_fluid_temperature"`, `installation_id`,
`mixing_description`, `min_temperature_k`, `max_temperature_k`,
`min_pressure_pa`, and `max_pressure_pa`. Bulk-temperature inference is outside
this version. Each of `channels.observation` and `channels.reference` has
`instrument_id`, `logger_id`, `location`, and `calibration` (a descriptor or
`null`). Reference and observation instrument and logger identities must
both differ for eligibility.

Descriptors identify retained files by relative path and content hash:

```json
{"file": "observation.csv", "sha256": "sha256:<64 lowercase hexadecimal digits>"}
```

This is a placeholder, not a usable digest. Calculate the real digest after
finalizing each file. For example, in PowerShell:

```powershell
$evidenceHash = (Get-FileHash -LiteralPath '.\water-campaign\observation.csv' -Algorithm SHA256).Hash.ToLowerInvariant()
"sha256:$evidenceHash"
```

The path must resolve to a file inside the campaign directory and cannot point
to the manifest itself. Evidence and manifest files are limited to 8 MiB each,
with a 64 MiB total retained-evidence budget. JSON fields are exact: duplicate,
missing, and unexpected keys are refused where the schema is checked. Unknown
values are not automatically inferred.

Observation and reference CSVs use the exact ordered header
`timestamp_s,temperature,standard_uncertainty,quality`. `quality` is `valid` or
`missing`; a missing row keeps both numeric value fields empty. Uncertainty is
a nonnegative **standard** uncertainty in the readout's temperature unit.
Prediction CSVs use `timestamp_s,temperature_k`. Files need actual rows for
evaluation; a header-only template is not measured evidence. At most 20,000 rows
per file and 100 runs per campaign are accepted. Timestamps are strictly
increasing elapsed seconds from the run's declared acquisition start, in the
range 0–86,400. Observation, reference, and prediction timestamp arrays must
match exactly: the adapter does no implicit interpolation, lag fitting,
resampling, or synchronization.

The examples below show full nested field names. `null` values and angle-bracket
placeholders mark missing real evidence; these fragments are **not** a
completed, executable campaign. Fill them from retained records, not invented
measurements or nominal instrument specifications.

A held-out element of `runs` has this shape:

```json
{
  "run_id": "<reserved-run-id>",
  "split": "held_out",
  "phase": "heating",
  "fluid_batch_id": "<actual-batch-id>",
  "acquisition_start_utc": null,
  "acquisition_end_utc": null,
  "pressure_pa": null,
  "observation": {"file": "observation.csv", "sha256": "sha256:<digest>"},
  "reference": {"file": "reference.csv", "sha256": "sha256:<digest>"},
  "estimates": {"file": "estimates.csv", "sha256": "sha256:<digest>"},
  "reference_extra_uncertainty_k": {
    "installation": null,
    "timing": null,
    "dynamic": null
  }
}
```

`phase` is `heating`, `cooling`, or `hold`; `split` is `development` or
`held_out`. A development run uses `reference: null` and `estimates: null` in
this evaluator. Run duration must be positive and no more than one day, and
pressure must fall within declared bounds. Acquisition times use explicit UTC,
such as an actual recorded ISO timestamp ending in `Z`. All run IDs are unique.
The code rejects reuse of identical observation/reference bytes as separate
runs, but changed bytes alone do not prove that acquisitions were independent.

A calibration descriptor points to JSON with this shape, separately completed
for each channel:

```json
{
  "schema": "ciw.fluid-temperature-calibration.v1",
  "calibration_id": "<actual-calibration-id>",
  "instrument_id": "<same-as-channel>",
  "logger_id": "<same-as-channel>",
  "input_unit": "degC",
  "input_min": null,
  "input_max": null,
  "scale": null,
  "offset_k": null,
  "coefficient_covariance": [[null, null], [null, null]],
  "residual_standard_uncertainty_k": null,
  "valid_from_utc": null,
  "valid_until_utc": null,
  "certificate": {"file": "calibration-certificate.pdf", "sha256": "sha256:<digest>"},
  "traceability_statement": null,
  "fit_run_ids": []
}
```

Use `K` or `degC` for `input_unit`. Coefficient covariance must be symmetric
positive semidefinite and ordered as `(scale, offset_k)`. The instrument/logger
binding, validity interval across acquisition, readout range, and corrected
temperature against scope are checked. `fit_run_ids` identifies every campaign
run used in fitting; none may be held out. An empty list is appropriate only if
no campaign runs were used, with external calibration provenance retained.

`held_out_policy` has this shape:

```json
{
  "development_run_ids": [],
  "reserved_run_ids": ["<reserved-run-id>"],
  "model_sha256": "sha256:<digest-of-frozen-model-artifact>",
  "model_artifact": {"file": "frozen-model.json", "sha256": "sha256:<same-model-digest>"},
  "prediction_sha256_by_run": {"<reserved-run-id>": "sha256:<estimates.csv-digest>"},
  "criteria_sha256": null,
  "model_frozen_at_utc": null,
  "predictions_frozen_at_utc": null,
  "reference_unsealed_at_utc": null,
  "split_frozen_at_utc": null,
  "reference_used_for_fit": false,
  "independence_statement": null,
  "reviewed_by": null
}
```

The run-ID lists must equal the declared complete partition. The retained model
artifact must match `model_sha256`; retain code, parameters, preprocessing, and
version identities sufficient to identify the actual frozen estimator. For each
supplied held-out prediction file, `prediction_sha256_by_run` binds the run ID
to that file's descriptor hash. A complete campaign supplies all held-out files.

The enforced chronology is `split <= model <= predictions < reference_unsealed`.
The split is strictly before every held-out acquisition, the model freeze is
no later than each held-out start, and prediction freeze is no earlier than
each held-out end. `reference_used_for_fit` must truthfully be `false`;
timestamps and review text are assertions requiring supporting external
evidence. Do not fill them simply to suppress a blocker.

`criteria_sha256` binds the complete criteria value using the adapter's
canonical JSON encoding: sorted keys, two-space indentation, default JSON
ASCII escaping, finite numbers, a final newline, and UTF-8. It is not the
manifest file's hash. Calculate it after finalizing the actual criteria:

```sh
python -c "import hashlib,json,pathlib; c=json.loads(pathlib.Path('water-campaign/campaign.json').read_text(encoding='utf-8-sig'))['criteria']; b=(json.dumps(c,indent=2,sort_keys=True,allow_nan=False)+'\n').encode('utf-8'); print('sha256:'+hashlib.sha256(b).hexdigest())"
```

A criteria hash binds content but does not create agreement or prove when it
was agreed. A draft or `null` value remains unagreed even if correctly hashed.

An explicitly unagreed `criteria` object is:

```json
{
  "status": "draft",
  "approved_by": null,
  "approved_at_utc": null,
  "decision_rule": null,
  "coverage_factor": null,
  "min_held_out_runs": null,
  "required_phases": null,
  "min_scored_fraction": null,
  "min_temperature_span_k": null,
  "max_abs_bias_k": null,
  "max_rmse_k": null,
  "max_abs_error_k": null,
  "max_reference_expanded_uncertainty_k": null
}
```

`criteria: null` also preserves the missing-agreement state. A draft produces
`criteria_result: "not_evaluated"`; no numeric limit is implied. Only after
actual agreement may `status` become `agreed`, with real reviewer/time records
and all required values. Agreement must precede both confirmatory acquisition
and reference unsealing. The sole implemented decision rule is
`conservative_pointwise_reference_guard`: for each scored sample it uses
`abs(error) + coverage_factor * u_reference`, then checks per-run guarded
maximum error and RMSE, guarded absolute bias, reference uncertainty, scored
fraction, and temperature span. Required phases and run count must also be
present. The factor is a declared multiplier; its use does not establish
confidence or coverage. These checks cannot replace the characterization,
independence, uncertainty-budget, and scope review described above.

`uncertainty_budget` remains `null` until a real review record is available.
Its completed structure is:

```json
{
  "assumption": "independent_disjoint_components",
  "reviewed_by": "<actual-reviewer>",
  "evidence": {"file": "uncertainty-budget.pdf", "sha256": "sha256:<digest>"}
}
```

The declaration requires a retained review record supporting the implemented
assumption of disjoint, mutually uncorrelated input, coefficient-fit, residual,
and extra components. The scale–offset covariance remains represented inside
the coefficient-fit component. A budget requiring additional correlation
handling blocks eligibility and requires an extended adapter. Review text and
a retained document do not automatically verify the budget's scientific
adequacy.

## Inputs needed to start with real data

- The fluid/composition, apparatus description, intended temperature target,
  proposed range, and normal mixing/flow conditions.
- A sample untouched temperature export, its column/unit definitions, device
  and channel identities, timestamp basis, and logger processing settings.
- Calibration/correction records for the sensing chain and separate reference
  probe/logger, including uncertainties and relevant valid ranges.
- Existing run logs and any pilot results, with which data have already been
  used for development clearly identified.
- The intended estimator and allowed inputs, plus the accuracy/dynamic/coverage
  claims the comparison should eventually support.
- The person or team who can settle the acceptance worksheet and the practical
  method for retaining unseen reference data until prediction freeze.

These inputs allow the adapter to be mapped to an actual export and the pilot
campaign to be tailored. Until they arrive, the implementation and this
protocol are preparation for physical evidence, not substitutes for it.
