# Agro-food cold-chain thermal transfer experiment

This experiment deliberately does not create an agriculture-specific computational architecture.

It reuses the existing two-capacity thermal observer, covariance handling, missing-observation logic and prospective sensor-selection machinery unchanged, and changes only the application fixture.

## Synthetic application

The modeled state is product core/shell temperature under refrigerated ambient conditions, suitable as a bounded post-harvest cold-chain analogue for berries, grapes or another specialty product.

The workload includes:

- refrigerated ambient temperature;
- low internal heat load;
- core + shell temperature observations;
- explicit sensor dropouts;
- prior/process/measurement covariance;
- held-out synthetic observation;
- a sensor-cost budget.

The retained thermal instrument then performs state estimation and prospective sensor selection.

## What this tests

The experiment asks whether a general thermal/state-estimation instrument can be reused as-is in an agro-food workload.

It does not test crop biology, spoilage kinetics, food safety, yield, disease, quality grading, refrigeration hardware, or a physical farm/cooperative.

The required transfer claims are therefore narrow:

- existing thermal instrument reused unchanged;
- no new agriculture architecture introduced;
- physical validation remains false;
- product-quality prediction remains false;
- state admission remains false.

## Why this is useful

This is the first small step toward the broader regional transfer hypothesis: manufacturing/materials workloads in Canada and agriculture/agro-food/energy/thermal systems in France should stress different domain science while reusing some of the same instrumentation primitives.

If the same thermal, calibration, DSP, state-estimation, geospatial, uncertainty, verification and optimization primitives survive those workloads, that is evidence for the shared instrumentation boundary.

If they require hidden domain-specific assumptions, those failures belong in the research ledger.
