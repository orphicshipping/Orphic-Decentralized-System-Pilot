# Temperature pilot readiness inventory

The literature-based [pilot setup](../examples/temperature/pilot-setup.json)
selects a candidate experiment and keeps unacquired hardware identities,
calibration records, observations and qualification reviews explicitly missing.
It is a planning record, not a processing request or purchasing instruction.

Run the data-only check from a checkout or installed package:

~~~sh
python -m ciw.temperature_readiness \
  --setup examples/temperature/pilot-setup.json \
  --output results/temperature-pilot-readiness.json
~~~

The optional output is create-only. Exit status is 0 when required declarations
are complete for review, 1 when fields remain missing or numerically invalid, and
2 for malformed contracts or file errors. The current plan is expected to return
1. No sensor, model, prover, network service or evidence reference is opened.

## What is checked

The report binds the complete input plan by content identity and inventories four
stages: setup definition, confirmatory design, qualification-evidence inventory,
and maintenance definition. Model-comparison and temperature-SP1 declaration
lists are separate; selecting a model target or proposing proof work enables its
list. Unrequested optional work does not block the conventional plan.

Null, empty, placeholder or partially populated structured records are missing.
Unknown plan fields, asserted physical qualification, and SP1-derived physical
qualification refuse. Product-conformity declarations remain unassessed. Selected numeric
fields have explicit formats:

| Field | Format |
| --- | --- |
| Temperature range | [lower, upper] in K, positive and ordered |
| Heating/cooling rate range | Ordered signed bounds in K/s |
| Sampling interval | Positive seconds |
| Allowed timing uncertainty | Nonnegative seconds |
| Required uncertainty and reproducibility limit | Positive K |

Other required fields accept substantive text or structured records. This is
presence checking: the checker cannot tell whether an apparently complete
description is scientifically sufficient. A filled reference URL is not fetched;
a filled review field does not appoint this software as its reviewer.

The status declarations_complete_for_review is compatible with a wholly
synthetic fixture. It never means experimentally ready, physically qualified,
traceable, product compliant, authenticated or authorized to operate. Original
records and qualified human review remain necessary. See the separate
[qualification protocol](TEMPERATURE_QUALIFICATION.md).

## Literature choices and unresolved evidence

The plan's source_selection separates sourced apparatus facts, proposed design
targets and dataset suitability. Its apparatus basis is
[Rodrigues et al., university-hosted original paper](https://revistas.ufpr.br/reterm/article/view/80462).
The inspected publication provides an experiment design; no linked machine-readable
acquisition dataset or complete traceability/installed-uncertainty dossier was
located. Published plots and averages are not used as fresh observations,
training data or independent validation.

The separate [public polymer temperature dataset assessment](POLYMER_TEMPERATURE_DATASETS.md)
records inspected academic archives, NIST catalog candidates, import gaps and
measurand differences for a conditional model-comparison benchmark. Those
records do not qualify the proposed installed measurement chain.

[NIST's thermocouple calibration service](https://www.nist.gov/pml/sensor-science/temperature-humidity/thermocouples-calibrations-services)
provides a primary reference-method basis. A service capability does not become
this proposed chain's calibration certificate or uncertainty contribution.
The plan leaves those actual records absent.

The selected paper's record duration and acquisition rate exceed the affine
processor's 64-sample request limit. Preserve the original full cycle; do not
silently truncate it or treat independent windows as a covariance-preserving
full-cycle calculation. A larger declared profile or an explicitly qualified
handoff is separate work.

The affine input would be the readout's indicated temperature after its internal
thermocouple conversion, explicitly converted to K. This does not replace
thermocouple EMF linearization by a globally affine law. Cold-junction effects,
readout conversion and the residual of the subsequent calibration fit require
their own uncertainty evidence.

No physical equipment has been ordered or installed by this research step.
Numerical targets in proposed_not_source_measurements are provisional design
choices, not literature-derived performance results or customer-agreed limits.

## Bounded next model-comparison step

The repository has affine calibration, a shell-conditioned two-capacity thermal
reference and lumped polymer cooling, with different measurands and uncertainty
scope. It has no temperature PINN/gPINN execution. The measured-thermal path
retains input/model/clock uncertainty without propagating all of it, so its
conditional filter covariance cannot substitute for a complete uncertainty
budget.

A useful next implementation is a retained comparison contract accepting a
frozen grouped split manifest, conventional and candidate estimate streams,
and a separately withheld reference for the same measurand/time grid. Preserve
different model identities; check declared specimen/cycle disjointness; report
bias, RMSE, maximum error and missing counts separately for in-envelope and
OOD conditions. Bind candidate weights, preprocessing and training declarations.
Synthetic fixtures can qualify these software checks, but cannot authenticate
training history, establish model discrepancy or replace held-out physical data.
