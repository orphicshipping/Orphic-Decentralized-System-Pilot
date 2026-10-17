# Curated molecular reference data

This increment procures a bounded source pack and prepares data for the existing
GROMACS and LAMMPS providers. It does not create an execution registry, evidence
store, Workbench data family, training service or generic force-field converter.

## Source and dependency identities

NET main inspected: `0374076695475669188d61698e4656ced2263e01`.
This candidate is stacked on the LAMMPS provider in PR #197 at
`8569ac95f234c9334224832df15e91c002bfae20`. GROMACS retained execution requires the
independent PR #192 increment at `00ea251ef9574148105283443e0f8a3bdf38bf2e` or a
compatible successor. The parent #185 data bridges, ALCHEMI and both native
runners are unchanged. GROMACS manifest and receipt APIs were read at that exact
updated head rather than inferred from its older PR description.

| Acquired content | Identity / origin | Delivered use |
| --- | --- | --- |
| NIST configuration 4 | 30 reduced-coordinate LJ sites, periodic cuboid of side 8 | Nine published numerical-reference checks and two explicit data projections |
| FEASST ethane and n-butane definitions | Two TraPPE-UA native particle files | Retained scientific parameters and section inventory; bonded execution is refused |
| Shell 65N8U and 52N7 | Two manufacturer grades, ten selected nominal values | Typed material-property targets, normalized units and explicit missing conditions |
| Dow CONTINUUM DGDC-2490 BK | One manufacturer grade, four selected nominal values | Density and load-specific melt mass-flow targets, not a viscosity law |

