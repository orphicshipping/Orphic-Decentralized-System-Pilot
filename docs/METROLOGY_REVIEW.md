# Metrology comparison campaigns and action drafts

`ciw.metrology_review` adds local, bounded review functions to the sampled profiles.
Neither a numerical comparison nor an action draft qualifies a physical profile.
No release ledger is changed.

## Campaign binding

A `ciw.metrology-campaign.v1` manifest contains exactly `candidate_ref`, `plan_ref`,
`calibration_refs`, `reference_capture_refs` and `comparison_request` in addition
to its schema. The candidate ref is SHA-256 of canonical `runtime_identity()`;
it binds the numerical and contract source hashes and pinned NumPy version.
`plan_ref` binds an externally retained predeclared campaign plan. Calibration and
held-out reference refs are distinct bounded SHA-256 lists. A reference cannot be
a calibration input or an assessed case. These digest checks establish declared
separation, not acquisition independence or authenticated custody.

Supply 1–64 cases from one profile with distinct capture identities. Each comparison
left sample must match the computed quantity, unit, frame and exact source digest;
each right sample must bind a declared held-out reference. The existing
`reference_comparison.compare` owns quantity/identity/clock alignment, full joint
covariance, uncertainty completeness and exact guarded decisions. No independent
noise or repeat-average uncertainty is invented by this adapter. See
[reference_comparison.py](../src/ciw/reference_comparison.py) for the existing comparison contract.

```sh
python -m ciw.metrology_review campaign manifest.json capture-1.json capture-2.json \
  --output campaign-report.json
```

Manifest/result inputs are bounded at 2 MiB each, each case at 48 KiB; output is
create-only. Reports retain manifest, source/result identities, exact comparison,
synthetic/recorded declarations and blockers. Fewer than three declared repeats
is a blocker. Synthetic evidence is always `BLOCKED`; recorded evidence still
requires independent physical review and source authentication. There is no
API for promoting either to physical qualification. This runner does not close
the separate physical BIM campaign; the CSE profile retains its own contract.

## Non-executing action draft

```sh
python -m ciw.metrology_review draft capture.json assessment.json \
  --target project-review-target --action request_review \
  --current-source-ref sha256:EXACT_CASE_DIGEST --current-policy-revision EXACT_REVISION \
  --output proposal.json
```

Allowed actions are `request_review`, `propose_remeasurement` and
`propose_quarantine`. The proposal names the exact retained finding, source,
policy revision, target and intended action, with explicit approval required.
Retained witness validation performs no solver or device/WMS/schedule call.
Changed evidence, changed policy or changed proposal contents invalidate review.
All current proposals are `DRAFT_BLOCKED_UNQUALIFIED`; execution is false.
Deployment-specific grants, approval, interlocks and recovery are future work.
