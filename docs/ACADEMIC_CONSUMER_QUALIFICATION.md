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

## Qualification boundary

The historical V3 inventory has 41 overlapping families and 173 mappings; it
is a research inventory rather than proof of compatible native consumers.
This bounded reaction check does not qualify the remaining families, general
reaction mechanisms, the upstream Gambit/JAX interaction engine, physical
materials, field dynamics, manufacturing release or hardware control.
The earlier procurement's two empty RDF fixtures were rejected by a nonempty
payload check; empty graphs require separate exact publisher-blob verification.
Their absence does not license substituting arbitrary empty content.
