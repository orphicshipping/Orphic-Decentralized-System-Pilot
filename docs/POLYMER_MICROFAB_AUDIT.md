# Polymer microfab + discrete manufacturing: source audit and first slice

## Namespace integration and retained-record compatibility

This dossier increment is stacked on the manufacturing runtime in PR #177,
which includes the plan-envelope declarations from PR #176. Three distinct
entry points coexist: `net polymer microfab` compiles declaration envelopes,
`net polymer manufacture` records manufacturing state and genealogy, and
`net polymer dossier` binds retained Session cycle results into production
review evidence. The Python owners are `ciw.microfab`, `ciw.polymer_microfab`,
and `ciw.polymer_production_dossier`, respectively. None shadows another.

The dossier's earlier, unmerged implementation used the `polymer_microfab`
module and command name. Its record schemas and scientific calculations remain
unchanged, but its runtime commitment changes: `runtime_identity()` includes
the normalized source bytes and filenames of both implementation modules.
The exact earlier runtime hash from commit `418e06bc` is
`22fa6f4a7b1a9556b5290777e67be4ca1576ebb2e653237aee42d1a881eaa5f5`.
Inspection, verification and replay explicitly refuse that prior runtime under
the renamed implementation. They do not rewrite the saved dossier or silently
migrate its identity. Keep older evidence and its original runtime together;
to use the new implementation, explicitly run the original request into a new
output directory, creating fresh occurrences and a new runtime commitment.

The original qualification receipt below remains historical evidence of its
original source increment. The namespace integration adds real three-way
native-command/API coexistence tests and three unchanged-artifact/legacy-runtime
refusal tests. Current qualification is retained separately; an earlier passing
hosted run does not qualify this changed head.

Audit date: 5 October 2026. Repository resolved by the connector to
`atomtrapping/Notations-Engineering-Terminal` (the Systems-Terminal URL redirects).
Audited base: `0091492b9dc237b05c33cac77d2307115bd803b2`, tree
`41d6e57123c18a075310cafa74a9a409ce2a3d76`.

## Finding

The repository contains reusable executable contracts, numerical models, native
measurement-ingress validation, retained Session operations and independent
polymer numerical checks. This is a substantial software substrate for the
proposed workload, not merely an architecture described in README files. It does
not establish a commissioned manufacturing cell, universal polymer processing,
comparative superiority over other platforms, or physical/safety qualification.

The audit inspected selected source modules from the CI-built wheel for that
exact commit, rather than the entire repository's tests, native executables or
all pending branches. The archive and wheel hashes were verified against the
retained build manifest. No whole-repository clone was available. The focused
regression and existing polymer qualification ran against that real package
source with this additive overlay. The receipt records this limitation and the
actual local dependency versions.

## Six-criterion matrix

