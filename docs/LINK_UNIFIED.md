# One Link: profiles and mathematical preservation

**Decision, 2026-10-06: Link is the common physical-I/O application/runtime.
Gauge, thermal, pressure, vibration and electrical are profiles, not independent
software architectures. Hardware/product labels may remain commercially useful.**

This candidate extends PR209 at `959e0625f118df3bc8841a9e262bc2810be26cd1`.
The inspected parent already uses one DeviceRuntime, wire codec, journal, ingress,
Session and scalar investigation. The change makes that shared architecture
explicit in selection, introspection and its mathematical contracts; it does not
merge fictional separate backends or replace NET's existing mathematical systems.

## One application, compatible history

```sh
net link catalog
net link example --profile gauge --output-dir ../gauge-example
net link demo --profile thermal --output-dir ../thermal-example
net link demo --profile pressure --output-dir ../pressure-example
net link demo --profile vibration --output-dir ../vibration-example
net link demo --profile electrical --output-dir ../electrical-example
```

The default example selector is `gauge`. Canonical selectors also include machine,
condition, field, daq and calibration as existing application recipes. Legacy
GaugeLink/MachineLink/ConditionLink/FieldLink/ThermalLink/DAQLink/CalLink selectors
remain accepted and generate the exact same original example manifests and
observations. PressureLink/VibrationLink/ElectricalLink are aliases for the new
single-quantity synthetic examples. There is no new driver or protocol implied.

V1 manifest `persona`, hardware/product identity and raw evidence are NOT renamed.
These participate in existing content identities. New examples keep an explicit
legacy wire-compatibility tag; current profile matching never consults that tag.
Old factory/catalog APIs remain available. The application-facing catalogue adds
one runtime identity, canonical selectors and quantity signatures to the original
catalogue rather than maintaining a competing device registry.

All examples are synthetic. The pressure and vibration cases run scalar records,
not a pressure-transducer driver, synchronized vibration waveform, spectral
analysis or qualified machine-health baseline. Actual CAN/RS-485/optical/motion
profiles and their physical interfaces still require independent qualification.
A common runtime does not imply every board runs the same firmware image.

## Sets, types, relations and logic in an executable boundary

```sh
net link catalog --manifest ../gauge-example/manifest.json \
  --manifest-ref sha256:EXTERNALLY_RETAINED_EXACT_BYTE_DIGEST
```

This reads bounded, operator-selected bytes without opening or enrolling a device.
Its local `ciw.link-profile-view.v1` is an interpretation of the existing manifest,
not a replacement acquisition schema. It retains both the exact-byte reference
and the existing canonical manifest identity.

Device, instrument, channel, quantity and unit objects have separate typed
references. Channel references are scoped by device so two CH0 labels do not
collapse into one entity. Relations declare device-to-channel,
channel-to-instrument, quantity and unit bindings. These are manifest declarations,
not authenticated physical facts or an additional graph database.

Profile predicates are evaluated from enabled channel quantity/unit signatures:

| Predicate outcome | Interpretation |
| --- | --- |
| DECLARED_MATCH | Named quantity/unit fits the bounded profile; review may proceed. |
| NO_MATCH | Declared quantity is outside this profile or this channel is disabled. |
| UNKNOWN | The quantity matches but its unit semantics are not registered. |

A machine with pressure and temperature channels can match both profiles.
A GaugeLink-labelled declaration with temperature/K matches thermal, not gauge.
Unknown units are not guessed or silently converted. Opaque input still requires
its separately bound decoder. Connection, authentication and calibration stay
UNKNOWN; every execution/configuration/control/release permission remains false.
A match is NOT model eligibility, a qualified measurement or a capability grant.

## Connect Link to NET's existing mathematical substrate

```sh
net link inspection demo --output-dir ../inspection
# Use the request reference emitted by the selected investigation.
net link semantics ../inspection --request-ref sha256:EXTERNAL_REQUEST_DIGEST \
  --output ../semantic-review.json
```

This uses the existing validated scalar reader and original Session records.
It creates no new scientific execution and preserves the original request,
workspace, graph, execution and result identities, including failed/absent stages.
An optional review file is create-only. The engineering command is not another
required customer setup step. Digital Inspection is visibly branded Link; its
result/uncertainty/audit/HELD gates and original computations are unchanged.

