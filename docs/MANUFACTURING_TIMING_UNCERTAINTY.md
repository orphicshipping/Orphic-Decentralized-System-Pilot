# Correlated timing uncertainty on the existing manufacturing model

`engineering.characterize-manufacturing-time.v2` is the opt-in uncertainty-aware
extension of the existing timing characterization. The original v1/v2 planner and
`engineering.characterize-manufacturing-time.v1` remain available and retain their
original data. No observation, manufacturing, acquisition or execution store is added.

```sh
python -m ciw.sensor_manufacturing_cli run inputs request.json \
  --timing-uncertainty --output-dir uncertainty-session
python -m ciw.sensor_manufacturing_cli inspect uncertainty-session
python -m ciw.sensor_manufacturing_cli report uncertainty-session --output uncertainty.html
python -m ciw.sensor_manufacturing_cli replay uncertainty-session --output-dir replay
python -m ciw.sensor_manufacturing_cli export-inputs uncertainty-session --output-dir restored
```

All output paths must be new. Reports are outside the retained Session. The original
source inventory, payload validator, registry, Session, snapshot anchors, replay and
original-input export are reused. No machine, gauge or network is opened. No automatic
model promotion or inspection/manufacturing release is performed.

## Statistical scope

The original descriptive fit remains elapsed seconds = setup + batches * batch time.
Its means are not refitted or weighted. For training design X, the unchanged OLS
estimator is beta = H y_train, where H = (X^T X)^-1 X^T. Exact rational influence
rows are calculated directly for the two-parameter model, not by a numerical inverse.

A single supplied joint covariance C covers all original duration observations,
including held-out trials and all characterized operations. Rows of a linear map W
represent setup, batch time, target conditional means, and held-out residuals:

- Parameter rows use H on training coordinates and exactly zero on held-out coordinates.
- A target row is [1, target_batch_count] H.
- A held-out residual row is e_holdout - [1, heldout_batch_count] H.

The propagation is W C W^T. Signed training/holdout and cross-operation terms remain
in the calculation. Source matrices are not diagonalized, symmetrized, clipped or
repaired. Singular positive-semidefinite covariance is allowed. The native covariance
validator runs unchanged; this exact-rational provider additionally requires exact
symmetry and positive semidefiniteness. A near-indefinite matrix tolerated by generic
floating validation therefore can refuse this provider without changing old readers.

The original covariance artifact is retained, and derived covariance uses the existing
`covariance-artifact.v1` with source covariance ancestry, coordinate labels, original
means and units. Exact rational matrix and influence-map sidecars preserve arithmetic
that a JSON floating representation cannot express exactly. Nonzero underflow or
nonfinite output is refused rather than silently reported as zero. The additional
full-joint profile is bounded to 64 trial axes; the previous larger timing path is
unchanged and does not acquire this bound.

This propagates only the supplied measurement-error model. It is not GLS, a residual
noise estimator, a new calibration, a total future-duration uncertainty model or proof
that a supplied covariance is empirically correct. Missing process discrepancy,
between-run variation, covariance-estimation uncertainty and uncertain batch counts
remain outside this increment. Counts are exact *declarations*, not authenticated
machine counts. Held-out values never enter the mean fit.

## Explicit input files

Continue using the unchanged sensor-project request and timing protocol/study. Add a
`model.input` artifact called `manufacturing-timing-uncertainty.json` with exactly:

```json
{
  "schema": "ciw.manufacturing-timing-uncertainty.v1",
  "study_ref": "sha256:<original timing study bytes>",
  "protocol_ref": "sha256:<original timing protocol bytes>",
  "covariance_path": "timing-covariance.json",
  "coverage_multiplier": 2
}
```

The multiplier must be a finite explicit number in (0,10], not a boolean. The named
covariance file is a separate retained `model.input` containing the native covariance
artifact. Its ordered axes are the original observation digests in the timing result's
series/sample order: operations sorted lexically, samples in original protocol order.
The vector contains every trial, including held-out trials; no subset, duplicate axis,
implicit reordering or independent-block assumption is accepted.

All units must be `s`, and the vector frame is `manufacturing.timing.duration-vector`.
Reference values must match the exact selected original duration values. Its basis is
`{"kind":"observation","id":<content identity>}`, where that identity is computed by
NET over `{"protocol_ref": original_protocol_ref, "observation_refs": ordered_digests}`.
The provenance's `source_evidence_ids` includes the original study/protocol byte hashes
and every original observation digest. Any already-supplied scalar covariance must
agree exactly with the joint diagonal and appear in `source_covariance_ids`.

