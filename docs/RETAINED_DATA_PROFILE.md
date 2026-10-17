# Retained native data profile

NCP includes the existing typed-data owner's `ciw.data_sources` envelope and the
`data-record` path in its shared Workbench. GaugeLink measurement attachment uses
those native APIs to retain the complete measurement artifact, declared input
and output covariance, and observation stream in the original Session. Attachment
records no new scientific execution or result occurrence.

## Owner provenance and scope

The retention/CSV implementation and its 105 native contract tests come from
[PR185 commit a7724105](https://github.com/atomtrapping/Notations-Control-Protocol/tree/a7724105b5c3f59cabaef7085c9329bb7dfe1279).
The original `data_sources.py` Git blob is
`c2c7e88ad9095f8d8a9e961755adb66f1f77344a`; the original test blob is
`4b3396452b5ccf051ffab78f2bcecd6ee299530f`.
The restored Workbench integration starts from owner blob
`e3326204e5d1b1a6bc4a894dcb2a8bbc9b30660b` from
[PR200 commit cf5342a4](https://github.com/atomtrapping/Notations-Control-Protocol/tree/cf5342a4e0996bcbe04671572f0c7e6da21c4b7e).
Its changes to the current catalog are typed-data descriptors, immutable revision
links, idempotent occurrence retries, and validated source dependency edges.
A bounded atomic source-batch method adds to that owner: live measurement
attachment keeps the same Workbench object, trusted provider bindings and
in-flight capacity reservations. Failed batches, including interruption, roll
back their source catalog changes. Saved restore still requires explicit rebinding.
Session retains its current atomic publication and Windows long-path handling.
Only the owner's read-only `data.list` and `data.get` request handlers are added;
explicit source input links also join the existing correction dependency graph.

This profile accepts 17 versioned native schemas whose validators are already
included: state, observation, observation stream, artifact, comparison,
verification, parameter space, experiment, checkpoint, graph run, check plan,
check report, thermal observation view, both thermal math inspection versions,
covariance artifact, and polymer cycle request. The closed schema table selects
existing validators; retained input cannot choose executable code.

The two PR185 field/compute mapping routes are deliberately absent because their
owner implementations are not integrated in this package. The later PR200
HOOMD, product-study, manufacturing-report, and toolpoint-stream routes are also
absent. Their schemas refuse explicitly. This bounded retention profile does not
claim the complete PR185/PR200 execution, projection, CLI, or provider program.

## Retention and authority

Exact original input bytes, units, frames, clocks, missing observations and full
covariance remain native. CSV conversion requires explicit scalar mappings and
replays its receipt against the retained input; it cannot infer missing data or
covariance. Native envelopes are bounded to 32 MiB and the shared Workbench to
64 MiB. A dataset revision cannot replace existing evidence. Revision history
alone is not a computational dependency. Explicit input links must name retained
sources and form an acyclic graph.

The existing proposal/review journal governs corrections. An accepted correction
makes downstream retained sources stale without rewriting their bytes; a newly
retained revision alone does not approve a correction. Offline reopen validates
the same data, identities and links without starting providers. Hash consistency
does not authenticate a source, establish physical qualification or admit state.
The read-only MCP tool allowlists and spatial WebSocket restrictions are unchanged.

## Verification

`tests/test_link_measurement_workbench.py` runs all eight native attachment cases
by default; no environment opt-in is needed. The original 105 owner contract
cases remain unchanged. Twenty-three focused `test_data_workbench_retention.py` cases
cover unavailable schema refusals, detached reads, immutable retries, revision
and dependency refusals, offline reopen, transitive correction staleness, and
atomic batch rollback/capacity handling. An actual pinned-provider Workbench
regression verifies live binding retention and offline restore without bindings.
Synthetic fixtures exercise software contracts and do not qualify hardware.
The installed provider-profile gate requires every selected case to pass with
zero skips and compares the installed package with the exact wheel/source bytes.
