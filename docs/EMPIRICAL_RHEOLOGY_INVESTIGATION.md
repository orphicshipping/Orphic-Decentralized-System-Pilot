# One empirical investigation: historical PDMS rheology

## Question and completed boundary

At the conditions reported in NIST SRM 2491 Certificate of Analysis Table 5c,
is the shear storage modulus compatible with the exact zero predicted by a
purely viscous Newtonian constitutive model? The primary comparison is at
20 degrees C and 10 rad/s. The selection is exploratory, not preregistered.

This is a **secondary empirical investigation using published certified
summaries**, not a new experiment by NET and not acquisition from a rheometer.
It extends the existing `ciw.materials_bench` entrypoint from PR174. The
existing numerical kernels and their comparison/qualification claims are not
replaced. No new ledger, native provider, Session operation, workspace format,
calibration grant, part release or hardware control path is introduced.

## Measurement -> identity

The source is NIST's archived SRM2491 certificate, issued 2002-12-31. Table5c is
on printed/PDF page10 of14. It contains 16 oscillation frequencies and 32
reported modulus values: shear storage G' and shear loss G''. The table is
not a set of independent replicates, nor raw torque/phase/time measurements.
Its complete numerical rows were checked against the publisher's page image.
The machine-readable transcription is retained in both the request's
`capture.csv_utf8` and a human-inspectable companion CSV.

The exact LF/UTF-8 transcription digest is:

```text
98909434425e89d838bd6688f54c6ba6ad81a05a34b4cb13de2d91becbfb86b2
```

**This is not a hash of the publisher's PDF.** The PDF could be read and rendered
through web retrieval, but downloading its original bytes into the execution
container failed. `original_pdf_sha256` remains null and
`original_pdf_retained` remains false. The reviewed transcription is the pinned
local evidence snapshot. Its review is assistant-performed and not authenticated
or independently signed. The tool does not claim a digitally authenticated NIST
certificate, complete source-byte retention, or independent transcription review.

Changing a number and recalculating the supplied CSV checksum is insufficient:
the snapshot must also match the reviewed, host-pinned digest. Changing the
source URL, certificate edition, table, uncertainty type, k, material identity,
method classification or certification expiration refuses the request. A new
source requires explicit review and a new profile; it cannot enter by impersonating
this material. This is a narrow known-source importer, not a generic CSV reader.

Each observation has a stable identity determined by source identity, quantity,
and original table/row/cell location. A Pa -> kPa representation change retains
that observation identity and creates a different representation identity.
Uncertainty scales with the unit, and variance scales with the square of the
unit conversion. Evidence identity is distinct from operation identity,
execution occurrence, result occurrence, inspection and verification occurrences.
The scientific content digest is separate from the seal covering the complete
result occurrence. Repeated executions do not create new physical observations.

The source's original measurement time, bottle identity, instrument serial and
raw calibration records are unavailable and remain null or explicitly absent.
The transcription creation time and NET execution time do not fill those gaps.
Future captures and execution timestamps preceding the capture are refused.

## Calibration and condition identity

Page2 describes an ARES controlled-strain rheometer, transducer calibration with
known applied loading, a steel coupon for phase calibration and a NIST-calibrated
thermistor for temperature comparison. Oscillatory measurements used 50 mm
parallel plates, a nominal 1 mm gap and a reported 2% strain amplitude.
These are **publisher-reported conditions**, not native achieved-state telemetry
or newly verified calibrations. Individual temperature and frequency uncertainties
are not extracted from this table and are not invented.

The source expiration date is **2012-12-31**. Historical numerical interpretation
is permitted by this profile; current instrument calibration is not. Reading this
archive does not authorize use of an old physical bottle as a current standard.
No inference is made about an arbitrary PDMS grade, NET's equipment, a material
lot in the microfab, or a finished part.

The measured observables are shear moduli G', G'', not the tensile Young's
moduli E', E'' produced by the earlier uniaxial model. No implicit factor-of-three,
incompressibility, Poisson-ratio, Hz-to-rad/s, temperature-shift or specimen
identity substitution is performed. Such a comparison requires another explicit
physical transformation and its supporting evidence.

## Uncertainty -> comparison

Page1 declares expanded combined uncertainties with k=2 and approximately
95% coverage. The importer retains the reported U and computes:

```text
u = U/k
variance = u^2
Y = a*X                 # exact Pa <-> kPa unit transformation only
u(Y) = abs(a)*u(X)
variance(Y) = a^2*variance(X)
```

These are normalized publisher-reported uncertainty statements. The underlying
coverage and calibration budget are not independently validated by NET. There
is no reconstructed standard error from 16 different frequencies, and no
addition of invented sensor or rounding components to the supplied combined U.
The precision of the published rounded values is preserved in the captured text.

For a purely viscous Newtonian model, shear stress is eta times shear-strain
rate. Under harmonic shear, G* = i*omega*eta, hence G'=0 at every frequency.
That zero is a constitutive hypothesis, not a fitted parameter estimate with
missing parameter uncertainty silently set to zero. It is not a Maxwell-model
prediction, a calibration fit or an independent validation of a fitted model.

For each storage-modulus observation, d=G'-0 and u(d)=u(G'). The operation records
the expanded residual interval [d-U,d+U] and whether it contains zero. This
single-observable comparison does not require a covariance between independent
uncertain inputs: its sole uncertain input is the reported G'. No p-value,
chi-squared statistic, familywise confidence or joint goodness-of-fit is inferred.
The 16 marginal screens are retained, with the 10 rad/s row explicitly primary.

