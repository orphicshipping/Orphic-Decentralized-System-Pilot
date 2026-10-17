# Executable materials investigations through NET Session

## Scope and status

This increment turns the missing retained-execution paths in the polymer,
chemistry, physics and empirical-rheology workstream into operator commands. It
is not implementation of every research direction or read-only entry in NET's
whole catalog. Immutable evidence, offline inspectors, missing-covariance holds,
calibration restrictions and equipment-authority boundaries remain protected.

The existing thermochemical, uniaxial viscoelastic, finite-population and NIST
empirical kernels are reused without modification. The new conditional
uncertainty calculations consume NET's existing covariance-artifact.v1 contract;
they do not invent another covariance representation or relax its admission.

| Profile | Previously available | Implemented execution path |
| --- | --- | --- |
| thermochemistry | Standalone abstract reaction/heat benchmark | Session evaluate, inspect, fresh reproduction, reopen and replay |
| viscoelasticity | Standalone generalized-Maxwell benchmark | Same retained lifecycle; no new constitutive qualification |
| population | Standalone molar-mass population moments | Same retained lifecycle; explicit number/mass basis preserved |
| empirical-rheology | Standalone historical NIST secondary-data comparison | Same retained lifecycle plus existing independent Decimal arithmetic audit |
| complex-viscosity | Held when the historical table lacks joint covariance | Conditional calculation from a separately supplied, exactly bound full joint covariance |
| joint-residual | No joint test from unreported cross-frequency covariance | Conditional correlated residual calculation against an explicitly exact reference |

The last two profiles do not fill the NIST table's missing covariance. Their
packaged inputs are labeled synthetic-fixture, not measurements. Original NIST
uncertainty, joint-comparison and current-calibration holds still apply. The
published empirical table is historical secondary evidence, not an owned
instrument run or a current calibration certificate.

## Operator commands

From an installed candidate:

```sh
net materials inventory
net materials example empirical-rheology --output request.json
net materials run request.json --output-dir investigation
net materials inspect investigation/workspace.json
net materials verify investigation/workspace.json --output-dir fresh-verification
net materials replay investigation/workspace.json --output-dir fresh-replay
```

The six profile names in the table are accepted by `example`. Files and output
directories must be new. A run executes evaluation, inspection and verification
through real NET Session operations, then writes the existing workspace schema.
Failures are retained as refused execution occurrences, not erased or promoted
to successful results. Reopen with no binding is offline and has no executable
operations. Verify/replay require the operator command to bind the allowlisted
operations explicitly and create another output directory. The original
workspace remains unchanged. The same API is available through
`ciw.materials_workflow.create_session`, `execute`, and `reopen`.

The operation IDs are materials.evaluate.v1, materials.inspect.v1,
materials.verify.v1 and materials.replay.v1. Registration happens only in trusted
process setup. Unknown profiles, saved provider names and conflicting schema
ownership refuse rather than importing arbitrary code. The default operation
registry is not mutated. Saved parameters do not confer code-loading authority.

The run transport contains one explicitly synthetic declaration-selection
coordinate; it is not a fabricated sensor timestamp or a measured channel. Its
request, original quantity/condition metadata, source-kind label and content
identity are retained. Repeated execution creates new execution/result
occurrences, not additional physical observations.

## Covariance-aware calculations

For the complex-viscosity magnitude, with Gprime, Gdoubleprime in Pa and angular
frequency omega in rad/s:

```text
eta_abs = hypot(Gprime, Gdoubleprime) / omega
J = [Gprime / (hypot(Gprime,Gdoubleprime)*omega),
     Gdoubleprime / (hypot(Gprime,Gdoubleprime)*omega),
     -eta_abs / omega]
variance(eta_abs) = J C J^T
```

C binds the exact observation declaration, ordered quantity axes, units,
conditions, reference values and source references. Every off-diagonal term is
used. The output is another canonical covariance artifact with input-covariance
ancestry and Pa*s units. This is first-order Taylor propagation, not validated
nonlinear coverage. Zero modulus has an undefined gradient and returns HELD.
No negative variance is clipped and no covariance is repaired to obtain a pass.
The synthetic packaged example yields 2.5 Pa*s and variance 0.022945 (Pa*s)^2.

For the joint-residual profile:

```text
r = observations - exact_declared_reference
D_squared = r^T C^-1 r
```

The computation scales by marginal standard deviations and uses a Cholesky
solve, not an explicit matrix inverse. The normalized matrix must be positive
definite with minimum eigenvalue greater than 1e-12. Singular or ill-conditioned
inputs are HELD; there is no pseudoinverse or invented independent covariance.
An exact symmetric matrix is required by this consumer; it is not symmetrized.
Unrepresentable scaling also returns HELD. The synthetic two-dimensional example
has D_squared = 4/3 and is within its caller-declared threshold of 3.

