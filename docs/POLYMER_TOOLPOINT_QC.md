# Tool-point QC for NET Polymer Microfab + Discrete Manufacturing Runtime

## Implemented boundary

This increment adds an **offline point-of-process quality-control instrument**
for retained injection-molding signals, plus enforced quality gates in the
manufacturing record reducer. It extends the foundation in PR #177; it does not
replace NET's identity encoder, State Ledger, measurement stack or machine safety
controller. The existing v1 request and replay result remain unchanged.

The two independent product-quality conditions are:

```text
process signature acceptable != final inspection passed != shipment authorized
```

A clean process signature cannot clear a dimensional failure. A passing final
measurement cannot clear a process hold. A later hold propagates through recorded
assembly containment without deleting consumption, packaging or inspection
history. No physical part is rejected or shipped by this software increment.

## Implemented records and files

| Record or function | Implementation |
| --- | --- |
| Revision-bound quality requirements and final inspection | Existing part catalog and `inspection_plan`; exact part content identity is reused |
| Tool-point monitoring plan | `polymer_toolpoint_qc.py`: `ciw.toolpoint-monitoring-plan.v1` |
| Retained signals and sampled uncertainty | `ciw.toolpoint-observation.v1`; meaning-bound channel content hashes |
| Cycle-quality assessment | Per-rule, per-cavity, shared-channel and cohort decisions, residual intervals and causal diagnostic hypotheses |
| Manufacturing QC policy and nonconformances | `manufacturing_quality.py`: order-to-plan bindings, process and final-inspection holds, downward assembly propagation |
| Manufacturing integration | `manufacturing_runtime.py`: explicit v2 request/report, QC events, release views, quality-aware order counts and genealogy |
| Operator entry points | `polymer_quality_cli.py`, existing manufacturing CLI with `--quality-control`, and `net polymer qc` dispatch |
| Reproducible demonstrations | `polymer_quality_example.py`; all curves and thresholds are authored synthetic fixtures |

## Monitoring plans

A plan binds its own ID/revision to the exact part and complete polymer cell
configuration, source semantics, clock, tool frame, validity window, cavity set,
reference-basis references, phase windows and sensor locations. A configuration
change cannot inherit the old plan. Each forming work order in manufacturing v2
must bind exactly one declared plan; missing bindings are refused.

Supported measurement meanings are melt pressure (`Pa`), cavity pressure (`Pa`),
tool temperature (`K`) and screw position (`m`). Locations distinguish the shared
nozzle and screw from cavity-local hot-runner tips, near-gate points, end-of-fill
points and tool-body temperature measurements. Every configured cavity needs local
monitoring coverage. Every listed sensor must have a rule. No unit aliases or
implicit conversions are accepted.

Rules bind fixed, nonoverlapping process phases, expressed in seconds relative
to the retained injection/capture trigger. Phase boundaries are supplied; this
increment does not infer filling/packing transition time from the machine.
The synthetic example monitors nozzle peak pressure and reference residual,
near-gate peak/packing integral, end-of-fill pressure arrival, and tool temperature.
It is not a recommended recipe or a validated defect classifier for a real tool.

## Numerical semantics

The implemented features are **peak**, **pressure-time integral**, **first pressure
threshold arrival**, and **maximum absolute trajectory residual**. Samples retain
values and supplied expanded uncertainty. Unknown uncertainty remains unknown.

For a sampled value `y_i` with supplied expanded uncertainty `u_i`, the numerical
interval is `[y_i-u_i, y_i+u_i]`. Peak intervals use the maxima of the lower and
upper envelopes. Pressure integrals use trapezoidal integration of those envelopes.
No statistical independence, square-root-of-N reduction, or simultaneous coverage
probability is inferred. These are interval calculations on supplied declarations,
not calibration certificates or guaranteed physical error bounds.

Signals and reference curves are piecewise-linear between their supplied points.
Rule endpoints may be interpolated but never extrapolated. Residual extrema use
the union of measurement and reference knots, so an intermediate reference peak
cannot disappear just because it was not a measurement timestamp.
Arrival uncertainty is bracketed by the first upper-envelope and lower-envelope
threshold crossings, expanded by the supplied clock-skew bound. Arrival already
possible at the first phase sample is left-censored and insufficient, not time zero.
A fully observed non-arrival after the latest acceptable time is a process
deviation, not proof of a short shot or a blocked gate.