| Criterion | Existing executable implementation | Remaining gap | Reuse / refactor / add |
|---|---|---|---|
| 1. Typed physical identities and provenance | `core/identities.py` separates content and occurrence identities. `polymer_contract.py:validate_request` binds facility/machine/tool/material lot/cycle/part and exact sensor units; `industrial_transition.py` validates declared cross-system identity continuity. | Production identities are declarations, not authenticated physical identity. No shared global serial reservation or expanded manufacturing BOM/BOP is established. | Reuse existing identities and cycle source. **Added** part revision, work order, material specification, cell/cavity and per-dossier serial/shot-slot bindings. Do not create a duplicate canonical entity registry. |
| 2. Synchronized measurements and calibration | `calibrated_window.py` validates full ordered joint covariance, exact PSD constraints and Jacobian projection; `polymer_ingress.py` retains native clock/calibration bundles and exact provenance while projecting marginal samples. `polymer_metrology.py` checks freshness, condition and declared skew. | Matching clock labels/nominal times is not certified synchronization. No newly commissioned clocks, traceable calibration chain or physical sensor was tested here. Native ingress deliberately withholds cooling bindings when its covariance assumptions are not supported. | Reuse ingress, including its abstentions and retained joint covariance. **Added** explicit same-sensor, frame, nominal forecast-time and calibration-reference checks for a separate follow-up. Do not collapse unknown correlation into independence. |
| 3. Composable physics/state runtimes | `system_spec.py`, `system_models.py`, `system_execution.py` implement a bounded thermal/mechanical/sensor composition and bounded workers. `polymer_models.py` has lumped cooling, pressure-threshold timing and toy control; `polymer_verification.py` independently audits the finite calculations. | Not a full injection-flow, viscoelastic, cure, shrinkage, tooling or qualified plant-estimation model. Pending science branches were not included in this base audit. | Reuse current Session operations; no new solver in this change. Add separately qualified material/process providers later. Do not refactor existing numerical kernels merely to fit manufacturing names. |
| 4. Bounded dispatch and authority separation | `operations/runner.py` validates source/dependencies, detaches provider data and retains refusals. Polymer control accepts `simulation_only`. `industrial_transition.py` stops at external authority review. | `interop_ingress.py` explicitly does not implement/certify an OPC UA or MTConnect parser. No live OPC UA client, PLC-write adapter, authenticated machine authorization or real-time safety controller is delivered. | Preserve current dispatch and admission boundaries. **Added** review-only production projection and fixed no-actuation/no-release authority. Future machine transport must be a separately commissioned adapter, not an extension of this dossier's trust. |
| 5. Geometry/frame and simulation/physical correspondence | Cycle ingress enforces exact frames. `native_interop.py` binds pinned native providers and retains transport bytes; geometry contracts validate explicitly ordered frames, coordinates and covariance. | Generic representation/geometry tooling is not a commissioned mold/cavity/sensor registration. No new geometric transform or measured registration residual was qualified. | Reuse explicit mapping/preservation contracts; **added** cavity identity membership and frame binding, not geometric calibration. Add measured mold/part/sensor registration and uncertainty propagation separately. |
| 6. Residual and qualification evidence persistence | `polymer_workflow.py` retains requests, Session workspaces, assessment/execution/result/verification occurrences, inspect/replay and fresh read-only audits. | Existing cycle evidence alone does not constitute per-order genealogy, a held-out temperature residual or product release. | Reuse workspaces and create-only storage. **Added** per-part production dossier, nominal-time model/measurement residual, retained hold decisions and a separate aggregate fresh-audit receipt. No release certificate is granted. |

The refurbishment workload also has work-order records. Its asset repair,
customer and warranty state machine is not reused as a molding production state
machine. Both continue to share NET's underlying evidence primitives. A generic
cross-domain production registry, if added, should unify authoritative identity
and admission rather than duplicate either workload's declarations.

## What this increment implements

The first slice is a bounded **single-material injection-molding evidence
campaign**, linking:

`requirement/BOM/route references -> part revision -> material lot -> configured
cell/tool/cavity -> work order -> retained cycle -> serial -> metrology ->
prediction residual -> engineering-review dossier`.

BOM and route are immutable content references, not an expanded route executor or
material-consumption ledger. Assembly, inventory transactions, rework routing,
certificates and shipment release are not implemented. Uniqueness checks apply
within one dossier, not across an unimplemented enterprise serial registry.

Every entry calls the existing `polymer_workflow.run`, retaining the original four
Session operations: assessment, copilot context, simulated control and numerical
verification. No new operation identity namespace, state owner or admission path
is introduced. Optional full native ingress is validated and passed through to
the existing workflow; its scientific abstentions are preserved. A valid native
provider campaign was not executed in this increment.

The request binds the exact part-revision tolerances and cooling model. A batch
can retain up to 16 parts; aggregate budgets are 16,384 sensor samples, 4,096 toy
control cycles and 4 MiB of request JSON. Duplicate serials, duplicate
cycle/cavity slots, inconsistent same-shot configurations, reused selected
part-evidence identifiers, mixed observed/synthetic parts, and mismatched
material/cell/frame/model/specification bindings are refused before execution.
Inputs are detached. Identifiers never become directory paths. Publication is
create-only; partial child evidence and a failure receipt survive ordinary
execution exceptions. An incomplete directory cannot inspect as a complete
production dossier.

### Residual semantics

A follow-up is an explicitly declared held-out part-temperature record, in K,
from the model's bound sensor/calibration/clock. Its nominal timestamp must equal
`cycle.end_s + forecast_horizon_s` and be strictly later than the input window.
There is no implicit interpolation, unit conversion or frame transformation.
Distinct evidence references prevent direct input reuse; they do not prove
observation authenticity or statistical independence.

