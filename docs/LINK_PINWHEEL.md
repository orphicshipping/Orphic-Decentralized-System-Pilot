# Link Industrial Pinwheel: retained evidence to a next-experiment proposal

## Named investigation and delivered scope

An existing GaugeLink scalar study and `notations.measurement.v1` bundle now
support an explicit uncertainty-improvement review, independent arithmetic audit,
read-only MCP inspection, fresh-occurrence replay and five-blade offline report.
This extends [Link measurements](LINK_MEASUREMENTS.md) and the existing
[scalar study](LINK_SCALAR_STUDY.md), not the shared Workbench or device authority.

The organizing brief names **Augment, Measure, Translate, Understand,
Operationalize** around a **Verified Physical State** target. Here that hub is
an inspection projection of the original source, NOT a new canonical-state
owner. Its physical status remains **NOT_ESTABLISHED**. No percentage-complete
or all-green physical qualification is inferred from software tests.

| Blade | Executable connection | What remains outside this increment |
| --- | --- | --- |
| Augment | Existing captured bytes, device/firmware/configuration and procedure | Installing or qualifying a sensor/electrical interface |
| Measure | Existing declared affine correction and full joint covariance | New calibration fit, traceability, fleet recalibration |
| Translate | Exact existing measurement bundle and native record ancestry | New protocol drivers, coordinate transforms, genealogy reducer |
| Understand | Original classical mean plus conditional uncertainty analysis | New physics/PINN, trained model or lifetime prediction |
| Operationalize | Inspectable proposal and a separate arithmetic audit | Authorization, deployment, machine commands or physical verification |

The feedback is **Understand -> Operationalize -> Augment**: identify a
hypothetical measurement improvement worth testing. It is not evidence that a
new instrument, adapter or calibration has already improved anything.

## Mathematics and restrictions

For the existing mean `y = a * mean(x) + b`, use the original ordered vector
`[readings..., gain, offset]` and gradient `[a/n, ..., mean(x), 1]`.
The calculation retains the native covariance and all terms of `g Sigma g^T`.
Each paired off-diagonal contribution is `g_i (Sigma_ij + Sigma_ji) g_j`;
no symmetrization, diagonalization or clipping is performed.

`abs(g_i) * sqrt(Sigma_ii)` is a marginal sensitivity scale, NOT an additive
causal attribution when cross-correlations exist. Signed cross-terms are
separately inspectable. Reducing an individual uncertainty can remove error
cancellation and INCREASE the combined uncertainty. The regression suite
contains an exact singular-covariance example of that behavior.

One to eight operator-declared candidates specify diagonal error-amplitude
scales `D` between zero and one. The scenario covariance is `D Sigma D`, with
all original means and the original first-order linearization held fixed.
This is NOT statistical conditioning, a new sensor fusion estimate, measured
performance, a complete error budget or an optimal sensor-placement algorithm.
Shared calibration error is not assumed independent between readings.

Candidates are ranked by projected variance and then their explicit ID. The
lowest-variance improving candidate is proposed only when the declared baseline
uncertainty objective is not already met. A candidate need not meet the goal to
be worth testing; its `objective_met` field makes that limitation visible. Costs,
expected resale prices and dollar ROI are not invented or used.

Unknown input covariance produces **UNCERTAINTY_UNKNOWN**, no candidate ranking
and a request for an evidence-supported budget. Zero variance from a declared
model is not a physical-accuracy claim. Nonfinite, unrepresentable nonzero or
negative projected values refuse; there is no silent clamp to zero.

The Decimal primary kernel uses 4096-digit local precision and divides an exact
quadratic numerator once. The separate Fraction-based audit does not call that
kernel. It checks gradients, covariance terms, every scenario matrix, variance,
ranking and threshold outcomes. Standard-uncertainty comparisons allow eight
binary64 ulps for square-root rounding. The original two-stage model variance
is retained beside the direct quadratic projection and their rounding difference.
This first-order budget does not include undeclared nonlinear/model discrepancy.

## Exact identities and lifecycle

- Plan: `ciw.link-improvement-plan.v1`, at most 32 KiB, exact original bundle,
  evidence, model-result and audit-result references; same original output unit.
- Review: `link.review-improvement.v1`, existing decision role, PROPOSAL_ONLY.
- Audit: `link.audit-improvement.v1`, existing verification role, arithmetic only.
- Both use the existing OperationRegistry, CapabilityRegistry, typed DAG,
  source-bound prerequisite checks and Session. No source-loaded executable.
- Existing four study results/executions remain byte-equivalent. Review adds
  only two occurrences (or a retained refusal prefix); graphs account for them.
- At the operation boundary the bundle reference is declared. The enclosing
  `link_pinwheel` lifecycle checks it against the actual validated full bundle.
- Original request/workspace/graph bytes travel inside the unchanged measurement
  bundle. `restore-source` recovers them exactly; `replay` creates only new review
  occurrences, not new measurements or a new original calibration/model run.
- Reopen verifies retained structures/calculations without hardware, a bound
  numerical provider, network access or new operation occurrences. It may perform
  consistency arithmetic; it is not a claim of zero validation computation.

The `instance_id` is a local operator label, NOT tenant authentication or an
access-control boundary. This increment aggregates no customer sites, transmits
nothing, trains no model and establishes no contribution permission. The existing
network identity/consent work remains separately owned. No ESM admission,
production release, update installation, RF or apparatus action is available.

## Commands

Install the reviewed candidate and its unchanged dependencies, then use new
output paths:

```sh
net link study example --output synthetic-request.json
net link study run synthetic-request.json --output-dir scalar-study
net observation ingest scalar-study --output measurements.json
net link pinwheel plan measurements.json --instance-id local-lab \
  --target-uncertainty 0.03 --output improvement-plan.json
net link pinwheel run measurements.json improvement-plan.json --output-dir review
net link pinwheel inspect review
net link pinwheel report review --output pinwheel.html
net link pinwheel replay review --output-dir review-replay
net link pinwheel restore-source review --output-dir original-study
net link pinwheel mcp review
```

The plan command offers three explicitly hypothetical 50% reductions in reading,
gain or shared-offset error amplitudes. Supply a reviewed edited plan for other
scenarios. No command changes the existing instrument or original study.

MCP uses the existing AgentHost and stdio server, a frozen `investigation` alias
and zero execution grants. The offline HTML has no script or network resources,
five navigation areas, numeric results, full evidence details and a conspicuous
NOT ESTABLISHED physical-state target. It is not the deployed company website.

## Validation and integration ownership

```sh
python -m pytest -q tests/test_link_pinwheel.py
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
python scripts/check_link_installation.py --wheel dist/<wheel>.whl \
  --output-dir results/pinwheel-installed --require-pins --with-pinwheel
```

The qualifier includes inherited Link, connectivity, scalar and measurement
cases, byte matching of the complete installed package, original command
journeys and six pinwheel CLI journeys. A separately selected
`--allow-host-dependencies` run records non-pinned local evidence; it never counts
as an exact-pin supported-release pass. Required unavailable dependencies block.

No PR200/185 data bridge, shared Session/runner/covariance contract, manufacturing
store, network consent code, license or dependency pin is changed. This branch
builds on PR208. Workbench attachment retains its separate PR200/185 dependency.
Physical gauge/board, ARM/Windows serial behavior, timing/calibration, enclosure,
fleet deployment, Parquet/database and full-monorepo gates remain separate.
