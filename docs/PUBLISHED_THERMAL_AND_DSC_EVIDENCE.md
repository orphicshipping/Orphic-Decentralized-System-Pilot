# Published thermometry and DSC evidence

This extension adds actual public measurement tables to the retrospective
evidence workflow. The instrument-family order remains the repository's source
introduction order, as requested; it is not a chronology of publication dates.
No release configuration, prospective approval gate or physical qualification
status changes.

## Thermometry

The complete selected table is SIM.T-S2 Table 23: six external laboratories and
two CEM control calibrations for the large platinum resistance thermometer at
nominal 100 degrees C. The reference uses CEM's initial and final calibrations,
excluding the external participants. This improves reference separation over
the previously reviewed CCT-K7 participant-average reference.

The printed differences already express candidate-minus-reference reduced
resistance deviations as equivalent temperature differences. They are not two
separate absolute temperature readings. The importer preserves those signed
differences and the separately published candidate/reference expanded
uncertainties. It reconstructs `U(d) = sqrt(U_candidate^2 + U_reference^2)` using
the report's quadrature convention, without inventing a zero reference reading
or labelling that reconstructed uncertainty a published literal.

Coverage evidence remains unresolved for SENCAMER and IEN-CMFT. IBMETRO's table
uncertainty conflicts with its inspected budget. These three issues remain
visible. The author equation is available, but a numerical acceptance boundary
was not located. Whether the reconstructed interval includes zero is therefore
a descriptive result, never an agreed qualification decision. Pilot controls
are retained separately and are not scored as independent candidates.

Source: [SIM.T-S2, report pages 14-15, Table 23 and uncertainty appendices](https://www.bipm.org/documents/20126/45532447/SIM.T-S2.pdf/9b4b7dd4-325b-2a86-4353-16ceeb7c8393).

## DSC calibration-material measurements

The DSC importer retains all 180 processed enthalpy measurements in NIST
SP 260-242 Table A.2.1: 15 samples, seven ingots, one material lot, four nominal
heating rates and three replicates per sample/rate. Exact displayed decimal
tokens, sample masses, measured heating rates and table coordinates remain
available. The extractor pins the inspected PDF's SHA-256, compares pypdf and
pdfplumber extraction for every row, checks Table 3 masses, and checks anchors
on all seven visually inspected pages.

The publication describes the row uncertainties as standard uncertainties due
to systematic effects. Its separate hierarchical model handles ingot,
subsample and random effects. The importer preserves this distinction and does
not divide the row uncertainty by the square root of the number of records.
It reports descriptive means and checks that the source model's more precise
systematic inputs are compatible with rounding of the appendix values.

This is not a refit of the Bayesian certification model. The certified enthalpy
is derived from these observations, so it cannot independently validate the
same observations. Indium calorimetry also does not validate polymer glass
transition temperature, melting-peak temperature, rheology or mechanical
properties. Measured rates are preserved rather than replaced with nominal
rates; table replicate order is not claimed to be acquisition order.

Source: [NIST SP 260-242, sections 2.3, 3.2 and 4.2; Table A.2.1](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.260-242.pdf).

## Execution and verification

Both importers use bounded local evidence files with SHA-256 descriptors.
They refuse inconsistent provenance and write to fresh output directories,
publishing the completed report last. Runtime calculations need only Python
3.11 or newer. PDF re-extraction additionally requires pypdf and pdfplumber.

```text
python -m ciw.published_thermal_differences --manifest campaigns/thermal/campaign.json --output-dir new-thermal-run
python -m ciw.published_dsc_measurements --manifest campaigns/dsc/campaign.json --output-dir new-dsc-run
```

Use the portable bundle's source fetcher before reproducing runs. Original
third-party PDFs are linked and hash-checked rather than redistributed. A
failed download or changed source hash requires review. A manifest hash
establishes byte consistency, not an external signature or an original
calibration certificate. The DSC extraction script additionally pins the
reviewed source edition; runtime curation remains an explicit evidence step.

After source fetching, the extraction checks can be repeated without changing
curated data:

```text
python extraction/extract_nist_dsc.py --source campaigns/dsc/source.pdf --output-dir campaigns/dsc --check
python extraction/extract_thermal_table.py --source campaigns/thermal/source.pdf --output extraction/thermal-table.json --check
```

Thermal CLI completion is not laboratory acceptance. DSC exit 0 means the
rounding check is compatible, exit 2 requests source-precision review, and
exit 1 indicates malformed or unavailable evidence. Every result retains
`local_instrument_qualification: not_established` and `release_status: unchanged`.

A particular instrument still needs its own raw exports, applicable
calibration evidence, independently assigned held-out references and agreed
limits for the intended measurand, matrix and range. Public reports do not
supply those facts for an unidentified local system.