Peak, integral and residual intervals are **conditional on declared aligned
sample times**. Clock skew is an admission check and is explicitly propagated to
arrival timing; full timestamp jitter, sample-clock covariance and their effects
on all other features are not propagated. Unobserved intersample spikes are not
certified absent. Calibration-source and reference-source uncertainties are not
re-estimated here. These limitations must be included in physical qualification.

Decimal arithmetic at 512-digit precision preserves tiny positive supplied
uncertainties at comparison boundaries. It does not restore precision lost before
the data entered the runtime. Reference curves carry distinct model and content
references; evaluation never refits a curve to hide its residual.

## Decisions and missing evidence

A rule returns:

| Decision | Meaning |
| --- | --- |
| `PROCESS_SIGNATURE_ACCEPTABLE` | Entire computed interval is inside supplied limits, with the required evidence and phase coverage |
| `DEVIATION` | Computed interval is wholly outside limits, or the arrival deadline is observably missed |
| `INDETERMINATE` | Interval overlaps an acceptance boundary |
| `INSUFFICIENT_EVIDENCE` | Missing/invalid evidence quality, incomplete capture, unresolved arrival, or an expired declaration |

Absent channels, bad/saturated/stale/unknown channel-health declarations, unknown
uncertainty, missing calibration or alignment references, declared calibration
expiry, excessive clock skew, excessive original sampling gaps and incomplete
coverage do not become passes. Malformed records, changed sample hashes, reordered
timestamps, future samples, unit substitutions and inconsistent identities are
refused rather than silently repaired.

A retained capture prefix can establish an early high-pressure or residual
excursion. It cannot establish an acceptable full shot before every required phase
and feature is resolved. Detection timestamps are measured in the retained trace;
no wall-clock latency, real-time deadline or machine-response capability is claimed.

A high nozzle peak together with delayed/nonobserved local pressure arrival can
produce `FLOW_DEVIATION_SUSPECTED`, with its supporting rule IDs. Root cause remains
`not_established`; the instrument does not assert `GATE_BLOCKED`.

## Part containment and discrete manufacturing

Manufacturing v2 adds one required top-level `quality_control` policy and accepts
`quality_assessment` and `quality_hold` events. Existing v1 requests reject these
new fields/events. Keeping v1 available is backward compatibility, not a statement
that v1 has in-process QC or is eligible for physical release.

At a forming birth, process quality starts at `AWAITING_EVIDENCE`. Final inspection
may occur before or after QC, but assembly and packaging require both acceptable
process evidence and passing final inspection, with no open hold. Assembly records
have their own final inspection and inherit later component holds through genealogy.

A QC observation must match the cycle ID, its retained cycle-evidence reference,
the exact configuration and part, and the cavity-to-serial map. Acquisition must
end no later than the recorded cycle completion, which must precede or coincide
with the QC event. There is one final retained assessment per cycle; reassessment
cannot overwrite or clear an earlier hold. Prefix evaluation is available in the
standalone instrument, but incremental streaming assessment/admission is not
implemented in the manufacturing reducer.

A local sensor deviation holds the mapped local output; a shared-channel deviation
holds all affected outputs. Without a resolved, evidence-referenced cavity map,
the manufacturing reducer holds the entire recorded cycle cohort, even when some
signal signatures look normal. Unresolved hints cannot introduce foreign serials.

`quality_hold` records an independently declared concern about an existing serial.
Its hold propagates to every recorded descendant assembly, including one already
packed. `CONSUMED`, `PACKED` and `SCRAPPED` logistics states remain intact. The
quality release view becomes blocked, and current accepted quantities exclude
held records. Open nonconformances retain their reason, evidence reference,
origin/affected serials and event reference. Clean process data does not relabel a
manual or dimensional concern as a process defect.

Holds are monotone in this increment. Scrap is retained as a disposition record;
rework, disassembly, use-as-is concessions, authenticated hold release, automatic
reject sorting, certificates, recall execution and shipment release are deferred.
Packaging and every shipment remain `HELD`/not authorized at the release boundary.
A coherent rewrite of all source declarations is still unauthenticated; replay
verification does not prevent such a rewrite or confer production authority.

## Evidence and authority

