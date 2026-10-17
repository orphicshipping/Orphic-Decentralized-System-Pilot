# Metrology implementation scope and remaining gates

This table records software candidates, not completed physical work. The bounded
BIM candidate in PR #232 is the base; sampled geometry, review contracts and the
HTTP adapter are additive, separately reviewable increments: contracts #236, numerical operations #237, and
campaign/action review #238.

| Task | Candidate implementation | Remaining acceptance work |
| --- | --- | --- |
| T1 | Bounded BIM contract plus additive sampled-case contract; exact identity, units/frame/calibration/provider/policy binding and separate statuses | Review supported geometry and conditional uncertainty assumptions per deployment |
| T2 | Exact bounded sources and authorized content-addressed sampled ingress; missingness/path/digest limits | Authenticate real acquisition/calibration custody |
| T3 | Existing pinned CSE BIM operation from #232; retained native provenance, no canonical-world mutation | Independent physical measurements and geometry qualification |
| T4 | Existing BIM local MCP plus sampled CLI/SDK stdio operations; fixed grants and retained retries/recovery | Profile-specific production lifecycle review |
| T5 | BIM qualification prerequisites remain explicit; sampled campaign runner cannot substitute for a BIM campaign | Physical BIM captures, independent pose/calibration, held-out references and predeclared acceptance targets; blocked |
| T6 | Sampled orthogonal least-squares plane residual range, declared sampling/correction/error model, analytic and degeneracy fixtures | Independent calibrated manufacturing validation; minimum-zone solver is out of scope |
| T7 | Fixed bounded guardband decisions and existing full-joint-covariance comparison integration; retained MCP lineage | Held-out calibrated measurements and independently reviewed installed-chain uncertainty; blocked for physical claims |
| T8 | Semantic ISO 1101:2017 perpendicularity association parser, no modifiers; graphical/ambiguous/other controls refuse | Normative rule access/review, datum establishment, control calculation and independently reviewed examples; not a GD&T compliance implementation |
| T9 | Frame-aligned envelope and exact rational carrier-rule rounding; occupied volume explicitly unknown | Real calibrated scale/coverage and independent applicable tariff validation |
| T10 | Visible 3D principal-axis lean bound to gravity/camera calibration declarations; no hidden-integrity/safety inference | Representative captures, independent physical inclinometer comparison and latency/dropout campaign |
| T11 | Bounded authenticated Streamable HTTP candidate for isolated sampled investigations; actual SDK tests | Physical BIM prerequisite, managed TLS/identity/credential custody and deployment qualification; no hosted service deployed |
| T12 | Stale-bound review/remeasurement/quarantine proposal drafts, no integration calls and mandatory approval | Qualified relevant physical profile and deployment-specific review; execution remains separate future work |

Checkpoint A's BIM software tests and wheel evidence belong to #232. This extension
adds sampled-profile software checks; it does not turn those fixtures into physical
observations. Checkpoints B and C remain open for physical and normative acceptance.
Checkpoint D includes installed-wheel software journeys only; final physical/profile
release remains blocked. No qualification ledger update or automatic release occurs.

See [sampled scope](METROLOGY_SAMPLED_PROFILES.md),
[review contracts](METROLOGY_REVIEW.md) and [HTTP scope](METROLOGY_REMOTE.md).

The [software checkpoint](../validation/metrology/2026-10-17-software/README.md) records
source/installed tests, wheel identities and external prerequisites.