### Actual primary result

| Quantity | Value |
|---|---:|
| Source temperature | 20 degrees C (293.15 K) |
| Source angular frequency | 10 rad/s |
| Source shear storage modulus | 2636 Pa |
| Expanded uncertainty U | 84 Pa |
| Coverage factor k | 2 |
| Standard uncertainty u | 42 Pa |
| Standard variance | 1764 Pa^2 |
| Newtonian storage-modulus prediction | 0 Pa |
| Expanded residual interval | [2552, 2720] Pa |
| Outcome | INCOMPATIBLE_WITH_PUBLISHED_INTERVAL |

This rejects the zero-storage approximation **for this source, observable and
condition under the declared interval rule**. It is not a manufacturing
conformity decision, proof that a fitted viscoelastic model is correct, or a
statement that a Newtonian approximation is unusable for every other purpose.

### Unknown covariance remains unknown

The table does not supply joint G'/G'' covariance or cross-frequency covariance.
Consequently, propagated complex-viscosity uncertainty is HELD, and a joint
frequency goodness-of-fit statistic is HELD. Frequency uncertainty is also not
available for a propagated viscosity calculation. The implementation does not
replace these missing quantities with zero, diagonal covariance, independence,
bootstrap pseudo-replicates or a sample covariance calculated across frequencies.
Current instrument calibration is independently HELD for the expiration and
native-acquisition reasons above. An eligible marginal decision can coexist
with these blocked derived claims.

## Running through the existing materials interface

From this candidate source overlay, in PowerShell:

```powershell
$env:PYTHONPATH = 'src'
python -m ciw.materials_bench empirical-run examples/polymer/nist2491-investigation.json --output empirical-result.json
python -m ciw.materials_bench empirical-inspect empirical-result.json --output empirical-inspection.json
python -m ciw.materials_bench empirical-verify empirical-result.json --output empirical-verification.json
```

On POSIX, use `PYTHONPATH=src` before each command. Every output path must be new.
The existing strict JSON loader and create-only publication boundary are reused.
Old `run`, `inspect` and `reproduce` actions remain intact. No dependency is added.
The empirical CLI was also tested with `python -S`, without site-packages.
This increment does not add a `net polymer` or Session operation route.

`empirical-inspect` checks record/content seals, the reviewed source pin,
source/condition identities, normalization, result bindings, shapes and held
claims. It **does not repeat the Newtonian claim comparison**. A coherently
resealed, false comparison can therefore pass this limited inspection.
`empirical-verify` then independently parses the retained decimal literals and
uses 50-digit Decimal arithmetic to audit all 32 normalized measurements and
all 16 residual-interval screens, without calling the production comparison.
The arithmetic allowance is eight binary64 ulps; exact zero stays exact.
It creates a new verification occurrence and leaves the original result intact.
Hashes and a passing arithmetic audit are not source authentication.

## Development and verification

The unchanged PR174 source snapshot was checked against its GitHub blob
identities, then passed its original 428 focused tests in this environment.
The new empirical suite has 116 tests. It covers source tampering even after
rehashing, U versus u, variance scaling, the full captured table, source-cell
identity, Pa/kPa commuting results, source edition/expiry/condition guards,
unknown covariance, direct helper calls, occurrence separation, forged retained
comparisons, independent arithmetic and source/stdlib-only CLI paths.

A direct-helper gap was found and repaired during development: an edited,
self-resealed detached observation could reach the newly written public
comparison helper without being rebound to the source request. The full
run/inspection path was already source-bound. The public helper now takes the
source request, revalidates its reviewed capture, and requires exact membership
of the observation and representation before comparison. Six mutation cases
and five malformed-observation cases protect that boundary.

Two all-suite attempts hit tool wall limits (45 and 90 seconds); neither is
counted as a completed pass. The final all-suite command, with BLAS/OpenMP thread
limits explicitly set to one, completed with **544 passed, zero failures and
zero skips**, exit code 0. Its 116 empirical tests extend the original 428-test
selection. This observed scheduling change is not a diagnosis of either timeout.
JUnit, exact environment settings and patch round-trip records are retained in
the delivery validation manifest; only successful recorded exits count as passes.

Qualification remains focused source-level testing, not full-repository,
installed-wheel, Windows, pinned-environment, original-PDF-byte, native-device,
independent-transcription, calibration-coverage or physical-part qualification.
The prior PR172 and PR175 branches are not incorporated or overwritten here.

## Primary references

- NIST SRM2491 archived Certificate of Analysis, pages1,2,10; issue2002-12-31:
  https://tsapps.nist.gov/srmext/certificates/archives/2491.pdf
- NIST publication page for the underlying rheological certification study:
  https://www.nist.gov/publications/certification-rheological-behavior-srm-2491-polydimethylsiloxane
- NIST, expanded uncertainty and coverage factors:
  https://physics.nist.gov/cuu/Uncertainty/coverage.html
- NIST, combining uncertainty components (including covariance):
  https://physics.nist.gov/cuu/Uncertainty/combination.html

Only numerical facts and attribution are transcribed; the original publisher
PDF and logos are not redistributed. Existing project licensing is unchanged.
