# Chemistry, polymer and materials measurement campaigns

The release remains unchanged. This development path compares **method-reported
batch endpoints** with independent method reports or certified reference
material (CRM) assigned values. Actual laboratory evidence, reviewed
uncertainty, independent held-out results and agreed criteria remain required.

The comparator normalizes corrected results. It does not calibrate signals,
apply an affine correction, generate predictions, run chemistry or reduce curves.
It establishes neither standard compliance/accreditation nor physical qualification.

## Preserve source history and proposed scope

The accompanying `chemistry-materials-chronology.json` separates verified
repository history from proposed laboratory families. Existing entries use
source-anchor first-add commits and author timestamps, not dates of equipment
ownership, method invention or physical qualification.

| Source order | Author date | Existing scope |
| --- | --- | --- |
| 1 | 2026-09-23 | Synthetic two-capacity thermal observer |
| 2 | 2026-09-26 | Abstract first-order A-to-B numerical reaction benchmark |
| 3–5 | 2026-10-02 | Elastic contact, plastic crush and spatial plate benchmarks |
| 6 | 2026-10-03 | Polymer cycle features and retained quantitative ingress |
| 7 | 2026-10-03 | Fluid/material inventory and boundary-transfer balance |

The abstract reaction benchmark establishes no experimental chemistry,
substance-specific mechanism, chemical calibration or reaction heat.
Spring stiffness is not material modulus; polymer feature ingress does not
qualify analytical or materials-testing equipment.

Proposed families have `null` historical dates; planning order is not chronology.
Registered families can include unsupported endpoints. Fluid/scalar campaigns
retain their existing scope.

## First endpoint profiles

Use `catalog` or generated `profile.json` for units, exact condition keys and
deferred families. Begin with a reported endpoint whose method context exists.

| Profile | Meaning and conditions that must match |
| --- | --- |
| `chemistry.amount_concentration.v1` | Named entity amount/solution volume; matrix, preparation, dilution and temperature |
| `chemistry.mass_concentration.v1` | Named analyte mass/solution volume; same identity, matrix and preparation basis |
| `chemistry.mass_fraction.v1` | Analyte mass/total sample mass; declared as-received, dry or wet basis |
| `chemistry.ph.v1` | Operational pH method; temperature, solvent composition, electrode and buffer calibration |
| `materials.bulk_density.v1` | Mass/defined bulk volume; void treatment, packing, conditioning and temperature |
| `polymer.apparent_shear_viscosity.v1` | Apparent shear viscosity at declared shear rate, temperature, geometry and sample history |
| `polymer.melt_mass_flow_rate.v1` | MFR at declared temperature, load, die, conditioning and cut method |
| `polymer.dsc_tg_midpoint.v1` | Assigned DSC midpoint; construction, baseline, scan rate, atmosphere, cycle and history |
| `polymer.dsc_melting_peak.v1` | Assigned melting peak; selected peak, baseline, heating rate, cycle and history |
| `materials.tensile_strength_engineering.v1` | Maximum force/original area; specimen geometry, orientation, rate and conditioning |
| `materials.tensile_modulus_chord.v1` | Engineering stress/strain chord over specified endpoints; strain method and compliance |

