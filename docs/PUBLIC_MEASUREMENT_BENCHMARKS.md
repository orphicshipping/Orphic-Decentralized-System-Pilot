# Public measured-data comparisons

The public evidence package now contains 30 actual published candidate results:
six liquid-density results and 24 ethanol mass-fraction results. This is a
retrospective report adapter. The prospective campaign adapters remain unchanged;
their missing instrument records, approvals and freeze events are not replaced
with invented historical metadata. The release remains unchanged.

## Results and interpretation

| Dataset | Imported evidence | Result | Qualification boundary |
| --- | --- | --- | --- |
| COOMET.M.D-S1 density | Six results, three liquids, two external laboratories | Four eligible rows: three satisfy the published strict En < 1 rule; INM tridecane does not | Reproduces the original comparison under its reported uncertainty model |
| SIM.QM-K27 chemistry | 24 primary results from 12 external laboratories at two ethanol levels | Descriptive k=2 intervals include zero for 19 rows and exclude it for five | Zero covariance is assumed; no prepublished numerical participant acceptance rule was found |
| Water thermometry CCT-K7 | Reference construction reviewed | Participant mean is not an independent reference for its own contributors | Not imported as a held-out qualification campaign |
| Polymer melt flow / DSC | Published measurement and calibration evidence reviewed | Reference intake and limitations recorded | Certification data cannot validate itself against the value it established |

Density uses VNIIM reference values assigned before dispatch. Candidate
traceability routes differ from the reference route in the report. Correlated
participants, the reference laboratory itself, and contradictory
tetrachloroethylene tables are excluded. Two AzMI water/oil rows remain visible
but are excluded from the eligible set because uncertainty entries disagree.
The repeated instrument serial in the source is not treated as verified hardware
identity. Sources: [COOMET report, Tables 11-13 and equations 5-6](https://www.bipm.org/documents/20126/48150881/COOMET.M.D-S1.pdf/65994b77-b295-bd49-53d6-403280ce0877).

Chemistry uses the independently prepared, purity-corrected gravimetric INMETRO
reference. The coordinator's analytical results and alternate LATU calibrant
results are excluded. Printed candidate coverage factors and U95 values are
preserved; they are not all 2. Source reporting/rounding inconsistencies are
retained. The illustrative interval calculation is not the report's narrative
equivalence decision; LATU level 1 exposes that distinction. Source:
[SIM.QM-K27 report, Tables 3-4](https://www.bipm.org/documents/20126/44673170/SIM.QM-K27.pdf/67f37e99-bbdf-6260-e865-a2da0909e69b).

## Reproducible execution

The portable package contains source transcriptions, normalized records,
manifests, completed reports, source URLs and hashes, and this adapter's Python
code. Original third-party reports are retrieved on demand rather than included
in the archive. Their exact bytes were retained during analysis.

From the portable package directory, with Python 3.11 or newer:

```text
python fetch_sources.py
python -m ciw.published_measurement_comparisons --manifest campaigns/density/campaign.json --output-dir my-density-reproduction
python -m ciw.published_measurement_comparisons --manifest campaigns/chemistry/campaign.json --output-dir my-chemistry-reproduction
```

Use a fresh output directory. Source changes or mismatched hashes are refused.
CLI exit 0 means reproduction completed, including when a published laboratory
result fails a rule. Inspect each row's status and the report counts.

The adapter computes signed candidate-minus-reference differences and
`U(d) = 2*sqrt(u_candidate^2 + u_reference^2)` under an explicit zero-covariance
assumption. It preserves reported values and recomputed values separately.
It does not pool shared references, reduce uncertainty with laboratory count,
or silently replace rounded source values. The reported-difference route
requires an explicit source coverage factor of 2.

Chemistry's source percentage deviations and candidate U95 values are retained
in `source-records.json`. They are deliberately not mapped to absolute
difference or expanded difference uncertainty fields. The normalized comparison
unit remains mg/g; conversion to kg/kg, if needed, is multiplication by 0.001.

## Chronological disposition and remaining evidence

Thermal sources were reviewed first. The CCT-K7 key comparison reference
includes the contributing laboratories, so it cannot act as an independent
holdout for those same records. [CCT-K7, section 6.2](https://www.bipm.org/documents/20126/45531486/CCT-K7.pdf/e55569f0-abb8-67dd-281e-c162f9a90ad1).
Chemistry and density then supplied the usable public pilots. Later polymer
and materials sources remain explicitly bounded by their method and specimen
history.

NIST's indium report supplies measured DSC enthalpies with uncertainties, but
the certified enthalpy is derived from those observations and the QC check
reuses calibration material. Indium enthalpy is also a different measurand
from polymer Tg or a melting-peak temperature. [NIST SP 260-242](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.260-242.pdf).

NIST's polyethylene melt-flow study documents calibration and measurement
uncertainty. SRM 1473a and 1473b share a resin lot, and historical comparisons
change preheat duration and charge mass. Those comparisons do not establish an
independent lot holdout under matched conditions. The package preserves a
reference intake and the reported melt temperature profile without scoring them
as a qualification. [NIST SP 260-144](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication260-144.pdf).

For a specific instrument or estimator, the remaining evidence is its identity,
intended matrix and range, applicable calibration and raw exports, reference
custody and separation, and approved method-specific limits. Internet data can
test the public benchmark workflow; it cannot establish the performance of an
unidentified system. No approval, acquisition date, calibration certificate or
physical measurement was fabricated.