Meaning-bound sample hashes are checked against the actual embedded sample data.
External calibration, alignment, cycle-source, model and authorization references
remain declared references; their backing artifacts are not fetched or physically
validated by this increment. The new inline signal profile is not a certified
adapter for the existing native metrology bundle or OPC UA/EUROMAP acquisition.

Evidence content IDs, operation ID, evaluation occurrence IDs and fresh
verification occurrence IDs remain distinct. The existing NET canonical encoder
is reused unchanged. Same-implementation replay is an integrity/regression check,
not an independently implemented scientific verifier or a State Ledger admission.

No acquisition driver, fieldbus write, PLC/robot command, execute grant, physical
containment or closed-loop correction is added. Physical machine interlocks and
safety control remain outside this code. No quality plan, fixture or test count
constitutes physical qualification.

## Run

From a source checkout containing the foundation and this increment (POSIX):

```sh
PYTHONPATH=src python -m ciw.polymer_quality_cli demo --case normal --output normal-qc.json
PYTHONPATH=src python -m ciw.polymer_quality_cli demo --case flow-deviation --output abnormal-qc.json
PYTHONPATH=src python -m ciw.polymer_quality_cli verify abnormal-qc.json
PYTHONPATH=src python -m ciw.polymer_manufacturing_cli demo --quality-control --output qc-manufacturing.json
PYTHONPATH=src python -m ciw.polymer_manufacturing_cli verify qc-manufacturing.json
PYTHONPATH=src python -m ciw.polymer_manufacturing_cli trace qc-manufacturing.json --serial p004
```

PowerShell uses `$env:PYTHONPATH = "src"` before the same `python -m ...` commands.
Always use new output names; atomic create-only publication never replaces a
retained file. The QC CLI returns 0 for an acceptable signature, a successful
verification, or creating an example; 2 for a nonacceptable QC assessment; 1 for
malformed input or publication failure. Verification PASS concerns replay integrity,
so an accurately retained abnormal assessment can verify successfully. The
manufacturing CLI's exit 0 remains completion of record processing, not release.

In an installed full repository, the same instrument is dispatched as
`net polymer qc`, with `example`, `assess`, `demo`, and `verify` subcommands.
`net polymer manufacture demo --quality-control` selects the integrated v2 fixture.
Both full dispatcher tests are retained but were skipped in the focused snapshot
because unrelated repository dependencies were unavailable.

## Validation performed and remaining gates

`PYTHONPATH=src python -m pytest -q tests/test_polymer_toolpoint_qc.py tests/test_polymer_manufacturing.py`

Local result: **364 passed, 0 failed, 2 skipped**. This includes all 179 passing
foundation tests, 25 independently written Fraction-based integral oracles,
hand-calculated peak/arrival/integral/residual values, capture-prefix causality,
uncertainty-boundary stress, evidence/identity mutations, mapping faults, hold
propagation, reference-knot residual tests, replay tampering and CLI/publication
failures. The v1 synthetic result equals the retained pre-QC baseline exactly.

A separate branch-coverage run reported **93% combined coverage across the four
new modules only** (559 statements, 282 branches), not repository-wide coverage.
The coverage and test logs are retained in the change-set evidence bundle.

Repository cloning failed because the execution container could not resolve
GitHub. The focused source snapshot was read through the connector and the
modified-file baselines and shared identity module were checked against Git blob
identities. Full-repository regression, installed-wheel execution, Windows,
hosted CI, real sensor acquisitions and physical defect-detection performance
remain unverified. Existing provider code, license, dependency pins, allowlists
and physical execution authority are unchanged.

## External basis versus this implementation

Kistler's primary technical material describes in-mold pressure/temperature
sensing, purpose-dependent placement, and improved short-shot resolution near
end of fill. It also explicitly distinguishes a measured pressure signature from
a validated relationship to the required part characteristics. Those sources
support the sensing architecture, not this code's synthetic thresholds, detection
performance, release rules or physical qualification.

Sources inspected 2026-10-05:
- Kistler, Sensors for monitoring of the injection molding process:
  https://www.kistler.com/GB/en/sensors-for-monitoring-of-the-injection-molding-process/C00000060
- Kistler, Real-Time Release Testing in medical injection molding, especially
  the question on whether cavity pressure alone determines whether a part is good:
  https://www.kistler.com/CA/en/real-time-release-testing-in-medical-injection-molding/C00000042