Amount concentration, mass concentration and mass fraction are different
quantities. No molar-mass, density, dilution, recovery or basis conversion is
inferred. A mass-fraction numerator must be part of its denominator; water mass
divided by dry-solid mass is a different quantity.
[IUPAC amount concentration](https://goldbook.iupac.org/terms/view/A00295),
[NIST SP 811](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication811e2008.pdf)

pH is activity-based, with operational measurement conventions; hydrogen-ion
concentration and electrode voltage are not interchangeable inputs. Finite pH
outside 0–14 is allowed. Compare absolute pH differences, not ratios or percent
errors. [IUPAC pH](https://goldbook.iupac.org/terms/view/P04524)

Onset, midpoint and peak temperatures are different endpoints. DSC and DMA
transition assignments differ. Engineering, true, yield and break stresses
differ. Tangent, secant, chord, dynamic and flexural moduli are distinct.
No MVR-to-MFR or kinematic-to-apparent-viscosity conversion is supplied.

## Start with generated drafts

Run in an environment with this checkout installed. Every output directory
must be new. Run `adapt` after populating evidence; an untouched draft produces
a retained blocked report, not a qualified result.

```console
python -m ciw.laboratory_measurements catalog
python -m ciw.laboratory_measurements init --profile chemistry.mass_concentration.v1 --output-dir work/lab-campaign
python -m ciw.laboratory_measurements init-all --output-dir work/lab-campaigns
python -m ciw.laboratory_measurements adapt --manifest work/lab-campaign/campaign.json --output-dir work/lab-bundle
```

`init` emits `campaign.json`, `profile.json`, `results.template.csv`,
`comparison.template.json`, `comparability-review.template.json`,
`uncertainty-budget.template.json`, `held-out-policy.template.json` and
`criteria.template.json`. It supplies no actual values, certificates or approval.
`init-all` creates one directory per endpoint, `catalog.json`, and
`specialized/<family-id>/intake-draft.json` for each deferred family.
Specialized drafts are intake worksheets, not runnable endpoint campaigns.

The manifest schema is `ciw.laboratory-measurements.v1`. Populate `candidate`
and `reference`, then copy completed comparison/review/policy/budget/criteria
objects into the manifest; companion templates are not loaded automatically.
Keep original reports and native raw artifacts alongside any mapped exports.

Descriptors use exactly `{"file":"relative/path","sha256":"sha256:…"}`, with
a full lowercase SHA-256 digest replacing the ellipsis. All paths resolve from
the campaign manifest directory and must remain inside it. Descriptors retain
exact bytes; they do not certify authorship or laboratory conduct.

## Scope and method evidence

`scope` declares `target_identity`, `matrix`, `reporting_basis`, the
canonical-unit `minimum/maximum`, profile `conditions` and a
`method_scope_evidence` descriptor. Condition values are reviewed, nonempty
strings; enumerated values must match the profile. These descriptions do not
perform environmental or preparation corrections.

Both roles bind the same scope using `scope_sha256 =
digest(dump(manifest["scope"]))`. Import `digest` and `dump` from
`ciw.measurement_evidence`; arbitrary JSON formatting yields a different hash.
A changed scope requires renewed applicability review and updated bindings.

A `method_report` role requires `reporting_stage:
final_corrected_method_result`, laboratory/method/revision and instrument IDs,
declared unit, results, nonempty raw/calibration evidence lists, and descriptors
for method validation, QC and preparation evidence. Record inclusive
`valid_from_utc/valid_until_utc` from supplied method applicability.
Every imported result timestamp must fall within that interval.

Only the reference may use `evidence_type: crm_assigned_value`, with
`reporting_stage: certified_assigned_value`. It requires a certificate,
applicability and material identity. This route does not demand fabricated
instrument, raw-signal or preparation records for a certified assigned value.
Any supplied additional evidence is retained.

Leave actual specifications unassigned until supplied: analyte/material, matrix,
lot/parent, preparation, method, instrument, range/limits, conditions, calibration,
uncertainty, reference and criteria. Naming a standard does not establish fitness.

Method suitability includes selectivity, range, precision, bias, sampling and
sample handling as appropriate.
[Eurachem method-validation guide, third edition (2025)](https://www.eurachem.org/index.php/component/content/article?id=144%3Agdmv2014)

## Final reported results and CSV correspondence

Blank, dilution, recovery, internal-standard, dry-mass, baseline and calibration
corrections must already be complete where applicable; document them upstream.
Never reapply corrections or count uncertainty components twice. Retain native
curves and their reduction record alongside the reported endpoint.

Both roles use exactly this ordered CSV header:

```csv
result_id,sample_id,parent_sample_id,lot_id,preparation_id,run_id,replicate_id,measured_at_utc,value,standard_uncertainty,qualifier,detection_limit,quantification_limit,reference_material_id
```

The first eight fields are required identifiers and an explicit UTC timestamp.
Result IDs are unique within a role. Values and **standard uncertainties** use
that role's declared unit; normalization converts each to the profile canonical
unit. Uncertainty is finite and nonnegative. Temperature offsets change values,
not uncertainties. Profile domains and the declared reporting range are checked.

Comparison records contain `comparison_id`, `candidate_result_id`,
`reference_result_id` and `split` (`development` or `held_out`).
Every imported row must be accounted for. Each candidate is used once; only
a CRM assigned reference value may be reused. A CRM row requires
`reference_material_id`. Repeat references do not create independent standards.

A `comparability_review` identifies reviewer/evidence and confirms
`same_measurand`, `compatible_methods` and `valid_specimen_pairing`.
Matching units or similar names cannot replace this review.
Different principles may provide references if they measure the same endpoint
under compatible conditions. Pairing destructive-test specimens needs an
explicit representative-sampling justification.

No uniform time grid or timestamp pairing is inferred. Tables admit at most
100000 rows per role; evidence-size limits also apply. Retain sample lineage.

## Preserve qualifiers and censoring

`quantified` requires numeric value and standard uncertainty. The qualifiers
`below_detection`, `below_quantification`, `missing`, `invalid` and
`out_of_range` require both numeric cells empty. Do not substitute zero,
half a limit, or the limit itself.

Detection/quantification-limit columns and below-limit qualifiers are supported
only for the concentration and mass-fraction profiles. Required limits are
positive, detection limit cannot exceed quantification limit, and a quantified
value cannot be below either supplied limit. Other profiles leave limit cells
empty and do not use below-limit qualifiers.

Only pairs with two quantified results enter error metrics. All held-out pairs
remain in the quantified-fraction denominator, globally and within each group.
The comparator does not estimate censored values or perform interval analysis.
Retain failed QC, interference and out-of-range outcomes with their qualifiers.

## Calibration separation and uncertainty

A CRM calibrant cannot independently validate the same procedure. Re-measuring
it checks stability or processing consistency, which is a different claim.
Retain calibration material/sample/lot identities and reference certificates.
Every held-out row's material ID is checked against both calibration-material
lists; a calibrant cannot be relabelled as validation material on either side.
[NIST SP 260-136-2021, section 2.13.5](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.260-136-2021.pdf)

Each role supplies a method-reported standard uncertainty. Expanded uncertainty
needs a documented upstream conversion using its actual coverage information.
Resolution, tolerance or injection repeatability alone do not define it.

The budget requires `assumption: independent_candidate_reference`, reviewer
and evidence. Under that reviewed assumption,
`u_difference = sqrt(u_candidate^2 + u_reference^2)`.
Identical candidate/reference result digests are refused. Shared raw-export or
calibration-evidence hashes, instruments or calibrant IDs require a specialized
covariance/independence model; the same laboratory/method is also refused.
Shared procedural guides are allowed. Different IDs do not prove independence.

Material covariance needs a specialized model. Repeat injections do not remove
shared effects; full uncertainty is not reduced by the square root of row count.
No confidence interval or coverage-probability claim is established.

## Hold out parent samples and lots

The policy's `split_unit` is `lot` or `parent_sample`; it selects candidate
group counting and per-group metrics. **Both parent samples and lots must
remain disjoint between development and held-out partitions across both roles**.
Use global identities: sample/parent IDs share one namespace, with lots separate.
Each material ID has one (original parent, lot) mapping; different-lot or nested
original-root conflicts are refused, including across role/field boundaries.
Keep aliquots/preparations/injections together; do not hide lineage with aliases.

Held-out sample/parent and lot IDs cannot appear in either role's calibration
IDs. Development/reserved group lists must match the comparison partitions.
Counting parent samples from one lot is not breadth across independent lots.
The report separately counts held-out candidate parent samples and lots.

Freeze split and method before the first held-out candidate measurement.
Freeze candidate results at or after the last such measurement and strictly
before reference unsealing. Agreement on criteria also precedes every held-out
candidate measurement. The policy binds method/independence evidence and exact
candidate/reference result-file hashes; `reference_used_for_development`
must be false for the held-out reference. Development candidate/reference
results precede method freeze; held-out reference results exist by unsealing.

Compute `criteria_sha256` with `digest(dump(manifest["criteria"]))`.
Hashes and operator timestamps check consistency, not trusted custody.
If targets change the method or thresholds, record the old evaluation as
development and obtain a fresh held-out assessment.

## Criteria, outputs and status

The supported rule is `independent_measurement_difference_guard`, with
canonical-unit limits. For `d = candidate-reference`, each guarded error is
`abs(d)+k*u_difference`. Within each lot/parent group, guarded RMSE uses these
values; guarded absolute bias is `abs(mean(d))+mean(k*u_difference)`.
Both method uncertainties contribute; these are descriptive guards.

Agree `min_held_out_groups`, `min_quantified_fraction`,
`min_reference_span`, bias/RMSE/maximum-error limits,
`max_combined_expanded_uncertainty` and `coverage_factor` before use.
No thresholds, certificates or agreement are invented. The quantified fraction
is checked globally and per group; the reference span is a global endpoint span.

| CLI result | Meaning |
| --- | --- |
| Exit 0 for catalog/init/init-all | Requested software operation completed |
| Adapt exit 0; `passed_declared_criteria` | Supplied evidence satisfied declared checks and limits |
| Adapt exit 2; `blocked_missing_evidence` | Missing/draft/synthetic evidence; inspect `blockers` |
| Adapt exit 2; `failed_declared_criteria` | Complete supplied evidence failed limits; inspect `failed_checks` |
| Exit 1; stderr `status: refused` | Malformed or incompatible inputs, hashes, lineage, chronology or filesystem operation |

A completed adaptation retains `campaign.json`, hash-named input bytes in
`raw/`, `candidate-input.json` containing **candidate rows only**,
`reference-evaluation.json` containing paired records, and `report.json`
written last. Refused partial output directories must not be reused.
Every report retains `physical_validation: not_established` and
`release_status: unchanged`, including when declared criteria pass.

## Specialized families remain pending

Raw LC/GC/MS, UV-visible/FTIR/Raman, titration, DSC/TGA/DMA, rheograms, SEC/GPC,
hardness, XRD/microscopy and mechanical curves need technique-specific
calibration, reduction, uncertainty and independent-reference models.
Supporting a reported endpoint does not implement its raw reducer.
Additional endpoints remain unsupported until registered. Report software,
evidence and comparison results separately; the release remains unchanged.
