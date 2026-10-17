# Bounded BIM clearance inspection

This candidate adds `metrology.bim-clearance.v1` to the existing CapabilityRegistry,
OperationRegistry, runner, Session, AgentHost and local MCP transport. It does not
introduce a new execution ledger, geometry engine or authorization system.

The profile consumes a host-prepared inspection: exact bounded IFC bytes and model
revision, recorded scan coordinates, separate scan/model frames, independent pose
and full yaw/translation covariance, calibration applicability and uncertainty
declarations, one target element, and a versioned project requirement. The source
wrapper preserves the exact JSON bytes. Its single channel is a transport marker,
not a physical observation. All coordinates and lengths are in metres; covariance
ordering is yaw (radians), tx, ty, tz (metres). Cross terms have the corresponding
mixed units. The pose is model-from-scan; roll/pitch are unsupported.

## Calculation and scope

The fixed worker imports the existing CSE modules from an operator-bound clean
checkout. The complete native tree is pinned to
`f91c982b580ad4f16a27ea149eedf0e338f6d764`. The existing subprocess adapter checks
source bytes, interpreter identity and dependency versions before and after work,
and enforces a 30-second deadline and 256 KiB combined output bound. This is a
trusted numerical provider binding, not an operating-system sandbox. Dependency
versions are recorded/frozen at binding; dependency binary authenticity is not
established.

The worker compiles IFC with CSE, derives its scene, assesses the declared route,
refines association from the independent pose using the existing registration
routine (25 iterations, NLL gate < 6), and obtains CSE's actual responsibility
coverage and inspection plan. It refuses a different selected target. CSE's
existing `adapt_clearance_likelihood` performs support-face extraction, pose
agreement, uncertainty decomposition and innovation gates. Only its resulting
observation enters a **local** CSE session's conditioning/invariant pipeline. The
updated clearance assessment is retained; no shared canonical BIM state is changed.

The scope is the selected controlling element in the provider's supported IFC
scene and one proposed oriented box. The risk report also retains the scene's
other solid elements; it does not claim every element was measured. Geometry and
probabilities remain conditional on CSE's model and linearization. A modeled
`SATISFIED` verdict is not a physical clearance certificate.

Sampling, pose and systematic terms are explicitly declared independent for the
provider's scalar noise sum. Prior/measurement cross covariance must be explicitly
declared independent; unknown covariance is held. No independence is assumed
between element violation events: the provider reports max/sum probability bounds.

The offline reader checks exact source binding, frame/calibration applicability,
scan/scene/pose links, supported fields, retained uncertainty arithmetic, Gaussian
risk probabilities, dependence-safe bounds and verdict consistency. It does not
independently rerun the geometry solver or authenticate an external calibration
record. It does not create a verification identity. The runner separately retains
source evidence, execution occurrence and result identity. Same-attempt retries
reuse their response; intentional replay receives a new occurrence.

## Local use

Install NCP with the repository's dependencies. Prepare a separate native checkout
whose tree equals the pin above (a Git subtree split of
`instruments/domain/bim-estimator` from this candidate's base has that tree). Pass
that checkout's actual commit SHA; the adapter also verifies the fixed tree pin.
No files, commands or provider locations can be supplied through an MCP tool call.

```sh
python -m ciw.metrology_clearance prepare \
  examples/metrology-clearance/uncertain-inspection.json /tmp/clearance-profile
python -m ciw.metrology_clearance run /tmp/clearance-profile/profile.json \
  --provider /operator/cse-checkout --revision FULL_NATIVE_COMMIT_SHA --attempt assessment-001
python -m ciw.metrology_clearance inspect \
  /tmp/clearance-profile/agent-output/assessment-001/workspace.json
python -m ciw.metrology_clearance serve /tmp/clearance-profile/profile.json \
  --provider /operator/cse-checkout --revision FULL_NATIVE_COMMIT_SHA --resume
```

`prepare` creates a new directory and refuses existing destinations. `run` and
`serve` use the same retained domain operation. `--python` selects the trusted
native interpreter at host launch. The existing MCP tools are used unchanged:

```json
{"name":"net_execute","arguments":{"source":"source","graph":"baseline","attempt":"assessment-001"}}
```

Use `net_capabilities`, `net_inspect`, `net_observe` and `net_replay` for existing
review/replay functions. Source and graph are opaque host bindings. The retained
inspection carries `inspection_ref`; that string does not load or fetch anything.
The fixed graph accepts no agent-supplied numerical parameters. No candidate
parameter domains or qualification suites are granted by this profile.

A completed operation can contain `native.status=held` or an `UNRESOLVED` modeled
verdict. CLI exit zero means the retained operation completed, **not** that the
clearance passed. Numerical provider failure remains a runner refusal.

## Bounds and examples

- Exact inspection JSON: 48 KiB; canonical embedded IFC base64: at most 22,000
  characters; at most 256 STEP records and 64 native full variables.
- Scan: 10–1,024 finite xyz points; no CAD path, URL, camera stream, arbitrary
  registration settings or executable selection in inspection data.
- Provider geometry support is unchanged. Unsupported/missing geometry and native
  exceptions cannot produce a successful assessment.
- `inspection.json` is synthetic, with a deliberately tight pose declaration;
  actual fitted association disagrees and the inspection is held.
- `uncertain-inspection.json` uses the same synthetic scan with an explicitly large
  pose covariance (0.01 on each yaw/translation diagonal). It exercises native
  conditioning but retains `UNRESOLVED`; it is not a claim about survey accuracy.
  The fixture contains 900 CSE-synthesized surface points (seed 4, 2 mm noise) plus
  70 synthetic top-face points (seed 4, 2/3 mm x/z noise), rounded to 5 decimals.
  These are software fixtures, not independent held-out physical observations.

Calibration references and applicability are operator declarations. No certificate
bytes, authenticated issuer, calibration-chain completeness, independently surveyed
frame or physical campaign are supplied by these examples.

## Qualification still required

Before physical use, freeze the candidate provider and inspection policy, acquire
independent reference measurements not used for alignment/calibration, and retain
instrument/calibration records, frame transforms, environmental conditions, point
coverage and repeated measurements. Predeclare uncertainty/coverage targets and
acceptance criteria. Compare support/clearance estimates to those references with
their joint covariance. Keep model association and survey positioning separate.
A synthetic test suite cannot close these obligations.

Manufacturing planar assessment, standard/edition-specific GD&T, freight envelope
and visible lean remain separate profiles on the same substrate. Remote MCP,
hosted Board qualification and corrective-action adapters are not enabled here.
Robot offsets, construction stoppages or WMS quarantine require separate explicit
authority and recovery contracts. Geometry cannot establish hidden structural damage.