For retained prediction `p`, follow-up `y`, supplied standard uncertainties
`u_p`, `u_y`, multiplier `k`, and retained timing-temperature bound `b_t`:

```
r = y - p
u_upper = u_y + u_p
residual_interval = [r - k*u_upper - b_t, r + k*u_upper + b_t]
```

The sum is a conservative standard-uncertainty bound for unknown correlation
**of the supplied uncertainties**, not an assertion that model discrepancy is
known or that the interval has validated probabilistic coverage. The upstream
cooling profile's assumptions remain unchanged. Time matching is nominal, not
certified synchronization. A missing follow-up or abstaining model produces
INDETERMINATE, not a zero residual. Tolerance-boundary overlap stays indeterminate.

The synthetic demonstration's follow-up was authored as 323 K; it is not an
acquired measurement or a fitted-material validation point.

### Authority and operator decisions

`workflow_status=PASS` means the retained computational workflow completed with
its numerical checks. It can coexist with `HOLD_NONCONFORMING`,
`HOLD_INCOMPLETE_ORDER` or `HOLD_INDETERMINATE_EVIDENCE`.
`READY_FOR_ENGINEERING_REVIEW` is not production acceptance.

All outcomes retain: physical controller authority external, safety controller
false, OPC UA writes false, actuation not performed, authorization not granted,
state admission not performed, physical validation/calibration traceability not
established, product and shipment release not authorized. The stored control
proposal remains the original simulation-only proposal. There is no command to
send it to equipment, even when every software test passes.

Hashes detect content inconsistency, not an authenticated operator or malicious
coherent replacement of an entire dataset. BOM/drawing/material references are
not automatically fetched or independently certified.

## Commands

After installing this candidate:

```sh
net polymer dossier example --output order.json
net polymer dossier run order.json --output-dir production-evidence
net polymer dossier inspect production-evidence
net polymer dossier verify production-evidence --output fresh-audit.json
net polymer dossier replay production-evidence --output-dir production-replay
```

For the synthetic demonstration:

```sh
net polymer dossier demo --output-dir microfab-demo
```

All destinations must be new. A fresh audit receipt belongs outside the immutable
dossier directory. Inspection validates bindings and recomputes the deterministic
residual projection without executing numerical providers. Fresh verification
executes the existing independent cycle audits; the residual projection is
recomputed, **not** independently reimplemented at runtime. Decimal interval
checks and rejection tests cover that arithmetic in the regression suite. Replay
preserves scientific evidence identity and allocates new Session occurrences.

## Validation and integration boundary

`tests/test_polymer_production_dossier.py`: 75 local tests passed, no skips. Existing
`net polymer qualify`: 18/18 checks passed for injection and extrusion-blow
molding. The new CLI demo, inspect, fresh audit and replay passed. The JSON
receipt records commands, JUnit and changed-source hashes, versions and limits.

Local execution used the actual base-wheel package sources with the candidate
overlay under `PYTHONPATH=src`, not a newly built candidate wheel. Linux/Python
3.13.5, pytest 9.0.2, NumPy 2.3.5; NumPy differs from the repository's 2.4.3 pin.
Full-repository tests, a fresh installed candidate wheel, Windows and new native
provider execution have not been established locally. The added CI definition
covers the installed candidate on Linux/Windows and Python 3.11/3.13 with pinned
project dependencies. A workflow definition is not a successful hosted run.

Open science PRs #173/#174/#175 and portability PR #172 remain separate. In
particular, #175's cooling-uncertainty repair and science extensions are not
claimed as part of this main-based slice. The shared polymer CLI additions must
be preserved when those branches integrate. No existing branch, release tag,
license, dependency pin or provider grant is overwritten by this change.

## External comparison, not qualification

Ortiz et al., *Bidirectional immersive digital twin for real-time monitoring and
supervisory interaction in PLC-controlled manufacturing* (published 26 September
2026), DOI `10.1007/s00170-026-19127-w`, supports keeping supervisory interaction
outside the PLC's process-control authority. Its laboratory implementation does
not certify NET, a molding machine, or a functional-safety system.

The next physical integration slice is a read-only, machine-specific retained
state adapter with explicit node/asset/cavity mappings, calibrated measurements,
clock evidence and dropout/staleness tests. Authenticated supervisory command
handoff and controller acceptance must remain separate from that observational
path and from any independently engineered safety system.
