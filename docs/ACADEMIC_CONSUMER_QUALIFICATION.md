# Bounded academic-source consumer qualification

`scripts/qualify_academic_consumers.py` consumes retained public reference data
through the existing sequence Session operations and PR #185 data-source seam.
It neither downloads data nor adds a store, solver, model or public dataset.

## Exact inputs and acceptance

The input acquisition receipt is pinned by SHA256. Every one of its 287 source
and metadata files is checked by SHA256 and byte length; Git-backed source
files additionally match their recorded publisher blob. The source repository
for the reaction cases is `sbmlteam/sbml-test-suite` at
`473e119dd57226c3a7a729d598f9007f06f781c3`.

Five explicitly reviewed cases (00001, 00002, 00003, 00005, 00006) have an exact
closed two-state representation. They use the existing
`sequence.markov-analyze.v1` and `sequence.binary-evolve.v1` operations. The
mapping is declared as S1=M*p0 and S2=M*factor*p1. In case 00003, factor=2 and
the conserved amount is S1+S2/2. This mapping does not assert S1+S2 conservation
or execute a general SBML solver.

All four model, settings and expected-data files are rechecked immediately
before consumption and copied unchanged to each retained evidence directory.
The normalized declaration binds the complete projection manifest by content
identity. Its time grid is the published 51-point grid. Predictions are
explicitly projected back to mol and compared with the exact original CSV
using `data.compare-observations.v1`, with atol=1e-14 mol and rtol=1e-12.
Unknown experimental uncertainty remains absent. The declared tolerances are
finite numerical acceptance bounds, not measurement uncertainty.

Each case retains four execution occurrences (analysis, evolution, two scalar
comparisons), original source and prediction bytes, source identities, runtime
hashes, and a workspace that must reopen unchanged. The prediction provenance
names its generating execution. The projection manifest and source files are
external immutable qualification evidence; this script does not introduce a
general correction-aware SBML production adapter.

Cases 00004, 00007, 00008, 00009 and 00010 remain unsupported: their nonlinear
reverse kinetics, fixed boundary species or three-species stoichiometry are
outside the closed two-state model. They are never fitted or silently replaced.

The pinned TCLab 0–200 s transcription supplies 201 rows and four channels,
retained as 804 scalar observations with all columns, units, clock identity
and original bytes. This is typed retention only. It does not establish sensor
calibration, a complete experiment, independent heat flux or thermal-model fit.

An additional optional route executes the existing specialist-owned C++ FIR
kernel (`signal.filter.fir.v1`) on all four real TCLab channels. A declared
three-tap filter [.25, .5, .25] consumes rows 2–200; the actual rows at 0 and 1 s
are its initial history. No source samples, clock values or padding are invented.
The complete 804-observation source remains in the same Workbench; derived
streams carry its dependency. Four native filters and four typed comparisons
check 796 outputs against independent decimal convolution from original source
tokens at atol=1e-12 in each channel's declared unit. Native runtime/library
identities and provider-free restored workspaces are retained. This establishes
the finite filter computation, not a calibrated physical frequency response.
Heater commands retain `percent_command`, not an invented heat-input unit.

## Run with already acquired files

```
python scripts/qualify_academic_consumers.py \
  --acquisition-root /path/to/net-academic-remote-v2 \
  --tclab-csv /path/to/NET-Academic-Corpus-v1/data/tclab_data2_prefix.csv \
  --output-dir /path/to/new-evidence-directory
```

The output directory is create-only. Set `NET_ACADEMIC_ACQUISITION_ROOT` and
`NET_TCLAB_CSV` to run `tests/test_academic_consumers.py` against the same bytes.
Without these files the positive integration tests skip explicitly; generated
fixtures are never substituted. Negative contract tests still run.

For the FIR route, additionally pass `--dsp-library` and
`--dsp-library-sha256 sha256:<digest>`. The library must be an explicitly trusted
SCR build matching NET's ABI contract. The existing `.github/workflows/dsp-instrument.yml`
records its exact SCR source pin and generation/build command; this runner never
downloads or compiles a provider. Set `SCR_DSP_LIBRARY` to exercise the native
FIR academic tests. Absent native bindings are reported rather than replaced.

`--fao56-reference` optionally consumes the pinned FAO56 Example 18 JSON using
the existing `ciw.irrigation_solver.reference_et0` endpoint. Additional inputs
are explicitly transcribed from the same [official worked example](https://www.fao.org/4/x0490e/x0490e08.htm).
The result 3.8782548764547045 mm/day agrees with the printed 3.88 within the
retained 0.02 mm/day rounding allowance. The source's printed vapour deficit
0.589 differs from its separate rounded pressures (1.997−1.409=0.588); both
remain visible. A separate matched-input call agrees with the existing
independent `fao56_daily_et0` evaluator. This is three direct endpoint calls,
not Session executions or a complete farm plan. It invents no soil state,
measurement covariance, calendar year or pump. Source bytes are retained in
existing artifact metadata and checked against artifact digests. Set
`NET_FAO56_REFERENCE` for the corresponding real-source test.

## Existing graph and affine consumers

`scripts/qualify_graph_atlas_consumer.py --atlas ... --compute-root ... --output-dir ...`
maps the complete pinned Graph Atlas into the existing Compute Runtime projection.
All 1,253 graphs, 8,475 nodes (including isolates), and 12,342 undirected edges
survive a reversible paired-arc mapping. Forty bounded batches plus fresh replay
make 80 direct native Compute API calls. These are not Session executions and
do not invent acquisition clocks or coordinates.

The same graph records define 1-dimensional complexes with original vertices
and edges, without filling cliques. The existing `ciw.proof_f2` encoder, decoder
and reference calculator agree with an independent connected-components/cycle
oracle for all 1,252 nonempty graphs. Incorrect candidates remain mismatches;
the empty graph exercises the existing explicit refusal. This qualifies finite
statement arithmetic and binary interchange, not cryptographic proof execution
or a learned graph model.

`scripts/qualify_norris_affine_consumer.py --norris ... --sensitivity-source-root ... --output-dir ...`
passes all 36 original [NIST Norris](https://www.itl.nist.gov/div898/strd/lls/data/LINKS/DATA/Norris.dat)
rows and its certified coefficients into the existing native sensitivity
`affine_map`/`DifferentiableModel` evaluator. Eighty-digit decimal prediction and
derivative checks, certified residual statistics, and fresh-process replay must
agree. This verifies affine application, not coefficient fitting or calibration
qualification. Unspecified physical units and missing joint covariance remain
unspecified. The explicitly bound package executes from rechecked source-only
bytes; stale bytecode and sibling module shadowing cannot replace that source.
Set `NET_NORRIS_DAT` for the acquired-source regression test.

## Qualification boundary

The historical V3 inventory has 41 overlapping families and 173 mappings; it
is a research inventory rather than proof of compatible native consumers.
These bounded checks do not establish general reaction mechanisms,
the upstream Gambit/JAX interaction engine, physical
materials, field dynamics, manufacturing release or hardware control.
The earlier procurement's two empty RDF fixtures were rejected by a nonempty
payload check; empty graphs require separate exact publisher-blob verification.
Their absence does not license substituting arbitrary empty content.
