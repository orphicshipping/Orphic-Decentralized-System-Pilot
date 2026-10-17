# Bounded sampled metrology profiles

These additive profiles extend the existing bounded BIM clearance journey in
[METROLOGY_CLEARANCE.md](METROLOGY_CLEARANCE.md). They use the existing evidence,
CapabilityRegistry, OperationRegistry, Session, runner, AgentHost, recovery and
MCP conventions. They do not replace BIM schemas or register general GD&T claims.

## Input and authority

`ciw.metrology-case.v1` binds exact source bytes, inspection, feature and revision,
sampled region, datum, capture and revision, frame, calibration and revision,
calibration evidence digest, fixed provider revision and decision policy revision.
Unknown fields and inconsistent capture/frame/unit bindings refuse intake. Input
is at most **48 KiB**, containing **3–256** xyz points in metres (coordinates within
±10,000 m). Null points preserve missingness and prevent computation. Only one
case and one fixed operation execute per graph; profiles allow at most eight
attempts. Point arrays and 3×3 matrices are bounded; no dense point-pair matrix is
allocated. There is no image reconstruction, file fetching or arbitrary solver.

The opaque ingress handle is the exact JSON SHA-256. Resolution requires the
operator's allowlist, confines reads to the retained root and refuses symlinks,
path/URL handles, mismatched digests and oversized files. The retained root and
its parent directories are host-controlled. Content identity is not issuer or
calibration authentication. `recorded` is an operator declaration, not proof that
a capture is physical. All examples here are synthetic.

Computation (`completed`, `refused`, `not_computed`), conditional numerical decision
(`WITHIN_LIMIT`, `OUTSIDE_LIMIT`, `INCONCLUSIVE`) and approval (`not_granted`) remain
separate. Partial/unknown coverage, unresolved calibration, unknown uncertainty,
blur or motion hold the decision. A numerical within-limit result grants no
physical, standards, safety, state-admission or actuation authority.

## Supported calculations

| Profile | Declared quantity | Explicit exclusions |
| --- | --- | --- |
| `sampled-plane.v1` | Peak-to-valley orthogonal residual about an SVD least-squares plane, all samples retained | Minimum-zone flatness, surface between samples, general GD&T, datum establishment |
| `freight-envelope.v1` | Frame-aligned xyz extrema, longest dimension and envelope volume | Occupied volume, minimum rotated box, hidden/occluded extent, actual mass or carrier acceptance |
| `visible-lean.v1` | Acute angle between the visible point set's largest principal axis and calibrated gravity | Image-based reconstruction, hidden integrity, load stability or safety disposition |

NumPy **2.4.3** is required for new computations. Results retain the provider
revision, NumPy version and exact numerical/contract source hashes. Degenerate
plane orientation, dimensions or visible-axis eigenvalue gaps refuse computation.
Offline reopening checks retained source identities, numerical witnesses,
uncertainty arithmetic and exact decision bounds without SVD/eigensolver execution.
Witness stationarity/PSD checks use numerical tolerances; this is neither an
independent implementation qualification nor a formal geometry certificate.

The correction model multiplies supplied coordinates by the declared scale factor.
It separates a shared translation bound (which cancels in these translation-invariant
quantities), a per-point Euclidean residual bound, relative scale bound and angular
bound. These are conditional bounded error sets, not inferred Gaussian coverage or
complete installed-chain uncertainty. The operator must substantiate their scope.

For plane residual range, the bound is `2e + range*s + 4R*sin(a/2)` plus floating
point padding, with point residual bound `e`, relative scale bound `s`, centered
radius `R`, and declared fitted-normal angular bound `a`. That angular bound must
cover orientation effects; the software does not qualify its calibration basis.
For each envelope extent the bound is `2e + extent*s` plus padding. Lean uses a
covariance perturbation bound `4e*||X||F/sqrt(n) + 4e²`, a separated largest
eigen-direction, and the declared gravity/angular bound; unresolved separation
holds the decision. Scalar intervals round outward. An upper bound at/below the
fixed policy maximum is within limit; a lower bound above it is outside; overlap
is inconclusive. Decisions are conditional on the declared evidence and models.

Freight requires a versioned carrier rule with evidence digest, cm/in length unit,
positive decimal length increment, dimensional divisor, kg/lb mass unit and mass
increment. Exact rational arithmetic ceilings each dimension before calculating
and ceiling dimensional mass. Nominal/lower/upper bills remain separate. Rules
are supplied project policies; no carrier tariff is bundled or inferred.

The optional PMI field preserves semantic **ISO 1101:2017 perpendicularity** feature
and datum associations, without modifiers. Graphical-only annotations, other
controls and ambiguous associations refuse. This is an association parser only:
any PMI-bearing case is held for normative and datum review. See the
[ISO edition record](https://www.iso.org/standard/66777.html). No normative control
solver, standards-compliance result or independently reviewed GD&T example is
claimed.

## Local journey

```sh
python -m ciw.metrology_workflow example sampled-plane.v1 /tmp/plane.json
python -m ciw.metrology_workflow prepare /tmp/plane.json /tmp/plane-profile
python -m ciw.metrology_workflow run /tmp/plane-profile/profile.json --attempt run-001
python -m ciw.metrology_workflow inspect /tmp/plane-profile/agent-output/run-001/workspace.json
python -m ciw.metrology_workflow serve /tmp/plane-profile/profile.json --sdk --resume
```

Install `.[board-mcp]` for SDK serving. Use existing `net_execute` with only
`source=source`, `graph=baseline` and an attempt ID; `net_inspect` reads the returned
opaque workspace/graph-run handle. Tool arguments cannot select providers,
policies or grants. The same attempt returns its recorded response; interrupted
unknown outcomes are not automatically replayed. Intentional replay remains a
separate host operation. Preparation and example output refuse existing paths.

Run the focused tests through the repository configuration:

```sh
python -m pytest -q tests/test_metrology_contract.py tests/test_metrology_profiles.py
python -m pip wheel --no-deps . -w dist
```

Physical held-out comparisons, installed-chain calibration, representative sampling,
latency/dropout campaigns and independently reviewed normative controls remain
required before the associated deployment claims.