Unknown uncertainty is valid. With no options file, or with `covariance_path: null`,
the original descriptive characterization is retained and joint uncertainty remains
`JOINT_COVARIANCE_NOT_SUPPLIED`. Even having all scalar marginal uncertainties does
not establish the missing cross-trial dependence. No diagonal matrix is invented.

A joint error model added after acquisition is a separately supplied declaration.
Original observations, null fields, covariance and provenance are never rewritten.
Hashes establish retained relationships, not authenticity, prospective registration,
calibration or the statistical validity of the supplied uncertainty model.

## Held-out checks and the shared-error trap

Each held-out residual carries its propagated standard uncertainty and a diagnostic
band r +/- k*u. Exact squared comparisons distinguish a band wholly inside, wholly
outside or overlapping the protocol's unchanged nominal absolute-error limit. Bands
are marginal diagnostics, not simultaneous coverage or acceptance decisions. Choosing
k=2 alone does not establish 95% coverage, a normal distribution or future prediction.

A review candidate requires all original eligibility conditions and all held-out
bands within the original limit. Increased uncertainty cannot override a failed
original held-out criterion. A nonzero residual with exactly zero modeled residual
variance is explicitly marked inconsistent and cannot become a candidate. Rank
deficiency keeps the original covariance but does not invent coefficient uncertainty.

A shared additive timing error illustrates why this matters. It affects the fitted
setup and target mean but can cancel from residuals because the training and held-out
measurements share the same error. Good held-out residuals alone do not establish
absolute prediction accuracy. The tests demonstrate both exact singular cancellation
and cancellation with a separate independent component.

## Route contribution and boundaries

The result also retains joint covariance between target operation means. Where the
original fixed-dispatch update is eligible under the additional uncertainty review
and has one strict active path, the route sensitivity is the sum of the affected
operation influence rows on that path. Cross-operation covariance is included.
A tied active path withholds this derivative instead of choosing one arbitrarily.

This is a **local measurement component** at the fitted means. Other operation times
are conditioned on their declarations. Path switching is not bounded, and no route
prediction interval or total route uncertainty is supplied. The report explicitly
separates the original baseline schedule, descriptive timing correction, conditional
uncertainty review and missing future-process uncertainty. The old process comparator
continues to reject non-v2-planner sessions; a timing correction is not a speedup.

## Executed synthetic investigations

The private handoff uses unchanged original MEMS A1 files and original synthetic
PR217 duration observations. It adds only error-model/configuration files. The
one-site law remains 36 s setup +44 s/batch, predicting 564 s for inspection and a
1544 s route. Its target standard uncertainty is about 0.185757 s under the authored
0.04*I covariance, versus 2.008608 s after adding 4*11^T. Held-out residual uncertainty
is identical in those two scenarios. Neither model is an empirical timing claim.

Other examples retain unknown covariance, broad uncertainty overlapping the nominal
limit, held-out drift, a rank-deficient design and the original four-site law. All
27 preexisting inputs remain byte-exact. Known covariance cases restore29 inputs;
the explicit-unknown case restores28. The 12 planned modules are still not physical
production records, and their inspections remain pending.

## Verification and CI repair

Tests use genuine NET Sessions and native covariance validation. Independent dense
linear-algebra oracles, signed and singular matrices, train/holdout separation,
scalar/joint conflicts, provenance and axis binding, exact boundary checks, missing
uncertainty, original bytes, replay and no-clobber are exercised. Original selected
suites are unmodified. Existing native EDA doubles remain explicitly doubles.

The dedicated existing workflow is extended to include the new test module. It also
fixes an independently reproduced environment bug: `.resolve()` on the virtualenv's
Python symlink selected the base interpreter and lost the virtualenv prefix. The
workflow now uses `.absolute()` for that interpreter path; wheel file resolution is
unchanged. A real temporary-environment regression verifies the preserved prefix.
This defect is demonstrated locally; it is not asserted to explain an inaccessible
hosted job's failure without logs.

The installed qualifier still demands unchanged dependency pins by default. Explicit
`--allow-host-dependencies` is development verification only. See the build report for
actual versions, source/wheel/install hashes, executed commands and remaining gates.

## Method references

The covariance propagation uses the correlated-input law described by NIST Technical
Note1297 AppendixA, exact here for the linear fit/contrast maps. NIST's expanded
uncertainty guidance distinguishes a chosen multiplier from justified coverage.
These references support the method, not validation of the Notation prototype:

- https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-law-propagation-uncertainty
- https://physics.nist.gov/cuu/Uncertainty/coverage.html

For the nonlinear route maximum, only the explicitly described local derivative is
used. No formal proof identity, physical qualification, equipment control or release
permission follows from the calculation.