The four exact-byte files, including the NIST license, are from
[usnistgov/feasst](https://github.com/usnistgov/feasst) at
`af95d31a6401f08f99d38439c71427b30ce22e0a`. UTF-8 content was reconstructed from the
connector and verified against each upstream Git blob as well as SHA-256. No
upstream archive, binary or complete manufacturer document is represented as
retained when only selected facts were obtained.

`examples/molecular-data/catalog.json` is a curated input artifact. Its external
SHA-256 is in `catalog.sha256`; keep an independent copy for later verification.
Hashes detect changed bytes, not authenticated authorship or scientific validity.

## Primary scientific references

The [NIST cuboid reference](https://www.nist.gov/mml/csd/chemical-informatics-group/lennard-jones-fluid-reference-calculations-cuboid-cell)
publishes pair energy, pair virial and tail correction for potential truncation
at 3 and 4 sigma, and linear-force shifting at 3 sigma. Only configuration 4 was
procured. All quantities are in reduced LJ units. The virial is sum(r dot F),
not one third of that quantity. The tabulated LRC is total-configuration energy;
the per-particle equation is multiplied by N when comparing to the table.

A scalar all-pairs implementation reproduces all nine selected table entries
within half the last printed decimal place plus 1e-10. That tolerance describes
published rounding, not metrological uncertainty. The selected table values are
factual transcriptions, not byte-exact HTML. Pair energy and tail correction stay
separate. `DispCorr = no` in generated GROMACS inputs means the native observable
is pair energy only; no LRC is silently added.

The acquired alkane files cite [TraPPE](http://trappe.oit.umn.edu/). The academic
basis includes Martin and Siepmann, 1998, [DOI 10.1021/jp972543+](https://doi.org/10.1021/jp972543+).
These are FEASST representations, not drop-in GROMACS topologies or LAMMPS data.
Rigid bonds, harmonic prefactors, torsion functional forms, mixing rules and
intramolecular exclusions need explicit translation and independent tests before
any bonded profile can execute. No extrapolation to industrial HDPE grades is
made, and model parameters are not relabelled as measured properties.

NIST attribution and its complete distribution notice are retained in
`sources/FEASST-LICENSE.rst`. The original files are unmodified. Derived exports
are identified as NET transformations made on 2026-10-05, with scale, coordinate
quantization, boundary policy, cutoff and source hashes recorded.

## Industrial targets and limits

The fourteen facts come from the official
[Shell 65N8U page](https://www.shell.us/business/sectors/shell-polymers/product-selector/65n8u.html),
[Shell 52N7 technical data sheet](https://www.shell.us/business/sectors/shell-polymers/product-selector/52n7/_jcr_content/root/main/section/simple_1475348609/call_to_action/links/item0.stream/1761689930731/9d96573052c688272538b5fad1912f8776718fe8/shell-polymers-52n7-tds-hdpe.pdf),
and [Dow product page](https://www.dow.com/en-us/pdp.continuum-dgdc-2490-bk-bimodal-polyethylene-resin.525484z.html).
The Shell PDF table was visually checked. Its revision is `HDTD.52N7.R2`.
Unknown source revision dates remain null, not today's date.

Density, MFR, yield/break stress, secant moduli and thermal peaks retain the
original values and units, named test methods, reported conditions and known
missing conditions. Normalized values use kg/m3, kg/s, Pa and K. MFR testing
loads are not merged. MFR is not converted to viscosity. The eV to kJ/mol
conversion uses the exact SI elementary-charge and Avogadro constants recorded
with a [NIST source](https://physics.nist.gov/cuu/Constants/Table/allascii.txt).

Supplier nominal values are not material specifications, lot certificates,
calibration data, constitutive models or validated simulator targets. No lot,
measurement timestamp, molecular-weight distribution, conditioning or uncertainty
is fabricated. No simulator comparison or acceptance threshold is inferred when
the specimen, method or state is incompatible. Records remain
`NOT_COMPARABLE_TO_CURRENT_LJ_PROFILES` and uncertainty remains unspecified.
Manufacturer PDFs and marketing text are not redistributed. Only selected
numerical facts and provenance are included; model-training permission has not
been established and no training export is authorized by this pack.

## Executable data preparation

From this checkout:

```bash
CATALOG=$(cat examples/molecular-data/catalog.sha256)
python scripts/molecular_reference_data.py check --catalog-sha256 "$CATALOG"

# Preserve NIST periodic geometry; prepare a GROMACS pair-energy benchmark.
python scripts/molecular_reference_data.py prepare \
  --catalog-sha256 "$CATALOG" --profile nist-periodic \
  --output /new/outside-checkout/nist-periodic

# Explicitly change the physical problem to fit the existing LAMMPS profile.
python scripts/molecular_reference_data.py prepare \
  --catalog-sha256 "$CATALOG" --profile cluster-projection --allow-projection \
  --output /new/outside-checkout/cluster
```

Both exports contain original source bytes, normalized targets, force-field
inventory, numerical-reference checks and exact TOP/MDP/GRO input files.
The cluster export additionally contains the existing `ste.lammps-lj-input.v1`
record. No extra provenance fields are injected into that closed native schema;
lineage lives in the linked data export, and the unchanged provider retains its
own input/program/execution identities.

The periodic export scales distances by 3.4 angstrom per sigma, uses epsilon
0.0104 eV and a 3-sigma cutoff. This is dimensionalization of the benchmark, not
an experimental argon calibration. LAMMPS explicitly refuses this original
periodic case because PR #197 is non-periodic.

The cluster projection intentionally removes periodicity and changes the cutoff
to 2.5 sigma. It MUST NOT reuse NIST's periodic energy as a validation label.
A separate scalar calculation evaluates the transformed coordinates. Coordinates
are quantized to 1e-8 angstrom and identical values enter both consumers. GROMACS
uses a 256 angstrom periodic box and LAMMPS uses fixed boundaries; image-exclusion
and coordinate-span checks establish equivalence for this one static energy and
force evaluation only. There is no claim of trajectory or boundary equivalence.
All derivatives share the original source's split group to prevent placing
correlated variants in independent future training/evaluation sets. No actual
training split has been assigned.

The `prepare` command prints an export manifest SHA-256. Retain it outside the
export. Offline verification checks exact hashes, reconstructs every derived file
and checks the transformation semantics without launching a provider:

```bash
python scripts/molecular_reference_data.py verify \
  --export /path/to/export --manifest-sha256 RETAINED_MANIFEST_SHA256
```

Native invocation uses only the existing providers and requires explicit paths:

```bash
python scripts/molecular_reference_data.py run \
  --export /path/to/cluster --manifest-sha256 RETAINED_MANIFEST_SHA256 \
  --provider lammps --executable /absolute/path/lmp \
  --expected-sha256 REVIEWED_BINARY_SHA256 --output /new/native-attempt

python scripts/molecular_reference_data.py run \
  --export /path/to/nist-periodic --manifest-sha256 RETAINED_MANIFEST_SHA256 \
  --provider gromacs --executable /absolute/path/gmx_d \
  --expected-sha256 REVIEWED_BINARY_SHA256 \
  --runtime-binding /operator/reviewed-gromacs-binding.json \
  --binding-sha256 REVIEWED_BINDING_FILE_SHA256 --output /new/native-attempt
```

GROMACS requires its existing `ste.gromacs-runtime-binding.v1` manifest with the
executable, build record and explicitly declared dependency files. The supplied
manifest is bound into `ExecutionSpecification`, passed into the existing runner,
and its `receipt.json` is checked with the existing offline validator. No fallback
to the earlier version-only or temporary-artifact path exists. LAMMPS keeps its
current executable-bound descriptor and `verify_capture` path. Neither process
is a security sandbox or race-free execution attestation.

## Executed evidence and CI

Local execution used a retained source slice, with original commitment/engine/
specification and LAMMPS file hashes verified against PR #197. It was NOT a full
NET checkout and omitted the eager execution-package/dispatcher dependency
closure. The focused final suite passed **70 tests**, including actual LAMMPS
input validation/reference functions and an explicitly synthetic GROMACS API
double matching the updated inspected manifest interface. The initial run had
two test-assumption failures (temperature field name and reference tuple shape),
which were corrected; its failing log is retained rather than discarded.

The existing LAMMPS CI workflow is extended, not duplicated: it runs these tests
on Linux/Windows and feeds the cluster export into the SAME source-pinned native
CPU build after the existing native qualifier. No passing skip or scalar
replacement is permitted in that native step. Hosted execution must be assessed
from actual run outcomes, not this workflow definition.

No native GROMACS/LAMMPS calculation, installed NET Session journey, supplier
physical validation or private model training is established by the local data
checks. Missing executable bindings produce NOT_RUN/exit 2, never synthetic
native results. Wrong numerical results retain failed comparisons.

## Remaining data procurement

Before industrial polymer-model validation, obtain authorized lot-linked
composition/additive information, SEC/GPC molecular-weight distributions,
DSC traces with rates/history, pressure-volume-temperature data, rheometry with
geometry/shear/time/temperature, and specimen-specific mechanical tests with
conditioning and uncertainty. Request access and training rights separately.
ThermoML experimental records and broader force-field packages require exact
source/licensing/observable review; none is claimed as acquired by this pack.
