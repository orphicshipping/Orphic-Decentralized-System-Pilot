# Study-bound discrete manufacturing genealogy

This increment connects a retained engineering study to the **existing** manufacturing reducer. It does not introduce another ledger, inventory owner, release service or physics solver. It is stacked on the Polymer Microfab + Discrete Manufacturing Runtime, PR #177, commit `dd8782a9df195a79769cbe9e8d63579578dc248e`.

## The minimum connected path

`study + assessment -> selected candidate -> part/revision + requirements -> material specification/lot -> configuration + route + work order -> cycle/cavity/serial -> bound inspection observations -> existing disposition -> lot or serial trace`

`ciw.manufacturing_study` embeds the original manufacturing request, calls `manufacturing_runtime.replay`, and retains that result unchanged. The original reducer remains the sole owner of disposition, inventory allocation, event ordering and v2 quality holds. Its observation and calibration declarations are resolved locally against retained JSON objects rather than accepted as disconnected hashes.

### Added here versus inherited

| Boundary | Implementation |
| --- | --- |
| Part revision, inventory, cycle/cavity, serialized output, BOM/route, scrap and v2 process holds | Inherited unchanged from PR #177 |
| Study/candidate/material and per-cycle calculation bindings | Added in this adapter |
| Explicit characteristic-to-design-dimension or requirement-support links | Added in this adapter |
| Inline inspection sample, serial/lot/cycle/cavity and declared calibration applicability | Added in this adapter |
| Study coverage, inspection nonconformance review projection, serial/lot dossier trace | Added in this adapter |
| Operator entry point | Additive delegation under `net polymer manufacture study` |

## Boundaries enforced

Every selected component requirement stays attached to the exact part revision, including requirements that these inspections cannot establish. Material identity binds the entire retained material record, not just its grade name. Inspection links cannot substitute another component's requirement, weaken its declared limit or exclude the design nominal. The selected forming cycles bind the retained assessment explicitly.

Every retained inspection reading needs a corresponding evidence entry, including explicit missing evidence. A present sample binds its actual value, supplied expanded uncertainty, exact unit, condition, instrument, logical frame/clock, serial, part revision, material lot, cycle and cavity. Observation occurrence IDs cannot be reused. Sampling must occur after the recorded part birth and no later than the inspection event.

Calibration objects are **declarations**, not authenticated certificates. Their content references, instrument, source kind, units, frame/clock and validity interval are checked. A missing observation or explicitly missing calibration remains an indeterminate inspection and quarantine through the existing reducer. A supposedly resolved but stale, mismatched or absent calibration object is refused; the adapter does not silently strip or replace the invalid claim. Certificate backing files are not fetched or authenticated.

The inherited inspection rule accepts a declared measurement interval only when it is wholly inside the specified interval. A wholly outside interval fails; overlap with a limit is indeterminate. Unknown uncertainty is never assumed to be zero. These intervals are conditional on the supplied uncertainty declarations, not established probabilistic coverage.

V1 manufacturing retains its final-inspection-only policy. `AVAILABLE` in that profile does **not** claim process QC. V2 requires the original monitoring policy: a dimensional pass cannot clear absent or unacceptable process evidence, and a process pass cannot clear a dimensional or manual hold. The dossier exposes which profile was used and preserves v2 record-release gates.

The inspection nonconformance list is a read-only review projection of the existing reducer, **not a second nonconformance state machine**. Existing v2 process and manual nonconformances remain in the unchanged manufacturing result. Scrap preserves the birth and failed inspection history. No hold-release, rework, concession or shipment authorization is added.

## Retained study and synthetic demonstration

The fixtures retain the supplied `NET-Polymer-Engineering-Design-v1.zip` study and assessment JSON. They are not rerun or requalified here. The selected candidate is `tab-reference`, part `CONCEPT-TAB`, revision `A`: an idealized closure-tab feature, **not a complete qualified closure or container**.

The example declares one synthetic resin lot, one forming work order, two two-cavity cycles and four serials. Thickness tolerances, observations, calibration declarations, allocation masses and cell settings are authored synthetic test data. They are not validated molding conditions, acquired metrology or produced physical parts.

| Serial | Final inspection | Record disposition | Cause |
| --- | --- | --- | --- |
| tab001 | PASS | AVAILABLE | Both declared inspection intervals inside limits |
| tab002 | FAIL | SCRAPPED | Thickness interval wholly above the limit; explicit scrap event |
| tab003 | INDETERMINATE | QUARANTINED | Thickness uncertainty interval overlaps the upper limit |
| tab004 | INDETERMINATE | QUARANTINED | Thickness calibration explicitly missing |

The lot begins with 0.001 kg. Two cycles allocate 0.0006 kg in total: 0.0004 kg to four parts and 0.0002 kg to process waste, leaving 0.0004 kg. Scrapping a part does not return its mass to inventory. These are declared accounting quantities, not measured mass closure.

**Shipment is HELD for every part.** The retained mass calculation and measured mass characteristic remain different evidence. Tab deflection, stress, life, process review, body and assembly/interface obligations are not erased. All physical-conformity conclusions remain unestablished. The supplied v1 engineering study supports synthetic material declarations only; a `retained_observation` production source label would still require genuine operator-supplied evidence and would not authenticate acquisition automatically.

## Commands

After installation of this candidate and its PR #177 parent:

```sh
net polymer manufacture study example --study examples/study-manufacturing/study.json --assessment examples/study-manufacturing/assessment.json --output tab-order.json
net polymer manufacture study run tab-order.json --output tab-dossier.json
net polymer manufacture study verify tab-dossier.json --output tab-replay-check.json
net polymer manufacture study trace tab-dossier.json --lot synthetic.lot.1 --output lot-trace.json
net polymer manufacture study trace tab-dossier.json --serial tab002 --output scrap-trace.json
```

For the focused source snapshot, replace `net polymer manufacture` with `python -m ciw.polymer_manufacturing_cli` and set `PYTHONPATH` to its absolute `src` directory. The source module delegation was executed locally; the full native NET executable was not available in that snapshot.

All output paths must be new. No output is overwritten. Exit 0 means the requested record operation completed; **it is not a product acceptance or release decision**. Invalid requests exit 2. `verify` replays the same reducer under a fresh verification occurrence without modifying retained evidence. Content identity stays separate from execution identity.

## Validation and remaining gates

The local combined run passed **311 tests**, with **one skipped** original full-NET-dispatch test because the focused source snapshot lacks unrelated NET modules. This comprises 132 new adapter tests and 179 passing inherited manufacturing tests. New tests include 27 independent Fraction interval decisions, tiny positive uncertainty at a limit, coherently rehashed substitutions, causal timing, calibration, nonconformance review, actual v2 tool-point assessment, final/process separation, all module-CLI commands and create-only outputs.

A deliberately changed and coherently rehashed numerical metric can still be retained as **unverified calculation content**. The adapter tests this limitation explicitly: it does not substitute hash checking for the upstream engineering numerical verifier. Replay is not independent numerical verification, calibration traceability or physical validation.

The new workflow defines Linux/Windows, Python 3.11/3.13 source and installed-wheel checks, including the native command outside the source checkout. A workflow definition is not a successful hosted run. Local environment, exact hashes and limitations are in `validation/study-manufacturing/local-qualification.json`.

Full-repository regression, clean full-repository wheel installation, hosted CI, Windows, genuine acquired material/part data, certificate authentication, hardware qualification and production acceptance remain unestablished. This adapter's sample schema covers formed parts; assembly metrology, global serial reservation and live acquisition are not introduced. No NET Session operation registration, State Ledger admission, machine write or safety-controller authority is added.