The threshold is an engineering declaration, not an automatically calibrated
chi-squared critical value. No p-value or statistical coverage is asserted.
Uncertain fitted predictions require a separate prediction/cross-covariance
contract and are refused by this exact-reference profile. Unit-rescaling tests
check that the dimensionless score does not depend on an arbitrary unit scale.

## Identity, verification and authority

Source evidence, representation, operation, execution, result and verification
identities remain distinct. The existing PR184 retained-result dependency
contracts reject altered, foreign or unretained prerequisite occurrences before
provider execution and during saved-workspace checks. Hashes and source refs
establish consistency, not authorship, traceability or physical truth.

Inspection checks retained shape, source binding, identity, metadata and the
existing algebraic consistency checks, without running a time integrator or a
fresh empirical comparison. Some existing inspectors recompute elementary
population/frequency algebra; this is not physical validation.

For empirical-rheology, verification reuses the existing independent 50-digit
Decimal arithmetic audit of the published table and comparison. For the other
five profiles, verification is explicitly current-kernel reexecution/comparison,
not independent numerical verification. Previously existing independent solver
and high-precision tests are still run as regression tests, not attributed to
every live result. A successful operation's ordinary Session result still says
verification_status=not_verified: no separate admission or physical authority
is silently granted by these numerical checks.

Rehashed metadata cannot promote invented p-values, nonlinear coverage, units,
comparison thresholds or output-covariance ancestry. Rehashed verification
records must also preserve their comparison with the original candidate.
Authenticated calibration, physical validation, state admission, actuation and
part release all remain false. No raw instrument export, calibrated uncertainty
budget or specimen genealogy has been invented.

## Integration ownership

Science base: PR182 d4924247ae7a8b14d2bd608405b1a2f919b82c83, on PR174/PR173.
Core package source: actual retained CI wheel at 0091492b..., artifact11322442624,
workflow37255462777, wheel SHA256
`e13689bcfd03995cce7ee2918346d9e24dcbcb2b47a001dceb167382c9841b34`.

Two generic core files are reused exactly from PR184
662580211a31c00f6b30d0f60f815d67316d967d, not reimplemented:

- operations/schemas.py blob 4b08821f638a9725dccdbca7f300dc55f3e30209
- operations/runner.py blob 6a1e9470611c522f585f5bae8fc2d366ce5cfffa

No private domain-provider source or binary is included. The operator command
registry gains only the materials row. PR185 a7724105b5c3f59cabaef7085c9329bb7dfe1279
owns the shared Workbench data bridges: none of its bridge modules are edited.
Its shared operation files and PR184's hooks require normal parent integration
reconciliation; these files must not be blindly overwritten when sequencing
branches. No second data inventory, data bridge, ledger or workspace is added.

Observed main0374076695475669188d61698e4656ced2263e01 contains additional microfab
and documentation changes outside this source assembly. This delivery is not a
claim to have checked out, built or regression-tested that complete main tree.
The upstream pyproject, dependency pins and license are unchanged.

## Executed verification

Final source selection: 623 passed, zero failures/errors/skips. This consists of
544 earlier science/empirical cases and 79 new Session/uncertainty cases. Source
and installed runs are repeated executions of the same cases, not additive
counts of distinct tests. The final installed-wheel results and all exact
artifact identities are recorded in the companion validation manifest.

Five new adversarial metadata tests initially failed and then passed after
retained scope/unit/ancestry checks were added. One further adversarial test
exposed a rehashed verification/candidate mismatch; retained comparison checks
now reject it. These are defects found in the new integration, not silently
removed tests. The original stdlib-only empirical CLI test was made portable:
for python -S it copies six stdlib-only modules from the imported package into
an isolated directory. It does not expose all site-packages or fall back to the
source checkout in installed testing. No existing assertion was removed.

The wheel was built noneditably with the exact existing pyproject. Every
installed ciw package file was compared against the wheel bytes. Installed tests
run outside the source tree with PYTHONPATH removed. Explicitly copied local
dependencies were necessary because the runtime could not provision the pinned
index versions. Linux CPython3.13.5, pytest9.0.2, NumPy2.3.5, SciPy1.17.0 and
websockets16.0 were observed; NumPy/SciPy differ from repository pins.

The complete source suite was finally run with the lean installed test
interpreter and explicit source PYTHONPATH. Earlier host-interpreter calls hit
tool time limits; they are retained as incomplete, not counted as passes. This
configuration change does not diagnose the timeout cause.

The new workflow defines a pinned-dependency Linux/Windows installed-package
matrix. A workflow definition is not a successful hosted run. Full-repository
regression, original publisher-PDF-byte retention, Windows, pinned-environment,
owned instrument capture, independent calibration/coverage validation and live
manufacturing authority remain unestablished.

Primary uncertainty reference:
https://physics.nist.gov/cuu/Uncertainty/combination.html

The reference describes the first-order law of propagation including covariance;
it does not attest this implementation or the caller's data.