The review uses existing `SemanticRegistry`, `representation_morphisms` and
`preservation_contracts`, with no core modification. Four existing operation
signatures are linked to five representations:

```text
retained source -> indicated series -> corrected series -> mean candidate -> audit
```

The three adjacent compositions use the existing typed composition evaluator.
Contracts track source context, raw recovery, parent occurrences, clock domains
and uncertainty ancestry. Numeric interpretation/transformation has its own
property lineage. Decoder rounding, lossy mean aggregation and conditional
first-order covariance are declared, not hidden under a universal preservation
claim. An audit record is not another physical quantity.

COMPOSABLE means the declared intermediate types/obligations align. It is separate
from original operation completion, the arithmetic audit result and physical
qualification. These semantic lowerings are advertised but unbound; reviewing a
profile cannot register executable code. The private representation catalogue
used for the review is ordinary existing NET contract data, not a new scheduler,
plugin loader or canonical state owner.

## A concrete commuting-square check

The review can check re-expression of its declared affine correction in a finite
allowlist of quantity/unit coordinate systems, reusing the existing bounded
2x2 rational algebra in `representation_lab`:

    A(x) = a*x + b
    U(x) = s*x + t
    A_target(z) = a*z + s*b + t - a*t
    U*A = A_target*U

Exact coefficient matrices are compared, not just one coincidentally equal
sample. A supplied candidate target matrix can PASS or FAIL this equality through
`link_semantics.unit_path_witness(..., proposed_target_operator=...)`.
The diagnostic does not execute the candidate on hardware or modify a measurement.

For Celsius temperature, U has offset 273.15 K; a Celsius temperature difference
has offset zero. The fixed ASCII `degC` token is explicitly supported, while `C`
is not guessed. For a=2 and b=1 in degrees Celsius, reusing b=1 in kelvins FAILS;
the correct target offset is -272.15 K. Length mm -> m similarly requires scaling
the offset. Reference definitions:
<https://www.nist.gov/pml/special-publication-330/sp-330-section-2> and
<https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b9>.

Rationals use the existing kernel's decimal-roundtrip coefficient convention,
not the exact rational expansion of a binary float. The existing rational size
budget is unchanged; unsupported or excessive cases are UNRESOLVED in review.
Python distinguishes these constructions:
<https://docs.python.org/3.13/library/fractions.html>.

This establishes equality of these supplied affine matrices only. It does not
prove floating-point execution equivalence, physical accuracy, SP1 verification
or functor laws for arbitrary hardware. No observations or covariance matrices
are converted. Re-expressing uncertain parameters would require full joint
covariance transport, including correlations introduced by the affine origin;
that execution path is NOT delivered by this witness. Original covariance stays
in the existing scalar workflow and its retained parents, with UNKNOWN preserved.

## The rest of the ladder remains bounded

Physical board composition must bind interface compatibility, rates/bandwidth,
power, frames, clocks, resource conflicts and uncertainty dependencies. It is not
merely a union of capability labels. The existing System Board/compiler and
board-morphism binding remain the integration target; this increment does not
implement electronics composition or treat its Board as a physical PCB.

Likewise, approximately commuting diagrams need a declared metric and error
criterion. The uncertainty of two paths sharing source evidence needs their
cross-covariance; agreement alone is not an independent calibration check.
No broad inference/control eligibility follows from a unit-compatible profile.

The earlier boundary contract still separates evidence, filters, computation,
proof, verification, qualification, authorization and capability gates. Future
state estimation, SciML, optimization and control must compose beneath that
separation. Link is the heterogeneous I/O boundary; NET retains orchestration and
scientific-state ownership. A common runtime is not universal driver support.

## Integration and qualification

The existing Link source/installed qualifier includes `test_link_unification.py`
under `--with-delivery`, the three new synthetic profiles and a retained semantic
review. Original private GaugeLink application/CAD is not copied or renamed.
Pending #185/#200 data bridges, #206 identity/consent and #208/#210/#211
measurement/pinwheel/reference work are untouched. Preserve their pending CLI
rows during eventual integration; no combined cross-branch qualification is claimed.

No dependency pin, software license, wire schema, calibration coefficient,
numerical kernel, journal, Session or shared mathematical implementation changes.
No merge, deployment, device enrollment, flashing, real acquisition, cloud upload,
model training, machine actuation or physical qualification is authorized here.
